"""TASK-053: budget-capped benchmark runner with a fake client; NO network."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nutrivalue.promotion_benchmark import (
    Budget, BenchmarkError, pairwise, rank, reservation, run, score,
)
from nutrivalue.providers.openrouter import OpenRouterClient, OpenRouterError

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = {
    "good/model": {"prompt_usd_per_token": 1e-7, "completion_usd_per_token": 4e-7,
                   "supported_parameters": ["structured_outputs", "temperature"],
                   "input_modalities": ["text", "image"]},
    "bad/model": {"prompt_usd_per_token": 1e-7, "completion_usd_per_token": 4e-7,
                  "supported_parameters": [], "input_modalities": ["text", "image"]},
    "text/only": {"prompt_usd_per_token": 1e-7, "completion_usd_per_token": 4e-7,
                  "supported_parameters": [], "input_modalities": ["text"]},
}
GOOD = json.dumps({"page_class": "promotion", "promotions": [
    {"kind": "n_for_m", "n": 3, "m": 2, "percent": None, "amount_nok": None,
     "member_only": False, "scope": "fisk og skalldyr"}]})


class FakeClient:
    def __init__(self, cost=0.001, content=GOOD, fail_on=None):
        self.calls = []
        self.cost, self.content, self.fail_on = cost, content, fail_on
        self.status = 500

    def complete(self, model, prompt, image, schema, *, structured, temperature, max_tokens,
                 provider=None):
        self.calls.append((model, structured, temperature))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise OpenRouterError(f"OpenRouter HTTP {self.status}; no retry", self.status)
        content = "not json" if model == "bad/model" else self.content
        return {"content": content, "cost_usd": self.cost, "latency_s": 1.0}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.pages = []
        for n in (1, 2, 3):
            path = self.tmp / f"p{n}.jpg"
            path.write_bytes(b"\xff\xd8\xff" + bytes([n]))
            self.pages.append({"catalog_id": "cat", "page": n, "path": path,
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.out = self.tmp / "out"
        self.sleeps = []
        self.reference = {"pages": [
            {"catalog_id": "cat", "page": 1, "page_class": "promotion", "promotions": [
                {"kind": "n_for_m", "n": 3, "m": 2, "percent": None, "amount_nok": None,
                 "member_only": False, "scope": "fisk, skalldyr og frosne grønnsaker"}]},
            {"catalog_id": "cat", "page": 2, "page_class": "non_offer", "promotions": []},
            {"catalog_id": "cat", "page": 3, "page_class": "promotion", "promotions": [
                {"kind": "percent_off", "n": None, "m": None, "percent": 20.0,
                 "amount_nok": None, "member_only": False, "scope": "fisk"}]}]}

    def go(self, models, client, cap="1.00", execute=True):
        return run(self.pages, models, out_dir=self.out, cap_usd=Decimal(cap),
                   catalogue=CATALOGUE, client=client, execute=execute,
                   sleep=self.sleeps.append)

    def test_plan_makes_no_calls_and_execute_saves_scored_results(self):
        client = FakeClient()
        log = self.go(["good/model"], client, execute=False)
        self.assertEqual((log["planned_calls"], client.calls), (3, []))
        log = self.go(["good/model", "bad/model"], client)
        self.assertEqual(log["executed_calls"], 6)
        self.assertIn(("good/model", True, True), client.calls)
        self.assertIn(("bad/model", False, False), client.calls)
        report = score(self.reference, ["good/model", "bad/model"], self.out)
        good, bad = report["good/model"], report["bad/model"]
        self.assertEqual((good["true_positives"], good["false_assertions"], good["missed"]), (1, 2, 1))
        self.assertEqual(bad["invalid_json"], 3)
        self.assertEqual(Decimal(good["cost_usd"]), Decimal("0.003"))
        self.assertEqual(rank(report)[0], "good/model")
        budget = json.loads((self.out / "budget.json").read_text())
        self.assertEqual(len(budget["attempts"]), 6)
        self.assertTrue(all(a["status"] == "settled" for a in budget["attempts"]))

    def test_pairwise_agreement_counts_right_wrong_and_review(self):
        self.go(["good/model", "bad/model"], FakeClient())
        twin = dict(CATALOGUE["good/model"])
        catalogue = dict(CATALOGUE, **{"twin/model": twin})
        run(self.pages, ["twin/model"], out_dir=self.out, cap_usd=Decimal("1"),
            catalogue=catalogue, client=FakeClient(), execute=True, sleep=lambda s: None)
        rows = {tuple(r["pair"]): r for r in pairwise(
            self.reference, ["good/model", "bad/model", "twin/model"], self.out)}
        same = rows[("good/model", "twin/model")]
        self.assertEqual((same["agreed"], same["agreed_correct"], same["agreed_wrong"],
                          same["disagreements_for_review"]), (3, 1, 2, 0))
        mixed = rows[("good/model", "bad/model")]
        self.assertEqual((mixed["agreed"], mixed["disagreements_for_review"]), (0, 3))
        ordered = [tuple(r["pair"]) for r in pairwise(
            self.reference, ["good/model", "bad/model", "twin/model"], self.out)]
        self.assertEqual(ordered[-1], ("good/model", "twin/model"))  # 1 right, 2 agreed wrong

    def test_resume_never_repeats_saved_calls(self):
        self.go(["good/model"], FakeClient())
        again = FakeClient()
        log = self.go(["good/model"], again)
        self.assertEqual((log["remaining_calls"], again.calls), (0, []))

    def test_budget_cap_stops_before_overspending(self):
        cap = reservation(CATALOGUE["good/model"]) * 2
        client = FakeClient(cost=float(cap))
        with self.assertRaises(BenchmarkError):
            self.go(["good/model"], client, cap=str(cap))
        self.assertEqual(len(client.calls), 1)

    def test_unknown_cost_and_http_failure_block_further_calls(self):
        client = FakeClient(cost=None)
        with self.assertRaises(BenchmarkError):
            self.go(["good/model"], client)
        self.assertEqual(len(client.calls), 1)
        with self.assertRaises(BenchmarkError):
            self.go(["good/model"], FakeClient())
        out2 = self.tmp / "out2"
        self.out = out2
        failing = FakeClient(fail_on=2)
        with self.assertRaises(BenchmarkError):
            self.go(["good/model"], failing)
        self.assertEqual(json.loads((out2 / "budget.json").read_text())["attempts"][1]["status"],
                         "uncertain")

    def test_rate_limit_waits_bounded_then_continues(self):
        limited = FakeClient(fail_on=2)
        limited.status = 429
        log = self.go(["good/model"], limited)
        self.assertEqual((log["executed_calls"], log["rate_limit_waits"]), (3, 1))
        attempts = json.loads((self.out / "budget.json").read_text())["attempts"]
        self.assertEqual([a["status"] for a in attempts],
                         ["settled", "rejected", "settled", "settled"])
        self.assertIn(30.0, self.sleeps)

    def test_persistent_rate_limit_stops_after_max_waits(self):
        class Always429(FakeClient):
            def complete(self, *a, **k):
                self.calls.append(a[0])
                raise OpenRouterError("OpenRouter HTTP 429; no retry", 429)
        client = Always429()
        with self.assertRaises(BenchmarkError) as caught:
            run(self.pages, ["good/model"], out_dir=self.out, cap_usd=Decimal("1"),
                catalogue=CATALOGUE, client=client, execute=True, sleep=lambda s: None,
                max_rate_limit_waits=3)
        self.assertIn("rate limited", str(caught.exception))
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(self.go(["good/model"], FakeClient())["executed_calls"], 3)

    def test_interrupt_mid_call_is_recorded_uncertain_and_releasable(self):
        class Interrupted(FakeClient):
            def complete(self, *a, **k):
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.go(["good/model"], Interrupted())
        attempts = json.loads((self.out / "budget.json").read_text())["attempts"]
        self.assertEqual(attempts[0]["status"], "uncertain")
        Budget(self.out / "budget.json", Decimal("1.00")).release(
            "good/model|cat|1", "owner interrupted run")
        self.assertEqual(self.go(["good/model"], FakeClient())["executed_calls"], 3)

    def test_owner_release_of_one_uncertain_attempt(self):
        with self.assertRaises(BenchmarkError):
            self.go(["good/model"], FakeClient(fail_on=1))
        budget = Budget(self.out / "budget.json", Decimal("1.00"))
        with self.assertRaises(BenchmarkError):
            budget.release("good/model|cat|1", "")
        budget.release("good/model|cat|1", "HTTP 429 observed; not billed per activity log")
        self.assertEqual(self.go(["good/model"], FakeClient())["executed_calls"], 3)

    def test_calls_are_paced_and_page_major(self):
        client = FakeClient()
        self.go(["good/model", "bad/model"], client)
        self.assertEqual([c[0] for c in client.calls][:4],
                         ["good/model", "bad/model", "good/model", "bad/model"])
        self.assertEqual(len(self.sleeps), 5)

    def test_hash_mismatch_and_model_checks(self):
        self.pages[0]["sha256"] = "0" * 64
        with self.assertRaises(BenchmarkError):
            self.go(["good/model"], FakeClient())
        with self.assertRaises(BenchmarkError):
            self.go(["missing/model"], FakeClient())
        with self.assertRaises(BenchmarkError):
            self.go(["text/only"], FakeClient())

    def test_budget_cap_cannot_change_on_resume(self):
        Budget(self.out / "budget.json", Decimal("1.00"))
        with self.assertRaises(BenchmarkError):
            Budget(self.out / "budget.json", Decimal("2.00"))


class ClientTests(unittest.TestCase):
    def test_missing_key_fails_before_any_request(self):
        with self.assertRaises(OpenRouterError):
            OpenRouterClient("")

    def test_request_shape_and_no_key_leak(self):
        sent = {}

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return json.dumps({"choices": [{"message": {"content": GOOD}}],
                                                  "usage": {"cost": 0.0004}}).encode()

        def opener(request, timeout):
            sent["body"] = json.loads(request.data)
            sent["auth"] = request.get_header("Authorization")
            return Response()

        key = "synthetic-token-for-redaction-test"
        answer = OpenRouterClient(key, opener=opener).complete(
            "good/model", "prompt", b"img", {"type": "object"}, structured=True,
            temperature=False, max_tokens=100)
        self.assertEqual(answer["cost_usd"], 0.0004)
        self.assertEqual(sent["auth"], f"Bearer {key}")
        self.assertNotIn("temperature", sent["body"])
        self.assertEqual(sent["body"]["response_format"]["type"], "json_schema")
        self.assertTrue(sent["body"]["provider"]["allow_fallbacks"])
        self.assertEqual(sent["body"]["model"], "good/model")
        self.assertNotIn(key, json.dumps(answer))


class CliTests(unittest.TestCase):
    def test_outputs_must_stay_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(
                [sys.executable, str(ROOT / "scripts/benchmark_page_promotions.py"),
                 "--run-dir", tmp, "--reference", f"{tmp}/ref.json"],
                capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("data/private", done.stderr)


if __name__ == "__main__":
    unittest.main()
