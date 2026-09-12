"""壊れた文書・追跡情報を検知できることを小さな独立fixtureで検証する。"""
import json
import tempfile
import unittest
from pathlib import Path

from tools import workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gah-workflow-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.put("README.md", w.FM + "# Example\n\n[Guide](docs/guide.md)\n")
        self.put("docs/guide.md", w.FM + "# Guide\n\n入力と出力を確認する。\n")
        self.put("docs/acceptance/README.md", w.FM + "# Acceptance\n\n検収の入口。\n")

    def put(self, path, content):
        w.write(self.root, path, content)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in (self.root / "docs/birdseye").rglob("*.json")}

    def tasks(self, task_status="done", acceptance_status="approved"):
        self.put("docs/tasks/TASK.example.md", "---\ntask_id: 20260909-01\n"
                 f"intent_id: INT-GAH-001\nstatus: {task_status}\n---\n\n# Task\n")
        self.put("docs/acceptance/AC-20260909-01.md", "---\nacceptance_id: AC-20260909-01\n"
                 "task_id: 20260909-01\nintent_id: INT-GAH-001\n"
                 f"status: {acceptance_status}\n---\n\n# Acceptance\n")

    def test_generation_is_idempotent_and_contains_real_edges(self):
        self.assertTrue(w.generate(self.root)["changed"])
        before = self.snapshot()
        self.assertFalse(w.generate(self.root)["changed"])
        self.assertEqual(before, self.snapshot())
        index = json.loads(w.read(self.root / w.INDEX))
        self.assertIn(["README.md", "docs/guide.md"], index["edges"])
        cap = json.loads(w.read(self.root / index["nodes"]["docs/guide.md"]["caps"]))
        self.assertIn("README.md", cap["deps_in"])
        self.assertEqual([], w.artifact_errors(self.root))

    def test_index_node_order_is_portable_across_path_flavours(self):
        self.put("ZETA.md", w.FM + "# Zeta\n")
        self.put("docs/Alpha.md", w.FM + "# Alpha\n")
        w.generate(self.root)
        index = json.loads(w.read(self.root / w.INDEX))
        self.assertEqual(list(index["nodes"]), [
            "README.md", "ZETA.md", "docs/Alpha.md",
            "docs/acceptance/INDEX.md", "docs/acceptance/README.md", "docs/guide.md",
        ])
        self.assertEqual([], w.artifact_errors(self.root))

    def test_source_change_is_stale_then_regenerated(self):
        w.generate(self.root)
        self.put("docs/guide.md", w.read(self.root / "docs/guide.md") + "\n変更内容。\n")
        self.assertTrue(w.artifact_errors(self.root))
        self.assertEqual("00002", w.generate(self.root)["generation"])
        self.assertEqual([], w.artifact_errors(self.root))

    def test_missing_link_and_outside_link_fail(self):
        self.put("docs/guide.md", w.FM + "# Guide\n\n[Missing](missing.md)\n[Outside](../../outside.md)\n")
        errors = w.document_errors(self.root)
        self.assertTrue(any("参照先がない" in e for e in errors))
        self.assertTrue(any("repo外" in e for e in errors))

    def test_fenced_examples_do_not_create_document_links(self):
        self.put("docs/guide.md", w.FM + '# Guide\n\n```md\n[example](not-real.md)\n```\n')
        self.assertEqual([], w.document_errors(self.root))

    def test_mixed_generation_and_tampered_caps_fail(self):
        w.generate(self.root)
        hot = json.loads(w.read(self.root / w.HOT))
        hot["generated_at"] = "99999"
        self.put(w.HOT, w.dump(hot))
        self.put("docs/birdseye/caps/README.md.json", "{}\n")
        errors = w.artifact_errors(self.root)
        self.assertTrue(any(w.HOT in e for e in errors))
        self.assertTrue(any("README.md.json" in e for e in errors))

    def test_removed_document_caps_are_detected_and_cleaned(self):
        self.put("docs/extra.md", w.FM + "# Extra\n\n追加文書。\n")
        w.generate(self.root)
        extra = (self.root / "docs/extra.md").resolve()
        self.assertTrue(extra.is_relative_to(self.root))
        extra.unlink()
        self.assertTrue(any("余剰caps" in e for e in w.artifact_errors(self.root)))
        w.generate(self.root)
        self.assertEqual([], w.artifact_errors(self.root))

    def test_done_task_requires_approved_acceptance(self):
        self.tasks(acceptance_status="draft")
        self.assertTrue(w.task_errors(self.root))
        self.tasks()
        self.assertEqual([], w.task_errors(self.root))
        path = self.root / "docs/acceptance/AC-20260909-01.md"
        self.assertTrue(path.resolve().is_relative_to(self.root))
        path.unlink()
        self.assertTrue(w.task_errors(self.root))

    def test_duplicate_task_and_mismatched_intent_fail(self):
        self.tasks()
        original = w.read(self.root / "docs/tasks/TASK.example.md")
        self.put("docs/tasks/TASK.duplicate.md", original)
        self.assertTrue(any("重複" in e for e in w.task_errors(self.root)))
        acc = "docs/acceptance/AC-20260909-01.md"
        self.put(acc, w.read(self.root / acc).replace("INT-GAH-001", "INT-OTHER"))
        self.assertTrue(any("intent_id" in e for e in w.task_errors(self.root)))

    def test_acceptance_index_detects_changed_status(self):
        self.tasks(task_status="in_progress", acceptance_status="draft")
        w.generate(self.root)
        self.tasks()
        self.assertIn("Acceptance索引が古い", w.artifact_errors(self.root))
        w.generate(self.root)
        self.assertEqual([], w.artifact_errors(self.root))

    def test_branch_mapping_requires_docs_gate(self):
        self.put("governance/policy.yaml", "ci:\n  required_jobs:\n    - docs-gate\n")
        payload = {"remote_verified": False, "evidence_kind": "desired_configuration",
                   "required_status_checks": {"contexts": ["unit"]}}
        self.put("governance/branch-protection.expected.json", w.dump(payload))
        self.assertTrue(w.branch_errors(self.root))
        payload["required_status_checks"]["contexts"].append("docs-gate")
        self.put("governance/branch-protection.expected.json", w.dump(payload))
        self.assertEqual([], w.branch_errors(self.root))


if __name__ == "__main__":
    unittest.main()
