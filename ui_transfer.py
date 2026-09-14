#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui_transfer.py — 115 秒转的 macOS 原生主窗口

把原来「一个 rumps 输入框 → 一个目录选择窗」的两段式体验，
合并成一张完整的原生窗口：上半区编辑链接、下半区挑保存位置，底部一条按钮收尾。

为什么要合成一张窗：
  原流程会把用户从一个 Alert 弹到一个 NSPanel，两段之间没有视觉连续性，
  也不符合 macOS 原生应用「一件事一张窗」的习惯（对比系统的分享面板、导出面板）。
  现在链接和目录同屏可见，编辑链接时还能顺便挑目录。

外观上遵循 macOS HIG 的几个关键点：
  - 毛玻璃底 + 透明标题栏，内容铺满窗口（系统「存储管理」面板同款做法）
  - 全动态系统色（labelColor / secondaryLabelColor / controlAccentColor），
    深浅色与强调色自动跟随系统，一个十六进制色值都不写死
  - SF Symbol 统一视觉重量，行尾 chevron 明确给出「可进入」的暗示
  - NSPathControl 做面包屑 —— macOS 里表示层级路径的标准控件

动画的作用（不是装饰，是方向提示）：
  - 窗口出场：0.94 → 1.0 缩放入场，给出「从菜单栏图标长出来」的来源感
  - 目录切换：列表淡出下滑 → 换数据 → 反向滑回，用位移暗示层级进/出
  - 提交中：底部 spinner + 按钮转禁用，原位置实时反馈
  - 成功：主区整体换成结果面板 + checkmark 弹入，1.15s 后自动收窗
  - 失败：内容横向抖动（系统密码输错的处理）+ 内联提示，不弹 Alert

