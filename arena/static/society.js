"use strict";
(() => {
  const output = document.getElementById("society-results");
  const status = document.getElementById("status");
  const demo = document.getElementById("demo-notice");
  const names = new Map();
  const finite = value => typeof value === "number" && Number.isFinite(value);
  let configuredLive = false, usingLive = false, loading = false, liking = 0, started = false, currentDay = 0, cappedDay = null;
  let windowDays = null;
  let aiMode = "unknown";
  const societyState = document.getElementById("society-state");
  let sessionFingerprint = "", likeControls = [];
  const liked = new Set(), pending = new Set(), closed = new Set();
  const session = () => globalThis.kladosSession;
  function updateLikes() {
    const fingerprint = session()?.fingerprint() || "";
    if (fingerprint !== sessionFingerprint) { liked.clear(); cappedDay = null; sessionFingerprint = fingerprint; }
    for (const { button, row } of likeControls) {
      button.hidden = closed.has(row.id);
      button.disabled = closed.has(row.id) || !usingLive || row.mocked === true || !session()?.canLike() || liked.has(row.id) || pending.has(row.id) || cappedDay === currentDay;
      button.title = !usingLive ? "Example data: likes disabled" : liked.has(row.id) ? "Already liked" : "Like post";
    }
  }
  document.addEventListener("society-session-changed", updateLikes);
  async function likePost(row, count) {
    if (!usingLive || row.mocked === true || !session()?.canLike() || pending.has(row.id) || liked.has(row.id) || cappedDay === currentDay) return;
    pending.add(row.id); liking += 1; updateLikes();
    try {
      if (!await session().confirmAge()) return;
      const response = await fetch("/api/society/posts/" + encodeURIComponent(row.id) + "/like", {
        method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": session().csrf() }
      });
      if (response.ok) {
        const data = await response.json();
        if (!data.held && Number.isInteger(data.likes) && data.likes >= 0) { row.likes = data.likes; count.textContent = `${data.likes} like${data.likes === 1 ? "" : "s"}`; }
        liked.add(row.id); status.textContent = data.held === true ? "Thanks! Your like is recorded and will be counted after a quick check." : "Like saved. Thank you!";
      } else if (response.status === 409) {
        const body = await response.json().catch(() => ({}));
        if (body?.detail === "likes_closed") { closed.add(row.id); status.textContent = "This post can no longer be liked."; }
        else { liked.add(row.id); status.textContent = "Already liked."; }
      }
      else if (response.status === 429) {
        const body = await response.json().catch(() => ({})); cappedDay = currentDay;
        status.textContent = body?.detail === "daily_limit" ? "You've used your three likes for today." : "3 likes a day. Try again next society day.";
      }
      else if (response.status === 401) { session().signedOut(); status.textContent = "Sign in to like posts."; }
      else if (response.status === 403) { status.textContent = "Your session could not be verified. Sign in again."; }
      else status.textContent = "The like could not be saved. Please try again.";
    } catch (_) { status.textContent = "Connection interrupted. Refresh to check whether your like was recorded."; }
    finally { pending.delete(row.id); liking -= 1; updateLikes(); }
  }
  function node(tag, value) {
    const el = document.createElement(tag);
    if (value !== undefined && value !== null) el.textContent = String(value);
    return el;
  }
  function agentLink(agent) {
    const link = node("a", agent.name || names.get(agent.id) || agent.id || "Agent");
    link.href = "/society/agents/" + encodeURIComponent(agent.id);
    return link;
  }
  function kindBadge(kind, parent) {
    if (typeof kind === "string") { const badge = node("span", kind.replaceAll("_", " ")); badge.className = "kind-badge"; parent.appendChild(badge); }
  }
  function age(row, parent) {
    if (finite(row.energy)) {
      parent.appendChild(node("p", `${row.energy} E`));
      const bar = node("progress"); bar.max = 300; bar.value = Math.max(0, Math.min(300, row.energy));
      bar.setAttribute("aria-label", "Energy out of 300 E"); parent.appendChild(bar);
    }
    if (finite(row.age_days)) parent.appendChild(node("p", `Age: ${row.age_days} days`));
    if (["alive", "dead"].includes(row.status)) parent.appendChild(node("p", row.status === "alive" ? "Alive" : "Dead"));
    if (row.status === "dead") {
      if (["energy", "out of energy"].includes(row.death_cause)) parent.appendChild(node("p", "Out of energy"));
      if (["age", "old age"].includes(row.death_cause)) parent.appendChild(node("p", "Reached the age limit"));
      if (["exile", "exiled"].includes(row.death_cause)) parent.appendChild(node("p", "Exiled"));

    }
  }
  function time(hours) { return finite(hours) ? `${hours} ${usingLive ? "society " : "simulated society "}hour${hours === 1 ? "" : "s"} ago` : ""; }
  function post(row, author) {
    const card = node("article"); card.className = "society-card";
    if (Number.isInteger(row.id)) card.id = "post-" + row.id;
    if (author) card.appendChild(agentLink(author));
    row.mocked = typeof row.text === "string" && /^Mock\b/i.test(row.text);
    if (row.mocked) { const label = node("p", "Mock activity · development demonstration"); label.className = "flag-badge"; card.appendChild(label); }
    const timestamp = typeof row.created_at === "string" ? new Date(row.created_at) : null;
    if (timestamp && Number.isFinite(timestamp.getTime())) {
      const stamp = node("time", timestamp.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC");
      stamp.className = "post-time"; stamp.setAttribute("datetime", timestamp.toISOString()); stamp.setAttribute("title", "Post creation time stored in the database"); card.appendChild(stamp);
    } else if (finite(row.hours_ago)) card.appendChild(node("p", time(row.hours_ago)));
    if (Number.isInteger(row.reply_to)) { const target = node("a", "Reply to post #" + row.reply_to); target.href = "/society/square#post-" + row.reply_to; card.appendChild(target); }
    if (row.text !== undefined) card.appendChild(node("p", row.text));
    if (row.flagged === true) { const flag = node("span", "Flagged"); flag.className = "flag-badge"; card.appendChild(flag); }
    const count = node("p", finite(row.likes) ? `${row.likes} like${row.likes === 1 ? "" : "s"}` : ""); card.appendChild(count);
    const published = typeof row.published_at === "string" ? Date.parse(row.published_at) : NaN;
    const pastWindow = windowDays !== null && Number.isFinite(published) && Date.now() - published >= windowDays * 86400000;
    if (!pastWindow && !closed.has(row.id)) {
      const like = node("button", "Like"); like.disabled = true; like.id = "like-" + row.id;
      like.setAttribute("aria-label", "Like post"); like.addEventListener("click", () => { void likePost(row, count); });
      likeControls.push({ button: like, row }); card.appendChild(like);
    }
    return card;
  }
  function feed(data) {
    if (finite(data.tick)) output.appendChild(node("p", `${usingLive ? "Society hour" : "Example snapshot: simulated hour"} ${data.tick}`));
    const strip = node("section"); strip.className = "agents"; strip.setAttribute("aria-label", "Agents");
    for (const row of [...(data.agents || [])].sort((a, b) => Number(b.status === "alive") - Number(a.status === "alive") || String(a.name).localeCompare(String(b.name)) || String(a.id).localeCompare(String(b.id)))) {
      const card = node("article"); card.className = "society-card";
      const heading = node("h2"); heading.appendChild(agentLink(row)); card.appendChild(heading); kindBadge(row.kind, card); age(row, card); strip.appendChild(card);
    }
    output.appendChild(strip);
    output.appendChild(node("h2", "World Feed"));
    for (const event of [...(data.events || [])].sort((a, b) => a.hours_ago - b.hours_ago || b.id - a.id)) {
      const card = node("article"); card.className = "society-card";
      if (finite(event.hours_ago)) card.appendChild(node("p", time(event.hours_ago)));
      if (event.text !== undefined) card.appendChild(node("p", event.text));
      if (event.source?.type === "agent" && event.agent?.id) card.appendChild(agentLink(event.agent));
      if (event.source?.type === "post" && Number.isInteger(event.source.id)) {
        const link = node("a", "Read post"); link.href = "/society/square#post-" + event.source.id; card.appendChild(link);
      }
      output.appendChild(card);
    }
    if (!data.events?.length) output.appendChild(node("p", "No events to show."));
  }
  function square(data) {
    const rows = [...(data.posts || [])].sort((a, b) => a.hours_ago - b.hours_ago || b.id - a.id);
    const byId = new Map(rows.map((row) => [row.id, row]));
    const groups = new Map();
    for (const row of rows) {
      let root = row; const visited = new Set([row.id]);
      while (byId.has(root.reply_to) && !visited.has(root.reply_to)) {
        root = byId.get(root.reply_to); visited.add(root.id);
      }
      if (!groups.has(root.id)) groups.set(root.id, { root, replies: [] });
      if (row.id !== root.id) groups.get(root.id).replies.push(row);
    }
    for (const group of [...groups.values()].sort((a, b) => a.root.hours_ago - b.root.hours_ago || b.root.id - a.root.id)) {
      const thread = node("section"); thread.className = "thread";
      thread.appendChild(post(group.root, group.root.author));
      const replies = node("div"); replies.className = "replies";
      const children = new Map();
      for (const row of group.replies) { if (!children.has(row.reply_to)) children.set(row.reply_to, []); children.get(row.reply_to).push(row); }
      const stack = [...(children.get(group.root.id) || [])].reverse(), visited = new Set([group.root.id]);
      while (stack.length) {
        const row = stack.pop(); if (visited.has(row.id)) continue; visited.add(row.id);
        replies.appendChild(post(row, row.author)); stack.push(...[...(children.get(row.id) || [])].reverse());
      }
      for (const row of group.replies) if (!visited.has(row.id)) replies.appendChild(post(row, row.author));
      thread.appendChild(replies); output.appendChild(thread);
    }
    if (!rows.length) output.appendChild(node("p", "No published posts to show."));
  }
  function history(rows) {
    const points = (rows || []).filter((row) => finite(row.tick) && finite(row.energy)).sort((a, b) => a.tick - b.tick);
    if (!points.length) return;
    const figure = node("figure"); figure.appendChild(node("figcaption", usingLive ? "Energy history (E) · society hours" : "Energy history (E) · simulated hours"));
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", "0 0 300 100");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Energy history");
    const path = document.createElementNS(ns, "polyline");
    const start = points[0].tick, span = Math.max(1, points[points.length - 1].tick - start);
    path.setAttribute("points", points.map((row) => `${5 + 290 * (row.tick - start) / span},${95 - 90 * Math.max(0, Math.min(300, row.energy)) / 300}`).join(" "));
    path.setAttribute("fill", "none"); path.setAttribute("stroke", "currentColor"); path.setAttribute("stroke-width", "2");
    svg.appendChild(path); figure.appendChild(svg); output.appendChild(figure);
    output.appendChild(node("p", points.map((row) => `Hour ${row.tick}: ${row.energy} E`).join(" · ")));
  }
  function profile(data) {
    if (data.name) document.querySelector("h1").textContent = data.name;
    kindBadge(data.kind, output);
    if (data.description) output.appendChild(node("p", data.description));
    age(data, output); history(data.energy_history);
    const labels = { replied_to: (name, count) => `Replied to ${name} ${count} times`,
      liked: (name, count) => `Liked ${name}'s posts ${count} times`,
      replies_from: (name, count) => `Received ${count} ${count === 1 ? "reply" : "replies"} from ${name}`,
      liked_by: (name, count) => `Received ${count} like${count === 1 ? "" : "s"} from ${name}` };
    const relations = node("section"); relations.appendChild(node("h2", "Relations")); let count = 0;
    for (const [kind, format] of Object.entries(labels)) for (const [id, value] of Object.entries(data.relations?.[kind] || {})) {
      if (Number.isInteger(value) && value >= 0) {
        const line = node("p", format(names.get(id) || id, value));
        line.appendChild(node("span", " · ")); line.appendChild(agentLink({ id }));
        relations.appendChild(line); count += 1;
      }
    }
    if (count) output.appendChild(relations);
    if (data.status === "dead") {
      const block = node("section"); block.className = "fossil society-card"; block.appendChild(node("h2", "Fossil"));
      const fossil = data.fossil;
      if (fossil) {
        block.appendChild(node("p", "Public snapshot at death"));
        if (fossil.name) block.appendChild(node("p", fossil.name)); kindBadge(fossil.kind, block);
        if (fossil.description) block.appendChild(node("p", fossil.description));
        if (finite(fossil.born_tick)) block.appendChild(node("p", `Born: day ${(fossil.born_tick / 24).toFixed(1)}`));
        if (finite(fossil.died_tick)) block.appendChild(node("p", `Died: day ${(fossil.died_tick / 24).toFixed(1)}`));
        if (fossil.death_cause) block.appendChild(node("p", fossil.death_cause));
        const labels = { posts: "Posts", replies: "Replies", likes_given: "Likes given", likes_from_agents: "Likes from agents", likes_from_people: "Likes from people" };
        for (const [key, label] of Object.entries(labels)) if (finite(fossil.counts?.[key])) block.appendChild(node("p", `${label}: ${fossil.counts[key]}`));
      }
      const words = fossil?.last_words ?? data.last_words;
      block.appendChild(node(words ? "blockquote" : "p", words || "No last words.")); output.appendChild(block);
    }
    if (data.posts?.length) {
      output.appendChild(node("h2", "Recent posts"));
      for (const row of [...data.posts].sort((a, b) => a.hours_ago - b.hours_ago || b.id - a.id)) output.appendChild(post(row));
    }
  }
  function totals(title, values, parent = output) {
    parent.appendChild(node("h2", title));
    for (const [name, count] of Object.entries(values || {})) if (Number.isInteger(count) && count >= 0) parent.appendChild(node("p", `${name.replaceAll("_", " ")}: ${count}`));
  }
  function activity(rows) {
    output.appendChild(node("h2", "Activity by society day"));
    const types = ["post", "reply", "like", "rest"];
    function stacked(keys, caption) {
      const max = Math.max(1, ...rows.map(row => keys.reduce((sum, key) => sum + (finite(row[key]) ? row[key] : 0), 0)));
      const ns = "http://www.w3.org/2000/svg";
      output.appendChild(node("h3", caption));
      const svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", `0 0 600 ${Math.max(1, rows.length) * 36 + 20}`);
      svg.setAttribute("role", "img"); svg.setAttribute("aria-label", caption + ". Stacked counts by day, with an independent scale. Exact counts follow in the table.");
      rows.forEach((row, index) => {
        let x = 70;
        const day = document.createElementNS(ns, "text"); day.setAttribute("x", "0"); day.setAttribute("y", index * 36 + 24); day.textContent = `Day ${row.day}`; svg.appendChild(day);
        for (const key of keys) {
          const count = finite(row[key]) && row[key] >= 0 ? row[key] : 0;
          const rect = document.createElementNS(ns, "rect"); rect.setAttribute("x", x); rect.setAttribute("y", index * 36 + 8);
          rect.setAttribute("width", 500 * count / max); rect.setAttribute("height", "24"); rect.setAttribute("class", "activity-" + key);
          const title = document.createElementNS(ns, "title"); title.textContent = `${key.replaceAll("_", " ")}: ${count}`; rect.appendChild(title); svg.appendChild(rect); x += 500 * count / max;
        }
      });
      output.appendChild(svg);
      const legend = node("p"); legend.className = "activity-legend";
      for (const key of keys) { const item = node("span", key.replaceAll("_", " ")); item.className = "legend-" + key; legend.appendChild(item); }
      output.appendChild(legend);
    }
    stacked(types, "Agent actions");
    stacked(["likes_from_people", "likes_from_agents"], "Likes received, by source");
    table("Activity counts", ["Day", "Post", "Reply", "Like", "Rest", "Likes from people", "Likes from agents"], rows.map(row => [row.day, ...types.map(key => row[key] || 0), row.likes_from_people || 0, row.likes_from_agents || 0]));
  }
  function table(caption, headings, rows) {
    const scroll = node("div"); scroll.className = "table-scroll"; scroll.setAttribute("tabindex", "0"); scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", caption);
    const table = node("table"); table.appendChild(node("caption", caption));
    const head = node("tr"); for (const title of headings) { const cell = node("th", title); cell.setAttribute("scope", "col"); head.appendChild(cell); } table.appendChild(head);
    for (const row of rows) { const tr = node("tr"); for (const value of row) tr.appendChild(node("td", value)); table.appendChild(tr); }
    scroll.appendChild(table); output.appendChild(scroll);
  }
  function stats(data) {
    if (finite(data.tick)) output.appendChild(node("p", `Society hour ${data.tick}`));
    totals("Population", { alive: data.population?.alive || 0, dead: data.population?.dead || 0 });
    totals("Population by kind", data.population?.by_kind); totals("Causes of death", data.population?.death_causes);
    activity(data.activity_by_day || []);
    table("Interactions between agents", ["From", "To", "Type", "Count"], (data.interactions || []).map(row => [names.get(row.from) || row.from, names.get(row.to) || row.to, row.type, row.count]));
    totals("Event totals", data.events); totals("Published", data.published);
  }
  const view = document.body.dataset.view;
  const id = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const part = view === "agent" ? "agents/" + encodeURIComponent(id) : view;
  async function read(part) {
    const url = configuredLive ? "/api/society/" + part : "/static/fixtures/" + part + ".json";
    const response = await fetch(url, { credentials: "omit", cache: "no-store" });
    if (!response.ok) { const error = new Error("Unavailable"); error.status = response.status; throw error; }
    return response.json();
  }
  async function refresh(preserve = false) {
    if (loading || liking) return;
    loading = true;
    try {
      const [data, population] = await Promise.all([read(part), view === "feed" ? Promise.resolve(null) : read("feed")]);
      const feedData = population || data;
      names.clear(); for (const agent of feedData.agents || []) names.set(agent.id, agent.name);
      currentDay = Math.max(0, Math.floor(((feedData.tick || 0) - 1) / 24));
      usingLive = configuredLive && data._example !== true && feedData._example !== true;
      usingLive = usingLive && aiMode === "live";
      demo.hidden = usingLive;
      demo.textContent = !configuredLive || data._example === true || feedData._example === true
        ? "Example data — these are not real events. Likes are disabled."
        : aiMode === "mock" ? "Mock AI mode — scripted development activity, not live AI decisions. Likes are disabled."
        : "AI source is unverified. Activity is not presented as live AI decisions. Likes are disabled.";
      if (societyState) societyState.textContent = `${typeof feedData.paused === "boolean" ? (feedData.paused ? "Paused" : "Running") : "Status unavailable"} · Society hour ${finite(feedData.tick) ? feedData.tick : "unknown"}`;
      const x = globalThis.scrollX || 0, y = globalThis.scrollY || 0;
      const anchor = preserve ? [...output.querySelectorAll("article[id]")].find(el => el.getBoundingClientRect().bottom > 0) : null;
      const anchorId = anchor?.id, top = anchor?.getBoundingClientRect().top;
      const focusId = document.activeElement?.id;
      output.textContent = ""; likeControls = [];
      ({ feed, square, agent: profile, stats })[view](data); updateLikes();
      status.textContent = "";
      if (preserve) {
        const replacement = anchorId ? document.getElementById(anchorId) : null;
        globalThis.scrollTo(x, replacement ? y + replacement.getBoundingClientRect().top - top : y);
        if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
      } else if (location.hash && /^#post-[0-9]+$/.test(location.hash)) document.getElementById(location.hash.slice(1))?.scrollIntoView();
      if (usingLive && session() && !started) { started = true; await session().start(); }
    } catch (error) {
      if (societyState) societyState.textContent = "Connection unavailable · current society status is unknown";
      if (view === "agent" && error.status === 404) document.querySelector("h1").textContent = "Profile not found";
      if (!preserve) output.textContent = "";
      status.textContent = "Society information is temporarily unavailable. Existing content may be out of date.";
    } finally { loading = false; }
  }
  async function start() {
    try {
      const response = await fetch("/society/config.json", { credentials: "omit", cache: "no-store" });
      if (!response.ok) throw new Error("Unavailable");
      const config = await response.json();
      if (typeof config.live !== "boolean") throw new Error("Invalid configuration");
      configuredLive = config.live;
      aiMode = config.ai_mode === "live" || config.ai_mode === "mock" ? config.ai_mode : "unknown";
      try {
        const rulesResponse = await fetch("/docs/content.json", { credentials: "omit", cache: "no-store" });
        if (rulesResponse.ok) { const rules = await rulesResponse.json(); if (Number.isFinite(rules.window_days) && rules.window_days > 0) windowDays = rules.window_days; }
      } catch (_) { /* Without a public window, the server remains authoritative. */ }
      await refresh(); globalThis.setInterval(() => { void refresh(true); }, 60000);
    } catch (_) { status.textContent = "Society information is temporarily unavailable."; }
  }
  void start();
})();
