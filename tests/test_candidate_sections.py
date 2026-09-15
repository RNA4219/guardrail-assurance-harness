"""候補の輸送上限を維持し、保存rootと分割本文の参照を照合する。"""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah import candidate_sections as sections
from gah.contracts import MAX_DOCUMENT_BYTES,ContractError
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


class CandidateSectionTests(unittest.TestCase):
    def test_large_candidate_reference_matches_saved_root_and_changed_section_is_detected(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE authority_artifacts(kind,id,digest,payload_json,run_id,PRIMARY KEY(kind,id,digest))')
        runs={name:{'payload':'x'*500000} for name in sections.KINDS};runs.update(sections.FLAGS)
        candidate={'candidate_id':'large','runs':runs}
        packed={'candidate_id':'large','runs':sections.pack(db,'large',runs)}
        self.assertLess(len(canonical_bytes(packed)),MAX_DOCUMENT_BYTES)
        self.assertEqual(sections.reference(candidate),content_ref('contract_candidate','large',packed))
        self.assertEqual(sections.unpack(db,'large',packed['runs']),runs)
        packed['runs']['new_ref']=packed['runs']['old_ref']
        with self.assertRaises(ContractError):sections.unpack(db,'large',packed['runs'])

    def test_verified_stored_root_reference_is_identical_for_small_and_large_candidates(self):
        from gah import resources
        from unittest.mock import patch
        for size in (8, 500000):
            db = sqlite3.connect(':memory:')
            self.addCleanup(db.close)
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE authority_artifacts(kind,id,digest,payload_json,run_id,PRIMARY KEY(kind,id,digest))')
            candidate = {'candidate_id':'stored', 'runs':{name:{'payload':'x'*size} for name in sections.KINDS}|sections.FLAGS}
            saved = {**candidate, 'runs':sections.pack(db, 'stored', candidate['runs'])}
            raw, digest = resources._packed(saved)
            row = {'payload_json':raw, 'digest':digest}
            expected = sections.reference(candidate)
            with patch.object(sections, 'reference', side_effect=AssertionError('rehashed expanded candidate')):
                self.assertEqual(sections.stored_reference(row, 'stored'), expected)
            with self.assertRaises(ContractError):
                sections.stored_reference(row, 'different')
            with self.assertRaises(ContractError):
                sections.stored_reference({**row, 'payload_json':raw+' '}, 'stored')
            with self.assertRaises(ContractError):
                sections.stored_reference({**row, 'digest':'0'*64}, 'stored')

    def test_reference_does_not_clone_or_mutate_large_input(self):
        from unittest.mock import patch
        candidate = {'candidate_id':'ownership', 'runs':{
            name:{'nested':['x'*500000]} for name in sections.KINDS}|sections.FLAGS}
        before = canonical_bytes(candidate)
        with patch.object(sections, 'deepcopy', side_effect=AssertionError('unnecessary copy')):
            first = sections.reference(candidate)
        self.assertEqual(canonical_bytes(candidate), before)
        candidate['runs']['new']['nested'][0] = 'changed'
        self.assertNotEqual(first, sections.reference(candidate))

    def test_legacy_small_candidate_reference_is_unchanged(self):
        candidate={'candidate_id':'small','runs':{name:{} for name in sections.KINDS}|sections.FLAGS}
        self.assertEqual(sections.reference(candidate),content_ref('contract_candidate','small',candidate))


class ChunkedCandidateSectionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE authority_artifacts(kind,id,digest,payload_json,run_id,PRIMARY KEY(kind,id,digest))')
        self.runs = {name:{'text':'境界😀' * 140000} for name in sections.KINDS} | sections.FLAGS

    def save(self):
        return sections.pack(self.db, 'chunked', self.runs)

    def rewrite(self, ref, mutate):
        row = self.db.execute('SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=?',
            tuple(ref[key] for key in ('kind','id','digest'))).fetchone()
        value = json.loads(row['payload_json']); mutate(value)
        updated = content_ref(ref['kind'], ref['id'], value)
        self.db.execute('INSERT OR REPLACE INTO authority_artifacts VALUES(?,?,?,?,?)',
            (ref['kind'], ref['id'], updated['digest'], canonical_bytes(value).decode('utf8'), 'chunked'))
        return updated

    def test_multibyte_boundaries_preserve_payload_reference_and_independent_values(self):
        saved = self.save()
        self.assertGreater(self.db.execute("SELECT COUNT(*) FROM authority_artifacts WHERE kind='candidate_section_chunk'").fetchone()[0], 0)
        first = sections.unpack(self.db, 'chunked', saved)
        self.assertEqual(first, self.runs)
        first['new']['text'] = 'changed'
        self.assertEqual(sections.unpack(self.db, 'chunked', saved), self.runs)
        self.assertEqual(sections.reference({'candidate_id':'chunked','runs':self.runs}),
            content_ref('contract_candidate', 'chunked', {'candidate_id':'chunked','runs':saved}))
        self.assertTrue(all(len(row[0].encode('utf8')) <= MAX_DOCUMENT_BYTES
            for row in self.db.execute('SELECT payload_json FROM authority_artifacts')))

    def test_missing_corrupt_and_foreign_chunks_are_rejected(self):
        for change in ('delete', 'payload', 'owner'):
            with self.subTest(change=change):
                self.db.execute('DELETE FROM authority_artifacts'); saved = self.save()
                row = self.db.execute("SELECT rowid FROM authority_artifacts WHERE kind='candidate_section_chunk' LIMIT 1").fetchone()
                if change == 'delete': self.db.execute('DELETE FROM authority_artifacts WHERE rowid=?', (row[0],))
                elif change == 'payload': self.db.execute("UPDATE authority_artifacts SET payload_json=payload_json || ' ' WHERE rowid=?", (row[0],))
                else: self.db.execute("UPDATE authority_artifacts SET run_id='other' WHERE rowid=?", (row[0],))
                with self.assertRaises(ContractError): sections.unpack(self.db, 'chunked', saved)

    def test_redigested_chunk_order_size_digest_and_unknown_versions_are_rejected(self):
        for change in ('order', 'size', 'digest', 'version', 'count', 'extra'):
            with self.subTest(change=change):
                self.db.execute('DELETE FROM authority_artifacts'); saved = self.save()
                def mutate(value):
                    if change == 'order': value['chunks'].reverse()
                    elif change == 'size': value['payload_bytes'] += 1
                    elif change == 'digest': value['payload_digest'] = '0' * 64
                    elif change == 'version': value['schema_version'] = 3
                    elif change == 'count': value['chunks'].pop()
                    else: value['extra'] = True
                saved['new_ref'] = self.rewrite(saved['new_ref'], mutate)
                with self.assertRaises(ContractError): sections.unpack(self.db, 'chunked', saved)

    def test_redigested_invalid_chunk_content_is_rejected(self):
        saved = self.save()
        def modify_section(section):
            section['chunks'][0] = self.rewrite(section['chunks'][0], lambda chunk:chunk.update(index=True))
        saved['new_ref'] = self.rewrite(saved['new_ref'], modify_section)
        with self.assertRaises(ContractError): sections.unpack(self.db, 'chunked', saved)

    def test_section_size_limit_and_late_storage_failure_roll_back(self):
        self.runs['transition'] = {'text':'x' * sections._MAX_SECTION_BYTES}
        with self.assertRaises(ContractError):
            with self.db: self.save()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM authority_artifacts').fetchone()[0], 0)
        self.runs['transition'] = {'text':'small'}
        self.db.execute("CREATE TRIGGER fail_late BEFORE INSERT ON authority_artifacts WHEN NEW.kind='candidate_section_chunk' AND instr(NEW.payload_json, '\"index\":1')>0 BEGIN SELECT RAISE(ABORT,'fixed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db: self.save()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM authority_artifacts').fetchone()[0], 0)


if __name__=='__main__':unittest.main()
