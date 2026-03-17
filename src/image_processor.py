"""
圖像處理模組

完整流水線：
  CLAHE 對比增強 → 前處理平滑 → Canny 邊緣
  → 形態學後處理 → 細線化（Thinning）
  → 輪廓擷取 → 過濾 → D-P 簡化

各步驟均可透過 ProcessingParams 獨立開關。
預覽圖由實際輪廓繪製產生（所見即所繪）。
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from PIL import Image

# ── Thinning 相容層 ─────────────────────────────
# 需要 opencv-contrib-python；僅有 opencv-python 時自動 fallback
try:
    from cv2.ximgproc import thinning as _cv_thinning  # type: ignore[attr-defined]
    HAS_THINNING = True
except Exception:
    HAS_THINNING = False


SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# ══════════════════════════════════════════════════
#  處理參數
# ══════════════════════════════════════════════════

@dataclass
class ProcessingParams:
    # ── Canny 邊緣偵測 ─────────────────────────
    threshold_low: int = 50
    threshold_high: int = 150

    # ── CLAHE 局部對比增強 ──────────────────────
    use_clahe: bool = True
    clahe_clip: float = 2.0        # clipLimit，越大越強，通常 1–4

    # ── 前處理平滑 ──────────────────────────────
    use_bilateral: bool = True
    bilateral_d: int = 9           # 鄰域直徑（固定，太大會非常慢）
    bilateral_sigma: float = 75.0  # sigmaColor & sigmaSpace 共用

    use_median: bool = False
    median_ksize: int = 5          # 必須為奇數

    # ── 形態學後處理 ────────────────────────────
    use_morph_close: bool = True
    morph_close_ksize: int = 3

    use_morph_open: bool = False
    morph_open_ksize: int = 3

    # ── 細線化（Thinning）──────────────────────
    # 消除「沿粗邊界兩側來回走」的根本問題
    # 需要 opencv-contrib-python；缺少時自動跳過
    use_thinning: bool = True

    # ── 分區域過濾（人像專用）──────────────────
    # 上半部保留五官細節；下半部強力抑制衣物紋理
    use_region_filter: bool = False
    region_split_pct: int = 58     # 分割線位置（距頂部的百分比）
    region_lower_sigma: int = 120  # 下半部 Bilateral sigma（通常比全域更大）
    region_lower_thresh_low: int = 80
    region_lower_thresh_high: int = 180

    # ── 輪廓過濾 ───────────────────────────────
    min_length: int = 10           # 最少點數（D-P 之前）
    min_area: int = 0              # 最小面積 px²（0 = 不過濾）

    # ── Douglas-Peucker 路徑簡化 ───────────────
    use_approx: bool = True
    approx_epsilon_ppm: int = 10   # ‰ of arc length；較大值 = 更簡化

    # ──────────────────────────────────────────
    @classmethod
    def from_dict(cls, d: dict) -> ProcessingParams:
        def _b(v, default: bool) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, int):
                return bool(v)
            if isinstance(v, str):
                return v.lower() not in ("false", "0", "")
            return default

        return cls(
            threshold_low=int(d.get("threshold_low", 50)),
            threshold_high=int(d.get("threshold_high", 150)),
            use_clahe=_b(d.get("use_clahe"), True),
            clahe_clip=float(d.get("clahe_clip", 2.0)),
            use_bilateral=_b(d.get("use_bilateral"), True),
            bilateral_d=int(d.get("bilateral_d", 9)),
            bilateral_sigma=float(d.get("bilateral_sigma", 75.0)),
            use_median=_b(d.get("use_median"), False),
            median_ksize=int(d.get("median_ksize", 5)),
            use_morph_close=_b(d.get("use_morph_close"), True),
            morph_close_ksize=int(d.get("morph_close_ksize", 3)),
            use_morph_open=_b(d.get("use_morph_open"), False),
            morph_open_ksize=int(d.get("morph_open_ksize", 3)),
            use_thinning=_b(d.get("use_thinning"), True),
            use_region_filter=_b(d.get("use_region_filter"), False),
            region_split_pct=int(d.get("region_split_pct", 58)),
            region_lower_sigma=int(d.get("region_lower_sigma", 120)),
            region_lower_thresh_low=int(d.get("region_lower_thresh_low", 80)),
            region_lower_thresh_high=int(d.get("region_lower_thresh_high", 180)),
            min_length=int(d.get("min_length", 10)),
            min_area=int(d.get("min_area", 0)),
            use_approx=_b(d.get("use_approx"), True),
            approx_epsilon_ppm=int(d.get("approx_epsilon_ppm", 10)),
        )


# ══════════════════════════════════════════════════
#  圖片載入
# ══════════════════════════════════════════════════

def load_image(path: str) -> Optional[np.ndarray]:
    """以 np.fromfile + cv2.imdecode 載入圖片，相容含中文的 Windows 路徑。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


