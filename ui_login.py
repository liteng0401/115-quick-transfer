#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui_login.py — 原生扫码登录窗口

原来的实现是把二维码 PNG 丢给系统「预览」打开，用户还得自己去窗口堆里找它、
关掉它，登录完了也不消失。这里换成一个独立的原生面板：
状态在同一处 evolves —— 等待扫码 → 已扫码待确认 → 成功（自动收窗），
过期则原地给出「重新获取」，不用回到菜单栏再点一遍。

做成非模态而不是 runModalForWindow：登录结果要靠菜单栏应用的主 NSTimer
轮询推进，模态会挡住菜单交互，非模态才能让「二维码窗口 + 菜单栏状态」同时活着。
"""

from __future__ import annotations

from AppKit import (
    NSApplication,
    NSCenterTextAlignment,
    NSColor,
    NSImageView,
    NSImageScaleProportionallyUpOrDown,
    NSMakeRect,
    NSObject,
    NSView,
    NSWindow,
)

from ui_theme import (
    DUR_SLOW,
    PAD,
    animate,
    dismiss_window,
    make_button,
    make_card,
    make_icon_view,
    make_label,
    make_spinner,
    make_window,
    place,
    pop_in,
    present_window,
    symbol,
)

W = 360.0
H = 470.0


class LoginWindow:
    """一张跟着扫码流程演进状态的面板。"""

    def __init__(self, qr_path: str, display: str = "115 秒转") -> None:
        self._qr_path = qr_path
        self._display = display
        self._win = None
        self._stage = None      # 展示 QR 的容器，成功后整体换成结果
        self._qr_view = None
        self._hint = None
        self._status_icon = None
        self._status_text = None
        self._spin = None
        self._btn = None
        self._on_retry = None
        self._done = False

    # ---------------- 显示 ----------------

    def show(self, on_retry=None) -> None:
        """幂等：窗口还活着就只是把它唤到前台，避免重复建窗。"""
        self._on_retry = on_retry
        if self._win is None:
            self._build()
            return
        self._win.makeKeyAndOrderFront_(None)
        try:
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:  # noqa: BLE001
            pass

    def set_qr(self, path: str) -> None:
        """换一张新二维码（原窗口复用，不闪不重建）。"""
        self._qr_path = path
        img = self._load_qr()
        if img is not None and self._qr_view is not None:
            self._qr_view.setImage_(img)

    def _build(self) -> None:
        win = make_window(W, H, "扫码登录")
        self._win = win
        root = win.contentView()

        ico = make_icon_view("person.badge.key.fill", 30.0,
                             NSColor.controlAccentColor())
        place(ico, (W - 30) / 2, 26, 30, 30, root)
        root.addSubview_(ico)

        t1 = make_label("扫码登录 115", 20.0, 0.3)
        t1.setAlignment_(NSCenterTextAlignment)
        place(t1, 30, 64, W - 60, 26, root)
        root.addSubview_(t1)

        t2 = make_label("打开手机「115」App，右上角 ⊕ → 扫一扫", 12.0, 0.0,
                        NSColor.secondaryLabelColor())
        t2.setAlignment_(NSCenterTextAlignment)
        place(t2, 20, 94, W - 40, 16, root)
        root.addSubview_(t2)

        card = make_card(12.0)
        place(card, PAD + 12, 122, W - (PAD + 12) * 2, 244, root)
        root.addSubview_(card)
        self._stage = card

        cw, chh = card.frame().size.width, card.frame().size.height
        qr = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        qr.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        qr.setEditable_(False)
        img = self._load_qr()
        if img is not None:
            qr.setImage_(img)
            size = min(cw, chh) - 32
            place(qr, (cw - size) / 2, (chh - size) / 2, size, size, card)
            card.addSubview_(qr)
            self._qr_view = qr

        # 二维码下方状态行（背景 role: 在毛玻璃上保持与系统设置同色）
        status_icon = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 15, 15))
        status_icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        status_icon.setEditable_(False)
        place(status_icon, 40, H - 78, 15, 15, root)
        root.addSubview_(status_icon)
        self._status_icon = status_icon

        status_text = make_label("等待扫码…", 12.0, 0.0,
                                 NSColor.secondaryLabelColor())
        place(status_text, 61, H - 79, W - 100, 16, root)
        root.addSubview_(status_text)
        self._status_text = status_text

        spin = make_spinner(14.0)
        place(spin, 40, H - 78, 14, 14, root)
        root.addSubview_(spin)
        spin.startAnimation_(None)
        self._spin = spin

        btn = make_button("重新获取二维码", size=13.0)
        place(btn, (W - 132) / 2, H - 116, 132, 32, root)
        btn.setTarget_(None)
        btn.setAction_(None)
        btn.setHidden_(True)
        root.addSubview_(btn)
        self._btn = btn

        helper = _LoginBridge.alloc().init()
        helper.owner = self
        self._helper = helper
        btn.setTarget_(helper)
        btn.setAction_(helper.retry_)
        # 非模态窗口，但点红色关闭按钮也应走 close() 统一收尾
        # （清理 self._win，避免留下一个已关闭却仍被引用的窗口）。
        win.setDelegate_(helper)

        win.setLevel_(3)  # NSFloatingWindowLevel，始终浮在最上层
        present_window(win, duration=DUR_SLOW)

    def _load_qr(self):
        try:
            from AppKit import NSImage

            img = NSImage.alloc().initWithContentsOfFile_(self._qr_path)
            return img if img and img.isValid() else None
        except Exception:  # noqa: BLE001
            return None

    # ---------------- 状态推进 ----------------

    def mark_scanned(self) -> None:
        self._set_status("已扫码，请在手机上确认",
                         NSColor.secondaryLabelColor(),
                         "hand.raised.fill", NSColor.systemBlueColor())

    def waiting(self) -> None:
        self._set_status("等待扫码…", NSColor.secondaryLabelColor(), None, None)

    def succeed(self, name: str = "") -> None:
        """成功：二维码整块换成对勾，1.1s 后自动收窗。"""
        self._done = True
        self._spin.stopAnimation_(None)
        self._spin.setHidden_(True)
        self._status_icon.setHidden_(True)
        self._status_text.setStringValue_("")

        for v in list(self._stage.subviews()):
            v.removeFromSuperview()

        size = 72.0
        sw = self._stage.frame().size.width
        sh = self._stage.frame().size.height
        mark = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
        mark.setImage_(symbol("checkmark.circle.fill", size,
                              NSColor.systemGreenColor()))
        mark.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        place(mark, (sw - size) / 2, sh / 2 - size / 2 - 14, size, size, self._stage)
        self._stage.addSubview_(mark)
        mark.setHidden_(True)

        txt = make_label(f"登录成功{' · ' + name if name else ''}", 15.0, 0.3)
        txt.setAlignment_(NSCenterTextAlignment)
        place(txt, 12, sh / 2 + 36, sw - 24, 20, self._stage)
        self._stage.addSubview_(txt)
        txt.setHidden_(True)

        pop_in(mark, overshoot=0.2)
        pop_in(txt, overshoot=0.08)

        from Foundation import NSTimer

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.1, self._helper, "autoClose:", None, False
        )

    def fail(self, reason: str) -> None:
        """失败/过期：原地给出原因和重试按钮，不消失、也不用回菜单栏重点。"""
        self._spin.stopAnimation_(None)
        self._spin.setHidden_(True)
        self._set_status(reason, NSColor.systemOrangeColor(),
                         "exclamationmark.triangle.fill",
                         NSColor.systemOrangeColor())
        if self._qr_view is not None:
            animate(lambda: self._qr_view.animator().setAlphaValue_(0.35), 0.24)
        self._btn.setHidden_(False)

    def retry_clicked(self) -> None:
        """重取二维码：先让 UI 回到等待态，再交给外部换图（避免窗口重建闪一下）。"""
        self._btn.setHidden_(True)
        self._spin.setHidden_(False)
        self._spin.startAnimation_(None)
        self._set_status("正在重新获取…", NSColor.secondaryLabelColor(), None, None)
        if self._on_retry:
            self._on_retry()

    def close(self) -> None:
        if self._win is None:
            return
        win = self._win
        self._win = None      # 先标记已关，避免动画期间被重复触发
        try:
            NSApplication.sharedApplication().stopModal()
        except Exception:  # noqa: BLE001
            pass

        def done() -> None:
            try:
                win.orderOut_(None)
            except Exception:  # noqa: BLE001
                pass

        dismiss_window(win, done)

    def is_visible(self) -> bool:
        return self._win is not None

    # ---------------- 内部 ----------------

    def _set_status(self, text: str, color, icon_name, icon_color) -> None:
        self._status_text.setStringValue_(text)
        self._status_text.setTextColor_(color)
        if icon_name is None:
            self._status_icon.setHidden_(True)
            self._spin.setHidden_(False)
            if not self._done:
                self._spin.startAnimation_(None)
            return
        self._spin.stopAnimation_(None)
        self._spin.setHidden_(True)
        self._status_icon.setImage_(symbol(icon_name, 14.0, icon_color))
        self._status_icon.setHidden_(False)


class _LoginBridge(NSObject):
    """登录窗口的按钮/定时器桥：同样只放 AppKit 选择器。"""

    def retry_(self, _sender=None) -> None:
        self.owner.retry_clicked()

    def autoClose_(self, _timer=None) -> None:
        self.owner.close()

    def windowShouldClose_(self, _sender=None) -> bool:
        # 点红色关闭按钮 = 正常关闭：交给 close() 做清理与收场动画。
        # 返回 False 阻止系统默认关窗，避免绕过 close() 留下悬挂引用。
        try:
            self.owner.close()
        except Exception:  # noqa: BLE001
            pass
        return False
