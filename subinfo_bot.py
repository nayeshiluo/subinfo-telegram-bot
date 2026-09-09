#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专属订阅查询与管理 Bot —— 柠檬定制版 v3（全功能管家）
核心能力：
1. 单链接秒查：私聊直发 / 群聊 /sub 或 /get，返回流量/到期/节点/进度条
2. 文件解析：直接上传 .txt, .yaml, .yml, .log，自动解析节点总数与国家分布
3. 订阅收藏夹：/save <名称> <链接>，/my 一览名下所有订阅，/del <名称>
4. 节点一键清洗导出：在卡片中直接点击【📥 导出节点文件】，发送干净的节点 txt
5. 轻量按钮交互 + /menu 导航面板
"""
import os
import re
import sys
import time
import json
import html
import base64
import sqlite3
import logging
import signal
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import requests

# ---------- 配置 ----------
BOT_TOKEN = os.environ.get("SUBINFO_BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.environ.get("SUBINFO_ADMIN_IDS", "7996620779").split(",") if x.strip()}
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
TZ = timezone(timedelta(hours=8))  # 北京时间 UTC+8

DB_PATH = "/home/ubuntu/subinfo-bot/subs.db"
REQUEST_TIMEOUT = 20
RATE_LIMIT_SEC = 5
MAX_SUB_SIZE = 5 * 1024 * 1024  # 订阅内容读取上限 5MB
CACHE_MAX = 300

CLASH_UAS = [
    "clash-verge/v2.0.4",
    "ClashforWindows/0.20.39",
    "Clash.Meta/1.18",
    "v2rayNG/1.8.10",
    "sing-box/1.10.0",
]

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
PROTO_RE = re.compile(r"(vless|vmess|trojan|ss|ssr|tuic|hysteria2?|hysteria|wireguard)://", re.I)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("subinfo-bot")

session = requests.Session()
last_hits: dict[int, float] = defaultdict(float)
query_cache: dict[int, dict] = {}  # message_id -> {"url": str, "body": bytes, "ts": float}
running = True

# ---------- 数据库初始化 ----------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            )
        """)
        conn.commit()

init_db()

# ---------- 常用辅助 ----------
def fmt_bytes(n) -> str:
    try:
        n = float(n)
    except Exception:
        return "未知"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{int(n)} {units[i]}" if i == 0 else f"{n:.2f} {units[i]}"

def mask_url(url: str) -> str:
    return re.sub(r"(token|key|uuid|password|pass|secret)=([^&]+)", lambda m: f"{m.group(1)}=*****", url, flags=re.I)

def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text)

def sub_progress_bar(used: float, total: float, width: int = 12) -> str:
    if total <= 0:
        return "`[无限制]`"
    ratio = max(0.0, min(1.0, used / total))
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = ratio * 100
    color = "🔴" if pct >= 90 else ("🟠" if pct >= 60 else "🟢")
    return f"`{bar} {pct:.1f}%` {color}"

