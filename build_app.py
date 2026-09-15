#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_app.py — 把 115 秒转打包成 macOS 菜单栏应用（.app）

产物：dist/115QuickTransfer.app
说明：打包后自动完成两件事
  1. 在 Info.plist 中写入 LSUIElement=true（菜单栏常驻、不占 Dock）
  2. ad-hoc 代码签名（Apple Silicon 本地运行必需）

用法：
  python3 build_app.py
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_NAME = "115QuickTransfer"
APP_VERSION = "2.0.0"          # 发布版本号：改这里，Info.plist 与 zip 名一起跟着变
MIN_MACOS = "12.0"          # 最低系统版本
DIST_APP = HERE / "dist" / f"{APP_NAME}.app"

PY = sys.executable


def main() -> int:
    # PyInstaller 缓存放到项目内（避免 ~/Library/Application Support 被沙箱/权限拦截）
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(HERE / ".pyinstaller-cache")
    (HERE / ".pyinstaller-cache").mkdir(parents=True, exist_ok=True)

    # 0) 从 assets/src 里的原图生成图标资源：
    #    · AppIcon.icns（应用图标，给 PyInstaller 用）
    #    · iconTemplate.png / @2x / @3x（菜单栏模板图，给 app_main 运行时用）
    #    放到打包流程里而不是手工跑脚本，是为了避免"改了原图忘了重新生成图标"。
    import make_icons

    make_icons.generate()

    # 1) 清理旧的 dist/build
    for d in (HERE / "dist", HERE / "build"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    # 2) 用 PyInstaller 打包（windowed 菜单栏应用）
    import PyInstaller.__main__

    args = [
        str(HERE / "app_main.py"),
        "--name", APP_NAME,
        "--windowed",
        # 应用图标（访达/启动台里显示的那个）。之前一直没传，用的是 PyInstaller
        # 自带的默认图标。
        "--icon", str(HERE / "assets" / "AppIcon.icns"),
        "--noconfirm",
        "--clean",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--add-data", f"{HERE / 'assets' / 'iconTemplate.png'}{os.pathsep}assets",
        "--add-data", f"{HERE / 'assets' / 'iconTemplate@2x.png'}{os.pathsep}assets",
        "--add-data", f"{HERE / 'assets' / 'iconTemplate@3x.png'}{os.pathsep}assets",
        "--collect-all", "p115client",
        "--hidden-import", "requests",
        "--hidden-import", "qrcode",
        "--hidden-import", "PIL",
        # 这几个 UI 模块是在函数体内动态 import 的，静态分析扫不到，要显式声明
        "--hidden-import", "ui_theme",
        "--hidden-import", "ui_transfer",
        "--hidden-import", "ui_login",
        "--hidden-import", "ui_share",
        "--hidden-import", "dir_picker",
        "--collect-submodules", "p115client.tool.clouddownload",
        "--collect-submodules", "p115client.tool.iterdir",
        # 注：ui_theme 用的 CAMediaTimingFunction 走运行时 objc.lookUpClass，
        # 由系统 QuartzCore 框架在运行期提供，不需要（也无法）把 QuartzCore 打进包里。
    ]
    PyInstaller.__main__.run(args)

    if not DIST_APP.exists():
        print("打包失败：未生成 .app", file=sys.stderr)
        return 1

    # 3) Info.plist：LSUIElement=true + NSPrincipalClass，菜单栏常驻且启动稳定
    plist_path = DIST_APP / "Contents" / "Info.plist"
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)
    plist["LSUIElement"] = True
    plist["NSPrincipalClass"] = "NSApplication"
    plist["CFBundleShortVersionString"] = APP_VERSION
    plist["CFBundleVersion"] = APP_VERSION
    plist["LSMinimumSystemVersion"] = MIN_MACOS
    plist["NSHumanReadableCopyright"] = "MIT License"
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    # 4) ad-hoc 签名
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(DIST_APP)],
        check=True,
    )
    # 5) 顺手打一个 zip，方便分发/备份
    try:
        zip_path = HERE / "dist" / f"{APP_NAME}-v{APP_VERSION}.zip"
        subprocess.run(
            ["ditto", "-c", "-k", "--keepParent", str(DIST_APP), str(zip_path)],
            check=True,
        )
        print(f"压缩包：{zip_path}")
    except Exception as e:  # noqa: BLE001
        print(f"（跳过压缩包：{e}）")

    print(f"完成：{DIST_APP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
