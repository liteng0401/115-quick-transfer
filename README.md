# 115 秒转 · 115QuickTransfer

[![Release](https://img.shields.io/github/v/release/liteng0401/115-quick-transfer?label=release&color=brightgreen)](https://github.com/liteng0401/115-quick-transfer/releases/latest)
[![License](https://img.shields.io/github/license/liteng0401/115-quick-transfer?color=blue)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS%2012%2B%20%C2%B7%20Apple%20Silicon-black)
[![Python](https://img.shields.io/badge/python-3.13-3776AB)](https://www.python.org/)

> 常驻 macOS 菜单栏的小工具：复制磁力 / ed2k / 直链，点一下就提交为 115 网盘「云下载」任务，
> 并自动落到你指定的文件夹。**不用开浏览器，不用登录网页版，不用手点「云下载」。**

```
复制资源链接 → 点菜单栏图标 → 点「添加云下载」
            → 同一张窗口里：上半区确认链接 / 下半区挑文件夹 → 点「转存」
            → 任务进入 115 云下载 → 资源落到你选的文件夹
```

链接编辑、目录选择、提交、结果反馈全在同一张原生窗口里完成，不用在几个弹窗之间来回跳。

<!-- TODO: 在这里放 1-2 张截图/GIF，展示菜单栏图标 + 转存窗口。
     截图请勿使用真实账号名或真实网盘目录名。 -->

---

## 为什么做这个

GitHub 上的 115 工具大多是 **Web 服务**（Docker 自托管）、**Telegram 机器人** 或 **浏览器扩展**——
功能很全，但都要额外跑一个环境。

这个工具只干一件事，而且干得最省事：

| | 本工具 | 常见同类方案 |
|---|---|---|
| 运行环境 | macOS 菜单栏常驻，双击即用 | 需要 Docker / 服务器 / 浏览器 |
| 操作路径 | 复制链接 → 点一下 | 打开网页 → 登录 → 粘贴 → 选目录 → 提交 |
| 依赖 | 无（打包成单个 .app） | Node / Python 环境、容器、数据库 |

**它不试图取代那些全家桶，只是把最高频的那个动作压缩到两次点击。**

---

## 功能特性

- **剪贴板即输入** —— 复制链接后点菜单栏即可，支持一行一条批量提交
- **可视化选目录** —— 原生目录浏览器，双击进入子文件夹，点顶部路径条（`115 › 媒体库 › 电影`）跳回任意一层，还能直接新建文件夹
- **扫码登录** —— 手机 115 App 扫一次码，之后长期有效；Cookie 保存在本机（权限 600）
- **115 分享链接快速转存** —— 粘贴一个 115 分享链接（可带 `?password=` 提取码），一键解析出分享里的文件和文件夹，勾选后直接转存到你网盘里的任意目录，不用先下载到自己电脑再上传
- **原生手感** —— 毛玻璃窗口、SF Symbol 矢量图标、缓动动画，跟随系统深浅色
- **不卡界面** —— 目录加载、分享解析、转存提交全部走后台线程，网络慢也不会冻住窗口
- **系统通知** —— 转存完成、失败、登录成功都会通知

---

## 安装

### 方式一：下载打包版（推荐）

1. 到 [Releases](../../releases) 下载最新的 `115QuickTransfer-vX.Y.zip`
2. 解压，把 `115QuickTransfer.app` 拖进「应用程序」
3. **首次打开要绕过 Gatekeeper**：在 app 上 **右键 → 打开 → 再点「打开」**

> 为什么会被拦？因为本项目只做 ad-hoc 签名（没有 Apple 开发者证书，一年 99 美元）。
> 如果你更想彻底一点，也可以在终端执行：
> ```bash
> xattr -dr com.apple.quarantine /Applications/115QuickTransfer.app
> ```

### 方式二：从源码运行

```bash
git clone https://github.com/liteng0401/115-quick-transfer.git
cd 115-quick-transfer

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python app_main.py
```

### 方式三：自己打包成 .app

```bash
.venv/bin/python build_app.py
# 产物：dist/115QuickTransfer.app  +  dist/115QuickTransfer-vX.Y.zip
```

`build_app.py` 会先调用 `make_icons.py`，从 `assets/src/` 里的原图生成**应用图标**（`AppIcon.icns` → `CFBundleIconFile`）和 **1x/2x/3x 三档菜单栏模板图**，再交给 PyInstaller 打包（`--icon`）、写入 `LSUIElement`（菜单栏常驻、不占 Dock）并做 ad-hoc 签名。

图标要换成别的，只改 `assets/src/` 里的原图后重新打包即可：

- `app-icon.png` —— 应用图标原图（方形、建议 1024px）。
- `menu-icon-magnet.png` —— 菜单栏图标原图（深底 + 白色马蹄形磁铁，当前默认）。
  它是**纯二级位图**（全图只有背景与图形两个灰度、零抗锯齿），
  `make_icons.py` 会自动量出这两个电平并按中点二值化，直接得到精确到 1px 的形状；
  抗锯齿交给后续缩放产生。换成同类「深底 + 浅色实心图形」的新图可直接用。
- `menu-icon-folder.png` —— 备用：浅色文件夹 + U 形镂空。
  注意它是**没有 alpha 通道的 RGB 图**：「透明棋盘格」是画进像素的底纹，
  `make_icons.py` 会自己把图形从棋盘格里认出来（外轮廓用局部标准差判别、
  U 形洞口用亮度梯度定边后做几何重建）。
- `menu-icon-line.png` —— 备用：纯黑线条 + 白底（笔画在 18pt 下会发灰，不推荐）。

菜单栏取哪种图由 `make_icons.py` 顶部的 `MENU_KIND` 控制
（`magnet` / `folder` / `solid` / `line`）。

---

## 使用说明

点菜单栏图标后：

| 菜单项 | 作用 |
| --- | --- |
| **添加云下载** | 弹出转存窗口：上半区是链接编辑框（已自动填好剪贴板内容，支持一行一条批量）；下半区是目录树，**双击**进入子文件夹，点顶部**路径条**可跳回任意一层，「新建文件夹」可在当前目录下先建好再存。确认后点「转存」（或按回车）提交，成功后窗口自动收起 |
| **转存 115 分享链接** | 弹出「分享转存」窗口：粘贴一个 115 分享链接（形如 `https://115.com/s/xxxx?password=xxxx`）点「解析」，即可看到分享里的文件与文件夹；**勾选**要转存的项目（默认整份全选，**双击**可进入分享里的子文件夹），下半区挑好自己网盘里的目标目录后点「转存」，文件直接落到你网盘、不会先下到本机 |
| **状态：已登录（账号名） / 未登录** | 显示当前账号，点击可手动刷新登录状态 |
| **登录 / 切换账号** | 弹出二维码（用系统预览打开），手机 115 扫码确认后自动完成登录 |
| **退出** | 退出应用（菜单栏不再常驻） |

**小技巧**

- 默认停在你上次保存的文件夹，想换位置直接双击进别的目录。
- 想让 App 开机自启：系统设置 → 通用 → 登录项 → 把 `115QuickTransfer.app` 加进去。
- 转存 / 下载进度到 115 App 或网页版的「云下载」任务列表里看。

**关于 ed2k**

115 对磁力 / BT 任务一般会保存到你选的文件夹；个别 ed2k 单文件任务，
115 官方可能收进「云下载 / 离线下载」接收目录而不是你选的文件夹——以 115 实际行为为准。

---

## 使用前提

1. **macOS**（Apple Silicon / Intel 均可，Release 里的包是 arm64 构建；Intel 机器请从源码打包）
2. 115 账号需要有 **云下载配额**。免费「原石用户」没有云下载配额，离线功能用不了——
   这是 115 官方规则，不是本工具的限制。
3. 登录方式为 **115 官方扫码**，不需要也不应该把账号密码填给任何第三方。

---

## 常见问题

**Q：打开时提示「无法打开，因为 Apple 无法检查其是否包含恶意软件」？**
右键 → 打开 → 再点「打开」。或者执行上面那条 `xattr -dr` 命令。

**Q：提示未登录 / 登录失效？**
菜单里点「登录 / 切换账号」重新扫码即可。Cookie 长期有效，但 115 改规则时需要重新登录。

**Q：转存后找不到文件？**
去 115 App 或网页版的「云下载」看任务状态。另外见上面 ed2k 那一段。

**Q：分享链接转存和「添加云下载」有什么区别？**
后者是把磁力 / ed2k / 直链交给 115「云下载」（从源站离线拉取）；前者是把**别人分享给你**的 115 文件/文件夹，直接转存（复制）到**你自己的 115 网盘**某个目录，不经过你本机、也不消耗云下载配额。两者都走菜单栏，但面向不同的资源来源。

**Q：分享转存支持带提取码的链接吗？**
支持。链接里带上 `?password=xxxx` 即可；如果分享本身要求提取码而链接里没带，点「解析」会提示「提取码错误」，此时请把提取码补进链接再试。

**Q：转存整份分享会重复吗？**
勾选文件夹即代表转存其整个子树（包括子文件夹）。115 服务端会递归处理；若目标目录已存在同名内容，会提示「已转存过」。默认整份全选，按需用「全不选 / 双击进子目录」缩小范围。

**Q：会把我的 115 账号挤下线吗？**
登录后会以「macOS 客户端」身份绑定一台设备。如果你同时在用 115 官方 macOS 客户端，
那台可能需要重新登录一次，**不影响数据**。

**Q：为什么没有 Windows 版？**
整个界面是 AppKit 原生的，没有跨平台抽象层。移植等于重写。

**Q：用的是 115 官方接口吗？**
不是。本工具调用的是 115 网盘**网页 / 客户端同款接口**，并非 115 官方开放平台接口。

**Q：那为什么不接 115 官方开放平台？**
115 官方开放平台（[open.115.com](https://open.115.com/)）确实提供云下载、文件管理等接口，
合规性和稳定性都更好。但它有一个绕不过去的硬限制：

> 官方协议明确规定，开发者**不得**「以任何直接或间接的方式，赠与、转让、**提供**、售卖或
> **授权他人使用**」其开发凭证；并把「**将开发者账号共享给用户使用**」列为封号行为。

也就是说，**开源工具不能内置一个公共 AppID** —— 那等于让所有使用者共享同一个开发者的凭证，
是会被封号的行为。这既会害了开发者，也随时会害了所有使用者。

如果你希望走官方接口，需要**你自己**去申请：

1. 到 [open.115.com](https://open.115.com/) 提交开发者入驻申请
   （个人开发者需要提交身份证照片，审核由 115 单方决定，不保证通过）
2. 创建应用，拿到你自己的 AppID
3. 在支持官方模式的工具版本里填入你自己的 AppID

> ⚠️ **当前版本还不支持官方开放平台模式。** 这一版走网页接口，好处是**下载即用，
> 不需要申请任何东西**。官方模式还在路线图上。

---

## 路线图

- [ ] **官方开放平台模式** —— 让使用者填入自己的 AppID，走 115 官方授权接口。
      技术方案已摸清（[p115client](https://github.com/ChenyangGao/p115client) 内置的
      `P115OpenClient` 方法名与现用客户端一一对应，改动量不大），
      **目前卡在等待开发者入驻审核通过**。
- [ ] README 补截图 / GIF
- [ ] Intel Mac 构建（当前 Release 只提供 arm64）

---

## 隐私与安全

本节同时是本项目的**数据处理与隐私保护说明**。

**数据会不会离开你的电脑？不会。** 本工具是**本地运行的单机应用**：

- **没有服务端** —— 本项目不存在任何服务器，作者不运营任何后端。
- **不中转、不代理** —— 请求由你的电脑直接发往 115 接口，不经过任何第三方。
- **不收集数据** —— 无遥测、无统计、无崩溃上报。
- **不提供被禁止的服务** —— 不做文件分发、外链播放、对象存储。

**哪些东西被存在本地：**

| 内容 | 位置 | 说明 |
|---|---|---|
| 登录凭据（Cookie） | `~/Library/Application Support/115QuickTransfer/config.json` | 权限 `600`，仅本机可读 |
| 上次使用的目录 | 同上 | 方便下次打开直接定位 |
| 运行日志 | `~/Library/Application Support/115QuickTransfer/q115.log` | 排错用，不含账号密码 |

- **源码中不含任何账号、Cookie 或密钥**，你可以自行审查。
- 换账号：菜单里重新扫码；彻底清除：删掉上面那个目录即可。

---

## ⚠️ 免责声明

- 本项目**仅供学习交流与个人使用**，请勿用于任何商业或非法用途。
- 本工具调用的是 115 网盘的**网页 / 客户端同款接口**（协议由开源库 [p115client](https://github.com/ChenyangGao/p115client) 维护跟进），**并非 115 官方产品**，与 115 官方无任何关联。
- 请自用、**低频使用**。不要拿它高频批量刷任务——可能触发账号风控，后果自负。
- 接口可能因 115 官方调整而失效，作者不保证可用性，也不对账号异常、数据丢失或任何直接/间接损失负责。
- **使用本工具即表示你已理解并接受上述风险。**

---

## 目录结构

```
.
├── app_main.py       # 菜单栏入口（rumps），启动/退出与诊断日志
├── ui_transfer.py    # 转存主窗口（链接 + 目录 + 提交 + 结果动画）
├── ui_share.py       # 分享转存主窗口（解析分享链接 + 勾选内容 + 选目标目录 + 提交）
├── ui_login.py       # 扫码登录窗口（状态流转 + 自动收窗）
├── ui_theme.py       # macOS 视觉基座：毛玻璃 / SF Symbol / 卡片 / 动画原语
├── dir_picker.py     # 简化版目录选择窗口（回退路径）
├── q115_engine.py    # 115 引擎：扫码登录 / 目录 / 云下载任务 / 分享转存（纯逻辑，无 UI）
├── build_app.py      # 打包脚本（PyInstaller → .app）
├── make_icons.py     # 图标生成：原图 → AppIcon.icns + 菜单栏 1x/2x/3x 模板图
├── assets/           # 图标成品（assets/src/ 放原图，改图标只动 src）
└── requirements.txt
```

配置文件（Cookie / 上次使用的目录）：`~/Library/Application Support/115QuickTransfer/config.json`

---

## 开发者笔记

顺手记几个踩过的坑，改代码前值得一看：

**UI 结构**

- `ui_theme.py` 集中了所有「看起来像 macOS」的细节：毛玻璃窗口、透明标题栏、SF Symbol 取图、圆角卡片、pop-in / crossfade / shake 动画原语。
- 颜色一律用动态系统色（`labelColor` / `secondaryLabelColor` / `controlAccentColor`），深浅色和强调色跟随系统。**不要写死十六进制色值**，否则深夜模式会翻车。
- pyobjc 会把 `NSObject` 子类的每个方法按 ObjC 选择器规则解析参数个数。所以 UI 拆成「纯 Python 控制器（管布局/逻辑）+ 极简 `_Bridge(NSObject)`（只放转发选择器）」两层，业务逻辑写进 Bridge 会直接 `BadPrototypeError`。
- pyobjc 对象不能挂 Python 实例属性（`view.xxx = y` 会 `AttributeError`）。要标记子视图请用 `setTag_` + `viewWithTag_`。
- **`NSObject` 子类的类名在同一进程内必须全局唯一**——pyobjc 按类名注册 ObjC 类，重复会抛 `objc.error: ... is overriding existing Objective-C class`。所以 bridge 都带模块前缀（`_TransferBridge` / `_DirPickerBridge` / `_LoginBridge`）。

**模态窗口（这块最容易卡死）**

- `runModalForWindow_` **不会**因为用户点红色关闭按钮而结束。带 `NSClosableWindowMask` 的窗口若没设 delegate 并实现 `windowShouldClose_`，点关闭只会把窗口关掉、模态会话残留 → run loop 卡死、菜单和退出全失灵。
- **`NSTimer.scheduledTimer…` 注册在默认模式，而模态循环跑在 `NSModalPanelRunLoopMode`——默认模式的计时器在模态期间根本不触发。** 任何要在模态期间触发的计时器都必须用 `NSTimer.timerWith…` + `addTimer_forMode_(t, NSRunLoopCommonModes)`。

**打包**

- 新增的 UI 模块是在函数体内动态 import 的，PyInstaller 静态分析扫不到，要在 `build_app.py` 里用 `--hidden-import` 显式声明。**以后新增 UI 模块别忘了同步加进去**，否则打包出来的 app 会报 `ModuleNotFoundError`。
- `pyobjc-framework-Cocoa` 不含 QuartzCore。要用 `CAMediaTimingFunction` 请走运行时 `objc.lookUpClass("CAMediaTimingFunction")`，不要 `from QuartzCore import ...`。

**诊断**

- 启动、退出、崩溃都会追加写入 `~/Library/Application Support/115QuickTransfer/q115_launch.log`，出问题先看这个。

---

## 致谢

- [p115client](https://github.com/ChenyangGao/p115client) —— 115 网盘 Python 客户端，本项目的接口层完全依赖它，MIT 许可
- [rumps](https://github.com/jaredks/rumps) —— macOS 菜单栏应用框架
- [pyobjc](https://github.com/ronaldoussoren/pyobjc) —— AppKit / Foundation 绑定

---

## License

[MIT](LICENSE)
