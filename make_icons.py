#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_icons.py — 从 assets/src/ 里的原图生成两个图标资源

  输入：
    assets/src/app-icon.png      桌面图标原图（蓝色圆角方块 + 白色实心图形）
    assets/src/menu-icon-line.png 菜单栏图标原图（黑线稿 + 白底）

  输出：
    assets/AppIcon.icns          macOS 应用图标（含 16~1024 全尺寸）
    assets/AppIcon-1024.png      应用图标母版（透明底，便于预览/复用）
    assets/iconTemplate.png      菜单栏模板图 1x（18px）
    assets/iconTemplate@2x.png   菜单栏模板图 2x（36px）
    assets/iconTemplate@3x.png   菜单栏模板图 3x（54px）

设计说明（为什么不能直接用原图）：

  · 菜单栏原图的笔画只有 4px / 1024，占图形高度的 0.6%。放到 18pt 菜单栏
    （Retina 上 32 设备像素）后笔画约 0.19 像素 —— 直接隐形。所以菜单栏图标
    必须"加粗到能在小尺寸存活"，不能照抄原图。
  · 桌面原图是"蓝色圆角方块铺满整张画布 + 白底四角"。macOS 原生图标是
    824/1024 的圆角方块居中、四周透明，因此这里缩放并留白，避免在访达里
    比别的 App 大一圈。

用法：
  python3 make_icons.py                      # 生成全部图标
  python3 make_icons.py --menu-kind solid    # 指定菜单栏图标取图方式
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

HERE = Path(__file__).resolve().parent
SRC_DIR = HERE / "assets" / "src"
APP_SRC = SRC_DIR / "app-icon.png"
MENU_SRC = SRC_DIR / "menu-icon-line.png"

# macOS 原生应用图标里，圆角方块占整张画布的比例（824 / 1024）
APP_BODY_RATIO = 824 / 1024

# 菜单栏图标画布尺寸（点）与内容占比
MENU_BASE = 18
MENU_INSET = 0.055          # 上下各留 5.5% ⇒ 内容高 ≈ 89% × 18pt ≈ 16pt

# 菜单栏取图方式（2026-09-14 在本机真实菜单栏里 A/B 选出来的）：
#   "solid" —— 用桌面图标里的白色实心形状（高对比、粗壮）
#   "line"  —— 用原线稿，按 MENU_LINE_THICKEN 加粗（保留原设计的线条风格）
#
# 为什么默认 solid：把两个候选都装到真实菜单栏里截图对比过 —— 线稿版在 18pt 下
# 笔画只有约 1 个设备像素、整体发灰，明显比相邻的系统图标轻；实心版和旧的
# SF Symbol 云图标一个量级，一眼能认出"磁铁 + 下载 + 115 文件夹"。
# 线稿版并没有删掉，改这一个常量即可切回。
MENU_KIND = "solid"
MENU_LINE_THICKEN = 12      # 线稿向两侧各膨胀的像素数（原图 1024 尺度）
MENU_SOLID_FILL_HOLES = False  # True = 把镂空的「5」填实
# 只给"下载箭头"那个连通块单独加粗（向两侧各 N 像素，原图 1024 尺度）。
# 源图里箭头本来就是细线，在 18pt 菜单栏下只有约 1 个设备像素、看着发灰；
# 磁铁和文件夹是实心大块，不需要动，而放大它们会顺带把镂空的「5」堵死。
MENU_SOLID_THICKEN_ARROW = 6


# --------------------------------------------------------------------- 通用
def _premul_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """预乘 alpha 后再缩放。

    直接对 RGBA 做 LANCZOS 缩放会把透明区域的黑色混进边缘像素，产生一圈
    灰/浅色光晕（图标边缘看起来"脏"）。预乘 → 缩放 → 反预乘可以消除。
    """
    r, g, b, a = im.split()
    zero = Image.new("L", im.size, 0)
    pm = Image.merge("RGBA", (Image.composite(r, zero, a),
                              Image.composite(g, zero, a),
                              Image.composite(b, zero, a), a))
    pm = pm.resize(size, Image.LANCZOS)
    pr, pg, pb, pa = pm.split()
    adata = pa.tobytes()
    chans = []
    for ch in (pr, pg, pb):
        cdata = ch.tobytes()
        chans.append(Image.frombytes("L", pm.size, bytes(
            0 if adata[i] == 0 else min(255, round(cdata[i] * 255 / adata[i]))
            for i in range(len(cdata)))))
    return Image.merge("RGBA", (*chans, pa))


def _tight_alpha(icon: Image.Image, thresh: int = 110) -> Image.Image:
    """按较高阈值裁掉四周几乎透明的杂边。"""
    bb = icon.getchannel("A").point(lambda v: 255 if v > thresh else 0).getbbox()
    return icon.crop(bb)


