#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
q115_engine.py — 115 网盘「一键云下载」引擎（纯逻辑，不依赖 GUI）

能力：
  1. 扫码登录（115 官方二维码协议），保存 Cookie 到本机 App Support
  2. 检查登录状态
  3. 解析/校验磁力、ed2k、http(s)、ftp 链接
  4. 把链接提交为 115「云下载」任务，落到指定网盘目录
  5. 列出目标目录、由路径解析目录 id、按路径新建目录
  6. 查询云下载任务列表

底层使用 p115client（https://pypi.org/project/p115client/），
扫码/云下载协议随该库持续跟进上游更新。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import requests
    from p115client import P115Client
    from p115client.fs import P115ShareFileSystem
except Exception:  # pragma: no cover - 打包后依赖必然存在
    raise

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

APP_NAME = "115QuickTransfer"
APP_DISPLAY = "115 秒转"

# 扫码登录时绑定的“设备”，对应 115 生活 macOS 端（os_mac）。
# 选择它通常不会影响网页端/手机端已登录状态。
LOGIN_APP = "mac"

# 云下载任务状态（新 115 云下载 & 旧离线任务的取值都有可能出现，做并集映射）
TASK_STATUS_TEXT = {
    0: "等待开始", 1: "下载中", 2: "已完成", -1: "失败",
    9: "失败", 11: "已完成", 12: "进行中",
}


class Q115Error(Exception):
    """业务错误（带面向用户的中文提示）。"""

class Q115NotLoggedIn(Q115Error):
    pass


class Q115DirNotFound(Q115Error):
    pass


class Q115LoginCanceled(Q115Error):
    pass


class Q115LoginExpired(Q115Error):
    pass


def _retry_transient(fn, attempts: int = 3, delay: float = 1.5):
    """对 DNS 解析失败 / 连接失败等瞬时网络错误自动重试；业务错误不重试。"""
    import requests.exceptions as _rxc  # noqa: PLC0415

    last = None
    for i in range(attempts):
        try:
            return fn()
        except _rxc.RequestException as e:
            # 只重试连接类错误（ConnectionError 含 NameResolutionError/超时），
            # HTTP 4xx/5xx 状态错误不在此列（requests 仅在 raise_for_status 后才是 HTTPError）
            last = e
            if i + 1 < attempts:
                log(f"[net] 瞬时网络错误（第 {i + 1} 次），{delay}s 后重试: {e}")
                time.sleep(delay)
    raise last


