# SPDX-License-Identifier: MIT
# Copyright 2025 RNA4219

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    remote: dict[str, str] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "pass" if self.is_success else "fail",
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
            "remote": self.remote,
        }

    def emit(self) -> None:
        for message in self.errors:
            print(message, file=sys.stderr)
        for message in self.warnings:
            print(message, file=sys.stderr)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fetch_repo_security(repo: str, token: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310  # GitHub API with fixed host, token auth
        payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("GitHub repository response must be an object")
        return cast(dict[str, object], payload)


def _vulnerability_alerts_enabled(repo: str, token: str) -> bool:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/vulnerability-alerts",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310  # GitHub API with fixed host, token auth
            return bool(response.status == 204)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def _security_status(payload: dict[str, object], key: str) -> str | None:
    security = payload.get("security_and_analysis")
    if not isinstance(security, dict):
        return None
    feature = security.get(key)
    if not isinstance(feature, dict):
        return None
    status = feature.get("status")
    return str(status) if status is not None else None


def validate_security_posture(
    *,
    repo_root: Path,
    github_repo: str | None = None,
    github_token: str | None = None,
) -> ValidationResult:
    result = ValidationResult()

    dependabot_path = repo_root / ".github" / "dependabot.yml"
    security_workflow_path = repo_root / ".github" / "workflows" / "security.yml"
    sac_path = repo_root / "docs" / "security" / "SAC.md"
    checklist_path = repo_root / "docs" / "security" / "Security_Review_Checklist.md"
    requirements_path = repo_root / "docs" / "requirements.md"
    spec_path = repo_root / "docs" / "spec.md"
    readme_path = repo_root / "README.md"

    required_paths = (
        dependabot_path,
        security_workflow_path,
        sac_path,
        checklist_path,
        requirements_path,
        spec_path,
        readme_path,
    )
    for path in required_paths:
        if not path.exists():
            result.errors.append(f"Required security asset is missing: {path}")
            result.checks[str(path.relative_to(repo_root))] = "missing"
        else:
            result.checks[str(path.relative_to(repo_root))] = "present"

    if result.errors:
        return result

    dependabot_text = _read_text(dependabot_path)
    if "package-ecosystem: github-actions" not in dependabot_text:
        result.errors.append(".github/dependabot.yml does not configure github-actions updates.")
        result.checks["dependabot.github_actions"] = "fail"
    else:
        result.checks["dependabot.github_actions"] = "pass"
    if "interval: weekly" not in dependabot_text:
        result.errors.append(".github/dependabot.yml does not enforce a weekly update schedule.")
        result.checks["dependabot.weekly_schedule"] = "fail"
    else:
        result.checks["dependabot.weekly_schedule"] = "pass"

    sac_text = _read_text(sac_path)
    for marker in ("SAST", "Secrets", "依存", "Container"):
        if marker not in sac_text and marker.lower() not in sac_text.lower():
            result.errors.append(f"docs/security/SAC.md does not mention the '{marker}' gate.")
            result.checks[f"sac.{marker}"] = "fail"
        else:
            result.checks[f"sac.{marker}"] = "pass"

    checklist_text = _read_text(checklist_path)
    if "Dependabot" not in checklist_text and "dependabot" not in checklist_text:
        result.errors.append(
            "docs/security/Security_Review_Checklist.md does not mention Dependabot or dependency alert operations."
        )
        result.checks["checklist.dependabot"] = "fail"
    else:
        result.checks["checklist.dependabot"] = "pass"

    requirements_text = _read_text(requirements_path)
    spec_text = _read_text(spec_path)
    readme_text = _read_text(readme_path)
    if "Security posture" not in requirements_text and "セキュリティ" not in requirements_text:
        result.errors.append("docs/requirements.md does not contain an explicit security posture section.")
        result.checks["requirements.security_posture"] = "fail"
    else:
        result.checks["requirements.security_posture"] = "pass"
    if "Security baseline" not in spec_text and "セキュリティ" not in spec_text:
        result.errors.append("docs/spec.md does not contain an explicit security baseline section.")
        result.checks["spec.security_baseline"] = "fail"
    else:
        result.checks["spec.security_baseline"] = "pass"
    if "Security" not in readme_text:
        result.errors.append("README.md does not advertise the security baseline entry point.")
        result.checks["readme.security"] = "fail"
    else:
        result.checks["readme.security"] = "pass"

    if github_repo:
        if not github_token:
            result.warnings.append("GitHub security settings were not checked because no token was provided.")
            result.remote["github"] = "not_checked"
            return result
        try:
            repo_payload = _fetch_repo_security(github_repo, github_token)
            dependabot_updates = _security_status(repo_payload, "dependabot_security_updates")
            secret_scanning = _security_status(repo_payload, "secret_scanning")
            push_protection = _security_status(repo_payload, "secret_scanning_push_protection")
            result.remote["dependabot_security_updates"] = str(dependabot_updates)
            result.remote["secret_scanning"] = str(secret_scanning)
            result.remote["secret_scanning_push_protection"] = str(push_protection)
            if dependabot_updates != "enabled":
                result.errors.append("GitHub Dependabot security updates are not enabled.")
            if secret_scanning != "enabled":
                result.errors.append("GitHub secret scanning is not enabled.")
            if push_protection != "enabled":
                result.errors.append("GitHub secret scanning push protection is not enabled.")
            vulnerability_alerts = _vulnerability_alerts_enabled(github_repo, github_token)
            result.remote["vulnerability_alerts"] = "enabled" if vulnerability_alerts else "disabled"
            if not vulnerability_alerts:
                result.errors.append("GitHub vulnerability alerts are not enabled.")
        except urllib.error.HTTPError as exc:
            result.errors.append(f"Failed to fetch GitHub security posture: HTTP {exc.code}")
            result.remote["github"] = f"http_{exc.code}"

    return result


def _mapping_field(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def diff_security_posture(
    current: dict[str, object],
    baseline: dict[str, object],
) -> dict[str, object]:
    """Compare two exported security posture snapshots."""
    current_checks = _mapping_field(current, "checks")
    baseline_checks = _mapping_field(baseline, "checks")
    current_remote = _mapping_field(current, "remote")
    baseline_remote = _mapping_field(baseline, "remote")

    changes: list[dict[str, str]] = []
    for section, now_values, then_values in (
        ("checks", current_checks, baseline_checks),
        ("remote", current_remote, baseline_remote),
    ):
        keys = sorted(set(now_values) | set(then_values))
        for key in keys:
            before = str(then_values.get(key, "<missing>"))
            after = str(now_values.get(key, "<missing>"))
            if before != after:
                changes.append({
                    "section": section,
                    "key": str(key),
                    "before": before,
                    "after": after,
                })

    regressions = [
        change for change in changes
        if change["before"] in {"pass", "enabled", "present"}
        and change["after"] not in {"pass", "enabled", "present"}
    ]
    improvements = [
        change for change in changes
        if change["before"] not in {"pass", "enabled", "present"}
        and change["after"] in {"pass", "enabled", "present"}
    ]
    return {
        "changes": changes,
        "regressions": regressions,
        "improvements": improvements,
        "status": "regressed" if regressions else "changed" if changes else "unchanged",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate security posture docs, workflows, and optional GitHub settings.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when validation fails.")
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT, help="Repository root.")
    parser.add_argument("--github-repo", help="Repository in owner/name form for remote posture checks.")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable that contains a GitHub token.")
    parser.add_argument("--export-json", type=Path, help="Write current posture snapshot as JSON.")
    parser.add_argument("--baseline-json", type=Path, help="Compare current posture against a previous snapshot.")
    parser.add_argument("--json", action="store_true", help="Print current posture snapshot as JSON.")
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env) if args.github_repo else None
    result = validate_security_posture(
        repo_root=args.repo_root,
        github_repo=args.github_repo,
        github_token=token,
    )
    payload = result.to_dict()
    if args.baseline_json:
        baseline = json.loads(args.baseline_json.read_text(encoding="utf-8"))
        payload["diff"] = diff_security_posture(payload, baseline)
    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        args.export_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("diff"):
        diff = payload["diff"]
        if isinstance(diff, dict):
            print(
                f"Security posture diff: {diff.get('status')} "
                f"({len(diff.get('changes', []))} change(s), "
                f"{len(diff.get('regressions', []))} regression(s))"
            )
    result.emit()
    if result.is_success:
        print("Security posture matches docs, workflows, and GitHub settings.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
