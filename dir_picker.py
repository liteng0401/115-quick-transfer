#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dir_picker.py — 115 网盘目录的「可视化」选择窗口（AppKit 原生实现）

在 rumps 菜单栏应用中以模态方式弹出一个原生窗口：
  - 列表展示当前目录下的子文件夹，单击选中、双击进入
  - 「上级」返回上一级；「刷新」重新读取；「进入」打开选中的文件夹
  - 「新建文件夹…」在当前目录下创建子文件夹并自动进入
  - 「选择此文件夹」确认保存位置；「取消」放弃

返回 (路径, 目录id) —— 目录 id 直接用于提交云下载，省一次解析请求。

实现说明：纯 Python 控制器 _DirPicker 负责全部逻辑与界面布局；
NSObject 子类 _DirPickerBridge 只负责把 AppKit 需要的选择器（table 数据源/代理、
按钮动作）转发给控制器 —— pyobjc 会把 NSObject 子类里的每个方法都按
“ObjC 选择器”规则解析参数个数，逻辑方法放里面很容易踩 BadPrototypeError。
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from typing import Callable, Optional

from Foundation import NSObject
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSProgressIndicator,
    NSProgressIndicatorSpinningStyle,
    NSApplication,
    NSClosableWindowMask,
    NSPanel,
    NSScrollView,
    NSTableCellView,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSTitledWindowMask,
)

from q115_engine import Q115DirNotFound, Q115Engine, Q115Error, log


def pick_save_folder(engine: Q115Engine, start: str = "/") -> Optional[tuple[str, str]]:
    """弹出可视化目录选择窗口。

    返回 (用户选定的网盘路径, 目录id)，例如 ("/媒体库/电影", "123456")；
    用户取消或发生不可恢复错误时返回 None。
    """
    return _DirPicker(engine, start).run_modal()


def _join(base: str, name: str) -> str:
    return ("/" + name) if base == "/" else base.rstrip("/") + "/" + name


