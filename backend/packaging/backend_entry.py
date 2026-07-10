"""PyInstaller 入口：运行桌面侧车，而不是打印应用名称。"""

from app.desktop_server import main


if __name__ == "__main__":
    raise SystemExit(main())
