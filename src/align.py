"""
暫停恢復自動對齊模組
流程：
  1. 暫停時截取繪圖區中央 50% 作為 Snapshot（模板）
  2. 恢復時在繪圖區截圖，用 cv2.matchTemplate 搜尋模板
  3. 找不到時捲動畫面多輪搜尋
  4. 找到後拖動畫面校正，並更新 CoordinateMapper 的全域偏移
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import cv2
import numpy as np
import pyautogui

from .drawer import CoordinateMapper


MATCH_THRESHOLD = 0.7   # 模板比對最低相似度
SCROLL_STEPS = 5        # 每輪捲動格數
MAX_SCROLL_ROUNDS = 6   # 最大搜尋輪數


def _screenshot_region(x: int, y: int, w: int, h: int) -> np.ndarray:
    """截取指定螢幕區域，回傳 BGR ndarray"""
    pil_img = pyautogui.screenshot(region=(x, y, w, h))
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def take_snapshot(mapper: CoordinateMapper, img_w: int, img_h: int) -> np.ndarray:
    """
    截取繪圖區中央 50% 範圍作為暫停快照（模板）。
    """
    screen_w, screen_h = pyautogui.size()

    cx = int(mapper.offset_x + img_w * mapper.scale * 0.25)
    cy = int(mapper.offset_y + img_h * mapper.scale * 0.25)
    cw = int(img_w * mapper.scale * 0.5)
    ch = int(img_h * mapper.scale * 0.5)

    # 確保不超出螢幕邊界
    cx = max(0, min(cx, screen_w - cw))
    cy = max(0, min(cy, screen_h - ch))

    return _screenshot_region(cx, cy, cw, ch)


def _try_match(
    template: np.ndarray,
    search_region: Tuple[int, int, int, int],
) -> Optional[Tuple[int, int, float]]:
    """
    在 search_region 中搜尋模板，回傳 (found_x, found_y, score) 或 None。
    座標為螢幕絕對座標。
    """
    rx, ry, rw, rh = search_region
    scene = _screenshot_region(rx, ry, rw, rh)

    if scene.shape[0] < template.shape[0] or scene.shape[1] < template.shape[1]:
        return None

    result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= MATCH_THRESHOLD:
        found_x = rx + max_loc[0] + template.shape[1] // 2
        found_y = ry + max_loc[1] + template.shape[0] // 2
        return found_x, found_y, max_val
    return None


def realign(
    snapshot: np.ndarray,
    mapper: CoordinateMapper,
    img_w: int,
    img_h: int,
) -> bool:
    """
    恢復繪圖前執行對齊：
      - 在繪圖區截圖比對
      - 找不到則上下捲動多輪搜尋
      - 找到後拖動螢幕校正，並更新 mapper 偏移
    回傳是否對齊成功。
    """
    screen_w, screen_h = pyautogui.size()
    draw_x = int(mapper.offset_x)
    draw_y = int(mapper.offset_y)
    draw_w = int(img_w * mapper.scale)
    draw_h = int(img_h * mapper.scale)

    search_region = (
        max(0, draw_x),
        max(0, draw_y),
        min(draw_w, screen_w - draw_x),
        min(draw_h, screen_h - draw_y),
    )

    # 計算模板應在的原始中心位置
    template_cx = draw_x + draw_w // 2
    template_cy = draw_y + draw_h // 2

    # 第一輪：直接在繪圖區搜尋
    match = _try_match(snapshot, search_region)

    if match is None:
        # 捲動搜尋：先向下再向上
        directions = [1, -1]
        for direction in directions:
            for _ in range(MAX_SCROLL_ROUNDS // 2):
                pyautogui.scroll(SCROLL_STEPS * direction)
                time.sleep(0.3)
                match = _try_match(snapshot, search_region)
                if match is not None:
                    break
            if match is not None:
                break

    if match is None:
        return False

    found_x, found_y, _ = match

    # 計算偏移量（模板找到的位置 vs 原應在的位置）
    dx = template_cx - found_x
    dy = template_cy - found_y

    if abs(dx) < 2 and abs(dy) < 2:
        return True

    # 用左鍵拖動畫面校正（模擬地圖拖移）
    drag_start_x = screen_w // 2
    drag_start_y = screen_h // 2
    pyautogui.mouseDown(drag_start_x, drag_start_y, button="left")
    time.sleep(0.05)
    pyautogui.moveTo(drag_start_x + dx, drag_start_y + dy, duration=0.3)
    time.sleep(0.05)
    pyautogui.mouseUp(button="left")
    time.sleep(0.1)

    # 更新 mapper 的全域偏移補償
    mapper.apply_offset(dx, dy)
    return True
