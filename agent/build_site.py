"""Build the static archive site into _site/ (stdlib only, no pip install needed).

Writes data/index.json (the list of days and topics) and copies site/ + data/.
"""
from __future__ import annotations

import shutil

from .common import DATA, NEWS_DIR, ROOT, TOPICS_DIR, load_json, save_json

OUT = ROOT / "_site"


def build_index() -> dict:
    news = []
    for f in sorted(NEWS_DIR.glob("*.json"), reverse=True):
        d = load_json(f, {})
        stories = d.get("stories", [])
        news.append({"date": f.stem, "count": len(stories),
                     "high": sum(s.get("reel_potential") == "high" for s in stories),
                     "lead": stories[0]["headline"] if stories else ""})
    topics = []
    for f in sorted(TOPICS_DIR.glob("*.json"), reverse=True):
        d = load_json(f, {})
        topics.append({"id": f.stem, "date": f.stem[:10], "topic": d.get("topic", f.stem),
                       "one_liner": d.get("one_liner", "")})
    index = {"news": news, "topics": topics}
    save_json(DATA / "index.json", index)
    return index


def main() -> None:
    index = build_index()
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    shutil.copytree(DATA, OUT / "data", ignore=shutil.ignore_patterns("state", ".gitkeep"))
    (OUT / ".nojekyll").touch()
    print(f"[site] {len(index['news'])} days, {len(index['topics'])} topics -> {OUT}")


if __name__ == "__main__":
    main()
