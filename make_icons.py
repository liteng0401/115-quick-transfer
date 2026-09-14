#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_icons.py — 从 assets/src/ 里的原图生成两个图标资源

  输入：
    assets/src/app-icon.png       桌面图标原图（蓝色圆角方块 + 白色实心图形）
    assets/src/menu-icon-folder.png 菜单栏图标原图（浅色文件夹 + U 形镂空）
    assets/src/menu-icon-line.png 备用：黑线稿 + 白底（MENU_KIND="line" 时才用）

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
  · 菜单栏原图（menu-icon-folder.png）是**没有 alpha 通道的 RGB 图**，
    "透明棋盘格"是直接画进像素的。而且洞内叠了内阴影、把棋盘格压暗了。
    所以不能靠亮度阈值，也不能照抄像素边界（会发毛）——做法是：
    外轮廓用局部标准差判别（棋盘格有高频交替，文件夹平滑），
    U 形洞口先用灵敏阈值粗定位、再用亮度梯度精确定四条竖边，
    最后按"圆角矩形 + 半圆底"做几何重建。小尺寸下边缘才干净。

用法：
  python3 make_icons.py                      # 生成全部图标
  python3 make_icons.py --menu-kind solid    # 指定菜单栏图标取图方式
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

HERE = Path(__file__).resolve().parent
SRC_DIR = HERE / "assets" / "src"
APP_SRC = SRC_DIR / "app-icon.png"
MENU_FOLDER_SRC = SRC_DIR / "menu-icon-folder.png"
MENU_SRC = SRC_DIR / "menu-icon-line.png"

# macOS 原生应用图标里，圆角方块占整张画布的比例（824 / 1024）
APP_BODY_RATIO = 824 / 1024

# 菜单栏图标画布尺寸（点）与内容占比
MENU_BASE = 18
MENU_INSET = 0.055          # 上下各留 5.5% ⇒ 内容高 ≈ 89% × 18pt ≈ 16pt

# 菜单栏取图方式（都在本机真实菜单栏里 A/B 截图对比过）：
#   "folder" —— 用 menu-icon-folder.png：浅色文件夹 + U 形镂空（当前默认）
#   "solid"  —— 用桌面图标里的白色实心形状（磁铁 + 下载箭头 + 115 文件夹）
#   "line"   —— 用黑线稿，按 MENU_LINE_THICKEN 加粗（笔画太细，18pt 下会发灰）
#
# 2026-09-14 用户指定把菜单栏图标换成 menu-icon-folder.png，故默认改为 "folder"。
# 另外两种没删，改这一个常量即可切回。
MENU_KIND = "folder"
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


# -------------------------------------------- 从"假透明棋盘格"底图里抠形状
#
# 源图把透明棋盘格画成了真实像素（无 alpha 通道）。判别思路：
#   · 棋盘格 = 高频交替 → 局部标准差高（实测 ≈10~11）
#   · 文件夹内部平滑     → 局部标准差低（实测 ≈0~2）
#   · 洞内的棋盘格被内阴影压暗、对比度下降 → 用更灵敏的阈值，且边界会外扩
#     约 24px，所以还要用亮度梯度把四条竖边精确定回来。
_F_STD_R = 24          # 局部标准差窗口半径（约 1.1 格棋盘格）
_F_STD_BG = 6          # 背景阈值
_F_STD_HOLE = 4        # 洞内阈值（更灵敏）
_F_GRAD = 8            # 梯度噪声地板。真实 U 边界梯度 32~70，洞内棋盘格格边 16~21，
                       # 所以不能只靠固定阈值：统一取"最强的 4 个峰"再校验正负号模式
_F_TOP_EDGES = 4       # 每行取最强的几条边
_F_FRAME = 8           # 图像最外圈窗口被截断 → 直接当背景，否则会堵死洪水填充
_F_ISLAND = 7000       # 洞内面积小于此值的实体孤岛 → 并进洞里
_F_EDGE_GAP = 12       # 相邻多近的梯度峰算同一条边


