"""完了済み全600段階の再検証で、行の改変・削除・時刻・失効を隠さない。"""
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_aggregation_cache as seed
from gah import run_evidence as evidence,evidence_snapshot_cache as cache

class EvidenceSnapshotCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.AggregationCacheTests.setUpClass.__func__(cls)
        db=sqlite3.connect(':memory:',isolation_level=None);db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE');evidence.create_schema(db)
            bound=cls.prepared['bound_run'];cls.run_id=bound['manifest']['run_id']
            cls.allowed={cls.run_id:evidence.bound_bundle_digest(bound)}
            book=evidence.RunEvidenceBook(db,now=1000,allowed_bindings=cls.allowed)
            view=book.start_run(bound,cls.prepared['execution_profile']);cls.binding=view['binding']
            for attempt in cls.records:book.record_attempt(attempt)
            book.finalize(cls.run_id);db.commit();cls.database=db.serialize()
        finally:db.close()
    def setUp(self):
        cache.clear();self.db=sqlite3.connect(':memory:',isolation_level=None);self.addCleanup(self.db.close)
        self.db.deserialize(self.database);self.db.row_factory=sqlite3.Row;self.db.execute('BEGIN IMMEDIATE')
        self.book=evidence.RunEvidenceBook(self.db,now=1000,allowed_bindings=self.allowed)
    def current(self):return self.book.current_use(self.run_id,self.binding)
    def test_same_rows_reuse_validation_but_source_change_forces_recheck(self):
        original=evidence.RunEvidenceStore._validate_store_contents;calls=[]
        def counted(book,*args):calls.append(1);return original(book,*args)
        with patch.object(evidence.RunEvidenceStore,'_validate_store_contents',new=counted):
            first=self.current();self.assertEqual(first,self.current());self.assertEqual(len(calls),1)
            with patch('gah.evaluation_authority._source_digest',return_value='b'*64):self.current()
            self.assertEqual(len(calls),2)
        self.assertFalse(first['ci_eligible']);self.assertFalse(first['use'])
    def test_changed_payload_unchanged_digest_deletion_and_metadata_are_rejected(self):
        self.current()
        statements=[
            "UPDATE attempts SET attempt_json=replace(attempt_json,'COMPLETED','PENDING') WHERE attempt_id=(SELECT MIN(attempt_id) FROM attempts)",
            "DELETE FROM attempts WHERE attempt_id=(SELECT MIN(attempt_id) FROM attempts)",
            "UPDATE attempts SET delivery_count=0 WHERE attempt_id=(SELECT MIN(attempt_id) FROM attempts)",
            "UPDATE terminals SET created_at=1001",
            "UPDATE run_state SET decision_digest='"+'f'*64+"'",
        ]
        for statement in statements:
            with self.subTest(statement=statement.split(' SET ')[0]):
                self.db.execute('SAVEPOINT damage');self.db.execute(statement)
                try:
                    with self.assertRaises(evidence.EvidenceError):self.current()
                finally:self.db.execute('ROLLBACK TO damage');self.db.execute('RELEASE damage')
                self.current()
    def test_malformed_bound_shape_keeps_the_storage_error_contract(self):
        import hashlib
        self.current()
        self.db.execute('UPDATE bound_runs SET bundle_json=?,bundle_digest=? WHERE run_id=?',
                        ('[]',hashlib.sha256(b'[]').hexdigest(),self.run_id))
        with self.assertRaisesRegex(evidence.EvidenceError,'STORAGE_CORRUPT'):self.current()
    def test_revocation_and_past_clock_are_not_hidden(self):
        self.current();self.book.record_evidence_state(self.run_id,{'state':'REVOKED','valid_until':None,'revocation_generation':1})
        self.assertIn('EVIDENCE_NOT_CURRENT',self.current()['reasons'])
        earlier=evidence.RunEvidenceBook(self.db,now=999,allowed_bindings=self.allowed)
        with self.assertRaises(evidence.EvidenceError):earlier.current_use(self.run_id,self.binding)
    def test_current_expiry_is_recomputed_with_identical_saved_rows(self):
        self.book.record_evidence_state(self.run_id,{'state':'VALID','valid_until':1001,'revocation_generation':1})
        original=evidence.RunEvidenceStore._validate_store_contents;calls=[]
        def counted(book,*args):calls.append(1);return original(book,*args)
        with patch.object(evidence.RunEvidenceStore,'_validate_store_contents',new=counted):
            self.assertNotIn('EVIDENCE_NOT_CURRENT',self.current()['reasons'])
            later=evidence.RunEvidenceBook(self.db,now=1002,allowed_bindings=self.allowed)
            self.assertIn('EVIDENCE_NOT_CURRENT',later.current_use(self.run_id,self.binding)['reasons'])
            self.assertEqual(len(calls),1)
    def test_cache_limits_fall_back_to_full_validation(self):
        self.current();original=evidence.RunEvidenceStore._validate_store_contents;calls=[]
        def counted(book,*args):calls.append(1);return original(book,*args)
        with patch.object(cache,'_MAX_BYTES',1),patch.object(evidence.RunEvidenceStore,'_validate_store_contents',new=counted):
            self.current();self.current()
        self.assertEqual(len(calls),2)

class MarkerCapacityTests(unittest.TestCase):
    def test_six_completed_runs_remain_cached_and_count_is_bounded(self):
        cache.clear()
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.execute('BEGIN')
        calls=[]
        def validate(*args):calls.append(args[1])
        bound={'case_set':{'cases':[{} for _ in range(400)]}}
        state={'finalized_at':1000}
        def check(run_id):cache.check(db,run_id,1000,{},bound,{},None,state,validate)
        with patch.object(cache,'_snapshot',return_value=b'fixed-row-snapshot'):
            for _ in range(3):
                for i in range(6):check('saved-'+str(i))
            self.assertEqual(len(calls),6)
            for i in range(6,33):check('saved-'+str(i))
            self.assertEqual(len(cache._markers),32)
            check('saved-32');self.assertEqual(len(calls),33)
            check('saved-0');self.assertEqual(len(calls),34)
        cache.clear()


if __name__=='__main__':unittest.main()
