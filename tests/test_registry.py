"""Control Registryの構造・依存閉包・影響範囲を検査する。"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.registry import affected_controls, dependency_closure, validate_registry


_DIGEST = "0" * 64


def _ref(kind: str, identifier: str) -> dict[str, str]:
    return {"kind": kind, "id": identifier, "digest": _DIGEST}


def _control(index: int, dependency: str | None = None, *, mutation: bool = True) -> dict:
    control_id = f"C{index:02d}"
    obligation = {
        "obligation_id": f"O{index:02d}",
        "kind": "mutation" if mutation else "constraint",
        "required": True,
        "event_policy": "aggregate" if mutation else "none",
        "evaluator_ref": _ref("evaluator", f"E{index:02d}"),
    }
    return {
        "control_id": control_id,
        "owner": "assurance-owner",
        "invariant": f"Invariant for {control_id}",
        "criticality": "critical" if index == 0 else "noncritical",
        "target_ref": _ref("target", f"T{index:02d}"),
        "dependencies": [] if dependency is None else [dependency],
        "obligations": [obligation],
        "mutation_applicability": {
            "status": "applicable" if mutation else "not_applicable",
            "reason": None if mutation else "この対象にはmutationを適用しない",
        },
    }


def _registry() -> dict:
    return {
        "schema_version": 1,
        "kind": "control_registry",
        "registry_id": "registry-main",
        "controls": [
            _control(0, mutation=False),
            *[_control(index, f"C{index - 1:02d}") for index in range(1, 10)],
        ],
    }


class RegistryTests(unittest.TestCase):
    def test_ten_control_registry_is_independent_and_graph_queries_are_sorted(self):
        document = _registry()
        validated = validate_registry(document)
        self.assertEqual([control["control_id"] for control in validated["controls"]], [f"C{i:02d}" for i in range(10)])
        validated["controls"][0]["invariant"] = "変更後"
        self.assertEqual(document["controls"][0]["invariant"], "Invariant for C00")
        self.assertEqual(dependency_closure(document, ["C05", "C02"]), [f"C{i:02d}" for i in range(6)])
        self.assertEqual(affected_controls(document, ["C00"]), [f"C{i:02d}" for i in range(10)])
        self.assertEqual(affected_controls(document, None), [f"C{i:02d}" for i in range(10)])

    def test_invalid_registry_shapes_and_policy_downgrades_are_rejected(self):
        cases = []

        unknown = _registry()
        unknown["unexpected"] = True
        cases.append(unknown)

        duplicate_control = _registry()
        duplicate_control["controls"].append(copy.deepcopy(duplicate_control["controls"][0]))
        cases.append(duplicate_control)

        missing_dependency = _registry()
        missing_dependency["controls"][1]["dependencies"] = ["missing"]
        cases.append(missing_dependency)

        cycle = _registry()
        cycle["controls"][0]["dependencies"] = ["C09"]
        cases.append(cycle)

        critical_optional = _registry()
        critical_optional["controls"][0]["obligations"][0]["required"] = False
        cases.append(critical_optional)

        applicable_without_mutation = _registry()
        applicable_without_mutation["controls"][1]["obligations"][0]["kind"] = "constraint"
        applicable_without_mutation["controls"][1]["obligations"][0]["event_policy"] = "none"
        cases.append(applicable_without_mutation)

        not_applicable_with_mutation = _registry()
        not_applicable_with_mutation["controls"][0]["mutation_applicability"] = {
            "status": "not_applicable", "reason": "対象外"
        }
        not_applicable_with_mutation["controls"][0]["obligations"][0]["kind"] = "mutation"
        cases.append(not_applicable_with_mutation)

        strict_bool = _registry()
        strict_bool["controls"][1]["obligations"][0]["required"] = 1
        cases.append(strict_bool)

        for document in cases:
            with self.subTest(document=document):
                with self.assertRaises(ContractError):
                    validate_registry(document)

    def test_queries_reject_empty_duplicate_and_unknown_selection(self):
        document = _registry()
        for query in (
            lambda: dependency_closure(document, []),
            lambda: dependency_closure(document, ["C01", "C01"]),
            lambda: dependency_closure(document, ["missing"]),
            lambda: affected_controls(document, []),
            lambda: affected_controls(document, ["C01", "C01"]),
            lambda: affected_controls(document, ["missing"]),
        ):
            with self.assertRaises(ContractError):
                query()

    def test_schema_matches_public_limits_and_shape(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "control-registry.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(schema["properties"]["controls"]["maxItems"], 1000)
        self.assertEqual(schema["$defs"]["control"]["properties"]["obligations"]["maxItems"], 100)
        self.assertEqual(schema["$defs"]["control"]["properties"]["dependencies"]["maxItems"], 100)

    def test_maximum_length_dependency_chain_and_cycle_are_iterative(self):
        document = _registry()
        document["controls"] = [
            _control(index, None if index == 0 else f"C{index - 1:02d}")
            for index in range(1000)
        ]
        validated = validate_registry(document)
        self.assertEqual(len(validated["controls"]), 1000)
        document["controls"][0]["dependencies"] = ["C999"]
        with self.assertRaises(ContractError):
            validate_registry(document)

    def test_direct_dict_canonical_size_limit_is_enforced(self):
        document = _registry()
        document["controls"] = [
            _control(index, None if index == 0 else f"C{index - 1:02d}")
            for index in range(1000)
        ]
        for control in document["controls"]:
            control["owner"] = "o" * 512
            control["invariant"] = "i" * 512
        with self.assertRaises(ContractError):
            validate_registry(document)


if __name__ == "__main__":
    unittest.main()
