"""
pywebview JS API 橋接模組
前端 JavaScript 透過 window.pywebview.api.* 呼叫這裡的方法。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

import pyautogui
import webview

from .state_machine import StateMachine
from .image_processor import (
    load_image,
    extract_foreground,
    img_to_preview_b64,
    contours_to_preview_b64,
    process_image,
    ProcessingParams,
)
from .path_optimizer import sort_contours_nearest
from .drawer import CoordinateMapper, Drawer, MIN_DELAY
from .hotkey_manager import HotkeyManager
from . import align


class DrawingAPI:
    """所有暴露給前端的 API 方法"""

    def __init__(self) -> None:
        self._window: Optional[webview.Window] = None
        self._state = StateMachine()
        self._hotkeys = HotkeyManager()

        # 原始圖片與前景提取結果
        self._img = None
        self._img_fg = None         # GrabCut 前景提取後的影像（None = 未啟用）
        self._img_path: str = ""
        self._img_w: int = 0
        self._img_h: int = 0

        # 上次使用的處理參數（供前景提取後重新計算、及繼承預設值）
        self._proc_params: ProcessingParams = ProcessingParams()

        # 輪廓列表（已排序）
        self._contours: list = []

        # 對齊用快照 / mapper
        self._snapshot = None
        self._mapper: Optional[CoordinateMapper] = None

        # 繪圖執行緒
        self._draw_thread: Optional[threading.Thread] = None

        self._hotkeys.register(
            on_start=self._on_hotkey_start,
            on_pause_resume=self._on_hotkey_pause_resume,
            on_stop=self._on_hotkey_stop,
        )

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    # ------------------------------------------------------------------ #
    #  圖片相關
    # ------------------------------------------------------------------ #

    def open_file_dialog(self) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "error": "window not ready"}
        file_types = ("Image Files (*.jpg;*.jpeg;*.png;*.bmp;*.webp)",)
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types
        )
        if result and len(result) > 0:
            return {"ok": True, "path": result[0]}
        return {"ok": False, "path": ""}

    def load_image(
        self,
        path: str,
        params: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        載入圖片，清除舊的前景提取結果，回傳預覽與尺寸。
        params: 處理參數 dict（可省略，使用預設值）。
        """
        img = load_image(path)
        if img is None:
            return {"ok": False, "error": f"無法載入圖片：{path}"}

        self._img = img
        self._img_fg = None
        self._img_path = path
        self._img_h, self._img_w = img.shape[:2]

        resp = self.update_preview(params or {})
        if resp["ok"]:
            resp["img_w"] = self._img_w
            resp["img_h"] = self._img_h
            resp["original_preview"] = img_to_preview_b64(img)
        return resp

    def update_preview(self, params: dict) -> dict[str, Any]:
        """
        以最新參數重新執行完整處理流水線並更新預覽。
        預覽圖由實際輪廓繪製生成（所見即所繪）。
        """
        if self._img is None:
            return {"ok": False, "error": "尚未載入圖片"}

        self._proc_params = ProcessingParams.from_dict(params)
        src = self._img_fg if self._img_fg is not None else self._img
        _, contours = process_image(src, self._proc_params)
        self._contours = sort_contours_nearest(contours)

        preview = contours_to_preview_b64(
            self._contours, self._img_w, self._img_h
        )
        return {
            "ok": True,
            "preview": preview,
            "contour_count": len(self._contours),
        }

    def get_original_preview(self) -> dict[str, Any]:
        """回傳原始圖片彩色縮圖（供前景框選模式顯示）"""
        if self._img is None:
            return {"ok": False, "error": "尚未載入圖片"}
        return {
            "ok": True,
            "original_preview": img_to_preview_b64(self._img),
            "img_w": self._img_w,
            "img_h": self._img_h,
        }

    # ------------------------------------------------------------------ #
    #  前景提取
    # ------------------------------------------------------------------ #

    def set_foreground_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
    ) -> dict[str, Any]:
        """
        接收正規化 [0,1] 矩形座標，在背景執行緒執行 GrabCut 前景提取。
        完成後透過 onForegroundDone 事件推送新預覽。
        """
        if self._img is None:
            return {"ok": False, "error": "尚未載入圖片"}

        img_w, img_h = self._img_w, self._img_h
        px = max(0, int(x * img_w))
        py = max(0, int(y * img_h))
        pw = max(1, min(int(w * img_w), img_w - px))
        ph = max(1, min(int(h * img_h), img_h - py))

        if pw < 5 or ph < 5:
            return {"ok": False, "error": "選取範圍太小，請圈選更大的區域"}

        saved_params = self._proc_params

        def _worker() -> None:
            try:
                fg = extract_foreground(self._img, (px, py, pw, ph))
                self._img_fg = fg
                _, contours = process_image(fg, saved_params)
                self._contours = sort_contours_nearest(contours)
                preview = contours_to_preview_b64(
                    self._contours, img_w, img_h
                )
                self._notify_ui("onForegroundDone", {
                    "preview": preview,
                    "contour_count": len(self._contours),
                })
            except Exception as exc:
                self._notify_ui("onForegroundError", {"error": str(exc)})

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True}

    def clear_foreground(self) -> dict[str, Any]:
        """清除前景提取結果，回到原始圖片"""
        self._img_fg = None
        return self.update_preview(self._proc_params.__dict__)

    # ------------------------------------------------------------------ #
    #  繪製起點選取
    # ------------------------------------------------------------------ #

    def start_pick_position(self, delay_sec: int = 3) -> dict[str, Any]:
        """倒數 delay_sec 秒後截取滑鼠位置作為繪製起點錨點"""
        delay_sec = max(1, int(delay_sec))

        def _worker() -> None:
            for remaining in range(delay_sec, 0, -1):
                self._notify_ui("onPickCountdown", {"remaining": remaining})
                time.sleep(1)
            mx, my = pyautogui.position()
            self._notify_ui("onPickDone", {"x": int(mx), "y": int(my)})

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    #  繪圖控制
    # ------------------------------------------------------------------ #

    def start_drawing(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        開始自動繪圖。
        params 欄位：
          - avoid_left/right/top/bottom : float [0,1)
          - drag_step                   : int (px)
          - draw_delay                  : float (s)
          - draw_button                 : "right" | "left"
          - anchor_x / anchor_y        : int（可選，設定後以此為繪圖中心）
        """
        if self._img is None or not self._contours:
            return {"ok": False, "error": "請先載入圖片並確認輪廓不為空"}

        if not self._state.start():
            return {"ok": False, "error": "繪圖中或狀態不允許啟動"}

        src = self._img_fg if self._img_fg is not None else self._img
        h, w = src.shape[:2]

        anchor_x = params.get("anchor_x")
        anchor_y = params.get("anchor_y")
        anchor = (int(anchor_x), int(anchor_y)) if (
            anchor_x is not None and anchor_y is not None
        ) else None

        try:
            mapper = CoordinateMapper(
                img_w=w,
                img_h=h,
                avoid_left=float(params.get("avoid_left", 0.0)),
                avoid_right=float(params.get("avoid_right", 0.0)),
                avoid_top=float(params.get("avoid_top", 0.0)),
                avoid_bottom=float(params.get("avoid_bottom", 0.0)),
                anchor=anchor,
            )
        except ValueError as exc:
            self._state.reset()
            return {"ok": False, "error": str(exc)}

        self._mapper = mapper

        drawer = Drawer(
            mapper=mapper,
            state=self._state,
            draw_button=str(params.get("draw_button", "right")),
            drag_step=int(params.get("drag_step", 5)),
            draw_delay=float(params.get("draw_delay", 0.05)),
            on_progress=self._on_progress,
        )

        contours_copy = list(self._contours)
        self._draw_thread = threading.Thread(
            target=self._draw_worker,
            args=(drawer, contours_copy),
            daemon=True,
        )
        self._draw_thread.start()
        return {"ok": True}

    def _draw_worker(self, drawer: Drawer, contours: list) -> None:
        drawer.draw_contours(contours)
        self._state.reset()
        self._notify_ui("onDrawingFinished", {})

    def pause_drawing(self) -> dict[str, Any]:
        if self._state.pause():
            if self._img is not None and self._mapper is not None:
                self._snapshot = align.take_snapshot(
                    self._mapper, self._img_w, self._img_h
                )
            return {"ok": True}
        return {"ok": False, "error": "非繪圖中狀態"}

    def resume_drawing(self, auto_align: bool = True) -> dict[str, Any]:
        if not self._state.is_paused():
            return {"ok": False, "error": "非暫停狀態"}

        if auto_align and self._snapshot is not None and self._mapper is not None:
            success = align.realign(
                self._snapshot, self._mapper, self._img_w, self._img_h
            )
            if not success:
                self._notify_ui("onAlignFailed", {})

        self._state.resume()
        return {"ok": True}

    def stop_drawing(self) -> dict[str, Any]:
        self._state.stop()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    #  熱鍵設定
    # ------------------------------------------------------------------ #

    def update_hotkeys(
        self, key_start: str, key_pause: str, key_stop: str
    ) -> dict[str, Any]:
        self._hotkeys.register(
            on_start=self._on_hotkey_start,
            on_pause_resume=self._on_hotkey_pause_resume,
            on_stop=self._on_hotkey_stop,
            key_start=key_start,
            key_pause=key_pause,
            key_stop=key_stop,
        )
        return {"ok": True, "keys": self._hotkeys.current_keys}

    def get_hotkeys(self) -> dict[str, str]:
        return self._hotkeys.current_keys

    # ------------------------------------------------------------------ #
    #  狀態查詢
    # ------------------------------------------------------------------ #

    def get_state(self) -> str:
        return self._state.state.name

    # ------------------------------------------------------------------ #
    #  內部輔助
    # ------------------------------------------------------------------ #

    def _on_progress(self, done: int, total: int) -> None:
        self._notify_ui("onProgress", {"done": done, "total": total})

    def _notify_ui(self, event: str, data: dict) -> None:
        if self._window is None:
            return
        try:
            data_json = json.dumps(data)
            js = (
                f"window.dispatchEvent("
                f"new CustomEvent('{event}', {{detail: {data_json}}}));"
            )
            self._window.evaluate_js(js)
        except Exception:
            pass

    def _on_hotkey_start(self) -> None:
        if self._state.is_idle():
            self._notify_ui("onHotkeyStart", {})

    def _on_hotkey_pause_resume(self) -> None:
        if self._state.is_drawing():
            self.pause_drawing()
            self._notify_ui("onHotkeyPause", {})
        elif self._state.is_paused():
            self.resume_drawing()
            self._notify_ui("onHotkeyResume", {})

    def _on_hotkey_stop(self) -> None:
        self.stop_drawing()
        self._notify_ui("onHotkeyStop", {})
