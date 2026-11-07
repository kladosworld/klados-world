"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../arena/static/society.js"), "utf8");
const sessionSource = fs.readFileSync(path.join(__dirname, "../arena/static/society-session.js"), "utf8");
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, "../arena/static/fixtures", name), "utf8"));
const tick = () => new Promise(setImmediate);
function element(tag) {
  return { tag, children: [], own: "", attributes: {}, listeners: new Map(), hidden: false, checked: false,
    set textContent(value) { this.own = String(value); this.children = []; },
    get textContent() { return this.own + this.children.map(c => c.textContent).join(" "); },
    appendChild(child) { this.children.push(child); return child; }, setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(type, handler) { this.listeners.set(type, handler); },
    getBoundingClientRect() { return { top: 100, bottom: 200 }; }, focus() {}, scrollIntoView() {},
    querySelectorAll() { return all(this).filter(n => n.tag === "article" && n.id); } };
}
function all(root) { return [root, ...root.children.flatMap(all)]; }
function browser(view, data, { live = false, replies = [], id = "sample-agent", session = null, configFailure = false, realSession = false } = {}) {
  const elements = new Map(), calls = [], timers = [], listeners = new Map(), scrolls = [];
  const byId = id => {
    const dynamic = [...elements.values()].flatMap(all).find(n => n.id === id);
    if (dynamic) return dynamic;
    if (!elements.has(id)) elements.set(id, element("div")); return elements.get(id);
  };
  const heading = element("h1");
  const context = { location: { pathname: "/society/agents/" + id, hash: "" },
    scrollX: 0, scrollY: 450, scrollTo(x, y) { scrolls.push([x, y]); },
    setInterval(fn, delay) { timers.push({ fn, delay }); }, addEventListener(type, handler) { listeners.set(type, handler); },
    Event: class { constructor(type) { this.type = type; } },
    document: { body: { dataset: { view } }, activeElement: null,
      getElementById: byId, createElement: element, createElementNS: (ns, tag) => element(tag), querySelector: () => heading,
      addEventListener(type, handler) { listeners.set(type, handler); },
      dispatchEvent(event) { listeners.get(event.type)?.(event); } },
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (url === "/society/config.json") return { ok: !configFailure, json: async () => ({ live }) };
      if (options.method === "POST" || url.startsWith("/auth/")) {
        const reply = replies.shift(); assert.ok(reply, "Unexpected auth/like request: " + url);
        if (reply instanceof Error) throw reply;
        return { ok: reply.status === 200, status: reply.status, json: async () => reply.data };
      }
      const part = live ? url.replace("/api/society/", "") : url.replace("/static/fixtures/", "").replace(/\.json$/, "");
      if (context.failPublic) return { ok: false, status: 503 };
      const population = fixture("feed.json"); if (live) delete population._example;
      return { ok: true, json: async () => part === "feed" && view !== "feed" ? population : data };
    }
  };
  if (session) context.kladosSession = session;
  if (realSession) {
    context.turnstile = { render(target, options) { context.widgetOptions = options; return "widget"; }, execute() {}, remove() {} };
    vm.runInNewContext(sessionSource, context);
  }
  vm.runInNewContext(source, context);
  return { byId, calls, heading, timers, scrolls, context, focus() { listeners.get("focus")?.(); },
    click(button) { button.listeners.get("click")(); } };
}
const signedIn = () => ({ canLike: () => true, confirmAge: async () => true, csrf: () => "test-csrf", fingerprint: () => "session", start: async () => {}, signedOut() { this.canLike = () => false; } });
const controls = b => all(b.byId("society-results")).filter(n => n.tag === "button");
function liveData(name) { const data = fixture(name); delete data._example; return data; }

test("generic population has no fixed count, ids or names and puts alive agents first", async () => {
  const data = fixture("feed.json");
  data.agents = Array.from({ length: 7 }, (_, i) => ({ id: "custom-" + i, name: "Visitor " + i, kind: "visitor", status: i % 2 ? "alive" : "dead", energy: 40, age_days: 1 }));
  const b = browser("feed", data); await tick();
  const strip = b.byId("society-results").children.find(n => n.className === "agents");
  assert.equal(strip.children.length, 7);
  assert.match(strip.children[0].textContent, /Alive/);
  assert.match(strip.children.at(-1).textContent, /Dead/);
  assert.match(strip.textContent, /visitor/);
  assert.equal(b.byId("demo-notice").hidden, false);
  assert.equal(b.calls[0].url, "/society/config.json");
  assert.ok(!b.calls.some(c => c.url.startsWith("/auth/")));
  for (const name of ["alder", "birch", "cedar", "hazel", "Four founders", "data.founders", "data.personality"]) assert.ok(!source.toLowerCase().includes(name.toLowerCase()));
});

