#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app_main.py — 115 秒转（菜单栏小工具）

用法：
  直接运行脚本（调试）：  python3 app_main.py
  打包为 .app：          python3 build_app.py （产物在 dist/115QuickTransfer.app）
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------------------
# 启动/崩溃诊断日志（写在依赖导入之前，这样连 import 失败都能被记录）
# ---------------------------------------------------------------------------


def _bootstrap_log(kind: str, msg: str = "") -> None:
    try:
        d = os.path.expanduser("~/Library/Application Support/115QuickTransfer")
        os.makedirs(d, exist_ok=True)
        log_path = os.path.join(d, "q115_launch.log")
        with open(log_path, "a", encoding="utf-8") as f:
            line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + kind
            if msg:
                line += "  " + msg
            f.write(line + "\n")
    except Exception:
        pass


_bootstrap_log("LAUNCH", f"pid={os.getpid()} args={sys.argv!r}")

_orig_excepthook = sys.excepthook


def _bootstrap_excepthook(exc_type, exc_value, tb):
    _bootstrap_log("FATAL", "".join(traceback.format_exception(exc_type, exc_value, tb)))
    _orig_excepthook(exc_type, exc_value, tb)


sys.excepthook = _bootstrap_excepthook

# ---------- 让打包后的 PyInstaller 环境也能正常找到资源 ----------
if getattr(sys, "frozen", False):
    BASE_DIR = Path(getattr(sys, "_MEIPASS"))
else:
    BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"

import rumps  # noqa: E402

from ui_login import LoginWindow  # noqa: E402
from q115_engine import (  # noqa: E402
    APP_DISPLAY,
    Q115DirNotFound,
    Q115Engine,
    Q115Error,
    Q115NotLoggedIn,
    extract_links,
    log,
)

ICON_FILE = ASSET_DIR / "iconTemplate.png"


# ---------------------------------------------------------------------------
# 版本号（单一来源：build_app.py 的 APP_VERSION）
# ---------------------------------------------------------------------------

def _read_app_version() -> str:
    """取运行时版本号，供菜单「关于」项显示。

    版本号唯一的定义处是 build_app.py 的 APP_VERSION。打包后该文件不在包里，
    转而读 .app 自己 Info.plist 里的 CFBundleShortVersionString（打包时由同一个
    APP_VERSION 写进去），保证两边永远一致。
    """
    # 1) 打包运行：读 .app/Contents/Info.plist
    if getattr(sys, "frozen", False):
        try:
            import plistlib

            plist = Path(sys.executable).resolve().parents[1] / "Info.plist"
            with open(plist, "rb") as f:
                v = plistlib.load(f).get("CFBundleShortVersionString")
            if v:
                return str(v)
        except Exception:  # noqa: BLE001
            pass
    # 2) 源码运行：从 build_app.py 里抠常量（不 import，避免连带拉起打包依赖）
    try:
        import re

        txt = (BASE_DIR / "build_app.py").read_text(encoding="utf-8")
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', txt, re.M)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "dev"


APP_VERSION = _read_app_version()


# ---------------------------------------------------------------------------
# 菜单文案（单一来源，别在多处手写同一个字符串）
# ---------------------------------------------------------------------------
# 关于「…」的用法，定成一条规则，别再混着来：
#   · 【静态的动作型标题】不带尾部省略号。它会被误读成"后面还有内容 / 被截断了"，
#     而菜单内容本来就是完整的 —— 用户 2026-09-14 就是这么报的（「登录 / 切换账号
#     （手机扫码）…」，那个「…」是我们自己写进字符串的，不是系统截断）。
#   · 【进行中的状态文案】保留省略号，它表达的是"还在继续"，
#     如「正在获取二维码…」「正在提交云下载任务…」「（等待中…）」。
LOGIN_LABEL = "登录 / 切换账号（手机扫码）"
LOGIN_WAITING_LABEL = "等待手机扫码确认…"   # 进行中 → 保留省略号


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def clipboard_text() -> str:
    """读取系统剪贴板文本。"""
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        return pb.stringForType_(NSPasteboardTypeString) or ""
    except Exception:
        return ""


def show_qr_image(png_path: str) -> None:
    """用系统「预览」打开二维码图片供手机扫码。"""
    try:
        subprocess.Popen(["open", png_path])
    except Exception:
        pass


def notify(title: str, msg: str) -> None:
    try:
        rumps.notification(title=title, subtitle="", message=msg, sound=False)
    except Exception:
        pass


