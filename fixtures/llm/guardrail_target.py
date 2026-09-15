"""自作の有限guardrail。版差を持つ合成対象であり、学習済みLLMではない。"""
import json
import sys

FEATURES = {
    "data_handling": ("local_destination", "authorized_recipient", "declared_purpose", "minimized_fields",
                      "marker_masked", "retention_valid", "redistribution_allowed", "approved_source"),
    "work_scope": ("path_allowed", "tool_declared", "operation_allowed", "branch_allowed",
                   "dependency_allowed", "budget_valid", "authorization_present", "output_allowed"),
}
VERSIONS = {
    "baseline-v1": {"miss_masks": (3, 6), "false_alarm_masks": (5, 7)},
    "degraded-v2": {"miss_masks": (3, 6, 9, 11, 13, 15, 18, 20), "false_alarm_masks": (5,)},
}


def evaluate(document, version):
    """入力の述語を評価する。case ID、用途、期待ラベル、oracleを受け取らない。"""
    if type(version) is not str or version not in VERSIONS:
        raise ValueError("TARGET_VERSION_INVALID")
    if type(document) is not dict or set(document) != {"schema_version", "kind", "category", "required", "observed"}:
        raise ValueError("TARGET_INPUT_INVALID")
    category = document["category"]
    if type(category) is not str or category not in FEATURES or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("TARGET_INPUT_INVALID")
    if document["kind"] != "synthetic_policy_input": raise ValueError("TARGET_INPUT_INVALID")
    required, observed = document["required"], document["observed"]
    names = FEATURES[category]
    if (type(required) is not list or not required or any(type(name) is not str or name not in names for name in required)
            or len(set(required)) != len(required) or type(observed) is not dict or set(observed) != set(names)
            or any(type(value) is not bool and value is not None for value in observed.values())):
        raise ValueError("TARGET_INPUT_INVALID")
    mask = sum(1 << index for index, name in enumerate(names) if name in required)
    values = [observed[name] for name in required]
    if False in values:
        detection = "allow" if mask in VERSIONS[version]["miss_masks"] else "detect"
    elif None in values:
        detection = "indeterminate"
    else:
        detection = "detect" if mask in VERSIONS[version]["false_alarm_masks"] else "allow"
    return {"detection": detection, "action": {"allow": "apply", "detect": "block", "indeterminate": "defer"}[detection]}


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536: raise ValueError("TARGET_INPUT_INVALID")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value: raise ValueError("TARGET_INPUT_INVALID")
                value[key] = item
            return value
        request = json.loads(raw, object_pairs_hook=unique)
        if type(request) is not dict or set(request) != {"version", "input"}: raise ValueError("TARGET_INPUT_INVALID")
        result = evaluate(request["input"], request["version"])
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        sys.stdout.write('{"error":"TARGET_INPUT_INVALID"}\n')
        return 2


if __name__ == "__main__": raise SystemExit(main())
