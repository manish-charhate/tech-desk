"""Check every feed in config/sources.yaml and report which ones work.

Usage: python -m agent.check_feeds
"""
from concurrent.futures import ThreadPoolExecutor

from .news import fetch_feed, load_sources


def main() -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_feed, load_sources()))
    bad = 0
    for src, items, err in results:
        if err or not items:
            bad += 1
            print(f"FAIL  {src['name']:<28} {err or 'feed parsed but has 0 items'}")
        else:
            dated = sum(1 for i in items if i["published"])
            imgs = sum(1 for i in items if i["image"])
            print(f"OK    {src['name']:<28} {len(items):>3} items, {dated} dated, {imgs} with images")
    print(f"\n{len(results) - bad}/{len(results)} feeds working.")


if __name__ == "__main__":
    main()