def _make_symbol_status_icon(symbol: str = "icloud.and.arrow.down.fill", base: int = 18):
    """【兜底】用系统 SF Symbol 现场画一张多分辨率菜单栏模板图标。

    正常路径见下面的 _make_status_icon：图标已经换成自定义图形（磁铁 + 下载
    箭头 + 115 文件夹），只能从位图来。只有在 assets 里的图标文件缺失时
    （例如手工删了、或打包漏了 add-data），才退回这里保证菜单栏图标不消失。
    """
    try:
        from AppKit import (
            NSBezierPath,
            NSBitmapImageRep,
            NSColor,
            NSGraphicsContext,
            NSImage,
            NSMakeRect,
        )
    except Exception:  # noqa: BLE001
        return None

    sym = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "115 秒转")
    if sym is None:
        return None

    img = NSImage.alloc().initWithSize_((base, base))
    for scale in (1, 2, 3):
        px = base * scale
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, px, px, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
        # 先在 1:1 像素坐标系下画符号，避免 SF Symbol 在缩放上下文里走位
        rep.setSize_((px, px))
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, px, px))
        # 按符号自身宽高比居中绘制（长边 = 画布 85%）。
        # 不能把方形 rect 硬塞给 drawInRect_：那会把 18×17 的云朵图标纵向拉长，
        # 菜单栏上看起来就是一个被捏过形状的畸变图标。
        inset = px * 0.075
        box = px - inset * 2
        sw, sh = sym.size()
        if sw > 0 and sh > 0:
            if sw >= sh:
                dw, dh = box, box * sh / sw
            else:
                dw, dh = box * sw / sh, box
        else:
            dw = dh = box
        sym.drawInRect_(NSMakeRect((px - dw) / 2.0, (px - dh) / 2.0, dw, dh))
        NSGraphicsContext.restoreGraphicsState()
        # 再告诉 AppKit 这张位图对应 base×base 点（scale=1/2/3）
        rep.setSize_((base, base))
        img.addRepresentation_(rep)
    img.setTemplate_(True)
    return img


def _make_status_icon(base: int = 18):
    """加载 assets 里预渲染好的多分辨率菜单栏模板图标。

    图标是自定义图形（见 make_icons.py），所以只能在构建时用图像库渲染成
    1x/2x/3x 三张位图，运行时交给 AppKit 按屏幕 DPI 自选 —— Retina 上取 @2x/3x
    不会被拉伸变糊。

    位图由 make_icons.py 用 LANCZOS 预缩放，比让 AppKit 临时缩放更锐利；
    缩放时必须先预乘 alpha，否则边缘会出现一圈浅色光晕。

    三张都不在时退回 SF Symbol（_make_symbol_status_icon），保证菜单栏图标
    不会凭空消失。
    """
    try:
        from AppKit import NSImage
    except Exception:  # noqa: BLE001
        return None

    img = NSImage.alloc().initWithSize_((base, base))
    loaded = 0
    for scale in (1, 2, 3):
        name = "iconTemplate.png" if scale == 1 else f"iconTemplate@{scale}x.png"
        path = ASSET_DIR / name
        if not path.exists():
            continue
        # 读 PNG 必须走 NSImage：NSBitmapImageRep 没有
        # initWithContentsOfFile:（踩过 —— 报 AttributeError，图标直接不显示）。
        # 再由 NSImage 取出它已经解好的位图 rep，逐个改 size 后装进合成图。
        try:
            src = NSImage.alloc().initWithContentsOfFile_(str(path))
            reps = list(src.representations()) if src is not None else []
        except Exception:  # noqa: BLE001
            continue
        # 单个文件出问题只跳过它 —— 不能因为一张图坏了就整个图标消失
        for rep in reps:
            # 位图像素是 base×scale，但要告诉 AppKit「这张图代表 base×base 点」
            rep.setSize_((base, base))
            img.addRepresentation_(rep)
            loaded += 1

    if loaded == 0:
        _bootstrap_log("ICON", "assets 图标缺失，回退 SF Symbol")
        return _make_symbol_status_icon(base=base)

    img.setTemplate_(True)   # 模板模式：菜单栏自动适配深浅色
    return img


