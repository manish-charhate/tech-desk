"""Shared helpers for both agents."""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NEWS_DIR = DATA / "news"
TOPICS_DIR = DATA / "topics"
STATE_DIR = DATA / "state"
CONFIG_DIR = ROOT / "config"

TZ = ZoneInfo(os.environ.get("DESK_TZ", "Asia/Kolkata"))

# Models are configurable so you can switch without touching code.
NEWS_MODEL = os.environ.get("NEWS_MODEL") or "claude-haiku-4-5-20251001"
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL") or "claude-sonnet-5"

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

USER_AGENT = "TechDeskBot/1.0 (personal news reader; +https://github.com)"

_session: requests.Session | None = None


def http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def now() -> datetime:
    return datetime.now(TZ)


def today() -> str:
    return now().strftime("%Y-%m-%d")


# ---------- JSON files ----------

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    """Atomic write so a crash never leaves a half-written file in the repo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ---------- text / URLs ----------

_TRACKING_PARAMS = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|cmpid|ncid|taid|guccounter)$", re.I)


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication (not for display)."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def strip_html(text: str, limit: int | None = None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "topic"


def site_link(fragment: str) -> str:
    return f"{SITE_URL}/#/{fragment}" if SITE_URL else ""


# ---------- Telegram ----------

def telegram_send(text: str, buttons: list[list[dict]] | None = None) -> None:
    """Send an HTML-formatted message. Silently skipped if Telegram isn't configured."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] not configured, message was:\n" + text)
        return
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = http().post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=20)
        if not r.ok:
            print(f"[telegram] send failed: {r.status_code} {r.text[:300]}")
    except requests.RequestException as e:
        print(f"[telegram] send failed: {e}")


def esc(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(text or "", quote=False)


# ---------- Claude ----------

def claude():
    import anthropic  # imported lazily so --dry-run works without the SDK configured

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(max_retries=4)


def forced_tool_call(model: str, system: str, user: str, tool: dict, max_tokens: int = 8000) -> dict:
    """Ask Claude to answer by calling one tool, and return that tool's input.

    Forcing a tool call with a JSON schema is the most reliable way to get
    structured output back.
    """
    resp = claude().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return block.input
    raise RuntimeError(f"Model did not call {tool['name']} (stop_reason={resp.stop_reason})")
