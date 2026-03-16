"""
SlaytheSpire2 img2drawing
桌面自動化繪圖工具 - 入口點
"""
import sys
import os
import webview
from src.api import DrawingAPI


def get_web_dir() -> str:
    """取得 web 目錄的絕對路徑（相容打包後環境）"""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "web")


def main() -> None:
    api = DrawingAPI()
    window = webview.create_window(
        title="img2drawing",
        url=os.path.join(get_web_dir(), "index.html"),
        js_api=api,
        width=900,
        height=700,
        min_size=(700, 550),
        resizable=True,
        on_top=False,
    )
    api.set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