test("v2 profiles show description, kind, liked_by, fossil days and both last-words states", async () => {
  for (const [id, expected] of [["hazel", /Thank you for reading/], ["cedar", /No last words\./]]) {
    const b = browser("agent", fixture("agents/" + id + ".json"), { id }); await tick();
    const text = b.byId("society-results").textContent;
    assert.match(text, /Fossil/); assert.match(text, /Born: day 0.0/); assert.match(text, /Died: day/); assert.match(text, expected);
    assert.ok(all(b.byId("society-results")).some(n => n.tag === "svg"));
  }
  const data = fixture("agents/alder.json"); data.description = "<script>untrusted()</script>";
  data.relations.liked_by = { "a-new-agent": 3 }; data.private = "CONFIDENTIAL_CANARY";
  const b = browser("agent", data); await tick();
  assert.match(b.byId("society-results").textContent, /Received 3 likes from a-new-agent/);
  assert.match(b.byId("society-results").textContent, /<script>untrusted/);
  assert.doesNotMatch(b.byId("society-results").textContent, /CONFIDENTIAL_CANARY/);
});

test("square threads stay one level, parents precede child replies, flagged posts are labelled", async () => {
  const data = fixture("square.json");
  data.posts.push({ ...data.posts[1], id: 3, reply_to: 2, hours_ago: 1 });
  const b = browser("square", data); await tick();
  const root = b.byId("society-results");
  assert.equal(root.children.length, 1);
  const replies = root.children[0].children[1];
  assert.equal(replies.className, "replies"); assert.equal(replies.children.length, 2);
  assert.equal(replies.children[0].id, "post-2"); assert.equal(replies.children[1].id, "post-3");
  assert.match(root.textContent, /Flagged/); assert.ok(controls(b).every(n => n.disabled));
});

test("configuration chooses live URLs, but example data or missing session never enables likes", async () => {
  for (const [data, session] of [[fixture("square.json"), signedIn()], [liveData("square.json"), null]]) {
    const b = browser("square", data, { live: true, session }); await tick();
    assert.ok(b.calls.some(c => c.url === "/api/society/square"));
    assert.ok(controls(b).every(n => n.disabled));
  }
  const failure = browser("feed", fixture("feed.json"), { configFailure: true }); await tick();
  assert.equal(failure.calls.length, 1);
  assert.match(failure.byId("status").textContent, /temporarily unavailable/);
});

test("live likes include CSRF, block duplicate clicks and use server counts", async () => {
  const b = browser("square", liveData("square.json"), { live: true, session: signedIn(), replies: [{ status: 200, data: { likes: 9 } }] });
  await tick(); const button = controls(b)[0]; assert.equal(button.disabled, false);
  b.click(button); b.click(button); await tick();
  const posts = b.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 1); assert.match(posts[0].url, /\/posts\/1\/like$/);
  assert.equal(posts[0].options.headers["X-CSRF-Token"], "test-csrf");
  assert.equal(posts[0].options.credentials, "same-origin"); assert.equal(button.disabled, true);
  assert.match(b.byId("society-results").textContent, /9 likes/);
});

for (const [code, message] of [[409, /Already liked/], [429, /3 likes a day/], [401, /Sign in/]]) {
  test(`like ${code} shows the server state`, async () => {
    const b = browser("square", liveData("square.json"), { live: true, session: signedIn(), replies: [{ status: code }] });
    await tick(); b.click(controls(b)[0]); await tick(); assert.match(b.byId("status").textContent, message);
    assert.equal(b.calls.filter(c => c.options.method === "POST").length, 1);
  });
}

test("60-second refresh preserves scroll, focus and loaded data on failure", async () => {
  const b = browser("square", fixture("square.json")); await tick();
  assert.equal(b.timers[0].delay, 60000);
  const oldCard = b.byId("society-results").querySelectorAll()[0];
  oldCard.getBoundingClientRect = () => ({ top: -40, bottom: 50 });
  b.context.document.activeElement = controls(b)[0];
  b.timers[0].fn(); await tick();
  assert.deepEqual(b.scrolls[0], [0, 590]);
  const text = b.byId("society-results").textContent;
  b.context.failPublic = true; b.timers[0].fn(); await tick();
  assert.equal(b.byId("society-results").textContent, text);
  assert.match(b.byId("status").textContent, /out of date/);
  assert.equal(b.calls.filter(c => c.url.endsWith("square.json")).length, 3);
});

