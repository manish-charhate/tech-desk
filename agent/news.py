"""Agent 1: daily tech news desk.

Pipeline:
  1. Fetch every RSS feed in config/sources.yaml
  2. Keep only items that are recent AND never seen before (code-enforced freshness)
  3. Claude curates, merges duplicates and writes the digest. It can only point
     at item IDs we gave it, so every source URL is a real feed URL, never invented.
  4. Enrich each story with an image and any embedded YouTube videos from the article
  5. Save data/news/YYYY-MM-DD.json, mark everything as seen, send a Telegram summary

Usage:
  python -m agent.news            # normal run
  python -m agent.news --dry-run  # fetch + filter only, no Claude, nothing saved
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser
import yaml
from bs4 import BeautifulSoup

from .common import (
    CONFIG_DIR, NEWS_DIR, NEWS_MODEL, STATE_DIR, esc, forced_tool_call, http,
    load_json, normalize_url, now, save_json, site_link, strip_html, telegram_send, today,
)

FRESH_HOURS = 30          # a little over 24h so a late cron run never misses items
MAX_CANDIDATES = 160      # cap what we send to the model
SEEN_RETENTION_DAYS = 45  # forget URLs older than this
SEEN_PATH = STATE_DIR / "seen.json"

CATEGORIES = ["ai", "software", "dev-tools", "hardware", "chips", "mobile", "cloud", "security", "business"]


# ---------------------------------------------------------------- fetching

def load_sources() -> list[dict]:
    return yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text())["sources"]


def fetch_feed(src: dict) -> tuple[dict, list[dict], str | None]:
    try:
        r = http().get(src["url"], timeout=25)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001 - one bad feed must not kill the run
        return src, [], str(e)[:200]
    feed = feedparser.parse(r.content)
    return src, [parse_entry(e, src) for e in feed.entries], None


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def _entry_image(entry) -> str | None:
    for m in entry.get("media_content", []) or []:
        if m.get("url") and (m.get("medium") == "image" or "image" in (m.get("type") or "") or not m.get("type")):
            return m["url"]
    for m in entry.get("media_thumbnail", []) or []:
        if m.get("url"):
            return m["url"]
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and "image" in (link.get("type") or ""):
            return link.get("href")
    html = entry.get("summary", "") + "".join(c.get("value", "") for c in entry.get("content", []) or [])
    m = re.search(r'<img[^>]+src="([^"]+)"', html)
    return m.group(1) if m else None


def _youtube_from_entry(entry) -> list[str]:
    link = entry.get("link", "")
    if "youtube.com/watch" in link or "youtu.be/" in link:
        return [link]
    return []


def parse_entry(entry, src: dict) -> dict:
    published = _entry_time(entry)
    return {
        "title": strip_html(entry.get("title", ""), 200),
        "url": entry.get("link", ""),
        "summary": strip_html(entry.get("summary", ""), 450),
        "published": published.isoformat() if published else None,
        "source": src["name"],
        "source_type": src.get("type", "outlet"),
        "image": _entry_image(entry),
        "videos": _youtube_from_entry(entry),
    }


# ---------------------------------------------------------------- freshness

def title_key(title: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return "t:" + hashlib.sha1(norm.encode()).hexdigest()[:16]


def is_seen(item: dict, seen: dict) -> bool:
    return normalize_url(item["url"]) in seen or title_key(item["title"]) in seen


def filter_fresh(items: list[dict], seen: dict, ref: datetime, hours: int = FRESH_HOURS) -> list[dict]:
    cutoff = ref - timedelta(hours=hours)
    fresh, keys = [], set()
    for it in items:
        if not it["url"] or not it["title"] or not it["published"]:
            continue  # undated items can't be proven fresh, so skip them
        if datetime.fromisoformat(it["published"]) < cutoff:
            continue
        if is_seen(it, seen):
            continue
        k = normalize_url(it["url"])
        if k in keys:
            continue
        keys.add(k)
        fresh.append(it)
    # Official sources first, then newest first
    fresh.sort(key=lambda i: (i["source_type"] != "official", -datetime.fromisoformat(i["published"]).timestamp()))
    return fresh[:MAX_CANDIDATES]


def mark_seen(items: list[dict], seen: dict, day: str) -> None:
    for it in items:
        seen[normalize_url(it["url"])] = day
        seen[title_key(it["title"])] = day


def prune_seen(seen: dict, ref: datetime) -> dict:
    cutoff = (ref - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in seen.items() if v >= cutoff}


# ---------------------------------------------------------------- curation

DIGEST_TOOL = {
    "name": "publish_digest",
    "description": "Publish today's curated tech news digest.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "description": "8 to 12 stories, most important first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "headline": {"type": "string", "description": "Plain-English headline in your own words, max 90 chars."},
                        "what_happened": {"type": "string", "description": "2-3 short sentences. Facts only, from the items given."},
                        "status": {"type": "string", "enum": ["confirmed", "rumour", "leak"]},
                        "why_it_matters": {"type": "string", "description": "One line for a normal person."},
                        "dev_angle": {"type": "string", "description": "One line on what it means for developers."},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "reel_potential": {"type": "string", "enum": ["high", "medium", "low"]},
                        "hook_idea": {"type": "string", "description": "One scroll-stopping opening line for a short video."},
                        "item_ids": {
                            "type": "array", "items": {"type": "integer"},
                            "description": "IDs of the items covering this story. Put the official/primary source first.",
                        },
                    },
                    "required": ["headline", "what_happened", "status", "why_it_matters", "dev_angle",
                                 "category", "reel_potential", "hook_idea", "item_ids"],
                },
            }
        },
        "required": ["stories"],
    },
}

CURATOR_SYSTEM = """You are the editor of a daily tech news desk for a short-video creator who explains tech simply.

