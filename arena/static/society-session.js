"use strict";
(() => {
  const byId = id => document.getElementById(id);
  const panel = byId("society-auth");
  if (!panel) return;
  let session = { authenticated: false }, active = false, busy = false, widget = null, loader = null, challenge = "";
  const message = text => { byId("session-status").textContent = text; };
  function changed() {
    panel.hidden = !active;
    byId("society-sign-in").hidden = !active || !!session.authenticated;
    byId("society-guest").hidden = !active || !!session.authenticated || !session.guest_enabled;
    byId("society-age-label").hidden = !active || !session.authenticated || !!session.adult_confirmed;
    byId("society-guest-start").disabled = busy || !byId("society-guest-adult").checked;
    byId("society-guest-adult").disabled = busy;
    document.dispatchEvent(new Event("society-session-changed"));
  }
  async function refresh() {
    if (!active || busy) return;
    try {
      const response = await fetch("/auth/session", { credentials: "same-origin", cache: "no-store" });
      session = response.ok ? await response.json() : { authenticated: false };
      changed();
    } catch (_) { message("Could not check sign-in. Please try again."); }
  }
  function failed(text) {
    challenge = ""; busy = false;
    if (widget !== null) globalThis.turnstile.remove(widget);
    widget = null; message(text); changed();
  }
  async function finish(token) {
    if (!busy || !challenge || !byId("society-guest-adult").checked) return;
    const csrf = challenge; challenge = "";
    try {
      const response = await fetch("/auth/guest", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ token, adult_confirmed: true }) });
      if (!response.ok) { failed("Guest verification failed. Please try again."); return; }
      if (widget !== null) globalThis.turnstile.remove(widget);
      widget = null; busy = false; await refresh(); message("Ready to like posts.");
    } catch (_) { failed("Connection interrupted. Reload to check your session."); }
  }
  function loadWidget() {
    if (globalThis.turnstile) return Promise.resolve();
    if (!loader) loader = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      script.async = true; script.onload = resolve; script.onerror = reject; document.head.appendChild(script);
    });
    return loader;
  }
  byId("society-guest-start").addEventListener("click", async () => {
    if (!active || busy || !byId("society-guest-adult").checked || session.authenticated) return;
    busy = true; changed(); message("Checking your guest session…");
    try {
      const response = await fetch("/auth/guest", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) { failed("Guest access is unavailable. Please sign in with GitHub."); return; }
      const data = await response.json(); challenge = data.csrf_token;
      await loadWidget();
      widget = globalThis.turnstile.render(byId("society-turnstile"), {
        sitekey: data.sitekey, action: "guest-vote", execution: "execute", appearance: "interaction-only",
        callback: token => { void finish(token); },
        "error-callback": () => { failed("The check failed. Please try again."); return true; },
        "expired-callback": () => failed("The check expired. Please try again."),
        "timeout-callback": () => failed("The check timed out. Please try again.")
      });
      globalThis.turnstile.execute(widget);
    } catch (_) { loader = null; failed("The guest check could not load. Please try again."); }
  });
  byId("society-guest-adult").addEventListener("change", changed);
  byId("society-age").addEventListener("change", changed);
  globalThis.kladosSession = {
    start() { active = true; changed(); return refresh(); },
    refresh,
    canLike() { return active && !busy && session.authenticated === true && typeof session.csrf_token === "string" &&
      (session.adult_confirmed === true || byId("society-age").checked); },
    csrf() { return session.csrf_token; },
    fingerprint() { return session.authenticated ? session.csrf_token : ""; },
    signedOut() { session = { authenticated: false, guest_enabled: session.guest_enabled }; changed(); message("Sign in to like posts."); },
    async confirmAge() {
      if (!this.canLike()) return false;
      if (session.adult_confirmed) return true;
      busy = true; changed();
      try {
        const response = await fetch("/auth/confirm-age", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": session.csrf_token } });
        if (!response.ok) { message("Sign in again and confirm you are 18 or older."); return false; }
        session.adult_confirmed = true; return true;
      } catch (_) { message("Age confirmation could not be saved. Please try again."); return false; }
      finally { busy = false; changed(); }
    }
  };
  globalThis.addEventListener("focus", () => { void refresh(); });
})();
