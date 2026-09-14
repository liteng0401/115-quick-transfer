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


def _make_status_icon(symbol: str = "icloud.and.arrow.down.fill", base: int = 18):
    """从 SF Symbol 生成一张多分辨率（1x/2x/3x）的菜单栏模板图标。

    macOS 状态栏会在不同 DPI 的屏幕上自动挑选合适的 NSBitmapImageRep，
    这样 Retina 屏上就不再是 1x PNG 被硬拉上去后的模糊效果。
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

        self.mi_transfer = rumps.MenuItem("转存剪贴板里的链接", callback=self.on_transfer)
        self.mi_status = rumps.MenuItem("状态：检查中", callback=self.on_refresh_status)
        self.mi_login = rumps.MenuItem("登录 / 切换账号（手机扫码）…", callback=self.on_login)
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
                self.mi_quit,
            ],
            quit_button=None,
        )
        # rumps 在 run() 中初始化状态栏时会读取 self._icon_nsimage，
        # 这里把 PNG fallback 替换成 1x/2x/3x 的矢量符号渲染图。
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
        ok, name = self.engine.account_info()
        if ok:
            label = "状态：已登录"
            if name:
                label += f"（{name}）"
        else:
            label = "状态：未登录"
        self.mi_status.title = label

    # ---------- 登录 ----------

    def on_login(self, _sender=None) -> None:
        if self._login_waiting:
            return
        self._start_qr()

    def _start_qr(self) -> None:
        """取一张新二维码并在原生登录窗口里显示。

        扫码过期/重试时也走这里 —— 窗口复用，只换图重画，不重新弹窗。
        """
        if self._login_waiting and self._login_win is None:
            return
        try:
            state = self.engine.start_login()
        except Q115Error as e:
            rumps.alert(title="无法登录", message=str(e), ok="知道了")
            return

        self._login_waiting = True
        self.mi_status.title = "状态：请用手机 115 扫码（等待中…）"
        self.mi_login.title = "等待手机扫码确认…"
        self.ensure_poll_timer()

        try:
            if self._login_win is None:
                self._login_win = LoginWindow(state.qr_png)
                self._login_win.set_on_close(self._on_login_window_closed)
            if self._login_win.is_visible():
                self._login_win.set_qr(state.qr_png)
            self._login_win.waiting()
            self._login_win.show(on_retry=self._start_qr)
        except Exception:  # noqa: BLE001
            # UI 层出问题不能把登录这个功能弄丢 —— 回退到用「预览」看图
            log("[login] 原生登录窗不可用，回退预览打开: " + traceback.format_exc())
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

    def _on_tick(self, _sender=None) -> None:
        """主线程定时器：处理扫码结果轮询 + 弹出排队通知。"""
        self._drain_notes()
        if not self._login_waiting:
            return
        status = self.engine.poll_login()
        win = self._login_win
        if status == "waiting":
            return
        if status == "scanned":
            self.mi_status.title = "状态：已扫码，请在手机上确认"
            if win is not None:
                win.mark_scanned()
            return
        if status == "ok":
            self._login_waiting = False
            try:
                self.engine.finish_login()
            except Q115Error as e:
                self.mi_login.title = "登录 / 切换账号（手机扫码）…"
                self.update_ui()
                # 失败原因在登录窗口里就地说清楚，不再额外弹一个 Alert
                if win is not None:
                    win.fail(f"登录失败：{e}")
                else:
                    rumps.alert(title="登录失败", message=str(e), ok="知道了")
                self._login_win = None
                return
            name = ""
            try:
                _, name = self.engine.account_info()
            except Exception:  # noqa: BLE001
                pass
            self.mi_login.title = "登录 / 切换账号（手机扫码）…"
            self.update_ui()
            if win is not None:
                win.succeed(name)   # 窗口自己对勾、1.1s 后自动关闭
            self._login_win = None
            notify(APP_DISPLAY, "登录成功，现在可以转存链接了")
            return
        # expired / canceled
        self._login_waiting = False
        self.mi_login.title = "登录 / 切换账号（手机扫码）…"
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

    def _on_login_window_closed(self) -> None:
        """用户手动关闭二维码窗口：立即清理引用、停止轮询、恢复菜单文案。"""
        self._login_waiting = False
        self._login_win = None
        self.mi_login.title = "登录 / 切换账号（手机扫码）…"
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
                message="请先点击「登录 / 切换账号（手机扫码）…」，用手机 115 扫码登录。",
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
    # 启动时只同步一次登录状态（登录态有 45s 缓存，之后不重复请求）
    try:
        app.update_ui()
        log("[app] 启动完成：登录状态已刷新")
    except Exception:
        log("[app] 启动刷新异常:\n" + traceback.format_exc())
    app.run()


if __name__ == "__main__":
    main()