class _DirPicker:
    """纯 Python 控制器：界面布局 + 115 目录逻辑（不受 pyobjc 选择器规则限制）。"""

    _folder_icon_cache = False  # False=未加载；None=系统不支持 SF Symbol

    @classmethod
    def _icon_folder(cls):
        """SF Symbol 文件夹图标（只创建一次并缓存）。"""
        if cls._folder_icon_cache is False:
            try:
                cls._folder_icon_cache = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    "folder.fill", None
                )
            except Exception:  # noqa: BLE001
                cls._folder_icon_cache = None
        return cls._folder_icon_cache

    def __init__(self, engine: Q115Engine, start: str = "/") -> None:
        self.engine = engine
        path = (start or "/").strip() or "/"
        self.path = path if path.startswith("/") else "/" + path
        self._cid: str = "0"
        self._children: list[dict] = []
        # 面包屑栈：每级存 (路径, 目录id, 该级子目录列表)，返回上级时零网络请求
        self._stack: list[tuple[str, str, list[dict]]] = []
        self._result: Optional[tuple[str, str]] = None
        self._win = None
        self._table = None
        self._path_label = None
        self._up_btn = None
        self._choose_btn = None
        self._spinner = None
        self._bridge: Optional[_DirPickerBridge] = None
        # 异步加载状态：后台线程拉目录时显示转圈，主线程不卡顿
        self._load_ticket = 0
        self._closed = False
        self._pending_callbacks: deque[Callable[[], None]] = deque()

    # ---------- 入口 ----------

    def run_modal(self) -> Optional[tuple[str, str]]:
        if not self.engine.is_logged_in():
            return None
        try:
            self._build_ui()
            self._enter_initial()
        except Q115Error as e:
            self._alert("无法读取目录", str(e))
            return None
        except Exception as e:  # noqa: BLE001
            log("[dirpicker] 打开窗口失败:\n" + traceback.format_exc())
            self._alert("无法打开选择窗口", f"{type(e).__name__}: {e}")
            return None

        self._result = None
        app = NSApplication.sharedApplication()
        self._win.makeKeyAndOrderFront_(None)
        try:
            app.activateIgnoringOtherApps_(True)
        except Exception:  # noqa: BLE001
            pass
        app.runModalForWindow_(self._win)
        self._closed = True  # 任何在途的后台加载回调都不再触碰界面
        self._win.orderOut_(None)
        return self._result

    def _enter_initial(self) -> None:
        """打开窗口时的起点目录（可能是上次用过的目录，需解析一次拿到 id）。"""
        try:
            self._cid = "0" if self.path == "/" else self.engine.resolve_dir_id(self.path)
        except Q115DirNotFound:
            if self.path != "/":
                self.path = "/"
                self._cid = "0"
            else:
                raise
        # 第一次读取同步拿数据（窗口还没弹出来，异步回调还没法渲染）
        self._load_children(async_=False)

    # ---------- 界面搭建 ----------

    def _build_ui(self) -> None:
        W, H = 560, 430
        bridge = _DirPickerBridge.alloc().init()
        bridge.owner = self
        self._bridge = bridge

        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered, False
        )
        win.setTitle_("选择本次保存位置（115 网盘）")
        win.setReleasedWhenClosed_(False)
        win.center()
        self._win = win
        # 让用户点红色关闭按钮也能正常收尾（否则 runModalForWindow_ 不会自动结束，
        # 窗口关了 app 却卡在 modal 模式：菜单/退出全失灵）。
        win.setDelegate_(bridge)
        content = win.contentView()

        # 顶部：当前位置
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(12, H - 30, W - 24, 20))
        label.setEditable_(False)
        label.setSelectable_(True)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setTextColor_(NSColor.labelColor())
        label.setFont_(NSFont.systemFontOfSize_(13))
        label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        content.addSubview_(label)
        self._path_label = label

        # 加载中转圈（后台拉目录时显示，主线程不冻结）
        spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(W - 24, H - 25, 14, 14))
        spinner.setStyle_(NSProgressIndicatorSpinningStyle)
        spinner.setIndeterminate_(True)
        spinner.setBezeled_(False)
        spinner.setDisplayedWhenStopped_(False)
        spinner.setHidden_(True)
        content.addSubview_(spinner)
        self._spinner = spinner

        # 中部：文件夹列表
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 54, W - 24, H - 96))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, W - 40, H - 120))
        col = NSTableColumn.alloc().initWithIdentifier_("name")
        col.setWidth_(W - 60)
        table.addTableColumn_(col)
        table.setHeaderView_(None)
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setAllowsMultipleSelection_(False)
        table.setRowHeight_(26)
        table.setDataSource_(bridge)
        table.setDelegate_(bridge)
        table.setTarget_(bridge)
        table.setDoubleAction_(bridge.rowDouble_)
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)
        self._table = table

        # 底部按钮（动作都走 bridge，再转发回本控制器）
        btn_specs = [
            ("上级", 12, 14, 62, bridge.goUp_),
            ("进入", 80, 14, 62, bridge.enter_),
            ("刷新", 148, 14, 62, bridge.refresh_),
            ("新建文件夹…", 216, 14, 108, bridge.newFolder_),
            ("取消", 340, 14, 88, bridge.cancel_),
            ("选择此文件夹", 434, 14, 114, bridge.choose_),
        ]
        for title, x, y, w, action in btn_specs:
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 30))
            b.setTitle_(title)
            b.setBezelStyle_(NSBezelStyleRounded)
            b.setTarget_(bridge)
            b.setAction_(action)
            content.addSubview_(b)
            if title == "上级":
                self._up_btn = b
            elif title == "选择此文件夹":
                self._choose_btn = b
            elif title == "取消":
                b.setKeyEquivalent_("\x1b")
        # 回车 = 选择此文件夹
        self._choose_btn.setKeyEquivalent_("\r")

    # ---------- 数据 ----------

    def _display(self) -> str:
        return "根目录 /" if self.path == "/" else self.path

    def _set_busy(self, busy: bool) -> None:
        if self._spinner is None:
            return
        if busy:
            self._spinner.setHidden_(False)
            self._spinner.startAnimation_(None)
        else:
            self._spinner.stopAnimation_(None)
            self._spinner.setHidden_(True)

    def _load_children(self, async_: bool = True, err_title: str = "无法读取目录",
                       on_error: Optional[Callable[[Exception], None]] = None) -> None:
        """按当前 self._cid 拉取子文件夹并渲染。

        async_=True（用户点击导航时）：后台线程发请求，主线程转圈但不卡；
        async_=False（首次打开窗口时）：同步拿数据，窗口出来就直接有内容。
        用 _load_ticket 防止旧请求结果覆盖新请求（竞态保护）。
        """
        ticket = self._load_ticket + 1
        self._load_ticket = ticket
        self._set_busy(True)

        def default_err(e: Exception) -> None:
            self._alert(err_title, str(e))

        err_cb = on_error or default_err

        if async_:
            def work():
                return self.engine.list_dir_children(self._cid) or []

            def on_done(children: list[dict]) -> None:
                if ticket != self._load_ticket or self._closed:
                    return
                self._children = children
                self._render(children)
                self._set_busy(False)

            def on_fail(e: Exception) -> None:
                if ticket != self._load_ticket or self._closed:
                    return
                self._set_busy(False)
                err_cb(e)

            self._run_nav_task(work, on_done, on_fail)
        else:
            try:
                children = self.engine.list_dir_children(self._cid) or []
            except Exception as e:  # noqa: BLE001
                self._set_busy(False)
                raise
            self._children = children
            self._render(children)
            self._set_busy(False)

    def _run_nav_task(self, work, on_done, on_error) -> None:
        """后台线程跑网络请求，结果回到主线程执行 on_done / on_error。"""
        def runner() -> None:
            try:
                res = work()
                self._post_main(lambda: on_done(res))
            except Q115Error as e:
                self._post_main(lambda: on_error(e))
            except Exception as e:  # noqa: BLE001
                log("[dirpicker] 后台加载异常:\n" + traceback.format_exc())
                self._post_main(lambda: on_error(e))
        threading.Thread(target=runner, daemon=True).start()

    def _post_main(self, callback: Callable[[], None]) -> None:
        """把回调排到主线程跑（模态会话期间也能被 run loop 处理）。"""
        self._pending_callbacks.append(callback)
        bridge = self._bridge
        if bridge is None:
            # UI 还没建好（理论上异步加载不会走到这里），直接跑兜底
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            # 关键：选择器要发到“拥有该方法的 bridge 实例”上，而不是 NSApplication
            bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
                "dpRunCallback:", None, False)
        except Exception:  # noqa: BLE001
            # 极端兜底：直接跑（可能不在主线程，但至少不丢结果）
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

    def _render(self, children: list[dict]) -> None:
        if self._closed or self._table is None:
            return
        self._table.reloadData()
        self._path_label.setStringValue_(
            f"当前位置：{self._display()}（{len(children)} 个子文件夹）"
        )
        self._up_btn.setEnabled_(bool(self._stack))
        self._choose_btn.setTitle_(
            "保存到「根目录」" if self.path == "/" else "选择此文件夹"
        )
        log(f"[dirpicker] 打开目录 {self.path}，子文件夹 {len(children)} 个")

    # ---------- 数据行（给 bridge 转发） ----------

    def row_count(self) -> int:
        return len(self._children or [])

    def row_name(self, row: int) -> str:
        children = self._children or []
        if 0 <= row < len(children):
            return children[row]["name"]
        return ""

    def cell_for_row(self, tv, row: int):
        """view-based 表格单元：文件夹图标 + 名称（视图带复用，不重复创建）。"""
        view = tv.makeViewWithIdentifier_owner_("q115FolderCell", None)
        if view is None:
            w = tv.bounds().size.width or 500
            view = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0, 0, w, 26))
            view.setIdentifier_("q115FolderCell")
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(10, 5, 16, 16))
            tf = NSTextField.alloc().initWithFrame_(NSMakeRect(34, 5, w - 44, 17))
            tf.setBezeled_(False)
            tf.setDrawsBackground_(False)
            tf.setEditable_(False)
            tf.setSelectable_(False)
            tf.setFont_(NSFont.systemFontOfSize_(13))
            tf.setTextColor_(NSColor.labelColor())
            view.setImageView_(iv)
            view.setTextField_(tf)
            view.addSubview_(iv)
            view.addSubview_(tf)
        view.textField().setStringValue_(self.row_name(row))
        img = self._icon_folder()
        if img is not None:
            view.imageView().setImage_(img)
        return view

    # ---------- 动作（给 bridge 转发） ----------

    def do_row_double(self) -> None:
        # 优先用 clickedRow，它直接对应被双击的那一行；
        # 某些情况下 selectedRow 还没更新，会导致双击没反应。
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

    def do_enter(self) -> None:
        self._enter_row(self._table.selectedRow())

    def _enter_row(self, row: int) -> None:
        children = self._children or []
        if not 0 <= row < len(children):
            return
        child = children[row]
        snapshot = (self.path, self._cid, self._children)
        self._stack.append(snapshot)
        self.path = _join(self.path, child["name"])
        self._cid = child["cid"]
        # 后台拉子目录，失败自动回退到原目录并提示
        self._load_children(
            async_=True,
            on_error=lambda e: self._nav_failed(snapshot, "无法打开", e),
        )

    def _nav_failed(self, snapshot: tuple[str, str, list[dict]], title: str, e: Exception) -> None:
        """导航（进入/新建）失败时回退到进入前的目录并弹提示。"""
        if self._closed:
            return
        self._restore(snapshot)
        self._alert(title, str(e))

    def _restore(self, snapshot: tuple[str, str, list[dict]]) -> None:
        self.path, self._cid, self._children = snapshot
        self._stack.pop()
        self._render(snapshot[2])

    def do_go_up(self) -> None:
        if not self._stack:
            return
        snapshot = self._stack.pop()
        self.path, self._cid, self._children = snapshot
        self._render(snapshot[2])  # 用缓存直接渲染，不发请求

    def do_refresh(self) -> None:
        if self._nav_busy():
            return
        self._load_children(async_=True, err_title="刷新失败")

    def do_new_folder(self) -> None:
        name = self._ask_name()
        if not name:
            return
        new_path = _join(self.path, name)
        try:
            new_cid = self.engine.create_dir(new_path)
        except Q115Error as e:
            self._alert("新建失败", str(e))
            return
        except Exception as e:  # noqa: BLE001
            log("[dirpicker] 新建目录异常:\n" + traceback.format_exc())
            self._alert("新建失败", f"{type(e).__name__}: {e}")
            return
        log(f"[dirpicker] 已新建 {new_path}（cid={new_cid}）")
        # 建好进入新目录，便于马上“选择此文件夹”
        snapshot = (self.path, self._cid, self._children)
        self._stack.append(snapshot)
        self.path = new_path
        self._cid = new_cid
        self._load_children(
            async_=True,
            on_error=lambda e: self._nav_failed(snapshot, "新建失败", e),
        )

    def _nav_busy(self) -> bool:
        """是否有目录加载在途（用于避免重复点击造成竞态）。"""
        return self._load_ticket != 0 and self._spinner is not None and not self._spinner.isHidden()

    def _ask_name(self) -> Optional[str]:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("新建子文件夹")
        alert.setInformativeText_(f"将在「{self._display()}」下创建，输入文件夹名称：")
        alert.addButtonWithTitle_("创建")
        alert.addButtonWithTitle_("取消")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 250, 24))
        alert.setAccessoryView_(field)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return None
        name = (field.stringValue() or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            self._alert("无法新建", "请输入一个不含 / 的文件夹名称，例如：美剧")
            return None
        return name

    def do_choose(self) -> None:
        self._result = (self.path, self._cid)
        NSApplication.sharedApplication().stopModalWithCode_(1)

    def do_cancel(self) -> None:
        self._result = None
        NSApplication.sharedApplication().stopModalWithCode_(0)

    # ---------- 工具 ----------

    def _alert(self, title: str, msg: str) -> None:
        a = NSAlert.alloc().init()
        a.setMessageText_(title)
        a.setInformativeText_(msg)
        a.addButtonWithTitle_("知道了")
        a.runModal()


class _DirPickerBridge(NSObject):
    """极简 NSObject 桥接层：只暴露 AppKit 需要的选择器并转发给控制器。

    注意：这里定义的每个方法都必须与 ObjC 选择器的参数个数一致，
    不要在这里写任何业务逻辑或带多余参数的辅助方法。
    """

    def numberOfRowsInTableView_(self, _tv) -> int:
        return self.owner.row_count()

    def tableView_objectValueForTableColumn_row_(self, _tv, _col, row: int) -> str:
        return self.owner.row_name(row)

    def tableView_viewForTableColumn_row_(self, tv, _col, row: int):
        return self.owner.cell_for_row(tv, row)

    def tableView_shouldEditTableColumn_row_(self, _tv, _col, _row) -> bool:
        return False

    def rowDouble_(self, _sender=None) -> None:
        self.owner.do_row_double()

    def dpRunCallback_(self, _obj=None) -> None:
        """后台线程把结果排回主线程后，由 run loop 调到这里统一执行。"""
        cbs = list(self.owner._pending_callbacks)
        self.owner._pending_callbacks.clear()
        for cb in cbs:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def enter_(self, _sender=None) -> None:
        self.owner.do_enter()

    def goUp_(self, _sender=None) -> None:
        self.owner.do_go_up()

    def refresh_(self, _sender=None) -> None:
        self.owner.do_refresh()

    def newFolder_(self, _sender=None) -> None:
        self.owner.do_new_folder()

    def choose_(self, _sender=None) -> None:
        self.owner.do_choose()

    def cancel_(self, _sender=None) -> None:
        self.owner.do_cancel()

    def windowShouldClose_(self, _sender=None) -> bool:
        # 点红色关闭按钮 = 取消选择。do_cancel 会 stopModalWithCode(0)
        # 正常结束模态会话，避免窗口关了 app 却卡在 modal 模式。
        # 返回 False 阻止系统默认关窗，由 do_cancel 统一收尾。
        try:
            self.owner.do_cancel()
        except Exception:  # noqa: BLE001
            pass
        return False

    def windowWillClose_(self, _notif=None) -> None:
        # 兜底：任何路径关窗都要结束模态会话，避免 app 卡在 modal 模式。
        try:
            if not self.owner._closed:
                NSApplication.sharedApplication().abortModal()
        except Exception:  # noqa: BLE001
            pass