# ------------------------------------------------------------- 应用图标
def build_app_master(canvas: int = 1024) -> Image.Image:
    """桌面图标 → 透明底、macOS 原生比例的 1024 母版。"""
    im = Image.open(APP_SRC).convert("RGB")
    W, H = im.size
    px = im.load()

    def whiteness(c) -> float:
        m = min(c)
        if m <= 170:
            return 0.0
        return min(1.0, (m - 170) / (255.0 - 170))

    # 从四角洪水填充"白区"（含抗锯齿过渡带里偏白的那一半）。
    # 圆角方块是闭合的，所以图形内部的白色区域不会被填到 —— 那部分要保留。
    flood = bytearray(W * H)
    stack = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]
    while stack:
        x, y = stack.pop()
        i = y * W + x
        if flood[i] or whiteness(px[x, y]) < 0.5:
            continue
        flood[i] = 1
        if x:
            stack.append((x - 1, y))
        if x < W - 1:
            stack.append((x + 1, y))
        if y:
            stack.append((x, y - 1))
        if y < H - 1:
            stack.append((x, y + 1))

    rgba = im.convert("RGBA")
    ap = rgba.load()
    for y in range(H):
        row = y * W
        for x in range(W):
            if not flood[row + x]:
                continue
            # 过渡带按"有多不白"给 alpha，边缘才是干净的抗锯齿而不是一圈浅色
            a = int(round((1.0 - whiteness(px[x, y])) * 255))
            ap[x, y] = px[x, y] + (a,) if a else (0, 0, 0, 0)

    body = rgba.crop(rgba.getbbox())
    target = int(round(canvas * APP_BODY_RATIO))
    body = _premul_resize(body, (target, target))
    master = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    off = (canvas - target) // 2
    master.paste(body, (off, off), body)
    return master


def write_icns(master: Image.Image, icns_path: Path, iconset_dir: Path | None = None) -> None:
    """用 iconutil 生成 .icns（需要完整的 iconset，否则系统会拒绝加载）。"""
    iconset = iconset_dir or (HERE / "assets" / "AppIcon.iconset")
    if iconset.exists():
        for f in iconset.iterdir():
            f.unlink()
    iconset.mkdir(parents=True, exist_ok=True)

    plan = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    for name, size in plan:
        _premul_resize(master, (size, size)).save(iconset / name)

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)], check=True)


# ------------------------------------------------------------- 菜单栏图标
def menu_master(kind: str = MENU_KIND, thicken: int = MENU_LINE_THICKEN,
                fill_holes: bool = MENU_SOLID_FILL_HOLES) -> Image.Image:
    """生成菜单栏图标母版：黑色 + alpha（模板图）。"""
    if kind == "line":
        im = Image.open(MENU_SRC).convert("L")
        W, H = im.size
        a = im.point(lambda v: 255 - v)          # 白底 → 透明，黑线 → 不透明
        if thicken:
            # MaxFilter 是方形膨胀：把线条向两侧各加宽 thicken 像素。
            # 必须做 —— 原图线条在 18pt 下只有 0.19 像素宽，不加粗就是隐形的。
            a = a.filter(ImageFilter.MaxFilter(thicken * 2 + 1))
        icon = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        icon.putalpha(a)
        return _tight_alpha(icon)

    if kind == "solid":
        im = Image.open(APP_SRC).convert("RGB")
        W, H = im.size
        # 内缩 120px 再取图，排除圆角方块自身的边缘（否则会在四角留下残影）
        inner = im.crop((120, 120, W - 120, H - 120))
        iw, ih = inner.size
        p = inner.load()
        a = Image.new("L", (iw, ih))
        ap = a.load()
        for y in range(ih):
            for x in range(iw):
                r, g, b = p[x, y]
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                v = (lum - 175.0) / 70.0
                ap[x, y] = 0 if v < 0 else (255 if v > 1 else int(v * 255))

        if fill_holes:
            # 从边界洪水填充"透明区"，填不到的透明像素就是图形内部的洞
            # （镂空的「5」、折角线），把它们补实 —— 小尺寸下更干净。
            reach = bytearray(iw * ih)
            edge = [(x, 0) for x in range(iw)] + [(x, ih - 1) for x in range(iw)] \
                + [(0, y) for y in range(ih)] + [(iw - 1, y) for y in range(ih)]
            while edge:
                x, y = edge.pop()
                i = y * iw + x
                if reach[i] or ap[x, y] > 24:
                    continue
                reach[i] = 1
                if x:
                    edge.append((x - 1, y))
                if x < iw - 1:
                    edge.append((x + 1, y))
                if y:
                    edge.append((x, y - 1))
                if y < ih - 1:
                    edge.append((x, y + 1))
            for y in range(ih):
                for x in range(iw):
                    if not reach[y * iw + x] and ap[x, y] < 255:
                        ap[x, y] = 255

        if MENU_SOLID_THICKEN_ARROW:
            a = _thicken_arrow(a, MENU_SOLID_THICKEN_ARROW)

        icon = Image.new("RGBA", (iw, ih), (0, 0, 0, 255))
        icon.putalpha(a)
        return _tight_alpha(icon)

    raise ValueError(f"未知的菜单栏图标类型：{kind}")


