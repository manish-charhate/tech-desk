"""Offline tests. Run with: python -m pytest -q"""
from datetime import datetime, timedelta, timezone

import feedparser

from agent.common import normalize_url
from agent.news import assemble_stories, filter_fresh, mark_seen, parse_entry, prune_seen

NOW = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)


def rss(items: str) -> str:
    return f"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
    <channel><title>t</title>{items}</channel></rss>"""


def item(title, link, hours_ago, img=None):
    pub = (NOW - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    media = f'<media:content url="{img}" medium="image"/>' if img else ""
    return f"<item><title>{title}</title><link>{link}</link><pubDate>{pub}</pubDate><description>&lt;p&gt;Body of {title}&lt;/p&gt;</description>{media}</item>"


def parse(xml, source_type="outlet"):
    src = {"name": "Test", "type": source_type}
    return [parse_entry(e, src) for e in feedparser.parse(xml).entries]


def test_normalize_url_strips_tracking_and_www():
    a = normalize_url("http://www.Example.com/post/?utm_source=x&id=5#top")
    b = normalize_url("https://example.com/post?id=5")
    assert a == b


def test_parse_entry_extracts_image_and_clean_summary():
    [it] = parse(rss(item("Chip launch", "https://x.com/a", 1, img="https://x.com/a.jpg")))
    assert it["image"] == "https://x.com/a.jpg"
    assert it["summary"] == "Body of Chip launch"
    assert it["published"].startswith("2026-09-27")


def test_filter_keeps_only_recent_and_unseen():
    items = parse(rss(item("New", "https://x.com/new", 2) + item("Old", "https://x.com/old", 50)
                      + item("Seen", "https://x.com/seen?utm_source=rss", 3)))
    seen = {normalize_url("https://x.com/seen"): "2026-09-26"}
    fresh = filter_fresh(items, seen, NOW)
    assert [i["title"] for i in fresh] == ["New"]


def test_same_title_different_url_is_treated_as_seen():
    first = parse(rss(item("Big Launch!", "https://x.com/a", 2)))
    seen = {}
    mark_seen(first, seen, "2026-09-26")
    again = parse(rss(item("big launch", "https://x.com/a-updated", 1)))
    assert filter_fresh(again, seen, NOW) == []


def test_official_sources_sorted_first():
    outlet = parse(rss(item("Outlet story", "https://o.com/1", 1)), "outlet")
    official = parse(rss(item("Official story", "https://c.com/1", 5)), "official")
    fresh = filter_fresh(outlet + official, {}, NOW)
    assert fresh[0]["title"] == "Official story"


def test_prune_seen_drops_old_entries():
    seen = {"a": "2026-01-01", "b": "2026-09-20"}
    assert prune_seen(seen, NOW) == {"b": "2026-09-20"}


def test_assemble_drops_stories_without_valid_ids():
    cands = parse(rss(item("Real", "https://x.com/r", 1, img="https://x.com/r.jpg")))
    base = {"headline": "h", "what_happened": "w", "status": "confirmed", "why_it_matters": "y",
            "dev_angle": "d", "category": "ai", "reel_potential": "high", "hook_idea": "k"}
    stories = assemble_stories([{**base, "item_ids": [0]}, {**base, "item_ids": [99]}], cands)
    assert len(stories) == 1
    assert stories[0]["sources"][0]["url"] == "https://x.com/r"
    assert stories[0]["images"] == ["https://x.com/r.jpg"]
