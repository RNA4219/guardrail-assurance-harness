"""保存された一操作の資源状態と固定planへの結合を照合する。"""
from copy import deepcopy
from gah.contracts import ContractError, require_object, require_id, require_ref, require_uint
from gah.ledger import Ledger, LedgerError
from gah.resources import ResourceBook, ResourceError, _unpack

ACTIONS = {"resource_operation": {"operator", "validator"}}
FRESH_ACTIONS = set(ACTIONS)
FIELDS = {"schema_version", "action", "request_id", "run_id", "operation_id", "expected_manifest_ref"}


def validate_request(value):
    try:
        require_object(value, FIELDS)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["action"] != "resource_operation":
            raise ContractError()
        for key in ("request_id", "run_id", "operation_id"):
            require_id(value[key])
        require_ref(value["expected_manifest_ref"])
        if value["expected_manifest_ref"]["kind"] != "run_manifest" or value["expected_manifest_ref"]["id"] != value["run_id"]:
            raise ContractError()
    except (ContractError, KeyError, TypeError, ValueError):
        raise ResourceError("INVALID_REQUEST") from None
    return deepcopy(value)


def inspect_operation(db, request, now):
    """認証・transaction・時計は接続側が提供する。返却は状態であり再送許可ではない。"""
    request = validate_request(request)
    book = ResourceBook(db)
    book._touch(now)
    run = book._run(request["run_id"])
    if run["manifest_digest"] != request["expected_manifest_ref"]["digest"]:
        raise ResourceError("BINDING_MISMATCH")
    op = book._operation(request["run_id"], request["operation_id"])
    try:
        policy = book._policy(run)
        reservation = book._reservation(_unpack(op["reservation_json"], op["reservation_digest"]), policy)
        for key in ("created_at", "owner_epoch"):
            require_uint(run[key])
        require_uint(op["owner_epoch"])
        require_uint(op["exposure_micros"])
        if not 1 <= op["owner_epoch"] <= run["owner_epoch"] or run["created_at"] > now:
            raise ResourceError("STORAGE_CORRUPT")
        for key in ("released", "conflicted"):
            if type(op[key]) is not int or op[key] not in (0, 1):
                raise ResourceError("STORAGE_CORRUPT")
        for key in ("intended_at", "stopped_at", "settled_at"):
            if op[key] is not None:
                require_uint(op[key])
                if not run["created_at"] <= op[key] <= now:
                    raise ResourceError("STORAGE_CORRUPT")
        intended = op["intended_at"]
        if (op["released"] and intended is not None
                or op["stopped_at"] is not None and (intended is None or op["stopped_at"] < intended)
                or op["settled_at"] is not None and (intended is None or op["settled_at"] < intended)
                or op["conflicted"] and intended is None
                or not op["conflicted"] and op["exposure_micros"] != 0):
            raise ResourceError("STORAGE_CORRUPT")
        usage_keys = ("usage_json", "usage_digest", "settled_at", "cost_micros")
        present = [op[key] is not None for key in usage_keys]
        if any(present) != all(present):
            raise ResourceError("STORAGE_CORRUPT")
        usage = None
        if all(present):
            usage = _unpack(op["usage_json"], op["usage_digest"])
            require_object(usage, {"input_tokens", "output_tokens", "cost_usd"})
            for key in ("input_tokens", "output_tokens"):
                require_uint(usage[key])
            _, cost = Ledger._parse_usd(usage["cost_usd"])
            require_uint(cost)
            require_uint(op["cost_micros"])
            if cost != op["cost_micros"] or cost == 0 and reservation["billing_mode"] != "non_billed_local":
                raise ResourceError("STORAGE_CORRUPT")
        result = {"run_id": request["run_id"], "operation_id": request["operation_id"],
            "manifest_ref": request["expected_manifest_ref"], "owner_epoch": op["owner_epoch"],
            "reservation_digest": op["reservation_digest"], "reservation": reservation,
            "dispatch_intended": intended is not None, "stopped": op["stopped_at"] is not None,
            "settled": op["settled_at"] is not None, "released": bool(op["released"]),
            "conflicted": bool(op["conflicted"]), "usage": usage, "cost_usd_micros": op["cost_micros"],
            "exposure_usd_micros": op["exposure_micros"], "checked_at": now, "ci_eligible": False}
        return deepcopy(result)
    except (ContractError, LedgerError, KeyError, TypeError, ValueError):
        raise ResourceError("STORAGE_CORRUPT") from None


def bound_operation(db, request, now, read_bound):
    """保存時のplanへの結合を照合する。現在の開始許可は要求しない。"""
    import hashlib
    from gah.run_contracts import content_ref
    from gah.wire import canonical_bytes
    value = inspect_operation(db, request, now)
    bound, _ = read_bound(request['run_id'])
    if content_ref('run_manifest', request['run_id'], bound['manifest']) != request['expected_manifest_ref']:
        raise ResourceError('BINDING_MISMATCH')
    row = db.execute('SELECT * FROM resource_bindings WHERE operation_id=?', (request['operation_id'],)).fetchone()
    if row is None or row['run_id'] != request['run_id']:
        raise ResourceError('STORAGE_CORRUPT')
    entries = [entry for entry in bound['plan']['entries']
        if content_ref('trial_entry', 'entry', entry)['digest'] == row['entry_digest']]
    if len(entries) != 1:
        raise ResourceError('STORAGE_CORRUPT')
    entry = entries[0]
    # 保存されたevaluator/targetを使い、将来のimage設定や新規開始可否に依存させない。
    if row['scenario'].startswith('guardrail:'):
        from .llm_admission import check_entry
        check_entry(db, request['run_id'], entry, row['scenario'], now)
    else:
        expected = hashlib.sha256(canonical_bytes({'worker_digest': entry['evaluator_ref']['digest'],
            'scenario': row['scenario']})).hexdigest()
        if entry['target_ref']['digest'] != expected or len(entry['stage_ids']) != 1:
            raise ResourceError('STORAGE_CORRUPT')
    return {**value, 'entry': deepcopy(entry), 'scenario': row['scenario'], 'entry_digest': row['entry_digest']}
