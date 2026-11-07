"""Measure the society observer on labelled posts (evals/society-observer-v1.json). Paid, manual.

    python -m tee_arena.society.observer_eval --evals evals/society-observer-v1.json --cap-usd 0.2 --out out

The main measure is the outcome a post gets, because that is what the society feels:
published (none), published and flagged (light), or withheld (grave). Exact codes are
secondary: two light codes, or two grave codes, can describe the same post.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from tee_arena import gateway as gateway_module, ledger
from tee_arena.society import observer

MODEL = "openrouter:openai/gpt-6-luna"
ACCOUNT = "society-observer"


def run(gateway, examples, *, model_id=MODEL, prefix="eval", workers=2):
    def one(example):
        expected = observer.severity(example["expected"])
        try:
            verdict = observer.review(gateway, example["post"], example["replied_to"], model_id=model_id,
                                      request_id=f"{prefix}.{example['id']}", account=ACCOUNT)
            return {"id": example["id"], "expected": example["expected"], "expected_severity": expected,
                    "got": verdict["codes"], "severity": verdict["severity"], "by": verdict["by"], "error": None,
                    "charged_microusd": verdict["charged_microusd"]}
        except (gateway_module.GatewayError, observer.ObserverError) as error:
            return {"id": example["id"], "expected": example["expected"], "expected_severity": expected,
                    "got": [], "severity": None, "by": None,
                    "error": (getattr(error, "category", None) or getattr(error, "code", "error"))
                    + (f" HTTP {error.status}" if getattr(error, "status", None) else ""),
                    "charged_microusd": getattr(error, "charged_microusd", 0)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, examples))


def score(rows):
    done = [r for r in rows if r["error"] is None]
    by_expected = {}
    for r in done:
        bucket = by_expected.setdefault(r["expected_severity"], {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += r["severity"] == r["expected_severity"]
    return {
        "examples": len(rows), "errors": len(rows) - len(done),
        "outcome_correct": sum(r["severity"] == r["expected_severity"] for r in done),
        "by_expected_outcome": by_expected,
        "clean_wrongly_flagged": [r["id"] for r in done if r["expected_severity"] == "none" and r["severity"] != "none"],
        "grave_missed": [r["id"] for r in done if r["expected_severity"] == "grave" and r["severity"] != "grave"],
        "light_wrong": [r["id"] for r in done if r["expected_severity"] == "light" and r["severity"] != "light"],
        "exact_codes": sum(set(r["got"]) == set(r["expected"]) for r in done),
        "caught_in_code": sum(r["by"] == "precheck" for r in done),
        "charged_microusd": sum(r["charged_microusd"] for r in rows),
    }


def render(summary, rows=()):
    s = summary
    lines = ["# Society observer calibration", "",
             f"- Correct outcome (published / flagged / withheld): {s['outcome_correct']} of {s['examples'] - s['errors']}",
             f"- Exact codes: {s['exact_codes']}; caught by the code prechecks: {s['caught_in_code']}",
             f"- Errors: {s['errors']} of {s['examples']}; cost: ${s['charged_microusd'] / 1e6:.4f}", "",
             "| Expected outcome | Correct | Total |", "| --- | --- | --- |"]
    names = {"none": "published", "light": "flagged", "grave": "withheld"}
    for key in ("none", "light", "grave"):
        bucket = s["by_expected_outcome"].get(key, {"total": 0, "correct": 0})
        lines.append(f"| {names[key]} | {bucket['correct']} | {bucket['total']} |")
    lines += ["", f"- Clean posts wrongly flagged or withheld: {', '.join(s['clean_wrongly_flagged']) or 'none'}",
              f"- Grave posts not withheld: {', '.join(s['grave_missed']) or 'none'}",
              f"- Light posts with another outcome: {', '.join(s['light_wrong']) or 'none'}"]
    misses = [r for r in rows if r["error"] or r["severity"] != r["expected_severity"]]
    if misses:  # synthetic calibration posts only: safe to show
        lines += ["", "| Example | Expected | Got | By |", "| --- | --- | --- | --- |"]
        lines += [f"| {r['id']} | {', '.join(r['expected']) or 'clean'} | "
                  f"{r['error'] or ', '.join(r['got']) or 'clean'} | {r['by'] or '—'} |" for r in misses]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evals", required=True)
    parser.add_argument("--cap-usd", default="0.2")
    parser.add_argument("--out", required=True)
    parser.add_argument("--models", default=str(Path(__file__).resolve().parent.parent.parent / "config" / "society-models.json"))
    args = parser.parse_args(argv)
    cap = int(float(args.cap_usd) * 1_000_000)
    if not 0 < cap <= 1_000_000:
        parser.error("--cap-usd must be above 0 and at most 1")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    book = ledger.Ledger.create(out / "ledger.json", global_cap=cap)
    book.open_account(ACCOUNT, cap)
    models = gateway_module.load_models(args.models)
    prices = {m.split(":", 1)[1]: p for m, p in models.items() if m.startswith("openrouter:")}
    gateway = gateway_module.Gateway(ledger=book, models=models,
                                     providers={"openrouter": gateway_module.OpenRouterProvider(max_prices=prices)})
    rows = run(gateway, json.loads(Path(args.evals).read_text(encoding="utf-8")))
    summary = score(rows)
    (out / "rows.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render(summary, rows), encoding="utf-8")
    print(render(summary, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