def parse_expire(expire_raw) -> tuple[str, str]:
    try:
        ts = int(expire_raw)
    except Exception:
        return "未知", ""
    if ts <= 0:
        return "长期有效", "永不过期"
    dt = datetime.fromtimestamp(ts, TZ)
    now = datetime.now(TZ)
    remain = dt - now
    total_s = remain.total_seconds()
    if total_s <= 0:
        return dt.strftime("%Y-%m-%d %H:%M"), "⚠️ 已到期"
    days, rem = divmod(int(total_s), 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days > 0:
        return dt.strftime("%Y-%m-%d %H:%M"), f"{days}天{hours}小时"
    if hours > 0:
        return dt.strftime("%Y-%m-%d %H:%M"), f"{hours}小时{mins}分"
    return dt.strftime("%Y-%m-%d %H:%M"), f"{mins}分钟"

def decode_sub_text(body: bytes) -> str:
    if not body:
        return ""
    text = body.decode("utf-8", errors="ignore").strip()
    # 尝试 base64 解码
    if len(text) > 40:
        try:
            pad = "=" * (-len(text) % 4)
            dec = base64.b64decode(text + pad).decode("utf-8", errors="ignore")
            if PROTO_RE.search(dec) or "proxies:" in dec or "- name:" in dec:
                return dec
        except Exception:
            pass
    return text

def parse_nodes(body: bytes) -> list[dict]:
    text = decode_sub_text(body)
    if not text:
        return []
    nodes = []
    # 1. URI 格式 (vmess, vless, trojan, ss, etc.)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = PROTO_RE.search(line)
        if not m:
            continue
        proto = m.group(1).lower()
        name = ""
        frag = line.split("#", 1)
        if len(frag) == 2:
            name = requests.utils.unquote(frag[1]).strip()
        if not name and proto == "vmess":
            try:
                payload = line.split("://", 1)[1].split("#", 1)[0]
                pad = "=" * (-len(payload) % 4)
                cfg = json.loads(base64.b64decode(payload + pad).decode("utf-8", errors="ignore"))
                name = cfg.get("ps", "")
            except Exception:
                pass
        nodes.append({"proto": proto, "name": name or f"{proto.upper()} 节点", "raw": line})
    
    # 2. YAML Clash 格式
    if not nodes and ("proxies:" in text or "- name:" in text):
        try:
            import yaml
            data = yaml.safe_load(text)
            proxies = data.get("proxies", []) if isinstance(data, dict) else []
            for p in proxies:
                if isinstance(p, dict) and "name" in p:
                    nodes.append({"proto": p.get("type", "clash"), "name": str(p["name"]), "raw": ""})
        except Exception:
            for m in re.finditer(r"name:\s*[\"']?([^\"'\n,]+)", text):
                nodes.append({"proto": "clash", "name": m.group(1).strip(), "raw": ""})
    return nodes

def country_stats(nodes: list[dict]) -> list[tuple[str, int]]:
    flags = {
        "香港": "🇭🇰", "台湾": "🇹🇼", "日本": "🇯🇵", "韩国": "🇰🇷", "新加坡": "🇸🇬",
        "美国": "🇺🇸", "英国": "🇬🇧", "德国": "🇩🇪", "法国": "🇫🇷", "荷兰": "🇳🇱",
        "俄罗斯": "🇷🇺", "澳大利亚": "🇦🇺", "加拿大": "🇨🇦", "泰国": "🇹🇭",
        "马来西亚": "🇲🇾", "越南": "🇻🇳", "印度": "🇮🇳", "土耳其": "🇹🇷",
        "菲律宾": "🇵🇭", "阿联酋": "🇦🇪", "中国": "🇨🇳", "其他": "🌐"
    }
    counter = defaultdict(int)
    for n in nodes:
        name = n["name"]
        matched = "其他"
        for region in flags:
            if region != "其他" and region in name:
                matched = region
                break
        counter[matched] += 1
    return sorted(counter.items(), key=lambda x: -x[1])

# ---------- 网络请求 ----------
def fetch_sub(url: str) -> dict:
    result = {"ok": False, "error": "", "url": url, "body": b"", "final_headers": {}}
    try:
        headers = {"User-Agent": CLASH_UAS[0], "Accept": "*/*"}
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
        content = b""
        for chunk in resp.iter_content(65536):
            content += chunk
            if len(content) > MAX_SUB_SIZE:
                break
        result["status"] = resp.status_code
        result["final_url"] = resp.url
        result["final_headers"] = dict(resp.headers)
        result["body"] = content
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
    return result

def parse_userinfo(headers: dict) -> dict:
    info = {}
    for part in headers.get("subscription-userinfo", "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            info[k.strip().lower()] = v.strip()
    return info

# ---------- 卡片生成 ----------
def build_card(fetch: dict, title: str = "订阅查询结果") -> str:
    url = fetch.get("url", "")
    lines = [f"📡 **{title}**", "", f"🔗 链接：`{mask_url(url)}`", ""]
    if not fetch.get("ok"):
        lines.append(f"❌ **查询失败**：`{html.escape(fetch.get('error', '未知'))}`")
        return "\n".join(lines)

    headers = fetch.get("final_headers", {})
    body = fetch.get("body", b"")
    info = parse_userinfo(headers)

    if not info:
        nodes = parse_nodes(body)
        lines.append("ℹ️ **未返回流量统计响应头**（属于节点型订阅或无需计费）")
        if nodes:
            lines.append(f"📦 成功识别节点：**{len(nodes)}** 个")
        return "\n".join(lines)

    total = float(info.get("total", 0))
    upload = float(info.get("upload", 0))
    download = float(info.get("download", 0))
    used = upload + download
    expire_raw = info.get("expire", "")

    lines.append("📊 **流量统计**")
    lines.append(f"• 总额度：**{fmt_bytes(total)}**")
    lines.append(f"• 已用量：**{fmt_bytes(used)}**（↑{fmt_bytes(upload)} / ↓{fmt_bytes(download)}）")
    lines.append(f"• 剩余量：**{fmt_bytes(max(0, total - used))}**")
    lines.append(f"• 进度：{sub_progress_bar(used, total)}")
    lines.append("")

    if expire_raw:
        exp_time, remain = parse_expire(expire_raw)
        lines.append(f"⏳ 到期时间：**{exp_time}** ({remain})")
        lines.append("")

    nodes = parse_nodes(body)
    if nodes:
        lines.append(f"📦 包含节点：**{len(nodes)}** 个")

    return "\n".join(lines)

def build_file_card(filename: str, body: bytes) -> str:
    nodes = parse_nodes(body)
    lines = [
        "📂 **本地文件深度解析**",
        "",
        f"📄 文件名：`{filename}`",
        f"📏 文件大小：`{len(body) // 1024} KB`",
        f"📦 识别有效节点：**{len(nodes)}** 个",
        ""
    ]
    if not nodes:
        lines.append("⚠️ 未在文件中检测到标准代理协议（Clash/Vmess/Vless/SS 等）")
        return "\n".join(lines)

    lines.append("🌍 **地区分布概览**")
    stats = country_stats(nodes)
    for region, cnt in stats[:8]:
        flag = {"香港":"🇭🇰","台湾":"🇹🇼","日本":"🇯🇵","韩国":"🇰🇷","新加坡":"🇸🇬","美国":"🇺🇸","英国":"🇬🇧"}.get(region, "🌐")
        lines.append(f"{flag} {region}：**{cnt}** 个")
    lines.append("")
    lines.append("💡 可点击下方按钮一键清洗并导出纯净节点列表")
    return "\n".join(lines)

# ---------- Telegram API 封装 ----------
def api_call(method: str, files=None, **params):
    try:
        if files:
            r = session.post(f"{API_BASE}/{method}", data=params, files=files, timeout=60)
        else:
            r = session.post(f"{API_BASE}/{method}", data=params, timeout=30)
        return r.json() if r.ok else {"ok": False, "error": r.text[:200]}
    except Exception as e:
        logger.error(f"API call {method} failed: {e}")
        return {"ok": False, "error": str(e)}

def send_msg(chat_id, text, reply_to=None, parse_mode="Markdown", reply_markup=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    res = api_call("sendMessage", **params)
    if not res.get("ok") and parse_mode:
        params.pop("parse_mode")
        res = api_call("sendMessage", **params)
    return res

def send_doc(chat_id, filename: str, content: bytes, caption: str = ""):
    files = {"document": (filename, content)}
    params = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
    return api_call("sendDocument", files=files, **params)

def answer_cb(callback_id, text="", alert=False):
    return api_call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=alert)

def build_kb(msg_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 刷新流量", "callback_data": f"reload:{msg_id}"},
                {"text": "🌍 节点详情", "callback_data": f"nodes:{msg_id}"},
            ],
            [
                {"text": "📥 导出节点", "callback_data": f"export:{msg_id}"},
                {"text": "📋 复制链接", "callback_data": f"copy:{msg_id}"},
            ]
        ]
    }

