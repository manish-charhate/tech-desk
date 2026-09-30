(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const main = $("#main");
  const list = $("#list");
  const picker = $("#picker");
  let index = { news: [], topics: [] };
  let tab = "news";
  const state = { category: null, hotOnly: false };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
  const link = (u, text) => (safeUrl(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(text || hostOf(u))}</a>` : "");
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  const parseDay = (d) => new Date(d + "T12:00:00");
  const longDate = (d) => parseDay(d).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const shortDate = (d) => parseDay(d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  const monthOf = (d) => parseDay(d).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  // ---------- sidebar ----------
  function renderList(current) {
    const items = tab === "news"
      ? index.news.map((n) => ({ href: `#/news/${n.date}`, key: n.date, when: shortDate(n.date), count: `${n.count}`, sub: n.lead, group: monthOf(n.date) }))
      : index.topics.map((t) => ({ href: `#/topic/${t.id}`, key: t.id, when: t.topic, count: "", sub: shortDate(t.date), group: monthOf(t.date) }));

    let html = "", lastGroup = "";
    for (const it of items) {
      if (it.group !== lastGroup) { html += `<li class="month">${esc(it.group)}</li>`; lastGroup = it.group; }
      html += `<li><a href="${it.href}"${it.key === current ? ' aria-current="page"' : ""}>
        <span class="when">${esc(it.when)}</span><span class="count">${esc(it.count)}</span>
        <span class="sub">${esc(it.sub)}</span></a></li>`;
    }
    list.innerHTML = html || `<li class="month">${tab === "news" ? "No days yet" : "No topics yet"}</li>`;

    picker.innerHTML = items.map((it) => `<option value="${it.href}"${it.key === current ? " selected" : ""}>${esc(it.when)}${it.count ? ` (${esc(it.count)})` : ""}</option>`).join("");
    for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  }

  // ---------- news day ----------
  function storyNotes(s) {
    return [
      s.headline,
      `Status: ${s.status}`,
      s.what_happened,
      `Why it matters: ${s.why_it_matters}`,
      `For developers: ${s.dev_angle}`,
      `Hook idea: ${s.hook_idea}`,
      "Sources:",
      ...s.sources.map((x) => `- ${x.name}: ${x.url}`),
      ...(s.images.length ? ["Images:", ...s.images.map((u) => `- ${u}`)] : []),
      ...(s.videos.length ? ["Videos:", ...s.videos.map((u) => `- ${u}`)] : []),
    ].join("\n");
  }

  function renderStory(s, i) {
    const img = safeUrl(s.images[0]);
    const tags = [
      `<span class="tag">${esc(s.category)}</span>`,
      s.status !== "confirmed" ? `<span class="tag ${esc(s.status)}">${esc(cap(s.status))}, not confirmed</span>` : `<span class="tag">Confirmed</span>`,
      `<span class="tag ${s.reel_potential === "high" ? "high" : ""}">Reel potential: ${esc(s.reel_potential)}</span>`,
    ].join("");
    const sources = s.sources.map((x) => link(x.url, x.name + (x.type === "official" ? " (official)" : ""))).join("");
    const videos = s.videos.map((u, n) => link(u, `Video ${n + 1}`)).join("");
    const images = s.images.slice(0, 4).map((u, n) => link(u, `Image ${n + 1}`)).join("");
    return `<li class="story" data-potential="${esc(s.reel_potential)}" data-category="${esc(s.category)}">
      <span class="bar" aria-hidden="true"></span>
      <div class="body">
        <div class="tags">${tags}</div>
        <h2>${esc(s.headline)}</h2>
        <p class="facts">${esc(s.what_happened)}</p>
        <dl>
          <dt>Why it matters</dt><dd>${esc(s.why_it_matters)}</dd>
          <dt>For developers</dt><dd>${esc(s.dev_angle)}</dd>
          <dt>Hook idea</dt><dd>${esc(s.hook_idea)}</dd>
        </dl>
        <div class="links"><span class="label">Sources</span>${sources}</div>
        ${images || videos ? `<div class="links"><span class="label">Media</span>${images}${videos}</div>` : ""}
        <div class="links" style="margin-top:10px"><button class="copy" data-i="${i}">Copy notes for script</button></div>
      </div>
      ${img ? `<img class="thumb" src="${esc(img)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
    </li>`;
  }

  async function showDay(date) {
    const day = await getJSON(`data/news/${date}.json`);
    const stories = day.stories || [];
    const cats = [...new Set(stories.map((s) => s.category))];
    const high = stories.filter((s) => s.reel_potential === "high").length;
    const failed = day.stats?.failed_sources || [];

    main.innerHTML = `
      <h1 class="day-title">${esc(longDate(date))}</h1>
      <p class="day-meta">${stories.length} stories picked from ${day.stats?.candidates ?? "?"} new items across ${day.stats?.sources_checked ?? "?"} sources. ${high} with high reel potential.${failed.length ? ` Feeds that failed: ${esc(failed.join(", "))}.` : ""}</p>
      <div class="filters" role="group" aria-label="Filter stories">
        <button class="chip hot" data-hot aria-pressed="${state.hotOnly}">High reel potential</button>
        ${cats.map((c) => `<button class="chip" data-cat="${esc(c)}" aria-pressed="${state.category === c}">${esc(cap(c))}</button>`).join("")}
      </div>
      <ol class="stories">${stories.map(renderStory).join("")}</ol>
      <p class="empty" id="nomatch" hidden>No stories match these filters.</p>`;

    const apply = () => {
      let shown = 0;
      for (const el of main.querySelectorAll(".story")) {
        const ok = (!state.hotOnly || el.dataset.potential === "high") && (!state.category || el.dataset.category === state.category);
        el.hidden = !ok; shown += ok;
      }
      $("#nomatch").hidden = shown > 0;
    };
    main.querySelector("[data-hot]").onclick = (e) => { state.hotOnly = !state.hotOnly; e.currentTarget.setAttribute("aria-pressed", state.hotOnly); apply(); };
    for (const b of main.querySelectorAll("[data-cat]")) {
      b.onclick = () => {
        state.category = state.category === b.dataset.cat ? null : b.dataset.cat;
        for (const o of main.querySelectorAll("[data-cat]")) o.setAttribute("aria-pressed", String(o.dataset.cat === state.category));
        apply();
      };
    }
    for (const b of main.querySelectorAll(".copy")) {
      b.onclick = async () => {
        try { await navigator.clipboard.writeText(storyNotes(stories[+b.dataset.i])); b.textContent = "Copied"; b.dataset.done = ""; }
        catch { b.textContent = "Copy failed"; }
      };
    }
    apply();
  }

  // ---------- topic ----------
  const flag = (x) => (x && x.verified === false ? `<span class="unverified">unverified, check before using</span>` : "");

  async function showTopic(id) {
    const t = await getJSON(`data/topics/${id}.json`);
    const sec = (title, body) => (body ? `<h2>${title}</h2>${body}` : "");
    const ul = (arr, fn) => (arr && arr.length ? `<ul>${arr.map(fn).join("")}</ul>` : "");

    if (t.raw_text) {
      main.innerHTML = `<article class="topic"><h1>${esc(t.topic)}</h1>
        <p class="lede">The research finished but wasn't saved in the usual format, so here is the raw output.</p>
        <div class="raw">${esc(t.raw_text)}</div>
        ${sec("Pages retrieved", ul(t.sources, (s) => `<li>${link(s.url, s.url)}</li>`))}</article>`;
      return;
    }

    main.innerHTML = `<article class="topic">
      <h1>${esc(t.topic)}</h1>
      <p class="lede">${esc(t.one_liner)}</p>
      ${sec("In simple words", t.explain_simple ? `<p>${esc(t.explain_simple)}</p>` : "")}
      ${sec("For engineers", t.explain_engineer ? `<p>${esc(t.explain_engineer)}</p>` : "")}
      ${sec("How it works", t.how_it_works?.length ? `<ol class="steps">${t.how_it_works.map((s) => `<li><strong>${esc(s.step)}</strong>${esc(s.detail)}</li>`).join("")}</ol>` : "")}
      ${sec("Analogy", t.analogy ? `<p>${esc(t.analogy)}</p>` : "")}
      ${sec("Key facts", ul(t.key_facts, (f) => `<li>${esc(f.fact)} ${link(f.source_url)}${flag(f)}</li>`))}
      ${sec("Myths", ul(t.myths, (m) => `<li><strong>Myth:</strong> ${esc(m.myth)}<br><strong>Reality:</strong> ${esc(m.reality)} ${link(m.source_url)}${flag(m)}</li>`))}
      ${sec("Hook ideas", ul(t.reel_hooks, (h) => `<li>${esc(h)}</li>`))}
      ${sec("Visual ideas", ul(t.visual_ideas, (v) => `<li>${esc(v)}</li>`))}
      ${sec("Images", t.images?.length ? `<div class="gallery">${t.images.filter((i) => safeUrl(i.url)).map((i) => `<figure><a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer"><img src="${esc(i.url)}" alt="${esc(i.caption)}" loading="lazy" referrerpolicy="no-referrer"></a><figcaption>${esc(i.caption)} ${link(i.page_url, "source page")}</figcaption></figure>`).join("")}</div><p class="day-meta">Check each image's usage rights before putting it in a video.</p>` : "")}
      ${sec("Videos", ul(t.videos, (v) => `<li>${link(v.url, v.title || v.url)}</li>`))}
      ${sec("Research papers", ul(t.papers, (p) => `<li>${link(p.url, p.title)} ${p.year ? `(${esc(p.year)})` : ""}${flag(p)}</li>`))}
      ${sec("All sources", ul(t.sources, (s) => `<li>${link(s.url, s.title || s.url)} ${s.kind ? `<span class="day-meta">${esc(s.kind)}</span>` : ""}${flag(s)}</li>`))}
      ${sec("Still to verify", ul(t.open_questions, (q) => `<li>${esc(q)}</li>`))}
    </article>`;
  }

  // ---------- routing ----------
  function empty(title, text) {
    main.innerHTML = `<div class="empty"><strong>${esc(title)}</strong>${esc(text)}</div>`;
  }

  async function route() {
    const [, kind, id] = location.hash.split("/");
    try {
      if (kind === "topic" && id) { tab = "topics"; renderList(id); await showTopic(id); }
      else if (kind === "news" && id) { tab = "news"; renderList(id); await showDay(id); }
      else if (tab === "topics") {
        if (index.topics[0]) { location.replace(`#/topic/${index.topics[0].id}`); return; }
        renderList(null); empty("No topics yet", "Pick a topic from the Telegram message on Wednesday or Saturday, or send /topic to the bot.");
      }
      else if (index.news[0]) { location.replace(`#/news/${index.news[0].date}`); return; }
      else { renderList(null); empty("Nothing here yet", tab === "news" ? "The first digest appears after the daily run at 6:00 AM, or when you run the Daily news workflow manually." : "Pick a topic from the Telegram message on Wednesday or Saturday, or send /topic to the bot."); }
    } catch (e) {
      empty("Couldn't load this page", `The file for it is missing or still deploying. Try again in a minute. (${e.message})`);
    }
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  for (const b of document.querySelectorAll(".tabs button")) {
    b.onclick = () => {
      tab = b.dataset.tab;
      const first = tab === "news" ? index.news[0] && `#/news/${index.news[0].date}` : index.topics[0] && `#/topic/${index.topics[0].id}`;
      if (first) location.hash = first; else { renderList(null); route(); }
    };
  }
  picker.onchange = () => { location.hash = picker.value; };
  window.addEventListener("hashchange", route);

  getJSON("data/index.json")
    .then((d) => { index = d; })
    .catch(() => {})
    .finally(route);
})();