def _support_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / APP_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def log(msg: str) -> None:
    """写一行带时间戳的诊断日志（用于排查 GUI 里的问题）。"""
    try:
        with open(_support_dir() / "q115.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + "  " + msg + "\n")
    except Exception:
        pass


class _LoginState:
    __slots__ = ("token", "uid", "qr_png", "created_at")

    def __init__(self, token: dict, uid: str, qr_png: str):
        self.token = token
        self.uid = uid
        self.qr_png = qr_png
        self.created_at = time.time()


# ---------------------------------------------------------------------------
# 配置存储（Cookie / 目标文件夹等）
# ---------------------------------------------------------------------------

class Config:
    """保存在 ~/Library/Application Support/115QuickTransfer/config.json"""

    def __init__(self) -> None:
        self.path = Path.home() / "Library" / "Application Support" / APP_NAME / "config.json"
        self.data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(self.path, 0o600)
        except Exception:
            # 配置文件写不进去时不应让主流程崩溃
            pass

    @property
    def cookie(self) -> Optional[str]:
        return self.data.get("cookie") or None

    @cookie.setter
    def cookie(self, value: Optional[str]) -> None:
        if value:
            self.data["cookie"] = value
        else:
            self.data.pop("cookie", None)
        self.save()

    @property
    def last_path(self) -> str:
        """上次转存使用的目录（旧版 config 里的 target_path 自动迁移）。"""
        v = self.data.get("last_path") or self.data.get("target_path") or "/"
        return v if v.startswith("/") else "/" + v

    @last_path.setter
    def last_path(self, value: str) -> None:
        self.data.pop("target_path", None)  # 迁移旧键
        self.data["last_path"] = ((value or "/").strip() or "/")
        self.save()


# ---------------------------------------------------------------------------
# 链接解析
# ---------------------------------------------------------------------------

_URL_RE = re.compile(
    r"(?:magnet:\?xt=[^\s]+|ed2k://[^\s]+|https?://[^\s]+|ftp://[^\s]+)",
    re.IGNORECASE,
)


def extract_links(text: str) -> list[str]:
    """从一段文本中提取若干条可转存链接（磁力 / ed2k / http / ftp）。"""
    links: list[str] = []
    for line in text.splitlines():
        for m in _URL_RE.findall(line):
            m = m.strip().rstrip(".,;，。；")
            if m and m not in links:
                links.append(m)
    return links


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class Q115Engine:
    # 登录态校验的缓存时长（秒）：避免每次点菜单都打账号接口
    _AUTH_TTL = 45
    # 云下载本月配额缓存时长（秒）
    _QUOTA_TTL = 300

    def __init__(self, config: Optional[Config] = None) -> None:
        self.cfg = config or Config()
        self._client: Optional[P115Client] = None
        self._login: Optional[_LoginState] = None
        self._auth_cache: Optional[tuple[float, tuple[bool, str]]] = None
        self._quota_cache: Optional[tuple[float, tuple[int, int]]] = None

    # ---------- 客户端 ----------

    def _make_client(self, cookie: str) -> P115Client:
        return P115Client(cookie)

    def client(self) -> P115Client:
        if self._client is None:
            if not self.cfg.cookie:
                raise Q115NotLoggedIn("尚未登录 115，请先在菜单中点「登录 / 切换账号」扫码登录")
            self._client = self._make_client(self.cfg.cookie)
        return self._client

    def reset_client(self) -> None:
        self._client = None

    # ---------- 登录 ----------

    def is_logged_in(self) -> bool:
        """校验登录态。注意：不要用 fs_files 目录接口——它极易触发风控，
        风控后会把“已登录”误判为“未登录”。改用轻量的账号类接口。"""
        return self._auth()[0]

    def account_info(self) -> tuple[bool, str]:
        """返回 (是否已登录, 账号显示名)。结果有 45 秒缓存。"""
        return self._auth()

    def invalidate_auth(self) -> None:
        """登录/登出后立即失效登录态缓存。"""
        self._auth_cache = None

    def cloud_quota(self) -> tuple[int, int]:
        """返回云下载本月配额 (剩余, 总量)。5 分钟缓存；失败抛异常。

        数据来自 open 版 get_quota_info（proapi.115.com），web 版该 action 已下线。
        package 数组里多个来源（VIP/赠送/购买…）求和即总余量。"""
        now = time.time()
        if self._quota_cache and now - self._quota_cache[0] < self._QUOTA_TTL:
            return self._quota_cache[1]
        resp = self.client().clouddownload_quota_info_open()
        if resp.get("state") not in (True, 1):
            raise Q115Error(
                f"获取云下载配额失败：{resp.get('message') or resp.get('code')}"
            )
        surplus = total = 0
        for pkg in (resp.get("data") or {}).get("package") or []:
            surplus += int(pkg.get("surplus") or 0)
            total += int(pkg.get("count") or 0)
        result = (surplus, total)
        self._quota_cache = (now, result)
        return result

    def invalidate_quota(self) -> None:
        """转存提交后失效配额缓存，下次打开窗口重新拉。"""
        self._quota_cache = None

    def _auth(self) -> tuple[bool, str]:
        now = time.time()
        if self._auth_cache and now - self._auth_cache[0] < self._AUTH_TTL:
            return self._auth_cache[1]
        result = self._fetch_auth()
        self._auth_cache = (now, result)
        return result

    def _fetch_auth(self) -> tuple[bool, str]:
        if not self.cfg.cookie:
            return False, ""
        client = self.client()
        # login_info 能顺带拿到账号名，优先用它
        try:
            resp = client.login_info()
            if resp.get("state") in (True, 1):
                d = resp.get("data") or {}
                name = d.get("user_name") or d.get("name") or str(d.get("user_id") or "")
                return True, name
        except Exception:
            pass
        for fn in (client.user_info, client.user_my):
            try:
                if fn().get("state") in (True, 1):
                    return True, ""
            except Exception:
                continue
        try:
            resp = client.clouddownload_task_list({"page": 1, "page_size": 1})
            if resp.get("state") in (True, 1):
                return True, ""
        except Exception:
            pass
        return False, ""

    def start_login(self) -> _LoginState:
        """获取二维码：返回二维码 PNG 文件路径（供弹出显示）。"""
        # 注意：官方扫码登录一律使用 web 版 token（/api/1.0/web/1.0/token/），
        # 其他 app 变体（如 mac）返回的 uid 不是可扫码的会话，手机会提示“二维码已过期”。
        # DNS/连接类瞬时错误自动重试 3 次（间隔 1.5s），网络抖动不再直接弹窗。
        try:
            resp = _retry_transient(lambda: P115Client.login_qrcode_token())
        except Exception as e:
            raise Q115Error(f"获取二维码失败，请检查网络后重试（{e}）") from e
        token = dict(resp.get("data") or {})
        uid = str(token.get("uid") or "")
        if not uid:
            raise Q115Error("获取二维码失败：响应缺少 uid，请稍后重试")
        content = token.pop("qrcode", "") or ("https://115.com/scan/dg-" + uid)
        fd, png = tempfile.mkstemp(suffix=".png", prefix="q115login-")
        os.close(fd)
        # 优先直接使用 115 官方生成的二维码图片（与网页/客户端展示的一致，最可靠）
        try:
            r = requests.get(
                "https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode",
                params={"uid": uid},
                timeout=10,
            )
            if r.ok and r.content:
                Path(png).write_bytes(r.content)
            else:
                raise RuntimeError(f"bad image response {r.status_code}")
        except Exception:
            # 降级：把官方二维码内容本地渲染成图片
            try:
                import qrcode  # noqa: PLC0415

                qr = qrcode.QRCode(border=2, box_size=10)
                qr.add_data(content)
                qr.make(fit=True)
                qr.make_image(fill_color="black", back_color="white").save(png)
            except Exception as e:
                raise Q115Error(f"生成二维码图片失败（{e}）") from e
        state = _LoginState(token, uid, png)
        self._login = state
        log(f"[login] 二维码已生成 uid={uid[:8]}… png={png}")
        return state

    def poll_login(self) -> str:
        """轮询扫码状态，返回状态码对应含义：
        'waiting' | 'scanned' | 'ok' | 'expired' | 'canceled' | 'error'"""
        st = self._login
        if st is None:
            return "error"
        if time.time() - st.created_at > 150:
            return "expired"
        try:
            resp = P115Client.login_qrcode_scan_status(st.token)
        except Exception:
            return "waiting"
        status = (resp.get("data") or {}).get("status")
        return {
            0: "waiting", 1: "scanned", 2: "ok",
            -1: "expired", -2: "canceled",
        }.get(status, "waiting")

    @staticmethod
    def _find_cookie(obj: Any) -> Optional[dict]:
        """在响应中递归寻找 cookie 字典（形如 {'UID':..., 'CID':..., ...}）。"""
        if isinstance(obj, dict):
            if "UID" in obj and ("CID" in obj or "SEID" in obj):
                return obj
            for v in obj.values():
                found = Q115Engine._find_cookie(v)
                if found:
                    return found
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                found = Q115Engine._find_cookie(v)
                if found:
                    return found
        return None

    def finish_login(self) -> str:
        """用户扫码确认后取回 Cookie 并保存，返回 cookie 字符串。"""
        st = self._login
        if st is None:
            raise Q115Error("登录流程状态丢失，请重新开始登录")
        try:
            resp = P115Client.login_qrcode_scan_result(st.uid, app=LOGIN_APP)
        except Exception as e:
            msg = str(e)
            if "取消" in msg:
                raise Q115LoginCanceled("已取消登录")
            raise Q115Error(f"登录确认失败（{msg}）") from e
        cookie_map = self._find_cookie(resp)
        if not cookie_map:
            raise Q115Error(f"登录响应异常，未取到 Cookie（{str(resp)[:200]}）")
        cookie = "; ".join(f"{k}={v}" for k, v in cookie_map.items())
        self.cfg.cookie = cookie
        self.reset_client()
        self.invalidate_auth()
        self._login = None
        log("[login] 扫码登录成功，Cookie 已保存")
        return cookie

    def logout(self) -> None:
        self.cfg.cookie = None
        self.reset_client()
        self.invalidate_auth()

    # ---------- 目录 ----------

    @staticmethod
    def _entry_name_dir(e: dict):
        """解析一条目录/文件记录 -> (name, is_dir) 或 None。"""
        if "n" in e:  # webapi.115.com/files 与 aps natsort 格式
            return e["n"], ("fid" not in e)
        if "fn" in e:  # aps 另一变体格式
            name = e.get("fn")
            fc = e.get("fc")
            is_dir = True if fc in ("0", 0) else ("fid" not in e)
            return name, is_dir
        if "file_name" in e:
            if e.get("file_category") is not None:
                try:
                    is_dir = int(e["file_category"]) == 0
                except (TypeError, ValueError):
                    is_dir = not e.get("file_sha1") if "file_sha1" in e else not e.get("sha1")
            elif "file_sha1" in e:
                is_dir = not e["file_sha1"]
            elif "category_id" in e:
                is_dir = "file_id" not in e
            else:
                is_dir = not ("size" in e or "file_size" in e)
            return e["file_name"], is_dir
        return None

    def list_dir_children(self, cid: str = "0") -> list[dict]:
        """列出某目录下一级的所有子目录 [(name, cid), ...]。

        使用 aps 极速目录接口（fs_files 会被风控，这里不再使用）。
        """
        client = self.client()
        children: list[dict] = []
        offset = 0
        limit = 500
        while True:
            try:
                resp = client.fs_files_aps({"cid": cid, "limit": limit, "offset": offset})
            except Exception as e:
                raise Q115Error(f"读取目录失败（{type(e).__name__}：{e}）") from e
            data = resp.get("data") if isinstance(resp, dict) else None
            if not data:
                break
            for e in data:
                parsed = self._entry_name_dir(e)
                if not parsed:
                    continue
                name, is_dir = parsed
                if not is_dir:
                    continue
                dir_id = str(e.get("cid") if "cid" in e else e.get("category_id") or "")
                if name and dir_id:
                    children.append({"name": name, "cid": dir_id})
            offset += len(data)
            if len(data) < limit:
                break
        children.sort(key=lambda x: x["name"])
        return children

    def resolve_dir_id(self, path: str) -> str:
        """由网盘路径（如 /影视/电影）解析目录 id。
        先走 115 官方 getid 接口，失败则逐级遍历兜底。"""
        path = (path or "").strip() or "/"
        if path == "/":
            return "0"
        if not path.startswith("/"):
            path = "/" + path
        client = self.client()
        try:
            resp = client.fs_dir_getid({"path": path})
            if resp.get("state") in (True, 1):
                # 返回格式：顶层 id（新）或 data.id（旧）
                d = resp.get("data")
                top_id = resp.get("id")
                if isinstance(d, dict):
                    for k in ("id", "cid", "file_id", "category_id"):
                        if d.get(k):
                            return str(d[k])
                elif d not in (None, ""):
                    return str(d)
                if top_id not in (None, ""):
                    return str(top_id)
        except Exception:
            pass
        # 兜底：逐级查找
        cid = "0"
        for comp in [c for c in path.split("/") if c]:
            found = None
            for child in self.list_dir_children(cid):
                if child["name"] == comp:
                    found = child
                    break
            if not found:
                raise Q115DirNotFound(f"网盘中找不到目录：{path}\n（最后一级：{comp}）")
            cid = found["cid"]
        return cid

    def create_dir(self, path: str) -> str:
        """按路径新建目录（可多级），返回目录 id。"""
        path = (path or "").strip() or "/"
        if path == "/":
            return "0"
        if not path.startswith("/"):
            path = "/" + path
        try:
            resp = self.client().fs_makedirs(path)
            if resp.get("state") not in (True, 1):
                raise Q115Error(f"新建目录失败：{resp.get('message') or resp}")
        except Q115Error:
            raise
        except Exception as e:
            raise Q115Error(f"新建目录失败（{e}）") from e
        return self.resolve_dir_id(path)

    # ---------- 云下载 ----------

    def quota(self) -> Optional[dict]:
        try:
            resp = self.client().clouddownload_quota_info()
            if resp.get("state") in (True, 1):
                return resp.get("data") or resp
        except Exception:
            return None
        return None

    def submit_links(self, links: list[str], dir_id: str = "0") -> dict:
        """把链接提交为云下载任务。返回接口原始响应。"""
        links = [x.strip() for x in links if x and x.strip()]
        if not links:
            raise Q115Error("没有可提交的链接")
        client = self.client()
        payload: dict[str, Any] = {"wp_path_id": str(dir_id)}
        if len(links) == 1:
            payload["url"] = links[0]
        else:
            for i, url in enumerate(links):
                payload[f"url[{i}]"] = url
        log("[engine] submit payload: " + json.dumps(payload, ensure_ascii=False)[:500])
        resp = client.clouddownload_task_add_urls(payload)
        log("[engine] submit resp: " + json.dumps(resp, ensure_ascii=False)[:1200])
        if resp.get("state") not in (True, 1):
            detail = self._describe_error(resp)
            if "10008" in detail or "任务已存在" in detail or "errtype=war" in detail:
                raise Q115Error(
                    "这条链接之前已经转存过（任务已存在），115 不允许重复添加。\n"
                    "请先在 115 里搜索资源名确认文件是否已在网盘某目录中；"
                    "换一条没转存过的新链接即可正常下载。"
                )
            raise Q115Error("云下载任务提交失败：" + detail)
        return resp

    @staticmethod
    def _describe_error(resp: Any) -> str:
        """把 115 各类错误字段拼成可读信息（message / error_msg / errtype 等都可能出现）。"""
        bits: list[str] = []
        seen: set = set()

        def walk(o: Any) -> None:
            if isinstance(o, dict):
                for k in ("errno", "errcode", "code", "errtype", "error_msg", "message", "error"):
                    v = o.get(k)
                    if v in (None, "", 0, False):
                        continue
                    s = f"{k}={v}"
                    if s not in seen:
                        seen.add(s)
                        bits.append(s)
                for v in o.values():
                    if isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(resp)
        return "；".join(bits)[:400] or ("未知错误：" + json.dumps(resp, ensure_ascii=False)[:200])

    def task_list(self, page: int = 1, page_size: int = 30) -> list[dict]:
        client = self.client()
        resp = client.clouddownload_task_list({"page": page, "page_size": page_size})
        if resp.get("state") not in (True, 1):
            raise Q115Error(f"读取云下载任务失败：{resp.get('message') or resp}")
        tasks = resp.get("tasks") or []
        out = []
        for t in tasks:
            status = t.get("status", t.get("stat", "?"))
            text = TASK_STATUS_TEXT.get(status, f"状态码 {status}")
            name = t.get("name") or t.get("file_name") or "(未知任务)"
            size = t.get("size") or 0
            out.append({"name": name, "status": status, "status_text": text, "size": size, "raw": t})
        return out


    # ---------- 115 分享链接转存 ----------

    def parse_share(self, url: str, receive_code: str | None = None):
        """解析一个 115 分享链接，返回一个 P115ShareFileSystem 对象。

        支持的链接形如：
          https://115.com/s/<share_code>?password=<提取码>
          https://115cdn.com/s/<share_code>
          https://share.115.com/<share_code>
          <share_code>[-<提取码>]
        解析失败或链接失效/提取码错误都会抛出 Q115Error（带中文提示）。
        """
        url = (url or "").strip()
        if not url:
            raise Q115Error("请先粘贴一个 115 分享链接")
        try:
            fs = P115ShareFileSystem.from_url(self.client(), url)
        except ValueError:
            raise Q115Error(
                "这不是一个有效的 115 分享链接。\n"
                "正确格式形如：https://115.com/s/xxxxxxxx?password=xxxx"
            )
        except Exception as e:  # noqa: BLE001
            raise Q115Error(f"解析分享链接失败：{e}") from e
        if receive_code:
            fs.receive_code = receive_code
        # 触发一次分享数据拉取：链接失效 / 提取码错误会在这里暴露出来
        try:
            _ = fs.share_data
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "码" in msg or "password" in msg.lower() or "receive_code" in msg:
                raise Q115Error("提取码错误，请检查分享链接或单独填写提取码") from e
            raise Q115Error(f"无法读取该分享（链接可能已失效）：{msg}") from e
        return fs

    def share_info(self, fs) -> dict:
        """返回分享的元信息：标题 / 分享者 / 文件数。拿不到时返回空字段。"""
        out = {"title": "", "user": "", "file_count": 0}
        try:
            d = getattr(fs, "share_data", None) or {}
        except Exception:  # noqa: BLE001
            d = {}
        info = (d.get("share_info") or {}) if isinstance(d, dict) else {}
        user = (d.get("userinfo") or {}) if isinstance(d, dict) else {}
        if isinstance(info, dict):
            out["title"] = info.get("share_title") or info.get("title") or ""
            fc = info.get("file_count") or info.get("file_num")
            if isinstance(fc, int):
                out["file_count"] = fc
        if isinstance(user, dict):
            out["user"] = user.get("user_name") or ""
        return out

    def share_list(self, fs, cid: str = "0") -> list[dict]:
        """列出分享里某个目录下的内容（cid 为分享内部的目录 id，根目录传 "0"）。

        返回 [(id, name, is_dir, size), ...]，按「文件夹在前、再按名称」排序。
        """
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            cid_int = 0
        try:
            items = list(fs.iterdir(cid_int))
        except Exception as e:  # noqa: BLE001
            raise Q115Error(f"读取分享内容失败：{e}") from e
        out: list[dict] = []
        for a in items:
            out.append({
                "id": str(a.get("id") or ""),
                "name": a.get("name") or "(未命名)",
                "is_dir": bool(a.get("is_dir")),
                "size": a.get("size") or 0,
            })
        out.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return out

    def share_receive(self, fs, file_ids: list[str], to_pid: str | int) -> dict:
        """把选中的分享文件/文件夹转存到我的网盘 to_pid 目录。

        file_ids 是分享内部的 file_id 列表；to_pid 是我的网盘目录 cid。
        文件夹的 file_id 会连同其整个子树一起转存（115 服务端递归处理）。
        返回接口原始响应；出错抛出 Q115Error（含中文提示）。
        """
        ids = [str(i) for i in file_ids if str(i).strip()]
        if not ids:
            raise Q115Error("没有选中任何要转存的项目")
        log(f"[share] receive ids={ids} to_pid={to_pid}")
        try:
            resp = fs.receive(ids, to_pid=int(to_pid))
        except Exception as e:
            msg = str(e)
            log(f"[share] receive 失败: {msg}")
            if "码" in msg or "password" in msg.lower() or "receive_code" in msg:
                raise Q115Error("提取码错误，请检查分享链接或单独填写提取码") from e
            if "重复" in msg or "已存在" in msg:
                raise Q115Error("该分享内容已转存过（目标目录里已存在同名文件）") from e
            raise Q115Error(f"转存失败：{msg}") from e
        return resp


# ---------------------------------------------------------------------------
# CLI 自检（便于开发/排障）
# ---------------------------------------------------------------------------

def _selftest() -> int:
    eng = Q115Engine()
    print("配置文件:", eng.cfg.path)
    print("登录状态:", eng.is_logged_in())
    if not eng.is_logged_in():
        print("说明: 未登录（正常）。真实验证需先在 GUI 中扫码登录。")
    try:
        st = eng.start_login()
        print("二维码图片已生成:", st.qr_png)
    except Exception as e:
        print("二维码生成失败:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
