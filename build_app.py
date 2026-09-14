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
DIST_APP = HERE / "dist" / f"{APP_NAME}.app"

PY = sys.executable


def _render_symbol_icon(symbol_name: str, size: int, out: Path) -> bool:
    """把 macOS SF Symbol 渲染成一张精确尺寸的高清 PNG 模板图标。

    直接用矢量符号生成 1x / 2x 图标，保证菜单栏放大时仍然锐利，
    同时给 rumps 的初始化阶段留一个可靠的 fallback。
    """
    try:
        from AppKit import (
            NSBezierPath,
            NSBitmapImageRep,
            NSColor,
            NSGraphicsContext,
            NSImage,
            NSMakeRect,
            NSPNGFileType,
        )
    except Exception:  # noqa: BLE001
        return False

    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, None)
    if img is None:
        return False

    # 精确控制输出像素：1x = 18px，@2x = 36px，避免 lockFocus 受屏幕缩放影响
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0
    )
    rep.setSize_((size, size))

    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    NSColor.clearColor().set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, size, size))
    # 让符号占满画布 85%，留出一点边距，视觉上和系统菜单栏图标对齐
    inset = size * 0.075
    rect = NSMakeRect(inset, inset, size - inset * 2, size - inset * 2)
    img.setSize_((rect.size.width, rect.size.height))
    img.drawInRect_(rect)

    NSGraphicsContext.restoreGraphicsState()

    png = rep.representationUsingType_properties_(NSPNGFileType, None)
    if png is None:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    png.writeToFile_atomically_(str(out), True)
    return True


def main() -> int:
    # PyInstaller 缓存放到项目内（避免 ~/Library/Application Support 被沙箱/权限拦截）
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(HERE / ".pyinstaller-cache")
    (HERE / ".pyinstaller-cache").mkdir(parents=True, exist_ok=True)

    # 0) 先生成高清菜单栏图标（和 app_main 里运行时用的是同一个符号）
    try:
        _render_symbol_icon("icloud.and.arrow.down.fill", 18, HERE / "assets" / "iconTemplate.png")
        _render_symbol_icon("icloud.and.arrow.down.fill", 36, HERE / "assets" / "iconTemplate@2x.png")
        print("已生成高清菜单栏图标")
    except Exception as e:  # noqa: BLE001
        print(f"（图标生成跳过：{e}）")

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
        "--noconfirm",
        "--clean",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--add-data", f"{HERE / 'assets' / 'iconTemplate.png'}{os.pathsep}assets",
        "--add-data", f"{HERE / 'assets' / 'iconTemplate@2x.png'}{os.pathsep}assets",
        "--collect-all", "p115client",
        "--hidden-import", "requests",
        "--hidden-import", "qrcode",
        "--hidden-import", "PIL",
        # 这几个 UI 模块是在函数体内动态 import 的，静态分析扫不到，要显式声明
        "--hidden-import", "ui_theme",
        "--hidden-import", "ui_transfer",
        "--hidden-import", "ui_login",
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
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    # 4) ad-hoc 签名
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(DIST_APP)],
        check=True,
    )
    # 5) 顺手打一个 zip，方便分发/备份
    try:
        zip_path = HERE / "dist" / f"{APP_NAME}-v1.4.zip"
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
             str(DIST_APP), str(zip_path)],
            check=True,
        )
        print(f"压缩包：{zip_path}")
    except Exception as e:  # noqa: BLE001
        print(f"（跳过压缩包：{e}）")

    print(f"完成：{DIST_APP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
