"""
圖像處理模組
- 載入圖片（支援中文路徑）
- 灰階化 + Canny 邊緣偵測
- 輪廓擷取與最小長度過濾
- 高保真輪廓逼近（保留細節）
- GrabCut 前景提取
- 產生 Pillow 預覽圖（邊緣圖 / 原圖彩色）
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import cv2
import numpy as np
from PIL import Image


SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


def load_image(path: str) -> Optional[np.ndarray]:
    """
    以 np.fromfile + cv2.imdecode 載入圖片，相容含中文的 Windows 路徑。
    失敗時回傳 None。
    """
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def detect_edges(
    img: np.ndarray,
    threshold_low: int,
    threshold_high: int,
) -> np.ndarray:
    """灰階化後做 Canny 邊緣偵測，回傳邊緣二值圖"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, threshold_low, threshold_high)
    return edges


def extract_contours(
    edges: np.ndarray,
    min_length: int,
) -> list[np.ndarray]:
    """
    從邊緣圖擷取輪廓，過濾過短輪廓（去噪），
    並做高保真逼近（小 epsilon），保留髮絲與細節。
    """
    contours, _ = cv2.findContours(
        edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE
    )
    result = []
    for cnt in contours:
        if len(cnt) < min_length:
            continue
        # 高保真逼近：epsilon 極小，盡量保留所有彎曲點
        epsilon = 0.001 * cv2.arcLength(cnt, closed=False)
        approx = cv2.approxPolyDP(cnt, epsilon, closed=False)
        result.append(approx)
    return result


def extract_foreground(
    img: np.ndarray,
    rect: tuple[int, int, int, int],
    iterations: int = 5,
) -> np.ndarray:
    """
    使用 GrabCut 擷取前景，背景填白，回傳與原圖同尺寸的 BGR 圖片。
    rect: (x, y, w, h) 為原始圖片像素座標。
    """
    h, w = img.shape[:2]
    x, y, rw, rh = rect

    x  = max(0, min(x, w - 2))
    y  = max(0, min(y, h - 2))
    rw = max(1, min(rw, w - x))
    rh = max(1, min(rh, h - y))

    mask      = np.zeros((h, w), np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    cv2.grabCut(img, mask, (x, y, rw, rh), bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_RECT)

    fg_mask = np.where(
        (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    result = img.copy()
    result[fg_mask == 0] = 255  # 背景設為白色
    return result


def img_to_preview_b64(img: np.ndarray, max_size: int = 400) -> str:
    """
    將原始 BGR 圖片縮圖後轉成 base64 PNG 字串（彩色），供前端框選前景時顯示。
    """
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def edges_to_preview_b64(edges: np.ndarray, max_size: int = 400) -> str:
    """
    將 Canny 邊緣圖縮圖後轉成 base64 PNG 字串，供前端 <img> 顯示。
    """
    pil_img = Image.fromarray(edges)
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"