# ══════════════════════════════════════════════════
#  核心流水線（私有輔助）
# ══════════════════════════════════════════════════

def _odd(n: int, minimum: int = 1) -> int:
    """確保核大小為奇數且 ≥ minimum"""
    n = max(minimum, int(n))
    return n if n % 2 == 1 else n + 1


def _apply_clahe(gray: np.ndarray, clip: float) -> np.ndarray:
    clahe = cv2.createCLAHE(
        clipLimit=float(clip),
        tileGridSize=(8, 8),
    )
    return clahe.apply(gray)


def _smooth_gray(
    gray: np.ndarray,
    params: ProcessingParams,
    bilateral_d: int,
    bilateral_sigma: float,
) -> np.ndarray:
    """對灰階圖套用 bilateral（可選）和 median（可選）"""
    if params.use_bilateral:
        sigma = float(bilateral_sigma)
        gray = cv2.bilateralFilter(gray, bilateral_d, sigma, sigma)
    if params.use_median:
        ksize = _odd(params.median_ksize, 3)
        gray = cv2.medianBlur(gray, ksize)
    return gray


def _build_edges(img: np.ndarray, params: ProcessingParams) -> np.ndarray:
    """
    從 BGR 圖片建立邊緣圖。
    支援全域模式（統一參數）與分區域模式（上半/下半各自調參）。
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # CLAHE 放最前：先增強對比再做任何平滑
    if params.use_clahe:
        gray = _apply_clahe(gray, params.clahe_clip)

    if params.use_region_filter:
        # ── 分區域模式 ─────────────────────────────
        split_y = max(1, min(h - 1, int(h * params.region_split_pct / 100)))
        top_gray    = gray[:split_y]
        bottom_gray = gray[split_y:]

        # 上半部：輕量平滑，保留五官細節
        upper_sigma = min(float(params.bilateral_sigma), 35.0)
        top_smooth  = _smooth_gray(top_gray, params,
                                   bilateral_d=5,
                                   bilateral_sigma=upper_sigma)
        top_edges   = cv2.Canny(top_smooth,
                                max(1, params.threshold_low),
                                max(2, params.threshold_high))

        # 下半部：強力平滑，抑制衣物紋理
        lower_sigma   = float(params.region_lower_sigma)
        bottom_smooth = cv2.bilateralFilter(
            bottom_gray, params.bilateral_d, lower_sigma, lower_sigma
        )
        if params.use_median:
            bottom_smooth = cv2.medianBlur(bottom_smooth, _odd(params.median_ksize, 3))
        bottom_edges = cv2.Canny(bottom_smooth,
                                 max(1, params.region_lower_thresh_low),
                                 max(2, params.region_lower_thresh_high))

        edges = np.zeros((h, w), dtype=np.uint8)
        edges[:split_y] = top_edges
        edges[split_y:] = bottom_edges
    else:
        # ── 全域模式 ───────────────────────────────
        gray  = _smooth_gray(gray, params,
                             params.bilateral_d,
                             params.bilateral_sigma)
        edges = cv2.Canny(gray,
                          max(1, params.threshold_low),
                          max(2, params.threshold_high))

    return edges


# ══════════════════════════════════════════════════
#  主流水線
# ══════════════════════════════════════════════════

def process_image(
    img: np.ndarray,
    params: ProcessingParams,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """
    完整影像處理流水線。
    回傳 (edges_image, contours_list)。

    edges_image  : 最終邊緣圖（形態學 + thinning 後），供除錯 / 預覽
    contours_list: 實際繪圖路徑，已過濾並做 D-P 簡化
    """
    # ── Step 1-2: CLAHE + 平滑 + Canny ──────────
    edges = _build_edges(img, params)

    # ── Step 3: 形態學後處理 ─────────────────────
    if params.use_morph_close:
        k = _odd(params.morph_close_ksize, 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    if params.use_morph_open:
        k = _odd(params.morph_open_ksize, 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        edges = cv2.morphologyEx(edges, cv2.MORPH_OPEN, kernel)

    # ── Step 4: 細線化（Thinning）───────────────
    # 把粗邊緣壓縮成單像素中心線，消除「沿邊界來回走」問題
    if params.use_thinning and HAS_THINNING:
        edges = _cv_thinning(edges)

    # ── Step 5: 輪廓擷取 ─────────────────────────
    raw_contours, _ = cv2.findContours(
        edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE
    )

    # ── Step 6: 過濾 + D-P 簡化 ─────────────────
    result: list[np.ndarray] = []
    epsilon_ratio = params.approx_epsilon_ppm / 1000.0

    for cnt in raw_contours:
        if len(cnt) < params.min_length:
            continue
        if params.min_area > 0 and cv2.contourArea(cnt) < params.min_area:
            continue

        if params.use_approx:
            arc_len = cv2.arcLength(cnt, closed=False)
            if arc_len > 0:
                cnt = cv2.approxPolyDP(cnt, epsilon_ratio * arc_len, closed=False)

        if len(cnt) >= 2:
            result.append(cnt)

    return edges, result


# ══════════════════════════════════════════════════
#  GrabCut 前景提取
# ══════════════════════════════════════════════════

def extract_foreground(
    img: np.ndarray,
    rect: tuple[int, int, int, int],
    iterations: int = 5,
) -> np.ndarray:
    """使用 GrabCut 擷取前景，背景填白。rect: (x, y, w, h) 像素座標。"""
    h, w = img.shape[:2]
    x, y, rw, rh = rect
    x  = max(0, min(x, w - 2))
    y  = max(0, min(y, h - 2))
    rw = max(1, min(rw, w - x))
    rh = max(1, min(rh, h - y))

    mask      = np.zeros((h, w), np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    cv2.grabCut(img, mask, (x, y, rw, rh),
                bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_RECT)

    fg_mask = np.where(
        (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    result = img.copy()
    result[fg_mask == 0] = 255
    return result


# ══════════════════════════════════════════════════
#  預覽圖生成
# ══════════════════════════════════════════════════

def contours_to_preview_b64(
    contours: list[np.ndarray],
    img_w: int,
    img_h: int,
    max_size: int = 400,
) -> str:
    """
    將實際繪圖輪廓畫在黑色畫布上生成預覽圖（所見即所繪）。
    確保預覽顯示的正是繪圖路徑，而非原始 Canny 像素。
    """
    canvas = np.zeros((img_h, img_w), dtype=np.uint8)
    if contours:
        cv2.drawContours(canvas, contours, -1, 255, 1)
    pil_img = Image.fromarray(canvas)
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def img_to_preview_b64(img: np.ndarray, max_size: int = 400) -> str:
    """將原始 BGR 圖片縮圖後轉成 base64 PNG（彩色），供前景框選顯示。"""
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
