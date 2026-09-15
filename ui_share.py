#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui_share.py — 115 分享链接「快速转存」原生主窗口

把「粘贴 115 分享链接 → 选文件 → 选目标目录 → 一键转存到我的网盘」做成一张
完整的原生窗口，遵循与 ui_transfer.py（磁力/ed2k 云下载主窗口）一致的视觉与交互：

  · 毛玻璃底 + 透明标题栏，内容铺满窗口（系统存储管理面板同款做法）
  · 全动态系统色，深浅色 / 强调色自动跟随系统，不写死任何十六进制色值
  · 链接解析、目录浏览、转存提交全部走后台线程，主线程只刷新 UI

与转存面板的结构差异：
  · 顶部不再是多行磁力编辑器，而是一个「115 分享链接」单行输入框 + 解析按钮
  · 中部是可勾选的分享内容列表（双击进文件夹、面包屑返回、全选/全不选，
    选择集在提交时按父级去重——勾选文件夹即代表转存其整棵子树）
  · 下部是「保存到」目标目录浏览器（直接复用已验证的目录导航逻辑）

实现说明：延续「控制器 + 桥」分层 —— 纯 Python 类 _SharePanel 负责布局与逻辑，
_ShareBridge(NSObject) 只暴露 AppKit 需要的选择器并转发给控制器。
"""

from __future__ import annotations

import threading
import traceback
from typing import Optional

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSBox,
    NSBoxCustom,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSNoTitle,
    NSPathControl,
    NSPathControlItem,
    NSPathStyleStandard,
    NSScrollView,
    NSTableCellView,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSTextView,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
)
try:
    from AppKit import NSButtonTypeSwitch
    _SWITCH = NSButtonTypeSwitch
except Exception:  # noqa: BLE001
    _SWITCH = 3  # NSSwitchButton 的传统枚举值

from Foundation import NSObject, NSTimer, NSRunLoop, NSRunLoopCommonModes

from q115_engine import Q115Engine, Q115Error, log
from ui_theme import (
    DUR_FAST,
    DUR_NORMAL,
    DUR_SLOW,
    PAD,
    R_CARD,
    animate,
    crossfade,
    dismiss_window,
    fade_in,
    fade_in_delayed,
    make_button,
    make_card,
    make_flat_button,
    make_icon_view,
    make_label,
    make_scroll,
    make_section_title,
    make_separator,
    make_spinner,
    make_window,
    place,
    pop_in,
    present_window,
    symbol,
)
from ui_transfer import bridge_action  # 复用同一套控件→桥的绑定工具


W = 640.0
H = 880.0
HEADER = 84.0
BODY_H = H - HEADER


def run_share_panel(engine: Q115Engine, clip: str = "", start: str = "/") -> Optional[tuple[int, str]]:
    """弹出分享转存主窗口。返回 (转存成功的项目数, 目标路径)；取消或失败返回 None。"""
    return _SharePanel(engine, clip, start).run_modal()


def _join(base: str, name: str) -> str:
    return ("/" + name) if base == "/" else base.rstrip("/") + "/" + name


def _fmt_size(n: int) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f = float(n)
    i = 0
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)} {units[i]}"
    return f"{f:.1f} {units[i]}"


# NSPathControl 图标格比例（与 ui_transfer 一致：24×26 最贴合图标格，符号不被拉变形）
_PATH_ICON_BOX_W = 24.0
_PATH_ICON_BOX_H = 26.0
_PATH_ICON_GLYPH_W = 23.2


def _breadcrumb_root_icon():
    sym = symbol("internaldrive", _PATH_ICON_GLYPH_W, NSColor.secondaryLabelColor())
    if sym is None:
        return None
    gw, gh = sym.size()
    if gw <= 0 or gh <= 0:
        return None
    img = NSImage.alloc().initWithSize_((_PATH_ICON_BOX_W, _PATH_ICON_BOX_H))
    for scale in (1, 2, 3):
        px, py = int(round(_PATH_ICON_BOX_W * scale)), int(round(_PATH_ICON_BOX_H * scale))
        from AppKit import NSBitmapImageRep, NSDeviceRGBColorSpace, NSGraphicsContext, NSBezierPath
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, px, py, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
        rep.setSize_((px, py))
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, px, py))
        sym.drawInRect_(NSMakeRect(
            (_PATH_ICON_BOX_W - gw) / 2.0 * scale, (_PATH_ICON_BOX_H - gh) / 2.0 * scale,
            gw * scale, gh * scale))
        NSGraphicsContext.restoreGraphicsState()
        rep.setSize_((_PATH_ICON_BOX_W, _PATH_ICON_BOX_H))
        img.addRepresentation_(rep)
    return img


class _SharePanel:
    """分享转存主窗口控制器：链接解析 + 文件勾选 + 目标目录 + 提交。"""

    _ico_folder = False
    _ico_chevron = False
    _ico_chevron_sel = False

    @classmethod
    def folder_icon(cls):
        if cls._ico_folder is False:
            cls._ico_folder = symbol("folder.fill", 17.0, NSColor.controlAccentColor())
        return cls._ico_folder

    @classmethod
    def file_icon(cls):
        return symbol("doc.fill", 15.0, NSColor.tertiaryLabelColor())

    @classmethod
    def chevron_icon(cls, selected: bool = False):
        if selected:
            if cls._ico_chevron_sel is False:
                cls._ico_chevron_sel = symbol("chevron.right", 11.0, NSColor.controlAccentColor())
            return cls._ico_chevron_sel
        if cls._ico_chevron is False:
            cls._ico_chevron = symbol("chevron.right", 11.0, NSColor.tertiaryLabelColor())
        return cls._ico_chevron

    # ---------------- 生命周期 ----------------

    def __init__(self, engine: Q115Engine, clip: str, start: str) -> None:
        self.engine = engine
        self._clip = clip or ""
        self._result: Optional[tuple[int, str]] = None
        self._busy = False
        self._closed = False

        # 分享解析结果
        self._share_fs = None
        self._parsed = False

        # 分享内容浏览 + 勾选
        self._s_crumbs: list[tuple[str, str]] = [("分享根目录", "0")]
        self._s_children: list[dict] = []
        self._selection: dict[str, str] = {}   # file_id -> 父级 cid（用于提交时按父级去重）

        # 目标目录浏览（复用已验证的目录导航逻辑）
        path = (start or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        t_segs = [s for s in path.split("/") if s]
        start_cid = "0"
        if t_segs:
            try:
                start_cid = engine.resolve_dir_id(path)
            except Exception:  # noqa: BLE001  上次目录可能已被删
                log("[share] 上次目录失效，回落根目录:\n" + traceback.format_exc())
                t_segs, path, start_cid = [], "/", "0"
        self._t_crumbs: list[tuple[str, Optional[str]]] = [("", "0")]
        self._t_crumbs += [(s, None) for s in t_segs[:-1]]
        if t_segs:
            self._t_crumbs.append((t_segs[-1], start_cid))
        self._t_cache: dict[str, list[dict]] = {}
        self._t_children: list[dict] = []

        # 预置会被代理回调访问到的视图属性为 None（沿用 ui_transfer 的铁律）
        self._link_field = None
        self._parse_btn = None
        self._meta_label = None
        self._s_table = None
        self._s_list_wrap = None
        self._s_spin = None
        self._s_empty = None
        self._s_level_label = None
        self._s_sel_label = None
        self._t_table = None
        self._t_list_wrap = None
        self._t_spin = None
        self._t_path_ctrl = None
        self._t_empty = None
        self._t_new_folder_btn = None
        self._pending_new_name = ""
        self._btn_ok = None
        self._btn_cancel = None
        self._foot_spin = None
        self._status_icon = None
        self._status_label = None
        self._zoom_hit = None

        # 异步加载状态
        self._nav_busy = False
        self._load_ticket = 0
        self._main_callback = None

    def run_modal(self) -> Optional[tuple[int, str]]:
        try:
            self._build()
        except Q115Error as e:
            self._alert("无法打开窗口", str(e))
            return None
        except Exception as e:  # noqa: BLE001
            log("[share] 建窗失败:\n" + traceback.format_exc())
            self._alert("无法打开窗口", f"{type(e).__name__}: {e}")
            return None

        app = NSApplication.sharedApplication()
        try:
            app.activateIgnoringOtherApps_(True)
        except Exception:  # noqa: BLE001
            pass
        present_window(self._win)
        app.runModalForWindow_(self._win)
        self._closed = True
        self._win.orderOut_(None)
        return self._result

    # ---------------- 界面 ----------------

    def _build(self) -> None:
        bridge = _ShareBridge.alloc().init()
        bridge.owner = self
        self._bridge = bridge

        win = make_window(W, H, "转存 115 分享链接")
        self._win = win
        win.setDelegate_(self._bridge)
        root = win.contentView()

        # --- 标题区 ---
        ico = make_icon_view("square.and.arrow.down.fill", 34.0, NSColor.controlAccentColor())
        place(ico, PAD, 22, 34, 34, root)
        root.addSubview_(ico)

        title = make_label("转存 115 分享链接", 22.0, 0.3, NSColor.labelColor())
        place(title, 70, 22, 420, 28, root)
        root.addSubview_(title)

        try:
            ok, name = self.engine.account_info()
        except Exception:  # noqa: BLE001
            ok, name = True, ""
        sub = make_label(f"已登录 · {name}" if (ok and name) else "115 网盘分享转存",
                         12.0, 0.0, NSColor.secondaryLabelColor())
        place(sub, 70, 51, W - 96, 16, root)
        root.addSubview_(sub)

        sep = make_separator(W)
        place(sep, 0, HEADER, W, 1, root)
        root.addSubview_(sep)

        # 标题区双击 → 缩放窗口（透明点击层，盖在标题区上方）
        zoom_hit = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, HEADER))
        zoom_hit.setAutoresizingMask_(NSViewWidthSizable)
        root.addSubview_(zoom_hit)  # 透明点击层，最后添加自然在最上层
        self._zoom_hit = zoom_hit
        self._setup_zoom_recognizer(zoom_hit)

        self._build_link_area(root)
        self._build_share_area(root)
        self._build_target_area(root)
        self._build_footer(root)

        # 剪贴板里有链接就预填，并自动尝试解析
        if self._clip:
            self._link_field.setStringValue_(self._clip)
            self._parse_btn.setEnabled_(True)
            self.do_parse()

    def _build_link_area(self, root) -> None:
        lab = make_section_title("115 分享链接")
        place(lab, PAD, 104, 200, 16, root)
        root.addSubview_(lab)

        hint = make_label("形如 https://115.com/s/xxxx?password=xxxx", 11.0, 0.0,
                          NSColor.tertiaryLabelColor())
        hint.setAlignment_(2)  # NSRightTextAlignment
        place(hint, W - PAD - 300, 105, 300, 14, root)
        root.addSubview_(hint)

        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 28))
        field.setBezelStyle_(1)  # NSRoundedBezelStyle
        field.setFont_(NSFont.systemFontOfSize_(13.0))
        field.setTextColor_(NSColor.labelColor())
        field.setPlaceholderString_("粘贴 115 分享链接…")
        field.setDelegate_(self._bridge)
        field.setTarget_(self._bridge)
        field.setAction_(bridge_action(self._bridge, "parse"))
        place(field, PAD, 128, W - PAD * 2 - 96, 28, root)
        root.addSubview_(field)
        self._link_field = field

        btn = make_button("解析", size=13.0)
        place(btn, W - PAD - 84, 128, 84, 28, root)
        btn.setTarget_(self._bridge)
        btn.setAction_(bridge_action(self._bridge, "parse"))
        btn.setEnabled_(bool(self._clip))
        root.addSubview_(btn)
        self._parse_btn = btn

        meta = make_label("粘贴 115 分享链接后点「解析」，即可看到分享里的文件。",
                          12.0, 0.0, NSColor.tertiaryLabelColor())
        place(meta, PAD, 166, W - PAD * 2, 16, root)
        root.addSubview_(meta)
        self._meta_label = meta

    def _build_share_area(self, root) -> None:
        lab = make_section_title("选择要转存的内容")
        place(lab, PAD, 200, 200, 16, root)
        root.addSubview_(lab)

        level = make_label("当前：分享根目录", 11.0, 0.0, NSColor.tertiaryLabelColor())
        level.setAlignment_(2)
        place(level, W - PAD - 240, 201, 240, 14, root)
        root.addSubview_(level)
        self._s_level_label = level

        sel = make_label("", 11.0, 0.0, NSColor.secondaryLabelColor())
        sel.setAlignment_(2)
        place(sel, W - PAD - 240, 219, 240, 14, root)
        root.addSubview_(sel)
        self._s_sel_label = sel

        b_up = make_flat_button("上一级", "arrow.up")
        place(b_up, PAD, 216, 84, 26, root)
        b_up.setTarget_(self._bridge)
        b_up.setAction_(bridge_action(self._bridge, "shareUp"))
        root.addSubview_(b_up)
        self._s_up_btn = b_up

        b_all = make_flat_button("全选", "checkmark")
        place(b_all, PAD + 92, 216, 64, 26, root)
        b_all.setTarget_(self._bridge)
        b_all.setAction_(bridge_action(self._bridge, "shareAll"))
        root.addSubview_(b_all)
        self._s_all_btn = b_all

        b_none = make_flat_button("全不选", "xmark")
        place(b_none, PAD + 164, 216, 72, 26, root)
        b_none.setTarget_(self._bridge)
        b_none.setAction_(bridge_action(self._bridge, "shareNone"))
        root.addSubview_(b_none)
        self._s_none_btn = b_none

        card = make_card()
        place(card, PAD, 252, W - PAD * 2, 304, root)
        root.addSubview_(card)

        iw = card.frame().size.width - 2
        ih = card.frame().size.height - 2
        scroll = make_scroll(NSMakeRect(1, 1, iw, ih))
        table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, iw, ih))
        col = NSTableColumn.alloc().initWithIdentifier_("name")
        col.setWidth_(iw - 2)
        col.setResizingMask_(2)
        table.addTableColumn_(col)
        table.setHeaderView_(None)
        table.setRowHeight_(32.0)
        table.setAllowsMultipleSelection_(False)
        try:
            table.setBackgroundColor_(NSColor.clearColor())
            table.setUsesAlternatingRowBackgroundColors_(False)
        except Exception:  # noqa: BLE001
            pass
        table.setDataSource_(self._bridge)
        table.setDelegate_(self._bridge)
        table.setTarget_(self._bridge)
        table.setDoubleAction_(bridge_action(self._bridge, "shareRowDouble"))
        table.setAction_(bridge_action(self._bridge, "shareRowClick"))
        scroll.setDocumentView_(table)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setHorizontalScrollElasticity_(0)
        except Exception:  # noqa: BLE001
            pass
        card.addSubview_(scroll)
        # 锁列宽（彻底消除横向滚动）：用固定设计宽度而非运行时测量。
        # 卡片宽 = W-2*PAD，iw = 卡片-2；clip 在「滚动条常显」系统上会少约 15px，
        # 故列宽取 iw-16，恒 <= clip 最小可能宽度 → 横向可滚距离恒为 0。
        scroll.setClipsToBounds_(True)
        table.setClipsToBounds_(True)
        cv = scroll.contentView()
        cv.setClipsToBounds_(True)
        col_w = iw - 24
        col.setWidth_(col_w)
        col.setMinWidth_(col_w)
        col.setMaxWidth_(col_w)
        t_f = table.frame()
        t_f.origin.x = 0
        t_f.size.width = col_w
        table.setFrame_(t_f)
        # 双击标题栏放大时列表随窗口伸缩、列宽同步重算（见 _relock_columns）
        card.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._s_table = table
        self._s_col = col
        self._s_list_wrap = scroll

        spin = make_spinner(20.0)
        place(spin, (iw - 20) / 2, ih / 2 - 10, 20, 20, card)
        card.addSubview_(spin)
        self._s_spin = spin

        empty = make_label("此分享里没有可转存的内容", 12.0, 0.0, NSColor.tertiaryLabelColor())
        empty.setAlignment_(2)
        place(empty, 0, card.frame().size.height / 2 - 16, card.frame().size.width, 16, card)
        card.addSubview_(empty)
        empty.setHidden_(True)
        self._s_empty = empty

    def _build_target_area(self, root) -> None:
        lab = make_section_title("保存到")
        place(lab, PAD, 576, 200, 16, root)
        root.addSubview_(lab)

        tip = make_label("双击进入 · 点上方路径可返回", 11.0, 0.0, NSColor.tertiaryLabelColor())
        tip.setAlignment_(2)
        place(tip, W - PAD - 240, 577, 240, 14, root)
        root.addSubview_(tip)

        pc = NSPathControl.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        try:
            pc.setPathStyle_(NSPathStyleStandard)
            pc.setTarget_(self._bridge)
            pc.setAction_(bridge_action(self._bridge, "targetPathClick"))
        except Exception:  # noqa: BLE001
            pc = None
        if pc is not None:
            place(pc, PAD, 598, W - PAD * 2, 26, root)
            root.addSubview_(pc)
        self._t_path_ctrl = pc

        card = make_card()
        list_h = H - 636 - 60
        place(card, PAD, 636, W - PAD * 2, list_h, root)
        root.addSubview_(card)

        iw = card.frame().size.width - 2
        ih = card.frame().size.height - 2
        scroll = make_scroll(NSMakeRect(1, 1, iw, ih))
        table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, iw, ih))
        col = NSTableColumn.alloc().initWithIdentifier_("name")
        col.setWidth_(iw - 2)
        col.setResizingMask_(2)
        table.addTableColumn_(col)
        table.setHeaderView_(None)
        table.setRowHeight_(32.0)
        table.setAllowsMultipleSelection_(False)
        try:
            table.setBackgroundColor_(NSColor.clearColor())
            table.setUsesAlternatingRowBackgroundColors_(False)
        except Exception:  # noqa: BLE001
            pass
        table.setDataSource_(self._bridge)
        table.setDelegate_(self._bridge)
        table.setTarget_(self._bridge)
        table.setAction_(bridge_action(self._bridge, "targetCellClick"))
        table.setDoubleAction_(bridge_action(self._bridge, "targetRowDouble"))
        scroll.setDocumentView_(table)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setHorizontalScrollElasticity_(0)
        except Exception:  # noqa: BLE001
            pass
        card.addSubview_(scroll)
        # 锁列宽（彻底消除横向滚动）：固定设计宽度 iw-16，恒 <= clip 最小可见宽。
        scroll.setClipsToBounds_(True)
        table.setClipsToBounds_(True)
        cv = scroll.contentView()
        cv.setClipsToBounds_(True)
        col_w = iw - 24
        col.setWidth_(col_w)
        col.setMinWidth_(col_w)
        col.setMaxWidth_(col_w)
        t_f = table.frame()
        t_f.origin.x = 0
        t_f.size.width = col_w
        table.setFrame_(t_f)
        card.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._t_table = table
        self._t_col = col
        self._t_list_wrap = scroll

        spin = make_spinner(20.0)
        place(spin, (iw - 20) / 2, ih / 2 - 10, 20, 20, card)
        card.addSubview_(spin)
        self._t_spin = spin

        empty = make_label("此目录下没有子文件夹", 12.0, 0.0, NSColor.tertiaryLabelColor())
        empty.setAlignment_(1)  # NSTextAlignmentCenter（1=居中，2=右对齐是错的）
        place(empty, 0, card.frame().size.height / 2 - 16, card.frame().size.width, 16, card)
        card.addSubview_(empty)
        empty.setHidden_(True)
        self._t_empty = empty

        self._load_target_children(animated=False)

    def _build_footer(self, root) -> None:
        status_icon = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
        status_icon.setImageScaling_(2)  # NSScaleProportionally
        status_icon.setEditable_(False)
        place(status_icon, PAD, H - 45, 16, 16, root)
        root.addSubview_(status_icon)
        self._status_icon = status_icon
        status_icon.setHidden_(True)

        status_lab = make_label("", 12.0, 0.0, NSColor.secondaryLabelColor())
        place(status_lab, PAD, H - 46, 320, 16, root)
        root.addSubview_(status_lab)
        self._status_label = status_lab

        spin = make_spinner(14.0)
        place(spin, PAD, H - 45, 14, 14, root)
        root.addSubview_(spin)
        self._foot_spin = spin
        spin.setHidden_(True)

        ok_x = W - PAD - 116
        c_x = ok_x - 8 - 76

        b_ok = make_button("转存", primary=True, size=13.0)
        place(b_ok, ok_x, H - 52, 116, 32, root)
        b_ok.setTarget_(self._bridge)
        b_ok.setAction_(bridge_action(self._bridge, "submit"))
        root.addSubview_(b_ok)
        self._btn_ok = b_ok
        self._win.setDefaultButtonCell_(b_ok.cell())

        b_new = make_flat_button("新建文件夹", "folder.badge.plus")
        nf_x = c_x - 8 - 90
        place(b_new, nf_x, H - 51, 90, 28, root)
        b_new.setTarget_(self._bridge)
        b_new.setAction_(bridge_action(self._bridge, "newFolder"))
        root.addSubview_(b_new)
        self._t_new_folder_btn = b_new

        b_cancel = make_button("取消", size=13.0)
        place(b_cancel, c_x, H - 52, 76, 32, root)
        b_cancel.setKeyEquivalent_("\x1b")
        b_cancel.setTarget_(self._bridge)
        b_cancel.setAction_(bridge_action(self._bridge, "cancelOp"))
        root.addSubview_(b_cancel)
        self._btn_cancel = b_cancel

        self._refresh_footer()

    # ---------------- 链接解析 ----------------

    def _link_text(self) -> str:
        if self._link_field is None:
            return ""
        try:
            return self._link_field.stringValue() or ""
        except Exception:  # noqa: BLE001
            return ""

    def do_link_changed(self) -> None:
        if self._parse_btn is not None:
            self._parse_btn.setEnabled_(bool(self._link_text().strip()))

    # ---------------- 双击标题栏缩放 ----------------

    def _setup_zoom_recognizer(self, view) -> None:
        """在标题区视图上安装双击手势识别器 → 切换窗口缩放。"""
        try:
            from AppKit import NSClickGestureRecognizer
            recog = NSClickGestureRecognizer.alloc().init()
            recog.setNumberOfClicksRequired_(2)
            recog.setTarget_(self._bridge)
            recog.setAction_(bridge_action(self._bridge, "toggleZoom"))
            view.addGestureRecognizer_(recog)
        except Exception:  # noqa: BLE001
            pass  # 旧系统可能没有 gesture recognizer，静默降级

    def _relock_columns(self) -> None:
        """双击标题栏放大/缩回后，按新窗口宽度重算并锁定两个列表的列宽，
        保证放大后既不出现横向滚动、行也能填满更宽的列表。"""
        try:
            w = int(self._win.frame().size.width)
        except Exception:  # noqa: BLE001
            return
        col_w = max(w - 2 * int(PAD) - 26, 200)
        for tbl, col in ((getattr(self, "_s_table", None), getattr(self, "_s_col", None)),
                         (getattr(self, "_t_table", None), getattr(self, "_t_col", None))):
            if tbl is None or col is None:
                continue
            col.setWidth_(col_w)
            col.setMinWidth_(col_w)
            col.setMaxWidth_(col_w)
            f = tbl.frame()
            f.size.width = col_w
            tbl.setFrame_(f)
            try:
                tbl.reloadData()
            except Exception:  # noqa: BLE001
                pass

    def do_toggle_zoom(self) -> None:
        """双击标题栏 → 在原始尺寸和放大尺寸之间切换。"""
        win = self._win
        if win is None:
            return
        if getattr(self, "_zoomed", False):
            # 缩回原尺寸
            frame = getattr(self, "_normal_frame", None)
            if frame is not None:
                win.setFrame_display_animate_(frame, True)
            self._relock_columns()
            self._zoomed = False
        else:
            # 记录当前尺寸，然后放大到接近屏幕大小（留边距）
            self._normal_frame = win.frame()
            screen = win.screen()
            if screen is None:
                return
            visible = screen.visibleFrame()
            margin = 48
            zoom_frame = NSMakeRect(
                visible.origin.x + margin,
                visible.origin.y + margin,
                visible.size.width - 2 * margin,
                visible.size.height - 2 * margin,
            )
            win.setFrame_display_animate_(zoom_frame, True)
            self._relock_columns()
            self._zoomed = True

    def do_parse(self) -> None:
        url = self._link_text().strip()
        if not url:
            self._show_error("请先粘贴 115 分享链接", warn=True)
            return
        self._set_busy_parse(True)
        self._set_meta("正在解析分享链接…", NSColor.secondaryLabelColor())
        threading.Thread(target=self._parse_worker, args=(url,), daemon=True).start()

    def _parse_worker(self, url: str) -> None:
        err = None
        fs = None
        info = {}
        items = []
        try:
            fs = self.engine.parse_share(url)
            info = self.engine.share_info(fs)
            items = self.engine.share_list(fs, "0")
        except Q115Error as e:
            err = str(e)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            log("[share] 解析异常:\n" + traceback.format_exc())
        # 把结果挂到实例上，由主线程回调消费
        self._share_fs = fs
        self._pending_info = info
        self._pending_items = items
        self._pending_err = err
        self._post_ui("parseDone:", "")

    def ui_parse_done(self) -> None:
        err = getattr(self, "_pending_err", None)
        if err:
            self._set_busy_parse(False)
            self._show_error(err, warn=True)
            self._shake(self._win.contentView())
            return
        info = getattr(self, "_pending_info", {})
        items = getattr(self, "_pending_items", [])
        self._parsed = True
        self._s_crumbs = [("分享根目录", "0")]
        # 默认全选根目录所有项目（快速转存 = 整份分享）
        for it in items:
            self._selection[it["id"]] = "0"
        self._render_share(items, animated=False)
        title = info.get("title") or "115 分享"
        user = info.get("user")
        meta = f"已识别：{title}"
        if user:
            meta += f" · 分享者 {user}"
        meta += f" · 共 {len(items)} 个文件/文件夹"
        self._set_meta(meta, NSColor.secondaryLabelColor())
        self._set_busy_parse(False)
        self._refresh_footer()

    def _set_busy_parse(self, busy: bool) -> None:
        if self._parse_btn is not None:
            self._parse_btn.setEnabled_(not busy and bool(self._link_text().strip()))
        self._set_foot_spin(busy)

    def _set_meta(self, text: str, color) -> None:
        if self._meta_label is None:
            return
        self._meta_label.setStringValue_(text)
        try:
            self._meta_label.setTextColor_(color)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 分享内容浏览 ----------------

    @property
    def _s_cur_cid(self) -> str:
        return self._s_crumbs[-1][1] or "0"

    def _render_share(self, children: list[dict], animated: bool = True) -> None:
        if self._closed:
            return
        self._s_children = children

        def swap() -> None:
            self._s_table.reloadData()
            self._s_empty.setHidden_(bool(children))
            self._s_level_label.setStringValue_("当前：" + self._s_crumbs[-1][0])
            self._refresh_sel_label()

        crossfade(self._s_list_wrap, swap) if animated else swap()

    def _refresh_sel_label(self) -> None:
        if self._s_sel_label is None:
            return
        n = self._effective_count()
        self._s_sel_label.setStringValue_(f"已选 {n} 项" if n else "未选择任何项目")

    def _effective_count(self) -> int:
        """提交时真正会转存的项目数（按父级去重后）。"""
        keys = set(self._selection.keys())
        return sum(1 for fid, p in self._selection.items() if p not in keys)

    def _load_share_level(self, cid: str, name: str, animated: bool = True,
                          on_error=None) -> None:
        if self._closed or self._share_fs is None:
            return
        self._nav_busy = True
        self._s_spin.setHidden_(False)
        self._s_spin.startAnimation_(None)
        self._load_ticket += 1
        ticket = self._load_ticket

        def worker() -> None:
            try:
                items = self.engine.share_list(self._share_fs, cid)
            except Exception as e:  # noqa: BLE001
                if ticket != self._load_ticket or self._closed:
                    return
                self._post_ui("shareLoad:", f"err\x00{str(e)}")
                return
            if ticket != self._load_ticket or self._closed:
                return
            # 进入新目录时，默认把其下所有项目加入选择（整份分享快速转存）
            for it in items:
                self._selection.setdefault(it["id"], cid)
            self._pending_items = items
            self._post_ui("shareLoad:", cid)

        threading.Thread(target=worker, daemon=True).start()

    def ui_share_load(self, payload) -> None:
        if payload.startswith("err\x00"):
            self._nav_busy = False
            self._s_spin.stopAnimation_(None)
            self._s_spin.setHidden_(True)
            self._nav_done_share()
            self._show_error(payload.split("\x00", 1)[1], warn=True)
            return
        cid = payload
        items = getattr(self, "_pending_items", [])
        self._nav_done_share()
        self._render_share(items, animated=True)

    def _nav_done_share(self) -> None:
        self._nav_busy = False
        self._s_spin.stopAnimation_(None)
        self._s_spin.setHidden_(True)

    def do_share_up(self) -> None:
        if self._busy or self._nav_busy or len(self._s_crumbs) <= 1:
            return
        self._s_crumbs.pop()
        self._load_share_level(self._s_cur_cid, self._s_crumbs[-1][0], animated=True)

    def do_share_row_double(self) -> None:
        row = -1
        try:
            row = self._s_table.clickedRow()
        except Exception:  # noqa: BLE001
            pass
        if row < 0:
            try:
                row = self._s_table.selectedRow()
            except Exception:  # noqa: BLE001
                pass
        if not (0 <= row < len(self._s_children)):
            return
        child = self._s_children[row]
        if not child["is_dir"]:
            return  # 文件不进入，仅可勾选
        self._s_crumbs.append((child["name"], child["id"]))
        self._load_share_level(child["id"], child["name"], animated=True)

    def do_share_all(self) -> None:
        for it in self._s_children:
            self._selection[it["id"]] = self._s_cur_cid
        self._s_table.reloadData()
        self._refresh_sel_label()

    def do_share_none(self) -> None:
        cur = {it["id"] for it in self._s_children}
        for fid in list(self._selection.keys()):
            if self._selection[fid] == self._s_cur_cid or fid in cur:
                self._selection.pop(fid, None)
        self._s_table.reloadData()
        self._refresh_sel_label()

    def do_toggle(self, sender) -> None:
        fid = sender.representedObject()
        if fid is None:
            return
        if fid in self._selection:
            self._selection.pop(fid, None)
            sender.setState_(0)
        else:
            self._selection[fid] = self._s_cur_cid
            sender.setState_(1)
        self._refresh_sel_label()
        self._refresh_footer()

    def do_share_row_click(self) -> None:
        """单击分享列表行 → 切换该行的勾选状态。"""
        row = -1
        try:
            row = self._s_table.clickedRow()
        except Exception:  # noqa: BLE001
            pass
        if not (0 <= row < len(self._s_children)):
            return
        fid = self._s_children[row]["id"]
        # 复用 toggle 逻辑（但不需要 sender，直接操作）
        if fid in self._selection:
            self._selection.pop(fid, None)
        else:
            self._selection[fid] = self._s_cur_cid
        self._s_table.reloadData()
        self._refresh_sel_label()
        self._refresh_footer()

    def do_new_folder(self) -> None:
        """在当前目标目录下新建文件夹。"""
        if self._busy or self._nav_busy:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("新建文件夹")
        alert.setInformativeText_(f"将在「{self._t_cur_path}」下创建：")
        alert.addButtonWithTitle_("创建")
        alert.addButtonWithTitle_("取消")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 24))
        alert.setAccessoryView_(field)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        name = (field.stringValue() or "").strip()
        if not name or "/" in name or name in (".", ".."):
            self._show_error("请输入不含 / 的文件夹名", warn=True)
            return
        new_path = _join(self._t_cur_path, name)
        self._pending_new_name = name
        self._nav_busy = True
        self._t_spin.setHidden_(False)
        self._t_spin.startAnimation_(None)
        ticket = self._load_ticket + 1
        self._load_ticket = ticket

        def worker():
            try:
                new_cid = self.engine.create_dir(new_path)
                children = self.engine.list_dir_children(new_cid) or []
            except Exception as e:
                if ticket != self._load_ticket or self._closed:
                    return
                self._post_ui("failure:", f"新建文件夹失败：{e}")
                return
            if ticket != self._load_ticket or self._closed:
                return
            self._post_ui("newFolderDone:", f"{new_cid}\x00{self._pending_new_name}")

        threading.Thread(target=worker, daemon=True).start()

    def ui_new_folder_done(self, payload: str) -> None:
        """主线程回调：新建文件夹完成，进入新目录。"""
        self._nav_busy = False
        self._t_spin.stopAnimation_(None)
        self._t_spin.setHidden_(True)
        try:
            new_cid, name = payload.split("\x00", 1)
        except ValueError:
            return
        # 清掉父目录缓存（多了一个子文件夹）
        self._t_cache.pop(self._t_cur_cid, None)
        self._t_crumbs.append((name, new_cid))
        self._t_cache[new_cid] = []
        # 立即加载新目录内容
        self._load_target_children(animated=True)

    # ---------------- 目标目录浏览（复用已验证导航逻辑） ----------------

    @property
    def _t_cur_cid(self) -> str:
        return self._t_crumbs[-1][1] or "0"

    @property
    def _t_cur_path(self) -> str:
        names = [c[0] for c in self._t_crumbs[1:]]
        return "/" + "/".join(names) if names else "/"

    def _load_target_children(self, animated: bool = True, on_error=None) -> None:
        if self._closed:
            return
        cid = self._t_cur_cid
        if cid in self._t_cache:
            self._render_target(self._t_cache[cid], animated)
            return
        self._nav_busy = True
        self._t_spin.setHidden_(False)
        self._t_spin.startAnimation_(None)
        self._load_ticket += 1
        ticket = self._load_ticket

        def worker() -> None:
            try:
                children = self.engine.list_dir_children(cid) or []
            except Exception as e:  # noqa: BLE001
                if ticket != self._load_ticket or self._closed:
                    return
                self._post_ui("targetLoad:", f"err\x00{str(e)}")
                return
            if ticket != self._load_ticket or self._closed:
                return
            self._pending_items = children
            self._post_ui("targetLoad:", cid)

        threading.Thread(target=worker, daemon=True).start()

    def ui_target_load(self, payload) -> None:
        if payload.startswith("err\x00"):
            self._nav_done_target()
            self._show_error(payload.split("\x00", 1)[1], warn=True)
            return
        children = getattr(self, "_pending_items", [])
        self._t_cache[payload] = children
        self._nav_done_target()
        self._render_target(children, animated=True)

    def _nav_done_target(self) -> None:
        self._nav_busy = False
        self._t_spin.stopAnimation_(None)
        self._t_spin.setHidden_(True)

    def _render_target(self, children: list[dict], animated: bool = True) -> None:
        if self._closed:
            return
        self._t_children = children

        def swap() -> None:
            self._t_table.reloadData()
            self._t_empty.setHidden_(bool(children))
            self._update_target_path_control()

        crossfade(self._t_list_wrap, swap) if animated else swap()

    def _update_target_path_control(self) -> None:
        pc = self._t_path_ctrl
        if pc is None:
            return
        items = []
        for i, (name, _cid) in enumerate(self._t_crumbs):
            item = NSPathControlItem.alloc().init()
            item.setTitle_("115" if i == 0 else (name or "/"))
            if i == 0:
                img = _breadcrumb_root_icon()
                if img is not None:
                    item.setImage_(img)
            items.append(item)
        try:
            pc.setPathItems_(items)
        except Exception:  # noqa: BLE001
            pass

    def do_target_up(self, depth: int) -> None:
        if self._busy or self._nav_busy or depth < 0 or depth >= len(self._t_crumbs):
            return
        if depth == len(self._t_crumbs) - 1:
            return
        # 面包屑各级在本流程里始终是已解析的目录 id（启动时解析、进入时记录），
        # 因此这里直接截断并重载即可，无需再走后台解析。
        self._t_crumbs = self._t_crumbs[:depth + 1]
        self._load_target_children()

    def do_target_path_click(self) -> None:
        pc = self._t_path_ctrl
        if pc is None:
            return
        try:
            item = pc.clickedPathItem()
            if item is None:
                return
            idx = list(pc.pathItems()).index(item)
        except Exception:  # noqa: BLE001
            return
        self.do_target_up(idx)

    def do_target_row_double(self) -> None:
        row = -1
        try:
            row = self._t_table.clickedRow()
        except Exception:  # noqa: BLE001
            pass
        if row < 0:
            try:
                row = self._t_table.selectedRow()
            except Exception:  # noqa: BLE001
                pass
        if not (0 <= row < len(self._t_children)):
            return
        child = self._t_children[row]
        self._t_crumbs.append((child["name"], child["cid"]))
        self._load_target_children(animated=True)

    def do_target_cell_click(self) -> None:
        try:
            self._win.makeFirstResponder_(self._t_table)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 行数据（桥转发） ----------------

    def s_row_count(self) -> int:
        return len(self._s_children or [])

    def s_row_name(self, row: int) -> str:
        kids = self._s_children or []
        return kids[row]["name"] if 0 <= row < len(kids) else ""

    def t_row_count(self) -> int:
        return len(self._t_children or [])

    def t_row_name(self, row: int) -> str:
        kids = self._t_children or []
        return kids[row]["name"] if 0 <= row < len(kids) else ""

    def s_cell_for_row(self, tv, row: int):
        view = tv.makeViewWithIdentifier_owner_("shareRow", None)
        if view is None:
            cols0 = tv.tableColumns()
            w = int(cols0[0].width()) if cols0 else 574
            view = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0, 0, w, 32))
            view.setIdentifier_("shareRow")
            view.setAutoresizingMask_(NSViewWidthSizable)

            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(11, 8, 17, 17))
            iv.setImageScaling_(2)
            # 文本框只到 w-172：右侧留足尺寸(50)+箭头(12)+勾选框(20)+间距，
            # 无论文件名多长，右边一排控件都压在可见区内。
            tf = NSTextField.alloc().initWithFrame_(NSMakeRect(34, 7, w - 172, 18))
            tf.setBezeled_(False)
            tf.setDrawsBackground_(False)
            tf.setEditable_(False)
            tf.setSelectable_(False)
            tf.setFont_(NSFont.systemFontOfSize_(13.0))
            tf.setTextColor_(NSColor.labelColor())
            tf.setLineBreakMode_(NSLineBreakByTruncatingTail)
            tf.setToolTip_("悬停查看完整文件名")
            tf.setAutoresizingMask_(NSViewWidthSizable)
            sz = NSTextField.alloc().initWithFrame_(NSMakeRect(w - 132, 8, 50, 16))
            sz.setBezeled_(False)
            sz.setDrawsBackground_(False)
            sz.setEditable_(False)
            sz.setSelectable_(False)
            sz.setFont_(NSFont.systemFontOfSize_(11.0))
            sz.setTextColor_(NSColor.tertiaryLabelColor())
            sz.setAlignment_(2)
            sz.setTag_(98)  # 复用路径用 viewWithTag_ 找回，别再靠 subviews() 下标
            sz.setAutoresizingMask_(NSViewWidthSizable)
            cb = NSButton.alloc().initWithFrame_(NSMakeRect(w - 34, 7, 20, 18))
            cb.setButtonType_(_SWITCH)
            cb.setTitle_("")
            cb.setControlSize_(1)
            cb.setTarget_(self._bridge)
            cb.setAction_(bridge_action(self._bridge, "toggle"))
            cv = NSImageView.alloc().initWithFrame_(NSMakeRect(w - 50, 10, 12, 12))
            cv.setImageScaling_(2)
            cv.setImage_(self.chevron_icon())
            cv.setTag_(99)

            view.setImageView_(iv)
            view.setTextField_(tf)
            view.addSubview_(iv)
            view.addSubview_(tf)
            view.addSubview_(sz)
            view.addSubview_(cv)
            view.addSubview_(cb)

        row_id = self._s_children[row]["id"]
        name = self.s_row_name(row)
        is_dir = self._s_children[row]["is_dir"]
        # 复用路径按当前实际行宽重排所有动态元素（autoresizing 在表格里不可靠）
        cols0 = tv.tableColumns()
        cw = int(cols0[0].width()) if cols0 else 574
        tf = view.textField()
        tf.setFrame_(NSMakeRect(34, 7, max(cw - 172, 60), 18))
        tf.setStringValue_(name)
        tf.setToolTip_(name)
        view.imageView().setImage_(self.folder_icon() if is_dir else self.file_icon())
        sz = view.viewWithTag_(98)
        if sz is not None and isinstance(sz, NSTextField):
            sz.setFrame_(NSMakeRect(cw - 132, 8, 50, 16))
            sz.setStringValue_(_fmt_size(self._s_children[row]["size"]))
        cb = None
        for v in view.subviews():
            if isinstance(v, NSButton):
                cb = v
                break
        if cb is not None:
            cb.setRepresentedObject_(row_id)
            cb.setState_(1 if row_id in self._selection else 0)
            cb.setFrame_(NSMakeRect(cw - 34, 7, 20, 18))
        cv = view.viewWithTag_(99)
        if cv is not None:
            cv.setFrame_(NSMakeRect(cw - 50, 10, 12, 12))
            try:
                is_sel = (tv.selectedRow() == row)
            except Exception:  # noqa: BLE001
                is_sel = False
            cv.setImage_(self.chevron_icon(is_sel))
        return view

    def t_cell_for_row(self, tv, row: int):
        view = tv.makeViewWithIdentifier_owner_("targetRow", None)
        if view is None:
            cols0 = tv.tableColumns()
            w = int(cols0[0].width()) if cols0 else 574
            view = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0, 0, w, 32))
            view.setIdentifier_("targetRow")
            view.setAutoresizingMask_(NSViewWidthSizable)
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(11, 8, 17, 17))
            iv.setImageScaling_(2)
            tf = NSTextField.alloc().initWithFrame_(NSMakeRect(34, 7, w - 130, 18))
            tf.setBezeled_(False)
            tf.setDrawsBackground_(False)
            tf.setEditable_(False)
            tf.setSelectable_(False)
            tf.setFont_(NSFont.systemFontOfSize_(13.0))
            tf.setTextColor_(NSColor.labelColor())
            tf.setLineBreakMode_(NSLineBreakByTruncatingTail)
            tf.setAutoresizingMask_(NSViewWidthSizable)
            cv = NSImageView.alloc().initWithFrame_(NSMakeRect(w - 26, 10, 12, 12))
            cv.setImageScaling_(2)
            cv.setImage_(self.chevron_icon())
            cv.setTag_(99)
            view.setImageView_(iv)
            view.setTextField_(tf)
            view.addSubview_(iv)
            view.addSubview_(tf)
            view.addSubview_(cv)
        # 复用路径按当前实际行宽重排文本框 + 箭头
        cols0 = tv.tableColumns()
        cw = int(cols0[0].width()) if cols0 else 574
        view.textField().setFrame_(NSMakeRect(34, 7, max(cw - 130, 60), 18))
        view.textField().setStringValue_(self.t_row_name(row))
        view.imageView().setImage_(self.folder_icon())
        cv = view.viewWithTag_(99)
        if cv is not None:
            cv.setFrame_(NSMakeRect(cw - 26, 10, 12, 12))
            try:
                is_sel = (tv.selectedRow() == row)
            except Exception:  # noqa: BLE001
                is_sel = False
            cv.setImage_(self.chevron_icon(is_sel))
        return view

    # ---------------- 提交 ----------------

    def _refresh_footer(self) -> None:
        if self._btn_ok is None:
            return
        ok = self._parsed and self._effective_count() > 0
        self._btn_ok.setEnabled_(ok and not self._busy)

    def do_submit(self) -> None:
        if self._busy:
            return
        if not self._parsed:
            self._show_error("请先粘贴并解析 115 分享链接", warn=True)
            return
        ids = [fid for fid, p in self._selection.items() if p not in set(self._selection.keys())]
        if not ids:
            self._show_error("没有选中任何要转存的项目", warn=True)
            return
        self.set_busy(True)
        threading.Thread(target=self._submit_worker, args=(ids,), daemon=True).start()

    def _submit_worker(self, ids: list[str]) -> None:
        folder = self._t_cur_path
        to_pid = self._t_cur_cid
        error = None
        try:
            self.engine.share_receive(self._share_fs, ids, to_pid)
        except Q115Error as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            log("[share] 提交异常:\n" + traceback.format_exc())
        if error:
            self._post_ui("failure:", error)
        else:
            self._post_ui("success:", f"{len(ids)}|{folder}")

    def ui_success(self, payload) -> None:
        parts = str(payload).split("|", 1)
        try:
            count = int(parts[0])
        except ValueError:
            count = 0
        folder = parts[1] if len(parts) > 1 else self._t_cur_path
        self.set_busy(False)
        self._result = (count, folder)
        self._show_success(count, folder)

    def ui_failure(self, msg) -> None:
        self.set_busy(False)
        self._show_error(str(msg), warn=True)
        self._shake(self._win.contentView())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for b in (self._btn_ok, self._btn_cancel, self._s_up_btn,
                  self._s_all_btn, self._s_none_btn,
                  self._t_new_folder_btn):
            if b is not None:
                b.setEnabled_(not busy)
        self._s_table.setEnabled_(not busy)
        self._t_table.setEnabled_(not busy)
        if busy:
            self._btn_ok.setTitle_("转存中…")
            self._set_foot_spin(True)
            self._status_icon.setHidden_(True)
            self._set_status("正在转存到「" + self._t_cur_path + "」…", NSColor.secondaryLabelColor())
        else:
            self._btn_ok.setTitle_("转存")
            self._set_foot_spin(False)
            self._refresh_footer()

    # ---------------- 反馈 ----------------

    def _set_foot_spin(self, on: bool) -> None:
        if self._foot_spin is None:
            return
        if on:
            self._foot_spin.setHidden_(False)
            self._foot_spin.startAnimation_(None)
        else:
            self._foot_spin.stopAnimation_(None)
            self._foot_spin.setHidden_(True)

    def _set_status(self, text: str, color) -> None:
        if self._status_label is None:
            return
        self._status_label.setStringValue_(text)
        try:
            self._status_label.setTextColor_(color)
        except Exception:  # noqa: BLE001
            pass

    def _show_error(self, msg: str, warn: bool = False) -> None:
        color = NSColor.systemOrangeColor() if warn else NSColor.systemRedColor()
        if self._status_icon is not None:
            self._status_icon.setImage_(symbol(
                "exclamationmark.triangle.fill" if warn else "xmark.circle.fill", 15.0, color))
            self._status_icon.setHidden_(False)
        self._set_status(msg, color if not warn else NSColor.labelColor())

    def _shake(self, view) -> None:
        if view is None:
            return
        origin = view.frame().origin

        def body() -> None:
            for dx in (-7, 6, -4, 3, -2, 1, 0):
                view.animator().setFrameOrigin_((origin.x + dx, origin.y))

        animate(body, DUR_FAST * 2)

    def _show_success(self, count: int, folder: str) -> None:
        shown = "根目录" if folder == "/" else folder
        self._btn_ok.setEnabled_(True)
        self._btn_ok.setTitle_("完成")
        self._btn_cancel.setHidden_(True)
        self._status_icon.setHidden_(True)
        self._set_status("", NSColor.secondaryLabelColor())

        for v in list(self._win.contentView().subviews()):
            v.removeFromSuperview()

        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        mark = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 72, 72))
        mark.setImage_(symbol("checkmark.circle.fill", 72.0, NSColor.systemGreenColor()))
        mark.setImageScaling_(2)
        place(mark, (W - 72) / 2, 250, 72, 72, v)
        v.addSubview_(mark)

        t1 = make_label(f"已转存 {count} 个项目", 19.0, 0.3)
        t1.setAlignment_(2)
        place(t1, 60, 342, W - 120, 26, v)
        v.addSubview_(t1)

        t2 = make_label(f"已保存到「{shown}」，115 服务器处理完成后出现在该目录", 13.0, 0.0,
                        NSColor.secondaryLabelColor())
        t2.setAlignment_(2)
        place(t2, 40, 376, W - 80, 36, v)
        v.addSubview_(t2)

        self._win.contentView().addSubview_(v)
        pop_in(v, DUR_SLOW, overshoot=0.16)
        fade_in_delayed(t1, 0.14, DUR_NORMAL)
        fade_in_delayed(t2, 0.22, DUR_NORMAL)

        _auto = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.15, self._bridge, "autoClose:", None, False)
        NSRunLoop.mainRunLoop().addTimer_forMode_(_auto, NSRunLoopCommonModes)

    # ---------------- 后台→主线程回投 ----------------

    def _post_ui(self, sel: str, arg: str) -> None:
        try:
            self._bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
                sel, arg, False)
        except Exception:  # noqa: BLE001
            log("[share] 主线程回调失败:\n" + traceback.format_exc())

    def do_auto_close(self) -> None:
        self._stop_modal(1)

    def do_cancel(self) -> None:
        if self._busy:
            return
        self._result = None
        self._stop_modal(0)

    def _stop_modal(self, code: int) -> None:
        if self._closed:
            return
        self._closed = True
        self._load_ticket += 1
        self._nav_busy = False
        try:
            self._btn_ok.setEnabled_(False)
        except Exception:  # noqa: BLE001
            pass

        done = {"v": False}

        def finish() -> None:
            if done["v"]:
                return
            done["v"] = True
            try:
                NSApplication.sharedApplication().stopModalWithCode_(code)
            except Exception:  # noqa: BLE001
                pass

        dismiss_window(self._win, None)
        try:
            timer = NSTimer.timerWithTimeInterval_repeats_block_(
                0.25, False, lambda _t: finish())
            NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        except Exception:  # noqa: BLE001
            finish()

    # ---------------- 工具 ----------------

    def _alert(self, title: str, msg: str) -> None:
        a = NSAlert.alloc().init()
        a.setMessageText_(title)
        a.setInformativeText_(msg)
        a.addButtonWithTitle_("知道了")
        a.runModal()


class _ShareBridge(NSObject):
    """只暴露 AppKit 需要的选择器，全部转发给控制器。"""

    # --- 链接输入框 ---
    def controlTextDidChange_(self, _notif) -> None:
        self.owner.do_link_changed()

    # --- 分享内容表 ---
    def numberOfRowsInTableView_(self, tv) -> int:
        if tv is self.owner._s_table:
            return self.owner.s_row_count()
        return self.owner.t_row_count()

    def tableView_objectValueForTableColumn_row_(self, _tv, _col, _row) -> str:
        return ""

    def tableView_viewForTableColumn_row_(self, tv, _col, row: int):
        if tv is self.owner._s_table:
            return self.owner.s_cell_for_row(tv, row)
        return self.owner.t_cell_for_row(tv, row)

    def tableView_shouldEditTableColumn_row_(self, _tv, _col, _row) -> bool:
        return False

    def tableViewSelectionDidChange_(self, _notif) -> None:
        # 选中行变化：仅刷新行内箭头颜色
        try:
            if self.owner._s_table is not None:
                self.owner._s_table.reloadData()
            if self.owner._t_table is not None:
                self.owner._t_table.reloadData()
        except Exception:  # noqa: BLE001
            pass

    # --- 控件动作 ---
    def parse_(self, _sender=None) -> None:
        self.owner.do_parse()

    def shareUp_(self, _sender=None) -> None:
        self.owner.do_share_up()

    def shareAll_(self, _sender=None) -> None:
        self.owner.do_share_all()

    def shareNone_(self, _sender=None) -> None:
        self.owner.do_share_none()

    def toggle_(self, sender=None) -> None:
        self.owner.do_toggle(sender)

    def shareRowClick_(self, _sender=None) -> None:
        self.owner.do_share_row_click()

    def toggleZoom_(self, _sender=None) -> None:
        self.owner.do_toggle_zoom()

    def newFolder_(self, _sender=None) -> None:
        self.owner.do_new_folder()

    def shareRowDouble_(self, _sender=None) -> None:
        self.owner.do_share_row_double()

    def targetRowDouble_(self, _sender=None) -> None:
        self.owner.do_target_row_double()

    def targetCellClick_(self, _sender=None) -> None:
        self.owner.do_target_cell_click()

    def targetPathClick_(self, _sender=None) -> None:
        self.owner.do_target_path_click()

    def submit_(self, _sender=None) -> None:
        self.owner.do_submit()

    def cancelOp_(self, _sender=None) -> None:
        self.owner.do_cancel()

    def autoClose_(self, _timer=None) -> None:
        self.owner.do_auto_close()

    # --- 主线程回调（一个参数，对应 parseDone: / shareLoad: / targetLoad: / success: / failure:）---
    def parseDone_(self, _obj=None) -> None:
        self.owner.ui_parse_done()

    def shareLoad_(self, payload=None) -> None:
        self.owner.ui_share_load(payload or "")

    def targetLoad_(self, payload=None) -> None:
        self.owner.ui_target_load(payload or "")

    def newFolderDone_(self, payload=None) -> None:
        self.owner.ui_new_folder_done(payload or "")

    def success_(self, payload=None) -> None:
        self.owner.ui_success(payload)

    def failure_(self, payload=None) -> None:
        self.owner.ui_failure(payload)

    # --- 窗口关闭（点红色关闭按钮）---
    def windowShouldClose_(self, _sender=None) -> bool:
        try:
            self.owner.do_cancel()
        except Exception:  # noqa: BLE001
            pass
        return False

    def windowWillClose_(self, _notif=None) -> None:
        try:
            if not self.owner._closed:
                NSApplication.sharedApplication().abortModal()
        except Exception:  # noqa: BLE001
            pass
