"""Aggregate CI gate behind the single required status check "ci-required".

Reads the `needs` context as JSON from stdin, and the detect decision from the
PYTHON_REQUIRED environment variable ("true" or "false").

Fail-closed rules:
  * detect must succeed.
  * If Python CI is required, every Python job must be "success". A "skipped",
    "cancelled" or "failure" result fails the gate, so these jobs can never be
    skipped silently.
  * If Python CI is not required (bootstrap only), every Python job must be "skipped".
  * Any unexpected job or value fails the gate.
"""

import json
import os
import sys

PYTHON_JOBS = ("lint", "typecheck", "test", "package")


def evaluate(needs: dict, python_required: str) -> list[str]:
    errors = []
    detect = needs.get("detect", {}).get("result")
    if detect != "success":
        errors.append(f"detect: {detect}")
    if python_required not in ("true", "false"):
        errors.append(f"python_required has an unexpected value: {python_required!r}")
        return errors
    expected = "success" if python_required == "true" else "skipped"
    for job in PYTHON_JOBS:
        result = needs.get(job, {}).get("result")
        if result != expected:
            errors.append(f"{job}: {result} (expected {expected})")
    unknown = set(needs) - {"detect", *PYTHON_JOBS}
    if unknown:
        errors.append(f"unexpected jobs in needs: {sorted(unknown)}")
    return errors


def main() -> int:
    needs = json.load(sys.stdin)
    python_required = os.environ.get("PYTHON_REQUIRED", "")
    print(f"python_required: {python_required}")
    for name, info in sorted(needs.items()):
        print(f"{name}: {info.get('result')}")
    errors = evaluate(needs, python_required)
    if errors:
        print("::error::Required CI did not pass: " + "; ".join(errors))
        return 1
    print("ci-required: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