实现说明：延续 dir_picker 的「控制器 + 桥」分层 ——
纯 Python 类 _TransferPanel 负责全部布局与逻辑，_TransferBridge(NSObject) 只暴露
AppKit 需要的选择器。pyobjc 会把 NSObject 子类里每个方法按 ObjC 选择器规则解析，
业务逻辑混进去极易踩 BadPrototypeError。
"""

from __future__ import annotations

import threading
import traceback
from typing import Optional

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSBezierPath,
    NSBitmapImageRep,
    NSBox,
    NSBoxCustom,
    NSCenterTextAlignment,
    NSColor,
    NSDeviceRGBColorSpace,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSImageView,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSNoTitle,
    NSObject,
    NSPathControl,
    NSPathControlItem,
    NSPathStyleStandard,
    NSScaleProportionally,
    NSTableColumn,
    NSTableView,
    NSTextAlignmentRight,
    NSTextField,
    NSTextView,
    NSView,
    NSViewWidthSizable,
)
from Foundation import NSTimer, NSRunLoop, NSRunLoopCommonModes

from q115_engine import Q115Engine, Q115Error, extract_links, log
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

W = 640.0          # 窗口宽
H = 700.0          # 窗口高
HEADER = 84.0      # 标题区高度（下方一条分隔线）
BODY_H = H - HEADER


# ---------------------------------------------------------------------------

def run_transfer_panel(engine: Q115Engine, clip: str = "",
                       start: str = "/") -> Optional[tuple[list[str], str, str]]:
    """弹出主窗口。返回 (链接列表, 保存路径, 目录id)；用户取消或用例失败返回 None。"""
    return _TransferPanel(engine, clip, start).run_modal()


def _join(base: str, name: str) -> str:
    return ("/" + name) if base == "/" else base.rstrip("/") + "/" + name


# NSPathControl 会把 item 的图片【强行拉伸填满】它的图标格，完全忽略 NSImage 的 size
# （实测：不管把 image.size 设成 13×13、13×9.4 还是 26×9.4，画出来都是同一个 22×控件高 的方块）。
# 所以给它的图必须先按「图标格」的比例画好、符号居中，拉伸才成为无操作；
# 否则符号会被拉成一条 —— 面包屑根节点那个硬盘图标的畸变就是这么来的。
_PATH_ICON_BOX_W = 22.0
_PATH_ICON_BOX_H_FALLBACK = 26.0
_PATH_ICON_GLYPH_W = 13.0


def _breadcrumb_root_icon(box_h: float):
    """生成面包屑根节点图标：按图标格比例预拉伸，符号保持自身宽高比居中。"""
    sym = symbol("internaldrive", _PATH_ICON_GLYPH_W, NSColor.secondaryLabelColor())
    if sym is None:
        return None
    box_w = _PATH_ICON_BOX_W
    if not (8.0 <= float(box_h or 0.0) <= 60.0):
        box_h = _PATH_ICON_BOX_H_FALLBACK
    gw, gh = sym.size()
    if gw <= 0 or gh <= 0:
        return None
    img = NSImage.alloc().initWithSize_((box_w, box_h))
    for scale in (1, 2):
        px, py = int(round(box_w * scale)), int(round(box_h * scale))
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, px, py, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
        rep.setSize_((px, py))
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, px, py))
        sym.drawInRect_(NSMakeRect(
            (box_w - gw) / 2.0 * scale, (box_h - gh) / 2.0 * scale,
            gw * scale, gh * scale))
        NSGraphicsContext.restoreGraphicsState()
        rep.setSize_((box_w, box_h))
        img.addRepresentation_(rep)
    return img


class _TransferPanel:
    """转存主窗口控制器：界面 + 目录导航 + 提交流程。"""

    _ico_folder = False
    _ico_chevron = False
    _ico_chevron_sel = False

    @classmethod
    def folder_icon(cls):
        if cls._ico_folder is False:
            cls._ico_folder = symbol("folder.fill", 17.0, NSColor.controlAccentColor())
        return cls._ico_folder

    @classmethod
    def chevron_icon(cls, selected: bool = False):
        """行尾指示箭头。选中时换成强调色 —— 明确「这一行可以进去」。"""
        if selected:
            if cls._ico_chevron_sel is False:
                cls._ico_chevron_sel = symbol("chevron.right", 11.0,
                                              NSColor.controlAccentColor())
            return cls._ico_chevron_sel
        if cls._ico_chevron is False:
            cls._ico_chevron = symbol("chevron.right", 11.0,
                                      NSColor.tertiaryLabelColor())
        return cls._ico_chevron

    # ---------------- 生命周期 ----------------

    def __init__(self, engine: Q115Engine, clip: str, start: str) -> None:
        self.engine = engine
        self._clip = clip or ""
        self._result: Optional[tuple[list[str], str, str]] = None
        self._busy = False
        self._closed = False

        path = (start or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        segs = [s for s in path.split("/") if s]

        # 面包屑：[("", "0")] 是根，其后每级是 (名称, cid)。
        # 中间层如果还没解析过就先记 None，等用户点过去再补一次请求。
        start_cid = "0"
        if segs:
            try:
                start_cid = engine.resolve_dir_id(path)
            except Exception:  # noqa: BLE001  上次目录可能已被删掉
                log("[panel] 上次目录失效，回落根目录:\n" + traceback.format_exc())
                segs, path, start_cid = [], "/", "0"

        self._crumbs: list[tuple[str, Optional[str]]] = [("", "0")]
        self._crumbs += [(s, None) for s in segs[:-1]]
        if segs:
            self._crumbs.append((segs[-1], start_cid))

        self._cache: dict[str, list[dict]] = {}
        self._children: list[dict] = []
        self._body_wrap = None
        self._bridge = None
        self._count_msg = ""      # 上一次的计数文案，用来判断要不要闪一下

        # 目录导航异步化：主线程只更新 UI，网络请求全部放到后台线程
        self._nav_busy = False
        self._load_ticket = 0
        self._main_callback = None
        self._pending_new_name = ""  # 新建文件夹时暂存名称，供后台完成回调用

    def run_modal(self) -> Optional[tuple[list[str], str, str]]:
        try:
            self._build()
            self._load_children(animated=False)
        except Q115Error as e:
            self._alert("无法读取目录", str(e))
            return None
        except Exception as e:  # noqa: BLE001
            log("[panel] 建窗失败:\n" + traceback.format_exc())
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
        bridge = _TransferBridge.alloc().init()
        bridge.owner = self
        self._bridge = bridge

        win = make_window(W, H, "转存到 115")
        self._win = win
        # 让用户点红色关闭按钮也能正常收尾（否则 runModalForWindow_ 不会自动结束，
        # 窗口关了 app 却卡在 modal 模式：菜单/退出全失灵）。
        win.setDelegate_(self._bridge)
        root = win.contentView()

        # --- 标题区 ---
        ico = make_icon_view("icloud.and.arrow.down.fill", 34.0,
                             NSColor.controlAccentColor())
        place(ico, PAD, 22, 34, 34, root)
        root.addSubview_(ico)

        title = make_label("转存到 115", 22.0, 0.3, NSColor.labelColor())
        place(title, 70, 22, 380, 28, root)
        root.addSubview_(title)

        try:
            ok, name = self.engine.account_info()
        except Exception:  # noqa: BLE001
            ok, name = True, ""
        sub = make_label(f"已登录 · {name}" if (ok and name) else "115 网盘云下载",
                         12.0, 0.0, NSColor.secondaryLabelColor())
        place(sub, 70, 51, W - 96, 16, root)
        root.addSubview_(sub)

        sep = make_separator(W)
        place(sep, 0, HEADER, W, 1, root)
        root.addSubview_(sep)

        # --- 主体容器：成功后整块换成结果面板，所以单独包一层便于 crossfade ---
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, BODY_H))
        place(content, 0, HEADER, W, BODY_H, root)
        root.addSubview_(content)
        self._body_wrap = content

        self._build_link_area(content)
        self._build_folder_area(content)
        self._build_footer(root)

    def _build_link_area(self, root) -> None:
        lab = make_section_title("链接")
        place(lab, PAD, 16, 200, 16, root)
        root.addSubview_(lab)

        hint = make_label("一行一条 · 磁力 / ed2k / http", 11.0, 0.0,
                          NSColor.tertiaryLabelColor())
        hint.setAlignment_(NSTextAlignmentRight)
        place(hint, W - PAD - 240, 17, 240, 14, root)
        root.addSubview_(hint)

        # 聚焦描边：比卡片外扩 1pt 的一圈强调色，默认全透明。
        # 特意压在卡片下层 —— 露在外面的只有那 1pt，卡片照常接收鼠标事件，
        # 不用为了「点击穿透」再写一个 NSView 子类。
        ring = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        ring.setBoxType_(NSBoxCustom)
        ring.setBorderType_(3)  # NSLineBorder
        ring.setCornerRadius_(R_CARD + 1.0)
        ring.setBorderWidth_(2.0)
        ring.setBorderColor_(NSColor.controlAccentColor())
        ring.setFillColor_(NSColor.clearColor())
        ring.setTitlePosition_(NSNoTitle)
        ring.setAlphaValue_(0.0)
        place(ring, PAD - 1, 37, W - PAD * 2 + 2, 134, root)
        root.addSubview_(ring)
        self._focus_ring = ring

        card = make_card()
        place(card, PAD, 38, W - PAD * 2, 132, root)
        root.addSubview_(card)

        cw, ch = card.frame().size.width, card.frame().size.height
        scroll = make_scroll(NSMakeRect(1, 1, cw - 2, ch - 2))
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, cw - 4, ch - 6))
        tv.setDrawsBackground_(False)
        tv.setRichText_(False)
        tv.setFont_(NSFont.systemFontOfSize_(13.0))
        tv.setTextColor_(NSColor.labelColor())
        tv.setAutomaticLinkDetectionEnabled_(False)
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setAutomaticTextReplacementEnabled_(False)
        tv.setAutoresizingMask_(NSViewWidthSizable)
        tv.setDelegate_(self._bridge)
        scroll.setDocumentView_(tv)
        card.addSubview_(scroll)
        self._editor = tv

        if self._clip:
            tv.setString_(self._clip)
            try:
                tv.setSelectedRange_((len(self._clip), 0))
                tv.scrollRangeToVisible_((len(self._clip), 0))
            except Exception:  # noqa: BLE001
                pass

        ph = make_label("粘贴磁力 / ed2k / http 链接", 13.0, 0.0,
                        NSColor.placeholderTextColor())
        place(ph, 11, 8, 300, 18, card)
        card.addSubview_(ph)
        self._ph_label = ph
        ph.setHidden_(bool(self._clip))

        cnt = make_label("", 12.0, 0.0, NSColor.secondaryLabelColor())
        place(cnt, PAD, 176, W - PAD * 2, 16, root)
        root.addSubview_(cnt)
        self._count_label = cnt
        self._refresh_count()

    def _build_folder_area(self, root) -> None:
        lab = make_section_title("保存到")
        place(lab, PAD, 204, 200, 16, root)
        root.addSubview_(lab)

        tip = make_label("双击进入 · 点上方路径可返回", 11.0, 0.0,
                         NSColor.tertiaryLabelColor())
        tip.setAlignment_(NSTextAlignmentRight)
        place(tip, W - PAD - 240, 205, 240, 14, root)
        root.addSubview_(tip)

        pc = NSPathControl.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        try:
            pc.setPathStyle_(NSPathStyleStandard)
            pc.setTarget_(self._bridge)
            pc.setAction_(bridge_action(self._bridge, "pathClick"))
        except Exception:  # noqa: BLE001
            pc = None
        if pc is not None:
            place(pc, PAD, 226, W - PAD * 2, 26, root)
            root.addSubview_(pc)
        self._path_ctrl = pc

        card = make_card()
        list_h = BODY_H - 260 - 68            # 卡顶 260 → 底部按钮上方留出间距
        place(card, PAD, 260, W - PAD * 2, list_h, root)
        root.addSubview_(card)

        iw = card.frame().size.width - 2
        ih = card.frame().size.height - 2
        scroll = make_scroll(NSMakeRect(1, 1, iw, ih))
        table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, iw, ih))
        col = NSTableColumn.alloc().initWithIdentifier_("name")
        col.setWidth_(iw - 2)
        col.setResizingMask_(2)  # NSTableColumnAutoresizingMask
        table.addTableColumn_(col)
        table.setHeaderView_(None)
        table.setRowHeight_(32.0)
        table.setAllowsMultipleSelection_(False)
        table.setDataSource_(self._bridge)
        table.setDelegate_(self._bridge)
        table.setTarget_(self._bridge)
        table.setAction_(bridge_action(self._bridge, "cellClick"))
        table.setDoubleAction_(bridge_action(self._bridge, "rowDouble"))
        scroll.setDocumentView_(table)
        card.addSubview_(scroll)
        self._table = table
        self._list_wrap = scroll

        spin = make_spinner(20.0)
        place(spin, (iw - 20) / 2, ih / 2 - 10, 20, 20, card)
        card.addSubview_(spin)
        self._list_spin = spin

        empty = make_label("此目录下没有子文件夹", 12.0, 0.0,
                           NSColor.tertiaryLabelColor())
        empty.setAlignment_(NSCenterTextAlignment)
        place(empty, 0, card.frame().size.height / 2 - 16,
              card.frame().size.width, 16, card)
        card.addSubview_(empty)
        empty.setHidden_(True)
        self._empty_label = empty

    def _build_footer(self, root) -> None:
        status_icon = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
        status_icon.setImageScaling_(NSScaleProportionally)
        status_icon.setEditable_(False)
        place(status_icon, PAD, H - 45, 16, 16, root)
        root.addSubview_(status_icon)
        self._status_icon = status_icon
        status_icon.setHidden_(True)

        status_lab = make_label("", 12.0, 0.0, NSColor.secondaryLabelColor())
        place(status_lab, PAD, H - 46, 296, 16, root)
        root.addSubview_(status_lab)
        self._status_label = status_lab

        spin = make_spinner(14.0)
        place(spin, PAD, H - 45, 14, 14, root)
        root.addSubview_(spin)
        self._foot_spin = spin
        spin.setHidden_(True)

        # 底部从右往左排：主按钮 → 取消 → 新建文件夹
        ok_x = W - PAD - 116
        c_x = ok_x - 8 - 76
        n_x = c_x - 8 - 116

        b_ok = make_button("转存", primary=True, size=13.0)
        place(b_ok, ok_x, H - 52, 116, 32, root)
        b_ok.setTarget_(self._bridge)
        b_ok.setAction_(bridge_action(self._bridge, "submit"))
        root.addSubview_(b_ok)
        self._btn_ok = b_ok
        # setDefaultButtonCell 会给它系统强调色 + 回车快捷键（对话框标准行为）
        self._win.setDefaultButtonCell_(b_ok.cell())

        b_cancel = make_button("取消", size=13.0)
        place(b_cancel, c_x, H - 52, 76, 32, root)
        b_cancel.setKeyEquivalent_("\x1b")
        b_cancel.setTarget_(self._bridge)
        b_cancel.setAction_(bridge_action(self._bridge, "cancelOp"))
        root.addSubview_(b_cancel)
        self._btn_cancel = b_cancel

        b_new = make_flat_button("新建文件夹", "folder.badge.plus")
        place(b_new, n_x, H - 52, 116, 32, root)
        b_new.setTarget_(self._bridge)
        b_new.setAction_(bridge_action(self._bridge, "newFolder"))
        root.addSubview_(b_new)
        self._btn_new = b_new

        # _btn_ok 到这一步才存在，所以按钮初始可用性由这里补一次
        self._refresh_count()

    # ---------------- 目录数据 ----------------

    @property
    def _cur_cid(self) -> str:
        return self._crumbs[-1][1] or "0"

    @property
    def cur_path(self) -> str:
        names = [c[0] for c in self._crumbs[1:]]
        return "/" + "/".join(names) if names else "/"

    def _load_children(self, animated: bool = True, on_error=None) -> None:
        """拉当前目录的子文件夹；命中缓存时 0 请求，返回上级几乎瞬时。

        未命中缓存时把网络请求放到后台线程，避免主线程卡住导致窗口失去响应。
        on_error 用于进入子文件夹失败时做额外回滚（比如把面包屑弹回上一级）。
        """
        if self._closed:
            return
        cid = self._cur_cid
        if cid in self._cache:
            self._render(self._cache[cid], animated)
            return
        self._run_nav_task(
            work=lambda: self.engine.list_dir_children(cid) or [],
            on_done=lambda children: self._finish_load(cid, children, animated),
            on_error=on_error if on_error else self._load_failed,
        )

    def _run_nav_task(self, work, on_done, on_error) -> None:
        """把网络 IO 扔到后台线程；完成后安全回到主线程更新 UI。"""
        if self._closed:
            return
        self._nav_busy = True
        self._list_spin.setHidden_(False)
        self._list_spin.startAnimation_(None)
        self._load_ticket += 1
        ticket = self._load_ticket

        def worker() -> None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                if ticket != self._load_ticket or self._closed:
                    return
                self._post_main(lambda: (self._nav_done(), on_error(e)))
                return
            if ticket != self._load_ticket or self._closed:
                return
            self._post_main(lambda: (self._nav_done(), on_done(result)))

        threading.Thread(target=worker, daemon=True).start()

    def _nav_done(self) -> None:
        self._nav_busy = False
        self._list_spin.stopAnimation_(None)
        self._list_spin.setHidden_(True)

    def _post_main(self, callback) -> None:
        self._main_callback = callback
        self._bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
            "runCallback:", None, False)

    def _finish_load(self, cid: str, children: list[dict], animated: bool) -> None:
        self._cache[cid] = children
        self._render(children, animated)

    def _load_failed(self, err) -> None:
        self._nav_done()
        if isinstance(err, Q115Error):
            self._show_error(str(err))
        else:
            log("[panel] 读取目录异常:\n" + traceback.format_exc())
            self._show_error(f"{type(err).__name__}: {err}")

    def _render(self, children: list[dict], animated: bool = True) -> None:
        if self._closed:
            return
        self._children = children

        def swap() -> None:
            self._table.reloadData()
            self._update_path_control()
            self._empty_label.setHidden_(bool(children))

        crossfade(self._list_wrap, swap) if animated else swap()

    def _update_path_control(self) -> None:
        pc = self._path_ctrl
        if pc is None:
            return
        items = []
        box_h = 0.0
        try:
            box_h = pc.frame().size.height or pc.bounds().size.height or 0.0
        except Exception:  # noqa: BLE001
            box_h = 0.0
        for i, (name, _cid) in enumerate(self._crumbs):
            item = NSPathControlItem.alloc().init()
            item.setTitle_("115" if i == 0 else (name or "/"))
            if i == 0:
                img = _breadcrumb_root_icon(box_h)
                if img is not None:
                    item.setImage_(img)
            items.append(item)
        try:
            pc.setPathItems_(items)
        except Exception:  # noqa: BLE001
            pass

    def _focus_depth(self, depth: int) -> None:
        """跳到面包屑第 depth 层（0 = 根）。未解析过的层级会异步查 ID。"""
        if self._busy or self._nav_busy or depth < 0 or depth >= len(self._crumbs):
            return
        if depth == len(self._crumbs) - 1:
            return  # 点当前层，无需动作
        name, cid = self._crumbs[depth]
        if cid is None:
            path = "/" + "/".join(c[0] for c in self._crumbs[1:depth + 1])
            self._run_nav_task(
                work=lambda: self.engine.resolve_dir_id(path),
                on_done=lambda resolved_cid: self._finish_resolve(depth, resolved_cid),
                on_error=lambda err: self._load_failed(err),
            )
            return
        self._crumbs = self._crumbs[:depth + 1]
        self._load_children()

    def _finish_resolve(self, depth: int, cid: str) -> None:
        name = self._crumbs[depth][0]
        self._crumbs[depth] = (name, cid)
        self._crumbs = self._crumbs[:depth + 1]
        self._load_children()

    def _enter_row(self, row: int) -> None:
        if self._busy or self._nav_busy or not (0 <= row < len(self._children)):
            return
        child = self._children[row]
        self._crumbs.append((child["name"], child["cid"]))
        # _load_children 现在异步：进入失败时把面包屑弹回上一级
        self._load_children(animated=True, on_error=self._enter_failed)

    def _enter_failed(self, err) -> None:
        """进入子文件夹失败：弹回上一级并显示错误。"""
        if len(self._crumbs) > 1:
            self._crumbs.pop()
        self._load_failed(err)

    # ---------------- 链接 / 计数 ----------------

    def _editor_text(self) -> str:
        try:
            return self._editor.string() or ""
        except Exception:  # noqa: BLE001
            return ""

    def _links_now(self) -> list[str]:
        return extract_links(self._editor_text())

    def _refresh_count(self) -> None:
        text = self._editor_text()
        self._ph_label.setHidden_(bool(text.strip()))
        links = self._links_now()
        if not text.strip():
            msg = "已读取剪贴板" if self._clip else ""
            color = NSColor.tertiaryLabelColor()
        elif links:
            msg = f"识别到 {len(links)} 条链接"
            color = NSColor.secondaryLabelColor()
        else:
            msg = "没有识别到可用链接"
            color = NSColor.systemOrangeColor()

        if msg != self._count_msg:
            self._count_msg = msg
            if msg:
                fade_in(self._count_label, DUR_FAST)   # 内容变了就闪一下

        self._count_label.setStringValue_(msg)
        self._count_label.setTextColor_(color)

        # 没有可用链接就把「转存」按灰 —— 按钮可用性本身就是状态提示，
        # 比让人点下去再抖一下报错更符合 macOS 习惯。
        btn = getattr(self, "_btn_ok", None)
        if btn is not None:
            btn.setEnabled_(bool(links) and not self._busy)

    # ---------------- bridge 转发入口 ----------------

    def do_text_changed(self) -> None:
        self._refresh_count()

    def do_focus_changed(self, focused: bool) -> None:
        """输入框拿到/失去焦点时点亮外圈描边。

        macOS 表单的聚焦表达就是「描边亮起」，比给输入框套 focus ring 更整。
        """
        ring = getattr(self, "_focus_ring", None)
        if ring is None:
            return
        animate(lambda: ring.animator().setAlphaValue_(1.0 if focused else 0.0),
                DUR_FAST)

    def do_selection_changed(self) -> None:
        """选中行换了 —— 重画一遍，让行尾箭头换成强调色。"""
        try:
            self._table.reloadData()
        except Exception:  # noqa: BLE001
            pass

    def do_path_click(self) -> None:
        pc = self._path_ctrl
        if pc is None:
            return
        try:
            item = pc.clickedPathItem()
            if item is None:
                return
            idx = list(pc.pathItems()).index(item)
        except Exception:  # noqa: BLE001
            return
        self._focus_depth(idx)

    def do_row_double(self) -> None:
        # 优先用 clickedRow：它直接对应被双击的那一行，不受选择状态变化影响
        row = -1
        try:
            row = self._table.clickedRow()
        except Exception:  # noqa: BLE001
            pass
        if row < 0:
            try:
                row = self._table.selectedRow()
            except Exception:  # noqa: BLE001
                pass
        self._enter_row(row)

    def do_cell_click(self) -> None:
        # 单击只负责把键盘焦点交给列表，让回车/方向键能继续操作
        try:
            self._win.makeFirstResponder_(self._table)
        except Exception:  # noqa: BLE001
            pass

    def do_new_folder(self) -> None:
        if self._busy or self._nav_busy:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("新建文件夹")
        alert.setInformativeText_(f"将在「{self.cur_path}」下创建：")
        alert.addButtonWithTitle_("创建")
        alert.addButtonWithTitle_("取消")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 24))
        alert.setAccessoryView_(field)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        name = (field.stringValue() or "").strip()
        if not name or "/" in name or name in (".", ".."):
            self._show_error("请输入不含 / 的文件夹名")
            return
        new_path = _join(self.cur_path, name)
        self._pending_new_name = name
        self._run_nav_task(
            work=lambda: self._create_and_list(new_path),
            on_done=lambda res: self._finish_new_folder(res),
            on_error=lambda err: self._load_failed(err),
        )

    def _create_and_list(self, new_path: str):
        new_cid = self.engine.create_dir(new_path)
        children = self.engine.list_dir_children(new_cid) or []
        return new_cid, children

    def _finish_new_folder(self, res) -> None:
        new_cid, children = res
        # 父目录下多了一个新的子文件夹，清掉父目录缓存
        self._cache.pop(self._cur_cid, None)
        self._crumbs.append((self._pending_new_name, new_cid))
        self._cache[new_cid] = children
        self._render(children, animated=True)

    def do_submit(self) -> None:
        if self._busy:
            return
        links = self._links_now()
        if not links:
            target = self._editor.enclosingScrollView()
            self._shake(target if target is not None else self._editor)
            self._show_error("没有识别到可用链接", warn=True)
            return
        self.set_busy(True)
        threading.Thread(target=self._submit_worker, args=(links,), daemon=True).start()

    def do_cancel(self) -> None:
        if self._busy:
            return
        self._result = None
        self._stop_modal(0)

    # ---------------- 提交 ----------------

    def _submit_worker(self, links: list[str]) -> None:
        folder = self.cur_path
        cid = self._cur_cid
        error: str | None = None
        resp = None
        try:
            resp = self.engine.submit_links(links, dir_id=(cid or "0"))
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            log("[panel] 提交异常:\n" + traceback.format_exc())

        ok_count = len(links)
        if error is None:
            try:
                result = (resp or {}).get("data", {}).get("result") or []
                if result:
                    ok_count = sum(1 for r in result if r.get("state") in (True, 1))
                    bad = [r for r in result if r.get("state") not in (True, 1)]
                    if bad:
                        f = bad[0]
                        error = str(f.get("errtype") or f.get("error_msg")
                                    or f.get("message") or f)
                log(f"[panel] 提交完成 {ok_count}/{len(links)} → {folder}")
            except Exception:  # noqa: BLE001
                pass

        if error:
            self._post_ui("failure:", str(error))
        else:
            self._post_ui("success:", f"{ok_count}|{folder}|{len(links)}")

    def _post_ui(self, sel: str, arg: str) -> None:
        """后台线程 → 主线程的安全回投。"""
        try:
            self._bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
                sel, arg, False
            )
        except Exception:  # noqa: BLE001
            log("[panel] 主线程回调失败:\n" + traceback.format_exc())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._btn_ok.setEnabled_(not busy)
        self._btn_new.setEnabled_(not busy)
        self._btn_cancel.setEnabled_(not busy)
        self._editor.setEditable_(not busy)
        self._table.setEnabled_(not busy)
        if busy:
            self._btn_ok.setTitle_("提交中…")
            self._foot_spin.setHidden_(False)
            self._foot_spin.startAnimation_(None)
            self._status_icon.setHidden_(True)
            self._set_status("正在提交云下载任务…", NSColor.secondaryLabelColor())
        else:
            self._btn_ok.setTitle_("转存")
            self._foot_spin.stopAnimation_(None)
            self._foot_spin.setHidden_(True)

    def ui_success(self, payload) -> None:
        parts = str(payload).split("|", 2)
        try:
            count = int(parts[0])
        except ValueError:
            count = 1
        folder = parts[1] if len(parts) > 1 else self.cur_path
        self.set_busy(False)
        self._result = (self._links_now(), folder, self._cur_cid)
        self._show_success(count, folder)

    def ui_failure(self, msg) -> None:
        self.set_busy(False)
        self._show_error(str(msg), warn=True)
        self._shake(self._win.contentView())

    # ---------------- 反馈 ----------------

    def _set_status(self, text: str, color) -> None:
        self._status_label.setStringValue_(text)
        self._status_label.setTextColor_(color)

    def _show_error(self, msg: str, warn: bool = False) -> None:
        color = NSColor.systemOrangeColor() if warn else NSColor.systemRedColor()
        icon_name = "exclamationmark.triangle.fill" if warn else "xmark.circle.fill"
        self._status_icon.setImage_(symbol(icon_name, 15.0, color))
        self._status_icon.setHidden_(False)
        self._set_status(msg, color if not warn else NSColor.labelColor())

    def _shake(self, view) -> None:
        """输错密码那种横向抖动 —— 比弹 Alert 轻，却一眼就能注意到。"""
        if view is None:
            return
        origin = view.frame().origin

        def body() -> None:
            for dx in (-7, 6, -4, 3, -2, 1, 0):
                view.animator().setFrameOrigin_((origin.x + dx, origin.y))

        animate(body, DUR_FAST * 2)

    def _show_success(self, count: int, folder: str) -> None:
        """主区整体换成成功面板：checkmark 弹入 → 停留 → 自动收窗。"""
        shown = "根目录" if folder == "/" else folder
        self._btn_ok.setEnabled_(True)
        self._btn_ok.setTitle_("完成")
        self._btn_cancel.setHidden_(True)
        self._btn_new.setHidden_(True)
        self._status_icon.setHidden_(True)
        self._set_status("", NSColor.secondaryLabelColor())

        for v in list(self._body_wrap.subviews()):
            v.removeFromSuperview()
        card, t1, t2 = self._build_success_view(count, shown)
        self._body_wrap.addSubview_(card)
        pop_in(card, DUR_SLOW, overshoot=0.16)
        # 对勾先落定，两行文案随后依次跟上 —— 有先后节奏，不会糊成一片
        fade_in_delayed(t1, 0.14, DUR_NORMAL)
        fade_in_delayed(t2, 0.22, DUR_NORMAL)

        # 注意：此处是【模态面板内】，默认模式的 scheduledTimer 不会触发，
        # 必须把 timer 挂到 common run loop modes（含 NSModalPanelRunLoopMode），
        # 否则成功后永远不会自动收窗。
        _auto = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.15, self._bridge, "autoClose:", None, False
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(_auto, NSRunLoopCommonModes)

    def _build_success_view(self, count: int, folder: str):
        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, BODY_H))

        mark = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 72, 72))
        mark.setImage_(symbol("checkmark.circle.fill", 72.0,
                              NSColor.systemGreenColor()))
        mark.setImageScaling_(NSScaleProportionally)
        place(mark, (W - 72) / 2, 180, 72, 72, v)
        v.addSubview_(mark)

        t1 = make_label(f"已提交 {count} 条云下载任务", 19.0, 0.3)
        t1.setAlignment_(NSCenterTextAlignment)
        place(t1, 60, 272, W - 120, 26, v)
        v.addSubview_(t1)

        t2 = make_label(f"正在由 115 服务器下载，完成后出现在 {folder}", 13.0, 0.0,
                        NSColor.secondaryLabelColor())
        t2.setAlignment_(NSCenterTextAlignment)
        place(t2, 40, 306, W - 80, 18, v)
        v.addSubview_(t2)

        return v, t1, t2

    def do_auto_close(self) -> None:
        self._stop_modal(1)

    def _stop_modal(self, code: int) -> None:
        if self._closed:
            return
        self._closed = True
        # 取消所有在途的异步目录加载，避免收窗后回调再去操作已释放的视图
        self._load_ticket += 1
        self._nav_busy = False
        try:
            self._btn_ok.setEnabled_(False)   # 收窗动画期间别再接受点击
        except Exception:  # noqa: BLE001
            pass

        # 结束模态【绝不能】依赖动画 completion：NSAnimationContext 的 completion
        # 在 modal 循环内不保证触发，一旦不触发，窗口关了 app 却永久卡在 modal 模式
        # （菜单点击、退出全失灵）。
        # 可靠做法：把 stopModal 排进「common run loop modes」的计时器 ——
        # common modes 包含 NSModalPanelRunLoopMode，故模态循环一定会处理它；
        # 反之 scheduledTimer(默认模式) 在 modal 循环内【根本不会触发】。
        done = {"v": False}

        def finish() -> None:
            if done["v"]:
                return
            done["v"] = True
            try:
                NSApplication.sharedApplication().stopModalWithCode_(code)
            except Exception:  # noqa: BLE001
                pass

        dismiss_window(self._win, None)   # 收场动画只负责观感，不承担结束模态
        try:
            from Foundation import NSTimer, NSRunLoop, NSRunLoopCommonModes

            timer = NSTimer.timerWithTimeInterval_repeats_block_(
                0.25, False, lambda _t: finish())
            NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        except Exception:  # noqa: BLE001
            # 极端兜底：连计时器都建不出来就同步结束模态 —— 宁可牺牲动画也不能卡死
            finish()

    # ---------------- 行数据（bridge 转发） ----------------

    def row_count(self) -> int:
        return len(self._children or [])

    def row_name(self, row: int) -> str:
        kids = self._children or []
        return kids[row]["name"] if 0 <= row < len(kids) else ""

    def cell_for_row(self, tv, row: int):
        from AppKit import NSTableCellView

        view = tv.makeViewWithIdentifier_owner_("q115row", None)
        if view is None:
            w = tv.bounds().size.width or 500
            view = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0, 0, w, 32))
            view.setIdentifier_("q115row")
            view.setAutoresizingMask_(NSViewWidthSizable)

            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(11, 8, 17, 17))
            iv.setImageScaling_(NSScaleProportionally)
            tf = NSTextField.alloc().initWithFrame_(NSMakeRect(38, 7, w - 66, 19))
            tf.setBezeled_(False)
            tf.setDrawsBackground_(False)
            tf.setEditable_(False)
            tf.setSelectable_(False)
            tf.setFont_(NSFont.systemFontOfSize_(13.0))
            tf.setTextColor_(NSColor.labelColor())
            tf.setLineBreakMode_(NSLineBreakByTruncatingTail)
            tf.setAutoresizingMask_(NSViewWidthSizable)
            cv = NSImageView.alloc().initWithFrame_(NSMakeRect(w - 26, 10, 12, 12))
            cv.setImageScaling_(NSScaleProportionally)
            cv.setImage_(self.chevron_icon())
            cv.setTag_(99)  # pyobjc 对象不能挂 Python 属性，用 tag 找回它

            view.setImageView_(iv)
            view.setTextField_(tf)
            view.addSubview_(iv)
            view.addSubview_(tf)
            view.addSubview_(cv)

        # 行宽随窗口变化，chevron 每次都要贴回右边缘
        cv = view.viewWithTag_(99)
        if cv is not None:
            cv.setFrame_(NSMakeRect(tv.bounds().size.width - 26, 10, 12, 12))
            try:
                is_sel = (tv.selectedRow() == row)
            except Exception:  # noqa: BLE001
                is_sel = False
            cv.setImage_(self.chevron_icon(is_sel))

        view.textField().setStringValue_(self.row_name(row))
        fi = self.folder_icon()
        if fi is not None:
            view.imageView().setImage_(fi)
        return view

    # ---------------- 工具 ----------------

    def _alert(self, title: str, msg: str) -> None:
        a = NSAlert.alloc().init()
        a.setMessageText_(title)
        a.setInformativeText_(msg)
        a.addButtonWithTitle_("知道了")
        a.runModal()


def bridge_action(bridge, name: str):
    """取 bridge 上某个 action 的绑定方法，交给 setAction_ 用。

    直接传 bound method 比传选择器字符串更稳 —— pyobjc 会自己解析好签名。
    """
    return getattr(bridge, name + "_")


class _TransferBridge(NSObject):
    """只暴露 AppKit 需要的选择器，全部转发给控制器。

    方法名里的下划线对应 ObjC 选择器的冒号参数，参数个数必须严格一致 ——
    在这里写业务逻辑或多加参数，pyobjc 都会报 BadPrototypeError。
    """

    # --- NSTableView 数据源 / 代理 ---
    def numberOfRowsInTableView_(self, _tv) -> int:
        return self.owner.row_count()

    def tableView_objectValueForTableColumn_row_(self, _tv, _col, row: int) -> str:
        return self.owner.row_name(row)

    def tableView_viewForTableColumn_row_(self, tv, _col, row: int):
        return self.owner.cell_for_row(tv, row)

    def tableView_shouldEditTableColumn_row_(self, _tv, _col, _row) -> bool:
        return False

    # --- NSTextView 代理 ---
    def textDidChange_(self, _notif) -> None:
        self.owner.do_text_changed()

    def textDidBeginEditing_(self, _notif) -> None:
        self.owner.do_focus_changed(True)

    def textDidEndEditing_(self, _notif) -> None:
        self.owner.do_focus_changed(False)

    # --- NSTableView 选中变化 ---
    def tableViewSelectionDidChange_(self, _notif) -> None:
        self.owner.do_selection_changed()

    # --- 主线程回调 ---
    def runCallback_(self, _obj=None) -> None:
        cb = getattr(self.owner, "_main_callback", None)
        if cb is None:
            return
        self.owner._main_callback = None
        try:
            cb()
        except Exception:  # noqa: BLE001
            log("[panel] 主线程回调异常:\n" + traceback.format_exc())

    # --- 窗口关闭（点红色关闭按钮）---
    def windowShouldClose_(self, _sender=None) -> bool:
        # 交给 do_cancel 走统一的收尾路径：禁用按钮 -> 缩收回收动画 ->
        # stopModalWithCode 结束模态会话 -> run_modal 里 orderOut。
        # 返回 False 阻止系统默认的「直接关窗」，避免模态会话残留。
        try:
            self.owner.do_cancel()
        except Exception:  # noqa: BLE001
            pass
        return False

    def windowWillClose_(self, _notif=None) -> None:
        # 兜底：若窗口从其它路径（如程序化 close()）被关掉，
        # 确保模态会话一定结束，绝不让 app 卡在 modal 模式。
        try:
            if not self.owner._closed:
                NSApplication.sharedApplication().abortModal()
        except Exception:  # noqa: BLE001
            pass

    # --- 控件动作（无参选择器） ---
    def rowDouble_(self, _sender=None) -> None:
        self.owner.do_row_double()

    def cellClick_(self, _sender=None) -> None:
        self.owner.do_cell_click()

    def newFolder_(self, _sender=None) -> None:
        self.owner.do_new_folder()

    def submit_(self, _sender=None) -> None:
        self.owner.do_submit()

    def cancelOp_(self, _sender=None) -> None:
        self.owner.do_cancel()

    def pathClick_(self, _sender=None) -> None:
        self.owner.do_path_click()

    def autoClose_(self, _timer=None) -> None:
        self.owner.do_auto_close()

    # --- 主线程回调（一个参数，对应选择器 success: / failure:） ---
    def success_(self, payload=None) -> None:
        self.owner.ui_success(payload)

    def failure_(self, payload=None) -> None:
        self.owner.ui_failure(payload)
