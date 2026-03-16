"""
全域熱鍵管理模組
預設：F8 開始 / F9 暫停‧繼續 / F10 停止
使用者可自訂按鍵名稱（keyboard 函式庫格式）
"""
from __future__ import annotations

from typing import Callable, Optional

import keyboard


class HotkeyManager:
    def __init__(self) -> None:
        self._hooks: list = []
        self._key_start = "f8"
        self._key_pause = "f9"
        self._key_stop = "f10"

    def register(
        self,
        on_start: Callable[[], None],
        on_pause_resume: Callable[[], None],
        on_stop: Callable[[], None],
        key_start: Optional[str] = None,
        key_pause: Optional[str] = None,
        key_stop: Optional[str] = None,
    ) -> None:
        """
        註冊三個全域熱鍵。
        若提供自訂按鍵名稱，會覆蓋預設值。
        """
        self.unregister()

        if key_start:
            self._key_start = key_start.lower()
        if key_pause:
            self._key_pause = key_pause.lower()
        if key_stop:
            self._key_stop = key_stop.lower()

        self._hooks.append(
            keyboard.add_hotkey(self._key_start, on_start, suppress=False)
        )
        self._hooks.append(
            keyboard.add_hotkey(self._key_pause, on_pause_resume, suppress=False)
        )
        self._hooks.append(
            keyboard.add_hotkey(self._key_stop, on_stop, suppress=False)
        )

    def unregister(self) -> None:
        """移除所有已註冊的熱鍵"""
        for hook in self._hooks:
            try:
                keyboard.remove_hotkey(hook)
            except Exception:
                pass
        self._hooks.clear()

    @property
    def current_keys(self) -> dict[str, str]:
        return {
            "start": self._key_start,
            "pause": self._key_pause,
            "stop": self._key_stop,
        }