def _local_std(L: bytes, w: int, h: int, radius: int) -> bytearray:
    """局部标准差图（积分图，O(N)）。"""
    pw = w + 1
    S = [0] * (pw * (h + 1))
    Q = [0] * (pw * (h + 1))
    for y in range(h):
        rs = rq = 0
        base = (y + 1) * pw
        prev = y * pw
        rb = y * w
        for x in range(w):
            v = L[rb + x]
            rs += v
            rq += v * v
            S[base + x + 1] = S[prev + x + 1] + rs
            Q[base + x + 1] = Q[prev + x + 1] + rq

    def win(tab, x0, y0, x1, y1):
        return (tab[y1 * pw + x1] - tab[y0 * pw + x1]
                - tab[y1 * pw + x0] + tab[y0 * pw + x0])

    out = bytearray(w * h)
    for y in range(h):
        y0 = max(0, y - radius)
        y1 = min(h, y + radius + 1)
        rb = y * w
        for x in range(w):
            x0 = max(0, x - radius)
            x1 = min(w, x + radius + 1)
            n = (x1 - x0) * (y1 - y0)
            s = win(S, x0, y0, x1, y1)
            q = win(Q, x0, y0, x1, y1)
            m = s / float(n)
            var = q / float(n) - m * m
            v = int(var ** 0.5) if var > 0 else 0
            out[rb + x] = 255 if v > 255 else v
    return out


def _largest_component(bits: bytearray, w: int, h: int) -> bytearray:
    """只保留最大连通域（去掉零星噪点）。"""
    seen = bytearray(w * h)
    best = []
    for i in range(w * h):
        if seen[i] or not bits[i]:
            continue
        stack = [i]
        seen[i] = 1
        pts = []
        while stack:
            j = stack.pop()
            pts.append(j)
            x, y = j % w, j // w
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    k = ny * w + nx
                    if not seen[k] and bits[k]:
                        seen[k] = 1
                        stack.append(k)
        if len(pts) > len(best):
            best = pts
    out = bytearray(w * h)
    for i in best:
        out[i] = 1
    return out


def _flood_from_frame(bits: bytearray, w: int, h: int, frame: int) -> bytearray:
    """从最外 frame 像素的边框向内洪水填充。最外圈直接算背景。

    必须这么做：贴近图像边界时局部窗口被截断，标准差会掉到阈值以下，
    于是最外圈被误判成实体，把洪水填充整个堵死。
    """
    ext = bytearray(w * h)
    stack = []
    for y in range(h):
        for x in range(w):
            if x < frame or y < frame or x >= w - frame or y >= h - frame:
                ext[y * w + x] = 1
                stack.append(y * w + x)
    seen = bytearray(w * h)
    while stack:
        i = stack.pop()
        if seen[i]:
            continue
        seen[i] = 1
        x, y = i % w, i // w
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                onframe = (nx < frame or ny < frame
                           or nx >= w - frame or ny >= h - frame)
                if not seen[j] and (bits[j] or onframe):
                    stack.append(j)
    out = bytearray(1 if seen[i] else 0 for i in range(w * h))
    for y in range(h):
        for x in range(w):
            if x < frame or y < frame or x >= w - frame or y >= h - frame:
                out[y * w + x] = 1
    return out


def _gradient_edges(L: bytes, w: int, row: int, lo: int, hi: int,
                    thr: float, top: int = _F_TOP_EDGES) -> list:
    """在一行剖面里找梯度边。返回 [(坐标, 带符号梯度)]，按坐标排序。

    不能只靠固定阈值：洞内叠加内阴影后，棋盘格自身的格边梯度会升到 16~21，
    已经盖过阈值；而真实 U 边界是 32~70。所以统一"取最强的 top 个峰"
    （彼此间隔 > _F_EDGE_GAP），再由调用方校验正负号模式是否像 U。
    thr 只当噪声地板用。
    """
    vals = [L[row * w + c] for c in range(lo, hi + 1)]
    n = len(vals)
    peaks = []
    for i in range(3, n - 3):
        g = (sum(vals[i + 1:i + 4]) - sum(vals[i - 3:i])) / 3.0
        if abs(g) >= thr:
            peaks.append((abs(g), i, g))
    peaks.sort(key=lambda t: -t[0])
    picked = []
    for mag, i, g in peaks:
        if all(abs(i - p[1]) > _F_EDGE_GAP for p in picked):
            picked.append((mag, i, g))
            if len(picked) >= top:
                break
    picked.sort(key=lambda t: t[1])
    return [(lo + i, g) for _, i, g in picked]


