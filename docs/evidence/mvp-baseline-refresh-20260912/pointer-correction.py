"""current破損の確認後、全体回帰が完了してから最小のガードを追加する。"""
from pathlib import Path
import ast
import hashlib
import json
ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent


def main():
    result=json.loads((LOCAL/"pointer-before.json").read_text("utf-8"))
    assert all(row["corrupt_pointer_result"]=="accepted" for row in result["observations"])
    units=json.loads((LOCAL/"units-01/check.json").read_text("utf-8"))
    assert units["passed"]
    changes={}
    p=ROOT/"src/gah/baseline_generations.py";s=p.read_text("utf-8")
    a="def bind_candidate(db, proposal, source, now):\n";assert s.count(a)==1
    helper='''def current_predecessor(db, proposal):
    previous, record = predecessor(db, proposal)
    current = db.execute("SELECT * FROM baseline_current WHERE series_id=?", (proposal["series_id"],)).fetchone()
    if (current is None or current["generation"] != 1
            or current["adoption_id"] != previous["adoption_id"]):
        raise AdoptionError("GENERATION_CONFLICT")
    _, current_record, _ = base._history(db, current)
    if current_record != record:
        raise AdoptionError("STORAGE_CORRUPT")
    return previous, current


'''
    s=s.replace(a,helper+a)
    a='''    previous, _ = predecessor(db, proposal)
    current = db.execute("SELECT adoption_id FROM baseline_current WHERE series_id=?", (request["series_id"],)).fetchone()
    if current[0] != previous["adoption_id"]:
        raise AdoptionError("GENERATION_CONFLICT")
''';assert s.count(a)==1;s=s.replace(a,"    current_predecessor(db, proposal)\n")
    a='''    previous, _ = predecessor(db, proposal)
    current = db.execute("SELECT * FROM baseline_current WHERE series_id=?", (proposal["series_id"],)).fetchone()
''';assert s.count(a)==1;s=s.replace(a,"    previous, current = current_predecessor(db, proposal)\n")
    changes[p]=s
    p=ROOT/"src/gah/baseline_authority.py";s=p.read_text("utf-8")
    a='''    if proposal["expected_generation"] != 0 and _current_generation(db, proposal["series_id"]) != proposal["expected_generation"]:
        raise _error("PREREQUISITE_UNAVAILABLE")
''';assert s.count(a)==1
    s=s.replace(a,'''    if proposal["expected_generation"] != 0:
        if _current_generation(db, proposal["series_id"]) != proposal["expected_generation"]:
            raise _error("PREREQUISITE_UNAVAILABLE")
        from .baseline_generations import current_predecessor
        current_predecessor(db, proposal)
''')
    changes[p]=s
    p=ROOT/"tests/test_baseline_refresh.py";s=p.read_text("utf-8")
    a="    def test_update_failure_rolls_back_pointer_history_and_receipt(self):\n";assert s.count(a)==1
    s=s.replace(a,'''    def test_corrupt_current_pointer_cannot_be_hidden_by_refresh(self):
        for stage in ("propose", "validate", "adopt"):
            with self.subTest(stage=stage):
                self.source()
                if stage in {"validate", "adopt"}:
                    self.propose()
                if stage == "adopt":
                    self.validate()
                self.store._db.execute("UPDATE baseline_current SET baseline_digest=?", ("0" * 64,))
                tables = ("baseline_proposals", "baseline_validations", "baseline_adoptions", "baseline_current", "idempotency")
                before = {name: tuple(self.store._db.execute("SELECT * FROM " + name + " ORDER BY rowid")) for name in tables}
                with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                    getattr(self, stage)()
                self.assertEqual({name: tuple(self.store._db.execute("SELECT * FROM " + name + " ORDER BY rowid")) for name in tables}, before)

'''+a)
    changes[p]=s
    for text in changes.values(): ast.parse(text)
    before={path.relative_to(ROOT).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in changes}
    for path,text in changes.items():
        backup=LOCAL/"pointer-review-before"/path.relative_to(ROOT);assert not backup.exists()
        backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(path.read_bytes())
        path.write_text(text,"utf-8")
    with (LOCAL/"pointer-review-before.json").open("x",encoding="utf-8") as stream:
        json.dump({"source_sha256":before,"unit_check_sha256":hashlib.sha256((LOCAL/"units-01/check.json").read_bytes()).hexdigest()},stream,indent=2)
    print("3 files updated; previous full regression and pre-fix sources retained")


if __name__=="__main__":
    main()
