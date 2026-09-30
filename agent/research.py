"""Agent 2, step 2: research a confirmed topic.

Claude researches with web search + web fetch, and we also hand it arXiv papers
from the arXiv API. Afterwards the code checks every link:
  - source/fact URLs must have actually been retrieved during this run,
    otherwise they are flagged "unverified"
  - image URLs must return an image
  - YouTube links must exist (checked via YouTube's oEmbed endpoint)

Usage:
  python -m agent.research --topic "How HTTPS keeps you safe"
  python -m agent.research --pick 2        # 3rd option from the latest suggestions
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor

import feedparser

from .common import (
    RESEARCH_MODEL, STATE_DIR, TOPICS_DIR, claude, esc, http, load_json,
    normalize_url, now, save_json, site_link, slugify, telegram_send, today,
)

MAX_SEARCHES = 12
MAX_FETCHES = 10
MAX_PAUSES = 6

STOPWORDS = {"how", "does", "do", "what", "is", "are", "the", "a", "an", "works", "work", "why",
             "keeps", "keep", "you", "your", "of", "in", "on", "and", "to", "for", "it", "actually"}

SYSTEM = """You are a meticulous research assistant for a tech explainer channel. You prepare a research brief that the creator will turn into a 60-second video script. Credibility matters more than anything.

Source priority (use the highest available):
1. Official specifications and standards (IETF RFCs, W3C, IEEE, ISO, NIST, NPCI, 3GPP, etc.) and official documentation
2. Official engineering blogs and announcements from the company or project
3. Peer-reviewed papers and preprints (arXiv), university course material
4. Established publications (Ars Technica, IEEE Spectrum, The Verge, etc.)
Avoid SEO content farms, AI-generated listicles, forums and social media posts as sources for facts.

