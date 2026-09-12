# SPDX-License-Identifier: MIT
# Copyright 2025 RNA4219

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_POLICY = _REPO_ROOT / "governance" / "policy.yaml"

# Logical gate IDs in governance/policy.yaml map to one or more concrete GitHub
# check names for this repository. Downstream repositories may use different
# concrete names.
LOGICAL_TO_REPO_CHECK = {
    "governance-gate": ("governance",),
    "python-ci": ("unit",),
    "security-ci": ("Allowlist Guard", "Semgrep", "Bandit", "Gitleaks", "Dependency Audit & SBOM"),
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

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "pass" if self.is_success else "fail",
            "errors": self.errors,
            "warnings": self.warnings,
        }


def load_policy_required_jobs(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    required_jobs: list[str] = []
    in_ci_block = False
    in_required_jobs = False
    ci_indent = None
    required_indent = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))

        if stripped == "ci:":
            in_ci_block = True
            in_required_jobs = False
            ci_indent = indent
            continue

        if in_ci_block and ci_indent is not None and indent <= ci_indent:
            in_ci_block = False
            in_required_jobs = False

        if not in_ci_block:
            continue

        if stripped == "required_jobs:":
            in_required_jobs = True
            required_indent = indent
            continue

        if in_required_jobs and required_indent is not None and indent <= required_indent:
            in_required_jobs = False

        if in_required_jobs and stripped.startswith("- "):
            required_jobs.append(stripped[2:].strip())

    return required_jobs


def extract_required_check_names(payload: Mapping[str, object]) -> set[str]:
    required_status_checks = payload.get("required_status_checks")
    if not isinstance(required_status_checks, Mapping):
        return set()

    names: set[str] = set()

    contexts = required_status_checks.get("contexts")
    if isinstance(contexts, Sequence) and not isinstance(contexts, (str, bytes)):
        for context in contexts:
            if isinstance(context, str) and context.strip():
                names.add(context.strip())

    checks = required_status_checks.get("checks")
    if isinstance(checks, Sequence) and not isinstance(checks, (str, bytes)):
        for item in checks:
            if isinstance(item, str) and item.strip():
                names.add(item.strip())
                continue
            if isinstance(item, Mapping):
                context = item.get("context")
                if isinstance(context, str) and context.strip():
                    names.add(context.strip())

    return names


def validate_branch_protection(
    payload: Mapping[str, object],
    *,
    required_jobs: Iterable[str],
    logical_to_check: Mapping[str, Sequence[str]] = LOGICAL_TO_REPO_CHECK,
) -> ValidationResult:
    result = ValidationResult()
    configured_checks = extract_required_check_names(payload)
    if not configured_checks:
        result.errors.append("Branch protection payload does not include required status checks.")
        return result

    for logical_id in required_jobs:
        concrete_checks = logical_to_check.get(logical_id)
        if concrete_checks is None:
            result.warnings.append(
                f"No concrete check mapping is defined for logical gate ID '{logical_id}'."
            )
            continue
        missing = [check for check in concrete_checks if check not in configured_checks]
        if missing:
            missing_display = ", ".join(f"'{check}'" for check in missing)
            result.errors.append(
                f"Missing protected checks for logical gate ID '{logical_id}': expected {missing_display}."
            )

    return result


def build_weekly_audit_report(
    *,
    payload: Mapping[str, object],
    required_jobs: Iterable[str],
    result: ValidationResult,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    generated_at = generated_at or datetime.now(UTC)
    return {
        "kind": "BranchProtectionWeeklyAudit",
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "next_review_due": (generated_at + timedelta(days=7)).date().isoformat(),
        "required_jobs": sorted(required_jobs),
        "configured_checks": sorted(extract_required_check_names(payload)),
        "result": result.to_dict(),
    }


def _report_result(report: Mapping[str, object]) -> Mapping[str, object]:
    value = report.get("result")
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _string_list_field(payload: Mapping[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def build_weekly_nudge(report: Mapping[str, object]) -> dict[str, object]:
    result = _report_result(report)
    errors = _string_list_field(result, "errors")
    return {
        "nudge_id": f"NUDGE-branch-protection-{str(report.get('generated_at', 'unknown'))[:10]}",
        "reason": (
            f"Branch protection weekly audit has {len(errors)} error(s)"
            if errors else "Branch protection weekly audit passed; record review evidence"
        ),
        "target_kind": "branch_protection",
        "target_ref": "docs/security/Branch_Protection_Operation.md",
        "suggested_action": "Review branch protection export against governance/policy.yaml.",
        "created_at": report.get("generated_at"),
        "priority": "high" if errors else "low",
        "category": "weekly_audit",
        "blocking": bool(errors),
    }


def build_task_seed(report: Mapping[str, object]) -> str:
    generated = str(report.get("generated_at", datetime.now(UTC).isoformat()))[:10]
    result = _report_result(report)
    errors = _string_list_field(result, "errors")
    lines = [
        "---",
        f"task_id: task-branch-protection-weekly-audit-{generated.replace('-', '')}",
        "status: planned",
        "owner: security",
        f"last_reviewed_at: {generated}",
        f"next_review_due: {report.get('next_review_due', generated)}",
        "---",
        "",
        "# Task Seed: Branch Protection Weekly Audit",
        "",
        "## Objective",
        "",
        "Review branch protection settings against governance policy and docs.",
        "",
        "## Findings",
        "",
    ]
    if errors:
        lines.extend(f"- {error}" for error in errors)
    else:
        lines.append("- No blocking branch protection mismatches detected.")
    lines.extend([
        "",
        "## Commands",
        "",
        "```sh",
        "python tools/ci/check_branch_protection.py --protection-json branch-protection.json",
        "```",
        "",
    ])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a GitHub branch protection export against governance/policy.yaml."
    )
    parser.add_argument(
        "--protection-json",
        type=Path,
        required=True,
        help="Path to a JSON file exported from the GitHub branch protection API.",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=_DEFAULT_POLICY,
        help="Path to governance/policy.yaml.",
    )
    parser.add_argument("--report-output", type=Path, help="Write weekly audit JSON report.")
    parser.add_argument("--nudge-output", type=Path, help="Write PeriodicNudge-style JSON.")
    parser.add_argument("--task-seed-output", type=Path, help="Write remediation Task Seed draft.")
    parser.add_argument("--json", action="store_true", help="Print validation result as JSON.")
    args = parser.parse_args(argv)

    payload = json.loads(args.protection_json.read_text(encoding="utf-8"))
    required_jobs = load_policy_required_jobs(args.policy)
    result = validate_branch_protection(payload, required_jobs=required_jobs)
    weekly_report = build_weekly_audit_report(
        payload=payload,
        required_jobs=required_jobs,
        result=result,
    )
    if args.report_output:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(
            json.dumps(weekly_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.nudge_output:
        args.nudge_output.parent.mkdir(parents=True, exist_ok=True)
        args.nudge_output.write_text(
            json.dumps(build_weekly_nudge(weekly_report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.task_seed_output:
        args.task_seed_output.parent.mkdir(parents=True, exist_ok=True)
        args.task_seed_output.write_text(build_task_seed(weekly_report), encoding="utf-8")
    if args.json:
        print(json.dumps(weekly_report, ensure_ascii=False, indent=2))
    result.emit()
    if result.is_success:
        print(
            "Branch protection matches governance/policy.yaml logical gate IDs.",
            file=sys.stdout,
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
