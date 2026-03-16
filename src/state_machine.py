"""
狀態機模組
管理繪圖流程的五種狀態：待機 / 繪圖中 / 暫停 / 強制停止 / 重置
"""
import threading
from enum import Enum, auto


class State(Enum):
    IDLE = auto()       # 待機
    DRAWING = auto()    # 繪圖中
    PAUSED = auto()     # 暫停
    STOPPED = auto()    # 強制停止
    RESETTING = auto()  # 重置 UI


class StateMachine:
    def __init__(self) -> None:
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 預設不暫停

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def _set(self, new_state: State) -> None:
        with self._lock:
            self._state = new_state

    def start(self) -> bool:
        """嘗試從 IDLE / STOPPED 進入 DRAWING；失敗回傳 False"""
        with self._lock:
            if self._state in (State.IDLE, State.STOPPED, State.RESETTING):
                self._state = State.DRAWING
                self._pause_event.set()
                return True
            return False

    def pause(self) -> bool:
        """從 DRAWING 切換到 PAUSED"""
        with self._lock:
            if self._state == State.DRAWING:
                self._state = State.PAUSED
                self._pause_event.clear()
                return True
            return False

    def resume(self) -> bool:
        """從 PAUSED 切換回 DRAWING"""
        with self._lock:
            if self._state == State.PAUSED:
                self._state = State.DRAWING
                self._pause_event.set()
                return True
            return False

    def stop(self) -> None:
        """強制停止，喚醒暫停等待"""
        with self._lock:
            self._state = State.STOPPED
            self._pause_event.set()

    def reset(self) -> None:
        """重置為 IDLE"""
        with self._lock:
            self._state = State.IDLE
            self._pause_event.set()

    def wait_if_paused(self) -> bool:
        """
        在繪圖執行緒中呼叫：若暫停則阻塞直到恢復或停止。
        回傳 False 代表應終止繪圖迴圈。
        """
        self._pause_event.wait()
        return self._state not in (State.STOPPED,)

    def is_drawing(self) -> bool:
        return self._state == State.DRAWING

    def is_paused(self) -> bool:
        return self._state == State.PAUSED

    def is_stopped(self) -> bool:
        return self._state == State.STOPPED

    def is_idle(self) -> bool:
        return self._state == State.IDLE
