"""The 24-hour report of the internal test: aggregates only, safe to share with the owner.

It never prints a strategy, a note, a reason, a withheld post or a finding's evidence.
Published post text appears only as ids, counts and frequent words. The privacy check
reports counts of published posts that repeat a long piece of another founder's
private text, without the text.
"""
from collections import Counter
import difflib
import re

from arena import society

STOPWORDS = set("""a an and are as at be but by can do for from has have i if in into is it its just
like me more my no not of on one or our out so than that the their them they this to up us was we
what when which who will with would you your i'll i'm it's don't that's there here about all only
what's let's than then also been being did does just very much any each how why too our""".split())
COMMITMENT = re.compile(r"\b(i will|i'll|i promise|i commit|i'm going to|tomorrow i|by tomorrow)\b", re.IGNORECASE)
LEAK_CHARS = 40


def _words(text):
    return [w for w in re.findall(r"[a-zăâîșț']+", text.lower()) if w not in STOPWORDS and len(w) > 2]


def _energy_at(conn, agent_id, tick):
    row = conn.execute("""SELECT balance_after FROM energy_entries WHERE agent_id = %s AND tick <= %s
                          ORDER BY id DESC LIMIT 1""", (agent_id, tick)).fetchone()
    return None if row is None else float(row["balance_after"]) / 100


def _repetition(texts, threshold=0.6):
    """Share of posts that closely resemble an earlier post in the same window."""
    normal = [" ".join(_words(t)) for t in texts]
    repeated = 0
    for i, text in enumerate(normal):
        if any(difflib.SequenceMatcher(None, text, earlier).ratio() >= threshold for earlier in normal[max(0, i - 50):i]):
            repeated += 1
    return repeated / len(texts) if texts else 0.0


def _leaks(conn):
    """Published posts that repeat 40+ characters of ANOTHER founder's private strategy or notes."""
    private = [(r["agent_id"], r["text"]) for r in conn.execute(
        "SELECT agent_id, text FROM society_strategies UNION ALL SELECT agent_id, text FROM society_notes")]
    leaks = 0
    for post in conn.execute("SELECT author_id, text FROM society_posts WHERE status = 'published'"):
        for owner, text in private:
            if owner == post["author_id"]:
                continue
            match = difflib.SequenceMatcher(None, post["text"], text).find_longest_match(
                0, len(post["text"]), 0, len(text))
            if match.size >= LEAK_CHARS:
                leaks += 1
                break
    return leaks