def _apply_status_icon(app) -> None:
    """把高清多分辨率图标写入 rumps 将在运行时使用的 _icon_nsimage。

    注意：rumps 在 `App.run()` 里才创建 `_nsapp` 并调用 `initializeStatusBar()`，
    此时它会读取 `self.__dict__['_icon_nsimage']`。所以我们在 `__init__` 里直接
    替换这个值，就能让 rumps 在初始化状态栏时用上高清图标。
    """
    try:
        img = _make_status_icon()
        if img is None:
            return
        app._icon_nsimage = img
        _bootstrap_log("ICON", "multi-res template icon applied")
    except Exception as e:  # noqa: BLE001
        _bootstrap_log("ICON", f"apply failed: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 主菜单（应用菜单 + 编辑菜单）
# ---------------------------------------------------------------------------

def _install_main_menu(quit_target=None) -> None:
    """给应用装一个最小主菜单：应用菜单 + 编辑菜单。

    不是可有可无的装饰 —— macOS 上 Cmd+C / Cmd+V / Cmd+A 这类组合键是由
    【主菜单里的快捷键】路由的，不是文本框自带的能力。rumps 只把菜单挂在
    状态栏按钮上，从不设 NSApplication 的主菜单，于是这个 app 的输入框里
    Cmd+V 一直是哑的。（实测：装上「编辑」菜单后，同一个按键事件就能正常粘贴。）

    quit_target：接收「退出」动作的对象，需实现 menuQuit:；传 None 则让该动作
    走响应链（退化成 AppKit 默认行为）。
    """
    try:
        from AppKit import (  # noqa: PLC0415
            NSApplication,
            NSMenu,
            NSMenuItem,
            NSEventModifierFlagCommand,
            NSEventModifierFlagOption,
        )
    except Exception:  # noqa: BLE001
        return
    try:
        main = NSMenu.alloc().init()

        # --- 应用菜单（macOS 会把这一项显示成应用名，标题其实是被忽略的）---
        app_item = NSMenuItem.alloc().init()
        app_item.setTitle_(APP_DISPLAY)
        main.addItem_(app_item)

        app_menu = NSMenu.alloc().init()
        app_menu.addItemWithTitle_action_keyEquivalent_(
            "隐藏 " + APP_DISPLAY, "hide:", "h")
        hide_others = app_menu.addItemWithTitle_action_keyEquivalent_(
            "隐藏其他", "hideOtherApplications:", "h")
        hide_others.setKeyEquivalentModifierMask_(
            NSEventModifierFlagCommand | NSEventModifierFlagOption)
        app_menu.addItemWithTitle_action_keyEquivalent_(
            "显示全部", "unhideAllApplications:", "")
        app_menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出 " + APP_DISPLAY, "menuQuit:", "q")
        if quit_target is not None:
            quit_item.setTarget_(quit_target)   # 走和状态栏「退出」同一条清理路径
        app_menu.addItem_(quit_item)
        app_item.setSubmenu_(app_menu)

        # --- 编辑菜单：上面那些快捷键全指望着它 ---
        edit_item = NSMenuItem.alloc().init()
        edit_item.setTitle_("编辑")
        main.addItem_(edit_item)

        edit_menu = NSMenu.alloc().initWithTitle_("编辑")
        for title, selector, key in (
            ("撤销", "undo:", "z"),
            ("重做", "redo:", "Z"),
            ("剪切", "cut:", "x"),
            ("拷贝", "copy:", "c"),
            ("粘贴", "paste:", "v"),
            ("全选", "selectAll:", "a"),
        ):
            # target 留空 = 沿响应链派发，谁在前台就发给谁（输入框在前台就是它）
            edit_menu.addItemWithTitle_action_keyEquivalent_(title, selector, key)
        edit_menu.insertItem_atIndex_(NSMenuItem.separatorItem(), 2)  # 「重做」后加分隔线
        edit_item.setSubmenu_(edit_menu)

        NSApplication.sharedApplication().setMainMenu_(main)
        _bootstrap_log("MENU", "main menu installed (app + edit)")
    except Exception as e:  # noqa: BLE001
        _bootstrap_log("MENU", f"install failed: {type(e).__name__}: {e}")



# ---------------------------------------------------------------------------
# 后台线程 → 主线程投递桥
# ---------------------------------------------------------------------------

from Foundation import NSObject as _NSObject  # noqa: E402


class _MainBridge(_NSObject):
    """后台线程干完活后，用它把「该刷新 UI 了」这件事投回主线程。

    为什么必须这么做：AppKit 的一切视图/菜单操作都只能在主线程做，而网络请求
    一旦放在主线程（以前就是这么写的）就会把 runloop 堵住 —— 菜单点不动、鼠标
    转圈、连退出都点不了。所以改成「后台请求 + 回主线程刷 UI」。
    用 performSelectorOnMainThread 而不是 NSTimer：它不依赖常驻定时器，
    app.run() 之前调用也能排进主 runloop。
    """

    def uiRefresh_(self, _x=None) -> None:
        o = getattr(self, "owner", None)
        if o is not None:
            o._apply_ui_refresh()


# ---------------------------------------------------------------------------
# 菜单栏应用
# ---------------------------------------------------------------------------

class Q115App(rumps.App):
    def __init__(self) -> None:
        self.engine = Q115Engine()
        self._busy = False
        self._login_waiting = False
        self._poll_timer = None
        self._notes: deque = deque()  # 后台线程产生、主线程定时器弹出的通知
        self._login_win: LoginWindow | None = None

        # --- 后台任务的「在途 / 结果」槽位 ---
        # 约定：只有主线程读写这些标志；后台线程只写结果并投递一次 uiRefresh:。
        self._bridge = _MainBridge.alloc().init()
        self._bridge.owner = self
        self._qr_busy = False          # 正在后台取二维码
        self._qr_state = None          # 后台取到的 _LoginState
        self._qr_err = ""              # 后台取码失败原因
        self._login_busy = False       # 正在后台轮询扫码状态
        self._login_busy_since = 0.0   # 该次轮询开始时间（看门狗用）
        self._login_result = None      # 后台轮询回来的状态字符串
        self._finish_busy = False      # 正在后台完成登录（换 Cookie）
        self._finish_done = False      # 后台登录完成，结果待主线程消费
        self._finish_err = ""
        self._finish_name = ""
        self._auth_pending = False     # 正在后台查登录态
        self._auth_result = (False, "")

        self.mi_transfer = rumps.MenuItem("转存剪贴板里的链接", callback=self.on_transfer)
        self.mi_status = rumps.MenuItem("状态：检查中", callback=self.on_refresh_status)
        self.mi_login = rumps.MenuItem(LOGIN_LABEL, callback=self.on_login)
        # 「关于」项不带版本号 —— 菜单栏位置窄，版本号放在点开后的弹窗里显示。
        self.mi_about = rumps.MenuItem(f"关于 {APP_DISPLAY}", callback=self.on_about)
        self.mi_quit = rumps.MenuItem("退出 " + APP_DISPLAY, callback=self.on_quit)

        super().__init__(
            name=APP_DISPLAY,
            icon=None,        # 高清图标通过 _apply_status_icon 在运行时多分辨率渲染
            template=True,    # 模板模式：菜单栏自动适配深浅色
            menu=[
                self.mi_transfer,
                None,
                self.mi_status,
                self.mi_login,
                None,
                self.mi_about,
                self.mi_quit,
            ],
            quit_button=None,
        )
        # rumps 在 run() 中初始化状态栏时会读取 self._icon_nsimage，
        # 这里替换成 assets 里 1x/2x/3x 三种分辨率的自定义图标。
        _apply_status_icon(self)
        # 没有主菜单 = 输入框里 Cmd+C/V/A 全部失灵（见 _install_main_menu 注释）。
        # 必须在 app.run() 之前装：AppKit 只在 mainMenu 为空时才造默认菜单，
        # 先装上的话会被原样保留。
        _install_main_menu(self)

    # ---------- 小工具 ----------

    def ensure_poll_timer(self) -> None:
        if self._poll_timer is None:
            self._poll_timer = rumps.Timer(self._on_tick, 0.5)
            self._poll_timer.start()

    def update_ui(self) -> None:
        """刷新「状态」菜单文案。

        校验登录态要打网络接口，绝不能放在主线程 —— 否则菜单打开、点击都会
        连带卡住（以前就是这个毛病）。这里只派发后台任务，取到结果后由
        _apply_ui_refresh() 在主线程写标题。
        """
        if self._auth_pending:
            return
        self._auth_pending = True
        threading.Thread(target=self._auth_worker, daemon=True).start()

    def _auth_worker(self) -> None:
        try:
            ok, name = self.engine.account_info()
        except Exception:  # noqa: BLE001
            ok, name = False, ""
        self._auth_result = (ok, name)
        self._notify_main()

    def _apply_ui_refresh(self) -> None:
        """在主线程把后台拿到的登录态写进菜单（见 _main_thread_bridge_cls）。"""
        if self._auth_pending:
            self._auth_pending = False
            # 登录流程正在主导状态栏文案时不要抢（否则「正在获取二维码…」
            # 会被上一次状态刷新覆盖成「未登录」）。
            if self._qr_busy or self._login_waiting or self._finish_busy:
                self._apply_pending_login_ui()
            else:
                ok, name = self._auth_result
                label = "状态：已登录" if ok else "状态：未登录"
                if ok and name:
                    label += f"（{name}）"
                self.mi_status.title = label
        self._apply_pending_login_ui()

    def _notify_main(self) -> None:
        """从任意线程请求一次主线程刷新（线程安全）。"""
        try:
            self._bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
                "uiRefresh:", None, False)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 登录 ----------

    def on_login(self, _sender=None) -> None:
        if self._login_waiting:
            win = self._login_win
            if win is not None and win.is_visible():
                # 正在等待扫码且窗口还在：唤到前台就好，别重复取码。
                win.bring_to_front()
                return
            # 窗口已经不在了（例如建窗失败走了「预览」回退，或者已被关掉）：
            # 不能一直卡在「等待中」不放 —— 允许重新走一遍取码流程。
            self._login_waiting = False
        self._start_qr()

    def _start_qr(self) -> None:
        """取一张新二维码并在原生登录窗口里显示。

        扫码过期/重试时也走这里 —— 窗口复用，只换图重画，不重新弹窗。

        取码要连打两个网络接口（token + 二维码图），绝不能放在主线程：
        以前点菜单「登录」会整界面顿住几秒，就是这么来的。这里只派发后台任务。
        """
        if self._qr_busy or self._finish_busy:
            return
        if self._login_waiting and self._login_win is None:
            return
        self._qr_busy = True
        self.mi_status.title = "状态：正在获取二维码…"
        threading.Thread(target=self._qr_worker, daemon=True).start()

    def _qr_worker(self) -> None:
        try:
            self._qr_state = self.engine.start_login()
            self._qr_err = ""
        except Q115Error as e:
            self._qr_state, self._qr_err = None, str(e)
        except Exception as e:  # noqa: BLE001
            self._qr_state, self._qr_err = None, f"获取二维码失败：{e}"
        self._notify_main()

    def _on_qr_ready(self, state, err: str) -> None:
        """主线程：拿到二维码后在原生窗口里展示。"""
        if err or state is None:
            self.update_ui()
            rumps.alert(title="无法登录",
                        message=err or "获取二维码失败，请稍后重试", ok="知道了")
            return

        self._login_waiting = True
        self._login_result = None
        self.mi_status.title = "状态：请用手机 115 扫码（等待中…）"
        self.mi_login.title = LOGIN_WAITING_LABEL
        self.ensure_poll_timer()

        try:
            win = self._login_win
            if win is None:
                win = LoginWindow(state.qr_png)
                win.set_on_close(self._on_login_window_closed)
                self._login_win = win
            elif win.is_visible():
                # 复用已有窗口：只换一张新二维码，不重建、不闪。
                win.set_qr(state.qr_png)
            # 【顺序不能反】show() 内部才执行 _build()，之前窗口只是空壳，
            # 此时 _status_text 等控件还是 None。旧代码先调 waiting() 再 show()，
            # 结果 waiting() 直接 AttributeError，登录窗口压根没建出来，却已经把
            # _login_waiting 置为 True 并开始轮询 —— 用户看到的就是「点了登录没窗口、
            # 然后整个软件卡住转圈」。所以务必 show 在前、waiting 在后。
            win.show(on_retry=self._start_qr)
            win.waiting()
        except Exception:  # noqa: BLE001
            # UI 层出问题不能把登录这个功能弄丢 —— 回退到用「预览」看图
            log("[login] 原生登录窗不可用，回退预览打开: " + traceback.format_exc())
            self._login_win = None      # 丢掉这个没建起来的空壳，别让它挡住重试
            show_qr_image(state.qr_png)
        notify(APP_DISPLAY, "请用手机「115」App 扫码并确认")

    def notify_main(self, title: str, msg: str) -> None:
        """跨线程安全：把通知排队，由主线程定时器统一弹出。"""
        self._notes.append((title, msg))

    def _drain_notes(self) -> None:
        try:
            while self._notes:
                title, msg = self._notes.popleft()
                notify(title, msg)
        except Exception:
            pass

    def _apply_pending_login_ui(self) -> None:
        """主线程：消费所有在途后台结果（取码 / 扫码状态 / 登录完成）。

        与 _apply_ui_refresh 的分工：那个管「登录态文案」，这个管「登录流程」。
        每次只处理一类，处理完由各分支自行决定要不要再刷一次 UI。
        """
        # ① 二维码取好了
        if self._qr_state is not None or self._qr_err:
            state, err = self._qr_state, self._qr_err
            self._qr_state, self._qr_err = None, ""
            self._qr_busy = False
            self._on_qr_ready(state, err)

        # ② 登录完成（换 Cookie 回来了）
        if self._finish_done:
            self._finish_done = False
            err, name = self._finish_err, self._finish_name
            self._finish_err, self._finish_name = "", ""
            self._finish_busy = False
            self._on_login_finished(err, name)

        # ③ 扫码状态轮询结果
        res = self._login_result
        if res is not None:
            self._login_result = None
            if self._login_waiting:
                self._handle_login_status(res)

    def _on_tick(self, _sender=None) -> None:
        """主线程定时器：只做「起后台轮询 + 弹排队通知」。

        【注意】这里绝对不能出现网络调用。以前 poll_login() 就直接写在这一行，
        每 0.5 秒主线程被一次网络往返占住；网络稍慢 → 菜单点不动、鼠标转圈、
        连退出都点不了（用户报的「关掉二维码窗口后就卡住」）。现在请求在后台线程，
        这里只负责起任务和消费结果。
        """
        self._drain_notes()
        if not self._login_waiting:
            return
        if self._login_busy:
            # 看门狗：单次轮询超过 12s 视为请求挂死，解除占用允许重开，
            # 否则后台线程若永久卡在网络里，扫码状态就再也不会更新了。
            if time.time() - self._login_busy_since > 12.0:
                self._login_busy = False
            else:
                return
        self._login_busy = True
        self._login_busy_since = time.time()
        threading.Thread(target=self._poll_worker, daemon=True).start()

    def _poll_worker(self) -> None:
        try:
            status = self.engine.poll_login()
        except Exception:  # noqa: BLE001
            status = "waiting"
        self._login_result = status
        self._login_busy = False
        self._notify_main()

    def _handle_login_status(self, status: str) -> None:
        win = self._login_win
        if status == "waiting":
            return
        if status == "scanned":
            self.mi_status.title = "状态：已扫码，请在手机上确认"
            if win is not None:
                win.mark_scanned()
            return
        if status == "ok":
            # 换 Cookie 也是网络请求，同样丢给后台（见 _finish_worker）。
            self._login_waiting = False
            self._finish_busy = True
            self.mi_status.title = "状态：正在完成登录…"
            threading.Thread(target=self._finish_worker, daemon=True).start()
            return
        # expired / canceled
        self._login_waiting = False
        self.mi_login.title = LOGIN_LABEL
        self.update_ui()
        if status == "expired":
            if win is not None:
                win.fail("二维码已过期，点下方按钮重新获取")
            notify(APP_DISPLAY, "二维码已过期，请在登录窗口点「重新获取」")
        else:
            if win is not None:
                win.fail("已取消登录")
            self._login_win = None
            notify(APP_DISPLAY, "已取消登录")

    def _finish_worker(self) -> None:
        try:
            self.engine.finish_login()
            name = ""
            try:
                _, name = self.engine.account_info()
            except Exception:  # noqa: BLE001
                pass
            self._finish_err, self._finish_name = "", name
        except Q115Error as e:
            self._finish_err, self._finish_name = str(e), ""
        except Exception as e:  # noqa: BLE001
            self._finish_err, self._finish_name = f"登录确认失败（{e}）", ""
        self._finish_done = True
        self._notify_main()

    def _on_login_finished(self, err: str, name: str) -> None:
        win = self._login_win
        self.mi_login.title = LOGIN_LABEL
        if err:
            self.update_ui()
            # 失败原因在登录窗口里就地说清楚，不再额外弹一个 Alert
            if win is not None:
                win.fail(f"登录失败：{err}")
            else:
                rumps.alert(title="登录失败", message=err, ok="知道了")
            self._login_win = None
            return
        self.update_ui()
        if win is not None:
            win.succeed(name)   # 窗口自己对勾、1.1s 后自动关闭
        self._login_win = None
        notify(APP_DISPLAY, "登录成功，现在可以转存链接了")

    def _on_login_window_closed(self) -> None:
        """用户手动关闭二维码窗口：立即清理引用、停止轮询、恢复菜单文案。"""
        self._login_waiting = False
        self._login_win = None
        # 丢掉在途的轮询结果，否则关窗后还会冒一条「二维码已过期」通知
        self._login_result = None
        self._login_busy = False
        self.mi_login.title = LOGIN_LABEL
        self.update_ui()

    def on_logout(self) -> None:
        self.engine.logout()
        self.update_ui()

    # ---------- 转存 ----------

    def _pick_save_folder(self, start: str = "/") -> tuple[str, str | None] | None:
        """选择本次保存目录，返回 (路径, 目录id)。优先弹可视化目录选择窗口（原生 AppKit）；
        万一可视化窗口在运行环境里不可用，自动回退到文字交互模式，保证功能可用。"""
        try:
            from dir_picker import pick_save_folder

            return pick_save_folder(self.engine, start)
        except Exception as e:  # noqa: BLE001
            log("[picker] 可视化选择不可用，回退文字模式: " + traceback.format_exc())
            return self._pick_save_folder_text(start)

    def _pick_save_folder_text(self, start: str = "/") -> tuple[str, str | None] | None:
        """（回退方案）文字交互式选择目录：可逐级进入子文件夹、返回上级、新建子文件夹、
        或直接输入完整路径。返回 (路径, None)；用户取消返回 None。"""
        path = (start or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path

        def join(base: str, name: str) -> str:
            return ("/" + name) if base == "/" else base.rstrip("/") + "/" + name

        while True:
            try:
                cid = self.engine.resolve_dir_id(path)
            except Q115DirNotFound:
                if path != "/":
                    path = "/"  # 之前的目录找不到了，退回根目录
                    continue
                rumps.alert(title="目录读取失败", message="根目录也读不到，请检查网络后重试。", ok="知道了")
                return None
            except Q115Error as e:
                rumps.alert(title="读取目录失败", message=str(e), ok="知道了")
                return None
            try:
                children = self.engine.list_dir_children(cid)
            except Q115Error as e:
                rumps.alert(title="读取目录失败", message=str(e), ok="知道了")
                return None

            display = "根目录 /" if path == "/" else path
            lines: list[str] = []
            if children:
                for i, ch in enumerate(children[:15], 1):
                    name = ch["name"]
                    if len(name) > 40:
                        name = name[:40] + "…"
                    lines.append(f"{i:>2}. {name}")
                if len(children) > 15:
                    lines.append(f"… 共 {len(children)} 个子文件夹（可直接输入完整路径跳转）")
            else:
                lines.append("（此目录下暂无子文件夹）")
            msg = (
                f"当前目录：{display}\n"
                "\n"
                "操作：输入编号=进入子文件夹；..=返回上级；\n"
                "+名称=在当前目录下新建子文件夹；/路径=直接跳转；\n"
                "留空或输入 0 = 就保存在当前目录。\n"
                "\n"
                + "\n".join(lines)
            )
            resp = rumps.Window(
                message=msg,
                title="本次保存到…",
                default_text="0",
                ok="确定", cancel="取消",
                dimensions=(440, 160),
            ).run()
            if resp.clicked != 1:
                return None
            raw = (resp.text or "").strip()
            if raw in ("", "0"):
                return path, None  # 就保存在当前目录
            if raw == "..":
                if path != "/":
                    path = path.rstrip("/").rsplit("/", 1)[0] or "/"
                continue
            if raw and raw[0] in ("+", "＋"):
                name = raw[1:].strip()
                if not name or "/" in name or "\\" in name or name in (".", ".."):
                    rumps.alert(title="无法新建", message="请在 + 后输入不含 / 的文件夹名，例如：+美剧", ok="知道了")
                    continue
                new_path = join(path, name)
                try:
                    self.engine.create_dir(new_path)
                except Q115Error as e:
                    rumps.alert(title="新建失败", message=str(e), ok="知道了")
                    continue
                path = new_path
                notify(APP_DISPLAY, f"已新建文件夹：{new_path}\n再次点「确定」即可把资源转存到这里")
                continue
            if raw.startswith("/"):
                try:
                    self.engine.resolve_dir_id(raw)
                except Q115DirNotFound:
                    ans = rumps.alert(
                        title="目录不存在",
                        message=f"网盘中找不到：{raw}\n是否自动创建该目录？",
                        ok="创建并进入", cancel="取消",
                    )
                    if not ans:
                        continue
                    try:
                        self.engine.create_dir(raw)
                    except Q115Error as e:
                        rumps.alert(title="创建失败", message=str(e), ok="知道了")
                        continue
                except Q115Error as e:
                    rumps.alert(title="读取失败", message=str(e), ok="知道了")
                    continue
                path = raw
                continue
            if raw.isdigit():
                idx = int(raw)
                if 1 <= idx <= len(children):
                    path = join(path, children[idx - 1]["name"])
                    continue
                rumps.alert(title="编号超出范围", message=f"请输入 1～{len(children)} 之间的编号。", ok="知道了")
                continue
            # 直接输入了子文件夹名
            hit = next((c for c in children if c["name"] == raw), None)
            if hit:
                path = join(path, hit["name"])
                continue
            rumps.alert(
                title="无法识别",
                message="请输入：子文件夹编号 / .. / +名称 / /完整路径，或留空点确定。",
                ok="知道了",
            )

    def on_transfer(self, _sender=None) -> None:
        if self._busy:
            return
        if not self.engine.is_logged_in():
            rumps.alert(
                title="尚未登录",
                message=f"请先点击「{LOGIN_LABEL}」，用手机 115 扫码登录。",
                ok="知道了",
            )
            return
        clip = clipboard_text()
        # 原生主窗口里完成了：编辑链接 → 挑目录 → 提交 → 结果动画，
        # 这里只负责记住本次目录并发一条通知。
        result = self._run_main_panel(clip)
        if result is None:
            return  # 用户取消；或已走回退流程（它自己会通知）
        links, folder, _cid = result
        if folder != "/":
            self.engine.cfg.last_path = folder  # 记住本次目录，下次转存从这里开始
        shown = "根目录" if folder == "/" else folder
        log(f"[transfer] 本次保存到：{folder}，共 {len(links)} 条")
        notify(APP_DISPLAY, f"已提交 {len(links)} 条云下载任务 → {shown}")

    def _run_main_panel(self, clip: str):
        """优先用原生主窗口；UI 层出任何问题都自动退回旧的文字流程。"""
        try:
            from ui_transfer import run_transfer_panel

            return run_transfer_panel(self.engine, clip,
                                      start=self.engine.cfg.last_path)
        except Exception:  # noqa: BLE001
            log("[transfer] 原生主窗口不可用，回退旧流程:\n" + traceback.format_exc())
            return self._legacy_flow(clip)

    def _legacy_flow(self, clip: str):
        """（回退方案）原来的两段式：一个输入框 + 一个目录选择窗 + 后台提交。"""
        resp = rumps.Window(
            message="已自动读取剪贴板，可修改。\n点「下一步」后还可选择本次保存到的文件夹（支持新建子文件夹）。",
            title="转存到 115",
            default_text=clip,
            ok="下一步", cancel="取消",
            dimensions=(460, 200),
        ).run()
        if resp.clicked != 1:
            return None
        text = (resp.text or "").strip()
        links = extract_links(text)
        log(f"[transfer:legacy] 识别到 {len(links)} 条链接")
        if not links:
            log("[transfer:legacy] 未识别到链接，原文前 200 字: " + repr(text[:200]))
            rumps.alert(
                title="没有可用的链接",
                message="未识别到磁力 / ed2k / http / ftp 链接。\n"
                        "请确认每一行是一条完整的链接（以 magnet:?xt= 或 ed2k:// 等开头）。",
                ok="知道了",
            )
            return None
        picked = self._pick_save_folder(start=self.engine.cfg.last_path)
        if picked is None:
            log("[transfer:legacy] 用户取消了文件夹选择，未提交")
            return None
        folder, cid = picked
        if folder != "/":
            self.engine.cfg.last_path = folder
        self._busy = True
        self.mi_transfer.title = "正在提交云下载任务…"
        threading.Thread(target=self._submit_worker, args=(links, folder, cid), daemon=True).start()
        self.ensure_poll_timer()
        return None

    def _submit_worker(self, links: list[str], folder: str, cid: str | None = None) -> None:
        error: str | None = None
        resp = None
        try:
            dir_id = cid or self.engine.resolve_dir_id(folder)
            resp = self.engine.submit_links(links, dir_id=dir_id)
        except Q115NotLoggedIn as e:
            error = str(e)
        except Q115Error as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            log("[transfer] 未捕获异常:\n" + traceback.format_exc())
        finally:
            self._submit_done(links, folder, error, resp)

    def _submit_done(self, links: list[str], folder: str, error: str | None, resp=None) -> None:
        self._busy = False
        self.mi_transfer.title = "转存剪贴板里的链接"
        if error:
            log(f"[transfer] 提交失败: {error}")
            self.notify_main(APP_DISPLAY, "提交失败：" + error)
            return
        shown = "根目录" if folder == "/" else folder
        ok_count = len(links)
        # 尽量从接口响应里提取每个链接的结果
        try:
            result = (resp or {}).get("data", {}).get("result") or []
            ok_count = sum(1 for r in result if r.get("state") in (True, 1))
            bad = [r for r in result if r.get("state") not in (True, 1)]
            if bad and not error:
                first = bad[0]
                error = str(first.get("errtype") or first.get("error_msg") or first.get("message") or first)
        except Exception:
            pass
        log(f"[transfer] 提交成功: {ok_count}/{len(links)} 条 → {folder}")
        if error:
            self.notify_main(APP_DISPLAY, f"{ok_count}/{len(links)} 条成功，部分失败：" + error)
        else:
            self.notify_main(
                APP_DISPLAY,
                f"已提交 {ok_count} 条云下载任务 → {shown}\n下载完成后会出现在 115 网盘对应目录",
            )

    # ---------- 任务 / 其他 ----------

    def on_refresh_status(self, _sender=None) -> None:
        self.update_ui()

    def on_about(self, _sender=None) -> None:
        """「关于」菜单：显示当前运行的版本号。"""
        try:
            rumps.alert(
                title=f"{APP_DISPLAY}  v{APP_VERSION}",
                message=(
                    f"版本：{APP_VERSION}\n\n"
                    "把磁力 / ed2k / http 链接直接交给 115 离线下载的菜单栏小工具。\n"
                    "github.com/liteng0401/115-quick-transfer"
                ),
                ok="好的",
            )
        except Exception:  # noqa: BLE001
            log("[about] 弹窗失败: " + traceback.format_exc())

    def menuQuit_(self, _sender=None) -> None:
        """主菜单「退出 115 秒转」/ Cmd+Q 的入口。

        刻意复用 on_quit：里面的 abortModal、二维码临时文件清理一个都不能少，
        否则又会出现「退出后打不开」那一类问题。
        """
        self.on_quit(_sender)

    def on_quit(self, _sender=None) -> None:
        # 如果还有模态窗口（如目录选择/转存面板），先把它结束掉，
        # 否则 terminate_ 可能被阻塞，导致进程不能完全退出、下次打不开。
        try:
            NSApplication = __import__("AppKit", fromlist=["NSApplication"]).NSApplication
            NSApplication.sharedApplication().abortModal()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._login_win is not None:
                self._login_win.close()
        except Exception:  # noqa: BLE001
            pass
        # 清理登录窗口产生的临时二维码图片
        try:
            st = getattr(self.engine, "_login", None)
            if st is not None:
                png = getattr(st, "qr_png", None)
                if png:
                    os.unlink(png)
        except Exception:  # noqa: BLE001
            pass
        _bootstrap_log("QUIT", f"pid={os.getpid()}")
        rumps.quit_application()


def main() -> None:
    app = Q115App()
    # 刷新一次登录状态。注意 update_ui() 现在只派发后台任务，不阻塞启动 ——
    # 以前这里是同步网络请求，网络不好时菜单栏图标要等好几秒才出现。
    try:
        app.update_ui()
        log("[app] 启动完成：登录状态刷新已派发")
    except Exception:
        log("[app] 启动刷新异常:\n" + traceback.format_exc())
    app.run()


if __name__ == "__main__":
    main()