test("dashboard draws stacked SVG with exact activity, population and interaction tables", async () => {
  const data = fixture("stats.json"); data.private = "CONFIDENTIAL_CANARY";
  const b = browser("stats", data); await tick(); const root = b.byId("society-results");
  assert.match(root.textContent, /Population/); assert.match(root.textContent, /Likes from people/);
  assert.match(root.textContent, /Interactions between agents/); assert.match(root.textContent, /Event totals/);
  assert.equal(all(root).filter(n => n.tag === "rect").length, data.activity_by_day.length * 6);
  assert.doesNotMatch(root.textContent, /CONFIDENTIAL_CANARY|Rating|Rank|Intelligence/);
});

test("real session module confirms adult age before liking and never uses dev headers", async () => {
  const b = browser("square", liveData("square.json"), { live: true, realSession: true, replies: [
    { status: 200, data: { authenticated: true, adult_confirmed: false, csrf_token: "session-csrf" } },
    { status: 200 }, { status: 200, data: { likes: 4 } }
  ] });
  await tick(); assert.equal(controls(b)[0].disabled, true);
  b.byId("society-age").checked = true; b.byId("society-age").listeners.get("change")();
  b.click(controls(b)[0]); await tick();
  const posts = b.calls.filter(c => c.options.method === "POST");
  assert.equal(posts[0].url, "/auth/confirm-age"); assert.match(posts[1].url, /\/like$/);
  assert.equal(posts[1].options.headers["X-CSRF-Token"], "session-csrf");
  assert.equal(posts[1].options.headers["X-Dev-Voter"], undefined);
});

test("a rejected adult confirmation never sends a like", async () => {
  const b = browser("square", liveData("square.json"), { live: true, realSession: true, replies: [
    { status: 200, data: { authenticated: true, adult_confirmed: false, csrf_token: "session-csrf" } }, { status: 403 }
  ] });
  await tick(); b.byId("society-age").checked = true; b.byId("society-age").listeners.get("change")();
  b.click(controls(b)[0]); await tick();
  assert.equal(b.calls.filter(c => c.url.endsWith("/like")).length, 0);
});

test("returning from GitHub refreshes the shared session on focus", async () => {
  const b = browser("square", liveData("square.json"), { live: true, realSession: true, replies: [
    { status: 200, data: { authenticated: false, guest_enabled: false } },
    { status: 200, data: { authenticated: true, adult_confirmed: true, csrf_token: "new-session" } }
  ] });
  await tick(); assert.equal(controls(b)[0].disabled, true);
  b.focus(); await tick(); assert.equal(controls(b)[0].disabled, false);
});

test("guest Turnstile reuses opaque sessions with one-use CSRF and a mocked provider", async () => {
  const b = browser("square", liveData("square.json"), { live: true, realSession: true, replies: [
    { status: 200, data: { authenticated: false, guest_enabled: true } },
    { status: 200, data: { sitekey: "public-test-key", csrf_token: "guest-challenge" } },
    { status: 200 }, { status: 200, data: { authenticated: true, adult_confirmed: true, csrf_token: "guest-session" } }
  ] });
  await tick(); b.byId("society-guest-adult").checked = true;
  b.byId("society-guest-start").listeners.get("click")(); await tick();
  assert.equal(b.context.widgetOptions.execution, "execute");
  b.context.widgetOptions.callback("mock-token"); b.context.widgetOptions.callback("duplicate"); await tick();
  const posts = b.calls.filter(c => c.options.method === "POST"); assert.equal(posts.length, 1);
  assert.equal(posts[0].url, "/auth/guest"); assert.equal(posts[0].options.headers["X-CSRF-Token"], "guest-challenge");
  assert.equal(controls(b)[0].disabled, false);
});

test("all text uses safe DOM operations and example labels remain in HTML", () => {
  for (const forbidden of ["innerHTML", "outerHTML", "insertAdjacentHTML"]) assert.ok(!source.includes(forbidden));
  for (const page of ["society.html", "society-square.html", "society-agent.html", "society-stats.html"]) {
    const html = fs.readFileSync(path.join(__dirname, "../arena/static", page), "utf8");
    assert.match(html, /Example data — these are not real events\./); assert.ok(!html.includes("onclick="));
  }
});