Scope: software and hardware technology only: AI, chips, phones, computers, operating systems, developer tools, cloud, security, and major business moves by tech companies. Exclude crypto prices, celebrity gossip, deals/discount posts, product reviews, listicles, opinion columns and sponsored content.

Rules:
- Use ONLY the information in the items provided. Never add facts, numbers or quotes from memory.
- Merge items about the same event into one story and list all their IDs, official source first.
- Drop stories that repeat something in "Already covered in the last few days" unless there is a genuinely new development.
- Mark status "rumour" or "leak" whenever the item relies on unnamed sources, leaks, or reports that a company has not confirmed.
- Write in simple, clear English that a non-native speaker can follow. No hype words.
- Pick 8 to 12 stories. If fewer than 8 are worth it, publish fewer."""


def build_prompt(candidates: list[dict], recent_headlines: list[str]) -> str:
    lines = [f"Today is {today()}.", "", "Already covered in the last few days:"]
    lines += [f"- {h}" for h in recent_headlines] or ["- (nothing)"]
    lines += ["", "Today's new items:"]
    for i, it in enumerate(candidates):
        lines.append(f"[{i}] ({it['source_type']}: {it['source']}, {it['published'][:16]}) {it['title']}\n    {it['summary']}")
    return "\n".join(lines)


def recent_headlines(days: int = 3) -> list[str]:
    out = []
    for f in sorted(NEWS_DIR.glob("*.json"))[-days:]:
        out += [s["headline"] for s in load_json(f, {}).get("stories", [])]
    return out


def assemble_stories(raw_stories: list[dict], candidates: list[dict]) -> list[dict]:
    """Attach real sources/images/videos to the model's stories using item IDs."""
    stories = []
    for s in raw_stories:
        ids = [i for i in s.get("item_ids", []) if isinstance(i, int) and 0 <= i < len(candidates)]
        if not ids:
            continue  # a story without a real source never makes it into the feed
        items = [candidates[i] for i in ids]
        stories.append({
            **{k: s[k] for k in ("headline", "what_happened", "status", "why_it_matters",
                                 "dev_angle", "category", "reel_potential", "hook_idea")},
            "published": min(i["published"] for i in items),
            "sources": [{"name": i["source"], "type": i["source_type"], "title": i["title"],
                         "url": i["url"], "published": i["published"]} for i in items],
            "images": list(dict.fromkeys(i["image"] for i in items if i["image"])),
            "videos": list(dict.fromkeys(v for i in items for v in i["videos"])),
        })
    return stories


