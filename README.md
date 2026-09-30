# Tech desk

Two agents that feed your tech channel:

1. **Daily news** (every day, 6:00 AM IST): reads official newsrooms and trusted outlets, keeps only stories you haven't seen, and writes a curated digest with sources, images and video links.
2. **Topic research** (Wednesday and Saturday, 9:00 AM IST): suggests 5 explainer topics on Telegram. Tap one, or send your own, and it researches it from official docs, specs, papers and trusted sources.

Everything is saved in this repo and browsable on a private-ish website, one page per day and one per topic.

```
GitHub Actions (cron) ──> agent/news.py ──> data/news/2026-09-27.json ──┐
                    └──> agent/suggest.py ──> Telegram buttons         ├─> GitHub Pages site
Telegram tap ──> Cloudflare Worker ──> agent/research.py ──> data/topics/…json ─┘
```

## How freshness and accuracy are guaranteed

- **Fresh only:** an item must be published in the last 30 hours AND its URL (tracking params stripped) and title must not be in `data/state/seen.json`. Every candidate is marked seen after each run, even ones not picked. This is enforced in code, not by asking the model.
- **No invented links in the news feed:** Claude only returns item IDs; the code attaches the real feed URLs.
- **Research links are checked:** any source URL that wasn't actually retrieved during the run is flagged "unverified". Images must return an image. YouTube links are checked to exist and get their real title.
- **Rumours are labelled:** stories relying on leaks or unnamed sources are marked, and shown in purple on the site.

Still: open the primary source before you say anything on camera.

---

## Setup (about 45 minutes, once)

You'll need: a GitHub account, a Claude API key, a Telegram account, and a free Cloudflare account.

### 1. Create the repo

Create a new GitHub repo called `tech-desk` and push this folder to it.

**Public or private?** GitHub Pages on a private repo needs a paid GitHub plan. On a free plan the repo, and therefore the site, is public. That's fine for news curation, but anyone with the link can see your topics. The site is marked `noindex` so search engines skip it.

### 2. Get a Claude API key

At [platform.claude.com](https://platform.claude.com), create an API key and add a small amount of prepaid credit. Set a monthly spend limit in the Console so a bug can never surprise you.

### 3. Create the Telegram bot

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the **bot token**.
2. Send any message (like "hi") to your new bot.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser. Find `"chat":{"id":123456789`. That number is your **chat ID**.
   (Do this before step 6. Once the webhook is set, getUpdates stops working.)

### 4. Add secrets and variables to GitHub

Repo → Settings → Secrets and variables → Actions.

| Secrets | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from step 2 |
| `TELEGRAM_BOT_TOKEN` | from step 3 |
| `TELEGRAM_CHAT_ID` | from step 3 |

| Variables | Value |
|---|---|
| `SITE_URL` | `https://<your-username>.github.io/tech-desk` |
| `NEWS_MODEL` | optional, defaults to `claude-haiku-4-5-20251001` |
| `RESEARCH_MODEL` | optional, defaults to `claude-sonnet-5` |

### 5. Turn on GitHub Pages

Repo → Settings → Pages → Source: **GitHub Actions**.

Then Actions tab → **Daily news** → Run workflow. In about 3 minutes you should get a Telegram message and see the site at your `SITE_URL`.

### 6. Deploy the Telegram → GitHub bridge (Cloudflare Worker)

This lets the topic buttons and `/topic` command start research.

1. Create a **fine-grained GitHub token**: GitHub → Settings → Developer settings → Fine-grained tokens. Repository access: only `tech-desk`. Permissions: **Actions: Read and write**. Nothing else.
2. Edit `worker/wrangler.toml` and set `GH_REPO` to `your-username/tech-desk`.
3. Deploy:
   ```bash
   cd worker
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put TG_BOT_TOKEN
   npx wrangler secret put TG_CHAT_ID
   npx wrangler secret put GH_TOKEN
   npx wrangler secret put TG_WEBHOOK_SECRET   # make up a long random string, e.g. from `openssl rand -hex 24`
   ```
   Note the worker URL it prints, like `https://tech-desk-bot.<you>.workers.dev`.
4. Point Telegram at it (use the same secret string as above):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d url=https://tech-desk-bot.<you>.workers.dev \
     -d secret_token=<TG_WEBHOOK_SECRET> \
     -d 'allowed_updates=["message","callback_query"]'
   ```
5. Test: send `/topic How HTTPS keeps you safe` to your bot. You should get "Researching…" and, a few minutes later, a "Research ready" message.

---

## Daily use

- **6:00 AM:** Telegram digest with the top 5 stories. Open the full feed on the site, filter to "High reel potential", and hit **Copy notes for script** on anything you want to cover.
- **Wed and Sat, 9:00 AM:** five topic suggestions. Tap one, or send `/topic <your idea>` any time.
- **Anytime:** `/news` runs the news desk right now.

## Customising

- **Sources:** edit `config/sources.yaml`, then run `python -m agent.check_feeds` to confirm each feed works. Add official YouTube channels with their RSS URL (instructions in the file).
- **Schedule:** the cron lines are in `.github/workflows/`. GitHub cron uses UTC (IST = UTC + 5:30).
- **Editorial rules:** `CURATOR_SYSTEM` in `agent/news.py` and `SYSTEM` in `agent/research.py` are plain English. Tune them.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q                 # offline tests
python -m agent.check_feeds         # which feeds work
python -m agent.news --dry-run      # see today's fresh items, no Claude call, nothing saved
export ANTHROPIC_API_KEY=...        # then the real thing
python -m agent.news
python -m agent.research --topic "How UPI works"
python -m agent.build_site && python -m http.server -d _site 8000
```

## Costs (estimates; check current pricing)

- GitHub Actions, Pages, Cloudflare Worker, Telegram: free at this usage.
- Claude API: the daily curation runs on Haiku with one call per day. Research uses up to 12 web searches per topic (web search is billed per search, plus tokens). Expect a few dollars a month. Watch the Console usage page for the first two weeks and adjust `MAX_SEARCHES` in `agent/research.py` if needed.

## Troubleshooting

| Problem | Fix |
|---|---|
| No 6 AM message | Actions tab → Daily news → check the log. Scheduled runs can be a few minutes late. |
| "feed(s) failed" in the digest | Run `python -m agent.check_feeds`; the feed URL probably changed. Update `sources.yaml`. |
| Buttons do nothing | Check the worker logs (`npx wrangler tail`). Usually a wrong `GH_REPO`, an expired `GH_TOKEN`, or a mismatched webhook secret. |
| Site shows old data | The deploy job runs after the data commit; give it 2 minutes, then hard refresh. |
| Scheduled runs stopped | GitHub pauses cron on repos with 60 days of no activity. The daily data commits normally prevent this; re-enable it from the Actions tab if it happens. |
