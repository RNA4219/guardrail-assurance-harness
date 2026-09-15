"""候補表示が採択や通常CIの合格を捏造しないことを検査する。"""
from copy import deepcopy
import io
import unittest
from tests import test_run_report as helpers
from tools.gah_report import build_report, render_markdown, run
from gah.run_contracts import content_ref


class CandidateReportTests(unittest.TestCase):
    def setUp(self):
        self.helper = helpers.RunReportTests(); self.helper.setUp(); h = self.helper
        h.manifest['purpose'] = 'contract_candidate'
        ref = content_ref('run_manifest', 'normal', h.manifest)
        h.artifacts[ref['digest']] = h.manifest
        h.outputs['manifest_ref'] = ref
        h.outputs_ref = content_ref('run_outputs', 'normal', h.outputs)
        h.request['expected_manifest_ref'] = ref
        h.gate.update(expected_manifest_ref=ref, outputs_ref=None, assurance='HOLD', use=False,
            ci_eligible=False, exit_code=1, reasons=['CI_PURPOSE_REQUIRED', 'ASSURANCE_NOT_ALLOWED'])
        def client(uid, request):
            if request['action'] in {'ci_check','mutation_review_current'}: return h.client(uid, request)
            mapped = {'candidate_outputs':'run_outputs', 'candidate_artifact':'run_artifact'}
            value = h.client(uid, {**request, 'action':mapped[request['action']]})
            value['action'] = request['action']; return value
        h.runtime.client.side_effect = client
    def test_candidate_metrics_and_findings_are_readable_but_ci_remains_rejected(self):
        h = self.helper; result = build_report(h.runtime, h.request, candidate=True)
        self.assertFalse(result['ci_eligible']); self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['observed_assurance'], 'HEALTHY')
        self.assertEqual(result['purpose'], 'contract_candidate')
        self.assertNotIn('candidate_adopted', result)
        self.assertIn('通常CIには利用できない', render_markdown(result))
        self.assertEqual(h.runtime.client.call_args.args[1], h.request)
        stream = io.StringIO(); self.assertEqual(run(h.runtime, h.request, stream, candidate=True), 1)
    def test_forged_ci_success_and_wrong_output_mode_are_not_accepted(self):
        h = self.helper; h.gate.update(outputs_ref=h.outputs_ref, assurance='HEALTHY', use=True,
            ci_eligible=True, exit_code=0, reasons=[])
        with self.assertRaisesRegex(ValueError, 'REPORT_BINDING_MISMATCH'):
            build_report(h.runtime, h.request, candidate=True)
        with self.assertRaises((ValueError, KeyError)): build_report(h.runtime, h.request)


if __name__ == '__main__': unittest.main()
