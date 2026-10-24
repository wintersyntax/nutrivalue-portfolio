"""TASK-055: standing benefits, cross-checks, retries and pipeline scoring. NO network."""
from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nutrivalue import promotion_crosscheck as xcheck
from nutrivalue.page_sets import run_pages
from nutrivalue.promotion_benchmark import run
from nutrivalue.promotion_extraction import (
    PAIR, build, crosscheck_plan, import_extraction, retry_candidates, run_crosschecks,
    score_final, set_aside_for_retry,
)
from nutrivalue.providers.openrouter import api_key
from nutrivalue.standing_benefits import (
    StandingRegistryError, load_registry, match, normalize,
)
from test_promotion_extraction import (
    CATALOGUE, ROOT, A, B, SyntheticWeek, answer, item,
)

REGISTRY = load_registry(ROOT / "data" / "reference" / "standing_benefits.json")


class StandingTests(unittest.TestCase):
    def test_registry_matches_chain_kind_value_and_keywords(self):
        bonus = item("bonus_percent", percent=15, scope="Norske epler (15% Trumf-bonus)")
        self.assertEqual(match(bonus, "KIWI", REGISTRY)["id"], "kiwi-frukt-gront-15")
        self.assertIsNone(match(bonus, "REMA 1000", REGISTRY))
        self.assertIsNone(match(dict(bonus, percent=50), "KIWI", REGISTRY))
        angla = item(percent=20, member=False, scope="alt fra Ånglamark-serien")
        self.assertEqual(match(angla, "Coop Mega", REGISTRY)["id"], "coop-anglamark-20")
        self.assertIsNone(match(dict(angla, scope="Jif"), "Coop Mega", REGISTRY))

    def test_normalize_fixes_member_flag_and_keeps_one_per_page(self):
        parsed = {"page_class": "offers", "promotions": [
            item("bonus_percent", percent=15, scope="Gulrot"),
            item("bonus_percent", percent=15, scope="Epler"),
            item("n_for_m", n=3, m=2, scope="Taco")]}
        out = normalize(parsed, "KIWI", REGISTRY)["promotions"]
        self.assertEqual([p.get("standing_id") for p in out], ["kiwi-frukt-gront-15", None])
        self.assertTrue(out[0]["member_only"])
        self.assertIsNone(normalize(None, "KIWI", REGISTRY))

    def test_bad_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            path.write_text(json.dumps({"benefits": [{"id": "x", "chains": ["KIWI"],
                                                      "kind": "n_for_m", "value": 3,
                                                      "member_only": True}]}))
            with self.assertRaises(StandingRegistryError):
                load_registry(path)


class CrosscheckUnitTests(unittest.TestCase):
    PAGE = {"catalog_id": "cat", "page": 4, "sha256": "a" * 64, "chain": "MENY"}

    def numbered(self, *claims):
        return xcheck.claims_for(self.PAGE, list(claims))

    def saved(self, numbered, verdicts):
        checks = [{"id": c["id"], "printed": verdicts.get(c["id"], (False, False))[0],
                   "member_only": verdicts.get(c["id"], (False, False))[1],
                   "evidence": "SPAR -5 kr"} for c in numbered]
        return {"claims": numbered, "answer": {"content": json.dumps({"checks": checks})}}

    def test_decoy_is_deterministic_and_distinct(self):
        claims = [item("amount_off", amount=5, member=True, scope=f"P{i}") for i in range(3)]
        first, second = self.numbered(*claims), self.numbered(*claims)
        self.assertEqual(first, second)
        decoys = [c for c in first if c["decoy"]]
        self.assertEqual(len(decoys), 1)
        self.assertNotEqual(decoys[0]["amount_nok"], 5)
        self.assertEqual([c["id"] for c in first], [1, 2, 3, 4])

    def test_outcomes(self):
        claim = item("amount_off", amount=5, member=True, scope="Coop suppe")
        numbered = self.numbered(claim)
        real = next(c for c in numbered if not c["decoy"])["id"]
        fake = next(c for c in numbered if c["decoy"])["id"]
        ocr = ["spar", "-5 kr"]
        ok = xcheck.decide(self.saved(numbered, {real: (True, True)}), claim, ocr)
        self.assertTrue(ok["accepted"])
        self.assertEqual(ok["evidence"], "SPAR -5 kr")
        cases = [({real: (True, True), fake: (True, True)}, ocr, "decoy"),
                 ({real: (False, False)}, ocr, "not printed"),
                 ({real: (True, False)}, ocr, "members-only"),
                 ({real: (True, True)}, ["nothing here"], "OCR")]
        for verdicts, lines, reason in cases:
            outcome = xcheck.decide(self.saved(numbered, verdicts), claim, lines)
            self.assertFalse(outcome["accepted"])
            self.assertIn(reason, outcome["reason"])
        self.assertIn("not cross-checked", xcheck.decide(None, claim, ocr)["reason"])
        refuted = xcheck.decide(self.saved(numbered, {}), claim, ["nothing here"])
        self.assertTrue(refuted.get("refuted"))
        self.assertFalse(xcheck.decide(self.saved(numbered, {}), claim, ocr).get("refuted"))
        self.assertFalse(xcheck.decide(self.saved(numbered, {}), claim, None).get("refuted"))
        broken = {"claims": numbered, "answer": {"content": '{"checks": []}'}}
        self.assertIn("unusable", xcheck.decide(broken, claim, ocr)["reason"])


