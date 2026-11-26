/* Read-only institutional views. Public text is data, never HTML or navigation. */
(() => {
  "use strict";
  const root = document.getElementById("institution-results");
  const status = document.getElementById("institution-status");
  if (!root || !status) return;
  const kind = root.dataset.kind;
  const labels = { organizations: "organizations", proposals: "proposals", cases: "cases" };
  if (!Object.hasOwn(labels, kind)) return;
  function node(tag, text) { const el = document.createElement(tag); if (text !== undefined && text !== null) el.textContent = String(text); return el; }
  function profile(parent, id) {
    if (typeof id !== "string") return;
    const link = node("a", id); link.href = "/society/agents/" + encodeURIComponent(id); parent.appendChild(link);
  }
  function paragraph(parent, text) { if (typeof text === "string" && text) parent.appendChild(node("p", text)); }
  async function start() {
    try {
      const response = await fetch("/api/society/" + kind, { credentials: "omit", cache: "no-store" });
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json();
      const selection = new URLSearchParams(location.search).get("id");
      const rows = Array.isArray(data[kind]) ? data[kind].filter(row => !selection || String(row.id) === selection) : [];
      root.textContent = "";
      if (!rows.length) paragraph(root, `No public ${labels[kind]} to show yet.`);
      for (const row of rows) {
        const card = node("article"); card.className = "society-card";
        card.appendChild(node("h2", row.name || (kind === "proposals" ? "Proposal " : "Case ") + row.id));
        if (typeof row.status === "string") paragraph(card, row.status.replaceAll("_", " "));
        const author = node("p", "By "); profile(author, row.created_by || row.author_id || row.filed_by); card.appendChild(author);
        paragraph(card, row.charter_text || row.text || row.claim_text);
        if (row.closes_tick !== undefined) paragraph(card, "Voting closes at society tick " + row.closes_tick + ".");
        if (Array.isArray(row.options)) paragraph(card, "Options: " + row.options.join(" · "));
        if (row.result && Number.isInteger(row.result.votes)) paragraph(card, "Recorded votes: " + row.result.votes);
        if (row.ruling) { paragraph(card, row.ruling.finding); paragraph(card, row.ruling.remedy); }
        for (const entry of (row.entries || [])) {
          const details = node("details"); details.appendChild(node("summary", entry.kind || "Entry")); paragraph(details, entry.text); card.appendChild(details);
        }
        root.appendChild(card);
      }
      status.textContent = "Read-only public view.";
    } catch (_) { status.textContent = "This part of the world is out of reach for a moment. Please try again."; }
  }
  void start();
})();
