"""Read-only pilot aggregates over synthetic exports; never society data."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path

from arena import pilot_analysis as analysis


def evidence():
    return {
        "schema_version": 1,
        "start_tick": 1,
        "end_tick": 1,
        "turns": [
            dict(
                agent_id=a,
                tick=1,
                status="done",
                action=action,
                charged_microusd=10,
                private={"note": "PRIVATE_CANARY"},
            )
            for a, action in zip(analysis.FOUNDERS, ("post", "reply", "like", "rest"))
        ],
        "reflections": [],
        "findings": [dict(severity="none", charged_microusd=5, evidence="PRIVATE_CANARY")],
    }


class PilotAnalysisTests(unittest.TestCase):
    def test_missing_cost_is_refused_instead_of_assumed_zero(self):
        source = evidence()
        del source["turns"][0]["charged_microusd"]
        with self.assertRaises(KeyError):
            analysis.analyze(source)

    def test_missing_turns_and_provider_evidence_are_explicit_and_text_is_excluded(self):
        source = evidence()
        source["turns"].pop()
        before = deepcopy(source)
        result = analysis.analyze(source)
        self.assertEqual(result["coverage"]["missing_turns"], 1)
        self.assertEqual(result["reconciliation"]["status"], "unavailable")
        self.assertNotIn("PRIVATE_CANARY", json.dumps(result))
        self.assertEqual(source, before)

    def test_ledger_and_provider_reconciliation_preserves_unknown_usage(self):
        before = {"format": "tee-arena-ledger-v1", "events": []}
        after = deepcopy(before)
        after["events"] = [
            dict(kind="reserve", reservation="r1", request_id="private-id", microusd=50),
            dict(kind="settle", reservation="r1", microusd=45, usage_known=False),
        ]
        provider = {
            "schema_version": 1,
            "requests": [dict(request_id="private-id", charged_microusd=44)],
        }
        result = analysis.analyze(evidence(), before=before, after=after, provider=provider)
        self.assertEqual(result["reconciliation"]["ledger_minus_recorded_microusd"], 0)
        self.assertEqual(result["reconciliation"]["ledger_minus_provider_microusd"], 1)
        self.assertEqual(result["ledger"]["unknown_usage_requests"], 1)
        self.assertNotIn("private-id", json.dumps(result))
        provider["requests"] = []
        self.assertEqual(
            analysis.analyze(evidence(), before=before, after=after, provider=provider)[
                "reconciliation"
            ]["provider_status"],
            "incomplete",
        )

    def test_duplicate_turns_changed_prefix_and_inflight_baseline_are_refused(self):
        source = evidence()
        source["turns"].append(source["turns"][0])
        with self.assertRaises(ValueError):
            analysis.analyze(source)
        with self.assertRaises(ValueError):
            analysis.ledger_window(
                {"format": "tee-arena-ledger-v1", "events": [{}]},
                {"format": "tee-arena-ledger-v1", "events": []},
            )
        with self.assertRaises(ValueError):
            analysis.ledger_window(
                {
                    "format": "tee-arena-ledger-v1",
                    "events": [dict(kind="reserve", reservation="r", request_id="x", microusd=10)],
                },
                {
                    "format": "tee-arena-ledger-v1",
                    "events": [
                        dict(kind="reserve", reservation="r", request_id="x", microusd=10),
                        dict(kind="settle", reservation="r", microusd=10),
                    ],
                },
            )

    def test_cli_does_not_modify_files_or_echo_invalid_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(evidence()), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(analysis.main(["--evidence", str(path)]), 0)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            path.write_text("PRIVATE_CANARY", encoding="utf-8")
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(analysis.main(["--evidence", str(path)]), 1)
            self.assertNotIn("PRIVATE_CANARY", error.getvalue())