class ApiKeyTests(unittest.TestCase):
    def test_environment_first_then_keychain_on_macos_only(self):
        calls = []

        class Done:
            returncode, stdout = 0, "sk-or-from-keychain\n"

        def fake(cmd, **kw):
            calls.append(cmd)
            return Done()
        self.assertEqual(api_key({"OPENROUTER_API_KEY": " sk-env "}, "darwin", fake), "sk-env")
        self.assertEqual(calls, [])
        self.assertEqual(api_key({}, "darwin", fake), "sk-or-from-keychain")
        self.assertEqual(calls[0][:3], ["security", "find-generic-password", "-s"])
        self.assertEqual(api_key({}, "linux", fake), "")
        Done.returncode = 44
        self.assertEqual(api_key({}, "darwin", fake), "")


class PipelineClient:
    """Page answers by image byte; cross-checks answered by a verdict function."""

    def __init__(self, answers, verdict):
        self.answers, self.verdict, self.calls = answers, verdict, []

    def complete(self, model, prompt, image, schema, *, structured, temperature, max_tokens,
                 provider=None):
        self.calls.append((model, "checks" in schema.get("properties", {})))
        if "checks" in schema.get("properties", {}):
            lines = [line for line in prompt.splitlines() if line[:1].isdigit() and ". " in line]
            checks = []
            for line in lines:
                number, text = line.split(". ", 1)
                printed, member = self.verdict(model, image[-1], text)
                checks.append({"id": int(number), "printed": printed, "member_only": member,
                               "evidence": text if printed else ""})
            content = json.dumps({"checks": checks})
        else:
            content = self.answers[(model, image[-1])]
        return {"content": content, "cost_usd": 0.0004, "prompt_tokens": 4000,
                "completion_tokens": 100, "latency_s": 1.0,
                "provider": "Anthropic" if model == A else "OpenAI"}


