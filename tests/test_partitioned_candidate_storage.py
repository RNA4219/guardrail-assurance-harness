"""Candidate storage exposes compact roots until trusted expected-byte validation."""
from unittest.mock import patch
import unittest
from tests import test_partitioned_prepared_io as io_fixture
from tests import test_partitioned_contract_updates as contract_fixture
from gah import candidate_sections, partitioned_llm_admission as io
from gah.partitioned_llm_transitions import build

class PartitionedCandidateStorageTests(unittest.TestCase):
    def test_compact_read_does_not_restore_complete_plans(self):
        fixture = io_fixture.PartitionedPreparedIOTests('test_prepared_round_trip_and_wrong_artifact_rejected')
        self.addCleanup(fixture.doCleanups)
        source = fixture.prepared()
        baseline,previous,following = contract_fixture.PartitionedContractUpdateTests._baseline_and_next(source['bound_run'])
        runs = build(previous,following,baseline_record=baseline,source_prepared=source,
                     now=1100,old_run_id='storage-old',new_run_id='storage-new')
        db=fixture.db()
        saved=candidate_sections.pack(db,'storage-candidate',runs)
        with patch.object(io,'load_prepared',side_effect=AssertionError('unexpected full restore')):
            compact=candidate_sections.unpack(db,'storage-candidate',saved,restore_prepared=False)
            for side in ('old','new'):
                self.assertEqual(compact[side]['kind'],'partitioned_prepared_run')
                self.assertNotIn('bound_run',compact[side])
                checked=io.verify_expected_prepared(db,compact[side],runs[side])
                self.assertEqual(checked,runs[side])
        self.assertEqual(candidate_sections.unpack(db,'storage-candidate',saved),runs)
        compact['old']['run_id']='changed'
        self.assertNotEqual(compact,saved)
        self.assertEqual(candidate_sections.unpack(db,'storage-candidate',saved),runs)

if __name__ == '__main__': unittest.main()