Rules:
- Every fact, number, and myth correction must cite a URL you actually retrieved in this session. If you cannot verify something, leave it out.
- Do not invent URLs. Only use URLs that appeared in search results, fetched pages, or the user message.
- Images: only list image URLs you saw on official pages, press kits, or Wikimedia Commons. Note the page they came from.
- Videos: prefer official channels, conference talks and university lectures.
- Write explanations in simple, clear English a non-native speaker can follow. Technically accurate enough that engineers won't cringe.
"""

SCHEMA_HINT = """When your research is complete, output ONLY a JSON object inside <report_json></report_json> tags, with this shape:
{
  "topic": "string",
  "one_liner": "the idea in one sentence",
  "explain_simple": "3-5 sentences a 12-year-old would understand",
  "explain_engineer": "3-5 sentences with correct technical terms",
  "how_it_works": [{"step": "short title", "detail": "1-2 sentences"}],
  "analogy": "one everyday-life analogy, and where it breaks down",
  "key_facts": [{"fact": "string", "source_url": "url"}],
  "myths": [{"myth": "string", "reality": "string", "source_url": "url"}],
  "visual_ideas": ["diagram / animation ideas for a faceless video"],
  "reel_hooks": ["3 scroll-stopping opening lines"],
  "images": [{"url": "direct image url", "caption": "string", "page_url": "page it came from"}],
  "videos": [{"url": "youtube or official video url", "title": "string"}],
  "papers": [{"title": "string", "url": "url", "year": "string"}],
  "sources": [{"title": "string", "url": "url", "kind": "spec|official|paper|publication|other"}],
  "open_questions": ["things you could not verify, for the creator to check"]
}
Aim for 5-8 how_it_works steps, 5 key_facts, 2-3 myths, and 6-12 sources."""


# ---------------------------------------------------------------- arXiv

def arxiv_papers(topic: str, limit: int = 6) -> list[dict]:
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if w not in STOPWORDS][:5]
    if not words:
        return []
    query = " AND ".join(f"all:{w}" for w in words)
    try:
        r = http().get("https://export.arxiv.org/api/query",
                       params={"search_query": query, "max_results": limit, "sortBy": "relevance"}, timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[arxiv] failed: {e}")
        return []
    feed = feedparser.parse(r.content)
    return [{"title": re.sub(r"\s+", " ", e.title), "url": e.link, "year": e.get("published", "")[:4],
             "abstract": re.sub(r"\s+", " ", e.get("summary", ""))[:600]} for e in feed.entries]


# ---------------------------------------------------------------- Claude loop

def run_claude(topic: str, papers: list[dict]) -> tuple[str, set[str]]:
    """Run the research turn (handling pause_turn). Returns (final text, retrieved URLs)."""
    user = f"Research this topic for an explainer video: {topic}\n\nToday is {today()}.\n"
    if papers:
        user += "\nPossibly relevant arXiv papers (judge relevance yourself; ignore if off-topic):\n"
        user += "\n".join(f"- {p['title']} ({p['year']}) {p['url']}\n  {p['abstract']}" for p in papers)
    user += "\n\n" + SCHEMA_HINT

    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES},
        {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": MAX_FETCHES, "max_content_tokens": 15000},
    ]
    messages = [{"role": "user", "content": user}]
    retrieved = {normalize_url(p["url"]) for p in papers}
    texts: list[str] = []
    client = claude()

    for _ in range(MAX_PAUSES + 1):
        resp = client.messages.create(model=RESEARCH_MODEL, max_tokens=16000, system=SYSTEM,
                                      tools=tools, messages=messages)
        for block in resp.model_dump()["content"]:
            retrieved |= urls_in_block(block)
            if block["type"] == "text":
                texts.append(block["text"])
        if resp.stop_reason != "pause_turn":
            break
        # Long-running server tool turn: send the paused message back unchanged to continue.
        messages = [messages[0], {"role": "assistant", "content": resp.content}]

    usage = resp.usage
    print(f"[claude] stop={resp.stop_reason} in={usage.input_tokens} out={usage.output_tokens} "
          f"server_tools={getattr(usage, 'server_tool_use', None)}")
    return "".join(texts), retrieved


def urls_in_block(block: dict) -> set[str]:
    urls = set()
    if block["type"] == "web_search_tool_result" and isinstance(block.get("content"), list):
        urls |= {r.get("url") for r in block["content"] if r.get("url")}
    elif block["type"] == "web_fetch_tool_result" and isinstance(block.get("content"), dict):
        if block["content"].get("url"):
            urls.add(block["content"]["url"])
    elif block["type"] == "text":
        urls |= {c.get("url") for c in block.get("citations") or [] if c.get("url")}
    return {normalize_url(u) for u in urls}


def parse_report(text: str) -> dict | None:
    m = re.search(r"<report_json>\s*(\{.*\})\s*</report_json>", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        start, end = text.find("{"), text.rfind("}")
        raw = text[start:end + 1] if start != -1 and end > start else None
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- verification

def check_image(url: str) -> bool:
    try:
        r = http().get(url, timeout=12, stream=True)
        ok = r.ok and r.headers.get("content-type", "").startswith("image/")
        r.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


def check_video(url: str) -> str | None:
    """Returns the video title if the link is a real YouTube video, 'ok' for other hosts, None if broken."""
    if "youtube.com" in url or "youtu.be" in url:
        try:
            r = http().get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=12)
            return r.json().get("title") if r.ok else None
        except Exception:  # noqa: BLE001
            return None
    try:
        r = http().head(url, timeout=12, allow_redirects=True)
        return "ok" if r.status_code < 400 else None
    except Exception:  # noqa: BLE001
        return None


def verify(report: dict, retrieved: set[str]) -> dict:
    def flag(items: list[dict], key: str) -> list[dict]:
        for it in items:
            it["verified"] = normalize_url(it.get(key, "")) in retrieved
        return items

    report["key_facts"] = flag(report.get("key_facts", []), "source_url")
    report["myths"] = flag(report.get("myths", []), "source_url")
    report["sources"] = flag(report.get("sources", []), "url")
    report["papers"] = flag(report.get("papers", []), "url")

    images = report.get("images", [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        ok = list(pool.map(lambda i: check_image(i.get("url", "")), images))
    report["images"] = [i for i, good in zip(images, ok) if good]

    videos = report.get("videos", [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        titles = list(pool.map(lambda v: check_video(v.get("url", "")), videos))
    report["videos"] = []
    for v, title in zip(videos, titles):
        if title:
            if title != "ok":
                v["title"] = title  # use the real title, not the model's guess
            report["videos"].append(v)

    report["dropped"] = {"images": len(images) - len(report["images"]),
                         "videos": len(videos) - len(report["videos"])}
    return report


# ---------------------------------------------------------------- main

def resolve_topic(args) -> str:
    if args.topic and args.topic.strip():
        return args.topic.strip()[:120]
    if args.pick is not None and str(args.pick).strip() != "":
        topics = load_json(STATE_DIR / "suggestions.json", {}).get("topics", [])
        idx = int(args.pick)
        if 0 <= idx < len(topics):
            return topics[idx]["title"]
        raise SystemExit(f"No suggestion #{idx}. Run the suggest workflow first.")
    raise SystemExit("Give --topic or --pick.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--pick")
    args = ap.parse_args()
    topic = resolve_topic(args)
    print(f"[research] {topic}")

    papers = arxiv_papers(topic)
    text, retrieved = run_claude(topic, papers)
    report = parse_report(text)
    if report is None:
        # Keep the raw output rather than lose a paid run.
        report = {"topic": topic, "raw_text": text, "sources": [{"title": u, "url": u} for u in sorted(retrieved)]}
    report = verify(report, retrieved)
    report["topic"] = report.get("topic") or topic
    report["requested_topic"] = topic
    report["created_at"] = now().isoformat(timespec="seconds")
    report["model"] = RESEARCH_MODEL

    name = f"{today()}-{slugify(topic)}"
    path = TOPICS_DIR / f"{name}.json"
    save_json(path, report)
    print(f"[done] saved {path.name}")

    unverified = sum(not s.get("verified") for s in report.get("sources", []))
    msg = [f"<b>Research ready: {esc(report['topic'])}</b>", esc(report.get("one_liner", ""))]
    msg.append(f"{len(report.get('sources', []))} sources, {len(report.get('images', []))} images, "
               f"{len(report.get('videos', []))} videos.")
    if unverified:
        msg.append(f"⚠️ {unverified} source(s) marked unverified. Check them before scripting.")
    if "raw_text" in report:
        msg.append("⚠️ Output wasn't valid JSON, so the raw text was saved instead.")
    link = site_link(f"topic/{name}")
    if link:
        msg.append(f"<a href=\"{link}\">Read it</a> (live in a couple of minutes)")
    telegram_send("\n".join(m for m in msg if m))


if __name__ == "__main__":
    main()