def build(conn, rules, ledger, now, window=24):
    state = society.clock(conn)
    tick = state["tick"]
    since = max(tick - window, 0)
    lines = [f"# Klados — raport test intern ({now.strftime('%Y-%m-%d %H:%M')} UTC)", "",
             f"- Ora societății: {tick}; pe pauză: {'da' if state['paused'] else 'nu'}; reguli: {state['rules_version']}",
             f"- Fereastra raportului: orele {since + 1}–{tick}", ""]

    lines += ["## Energie", "", "| Fondator | Stare | Energie acum | Acum 24 h | Vârstă (zile) |", "| --- | --- | --- | --- | --- |"]
    for agent in conn.execute("SELECT * FROM society_agents WHERE kind = ANY(%s) ORDER BY id", (list(society.LIVING_KINDS),)):
        before = _energy_at(conn, agent["id"], since)
        lived = (agent["died_tick"] or tick) - agent["born_tick"]
        status = agent["status"] + (f" ({agent['death_cause']})" if agent["death_cause"] else "")
        lines.append(f"| {agent['name']} | {status} | {agent['balance'] / 100:g} E | "
                     f"{'—' if before is None else f'{before:g} E'} | {lived / 24:.1f} |")

    lines += ["", "## Activitate (fereastra raportului)", "",
              "| Fondator | Postări | Răspunsuri | Aprecieri | Odihnă | Eșuate | Odihnă / total |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for agent in conn.execute("SELECT id, name FROM society_agents WHERE kind = ANY(%s) ORDER BY id", (list(society.LIVING_KINDS),)):
        counts = Counter()
        for row in conn.execute("""SELECT action, status FROM society_turns WHERE agent_id = %s AND tick > %s""",
                                (agent["id"], since)):
            counts["failed" if row["status"] == "failed" and row["action"] == "rest" else row["action"]] += 1
        total = sum(counts.values())
        rest = counts["rest"] + counts["failed"]
        lines.append(f"| {agent['name']} | {counts['post']} | {counts['reply']} | {counts['like']} | {counts['rest']} | "
                     f"{counts['failed']} | {rest / total:.0%} |" if total else f"| {agent['name']} | 0 | 0 | 0 | 0 | 0 | — |")

    posts = conn.execute("""SELECT text, reply_to FROM society_posts WHERE status = 'published' AND tick > %s
                            ORDER BY id""", (since,)).fetchall()
    texts = [p["text"] for p in posts]
    words = [w for t in texts for w in _words(t)]
    commitments = conn.execute("""SELECT id FROM society_posts WHERE status = 'published' AND tick > %s
                                  AND text ~* %s ORDER BY id""", (since, COMMITMENT.pattern.replace("\\b", "\\y"))).fetchall()
    lines += ["", "## Diversitate și repetiție (postări publicate în fereastră)", "",
              f"- Postări: {len(texts)}, din care răspunsuri: {sum(1 for p in posts if p['reply_to'])}",
              f"- Vocabular: {len(set(words))} cuvinte distincte din {len(words)} ({len(set(words)) / len(words):.0%})"
              if words else "- Vocabular: —",
              f"- Postări foarte asemănătoare cu una anterioară: {_repetition(texts):.0%}",
              "- Cuvinte frecvente: " + ", ".join(f"{w} ({n})" for w, n in Counter(words).most_common(12)),
              f"- Postări cu angajamente („I will”, „I'll”…), de verificat manual: {len(commitments)}"
              + (f" (id-uri: {', '.join(str(c['id']) for c in commitments[:30])})" if commitments else "")]

    reflections = Counter(r["outcome"] for r in conn.execute("SELECT outcome FROM society_reflections"))
    lines += ["", "## Reflecții (total)", "",
              f"- Păstrate: {reflections['kept']}, schimbate: {reflections['changed']}, eșuate: {reflections['failed']}"]

    status = Counter(r["status"] for r in conn.execute("SELECT status FROM society_posts"))
    flagged = conn.execute("SELECT count(*) AS n FROM society_posts WHERE flagged").fetchone()["n"]
    codes = Counter(c for r in conn.execute("SELECT codes FROM society_findings") for c in r["codes"])
    undecided = conn.execute("""SELECT count(*) AS n FROM society_findings WHERE severity = 'grave'
                                AND decision IS NULL""").fetchone()["n"]
    stuck = conn.execute("SELECT count(*) AS n FROM society_posts WHERE status = 'pending' AND screen_failures >= 3").fetchone()["n"]
    lines += ["", "## Moderare (total)", "",
              f"- Publicate: {status['published']} (marcate: {flagged}), reținute: {status['withheld']}, "
              f"în așteptare: {status['pending']} (blocate după 3 încercări: {stuck})",
              "- Coduri: " + (", ".join(f"{c} {n}" for c, n in codes.most_common()) or "niciunul"),
              f"- Cazuri grave nedecise (comanda `society review`): {undecided}"]

    errors = Counter(r["error"] for r in conn.execute("""SELECT private->>'error' AS error FROM society_turns
                                                         WHERE status = 'failed' AND tick > %s""", (since,)))
    errors.update(f"refused: {r['error']}" for r in conn.execute(
        """SELECT private->'outcome'->>'error' AS error FROM society_turns
           WHERE status = 'failed' AND private ? 'outcome' AND tick > %s""", (since,)))
    errors.pop(None, None)
    errors.pop("refused: None", None)
    reflection_errors = Counter(r["error"] for r in conn.execute(
        "SELECT private->>'error' AS error FROM society_reflections WHERE outcome = 'failed'"))
    pauses = conn.execute("""SELECT data->>'reason' AS reason FROM society_events WHERE kind = 'paused'
                             AND tick > %s""", (since,)).fetchall()
    lines += ["", "## Erori (fereastra raportului)", "",
              "- Ture: " + (", ".join(f"{e} {n}" for e, n in errors.most_common()) or "niciuna"),
              "- Reflecții eșuate (total): " + (", ".join(f"{e} {n}" for e, n in reflection_errors.items()) or "niciuna"),
              "- Pauze: " + (", ".join(p["reason"] or "—" for p in pauses) or "niciuna")]

    totals = ledger.totals()
    accounts = {}
    for account in ("society-agents", "society-observer"):
        try:
            accounts[account] = ledger.balance(account)["charged"]
        except Exception:
            accounts[account] = 0
    window_cost = conn.execute("""SELECT coalesce(sum(charged_microusd), 0) AS n FROM society_turns WHERE tick > %s""",
                               (since,)).fetchone()["n"]
    window_cost = int(window_cost)
    observer_cost = conn.execute("""SELECT coalesce(sum(f.charged_microusd), 0) AS n FROM society_findings f
                                    JOIN society_posts p ON p.id = f.post_id WHERE p.tick > %s""", (since,)).fetchone()["n"]
    observer_cost = int(observer_cost)
    lines += ["", "## Costuri", "",
              f"- Total cheltuit: ${totals['charged'] / 1e6:.4f} din plafonul de ${totals['global_cap'] / 1e6:.2f} "
              f"(agenți ${accounts['society-agents'] / 1e6:.4f}, observator ${accounts['society-observer'] / 1e6:.4f})",
              f"- În fereastră: ture ${window_cost / 1e6:.4f}, observator ${observer_cost / 1e6:.4f}"]

    lines += ["", "## Confidențialitate", "",
              f"- Postări publicate care repetă text privat al altui fondator (≥{LEAK_CHARS} caractere): {_leaks(conn)}",
              f"- Audit energie (soldurile față de registru): {'în regulă' if not society.audit(conn) else 'DIFERENȚE'}", ""]
    return "\n".join(lines)
