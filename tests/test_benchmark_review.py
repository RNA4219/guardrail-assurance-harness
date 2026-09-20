"""監督側の独立した計測契約の反例。SLO達成の実測ではない。"""
from fractions import Fraction
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from gah import benchmark
from gah.contracts import ContractError


class BenchmarkReviewTests(unittest.TestCase):
    def test_even_median_and_nearest_rank_p95_are_exact(self):
        self.assertEqual(benchmark.metric_summary([1,2])["median"],Fraction(3,2))
        self.assertEqual(benchmark.metric_summary(range(1,101))["p95"],95)
        self.assertEqual(benchmark.ratio(3,2),Fraction(3,2))
        self.assertIsNone(benchmark.ratio(3,0))

    def test_ratio_does_not_accept_boolean_float_string_or_negative_work(self):
        for candidate,baseline in ((True,2),(1,True),(0.1,2),("1",2),(-1,2)):
            with self.subTest(candidate=candidate,baseline=baseline),self.assertRaises(ContractError):
                benchmark.ratio(candidate,baseline)

    def test_query_recipe_cannot_claim_full_run_ci_or_quickstart_interval(self):
        for recipe in ("candidate","current_ci","report"):
            self.assertEqual(benchmark.RECIPE_SURFACES[recipe],frozenset({"query"}))

    def test_missing_group_cpu_and_rss_can_be_saved_without_becoming_zero(self):
        value={name:None for name in benchmark.OBSERVATION_FIELDS}
        value.update(iteration=1,warmness="warm",wall_ns=100,retry_count=0,
                     failure_class="UNSUPPORTED_ENVIRONMENT",operation_status="INCOMPLETE",exit_code=2,valid_for_slo=False)
        saved=benchmark.validate_observation(value)
        self.assertIsNone(saved["cpu_ns"]);self.assertIsNone(saved["rss_group_peak_bytes"])
        forged={**saved,"failure_class":"NONE","operation_status":"COMPLETED","exit_code":0,"valid_for_slo":True}
        with self.assertRaises(ContractError):
            benchmark.validate_observation(forged)


if __name__=="__main__":
    unittest.main()
