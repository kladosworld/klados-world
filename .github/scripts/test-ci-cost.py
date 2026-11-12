"""Offline scheduling tests for application and security workflows."""

import importlib.util
import itertools
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL = (
    "needs.detect.outputs.python == 'true' && (github.event_name != 'pull_request' || "
    "toJSON(github.event.pull_request.draft) == 'false')"
)
TYPES = {"opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft"}
GROUP = (
    "${{ github.workflow }}-${{ github.event_name }}-"
    "${{ github.event.pull_request.number || github.ref }}"
)


def job(text, name):
    return re.search(
        r"^  " + re.escape(name) + r":\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S
    ).group(1)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.workflows = {
            p.name: p.read_text(encoding="utf-8")
            for p in (ROOT / ".github/workflows").glob("*.yml")
        }
        spec = importlib.util.spec_from_file_location(
            "ci_gate", ROOT / ".github/scripts/ci-gate.py"
        )
        self.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gate)

    def test_all_pr_workflows_handle_ready_and_new_heads(self):
        for name, text in self.workflows.items():
            self.assertIn("group: " + GROUP, text, name)
            self.assertIn(
                "cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text, name
            )
            events = re.search(r"^on:\n(.*?)(?=^[a-z])", text, re.M | re.S).group(1)
            if "  pull_request:" in events:
                types = set(
                    re.search(r"types: \[(.*?)\]", events).group(1).replace(" ", "").split(",")
                )
                self.assertEqual(types, TYPES, name)
            self.assertNotRegex(events, r"paths(?:-ignore)?:")
            self.assertNotIn("pull_request_target", events)

    def test_only_full_test_job_is_deferred_and_final_events_are_full(self):
        text = self.workflows["ci.yml"]
        self.assertEqual(re.search(r"^    if: (.+)$", job(text, "test"), re.M).group(1), FULL)
        for name in ("lint", "typecheck", "package"):
            self.assertEqual(
                re.search(r"^    if: (.+)$", job(text, name), re.M).group(1),
                "needs.detect.outputs.python == 'true'",
            )
        for event in ("push", "merge_group", "workflow_dispatch"):
            self.assertRegex(text, r"(?m)^  " + event + ":")
        for action in TYPES:
            for draft in (True, False, None, "false", 0):
                allowed = json.dumps(draft) == "false"
                self.assertEqual(allowed, draft is False, (action, draft))

    def test_existing_gate_blocks_deferred_tests_and_any_failure(self):
        text = self.workflows["ci.yml"]
        aggregate = job(text, "ci-required")
        self.assertIn("name: ci-required", aggregate)
        self.assertIn("if: always()", aggregate)
        self.assertIn("needs: [detect, lint, typecheck, test, package]", aggregate)
        for states in itertools.product(
            ("success", "failure", "skipped", "cancelled", None), repeat=4
        ):
            needs = {"detect": {"result": "success"}}
            needs.update(
                {
                    name: {"result": value}
                    for name, value in zip(self.gate.PYTHON_JOBS, states, strict=True)
                }
            )
            self.assertEqual(
                not self.gate.evaluate(needs, "true"), all(v == "success" for v in states), states
            )
        needs = {name: {"result": "success"} for name in ("detect", *self.gate.PYTHON_JOBS)}
        needs["detect"]["result"] = "failure"
        self.assertTrue(self.gate.evaluate(needs, "true"))
        del needs["test"]
        self.assertTrue(self.gate.evaluate(needs, "true"))

    def test_security_jobs_and_analysis_are_preserved_on_drafts(self):
        for path, name in [
            ("secret-scan.yml", "gitleaks"),
            ("workflow-lint.yml", "workflow-lint"),
            ("dependency-review.yml", "dependency-review"),
            ("codeql.yml", "analyze"),
        ]:
            self.assertNotRegex(job(self.workflows[path], name), r"(?m)^    if:")
        self.assertIn("security-extended", self.workflows["codeql.yml"])
        self.assertIn("language: [actions, python]", self.workflows["codeql.yml"])
        self.assertIn("fail-on-severity: moderate", self.workflows["dependency-review.yml"])
        self.assertIn("--redact", self.workflows["secret-scan.yml"])
        self.assertIn("--require-hashes", self.workflows["workflow-lint.yml"])


if __name__ == "__main__":
    unittest.main()
