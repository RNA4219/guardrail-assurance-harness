"""候補を上限内の不変artifactへ分割し、完全参照と本文を復元時に照合する。"""
from copy import deepcopy
import base64
import hashlib
from .contracts import ContractError, MAX_DOCUMENT_BYTES, require_digest, require_object, require_ref
from . import resources
from .run_contracts import content_ref
from .wire import canonical_bytes

KINDS = ("transition", "old", "new")
FLAGS = {"structurally_bound":True, "authority_connected":False, "ci_eligible":False}
_CHUNK_BYTES = 256 * 1024
_MAX_SECTION_BYTES = 4 * 1024 * 1024


def _identifier(candidate_id, name, index=None):
    values = [candidate_id, name] if index is None else [candidate_id, name, index]
    return ("cs-" if index is None else "cc-") + hashlib.sha256(canonical_bytes(values)).hexdigest()[:40]


def _documents(candidate_id, name, payload):
    """v1の参照を保持し、1文書を超える節のみ有界なv2へ分割する。"""
    section = {"schema_version":1, "kind":"candidate_section", "candidate_id":candidate_id,
               "section":name, "payload":payload}
    identifier = _identifier(candidate_id, name)
    if len(canonical_bytes(section)) <= MAX_DOCUMENT_BYTES:
        yield identifier, section
        return
    raw = canonical_bytes(payload)
    if len(raw) > _MAX_SECTION_BYTES:
        raise ContractError("CANDIDATE_SECTION_SIZE")
    refs = []
    for index, start in enumerate(range(0, len(raw), _CHUNK_BYTES)):
        chunk = {"schema_version":1, "kind":"candidate_section_chunk", "candidate_id":candidate_id,
                 "section":name, "index":index, "data":base64.b64encode(raw[start:start+_CHUNK_BYTES]).decode("ascii")}
        chunk_id = _identifier(candidate_id, name, index)
        refs.append(content_ref("candidate_section_chunk", chunk_id, chunk))
        yield chunk_id, chunk
    yield identifier, {"schema_version":2, "kind":"candidate_section", "candidate_id":candidate_id,
        "section":name, "payload_bytes":len(raw), "payload_digest":hashlib.sha256(raw).hexdigest(), "chunks":refs}


def pack(db, candidate_id, runs):
    require_object(runs, set(KINDS) | set(FLAGS))
    if any(runs[key] is not value for key,value in FLAGS.items()):
        raise ContractError("CANDIDATE_INVALID")
    if len(canonical_bytes(runs)) < MAX_DOCUMENT_BYTES - 4096:
        return deepcopy(runs)
    from .assurance_authority import _save
    result = {"schema_version":1, "kind":"candidate_run_sections", "candidate_id":candidate_id, **FLAGS}
    for name in KINDS:
        for identifier, document in _documents(candidate_id, name, runs[name]):
            ref = _save(db, candidate_id, document["kind"], identifier, document)
        result[name + "_ref"] = ref
    return result


def reference(candidate):
    """各節の完全refを持つ小さい保存rootを参照する。本文をコピーしない。"""
    root = dict(candidate)
    runs = root["runs"]; identifier = root["candidate_id"]
    if len(canonical_bytes(runs)) >= MAX_DOCUMENT_BYTES - 4096:
        sections = {"schema_version":1, "kind":"candidate_run_sections", "candidate_id":identifier, **FLAGS}
        for name in KINDS:
            for section_id, document in _documents(identifier, name, runs[name]):
                if document["kind"] == "candidate_section":
                    sections[name+"_ref"] = content_ref("candidate_section", section_id, document)
        root["runs"] = sections
    return content_ref("contract_candidate", identifier, root)


def stored_reference(row, candidate_id):
    """load_candidateで本文との一致を検証した後、保存rootを参照する。"""
    try:
        root = resources._unpack(row["payload_json"], row["digest"])
        if type(root) is not dict or root.get("candidate_id") != candidate_id:
            raise ContractError()
        return content_ref("contract_candidate", candidate_id, root)
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError):
        raise ContractError("CANDIDATE_SECTION_INVALID") from None


def _load(db, candidate_id, ref, kind, identifier):
    require_ref(ref)
    if ref["kind"] != kind or ref["id"] != identifier:
        raise ContractError()
    row = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=? "
        "AND length(CAST(payload_json AS BLOB))<=?", (kind, identifier, ref["digest"], MAX_DOCUMENT_BYTES)).fetchone()
    if row is None or row["run_id"] != candidate_id:
        raise ContractError()
    return resources._unpack(row["payload_json"], row["digest"])


def _payload(db, candidate_id, name, section):
    common = {"schema_version", "kind", "candidate_id", "section"}
    if (type(section) is not dict or type(section.get("schema_version")) is not int
            or section.get("kind") != "candidate_section" or section.get("candidate_id") != candidate_id
            or section.get("section") != name):
        raise ContractError()
    if section["schema_version"] == 1:
        require_object(section, common | {"payload"})
        return section["payload"]
    require_object(section, common | {"payload_bytes", "payload_digest", "chunks"})
    size = section["payload_bytes"]
    refs = section["chunks"]
    if (section["schema_version"] != 2 or type(size) is not int or not 1 <= size <= _MAX_SECTION_BYTES
            or type(refs) is not list or len(refs) != (size + _CHUNK_BYTES - 1) // _CHUNK_BYTES):
        raise ContractError()
    require_digest(section["payload_digest"])
    blocks = []
    for index, ref in enumerate(refs):
        chunk = _load(db, candidate_id, ref, "candidate_section_chunk", _identifier(candidate_id, name, index))
        require_object(chunk, common | {"index", "data"})
        if (type(chunk["schema_version"]) is not int or chunk["schema_version"] != 1
                or chunk["kind"] != "candidate_section_chunk" or chunk["candidate_id"] != candidate_id
                or chunk["section"] != name or type(chunk["index"]) is not int or chunk["index"] != index
                or type(chunk["data"]) is not str):
            raise ContractError()
        raw = base64.b64decode(chunk["data"].encode("ascii"), validate=True)
        if (len(raw) != min(_CHUNK_BYTES, size - index * _CHUNK_BYTES)
                or base64.b64encode(raw).decode("ascii") != chunk["data"]):
            raise ContractError()
        blocks.append(raw)
    raw = b"".join(blocks)
    payload = resources._unpack(raw.decode("utf8"), section["payload_digest"])
    legacy = {"schema_version":1, "kind":"candidate_section", "candidate_id":candidate_id,
              "section":name, "payload":payload}
    if len(canonical_bytes(legacy)) <= MAX_DOCUMENT_BYTES:
        raise ContractError()
    return payload


def unpack(db, candidate_id, value):
    if type(value) is not dict or value.get("kind") != "candidate_run_sections":
        return deepcopy(value)
    try:
        require_object(value, {"schema_version","kind","candidate_id"} | set(FLAGS) | {name+"_ref" for name in KINDS})
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["candidate_id"] != candidate_id
                or any(value[key] is not expected for key,expected in FLAGS.items())):
            raise ContractError()
        result = dict(FLAGS)
        for name in KINDS:
            section = _load(db, candidate_id, value[name+"_ref"], "candidate_section", _identifier(candidate_id, name))
            result[name] = _payload(db, candidate_id, name, section)
        return result
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError, UnicodeError):
        raise ContractError("CANDIDATE_SECTION_INVALID") from None