def _thicken_arrow(alpha: Image.Image, radius: int) -> Image.Image:
    """只把「下载箭头」那个连通块加粗 radius 像素，其它图形保持原样。

    为什么不能整体膨胀：磁铁和文件夹是实心大块，整体膨胀会把文件夹里镂空的
    「5」堵死（它的内孔只有几十像素宽）。而箭头是细线、恰恰最需要加粗，
    所以按连通块单独处理 —— 找出既不在最上、也不在最下的那个小块即可。
    """
    w, h = alpha.size
    px = alpha.load()
    seen = bytearray(w * h)
    comps = []
    for y0 in range(h):
        for x0 in range(w):
            if seen[y0 * w + x0] or px[x0, y0] < 128:
                continue
            stack = [(x0, y0)]
            seen[y0 * w + x0] = 1
            pts = []
            while stack:
                x, y = stack.pop()
                pts.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] \
                            and px[nx, ny] >= 128:
                        seen[ny * w + nx] = 1
                        stack.append((nx, ny))
            comps.append(pts)

    if len(comps) < 2:
        return alpha
    # 箭头 = 不在最顶部、也不在最底部，且面积较小的那个连通块
    ys = [sum(p[1] for p in c) / len(c) for c in comps]
    areas = [len(c) for c in comps]
    order = sorted(range(len(comps)), key=lambda i: ys[i])
    mid = order[1:-1] or order
    pick = min(mid, key=lambda i: areas[i])

    mask = Image.new("L", (w, h), 0)
    mp = mask.load()
    for x, y in comps[pick]:
        mp[x, y] = 255
    grown = mask.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    return ImageChops.lighter(alpha, grown)


def render_menu_reps(master: Image.Image, base: int = MENU_BASE,
                     inset: float = MENU_INSET) -> list[Image.Image]:
    """按内容高度 = base×(1-2×inset) 渲染 1x/2x/3x，画布固定 base 点的正方形。"""
    iw, ih = master.size
    reps = []
    for scale in (1, 2, 3):
        pc = base * scale
        ch = pc * (1 - 2 * inset)
        cw = ch * iw / ih
        cwi, chi = max(1, round(cw)), max(1, round(ch))
        if cwi > 1 and chi > 1:
            small = _premul_resize(master, (cwi, chi))
        else:                       # 1x 下只有几个像素，用直接缩放避免过度模糊
            small = master.resize((max(1, cwi), max(1, chi)), Image.LANCZOS)
        cv = Image.new("RGBA", (pc, pc), (0, 0, 0, 0))
        cv.paste(small, ((pc - small.width) // 2, (pc - small.height) // 2), small)
        reps.append(cv)
    return reps


# --------------------------------------------------------------------- 入口
def generate(menu_kind: str = MENU_KIND, thicken: int = MENU_LINE_THICKEN,
             fill_holes: bool = MENU_SOLID_FILL_HOLES, quiet: bool = False) -> dict:
    assets = HERE / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    app_master = build_app_master()
    app_master.save(assets / "AppIcon-1024.png")
    write_icns(app_master, assets / "AppIcon.icns")

    mm = menu_master(menu_kind, thicken, fill_holes)
    reps = render_menu_reps(mm)
    names = ["iconTemplate.png", "iconTemplate@2x.png", "iconTemplate@3x.png"]
    for rep, name in zip(reps, names):
        rep.save(assets / name)

    info = {
        "app_master": app_master.size,
        "menu_source": mm.size,
        "menu_reps": [r.size for r in reps],
        "menu_kind": menu_kind,
    }
    if not quiet:
        print(f"应用图标母版 {app_master.size} → AppIcon.icns")
        print(f"菜单栏图标({menu_kind}) 母版 {mm.size} → {[r.size[0] for r in reps]} px")
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 115 秒转的图标资源")
    ap.add_argument("--menu-kind", choices=("line", "solid"), default=MENU_KIND)
    ap.add_argument("--thicken", type=int, default=MENU_LINE_THICKEN)
    ap.add_argument("--fill-holes", action="store_true", default=MENU_SOLID_FILL_HOLES)
    args = ap.parse_args(argv)
    generate(args.menu_kind, args.thicken, args.fill_holes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
