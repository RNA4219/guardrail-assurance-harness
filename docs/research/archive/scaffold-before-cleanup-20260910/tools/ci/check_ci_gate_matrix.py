# SPDX-License-Identifier: MIT
# Copyright 2025 RNA4219

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    class _MiniYamlModule:
        @staticmethod
        def safe_load(content: str) -> dict[str, str]:
            result: dict[str, str] = {}
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                key, _, value = stripped.partition(":")
                result[key.strip()] = value.strip()
            return result
    yaml = _MiniYamlModule()

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.ci.check_branch_protection import load_policy_required_jobs

_DEFAULT_POLICY = _REPO_ROOT / "governance" / "policy.yaml"
_DEFAULT_CI_CONFIG = _REPO_ROOT / "docs" / "ci-config.md"

VALID_CHECKER_STAGES = ("observe", "warn", "enforce")

LOGICAL_TO_WORKFLOW = {
    "governance-gate": ".github/workflows/governance-gate.yml",
    "python-ci": ".github/workflows/test.yml",
    "security-ci": ".github/workflows/security.yml",
    "docs-gate": ".github/workflows/markdown.yml",
}


LOGICAL_TO_EXPECTED_CHECKS = {
    "governance-gate": ("governance",),
    "python-ci": ("unit",),
    "security-ci": ("Allowlist Guard", "Semgrep", "Bandit", "Gitleaks", "Dependency Audit & SBOM"),
    "docs-gate": ("docs-gate",),
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return not self.errors

    def emit(self) -> None:
        for message in self.errors:
            print(message, file=sys.stderr)
        for message in self.warnings:
            print(message, file=sys.stderr)


def load_checker_stages(policy_path: Path) -> dict[str, str]:
    """Load checker stages from governance/policy.yaml."""
    try:
        content = policy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        loaded = yaml.safe_load(content)
    except Exception:
        return {}
    if loaded is None or not isinstance(loaded, dict):
        return {}
    ci_section = loaded.get("ci")
    if ci_section is None or not isinstance(ci_section, dict):
        return {}
    stages = ci_section.get("checker_stages")
    if stages is None or not isinstance(stages, dict):
        return {}
    return {str(k): str(v) for k, v in stages.items()}


def validate_ci_gate_matrix(
    *,
    repo_root: Path,
    policy_path: Path,
    ci_config_path: Path,
) -> ValidationResult:
    result = ValidationResult()
    required_jobs = load_policy_required_jobs(policy_path)
    ci_config_text = ci_config_path.read_text(encoding="utf-8")

    for logical_id in required_jobs:
        workflow_rel = LOGICAL_TO_WORKFLOW.get(logical_id)
        concrete_checks = LOGICAL_TO_EXPECTED_CHECKS.get(logical_id)

        if workflow_rel is None:
            result.errors.append(f"No workflow mapping is defined for logical gate ID '{logical_id}'.")
            continue
        if concrete_checks is None:
            result.errors.append(f"No concrete check mapping is defined for logical gate ID '{logical_id}'.")
            continue

        workflow_path = repo_root / workflow_rel
        if not workflow_path.is_file():
            result.errors.append(
                f"Workflow file for logical gate ID '{logical_id}' is missing: {workflow_rel}"
            )
            continue

        workflow_text = workflow_path.read_text(encoding="utf-8")
        for concrete_check in concrete_checks:
            if concrete_check not in workflow_text:
                result.errors.append(
                    f"Workflow '{workflow_rel}' does not mention expected check/job '{concrete_check}' for '{logical_id}'."
                )

        if logical_id not in ci_config_text:
            result.errors.append(f"docs/ci-config.md does not mention logical gate ID '{logical_id}'.")
        if workflow_rel not in ci_config_text:
            result.errors.append(f"docs/ci-config.md does not mention workflow path '{workflow_rel}'.")
        for concrete_check in concrete_checks:
            if concrete_check not in ci_config_text:
                result.errors.append(
                    f"docs/ci-config.md does not mention concrete check/job '{concrete_check}' for '{logical_id}'."
                )

    # Validate checker_stages
    checker_stages = load_checker_stages(policy_path)
    for gate_id, stage in checker_stages.items():
        if stage not in VALID_CHECKER_STAGES:
            result.errors.append(
                f"Invalid stage '{stage}' for '{gate_id}' in policy.yaml. Must be one of: {', '.join(VALID_CHECKER_STAGES)}"
            )
        if gate_id not in ci_config_text:
            result.warnings.append(
                f"Checker '{gate_id}' in policy.yaml is not documented in docs/ci-config.md."
            )

    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate governance/policy.yaml logical gate IDs against workflows and docs/ci-config.md."
    )
    parser.add_argument("--policy", type=Path, default=_DEFAULT_POLICY, help="Path to governance/policy.yaml.")
    parser.add_argument(
        "--ci-config",
        type=Path,
        default=_DEFAULT_CI_CONFIG,
        help="Path to docs/ci-config.md.",
    )
    args = parser.parse_args(argv)

    result = validate_ci_gate_matrix(
        repo_root=_REPO_ROOT,
        policy_path=args.policy,
        ci_config_path=args.ci_config,
    )
    result.emit()
    if result.is_success:
        print("CI gate matrix matches governance/policy.yaml, workflows, and docs/ci-config.md.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
