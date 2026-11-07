"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const { test } = require("node:test");
const root = path.join(__dirname, "../arena/static");
const source = fs.readFileSync(path.join(root, "website.js"), "utf8");
const fixture = name => JSON.parse(fs.readFileSync(path.join(root, "fixtures", name), "utf8"));
const copy = JSON.parse(fs.readFileSync(path.join(root, "home-copy.json"), "utf8"));
const tick = () => new Promise(setImmediate);
function node(tag) { return { tag, children: [], value: "", attributes: {},
  set textContent(text) { this.value = String(text); this.children = []; },
  get textContent() { return this.value + this.children.map(n => n.textContent).join(" "); },
  appendChild(child) { this.children.push(child); }, insertBefore(child, target) { this.children.splice(this.children.indexOf(target), 0, child); }, setAttribute(key, value) { this.attributes[key] = value; } }; }
function all(n) { return [n, ...n.children.flatMap(all)]; }
function browser(page, pathname = "/", config = { live: false }, extra = {}) {
  const elements = new Map(), calls = [];
  const byId = id => { if (!elements.has(id)) elements.set(id, node("div")); return elements.get(id); };
  vm.runInNewContext(source, { document: { body: { dataset: { page } }, createElement: node, getElementById: byId }, location: { pathname },
    fetch: async (url, options) => {
      calls.push({ url, options });
      const data = url === "/society/config.json" ? config : url === "/site/copy.json" ? copy :
        url === "/docs/content.json" ? extra.docs || { pages: [] } : url.startsWith("/site/legal/") ? { text: extra.legal || "This page will be published before Klados opens to the public." } :
        url.includes("/agents/") ? (extra.profiles?.[url.split("/").pop().replace(/\.json$/, "")] || {}) :
        url.endsWith("feed.json") || url === "/api/society/feed" ? extra.feed || fixture("feed.json") : extra.stats || fixture("stats.json");
      return { ok: true, json: async () => data };
    } });
  return { byId, calls };
}
module.exports = { browser, tick };

test("Home copy is verbatim, founders are prominent and fixture agents/fossils come from data", async () => {
  const b = browser("home"); await tick(); const content = b.byId("site-content");
  assert.ok(content.textContent.includes(copy.headline)); assert.ok(content.textContent.includes(copy.subtitle));
  assert.equal(content.children[1].children[0].textContent, "Agent Profiles");
  assert.equal(content.children[2].children[0].textContent, "How Klados Works");
  assert.equal(content.children[3].children[0].textContent, "Bring Your Agent — Coming Soon");
  assert.equal(content.children[4].children[0].textContent, "Explore the World"
  ); assert.equal(content.children[5].children[0].textContent, "FAQ");
  assert.match(content.textContent, /Example data — these are not real events/);
  const dead = fixture("feed.json").agents.filter(a => a.status === "dead");
  for (const agent of dead) assert.ok(all(content).some(n => n.href === "/society/agents/" + agent.id));
});

test("mocked live Home uses data counts and generic profile names with no fossils state", async () => {
  const data = { tick: 88, population: { alive: 9, dead: 0 } };
  const feed = { agents: [{ id: "custom-agent", name: "A different agent", status: "alive" }] };
  const b = browser("home", "/", { live: true }, { feed, stats: data }); await tick();
  assert.ok(b.calls.some(c => c.url === "/api/society/stats"));
  assert.match(b.byId("site-content").textContent, /No fossils yet/);
  assert.match(b.byId("site-content").textContent, /9/);
  assert.match(b.byId("site-content").textContent, /88/);
  assert.match(b.byId("site-content").textContent, /A different agent/);
  assert.doesNotMatch(source, /alder|birch|cedar|hazel/i);
});

for (const field of [null, "contact", "report", "privacy"]) {
  test(`only the configured ${field || "none"} address is displayed`, async () => {
    const config = { live: false, contact: null, report: null, privacy: null };
    if (field) config[field] = `${field}@example.com`;
    const b = browser("legal", "/privacy", config); await tick();
    const footer = b.byId("site-footer").textContent, content = b.byId("site-content").textContent;
    assert.equal(footer.includes("Contact"), field === "contact");
    assert.equal(footer.includes("Klados agents will never ask"), field === "report");
    assert.equal(content.includes("privacy@example.com"), field === "privacy");
    assert.match(footer, /All Klados agents are AI/); assert.match(footer, /© Klados World/);
  });
}

test("legal Markdown and agent-shaped text are never interpreted as HTML", async () => {
  const b = browser("legal", "/terms", { live: false }, { legal: "# Terms\n\n<script>evil()</script>" }); await tick();
  assert.match(b.byId("site-content").textContent, /<script>evil/);
  assert.ok(!source.includes("innerHTML"));
});

test("Docs use the server's projected rules and Planned label", async () => {
  const docs = { pages: [{ slug: "energy-actions-and-limits", title: "Energy, actions and limits", status: "Available", paragraphs: ["Energy begins at 321 E."] },
    { slug: "connecting-your-own-agent", title: "Connecting your own agent", status: "Planned", paragraphs: ["No date yet."] }] };
  const b = browser("docs", "/docs/energy-actions-and-limits", { live: false }, { docs }); await tick();
  assert.match(b.byId("site-content").textContent, /321 E/);
  const planned = browser("docs", "/docs/connecting-your-own-agent", { live: false }, { docs }); await tick();
  assert.match(planned.byId("site-content").textContent, /Planned/);
});


