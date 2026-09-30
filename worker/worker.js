/**
 * Telegram -> GitHub Actions bridge (Cloudflare Worker, free tier).
 *
 * Telegram calls this URL whenever you message the bot or tap a button.
 * It checks the request really came from Telegram AND from your own chat,
 * then starts the matching GitHub workflow.
 *
 * Commands:
 *   tap a suggested topic   -> research that suggestion
 *   /topic <anything>       -> research your own topic
 *   /news                   -> run the daily news job now
 *   /help                   -> list commands
 *
 * Secrets (set with `npx wrangler secret put NAME`):
 *   TG_BOT_TOKEN, TG_CHAT_ID, TG_WEBHOOK_SECRET, GH_TOKEN
 * Vars (in wrangler.toml): GH_REPO ("owner/repo"), GH_BRANCH
 */
export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TG_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    const update = await request.json();
    const chatId = update.message?.chat?.id ?? update.callback_query?.message?.chat?.id;
    if (String(chatId) !== String(env.TG_CHAT_ID)) return new Response("ignored"); // only you can drive the bot

    try {
      if (update.callback_query) {
        await handleButton(update.callback_query, env);
      } else if (update.message?.text) {
        await handleText(update.message.text.trim(), env);
      }
    } catch (err) {
      await say(env, `Something went wrong: ${err.message}`);
    }
    return new Response("ok"); // always 200 so Telegram doesn't retry forever
  },
};

async function handleButton(cq, env) {
  const [kind, value] = (cq.data || "").split(":");
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "Starting research…" });
  if (kind === "pick" && /^\d$/.test(value)) {
    await dispatch(env, "research.yml", { topic: "", pick: value });
    await say(env, `Researching suggestion ${Number(value) + 1}. This takes about 3–8 minutes; I'll message you when it's ready.`);
  }
}

async function handleText(text, env) {
  const [cmd, ...rest] = text.split(/\s+/);
  const arg = rest.join(" ").trim();
  switch (cmd.toLowerCase().replace(/@.*$/, "")) {
    case "/topic":
      if (!arg) return say(env, "Send it like this: /topic How UPI works");
      if (arg.length > 120) return say(env, "Keep the topic under 120 characters.");
      await dispatch(env, "research.yml", { topic: arg, pick: "" });
      return say(env, `Researching “${arg}”. I'll message you when it's ready.`);
    case "/news":
      await dispatch(env, "daily-news.yml", {});
      return say(env, "Running the news desk now. The digest arrives in a few minutes.");
    default:
      return say(env, "Commands:\n/topic <topic> – research a topic\n/news – run today's news now\nOr tap a topic button when suggestions arrive.");
  }
}

async function dispatch(env, workflow, inputs) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "tech-desk-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: env.GH_BRANCH || "main", inputs }),
  });
  if (r.status !== 204) throw new Error(`GitHub returned ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

function say(env, text) {
  return tg(env, "sendMessage", { chat_id: env.TG_CHAT_ID, text });
}

function tg(env, method, body) {
  return fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
