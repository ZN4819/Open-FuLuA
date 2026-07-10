"""CD-1 的最小后端打包入口；实际侧车生命周期在 CD-4 接入。"""

from app.main import app


def packaged_app_name() -> str:
    return app.title


if __name__ == "__main__":
    print(packaged_app_name())
