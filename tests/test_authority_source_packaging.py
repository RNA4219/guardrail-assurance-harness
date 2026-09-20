"""authorityの配布依存と、処理のソース固定を検査する。"""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gah.evaluation_authority import _compute_source_digest
from tools.prepare_authority_runtime import SOURCES


class AuthoritySourcePackagingTests(unittest.TestCase):
    def test_local_import_dependencies_are_present_in_image_sources(self):
        selected = set(SOURCES)
        self.assertEqual(len(selected), len(SOURCES))
        missing = set()
        for name in SOURCES:
            self.assertTrue((ROOT / name).is_file(), name)
            if not name.endswith('.py'):
                continue
            for node in ast.walk(ast.parse((ROOT / name).read_text(encoding='utf8'))):
                targets = []
                if isinstance(node, ast.ImportFrom):
                    if node.level and name.startswith('src/gah/'):
                        if node.module:
                            targets.append('src/gah/' + node.module.replace('.', '/') + '.py')
                        else:
                            targets.extend('src/gah/' + item.name + '.py' for item in node.names)
                    elif node.module and node.module.startswith('gah.'):
                        targets.append('src/' + node.module.replace('.', '/') + '.py')
                for target in targets:
                    if (ROOT / target).is_file() and target not in selected:
                        missing.add(target)
        self.assertEqual(missing, set())

    def test_partitioned_v7_runtime_dependency_closure_is_packaged(self):
        required = {
            'src/gah/partitioned_corpus_authority.py',
            'src/gah/partitioned_corpus_store.py',
            'src/gah/partitioned_corpus_migrations.py',
            'src/gah/partitioned_scale_corpus.py',
            'src/gah/partitioned_case_set.py',
            'src/gah/query_scale_data.py',
            'src/gah/partitioned_llm_materialization.py',
            'src/gah/partitioned_guardrail_results.py',
            'src/gah/partitioned_guardrail_runner.py',
            'src/gah/partitioned_llm_admission.py',
            'src/gah/partitioned_normal_evidence.py',
        }
        self.assertTrue(required <= set(SOURCES))
        for name in required:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_review_and_termination_code_are_bound_to_extension_digest(self):
        original = Path.read_bytes
        baseline = _compute_source_digest()
        for filename in ('mutation_reviews.py', 'termination.py', 'run_catalog.py', 'run_diagnostics.py', 'pilot.py', 'pilot_authority.py', 'productization.py', 'sqlite_limits.py', 'bounded_files.py', 'storage_budget.py', 'worker_metrics.py', 'budget_warning.py', 'partitioned_llm_admission.py', 'partitioned_normal_evidence.py', 'partitioned_aggregation.py', 'partitioned_llm_materialization.py', 'partitioned_guardrail_results.py', 'partitioned_guardrail_runner.py', 'partitioned_case_set.py', 'partitioned_scale_corpus.py', 'partitioned_trial_plan.py', 'partitioned_run_contracts.py', 'query_scale_data.py'):
            with self.subTest(filename=filename):
                def changed(path):
                    value = original(path)
                    return value + b'\n' if path.name == filename else value
                with patch.object(Path, 'read_bytes', changed):
                    self.assertNotEqual(_compute_source_digest(), baseline)


if __name__ == '__main__':
    unittest.main()
