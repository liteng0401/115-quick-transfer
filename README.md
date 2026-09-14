# 115 秒转 · 115QuickTransfer

> 常驻 macOS 菜单栏的小工具：复制磁力 / ed2k / 直链，点一下就提交为 115 网盘「云下载」任务，
> 并自动落到你指定的文件夹。**不用开浏览器，不用登录网页版，不用手点「云下载」。**

```
复制资源链接 → 点菜单栏图标 → 点「转存剪贴板里的链接」
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
- **原生手感** —— 毛玻璃窗口、SF Symbol 矢量图标、缓动动画，跟随系统深浅色
- **不卡界面** —— 目录加载走后台线程，网络慢也不会冻住窗口
- **系统通知** —— 下载完成、失败、登录成功都会通知

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

打包脚本会自动渲染高清菜单栏图标、写入 `LSUIElement`（菜单栏常驻、不占 Dock）并做 ad-hoc 签名。

---

## 使用说明

点菜单栏图标后：

| 菜单项 | 作用 |
| --- | --- |
| **转存剪贴板里的链接** | 弹出转存窗口：上半区是链接编辑框（已自动填好剪贴板内容，支持一行一条批量）；下半区是目录树，**双击**进入子文件夹，点顶部**路径条**可跳回任意一层，「新建文件夹」可在当前目录下先建好再存。确认后点「转存」（或按回车）提交，成功后窗口自动收起 |
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

**Q：会把我的 115 账号挤下线吗？**
登录后会以「macOS 客户端」身份绑定一台设备。如果你同时在用 115 官方 macOS 客户端，
那台可能需要重新登录一次，**不影响数据**。

**Q：为什么没有 Windows 版？**
整个界面是 AppKit 原生的，没有跨平台抽象层。移植等于重写。

---

## 隐私与安全

- **凭据只存在本机**：`~/Library/Application Support/115QuickTransfer/config.json`，文件权限 `600`。
- **没有任何遥测**：不收集、不上传任何使用数据，不连第三方服务器（只连 `115.com` 的接口）。
- **本项目源码中不含任何账号、Cookie 或密钥**，你可以自行审查。
- 换账号：菜单里重新扫码；彻底清除：删掉上面那个 `config.json`。

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
├── ui_login.py       # 扫码登录窗口（状态流转 + 自动收窗）
├── ui_theme.py       # macOS 视觉基座：毛玻璃 / SF Symbol / 卡片 / 动画原语
├── dir_picker.py     # 简化版目录选择窗口（回退路径）
├── q115_engine.py    # 115 引擎：扫码登录 / 目录 / 云下载任务（纯逻辑，无 UI）
├── build_app.py      # 打包脚本（PyInstaller → .app）
├── assets/           # 菜单栏图标（由 build_app.py 渲染生成）
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
