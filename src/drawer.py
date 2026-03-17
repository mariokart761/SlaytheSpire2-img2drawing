"""
自動滑鼠繪圖模組

改善重點（依文件建議）：
1. 點清理三步驟：去重複 → 去抖動 → 去共線
   防止滑鼠在原地亂磨或畫出小鋸齒
2. 路徑方向最佳化：比較路徑兩端到目前游標距離，
   從較近的那端開始畫，減少空跑時間
3. 閉合路徑偵測：首尾距離 ≤ 閾值時補回起點，
   確保封閉輪廓能正確閉合
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional, Sequence

import numpy as np
import pyautogui

from .state_machine import StateMachine

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0

MIN_DELAY     = 0.02   # 最小起落筆延遲（秒），對齊 60fps
MIN_STEP_DELAY = 0.001 # 插值點之間的最小延遲


# ══════════════════════════════════════════════════
#  基礎工具
# ══════════════════════════════════════════════════

def _clamp_delay(delay: float) -> float:
    return max(MIN_DELAY, float(delay))

def _clamp_step_delay(delay: float) -> float:
    return max(MIN_STEP_DELAY, float(delay))

def _normalize_button(button: str) -> str:
    b = (button or "").strip().lower()
    if b not in {"left", "right", "middle"}:
        raise ValueError(f"Unsupported draw_button: {button!r}")
    return b

def _clamp_screen_xy(x: float, y: float) -> tuple[int, int]:
    sw, sh = pyautogui.size()
    return (
        max(0, min(int(round(x)), sw - 1)),
        max(0, min(int(round(y)), sh - 1)),
    )


# ══════════════════════════════════════════════════
#  座標映射
# ══════════════════════════════════════════════════

class CoordinateMapper:
    """
    圖片座標 → 螢幕座標的線性映射。

    用法：
      1. draw_region=(x,y,w,h)：圖片等比縮放放入指定矩形
      2. avoid_left/right/top/bottom：從螢幕扣除避開比例後居中
      3. 再加 anchor=(sx,sy)：以指定螢幕點為繪圖中心（覆蓋自動居中）
    """

    def __init__(
        self,
        img_w: int,
        img_h: int,
        *,
        draw_region: Optional[tuple[int, int, int, int]] = None,
        avoid_left:  float = 0.0,
        avoid_right: float = 0.0,
        avoid_top:   float = 0.0,
        avoid_bottom: float = 0.0,
        anchor: Optional[tuple[int, int]] = None,
    ) -> None:
        if img_w <= 0 or img_h <= 0:
            raise ValueError("img_w and img_h must be > 0")

        screen_w, screen_h = pyautogui.size()

        if draw_region is not None:
            x, y, w, h = draw_region
            if w <= 0 or h <= 0:
                raise ValueError(f"Invalid draw_region: {draw_region!r}")
            draw_x, draw_y, draw_w, draw_h = int(x), int(y), int(w), int(h)
        else:
            for name, val in [("avoid_left", avoid_left), ("avoid_right", avoid_right),
                               ("avoid_top", avoid_top),  ("avoid_bottom", avoid_bottom)]:
                if not (0.0 <= val < 1.0):
                    raise ValueError(f"{name} must be in [0.0, 1.0)")
            if avoid_left + avoid_right >= 1.0:
                raise ValueError("avoid_left + avoid_right must be < 1.0")
            if avoid_top + avoid_bottom >= 1.0:
                raise ValueError("avoid_top + avoid_bottom must be < 1.0")
            draw_x = int(round(screen_w * avoid_left))
            draw_y = int(round(screen_h * avoid_top))
            draw_w = int(round(screen_w * (1.0 - avoid_left - avoid_right)))
            draw_h = int(round(screen_h * (1.0 - avoid_top - avoid_bottom)))

        if draw_w <= 0 or draw_h <= 0:
            raise ValueError("Computed drawing region is empty")

        scale    = min(draw_w / img_w, draw_h / img_h)
        scaled_w = img_w * scale
        scaled_h = img_h * scale

        self.region_x = draw_x
        self.region_y = draw_y
        self.region_w = draw_w
        self.region_h = draw_h
        self.scale    = scale

        if anchor is not None:
            ax, ay = anchor
            self.offset_x = float(ax) - scaled_w / 2.0
            self.offset_y = float(ay) - scaled_h / 2.0
        else:
            self.offset_x = draw_x + (draw_w - scaled_w) / 2.0
            self.offset_y = draw_y + (draw_h - scaled_h) / 2.0

        self.delta_x = 0.0
        self.delta_y = 0.0

    def to_screen(self, img_x: float, img_y: float) -> tuple[int, int]:
        sx = self.offset_x + img_x * self.scale + self.delta_x
        sy = self.offset_y + img_y * self.scale + self.delta_y
        return _clamp_screen_xy(sx, sy)

    def apply_offset(self, dx: float, dy: float) -> None:
        self.delta_x += float(dx)
        self.delta_y += float(dy)

    def current_draw_region(self) -> tuple[int, int, int, int]:
        return (
            int(round(self.region_x + self.delta_x)),
            int(round(self.region_y + self.delta_y)),
            self.region_w, self.region_h,
        )


# ══════════════════════════════════════════════════
#  插值
# ══════════════════════════════════════════════════

def _interpolate_steps(
    x0: int, y0: int,
    x1: int, y1: int,
    step_size: int,
) -> list[tuple[int, int]]:
    """兩點之間依步長做線性插值（不含起點，含終點）"""
    if step_size <= 0:
        step_size = 1
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist == 0:
        return [(x1, y1)]
    n = max(1, int(math.ceil(dist / step_size)))
    return [_clamp_screen_xy(x0 + dx * i / n, y0 + dy * i / n)
            for i in range(1, n + 1)]


# ══════════════════════════════════════════════════
#  輪廓點解析
# ══════════════════════════════════════════════════

def _extract_points(cnt: np.ndarray) -> list[tuple[int, int]]:
    """解析 OpenCV 輪廓陣列為 [(x, y), ...] 串列"""
    arr = np.asarray(cnt)
    if arr.ndim == 3 and arr.shape[1] == 1 and arr.shape[2] == 2:
        pts = arr[:, 0, :]
    elif arr.ndim == 2 and arr.shape[1] == 2:
        pts = arr
    else:
        raise ValueError(f"Unsupported contour shape: {arr.shape}")
    return [(int(p[0]), int(p[1])) for p in pts]


# ══════════════════════════════════════════════════
#  路徑清理（文件建議：去重 / 去抖動 / 去共線）
# ══════════════════════════════════════════════════

def _dedupe_points(
    points: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """移除連續重複點"""
    if not points:
        return points
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def _remove_small_jitter(
    points: list[tuple[int, int]],
    min_dist: float = 1.5,
) -> list[tuple[int, int]]:
    """移除與前一保留點距離太近的點，消除原地微抖"""
    if len(points) < 2:
        return points
    out = [points[0]]
    for p in points[1:]:
        px, py = out[-1]
        qx, qy = p
        if math.hypot(qx - px, qy - py) >= min_dist:
            out.append(p)
    # 確保起終點都在
    if len(out) == 1 and len(points) > 1:
        out.append(points[-1])
    return out


def _remove_collinear_points(
    points: list[tuple[int, int]],
    tolerance: float = 1.0,
) -> list[tuple[int, int]]:
    """移除幾乎共線的中間點，減少不必要的方向變化"""
    if len(points) < 3:
        return points

    def _dist_pt_line(p, a, b):
        px, py = p
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        return abs(dy * px - dx * py + bx * ay - by * ax) / math.hypot(dx, dy)

    out = [points[0]]
    for i in range(1, len(points) - 1):
        if _dist_pt_line(points[i], out[-1], points[i + 1]) > tolerance:
            out.append(points[i])
    out.append(points[-1])
    return out


def _clean_path(
    points: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """依序執行三步清理：去重 → 去抖 → 去共線"""
    points = _dedupe_points(points)
    points = _remove_small_jitter(points, min_dist=1.2)
    points = _remove_collinear_points(points, tolerance=0.8)
    return points


# ══════════════════════════════════════════════════
#  路徑方向與閉合
# ══════════════════════════════════════════════════

def _is_closed_path(
    points: list[tuple[int, int]],
    threshold: float = 3.0,
) -> bool:
    """首尾距離 ≤ threshold 時視為閉合輪廓"""
    if len(points) < 3:
        return False
    x0, y0 = points[0]
    x1, y1 = points[-1]
    return math.hypot(x1 - x0, y1 - y0) <= threshold


def _orient_path(
    points: list[tuple[int, int]],
    current_screen_xy: tuple[int, int],
    mapper: CoordinateMapper,
) -> list[tuple[int, int]]:
    """
    比較路徑兩端映射到螢幕後與目前游標的距離，
    選擇從較近的那端出發，減少空跑距離。
    """
    if len(points) < 2:
        return points
    sx0, sy0 = mapper.to_screen(*points[0])
    sx1, sy1 = mapper.to_screen(*points[-1])
    cx,  cy  = current_screen_xy
    if math.hypot(sx1 - cx, sy1 - cy) < math.hypot(sx0 - cx, sy0 - cy):
        return list(reversed(points))
    return points


# ══════════════════════════════════════════════════
#  Drawer
# ══════════════════════════════════════════════════

class Drawer:
    """執行自動繪圖的核心類別"""

    def __init__(
        self,
        mapper:      CoordinateMapper,
        state:       StateMachine,
        draw_button: str = "right",
        drag_step:   int = 5,
        draw_delay:  float = 0.05,
        step_delay:  float = 0.001,
        close_path:  bool = True,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.mapper      = mapper
        self.state       = state
        self.draw_button = _normalize_button(draw_button)
        self.drag_step   = max(1, int(drag_step))
        self.draw_delay  = _clamp_delay(draw_delay)
        self.step_delay  = _clamp_step_delay(step_delay)
        self.close_path  = close_path   # 是否補閉合輪廓的最後一段
        self.on_progress = on_progress

    def _safe_mouse_up(self) -> None:
        try:
            pyautogui.mouseUp(button=self.draw_button)
        except Exception:
            pass

    def _move_to(self, x: int, y: int) -> None:
        pyautogui.moveTo(*_clamp_screen_xy(x, y), duration=0)

    def _press_at(self, x: int, y: int) -> None:
        self._move_to(x, y)
        time.sleep(self.draw_delay)
        pyautogui.mouseDown(x=x, y=y, button=self.draw_button)
        time.sleep(self.draw_delay)

    def _drag_segment(
        self,
        x0: int, y0: int,
        x1: int, y1: int,
    ) -> bool:
        """拖曳一段路徑；被停止/暫停時回傳 False"""
        for nx, ny in _interpolate_steps(x0, y0, x1, y1, self.drag_step):
            if not self.state.wait_if_paused():
                self._safe_mouse_up()
                return False
            self._move_to(nx, ny)
            if self.step_delay > 0:
                time.sleep(self.step_delay)
        return True

    def draw_contours(self, contours: Sequence[np.ndarray]) -> None:
        """
        依序繪製所有輪廓。
        每條輪廓：
          抬筆 → [方向最佳化] → 移到起點 → 落筆 → 沿路徑移動 → [補閉合] → 抬筆

        改善：
        - 每條輪廓繪製前先清理路徑點（去重/去抖/去共線）
        - 依目前游標位置決定路徑方向（減少空跑）
        - 若輪廓為閉合路徑則補畫回起點
        """
        total = len(contours)

        # 初始游標位置：從繪圖區中心出發
        try:
            current_pos: tuple[int, int] = (
                self.mapper.region_x + self.mapper.region_w // 2,
                self.mapper.region_y + self.mapper.region_h // 2,
            )
        except Exception:
            current_pos = _clamp_screen_xy(0, 0)

        try:
            for idx, cnt in enumerate(contours):
                if not self.state.wait_if_paused():
                    break

                # ── 解析 + 清理路徑點 ──────────────
                raw_pts = _extract_points(np.asarray(cnt))
                points  = _clean_path(raw_pts)
                if len(points) < 2:
                    continue

                # ── 路徑方向最佳化 ──────────────────
                points = _orient_path(points, current_pos, self.mapper)

                # ── 閉合輪廓補點 ────────────────────
                if self.close_path and _is_closed_path(points):
                    points = points + [points[0]]

                # ── 映射起點到螢幕 ──────────────────
                sx, sy = self.mapper.to_screen(*points[0])

                # 抬筆 → 移到起點 → 落筆
                self._safe_mouse_up()
                time.sleep(self.draw_delay)
                self._press_at(sx, sy)

                prev_sx, prev_sy = sx, sy

                # ── 沿路徑繪製 ──────────────────────
                for pt in points[1:]:
                    if not self.state.wait_if_paused():
                        self._safe_mouse_up()
                        return
                    tx, ty = self.mapper.to_screen(*pt)
                    if not self._drag_segment(prev_sx, prev_sy, tx, ty):
                        return
                    prev_sx, prev_sy = tx, ty

                # 抬筆
                self._safe_mouse_up()
                time.sleep(self.draw_delay)

                # 更新游標位置供下一條輪廓的方向判斷
                current_pos = (prev_sx, prev_sy)

                if self.on_progress:
                    self.on_progress(idx + 1, total)

        finally:
            self._safe_mouse_up()
