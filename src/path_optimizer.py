"""
路徑排序最佳化模組
對輪廓清單做貪婪最近鄰排序，減少抬筆後的空跑距離。
"""
from __future__ import annotations

import numpy as np


def _contour_start(cnt: np.ndarray) -> tuple[float, float]:
    """取輪廓第一個點座標"""
    pt = cnt[0][0]
    return float(pt[0]), float(pt[1])


def _contour_end(cnt: np.ndarray) -> tuple[float, float]:
    """取輪廓最後一個點座標"""
    pt = cnt[-1][0]
    return float(pt[0]), float(pt[1])


def sort_contours_nearest(
    contours: list[np.ndarray],
) -> list[np.ndarray]:
    """
    貪婪最近鄰排序：
    每次從當前抬筆位置找距離最短的下一條輪廓起點。
    """
    if not contours:
        return []

    remaining = list(contours)
    sorted_result: list[np.ndarray] = []

    # 從第一條開始
    current_end = _contour_start(remaining[0])
    sorted_result.append(remaining.pop(0))
    current_end = _contour_end(sorted_result[-1])

    while remaining:
        ex, ey = current_end
        best_idx = 0
        best_dist = float("inf")

        for i, cnt in enumerate(remaining):
            sx, sy = _contour_start(cnt)
            dist = (sx - ex) ** 2 + (sy - ey) ** 2
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        sorted_result.append(remaining.pop(best_idx))
        current_end = _contour_end(sorted_result[-1])

    return sorted_result