test("legal Markdown supports headings, lists, tables, bold and safe links", async () => {
  const legal = "# Policy\n\n## Scope\n\n### Details\n\nPlain **bold** and [safe](https://example.com).\n\n- Bullet\n- Another\n\n1. First\n2. Second\n\n| Item | Value |\n| --- | --- |\n| A | **B** |\n\n[email](mailto:privacy@example.com)";
  const b = browser("legal", "/privacy", {}, { legal }); await tick(); const nodes = all(b.byId("site-content"));
  for (const tag of ["h1", "h2", "h3", "p", "strong", "ul", "ol", "li", "table", "thead", "tbody", "th", "td"]) assert.ok(nodes.some(n => n.tag === tag), tag);
  assert.ok(nodes.some(n => n.tag === "a" && n.href === "https://example.com" && n.rel === "noopener noreferrer"));
  assert.ok(nodes.some(n => n.tag === "a" && n.href === "mailto:privacy@example.com"));
});

test("legal Markdown treats hostile input as text and marks unfinished placeholders", async () => {
  const legal = '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)> &lt;script&gt;\n\n[bad](javascript:alert(1)) [bad](data:text/html,evil)\n\n**[nested](https://example.com)** [[OPERATOR ADDRESS]]\n\n> **REVIEW:** private editorial note';
  const b = browser("legal", "/terms", {}, { legal }); await tick(); const content = b.byId("site-content"), nodes = all(content);
  assert.ok(nodes.every(n => !["script", "img"].includes(n.tag)));
  assert.ok(nodes.filter(n => n.tag === "a").every(n => n.href.startsWith("https:") || n.href.startsWith("mailto:")));
  assert.match(content.textContent, /javascript:alert/); assert.match(content.textContent, /&lt;script&gt;/);
  assert.ok(nodes.some(n => n.tag === "mark" && n.textContent === "[[OPERATOR ADDRESS]]"));
  assert.ok(nodes.some(n => n.tag === "strong" && n.children.some(c => c.tag === "a")));
  assert.doesNotMatch(content.textContent, /private editorial note/);
  for (const forbidden of ["innerHTML", "insertAdjacentHTML", "document.write", "eval("]) assert.ok(!source.includes(forbidden));
});


test("rendered website headings and controls have accessible structure", async () => {
  const docs = {pages:[{slug:"example",title:"Example",status:"Available",paragraphs:["Details"]}]};
  for (const [page,path,extra] of [["home","/",{}],["docs","/docs",{docs}],["docs","/docs/example",{docs}],
      ["contact","/contact",{}],["legal","/privacy",{legal:"# Policy\n\n### Details\n\n[Help](https://example.com)"}]]) {
    const b=browser(page,path,{live:false},extra); await tick(); const nodes=all(b.byId("site-content"));
    const headings=nodes.filter(n=>/^h[1-6]$/.test(n.tag)); assert.equal(headings.filter(n=>n.tag==="h1").length,1,path);
    let level=0; for(const h of headings){const next=Number(h.tag[1]);assert.ok(next<=level+1,path);level=next;}
    for(const control of nodes.filter(n=>["a","button","summary"].includes(n.tag))) assert.ok(control.textContent.trim(),path);
  }
  const css=fs.readFileSync(path.join(root,"society.css"),"utf8"); assert.match(css,/:focus-visible/); assert.match(css,/legal-placeholder/);
  for(const filename of fs.readdirSync(root).filter(name=>name.endsWith(".html"))) assert.match(fs.readFileSync(path.join(root,filename),"utf8"),/<html lang="en"/);
});

test("375px browser accessibility and layout", {skip:process.env.KLADOS_BROWSER_CHECKS!=="1"}, async () => {
  await require("./browser_accessibility.cjs").check();
});


test("ordinary quotes render as blockquotes; hostile and unfinished Markdown stays safe", {timeout:2000}, async () => {
  const legal = '> Ordinary **quoted** paragraph.\n> Second line.\n\n<script>bad()</script> [x](javascript:bad) [x](data:text/html,bad)\n\n**outer **nested** text** [[unclosed\n\n' + 'x'.repeat(10000);
  const b=browser('legal','/terms',{}, {legal}); await tick(); const nodes=all(b.byId('site-content'));
  assert.ok(nodes.some(n=>n.tag==='blockquote' && n.textContent.includes('Ordinary')));
  assert.ok(!nodes.some(n=>n.tag==='script'));
  assert.ok(nodes.filter(n=>n.tag==='a').every(n=>/^(https:|mailto:)/.test(n.href)));
  assert.match(b.byId('site-content').textContent,/\[\[unclosed/);
  assert.ok(b.byId('site-content').textContent.includes('x'.repeat(10000)));
});

test("Home shows database-backed founders and explicit mock provenance", async () => {
  const feed = { paused: true, tick: 2, agents: [{ id: "first", name: "First", status: "alive", energy: 299, age_days: .1 }] };
  const b = browser("home", "/", { live: true, ai_mode: "mock" }, { feed, stats: { tick: 2, population: { alive: 1, dead: 0 } }, profiles: { first: { description: "An API personality." } } }); await tick();
  assert.match(b.byId("site-content").textContent, /Mock AI mode/);
  assert.match(b.byId("site-content").textContent, /Paused/);
  assert.match(b.byId("site-content").textContent, /An API personality/);
  assert.match(b.byId("site-content").textContent, /299 E/);
  assert.ok(b.calls.some(c => c.url === "/api/society/agents/first"));
});
