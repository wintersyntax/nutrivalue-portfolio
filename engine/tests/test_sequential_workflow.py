"""Workflow consistency: local PR preflight and remote PR queue share one policy."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SequentialWorkflowContractTests(unittest.TestCase):
    def test_pre_pr_gate_reads_live_main_and_runs_tests_without_starting_runners(self):
        ready = (ROOT / "scripts/pr-ready").read_text(encoding="utf-8")
        for required in ("git ls-remote --exit-code origin refs/heads/main",
                         "git merge-base", "scripts/agent-session-status",
                         "scripts/check_pr_queue.py", "scripts/check_task_closure.py",
                         "--base-ref", "scripts/ci-local-check",
                         "scripts/ci_runner_status.py", "git diff --check",
                         "PR-READY: GREEN"):
            with self.subTest(required=required):
                self.assertIn(required, ready)
        self.assertNotIn("--authorize-merge", ready)
        self.assertNotIn("ci-runner-start", ready)

    def test_pr_acceptance_contains_exclusive_queue_gate_without_new_runner(self):
        workflow = (ROOT / ".github/workflows/pr-acceptance.yml").read_text(
            encoding="utf-8")
        self.assertIn("name: Check exclusive active PR lifecycle", workflow)
        # Local pre-PR uses authenticated gh; Actions should never pass
        # a secret token into checkout-controlled Python code.
        self.assertIn("scripts/check_pr_queue.py", ready := (
            ROOT / "scripts/pr-ready").read_text(encoding="utf-8"))
        self.assertIn("uses: actions/github-script@v7", workflow)
        self.assertIn("github.rest.pulls.list", workflow)
        self.assertIn("prs.length !== 1", workflow)
        self.assertIn("pr.number !== eventPr.number", workflow)
        self.assertIn("pr.head?.sha !== eventPr.head.sha", workflow)
        self.assertIn("pr.head?.ref !== eventPr.head.ref", workflow)
        self.assertIn("pr.base?.ref !== 'main'", workflow)
        self.assertIn("scripts/check_pr_contract.py", workflow)
        self.assertIn("HARD CHECKPOINT - PR task closure and documentation", workflow)
        self.assertIn("Fetch exact-base ledger for task closure", workflow)
        self.assertIn("pr.base.sha", workflow)
        self.assertIn("repos.getContent", workflow)
        self.assertIn("--base-ledger", workflow)
        self.assertIn("TASK CLOSURE", (ROOT / "scripts/check_task_closure.py").read_text(encoding="utf-8"))
        self.assertIn("pull-requests: read", workflow)
        self.assertNotIn("GH_TOKEN:", workflow)
        self.assertNotIn("secrets.GITHUB_TOKEN", workflow)
        self.assertIn("nutrivalue-ci", workflow)
        self.assertIn("nutrivalue-application", workflow)
        self.assertEqual(workflow.count("runs-on:"), 2)
        self.assertNotIn("ubuntu-latest", workflow)
        main = (ROOT / ".github/workflows/main-acceptance.yml").read_text(encoding="utf-8")
        self.assertIn("HARD CHECKPOINT - accepted main closes one TASK", main)
        self.assertIn("fetch-depth: 2", main)
        self.assertIn("scripts/check_task_closure.py", main)

if __name__ == "__main__":
    unittest.main()