class PipelineTests(SyntheticWeek):
    ANSWERS = {
        # page 1: per-product badges, only A lists both; B also has the 11 % standing bonus
        (A, 1): answer("promotion", item("amount_off", amount=5, member=True, scope="Coop suppe"),
                       item("amount_off", amount=5, member=True, scope="Coop pannekaker")),
        (B, 1): answer("promotion", item("amount_off", amount=5, member=True, scope="Coop suppe"),
                       item("bonus_percent", percent=11, scope="Frukt og grønt")),
        # page 2: members-only conflict on the same -30 %
        (A, 2): answer("offers", item(percent=30, member=True, scope="King Oscar makrell")),
        (B, 2): answer("offers", item(percent=30, member=False, scope="King Oscar makrell")),
        # page 3: B invalid first, valid on retry
        (A, 3): answer("non_offer"),
        (B, 3): "not json",
    }

    def verdict(self, model, page, text):
        if "-37" in text or "-12" in text or "-45" in text or "bonus" in text and "26" in text:
            return False, False                       # never confirm a decoy
        if page == 1:
            return True, True                         # B confirms Coop pannekaker -5 kr
        if page == 2:
            return True, False                        # both say: not members-only
        return False, False

    def ocr(self):
        from nutrivalue.promotion_extraction import OcrLookup
        return OcrLookup(run_pages(self.run_dir), self.out / "ocr",
                         reader=lambda path, cache: ("fake", ["spar -5 kr", "-30 %", "11 %"]))

    def pipeline(self, client):
        pages = run_pages(self.run_dir)
        common = dict(out_dir=self.out, cap_usd=Decimal("1"), catalogue=CATALOGUE,
                      client=client, execute=True, sleep=lambda s: None)
        run(pages, list(PAIR), **common)
        retry = retry_candidates(pages, self.out)
        self.assertEqual([(m, p["page"]) for m, p in retry], [(B, 3)])
        for model, page in retry:
            set_aside_for_retry(self.out, model, page)
        fixed = dict(self.ANSWERS)
        fixed[(B, 3)] = answer("non_offer")
        client.answers = fixed
        run([p for _, p in retry], list(PAIR), **common)
        self.assertEqual(retry_candidates(pages, self.out), [])
        plan = crosscheck_plan(pages, self.out, PAIR, REGISTRY)
        run_crosschecks(plan, self.out, catalogue=CATALOGUE, client=client,
                        cap_usd=Decimal("1"), execute=True, sleep=lambda s: None)
        return build(self.run_dir, self.out, ocr_lookup=self.ocr(), registry=REGISTRY), plan

    def test_full_pipeline_statuses(self):
        client = PipelineClient(dict(self.ANSWERS), self.verdict)
        built, plan = self.pipeline(client)
        self.assertEqual(sorted((p["verifier"], p["page"]["page"]) for p in plan),
                         [(A, 2), (B, 1), (B, 2)])
        rows = {(r[2], r[10], r[9]): r[12] for r in built["promotions"]}
        self.assertEqual(rows[(1, "Coop suppe", 1)], "accepted")
        self.assertEqual(rows[(1, "Coop pannekaker", 1)], "crosscheck_accepted")
        self.assertEqual(rows[(1, "11 % medlemsbonus på frukt og grønt for Coop-medlemmer", 1)],
                         "standing")
        self.assertEqual(rows[(2, "King Oscar makrell", 0)], "crosscheck_accepted")
        self.assertEqual(rows[(2, "King Oscar makrell", 1)], "superseded")
        ext = built["extraction"]
        self.assertEqual((ext["review_queue_size"], ext["standing_items"],
                          ext["crosscheck_accepted"], ext["superseded_items"],
                          ext["retried_answers"], ext["crosscheck_calls"]), (0, 1, 2, 1, 1, 3))
        self.assertEqual(Decimal(ext["cost_usd"]), Decimal("0.0004") * 9)
        evidence = json.loads(next(r[17] for r in built["promotions"]
                                   if r[12] == "crosscheck_accepted"))
        self.assertTrue(evidence["accepted"])
        self.register_run()
        self.assertTrue(import_extraction(built, self.db, ROOT / "analytics")["imported"])
        import sqlite3
        with sqlite3.connect(self.db) as db:
            final = sorted(r[0] for r in db.execute("SELECT final_status FROM v_week_promotions"))
            self.assertEqual(final, ["accepted", "accepted_by_crosscheck",
                                     "accepted_by_crosscheck", "standing", "superseded"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v_week_promotion_review")
                             .fetchone(), (0,))

    def test_skeptical_verifier_leaves_items_for_review_and_stale_checks_fail(self):
        client = PipelineClient(dict(self.ANSWERS), lambda m, p, t: (False, False))
        built, _ = self.pipeline(client)
        statuses = sorted(r[12] for r in built["promotions"])
        self.assertEqual(statuses.count("needs_review"), 3)
        self.assertNotIn("crosscheck_accepted", statuses)
        path = xcheck.path_for(self.out, B, run_pages(self.run_dir)[0])
        saved = json.loads(path.read_text())
        saved["claims"][0]["scope"] = "changed"
        path.write_text(json.dumps(saved))
        from nutrivalue.promotion_extraction import ExtractionError
        with self.assertRaises(ExtractionError):
            build(self.run_dir, self.out, registry=REGISTRY)

    def test_refuted_when_verifier_and_ocr_both_disagree(self):
        from nutrivalue.promotion_extraction import OcrLookup
        self.ocr = lambda: OcrLookup(run_pages(self.run_dir), self.out / "ocr2",
                                     reader=lambda path, cache: ("fake", ["nothing printed"]))
        client = PipelineClient(dict(self.ANSWERS), lambda m, p, t: (False, False))
        built, _ = self.pipeline(client)
        statuses = sorted(r[12] for r in built["promotions"])
        self.assertEqual(statuses.count("refuted"), 3)
        self.assertEqual(built["extraction"]["review_queue_size"], 0)
        self.assertEqual(built["extraction"]["refuted_items"], 3)
        promo = lambda **k: {**item(**k), "optional": False}  # noqa: E731
        reference = {"pages": [
            {"catalog_id": "cat", "page": 1, "promotions": [
                promo(kind="amount_off", amount=5, member=True)]},
            {"catalog_id": "cat", "page": 2, "promotions": [promo(percent=30, member=False)]}]}
        score = score_final(built, reference)
        self.assertEqual((score.get("required_refuted"), score.get("wrong_refuted")), (1, 2))
        self.register_run()
        import_extraction(built, self.db, ROOT / "analytics")
        import sqlite3
        with sqlite3.connect(self.db) as db:
            self.assertIn(("rejected_by_crosscheck",), db.execute(
                "SELECT DISTINCT final_status FROM v_week_promotions").fetchall())

    def test_pipeline_score_against_a_reference(self):
        client = PipelineClient(dict(self.ANSWERS), self.verdict)
        built, _ = self.pipeline(client)
        promo = lambda **k: {**item(**k), "optional": False}  # noqa: E731
        reference = {"pages": [
            {"catalog_id": "cat", "page": 1, "promotions": [
                promo(kind="amount_off", amount=5, member=True),
                promo(kind="amount_off", amount=5, member=True),
                {**item("bonus_percent", percent=11, member=True), "optional": True}]},
            {"catalog_id": "cat", "page": 2, "promotions": [promo(percent=30, member=False)]},
            {"catalog_id": "cat", "page": 3, "promotions": [promo(percent=50)]}]}
        self.assertEqual(score_final(built, reference),
                         {"required_auto": 3, "optional_auto": 1, "required_missing": 1})


if __name__ == "__main__":
    unittest.main()