def _fill_small_islands(hole: bytearray, solid: bytearray, w: int, h: int,
                        max_area: int) -> bytearray:
    """把洞口内部的小实体孤岛并进洞里。

    竖条顶端的圆角块因为被压暗、局部标准差掉到阈值以下，会被误判成实体，
    在洞口里留下两个小疙瘩。判据：面积小 + 包围盒落在洞口包围盒之内。
    """
    hx = [i % w for i in range(w * h) if hole[i]]
    hy = [i // w for i in range(w * h) if hole[i]]
    if not hx:
        return hole
    x0, x1, y0, y1 = min(hx), max(hx), min(hy), max(hy)
    seen = bytearray(w * h)
    out = bytearray(hole)
    for i in range(w * h):
        if out[i] or seen[i] or not solid[i]:
            continue
        stack = [i]
        seen[i] = 1
        comp = []
        while stack:
            j = stack.pop()
            comp.append(j)
            x, y = j % w, j // w
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    k = ny * w + nx
                    if not seen[k] and not out[k] and solid[k]:
                        seen[k] = 1
                        stack.append(k)
        cx = [j % w for j in comp]
        cy = [j // w for j in comp]
        if (len(comp) < max_area and min(cx) >= x0 and max(cx) <= x1
                and min(cy) >= y0 and max(cy) <= y1):
            for j in comp:
                out[j] = 1
    return out


def _u_solid(w: int, h: int, x0: int, x1: int, y0: int, y1: int,
             rt: float, rb: float) -> bytearray:
    """圆角顶 + 半圆底的「U」形实心块。与舌板块做差即得洞口。"""
    out = bytearray(w * h)
    cx = (x0 + x1) / 2.0
    for y in range(max(0, y0), min(h - 1, y1) + 1):
        if y <= y1 - rb:
            left, right = float(x0), float(x1)
            if rt and y < y0 + rt:
                dy = (y0 + rt) - y
                if 0 <= dy < rt:
                    dd = rt - math.sqrt(max(0.0, rt * rt - dy * dy))
                    left, right = x0 + dd, x1 - dd
        else:
            dy = y - (y1 - rb)
            if dy >= rb:
                dy = rb - 1e-9
            half = math.sqrt(max(0.0, rb * rb - dy * dy))
            left, right = cx - half, cx + half
        if right < left:
            continue
        li = max(0, int(math.floor(left)))
        ri = min(w - 1, int(math.ceil(right)))
        base = y * w
        for x in range(li, ri + 1):
            out[base + x] = 1
    return out


def folder_menu_master(src: Path = MENU_FOLDER_SRC) -> Image.Image:
    """把「浅色文件夹 + U 形镂空」的源图转成干净的模板图母版（黑色 + alpha）。"""
    im = Image.open(src).convert("L")
    w, h = im.size
    L = im.tobytes()

    # 1) 外轮廓
    std = _local_std(L, w, h, _F_STD_R)
    bg = bytearray(1 if std[i] > _F_STD_BG else 0 for i in range(w * h))
    bm = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in bg))
    bm = bm.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    bp = bm.load()
    bg = bytearray(1 if bp[i % w, i // w] else 0 for i in range(w * h))
    ext = _flood_from_frame(bg, w, h, _F_FRAME)
    solid = _largest_component(bytearray(0 if ext[i] else 1 for i in range(w * h)), w, h)

    # 平滑边界毛刺
    sm = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in solid))
    for _ in range(3):
        sm = sm.filter(ImageFilter.MedianFilter(9))
    sm = sm.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    sm = sm.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    smp = sm.load()
    solid = _largest_component(
        bytearray(1 if smp[i % w, i // w] else 0 for i in range(w * h)), w, h)

    # 2) 洞口粗定位（限制在外轮廓内部）
    inner = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in solid))
    for _ in range(12):
        inner = inner.filter(ImageFilter.MinFilter(3))
    ip = inner.load()
    cav = bytearray(1 if (std[i] > _F_STD_HOLE and ip[i % w, i // w]) else 0
                    for i in range(w * h))
    cm = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in cav))
    cm = cm.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    cm = cm.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    cp = cm.load()
    cav = _largest_component(
        bytearray(1 if cp[i % w, i // w] else 0 for i in range(w * h)), w, h)
    cav = _fill_small_islands(cav, solid, w, h, _F_ISLAND)

    cx = [i % w for i in range(w * h) if cav[i]]
    cy = [i // w for i in range(w * h) if cav[i]]
    if not cx:
        raise RuntimeError("菜单栏图标：没能从源图里抠出 U 形镂空")
    cav_x0, cav_x1, cav_y0, cav_y1 = min(cx), max(cx), min(cy), max(cy)

    # 3) 亮度梯度精确定四条竖边（洞内被压暗 → 粗定位的边界会外扩约 24px）
    band_lo = cav_y0 + int(0.28 * (cav_y1 - cav_y0))
    band_hi = cav_y0 + int(0.52 * (cav_y1 - cav_y0))
    lo = max(0, cav_x0 - 40)
    hi = min(w - 1, cav_x1 + 40)
    As, Bls, As2, bs = [], [], [], []
    for y in range(band_lo, band_hi + 1, 8):
        e = _gradient_edges(L, w, y, lo, hi, _F_GRAD)
        # U 的四条竖边必然是"暗-亮-暗-亮"交替：外左 / 舌板左 / 舌板右 / 外右
        signs = [1 if g > 0 else -1 for _, g in e]
        if len(e) == 4 and signs == [-1, 1, -1, 1]:
            As.append(e[0][0])
            Bls.append(e[1][0])
            As2.append(e[2][0])
            bs.append(e[3][0])
    if len(As) < 3:
        raise RuntimeError("菜单栏图标：没能从源图里量出 U 的四条竖边")
    med = lambda v: sorted(v)[len(v) // 2]
    A, Bl, a, b = med(As), med(Bls), med(As2), med(bs)

    # 4) 纵向：用粗定位边界反推（洞外扩量 d 由竖边标定）
    d = med([A - cav_x0, cav_x1 - b])
    cidx = (Bl + a) // 2
    counter_top = min(y for y in range(h) if cav[y * w + cidx]) if any(
        cav[y * w + cidx] for y in range(h)) else None
    if counter_top is None:
        raise RuntimeError("菜单栏图标：没找到舌板")
    Y0, Y1, Y1i = cav_y0 + d, cav_y1 - d, counter_top + d

    prong = Bl - A + 1
    counter = a - Bl - 1
    outer = _u_solid(w, h, A, b, Y0, Y1, prong / 2.0, (b - A + 1) / 2.0)
    tongue = _u_solid(w, h, Bl + 1, a - 1, Y0, Y1i, prong / 2.0, counter / 2.0)
    hole = bytearray(1 if (outer[i] and not tongue[i]) else 0 for i in range(w * h))

    # 5) 打洞
    alpha = bytearray(1 if (solid[i] and not hole[i]) else 0 for i in range(w * h))
    master = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mp = master.load()
    for i in range(w * h):
        if alpha[i]:
            mp[i % w, i // w] = (0, 0, 0, 255)
    print("   [folder] 外轮廓 %dx%d  U: A=%d Bl=%d a=%d b=%d Y0=%d Y1=%d Y1i=%d"
              % (w, h, A, Bl, a, b, Y0, Y1, Y1i))
    return _tight_alpha(master)


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

    if kind == "folder":
        return folder_menu_master()

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
    """把内容等比内接到 base×(1-2×inset) 的正方形里，渲染 1x/2x/3x。

    取"长短边都不超过包含盒"，而不是只按高度适配 —— 否则横宽的图形
    （例如文件夹，宽高比 1.11）会横向撑满画布、跟相邻图标贴在一起。
    竖长的图形（旧版磁铁母版宽高比 0.46）结果与按高度适配完全一致。
    """
    iw, ih = master.size
    reps = []
    for scale in (1, 2, 3):
        pc = base * scale
        box = pc * (1 - 2 * inset)
        k = box / float(max(iw, ih))
        cwi, chi = max(1, round(iw * k)), max(1, round(ih * k))
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
    ap.add_argument("--menu-kind", choices=("folder", "line", "solid"),
                    default=MENU_KIND)
    ap.add_argument("--thicken", type=int, default=MENU_LINE_THICKEN)
    ap.add_argument("--fill-holes", action="store_true", default=MENU_SOLID_FILL_HOLES)
    args = ap.parse_args(argv)
    generate(args.menu_kind, args.thicken, args.fill_holes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
