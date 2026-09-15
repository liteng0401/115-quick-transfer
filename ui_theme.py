#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui_theme.py — macOS 原生视觉基座

把 macOS Human Interface Guidelines 里那些「看起来才像原生」的细节做成可复用件：
毛玻璃窗口、vibrancy 文本、SF Symbol 图标、inset 分组容器、accent 强调按钮、
以及统一缓动的动画原语。

设计取舍（写给后来改这里的人）：
- 一律用「动态系统色」（labelColor / secondaryLabelColor / controlAccentColor），
  深浅色模式、强调色跟随系统自动切换，不写死任何十六进制色值。
- 窗口背景用 NSVisualEffectView 的 popover 材质 —— 这是 macOS 弹窗类界面
  （分享面板、字体面板、Safari 下载弹窗）的标准质感。
- 动画只用 AppKit 原生 NSAnimationContext + animator()，不引 QuartzCore 依赖，
  少一个打包依赖就少一处打包失败的可能；默认缓动在 0.2~0.4s 区间足够自然。
"""

from __future__ import annotations

import objc

from AppKit import (
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBox,
    NSBoxCustom,
    NSButton,
    NSClosableWindowMask,
    NSColor,
    NSFont,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSFullSizeContentViewWindowMask,
    NSImage,
    NSImageView,
    NSImageScaleProportionallyUpOrDown,
    NSImageSymbolConfiguration,
    NSClipView,
    NSLayoutAttributeNotAnAttribute,
    NSLineBreakByTruncatingTail,
    NSMakePoint,
    NSMakeRect,
    NSNoTitle,
    NSProgressIndicator,
    NSProgressIndicatorSpinningStyle,
    NSResizableWindowMask,
    NSScrollView,
    NSTextField,
    NSTextView,
    NSTitledWindowMask,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateFollowsWindowActiveState,
    NSVisualEffectView,
    NSWindow,
    NSWindowTitleHidden,
)

# --- 统一的动效时长：macOS 系统弹窗一般在 0.2~0.35s ---
DUR_FAST = 0.18
DUR_NORMAL = 0.26
DUR_SLOW = 0.34

# --- 统一的圆角 / 间距节奏 ---
R_CARD = 10.0
R_ROW = 6.0
PAD = 24.0


# ---------------------------------------------------------------------------
# 坐标系：AppKit 原点在左下，写界面时改成「距顶部多少」更符合直觉
# ---------------------------------------------------------------------------

def place(view, x: float, top: float, w: float, h: float, parent=None) -> None:
    """把一个 view 放到父级坐标下：左边距 x、距顶部 top、宽高 w/h。

    parent 要显式传：这里的布局流程是「先算出位置再 addSubview」，
    这时候 view.superview() 还是 nil，取不到父级高度，算出来的 y 会跑飞。
    """
    parent = parent if parent is not None else view.superview()
    parent_h = parent.frame().size.height if parent is not None else 0.0
    view.setFrame_(NSMakeRect(x, parent_h - top - h, w, h))


# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------

def make_label(text: str, size: float = 13.0, weight=NSFontWeightRegular,
               color=None, tracking: float = 0.0):
    """一个不可编辑、不可选、无背景的单行标签（放在毛玻璃上会自带 vibrancy）。"""
    lab = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    lab.setStringValue_(text)
    lab.setEditable_(False)
    lab.setSelectable_(False)
    lab.setBezeled_(False)
    lab.setDrawsBackground_(False)
    lab.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    lab.setTextColor_(color if color is not None else NSColor.labelColor())
    lab.setLineBreakMode_(NSLineBreakByTruncatingTail)
    lab.setPreferredMaxLayoutWidth_(10_000)
    return lab


def make_section_title(text: str):
    """分区小标题：13pt 中粗 + 次级色，macOS 设置面板里的那种语气。"""
    return make_label(text, size=13.0, weight=NSFontWeightMedium,
                      color=NSColor.secondaryLabelColor())


# ---------------------------------------------------------------------------
# 图标
# ---------------------------------------------------------------------------

_SYMBOL_CACHE: dict[tuple, NSImage | None] = {}


def _fit_symbol(img, size: float):
    """按符号自身的宽高比缩放，长边 = size。

    必须按比例：SF Symbol 的天然尺寸大多不是正方形
    （internaldrive 18×13、folder.fill 18×14、chevron.right 10×14）。
    早先无条件 setSize_((size, size)) 会把它们强行拉成正方形，
    表现就是「图标畸变」—— 面包屑根节点那个硬盘图标被纵向拉高 28%，
    行尾箭头被横向拉宽 40%。
    """
    try:
        w, h = img.size()
        if w > 0 and h > 0:
            if w >= h:
                img.setSize_((size, size * h / w))
            else:
                img.setSize_((size * w / h, size))
        else:
            img.setSize_((size, size))
    except Exception:  # noqa: BLE001
        pass
    return img


def _colorize_symbol(img, color):
    """给符号上色。

    注意：`NSImage.imageWithTintColor:` 在本机 pyobjc/AppKit 绑定里【不存在】
    （hasattr(NSImage, "imageWithTintColor_") is False），原先的写法被
    try/except 静默吞掉 —— 所有传了颜色的图标其实一直是灰的。
    可靠路径是 SymbolConfiguration 的 hierarchical color。
    """
    try:
        cfg = NSImageSymbolConfiguration.configurationWithHierarchicalColor_(color)
        out = img.imageWithSymbolConfiguration_(cfg)
        if out is not None:
            return out
    except Exception:  # noqa: BLE001
        pass
    try:  # 退路：万一以后绑定补上了这个 API
        out = img.imageWithTintColor_(color)
        if out is not None:
            return out
    except Exception:  # noqa: BLE001
        pass
    return img


def symbol(name: str, size: float = 16.0, color=None, scale=None):
    """SF Symbol 取图，带缓存。

    取不到（老系统或符号名写错）一律返回 None，让调用方静默降级 ——
    宁可少一个图标，也不要因为这个崩掉整个弹窗。
    """
    key = (name, size, None if color is None else color.hash(), scale)
    if key in _SYMBOL_CACHE:
        return _SYMBOL_CACHE[key]

    img = None
    try:
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if img is not None and scale is not None:
            cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
                size, NSFontWeightRegular, scale
            )
            configured = img.imageWithSymbolConfiguration_(cfg)
            if configured is not None:
                img = configured
    except Exception:  # noqa: BLE001
        img = None

    if img is not None:
        if color is not None:
            img = _colorize_symbol(img, color)
        img = _fit_symbol(img, size)

    _SYMBOL_CACHE[key] = img
    return img


def make_icon_view(name: str, size: float = 16.0, color=None):
    """一个只放 SF Symbol 的 NSImageView。"""
    iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
    iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    iv.setImage_(symbol(name, size, color))
    iv.setEditable_(False)
    return iv


# ---------------------------------------------------------------------------
# 容器
# ---------------------------------------------------------------------------

def make_card(corner: float = R_CARD):
    """一个圆角描边容器 —— 用来装列表/输入框，视觉上就是 macOS 的 inset group。

    底色用极淡的 controlBackgroundColor，深浅色模式下自动适配；
    背景色为空容器的 NSBox 在毛玻璃上会渲染出干净的卡片感。
    """
    box = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    box.setBoxType_(NSBoxCustom)
    box.setBorderType_(3)  # NSLineBorder
    box.setCornerRadius_(corner)
    box.setBorderWidth_(1.0)
    box.setBorderColor_(NSColor.separatorColor())
    box.setFillColor_(NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.22))
    box.setTitlePosition_(NSNoTitle)
    box.setContentViewMargins_((0, 0))
    return box


class _ClampedClipView(NSClipView):
    """横向偏移物理钳制为 0 的 clip view。

    无论文档视图比可视区宽多少（竖向滚动条出现会压窄可视区），
    bounds.origin.x 恒为 0 —— 横向弹性手势产生的偏移在最终落点处被强制归零，
    从机制上杜绝「左右滑完回不到原位 / 内容被遮挡」。"""

    def setBoundsOrigin_(self, origin):
        try:
            if origin.x != 0:
                origin = NSMakePoint(0, origin.y)
        except Exception:  # noqa: BLE001
            pass
        NSClipView.setBoundsOrigin_(self, origin)


def make_scroll(frame=None, draws_bg: bool = False, corner: float = 0.0):
    sv = NSScrollView.alloc().initWithFrame_(frame or NSMakeRect(0, 0, 10, 10))
    sv.setHasVerticalScroller_(True)
    sv.setHasHorizontalScroller_(False)
    sv.setAutohidesScrollers_(True)
    sv.setDrawsBackground_(draws_bg)
    if draws_bg:
        sv.setBackgroundColor_(NSColor.clearColor())
    # 换装钳制型 clip view（frame 保持与默认一致）
    try:
        cv = _ClampedClipView.alloc().initWithFrame_(sv.contentView().frame())
        cv.setDrawsBackground_(False)  # 默认 clip view 会画白底，必须关掉
        sv.setContentView_(cv)
    except Exception:  # noqa: BLE001
        pass  # 旧系统兜底：退回默认 clip view
    return sv


def make_editor(frame=None, font_size: float = 13.0, rich: bool = False):
    """多行文本编辑器（链接输入框）。"""
    tv = NSTextView.alloc().initWithFrame_(frame or NSMakeRect(0, 0, 10, 10))
    tv.setRichText_(rich)
    tv.setDrawsBackground_(False)
    tv.setFont_(NSFont.systemFontOfSize_(font_size))
    tv.setTextColor_(NSColor.labelColor())
    tv.setAutomaticLinkDetectionEnabled_(False)
    tv.setAutomaticQuoteSubstitutionEnabled_(False)
    tv.setAutomaticDashSubstitutionEnabled_(False)
    tv.setAutomaticTextReplacementEnabled_(False)
    try:
        tv.setIncSearchIfAvailable_(False)
    except Exception:  # noqa: BLE001
        pass
    return tv


def make_spinner(size: float = 18.0):
    sp = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
    sp.setStyle_(NSProgressIndicatorSpinningStyle)
    sp.setControlSize_(1)  # NSSmallControlSize
    sp.setDisplayedWhenStopped_(False)
    sp.setIndeterminate_(True)
    return sp


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------

def make_button(title: str, primary: bool = False, size: float = 13.0):
    """macOS 标准圆角按钮。

    primary=True 时用系统强调色 + 白字，并设为窗口默认键（回车触发），
    这是 macOS 对话框里「确定」按钮的标准形态。
    """
    b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    b.setTitle_(title)
    b.setBezelStyle_(NSBezelStyleRounded)
    b.setFont_(NSFont.systemFontOfSize_weight_(size, NSFontWeightMedium))
    if primary:
        b.setKeyEquivalent_("\r")
        try:
            b.setHasDestructiveAppearance_(False)
        except Exception:  # noqa: BLE001
            pass
        try:
            b.setContentTintColor_(None)
        except Exception:  # noqa: BLE001
            pass
    return b


def make_flat_button(title: str, symbol_name: str | None = None, size: float = 13.0):
    """无边框按钮 + SF Symbol —— macOS 12 起工具栏/页脚按钮的主流样式。

    showsBorderOnlyWhileMouseInside 让边框只在悬停时浮现，
    静止时是纯文字/图标，干净；悬停有反馈，可点性明确。
    """
    b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    b.setTitle_(title)
    if symbol_name:
        img = symbol(symbol_name, size - 1, NSColor.secondaryLabelColor())
        if img is not None:
            b.setImage_(img)
            try:
                b.setImagePosition_(3)  # NSImageLeading
                b.setImageHugsTitle_(True)
            except Exception:  # noqa: BLE001
                pass
    b.setBezelStyle_(NSBezelStyleRounded)
    b.setBordered_(False)
    b.setFont_(NSFont.systemFontOfSize_(size))
    b.setContentTintColor_(NSColor.labelColor())
    try:
        b.setShowsBorderOnlyWhileMouseInside_(True)
    except Exception:  # noqa: BLE001
        pass
    return b


# ---------------------------------------------------------------------------
# 毛玻璃窗口
# ---------------------------------------------------------------------------

def make_window(width: float, height: float, title: str = "",
                resizable: bool = False):
    """一张 macOS 原生观感的窗口：透明标题栏 + 全屏内容视图 + 毛玻璃底。

    这样做出来标题不像贴在窗框上的一条，内容可以一直铺到窗口边缘，
    和系统「关于本机」「存储空间」这类面板是同一套做法。

    resizable 默认 False —— 这一族窗口都是固定版式的模态面板，尺寸一变
    整个布局就散架，理由见下面的注释。
    """
    # 【为什么默认不可缩放 —— 这里踩过坑】
    # 之前 mask 里带着 NSResizableWindowMask，于是双击标题栏会走系统 zoom：
    # 640x700 的小面板被直接撑到整屏（实测 1920x1050）。而 place() 是绝对
    # 坐标 —— 只在建窗那一刻按固定高度算一次 y（parent_h - top - h），窗口
    # 变大后子视图仍停在按旧高度算出的位置：标题、输入卡片、目录列表、底部
    # 按钮会各自「漂」开，整块界面散架（边上留一大片空的毛玻璃）。
    # 去掉这个 mask 后：双击标题栏不再放大，绿色按钮自动变灰（固定尺寸面板
    # 的原生表现），拖边框也不生效。
    # 别改用 contentMinSize/contentMaxSize 去「锁」尺寸 —— 那样绿色按钮还是
    # 亮的、点下去毫无反应，等于摆了个会骗人的假按钮。
    # 另外别担心出场动画：style mask 只约束「用户拖拽 / zoom」，程序自己
    # setFrame: 照常生效，present_window / dismiss_window 的缩放不受影响。
    mask = (
        NSTitledWindowMask
        | NSClosableWindowMask
        | NSFullSizeContentViewWindowMask
    )
    if resizable:
        mask |= NSResizableWindowMask
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), mask, NSBackingStoreBuffered, False
    )
    win.setTitle_(title)
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(NSWindowTitleHidden)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.windowBackgroundColor())
    win.setMovableByWindowBackground_(True)
    win.setReleasedWhenClosed_(False)
    win.setRestorable_(False)

    fx = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    fx.setMaterial_(NSVisualEffectMaterialPopover)
    fx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    fx.setState_(NSVisualEffectStateFollowsWindowActiveState)
    fx.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    win.setContentView_(fx)
    return win


def make_separator(width: float):
    """一条 1px 分隔线，用 separatorColor 自动适配深浅色。"""
    sep = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, width, 1))
    sep.setBoxType_(NSBoxCustom)
    sep.setBorderType_(2)  # NSNoBorder
    sep.setFillColor_(NSColor.separatorColor())
    sep.setBorderWidth_(0.0)
    sep.setTitlePosition_(NSNoTitle)
    return sep


# ---------------------------------------------------------------------------
# 动画原语（全部走 AppKit 的 animator，线程安全）
# ---------------------------------------------------------------------------

def animate(body, duration: float = DUR_NORMAL, completion=None, timing=None) -> None:
    """跑一段 AppKit 动画。body 里写 xxx.animator().set... 即可。

    timing 默认使用 easeInEaseOut —— 这是 macOS 原生过渡最常用的节奏，
    比默认线性更「跟手」，也不会像弹簧那样太抢戏。
    """
    def group(ctx) -> None:
        ctx.setDuration_(duration)
        try:
            ctx.setAllowsImplicitAnimation_(True)
        except Exception:  # noqa: BLE001
            pass
        if timing is None:
            try:
                ctx.setTimingFunction_(
                    objc.lookUpClass("CAMediaTimingFunction").functionWithName_("easeInEaseOut"))
            except Exception:  # noqa: BLE001
                pass
        else:
            # 调用方传入了已构造好的 CAMediaTimingFunction（如 _ease_out() 的产物）
            try:
                ctx.setTimingFunction_(timing)
            except Exception:  # noqa: BLE001
                pass
        body()

    NSAnimationContext.runAnimationGroup_completionHandler_(group, completion)


def _ease_out():
    try:
        return objc.lookUpClass("CAMediaTimingFunction").functionWithName_("easeOut")
    except Exception:  # noqa: BLE001
        return None


def fade_in(view, duration: float = DUR_NORMAL, target: float = 1.0) -> None:
    view.setAlphaValue_(0.0)
    animate(lambda: view.animator().setAlphaValue_(target), duration)


def fade_out(view, duration: float = DUR_FAST, then=None) -> None:
    animate(lambda: view.animator().setAlphaValue_(0.0), duration,
            completion=lambda: then() if then else None)


def pop_in(view, duration: float = DUR_SLOW, overshoot: float = 0.12) -> None:
    """从略小的尺寸弹到实际尺寸 —— 成功标记、Toast 进位用。

    做法：先按中心缩到 (1-overshoot)，动画回原 frame，配合 alpha 淡入。
    比直接 alpha 出现更有「落定」的手感。
    """
    frame = view.frame()
    cx = frame.origin.x + frame.size.width / 2.0
    cy = frame.origin.y + frame.size.height / 2.0
    small_w = frame.size.width * (1.0 - overshoot)
    small_h = frame.size.height * (1.0 - overshoot)
    view.setFrame_(NSMakeRect(cx - small_w / 2.0, cy - small_h / 2.0, small_w, small_h))
    view.setAlphaValue_(0.0)

    def body() -> None:
        view.animator().setAlphaValue_(1.0)
        view.animator().setFrame_(frame)

    animate(body, duration, timing=_ease_out())


def present_window(win, scale_from: float = 0.94, duration: float = DUR_SLOW) -> None:
    """窗口出场：从略小的中心 expanding 到目标 frame，同时淡入。

    macOS 自己的窗口出现没有刻意动画，但第三方弹窗做这一下会明显更「贵气」，
    且不会让人觉得慢（0.3s 内完成）。
    """
    target = win.frame()
    frame = target
    small_w = frame.size.width * scale_from
    small_h = frame.size.height * scale_from
    small = NSMakeRect(
        frame.origin.x + (frame.size.width - small_w) / 2.0,
        frame.origin.y + (frame.size.height - small_h) / 2.0,
        small_w,
        small_h,
    )
    win.setFrame_display_(small, False)
    win.setAlphaValue_(0.0)

    def body() -> None:
        win.animator().setAlphaValue_(1.0)
        win.animator().setFrame_display_(target, False)

    win.makeKeyAndOrderFront_(None)
    animate(body, duration, timing=_ease_out())


def crossfade(container, swap, direction: float = -10.0,
              duration: float = DUR_NORMAL) -> None:
    """容器内容切换：先淡出并轻微位移，换数据后再从反方向滑回来。

    用在「进入/返回 子文件夹」这类同层级内容替换上 ——
    比生硬地 reloadData 多一层空间暗示，用户能感知到层级变化。
    """
    origin = container.frame().origin

    def phase_one(ctx) -> None:
        ctx.setDuration_(duration / 2.0)
        ctx.setCompletionHandler_(phase_two)
        try:
            ctx.setTimingFunction_(objc.lookUpClass("CAMediaTimingFunction").functionWithName_("easeIn"))
        except Exception:  # noqa: BLE001
            pass
        container.animator().setAlphaValue_(0.0)
        container.animator().setFrameOrigin_((origin.x, origin.y + direction))

    def phase_two() -> None:
        container.setFrameOrigin_((origin.x, origin.y - direction))
        swap()

        def back(ctx) -> None:
            ctx.setDuration_(duration)
            try:
                ctx.setTimingFunction_(objc.lookUpClass("CAMediaTimingFunction").functionWithName_("easeOut"))
            except Exception:  # noqa: BLE001
                pass
            container.animator().setAlphaValue_(1.0)
            container.animator().setFrameOrigin_(origin)

        NSAnimationContext.runAnimationGroup_completionHandler_(back, None)

    def group(ctx) -> None:
        phase_one(ctx)

    NSAnimationContext.runAnimationGroup_completionHandler_(group, None)


def fade_in_delayed(view, delay: float = 0.1, duration: float = DUR_NORMAL) -> None:
    """延迟淡入 —— 让同一屏里的元素有先后节奏，而不是齐刷刷一起冒出来。

    用 NSTimer 挂一次性回调，比开线程 sleep 安全：它保证在主线程跑，
    和 AppKit 的动画上下文在同一个 runloop 里。

    ⚠️ 必须挂到 NSRunLoopCommonModes：默认模式的 scheduledTimer 在 modal 会话
    （NSModalPanelRunLoopMode）里【根本不会触发】，而本函数正是用在模态面板的
    成功页里 —— 用默认模式会导致那两行文案永远停在 alpha=0（看不见）。
    """
    view.setAlphaValue_(0.0)
    try:
        from Foundation import NSTimer, NSRunLoop, NSRunLoopCommonModes

        def fire(_timer) -> None:
            animate(lambda: view.animator().setAlphaValue_(1.0), duration)

        timer = NSTimer.timerWithTimeInterval_repeats_block_(delay, False, fire)
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
    except Exception:  # noqa: BLE001  拿不到 block 版 timer 就直接淡入，别让节奏感拖累功能
        animate(lambda: view.animator().setAlphaValue_(1.0), duration)


def dismiss_window(win, then=None, duration: float = DUR_FAST) -> None:
    """窗口收场：轻微缩收 + 淡出，动画结束后再执行 then（通常是真正关闭）。

    系统面板消失得很干脆，这里加一下是为了不让窗口「啪」地没掉 ——
    0.18s 内缩到 0.97 并淡出，观感上像被收回菜单栏图标里。
    """
    if win is None:
        if then is not None:
            then()
        return

    target = win.frame()
    w, h = target.size.width * 0.97, target.size.height * 0.97
    small = NSMakeRect(
        target.origin.x + (target.size.width - w) / 2.0,
        target.origin.y + (target.size.height - h) / 2.0,
        w,
        h,
    )

    def body() -> None:
        win.animator().setAlphaValue_(0.0)
        win.animator().setFrame_display_(small, False)

    animate(body, duration, (lambda: then()) if then is not None else None,
            timing=_ease_out())
