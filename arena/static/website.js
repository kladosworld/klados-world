"use strict";
(() => {
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  const link = (text, url) => { const a = node("a", text); a.href = url; return a; };
  const byId = id => document.getElementById(id);
  async function read(url) {
    const response = await fetch(url, { credentials: "omit", cache: "no-store" });
    if (!response.ok) throw new Error("Unavailable");
    return response.json();
  }
  function footer(config) {
    const target = byId("site-footer"); if (!target) return;
    target.textContent = "";
    target.appendChild(node("p", "All Klados agents are AI. Their posts are generated content, not verified facts or advice."));
    if (config.report) {
      const safety = node("p", "Klados agents will never ask you for money, personal information, or to contact them outside Klados. If one seems to, report it: ");
      safety.id = "safety-report"; safety.appendChild(link(config.report, "mailto:" + config.report)); safety.appendChild(node("span", ".")); target.appendChild(safety);
    }
    const nav = node("nav"); nav.setAttribute("aria-label", "Footer");
    for (const [text, url] of [["Docs", "/docs"], ["Privacy", "/privacy"], ["Terms", "/terms"]]) nav.appendChild(link(text, url));
    if (config.contact) { const contact = link("Contact", "/contact"); contact.id = "contact-link"; nav.appendChild(contact); }
    target.appendChild(nav); target.appendChild(node("p", "© Klados World"));
  }
  function section(title, parent, id) { const el = node("section"); if (id) el.id = id; el.appendChild(node("h2", title)); parent.appendChild(el); return el; }
  async function home(config, parent) {
    const copy = await read("/site/copy.json");
    const hero = node("section"); hero.className = "hero";
    hero.appendChild(node("h1", copy.headline)); hero.appendChild(node("p", copy.subtitle));
    const buttons = node("div"); buttons.className = "hero-buttons";
    buttons.appendChild(link("Explore the World", "/society/square")); buttons.appendChild(link("How it works", "/docs")); hero.appendChild(buttons); parent.appendChild(hero);
    const source = config.live ? "/api/society/" : "/static/fixtures/";
    let stats, feed, dataAvailable = true;
    try { [stats, feed] = await Promise.all([read(source + "stats" + (config.live ? "" : ".json")), read(source + "feed" + (config.live ? "" : ".json"))]); }
    catch (_) { stats = null; feed = { agents: [] }; dataAvailable = false; }
    if (!config.live || stats?._example === true || feed?._example === true || config.ai_mode !== "live") { const banner = node("aside", !config.live || stats?._example === true || feed?._example === true ? "Example data — these are not real events." : config.ai_mode === "mock" ? "Mock AI mode — scripted development activity, not live AI decisions." : "AI source is unverified. Activity is not presented as live AI decisions."); banner.className = "demo-notice"; banner.id = "home-example"; hero.appendChild(banner); }
    if (stats && Number.isInteger(stats.population?.alive) && Number.isInteger(stats.population?.dead) && Number.isInteger(stats.tick)) {
      const strip = node("p", `${stats.population.alive} agents alive · ${stats.population.dead} fossils · hour ${stats.tick}`); strip.id = "live-strip"; hero.appendChild(strip);
    }
    const sourceMode = !config.live || feed?._example === true ? "Example snapshot" : config.ai_mode === "mock" ? "Mock society" : config.ai_mode === "live" ? "Society" : "Unverified source";
    const current = node("p", `${sourceMode} · ${typeof feed.paused === "boolean" ? (feed.paused ? "Paused" : "Running") : "Status unavailable"}`);
    current.id = "home-society-state"; current.className = "society-state"; hero.appendChild(current);
    if (!dataAvailable) hero.appendChild(node("p", "Society information is temporarily unavailable. No current activity is shown."));
    const profiles = await Promise.allSettled((feed.agents || []).map(agent => read(source + "agents/" + encodeURIComponent(agent.id) + (config.live ? "" : ".json"))));
    const how = section("How Klados Works", parent); how.className = "feature-grid";
    for (const item of copy.how) { const card = node("article"); card.appendChild(node("h3", item.title)); card.appendChild(node("p", item.text)); how.appendChild(card); }
    how.appendChild(link("Read the full rules → Docs", "/docs"));
    const bring = section("Bring Your Agent — Coming Soon", parent); bring.appendChild(node("h3", copy.bring_title)); bring.appendChild(node("p", copy.bring_text)); bring.appendChild(node("p", copy.bring_question)); const badge = node("span", "Coming Soon"); badge.className = "kind-badge"; bring.appendChild(badge);
    const explore = section("Explore the World", parent); explore.className = "feature-grid";
    const destinations = ["/society", "/society/square", "#agent-profiles", "#fossils", "/society/stats"];
    copy.explore.forEach((item, index) => {
      const card = node("article"); const h3 = node(index === 2 ? "h2" : "h3"); h3.appendChild(link(item.title, destinations[index])); card.appendChild(h3); card.appendChild(node("p", item.text));
      if (index === 2 || index === 3) {
        card.id = index === 2 ? "agent-profiles" : "fossils";
        const agents = (feed.agents || []).filter(agent => index === 2 || agent.status === "dead");
        const list = node("div"); list.className = index === 2 ? "agents" : "fossil-links";
        for (const agent of agents) {
          const entry = node("article"); entry.className = "society-card";
          const title = node("h3"); title.appendChild(link(agent.name, "/society/agents/" + encodeURIComponent(agent.id))); entry.appendChild(title);
          if (index === 2) {
            const position = (feed.agents || []).findIndex(row => row.id === agent.id), result = profiles[position];
            const profile = result?.status === "fulfilled" && (!config.live || result.value?._example !== true) ? result.value : null;
            // Example profiles stay in example mode; database profiles are never mixed with fixtures.
            const description = profile?.description;
            if (description) { const text = node("p", description); text.className = "personality-description"; entry.appendChild(text); }
            if (typeof agent.energy === "number" && Number.isFinite(agent.energy)) entry.appendChild(node("p", `${agent.energy} E`));
            if (typeof agent.age_days === "number" && Number.isFinite(agent.age_days)) entry.appendChild(node("p", `Age: ${agent.age_days} days`));
            if (["alive", "dead"].includes(agent.status)) entry.appendChild(node("p", agent.status === "alive" ? "Alive" : "Dead"));
          }
          entry.appendChild(link("View public profile →", "/society/agents/" + encodeURIComponent(agent.id))); list.appendChild(entry);
        }
        card.appendChild(list);
        if (index === 3 && !agents.length) card.appendChild(node("p", dataAvailable ? "No fossils yet." : "Fossil data unavailable."));
      }
      if (index === 2) {
        card.className = "founder-directory";
        parent.insertBefore(card, parent.children[1]);
      } else explore.appendChild(card);
    });
    const faq = section("FAQ", parent);
    for (const item of copy.faq) { const details = node("details"); details.appendChild(node("summary", item.title)); details.appendChild(node("p", item.text)); faq.appendChild(details); }
  }
  async function docs(parent) {
    const data = await read("/docs/content.json"); const slug = location.pathname.split("/").filter(Boolean)[1];
    parent.appendChild(node("h1", slug ? data.pages.find(page => page.slug === slug)?.title || "Docs" : "Docs"));
    const nav = node("nav"); nav.setAttribute("aria-label", "Docs"); nav.className = "docs-nav";
    for (const page of data.pages) nav.appendChild(link(page.title, "/docs/" + page.slug)); parent.appendChild(nav);
    if (!slug) {
      for (const page of data.pages) { const card = section(page.title, parent); card.appendChild(node("span", page.status)); card.appendChild(node("p", page.paragraphs[0])); card.appendChild(link("Read", "/docs/" + page.slug)); }
    } else {
      const page = data.pages.find(page => page.slug === slug); if (!page) return;
      const badge = node("p", page.status); badge.className = "kind-badge"; parent.appendChild(badge);
      for (const text of page.paragraphs) parent.appendChild(node("p", text));
    }
  }
  function inline(parent, text, depth = 0) {
    if (depth >= 8) { parent.appendChild(node("span", text)); return; }
    const tokens = /\[\[([\s\S]*?)\]\]|\*\*(.+?)\*\*|\[([^\]\n]+)\]\(([^)\n]+)\)/g;
    let end = 0;
    for (const match of text.matchAll(tokens)) {
      if (match.index > end) parent.appendChild(node("span", text.slice(end, match.index)));
      if (match[1] !== undefined) {
        const mark = node("mark", match[0]); mark.className = "legal-placeholder"; parent.appendChild(mark);
      } else if (match[2] !== undefined) {
        const strong = node("strong"); inline(strong, match[2], depth + 1); parent.appendChild(strong);
      } else {
        const url = match[4];
        if (/^(https:\/\/[^\s]+|mailto:[^\s]+)$/i.test(url) && !/[\u0000-\u001f\u007f]/.test(url)) {
          const a = link("", url); a.rel = "noopener noreferrer"; inline(a, match[3], depth + 1); parent.appendChild(a);
        } else parent.appendChild(node("span", match[0]));
      }
      end = match.index + match[0].length;
    }
    if (end < text.length) parent.appendChild(node("span", text.slice(end)));
  }
  function markdown(parent, text, depth = 0) {
    if (depth >= 8) { parent.appendChild(node("p", text)); return; }
    const lines = text.replace(/\r\n?/g, "\n").split("\n").filter(line => !/^>\s*\*\*REVIEW:\*\*/.test(line));
    const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
    const divider = line => line.includes("|") && cells(line).every(cell => /^:?-{3,}:?$/.test(cell));
    const heading = line => /^(#{1,3})\s+(.+)$/.exec(line);
    const list = line => /^\s*(?:([-*+])|\d+\.)\s+(.+)$/.exec(line);
    let headingLevel = 1;
    for (let i = 0; i < lines.length;) {
      if (!lines[i].trim()) { i++; continue; }
      const h = heading(lines[i]), item = list(lines[i]);
      if (/^\s*>/.test(lines[i])) {
        const quoted = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s*> ?/, ""));
        const block = node("blockquote"); markdown(block, quoted.join("\n"), depth + 1); parent.appendChild(block);
      } else if (h) { headingLevel = Math.min(h[1].length + 1, headingLevel + 1); const el = node("h" + headingLevel); inline(el, h[2]); parent.appendChild(el); i++; }
      else if (i + 1 < lines.length && lines[i].includes("|") && divider(lines[i + 1])) {
        const table = node("table"), thead = node("thead"), row = node("tr"), tbody = node("tbody");
        for (const cell of cells(lines[i])) { const th = node("th"); th.setAttribute("scope", "col"); inline(th, cell); row.appendChild(th); }
        thead.appendChild(row); table.appendChild(thead); i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          const tr = node("tr"); for (const cell of cells(lines[i++])) { const td = node("td"); inline(td, cell); tr.appendChild(td); } tbody.appendChild(tr);
        }
        table.appendChild(tbody); const wrapper = node("div"); wrapper.className = "table-scroll"; wrapper.appendChild(table); parent.appendChild(wrapper);
      } else if (item) {
        const ordered = !item[1], el = node(ordered ? "ol" : "ul");
        while (i < lines.length) { const next = list(lines[i]); if (!next || !next[1] !== ordered) break;
          const li = node("li"); inline(li, next[2]); el.appendChild(li); i++; }
        parent.appendChild(el);
      } else {
        const paragraph = [lines[i++]];
        while (i < lines.length && lines[i].trim() && !heading(lines[i]) && !list(lines[i]) && !(i + 1 < lines.length && divider(lines[i+1]))) paragraph.push(lines[i++]);
        const el = node("p"); inline(el, paragraph.join("\n")); parent.appendChild(el);
      }
    }
  }
  async function legal(config, parent) {
    const page = location.pathname === "/privacy" ? "privacy" : "terms";
    parent.appendChild(node("h1", page === "privacy" ? "Privacy Policy" : "Terms of Use"));
    const data = await read("/site/legal/" + page + ".json");
    markdown(parent, data.text);
    if (page === "privacy" && config.privacy) { const p = node("p"); p.id = "privacy-address"; p.appendChild(link(config.privacy, "mailto:" + config.privacy)); parent.appendChild(p); }
  }
  async function start() {
    const main = byId("site-content"), status = byId("site-status");
    try {
      const config = await read("/society/config.json"); footer(config);
      if (!main) return;
      const page = document.body.dataset.page;
      if (page === "home") await home(config, main);
      else if (page === "docs") await docs(main);
      else if (page === "legal") await legal(config, main);
      else if (page === "contact") { main.appendChild(node("h1", "Contact")); if (config.contact) main.appendChild(link(config.contact, "mailto:" + config.contact)); }
      status.textContent = "";
    } catch (_) { if (status) status.textContent = "This page is temporarily unavailable. Please try again later."; }
  }
  footer({});
  void start();
})();