# ---------------------------------------------------------------- enrichment

_YT = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def page_media(url: str) -> tuple[str | None, list[str]]:
    """Return (og:image, youtube links) found on an article page."""
    try:
        r = http().get(url, timeout=15)
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        return None, []
    soup = BeautifulSoup(r.text, "html.parser")
    og = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "twitter:image"})
    image = og.get("content") if og and og.get("content", "").startswith("http") else None
    ids = list(dict.fromkeys(_YT.findall(r.text)))[:3]
    return image, [f"https://www.youtube.com/watch?v={i}" for i in ids]


def enrich(stories: list[dict]) -> None:
    def work(story):
        image, videos = page_media(story["sources"][0]["url"])
        if image and image not in story["images"]:
            story["images"].insert(0, image)
        story["videos"] = list(dict.fromkeys(story["videos"] + videos))

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, stories))


# ---------------------------------------------------------------- output

POTENTIAL_ORDER = {"high": 0, "medium": 1, "low": 2}


def telegram_digest(day: str, stories: list[dict], failed: list[str]) -> str:
    top = sorted(stories, key=lambda s: POTENTIAL_ORDER[s["reel_potential"]])[:5]
    lines = [f"<b>Tech desk · {day}</b>", f"{len(stories)} fresh stories today.", ""]
    for s in top:
        flag = "" if s["status"] == "confirmed" else f" [{s['status'].upper()}]"
        lines.append(f"• <b>{esc(s['headline'])}</b>{flag}\n  {esc(s['why_it_matters'])}\n  <a href=\"{esc(s['sources'][0]['url'])}\">{esc(s['sources'][0]['name'])}</a>")
    link = site_link(f"news/{day}")
    if link:
        lines += ["", f"<a href=\"{link}\">Open the full feed</a> (updates in a couple of minutes)"]
    if failed:
        lines += ["", f"⚠️ {len(failed)} feed(s) failed: {esc(', '.join(failed))}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Fetch and filter only. No Claude call, nothing saved.")
    args = ap.parse_args()

    ref, day = now(), today()
    seen = load_json(SEEN_PATH, {})
    sources = load_sources()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_feed, sources))
    all_items, failed = [], []
    for src, items, err in results:
        if err:
            failed.append(src["name"])
            print(f"[feed] FAILED {src['name']}: {err}")
        else:
            print(f"[feed] {src['name']}: {len(items)} items")
            all_items += items

    candidates = filter_fresh(all_items, seen, ref)
    print(f"[filter] {len(all_items)} items -> {len(candidates)} fresh, unseen candidates")

    if args.dry_run:
        for c in candidates:
            print(f"  - {c['published'][:16]} [{c['source']}] {c['title']}")
        return

    if not candidates:
        telegram_send(f"<b>Tech desk · {day}</b>\nNo new stories since the last run.")
        return

    result = forced_tool_call(NEWS_MODEL, CURATOR_SYSTEM, build_prompt(candidates, recent_headlines()), DIGEST_TOOL)
    stories = assemble_stories(result.get("stories", []), candidates)
    enrich(stories)

    # If the job runs twice in a day, the second run adds to the same file.
    path = NEWS_DIR / f"{day}.json"
    existing = load_json(path, {})
    save_json(path, {
        "date": day,
        "generated_at": ref.isoformat(timespec="seconds"),
        "stats": {"sources_checked": len(sources), "failed_sources": failed, "candidates": len(candidates)},
        "stories": existing.get("stories", []) + stories,
    })

    # Mark ALL candidates as seen, not just the chosen ones, so skipped items
    # don't come back tomorrow.
    mark_seen(candidates, seen, day)
    save_json(SEEN_PATH, prune_seen(seen, ref))

    print(f"[done] saved {len(stories)} stories to {path.relative_to(path.parents[2])}")
    telegram_send(telegram_digest(day, stories, failed))


if __name__ == "__main__":
    main()
