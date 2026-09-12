"""公開した空v2 schemaから、テスト専用DBを毎回生成する。"""
import hashlib
from pathlib import Path
import sqlite3

FIXTURE = Path(__file__).parent / "fixtures/adoption-v2.sql"
FIXTURE_SHA256 = "ea78d09efebade6b9b0cc6ca0547f698f852f645fa11a968ae55f687fe4269df"


def create_legacy_adoption_fixture(path: Path) -> None:
    data = FIXTURE.read_bytes()
    if hashlib.sha256(data).hexdigest() != FIXTURE_SHA256:
        raise ValueError("LEGACY_FIXTURE_CHANGED")
    with path.open("xb"):
        pass
    db = sqlite3.connect(path)
    try:
        db.executescript(data.decode("utf-8"))
    finally:
        db.close()
