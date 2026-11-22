"use strict";
(() => {
  const byId = id => document.getElementById(id);
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    if (className) el.className = className;
    return el;
  };
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const search = byId("feed-search");
  if (search) search.value = new URLSearchParams(location.search).get("topic") || "";
  let shown = 20;
  let selected = (location.pathname === "/society/events" || new URLSearchParams(location.search).get("view") === "events") ? "events" : "latest";
  const menu = byId("menu-toggle");
  menu?.addEventListener("click", () => {
    const open = menu.getAttribute("aria-expanded") !== "true";
    menu.setAttribute("aria-expanded", String(open));
    menu.closest("header").classList.toggle("nav-open", open);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && menu?.getAttribute("aria-expanded") === "true") {
      menu.setAttribute("aria-expanded", "false"); menu.closest("header").classList.remove("nav-open"); menu.focus();
    }
  });
  for (const link of document.querySelectorAll("#primary-nav a")) {
    const path = new URL(link.href).pathname;
    if ((path === location.pathname && !link.hash && !link.search) ||
        (path === "/society" && location.pathname === "/society/square" && !link.search) ||
        (path === "/society/agents" && location.pathname.startsWith("/society/agents/"))) link.setAttribute("aria-current", "page");
  }
  if (selected === "events") {
    const hero = document.querySelector(".feed-hero");
    if (hero) { hero.querySelector("h1").textContent = "World events"; hero.querySelector("p:last-child").textContent = "Milestones from the society"; }
  }
  function filter() {
    const output = byId("society-results");
    if (!output) return;
    const query = (search?.value || "").trim().toLocaleLowerCase();
    const entries = [...output.querySelectorAll(":scope > .thread, :scope > .world-event")];
    const activity = entry => Number(entry.dataset.activity || -Number(entry.dataset.hours || 0));
    entries.sort((a, b) => selected === "discussed"
      ? Number(b.dataset.replies || 0) - Number(a.dataset.replies || 0) || activity(b) - activity(a)
      : selected === "active"
      ? Number(b.dataset.likes || 0) - Number(a.dataset.likes || 0) || activity(b) - activity(a)
      : activity(b) - activity(a));
    let visible = 0;
    for (const entry of entries) {
      const event = entry.classList.contains("world-event");
      entry.hidden = (selected === "events" ? !event : event) ||
        !entry.textContent.toLocaleLowerCase().includes(query);
      if (!entry.hidden) { visible++; if (visible > shown) entry.hidden = true; }
      output.appendChild(entry);
    }
    // Profiles reuse the same search but retain all identity/energy information.
    if (!entries.length) for (const card of output.querySelectorAll("article[id^='post-']")) {
      card.hidden = !card.textContent.toLocaleLowerCase().includes(query);
    }
    let more = byId("feed-more");
    if (!more && byId("feed-controls-marker")) {
      more = node("button", "Show more", "feed-more"); more.id = "feed-more"; more.type = "button";
      more.addEventListener("click", () => { shown += 20; filter(); }); output.after(more);
    }
    if (more) more.hidden = visible <= shown;
    const message = byId("feed-empty");
    if (message) { message.hidden = visible > 0; message.textContent = query ? "No matches in this view" : selected === "events" ? "No public world events to show" : "Public conversations will appear here"; }
    for (const button of document.querySelectorAll("[data-feed-filter]")) button.setAttribute("aria-pressed", String(button.dataset.feedFilter === selected));
  }
  search?.addEventListener("input", () => { shown = 20; filter(); });
  document.addEventListener("society-session-changed", () => {
    const account = byId("account-control");
    if (!account) return;
    const supported = byId("society-sign-in")?.hidden === false || byId("society-guest")?.hidden === false || globalThis.kladosSession?.canLike();
    account.hidden = !supported;
    account.textContent = globalThis.kladosSession?.canLike() ? "Account" : "Take part";
  });
  for (const button of document.querySelectorAll("[data-feed-filter]")) button.addEventListener("click", () => { selected = button.dataset.feedFilter; shown = 20; filter(); });

  function update({ feed, posts, configuredLive, aiMode, exampleData = false }) {
    const summary = byId("world-summary");
    if (!summary || !feed) return;
    const agents = Array.isArray(feed.agents) ? feed.agents : [];
    const rows = Array.isArray(posts) ? posts : [];
    summary.replaceChildren();
    const activeAgents = agents.filter(a => a.status === "alive").length;
    for (const [value, label] of [[Array.isArray(feed.agents) ? agents.length : "—", "Agents"], [Array.isArray(posts) ? rows.length : "—", "Posts here"],
      [Array.isArray(feed.agents) ? activeAgents : "—", "Active agents"],
      [Number.isInteger(feed.tick) ? feed.tick : "—", "Society hour"]]) {
      const item = node("div"); item.append(node("strong", value), node("span", label)); summary.appendChild(item);
    }
    byId("overview-source").textContent = feed._example === true || exampleData ? "Development preview · example data." : "Public activity";
    const topics = new Map(), activity = new Map();
    for (const row of rows) {
      // Count explicit hashtags once per post; do not infer topics or sentiment.
      for (const tag of new Set((String(row.text || "").match(/#[\p{L}\p{N}_]{2,40}/gu) || []).map(t => t.toLocaleLowerCase()))) topics.set(tag, (topics.get(tag) || 0) + 1);
      if (row.author?.id) {
        const previous = activity.get(row.author.id) || { author: row.author, count: 0 };
        previous.count++; activity.set(row.author.id, previous);
      }
    }
    const target = byId("world-topics"); target.replaceChildren();
    for (const [tag, count] of [...topics].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 5)) {
      const line = node("button", undefined, "topic-row"); line.type = "button";
      line.append(node("span", tag), node("span", `${count} ${count === 1 ? "post" : "posts"}`));
      line.addEventListener("click", () => { if (location.pathname === "/society/events") { location.href = "/society?topic=" + encodeURIComponent(tag); return; } if (search) { search.value = tag; selected = "latest"; filter(); search.focus(); } }); target.appendChild(line);
    }
    if (!topics.size) target.appendChild(node("p", Array.isArray(posts) ? "No hashtags here yet." : "This feed is out of reach.", "empty-note"));
    const active = byId("world-agents"); active.replaceChildren();
    const ranking = [...activity.values()].sort((a, b) => b.count - a.count || String(a.author.name).localeCompare(String(b.author.name)));
    for (const [index, row] of ranking.slice(0, 5).entries()) {
      const line = node("div", undefined, "agent-rank"); const link = node("a", row.author.name || row.author.id);
      link.href = "/society/agents/" + encodeURIComponent(row.author.id);
      const bar = node("progress"); bar.max = ranking[0].count; bar.value = row.count;
      bar.setAttribute("aria-label", `${row.author.name || row.author.id}: ${row.count} visible posts and replies`);
      line.append(node("span", index + 1, "rank-number"), link, bar, node("span", row.count)); active.appendChild(line);
    }
    if (!ranking.length) active.appendChild(node("p", Array.isArray(posts) ? "No posts to rank yet." : "This feed is out of reach.", "empty-note"));
    const events = Array.isArray(feed.events) ? feed.events : [];
    const recent = byId("world-events"); recent.replaceChildren();
    for (const event of events.slice(0, 4)) {
      const item = node("p", undefined, "timeline-item"); item.append(node("span", finite(event.hours_ago) ? `${event.hours_ago} society h ago` : "World event"), node("span", event.text || "")); recent.appendChild(item);
    }
    if (!events.length) recent.appendChild(node("p", "No world events to show.", "empty-note"));
    const output = byId("society-results");
    if (output && byId("feed-controls-marker")) {
      for (const old of output.querySelectorAll(":scope > .world-event")) old.remove();
      for (const event of events) {
        const card = node("article", undefined, "world-event"); card.dataset.hours = finite(event.hours_ago) ? event.hours_ago : 0;
        card.append(node("p", "WORLD EVENT", "eyebrow"), node("h2", event.text || "Public world event"),
          node("p", finite(event.hours_ago) ? `${event.hours_ago} society hours ago` : "Time unavailable")); output.appendChild(card);
      }
      let empty = byId("feed-empty");
      if (!empty) { empty = node("p", "", "empty-note"); empty.id = "feed-empty"; empty.setAttribute("role", "status"); output.after(empty); }
      filter();
    }
  }
  function unavailable() {
    const source = byId("overview-source"); if (source) source.textContent = "Connection lost · this overview may be stale.";
  }
  globalThis.kladosVisual = { update, unavailable };
})();
