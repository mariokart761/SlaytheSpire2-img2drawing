"""
自動滑鼠繪圖模組
- 螢幕座標映射（圖片座標 → 螢幕座標）
- 支援指定繪圖區域 draw_region=(x, y, w, h)
- 插值分段（拖拽步長）
- 起落筆延遲（I/O 節流）
- 右鍵 / 左鍵繪圖按鍵選擇
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional, Sequence

import numpy as np
import pyautogui

from .state_machine import StateMachine

# PyAutoGUI 設定
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0

MIN_DELAY = 0.02  # 最小起落筆延遲（秒）
MIN_STEP_DELAY = 0.001  # 插值點之間的最小延遲，避免事件送太快


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
    cx = max(0, min(int(round(x)), sw - 1))
    cy = max(0, min(int(round(y)), sh - 1))
    return cx, cy


class CoordinateMapper:
    """
    計算圖片座標到螢幕座標的線性映射。

    用法有三種：

    1. 指定 draw_region=(x, y, w, h)
       => 圖片等比縮放後放入這個矩形，不受 avoid_* 影響

    2. 不指定 draw_region，改用 avoid_left/right/top/bottom
       => 從整個螢幕扣掉避開比例後得到可繪圖區域，圖片居中

    3. 以上任一方式確定比例尺後，再指定 anchor=(screen_x, screen_y)
       => 以 anchor 作為縮放後圖片的「中心點」覆蓋預設的居中計算
    """

    def __init__(
        self,
        img_w: int,
        img_h: int,
        *,
        draw_region: Optional[tuple[int, int, int, int]] = None,
        avoid_left: float = 0.0,
        avoid_right: float = 0.0,
        avoid_top: float = 0.0,
        avoid_bottom: float = 0.0,
        anchor: Optional[tuple[int, int]] = None,
    ) -> None:
        if img_w <= 0 or img_h <= 0:
            raise ValueError("img_w and img_h must be > 0")

        screen_w, screen_h = pyautogui.size()

        if draw_region is not None:
            x, y, w, h = draw_region
            if w <= 0 or h <= 0:
                raise ValueError(f"Invalid draw_region size: {draw_region!r}")

            draw_x = int(x)
            draw_y = int(y)
            draw_w = int(w)
            draw_h = int(h)
        else:
            if not (0.0 <= avoid_left < 1.0 and 0.0 <= avoid_right < 1.0 and
                    0.0 <= avoid_top < 1.0 and 0.0 <= avoid_bottom < 1.0):
                raise ValueError("avoid_* must be within [0.0, 1.0)")

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

        # 等比縮放，讓整張圖完整放進繪圖區
        scale = min(draw_w / img_w, draw_h / img_h)
        scaled_w = img_w * scale
        scaled_h = img_h * scale

        self.region_x = draw_x
        self.region_y = draw_y
        self.region_w = draw_w
        self.region_h = draw_h
        self.scale = scale

        if anchor is not None:
            # anchor 作為縮放後圖片的中心點，覆蓋自動居中
            ax, ay = anchor
            self.offset_x = float(ax) - scaled_w / 2.0
            self.offset_y = float(ay) - scaled_h / 2.0
        else:
            # 在繪圖區內居中
            self.offset_x = draw_x + (draw_w - scaled_w) / 2.0
            self.offset_y = draw_y + (draw_h - scaled_h) / 2.0

        # 對齊模組可累加這兩個偏移
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
            self.region_w,
            self.region_h,
        )


def _interpolate_steps(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    step_size: int,
) -> list[tuple[int, int]]:
    """
    將兩點依步長做線性插值，回傳中間點清單（不含起點，含終點）
    """
    if step_size <= 0:
        step_size = 1

    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)

    if dist == 0:
        return [(x1, y1)]

    # ceil 才能保證每一步不超過 step_size
    n = max(1, int(math.ceil(dist / step_size)))

    points: list[tuple[int, int]] = []
    for i in range(1, n + 1):
        t = i / n
        nx = x0 + dx * t
        ny = y0 + dy * t
        points.append(_clamp_screen_xy(nx, ny))
    return points


def _extract_points(cnt: np.ndarray) -> list[tuple[int, int]]:
    """
    支援輪廓形狀：
    - OpenCV 常見: (N, 1, 2)
    - 或已攤平:   (N, 2)
    """
    arr = np.asarray(cnt)

    if arr.ndim == 3 and arr.shape[1] == 1 and arr.shape[2] == 2:
        pts = arr[:, 0, :]
    elif arr.ndim == 2 and arr.shape[1] == 2:
        pts = arr
    else:
        raise ValueError(f"Unsupported contour shape: {arr.shape}")

    result: list[tuple[int, int]] = []
    for p in pts:
        x = int(p[0])
        y = int(p[1])
        result.append((x, y))
    return result


class Drawer:
    """執行自動繪圖的核心類別"""

    def __init__(
        self,
        mapper: CoordinateMapper,
        state: StateMachine,
        draw_button: str = "right",
        drag_step: int = 5,
        draw_delay: float = 0.05,
        step_delay: float = 0.001,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.mapper = mapper
        self.state = state
        self.draw_button = _normalize_button(draw_button)
        self.drag_step = max(1, int(drag_step))
        self.draw_delay = _clamp_delay(draw_delay)
        self.step_delay = _clamp_step_delay(step_delay)
        self.on_progress = on_progress

    def _safe_mouse_up(self) -> None:
        try:
            pyautogui.mouseUp(button=self.draw_button)
        except Exception:
            pass

    def _move_to(self, x: int, y: int) -> None:
        x, y = _clamp_screen_xy(x, y)
        pyautogui.moveTo(x, y, duration=0)

    def _press_at(self, x: int, y: int) -> None:
        # 先確定到位再按下，避免按在錯位位置
        self._move_to(x, y)
        time.sleep(self.draw_delay)

        # 按下繪圖鍵
        pyautogui.mouseDown(x=x, y=y, button=self.draw_button)
        time.sleep(self.draw_delay)

    def _drag_segment(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """
        從 (x0, y0) 拖到 (x1, y1)
        回傳 False 表示被停止/暫停中斷
        """
        steps = _interpolate_steps(x0, y0, x1, y1, self.drag_step)

        for nx, ny in steps:
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
          抬筆 → 移到起點 → 落筆 → 沿路徑移動 → 抬筆
        """
        total = len(contours)

        try:
            for idx, cnt in enumerate(contours):
                if not self.state.wait_if_paused():
                    break

                points = _extract_points(np.asarray(cnt))
                if len(points) < 2:
                    continue

                # 映射到螢幕起點
                sx, sy = self.mapper.to_screen(*points[0])

                # 開始前先保證抬筆
                self._safe_mouse_up()
                time.sleep(self.draw_delay)

                # 到起點並按下
                self._press_at(sx, sy)

                prev_sx, prev_sy = sx, sy

                for pt in points[1:]:
                    if not self.state.wait_if_paused():
                        self._safe_mouse_up()
                        return

                    tx, ty = self.mapper.to_screen(*pt)
                    ok = self._drag_segment(prev_sx, prev_sy, tx, ty)
                    if not ok:
                        return
                    prev_sx, prev_sy = tx, ty

                # 一條輪廓結束，放開
                self._safe_mouse_up()
                time.sleep(self.draw_delay)

                if self.on_progress:
                    self.on_progress(idx + 1, total)

        finally:
            # 無論正常完成或中斷，都確保抬筆
            self._safe_mouse_up()