def cache_put(msg_id: int, url: str, body: bytes = b""):
    query_cache[msg_id] = {"url": url, "body": body, "ts": time.time()}
    if len(query_cache) > CACHE_MAX:
        oldest = sorted(query_cache.keys(), key=lambda k: query_cache[k]["ts"])[:50]
        for k in oldest:
            query_cache.pop(k, None)

def cache_get(msg_id: int) -> dict | None:
    return query_cache.get(msg_id)

# ---------- 用户订阅库 (SQLite) ----------
def db_save_sub(user_id: int, name: str, url: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO user_subs (user_id, name, url) VALUES (?, ?, ?)", (user_id, name, url))
        conn.commit()

def db_list_subs(user_id: int) -> list[tuple[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, url FROM user_subs WHERE user_id = ? ORDER BY id DESC", (user_id,))
        return cursor.fetchall()

def db_del_sub(user_id: int, name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM user_subs WHERE user_id = ? AND name = ?", (user_id, name))
        conn.commit()
        return c.rowcount > 0

# ---------- 交互逻辑 ----------
def send_menu(chat_id):
    text = (
        "🎛️ **专属订阅管理中心 · 功能面板**\n\n"
        "请选择您需要的功能："
    )
    kb = {
        "inline_keyboard": [
            [
                {"text": "📦 我的订阅库", "callback_data": "menu:my"},
                {"text": "🔍 查询新链接", "callback_data": "menu:query"},
            ],
            [
                {"text": "📂 上传文件说明", "callback_data": "menu:file"},
                {"text": "📖 完整指令手册", "callback_data": "menu:help"},
            ]
        ]
    }
    send_msg(chat_id, text, reply_markup=kb)

def send_help(chat_id):
    text = (
        "📖 **订阅管家指令手册**\n\n"
        "⚡ **基础查询**：\n"
        "• 私聊：直接发送订阅链接\n"
        "• 群聊：`/sub <链接>` 或 `/get <链接>`\n"
        "• 文件：直接上传 `.txt` / `.yaml` / `.yml` / `.log` 文件\n\n"
        "📦 **多订阅收藏夹**：\n"
        "• `/save <别名> <链接>` - 收藏订阅（如：`/save 玛卡巴卡 https://...`）\n"
        "• `/my` - 一键轮询名下所有订阅流量与剩余天数\n"
        "• `/del <别名>` - 移除指定的订阅\n\n"
        "🎛️ **菜单面板**：输入 `/menu` 调出交互式面板"
    )
    send_msg(chat_id, text)

def do_query_url(chat_id, uid, url, reply_to_id=None, title="订阅查询结果"):
    send_msg(chat_id, "🔍 正在连接订阅服务器并审计响应头…", reply_to=reply_to_id)
    fetch = fetch_sub(url)
    card = build_card(fetch, title=title)
    res = send_msg(chat_id, card, reply_to=reply_to_id)
    if res.get("ok"):
        m_id = res["result"]["message_id"]
        cache_put(m_id, url, fetch.get("body", b""))
        api_call("editMessageReplyMarkup", chat_id=chat_id, message_id=m_id, reply_markup=json.dumps(build_kb(m_id)))

def do_handle_document(chat_id, uid, doc: dict, reply_to_id=None):
    filename = doc.get("file_name", "sub.txt")
    file_id = doc.get("file_id")
    file_size = doc.get("file_size", 0)
    
    if file_size > MAX_SUB_SIZE:
        send_msg(chat_id, "❌ 文件过大，请上传 5MB 以内的配置文件。", reply_to=reply_to_id)
        return
    
    send_msg(chat_id, f"📥 正在拉取文件 `{filename}` 并提取节点特征…", reply_to=reply_to_id)
    f_info = api_call("getFile", file_id=file_id)
    if not f_info.get("ok"):
        send_msg(chat_id, "❌ 无法从 Telegram 下载该文件。", reply_to=reply_to_id)
        return
    
    file_path = f_info["result"].get("file_path", "")
    dl_url = f"{FILE_API_BASE}/{file_path}"
    try:
        r = session.get(dl_url, timeout=30)
        content = r.content
    except Exception as e:
        send_msg(chat_id, f"❌ 下载失败: {e}", reply_to=reply_to_id)
        return

    card = build_file_card(filename, content)
    kb = {
        "inline_keyboard": [
            [{"text": "📥 导出清洗节点", "callback_data": "export_raw"}],
        ]
    }
    res = send_msg(chat_id, card, reply_to=reply_to_id)
    if res.get("ok"):
        m_id = res["result"]["message_id"]
        cache_put(m_id, "", content)
        api_call("editMessageReplyMarkup", chat_id=chat_id, message_id=m_id, reply_markup=json.dumps(build_kb(m_id)))

def do_my_subs(chat_id, uid):
    subs = db_list_subs(uid)
    if not subs:
        send_msg(chat_id, "📭 您还没有收藏任何订阅！\n使用 `/save <名称> <链接>` 即可一键收藏。")
        return
    
    send_msg(chat_id, f"🔄 正在为您轮询名下 **{len(subs)}** 个订阅的实时健康度…")
    cards = []
    for name, url in subs:
        f = fetch_sub(url)
        info = parse_userinfo(f.get("final_headers", {}))
        if not info:
            nodes = parse_nodes(f.get("body", b""))
            cards.append(f"🏷️ **{name}**\n• 节点：约 {len(nodes)} 个 (无流量头)\n• 链接：`{mask_url(url)}`")
            continue
        total = float(info.get("total", 0))
        upload = float(info.get("upload", 0))
        download = float(info.get("download", 0))
        used = upload + download
        expire_raw = info.get("expire", "")
        exp_time, remain = parse_expire(expire_raw) if expire_raw else ("长期", "")
        
        cards.append(
            f"🏷️ **{name}**\n"
            f"• 流量：`{fmt_bytes(used)} / {fmt_bytes(total)}` (剩 {fmt_bytes(max(0, total-used))})\n"
            f"• 状态：{sub_progress_bar(used, total, width=8)}\n"
            f"• 到期：{exp_time} ({remain})"
        )
    
    full_text = "📦 **我的订阅库 · 实时总览**\n\n" + "\n\n───────────────\n\n".join(cards)
    send_msg(chat_id, full_text)

# ---------- 回调处理 ----------
def handle_callback(cb: dict):
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    uid = cb.get("from", {}).get("id", 0)
    cb_id = cb.get("id", "")

    if not data or not chat_id:
        return

    # 1. 菜单类回调
    if data.startswith("menu:"):
        action = data.split(":", 1)[1]
        if action == "my":
            answer_cb(cb_id, "📦 查询订阅库…")
            do_my_subs(chat_id, uid)
        elif action == "query":
            answer_cb(cb_id, "💡 请发送链接")
            send_msg(chat_id, "🔍 **查询订阅**\n\n请直接将机场链接发送给我即可开始测试。")
        elif action == "file":
            answer_cb(cb_id, "📂 支持直接拖入文件")
            send_msg(chat_id, "📂 **文件解析支持**\n\n可直接向我发送 `.txt`、`.yaml`、`.yml`、`.log` 等文件，Bot 会自动解码提取所有代理节点并生成清洗报表。")
        elif action == "help":
            answer_cb(cb_id, "📖")
            send_help(chat_id)
        return

    # 2. 卡片动作回调
    action, _, anchor = data.partition(":")
    try:
        anchor_id = int(anchor)
    except ValueError:
        return

    cached = cache_get(anchor_id)
    if not cached:
        answer_cb(cb_id, "⚠️ 记录已在内存中休眠，请重新发送链接或文件", alert=True)
        return

    url = cached.get("url", "")
    body = cached.get("body", b"")

    if action == "reload":
        if not url:
            answer_cb(cb_id, "本地上传文件无需刷新网络头", alert=True)
            return
        answer_cb(cb_id, "🔄 正在重新向节点服务器探活…")
        fetch = fetch_sub(url)
        cache_put(anchor_id, url, fetch.get("body", b""))
        new_card = build_card(fetch)
        api_call("editMessageText", chat_id=chat_id, message_id=msg_id, text=new_card, parse_mode="Markdown", reply_markup=json.dumps(build_kb(anchor_id)))

    elif action == "nodes":
        answer_cb(cb_id, "🌍 正在分类统计全球落地…")
        nodes = parse_nodes(body)
        stats = country_stats(nodes)
        lines = [f"🌍 **节点深度全览 (共 {len(nodes)} 个)**", ""]
        for reg, cnt in stats:
            flag = {"香港":"🇭🇰","台湾":"🇹🇼","日本":"🇯🇵","韩国":"🇰🇷","新加坡":"🇸🇬","美国":"🇺🇸"}.get(reg, "🌐")
            lines.append(f"{flag} {reg}：**{cnt}** 个")
        lines.append("")
        lines.append("📋 **前 10 个节点名预览**：")
        for n in nodes[:10]:
            lines.append(f"• {n['name']}")
        send_msg(chat_id, "\n".join(lines), reply_to=msg_id)

    elif action == "export":
        answer_cb(cb_id, "📥 正在清洗并导出节点文件…")
        nodes = parse_nodes(body)
        raw_list = [n["raw"] for n in nodes if n.get("raw")]
        if not raw_list:
            send_msg(chat_id, "⚠️ 该订阅或文件为 Clash YAML 格式，暂无独立原始链接行供导出为纯文本。", reply_to=msg_id)
            return
        clean_content = "\n".join(raw_list).encode("utf-8")
        send_doc(chat_id, "clean_nodes.txt", clean_content, caption=f"✅ 成功清洗导出 **{len(raw_list)}** 个纯净节点")

    elif action == "copy":
        answer_cb(cb_id, "📋 链接已脱敏")
        if url:
            send_msg(chat_id, f"🔗 **脱敏订阅链接**：\n`{mask_url(url)}`", reply_to=msg_id)
        else:
            send_msg(chat_id, "这是您直接上传的文件，无远端链接。", reply_to=msg_id)

# ---------- 消息分发 ----------
def handle_message(msg: dict):
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    uid = msg.get("from", {}).get("id", 0)
    text = msg.get("text", "")
    doc = msg.get("document")
    msg_id = msg.get("message_id")
    is_group = chat.get("type") in ("group", "supergroup")

    if not chat_id:
        return

    # 1. 文档文件处理
    if doc:
        do_handle_document(chat_id, uid, doc, reply_to_id=msg_id)
        return

    if not text:
        return

    text_clean = text.strip()

    # 2. 命令匹配
    if text_clean.startswith("/start"):
        send_msg(chat_id, "👋 欢迎使用**全能订阅管家 Bot**（柠檬定制 v3）！\n\n输入 `/menu` 呼出控制面板，或直接发送订阅链接 / 上传文件开始。")
        return
    if text_clean.startswith("/menu"):
        send_menu(chat_id)
        return
    if text_clean.startswith("/help"):
        send_help(chat_id)
        return
    if text_clean.startswith("/my"):
        do_my_subs(chat_id, uid)
        return

    # /save <name> <url>
    if text_clean.startswith("/save"):
        parts = text_clean.split(maxsplit=2)
        if len(parts) < 3:
            send_msg(chat_id, "⚠️ 用法：`/save <机场名称> <订阅链接>`\n例如：`/save 玛卡巴卡 https://sub.example.com/...`", reply_to=msg_id)
            return
        sub_name, sub_url = parts[1].strip(), parts[2].strip()
        db_save_sub(uid, sub_name, sub_url)
        send_msg(chat_id, f"✅ 成功收藏订阅 **{sub_name}**！\n输入 `/my` 可随时查阅所有订阅健康度。", reply_to=msg_id)
        return

    # /del <name>
    if text_clean.startswith("/del"):
        parts = text_clean.split(maxsplit=1)
        if len(parts) < 2:
            send_msg(chat_id, "⚠️ 用法：`/del <机场名称>`", reply_to=msg_id)
            return
        del_name = parts[1].strip()
        ok = db_del_sub(uid, del_name)
        if ok:
            send_msg(chat_id, f"🗑️ 已从您的库中移除订阅：**{del_name}**", reply_to=msg_id)
        else:
            send_msg(chat_id, f"❓ 未在您的库中找到名称为 **{del_name}** 的订阅", reply_to=msg_id)
        return

    # 3. 链接提取
    urls = extract_urls(text_clean)
    if is_group:
        m = re.match(r"^/(sub|get)\s+(.+)$", text_clean, re.I)
        if not m:
            return
        urls = extract_urls(m.group(2))

    if urls:
        do_query_url(chat_id, uid, urls[0], reply_to_id=msg_id)
    elif not is_group and not text_clean.startswith("/"):
        send_msg(chat_id, "❓ 未能识别出订阅链接。请直接粘贴包含 `http://` 或 `https://` 的链接，或直接拖入 `.txt`/`.yaml` 文件。", reply_to=msg_id)

# ---------- 主轮询 ----------
def get_updates(offset: int, timeout: int = 40):
    try:
        r = session.get(
            f"{API_BASE}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])},
            timeout=timeout + 15,
        )
        return r.json().get("result", []) if r.ok else []
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
        return []

def main():
    if not BOT_TOKEN:
        logger.error("SUBINFO_BOT_TOKEN 未设置")
        sys.exit(1)

    me = api_call("getMe")
    if me.get("ok"):
        bot = me["result"]
        logger.info(f"SubInfo Bot v3 已就绪: @{bot.get('username')}")
    else:
        logger.error(f"Bot Token 无效: {me.get('error')}")
        sys.exit(1)

    offset = 0
    while running:
        try:
            updates = get_updates(offset)
            for u in updates:
                if "callback_query" in u:
                    handle_callback(u["callback_query"])
                elif "message" in u:
                    handle_message(u["message"])
                offset = u["update_id"] + 1
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Loop exception: {e}")
            time.sleep(2)

def on_signal(sig, frame):
    global running
    running = False
    logger.info("退出中...")

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    main()
