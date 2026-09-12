from pathlib import Path
import importlib.util
import sqlite3
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from gah import read_checks as r
from gah.adoption import AdoptionError

class ReadChecksTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE values_test(value INTEGER)');self.db.execute('INSERT INTO values_test VALUES(1)');self.db.commit()
        self.calls=0
        @r.checked_read
        def read(db, now=0):
            self.calls+=1
            return {'row':db.execute('SELECT * FROM values_test').fetchone(),'nested':[],'now':now}
        self.read=read
    def test_same_transaction_reuses_but_outputs_do_not_alias(self):
        self.db.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):
            first=self.read(self.db);first['nested'].append('changed')
            second=self.read(self.db)
            self.assertEqual(self.calls,1);self.assertEqual(second['nested'],[]);self.assertEqual(second['row'][0],1)
        self.db.rollback()
    def test_request_boundary_and_now_force_recheck(self):
        self.db.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):self.read(self.db);self.read(self.db,1)
        with r.scope(self.db):self.read(self.db)
        self.assertEqual(self.calls,3);self.db.rollback()
    def test_write_and_rollback_clear_results(self):
        self.db.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):
            self.assertEqual(self.read(self.db)['row'][0],1)
            self.db.execute('SAVEPOINT test');self.db.execute('UPDATE values_test SET value=2')
            self.assertEqual(self.read(self.db)['row'][0],2)
            self.db.execute('ROLLBACK TO test')
            # 一度writeがあれば、この領域では以後cacheを使わない。
            self.assertEqual(self.read(self.db)['row'][0],1)
        self.assertEqual(self.calls,3)
        self.db.rollback()
    def test_outside_transaction_is_never_cached(self):
        with r.scope(self.db):self.read(self.db);self.read(self.db)
        self.assertEqual(self.calls,2)
    def test_bool_and_integer_arguments_are_not_same(self):
        self.db.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):self.read(self.db,True);self.read(self.db,1)
        self.assertEqual(self.calls,2);self.db.rollback()
    def test_cycle_and_depth_rejected(self):
        @r.checked_read
        def cyclic(db):return cyclic(db)
        with self.assertRaisesRegex(AdoptionError,'DEPENDENCY_CYCLE'):cyclic(self.db)
        @r.checked_read
        def deep(db,n):return deep(db,n+1)
        with self.assertRaisesRegex(AdoptionError,'DEPENDENCY_LIMIT'):deep(self.db,0)
    def test_exception_does_not_become_cached_success(self):
        counter=[]
        @r.checked_read
        def fail(db):counter.append(1);raise AdoptionError('EXPECTED')
        self.db.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):
            for _ in range(2):
                with self.assertRaises(AdoptionError):fail(self.db)
        self.assertEqual(len(counter),2);self.db.rollback()
    def test_checked_read_cannot_mutate_storage(self):
        @r.checked_read
        def write(db):db.execute('UPDATE values_test SET value=4');return True
        with self.assertRaisesRegex(AdoptionError,'CHECK_MUTATED_STORAGE'):write(self.db)

    def test_total_reference_budget_rejects_more_checks(self):
        @r.checked_read
        def value(db, number):return number
        self.db.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):
            for number in range(r.MAX_NODES):self.assertEqual(value(self.db,number),number)
            with self.assertRaisesRegex(AdoptionError,'DEPENDENCY_LIMIT'):value(self.db,r.MAX_NODES)
        self.db.rollback()
    def test_database_identity_isolated(self):
        other=sqlite3.connect(':memory:');self.addCleanup(other.close)
        other.row_factory=sqlite3.Row
        other.execute('CREATE TABLE values_test(value INTEGER)');other.execute('INSERT INTO values_test VALUES(9)');other.commit()
        self.db.execute('BEGIN IMMEDIATE');other.execute('BEGIN IMMEDIATE')
        with r.scope(self.db):
            self.assertEqual(self.read(self.db)['row'][0],1)
            self.assertEqual(self.read(other)['row'][0],9)
            self.assertEqual(self.read(self.db)['row'][0],1)
        self.assertEqual(self.calls,2);self.db.rollback();other.rollback()

if __name__=='__main__':unittest.main(verbosity=2)

