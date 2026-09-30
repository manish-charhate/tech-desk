"""Agent 2, step 1: suggest explainer topics and ask for confirmation on Telegram.

Runs on Wednesday and Saturday. Tapping a button (or sending /topic <anything>)
triggers the research workflow via the Cloudflare Worker.

Usage:
  python -m agent.suggest
"""
from __future__ import annotations

from .common import (
    NEWS_DIR, NEWS_MODEL, STATE_DIR, TOPICS_DIR, esc, forced_tool_call, load_json,
    now, save_json, telegram_send,
)

SUGGESTIONS_PATH = STATE_DIR / "suggestions.json"

SUGGEST_TOOL = {
    "name": "suggest_topics",
    "description": "Suggest explainer topics.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "description": "Exactly 5 topics.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short 'How X works' style title, max 45 chars."},
                        "why_now": {"type": "string", "description": "One line: why this is timely, tied to recent news if possible."},
                    },
                    "required": ["title", "why_now"],
                },
            }
        },
        "required": ["topics"],
    },
}

SYSTEM = """You plan "how it works" explainer videos for a tech channel that explains technology simply to students, early-career developers and curious non-engineers.

Good topics explain a mechanism under the hood (how UPI settles a payment, how HTTPS keeps you safe, how an LLM picks the next word, how a phone's camera does night mode). They are specific, visual, and can be explained in under 60 seconds.
Avoid topics that are just news, opinions, or too broad ("AI", "the cloud").
Prefer topics connected to this week's news, so the video rides current interest. Include at least one evergreen topic.
Never repeat a topic that was already covered."""


def main() -> None:
    headlines = []
    for f in sorted(NEWS_DIR.glob("*.json"))[-7:]:
        headlines += [s["headline"] for s in load_json(f, {}).get("stories", [])]
    done = [load_json(f, {}).get("topic", "") for f in TOPICS_DIR.glob("*.json")]

    prompt = "This week's headlines:\n" + "\n".join(f"- {h}" for h in headlines or ["(no news yet)"])
    prompt += "\n\nAlready covered (do not repeat):\n" + "\n".join(f"- {t}" for t in done or ["(none)"])

    topics = forced_tool_call(NEWS_MODEL, SYSTEM, prompt, SUGGEST_TOOL, max_tokens=1500)["topics"][:5]
    save_json(SUGGESTIONS_PATH, {"created_at": now().isoformat(timespec="seconds"), "topics": topics})

    lines = ["<b>Pick this week's explainer topic</b>", ""]
    for i, t in enumerate(topics, 1):
        lines.append(f"{i}. <b>{esc(t['title'])}</b>\n   {esc(t['why_now'])}")
    lines += ["", "Tap a topic to start research, or send <code>/topic your own idea</code>."]
    buttons = [[{"text": f"{i}. {t['title'][:40]}", "callback_data": f"pick:{i - 1}"}] for i, t in enumerate(topics, 1)]
    telegram_send("\n".join(lines), buttons)
    print("[done] suggestions sent")


if __name__ == "__main__":
    main()
