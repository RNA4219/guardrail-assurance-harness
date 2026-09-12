"""標準ライブラリだけで動く文書生成とCookbook Fullのローカル検証。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tools.ci import check_acceptance, check_adoption_tier, check_birdseye_freshness
from tools.ci import check_branch_protection, check_ci_gate_matrix
from tools.ci import check_downstream_onboarding, check_security_posture
from tools.ci import check_task_acceptance_bidirectional as sync

ROOT = Path(__file__).resolve().parents[1]
INDEX = "docs/birdseye/index.json"
HOT = "docs/birdseye/hot.json"
ACC_INDEX = "docs/acceptance/INDEX.md"
FM = ("---\nintent_id: INT-SR-001\nowner: RNA4219\nstatus: active\n"
      "last_reviewed_at: 2026-09-09\nnext_review_due: 2026-10-09\n---\n\n")
DOC_DIRS = ("docs", "orchestration", "src", "schemas", "fixtures", "datasets",
            "examples", "tools", "tests")
GATE_MAPPING = {"governance-gate": ["governance"], "python-ci": ["unit"],
                "docs-gate": ["docs-gate"]}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sha(path: Path) -> str:
    """テキストはLF正規化し、OS間のcheckout差を吸収する。"""
    return hashlib.sha256(read(path).encode("utf-8")).hexdigest()


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"出力先がrepo外: {rel}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or read(path) != content:
        path.write_text(content, encoding="utf-8", newline="\n")


def metadata(path: Path) -> dict:
    return sync._parse_front_matter(read(path))


def source_paths(root: Path) -> list[Path]:
    paths = list(root.glob("*.md"))
    for directory in DOC_DIRS:
        paths.extend((root / directory).rglob("*.md"))
    return sorted(p for p in paths if "archive" not in p.relative_to(root).parts
                  and "TEMPLATE" not in p.name)


def prose(content: str) -> str:
    content = re.sub(r"\A---\n.*?\n---\n", "", content, flags=re.S)
    return re.sub(r"^(`{3,}|~{3,}).*?^\1\s*$", "", content, flags=re.M | re.S)


def local_links(root: Path, path: Path) -> tuple[list[str], list[str]]:
    targets, errors = [], []
    for match in re.finditer(r"\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", prose(read(path))):
        url = match.group(1).strip("<>")
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = (path.parent / unquote(parsed.path)).resolve()
        name = path.relative_to(root).as_posix()
        if not target.is_relative_to(root.resolve()):
            errors.append(f"{name}: repo外の文書参照: {url}")
        else:
            targets.append(target.relative_to(root.resolve()).as_posix())
            if not target.exists():
                errors.append(f"{name}: 参照先がない: {url}")
    return sorted(set(targets)), errors


def acceptance_index(root: Path) -> str:
    lines = [FM + "# Acceptance Index\n", "自動生成。各記録のfrontmatterを正本とする。\n",
             "| Acceptance | Task | Status | Reviewed | Reviewer |",
             "|---|---|---|---|---|"]
    for path in sorted((root / "docs/acceptance").glob("AC-*.md")):
        m = metadata(path)
        cell = lambda k: str(m.get(k, "")).replace("|", "\\|")
        lines.append(f"| [{cell('acceptance_id')}]({path.name}) | {cell('task_id')} | "
                     f"{cell('status')} | {cell('reviewed_at')} | {cell('reviewed_by')} |")
    lines.append("\n[運用ガイド](README.md)\n")
    return "\n".join(lines)


def birdseye(root: Path, serial: str, verified: str) -> dict[str, str]:
    paths = source_paths(root)
    names = {p.relative_to(root).as_posix() for p in paths}
    edges = sorted({(p.relative_to(root).as_posix(), target) for p in paths
                    for target in local_links(root, p)[0] if target in names})
    nodes, capsules = {}, {}
    for path in paths:
        name = path.relative_to(root).as_posix()
        body = prose(read(path))
        heading = re.search(r"^# (.+)$", body, re.M)
        title = heading.group(1).strip() if heading else path.stem
        paragraphs = [s.strip() for s in body.split("\n\n")
                      if s.strip() and not s.lstrip().startswith(("#", "<!--"))]
        summary = re.sub(r"\s+", " ", paragraphs[0])[:420] if paragraphs else title
        role = name.split("/")[1] if name.startswith("docs/") else name.split("/")[0]
        role = role.removesuffix(".md")
        cap_path = "docs/birdseye/caps/" + name.replace("/", ".") + ".json"
        if cap_path in capsules:
            raise ValueError(f"caps path衝突: {name}")
        outgoing = [b for a, b in edges if a == name]
        incoming = [a for a, b in edges if b == name]
        nodes[name] = {"role": role, "caps": cap_path, "mtime": serial, "source_sha256": sha(path)}
        capsules[cap_path] = dump({"id": name, "role": role, "title": title,
                                  "summary": summary, "public_api": [],
                                  "deps_out": outgoing, "deps_in": incoming,
                                  "risks": ["文書の要約であり、製品の実装・受入を保証しない"],
                                  "tests": ["python -m tools.workflow check"],
                                  "generated_at": serial, "source_sha256": sha(path)})
    entrypoints = ("README.md", "HUB.codex.md", "BLUEPRINT.md", "GUARDRAILS.md", "RUNBOOK.md",
                   "EVALUATION.md", "docs/requirements.md", "docs/tasks/README.md", ACC_INDEX)
    hot_nodes = [{"id": n, "role": nodes[n]["role"], "reason": "標準の作業入口",
                  "caps": nodes[n]["caps"], "edges": [b for a, b in edges if a == n],
                  "last_verified_at": verified, "index_snapshot": INDEX,
                  "refresh_command": "python -m tools.workflow generate"}
                 for n in entrypoints if n in nodes]
    return {INDEX: dump({"generated_at": serial, "nodes": nodes, "edges": edges}),
            HOT: dump({"generated_at": serial, "nodes": hot_nodes}), **capsules}


def generation_state(root: Path) -> tuple[str, str]:
    index, hot = json.loads(read(root / INDEX)), json.loads(read(root / HOT))
    serial = index["generated_at"]
    if not isinstance(serial, str) or not re.fullmatch(r"\d{5}", serial):
        raise ValueError("世代番号が不正")
    verified = hot["nodes"][0]["last_verified_at"]
    datetime.fromisoformat(verified.replace("Z", "+00:00"))
    return serial, verified


def artifact_errors(root: Path) -> list[str]:
    serial, verified = generation_state(root)
    expected = birdseye(root, serial, verified)
    errors = [f"生成物が古い/不整合: {p}" for p, content in expected.items()
              if not (root / p).is_file() or read(root / p) != content]
    actual = {p.relative_to(root).as_posix() for p in (root / "docs/birdseye/caps").glob("*.json")}
    errors.extend(f"余剰caps: {p}" for p in sorted(actual - expected.keys()))
    if read(root / ACC_INDEX) != acceptance_index(root):
        errors.append("Acceptance索引が古い")
    return errors


def generate(root: Path) -> dict:
    write(root, ACC_INDEX, acceptance_index(root))
    serial = "00000"
    try:
        serial, verified = generation_state(root)
        timestamp = datetime.fromisoformat(verified.replace("Z", "+00:00"))
        if not artifact_errors(root) and datetime.now(UTC) - timestamp < timedelta(days=90):
            return {"changed": False, "generation": serial}
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    number = int(serial) + 1
    if number > 99999:
        raise ValueError("Birdseye世代が上限。世代更新方針をADRで決定する")
    serial = f"{number:05d}"
    expected = birdseye(root, serial, datetime.now(UTC).isoformat(timespec="seconds"))
    for path, content in expected.items():
        write(root, path, content)
    caps_root = (root / "docs/birdseye/caps").resolve()
    if not caps_root.is_relative_to(root.resolve()):
        raise ValueError("caps出力先がrepo外")
    for path in caps_root.glob("*.json"):
        rel = path.relative_to(root.resolve()).as_posix()
        if rel not in expected and path.resolve().parent == caps_root:
            path.unlink()
    return {"changed": True, "generation": serial, "nodes": len(expected) - 2}


def document_errors(root: Path) -> list[str]:
    errors = []
    for path in source_paths(root):
        name = path.relative_to(root).as_posix()
        errors.extend(local_links(root, path)[1])
        if name == "TASK.codex.md":
            continue
        m = metadata(path)
        for key in ("intent_id", "owner", "status", "last_reviewed_at", "next_review_due"):
            if not m.get(key):
                errors.append(f"{name}: metadata欠落: {key}")
        try:
            if date.fromisoformat(m["next_review_due"]) < date.fromisoformat(m["last_reviewed_at"]):
                errors.append(f"{name}: review日付の逆転")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name}: review日付が不正")
    for template in sorted((root / "templates").glob("*.md.template")):
        target = root / template.name.removesuffix(".template")
        headings = re.findall(r"^## .+$", read(template), re.M)
        body = read(target)
        for heading in headings:
            if heading not in body.splitlines():
                errors.append(f"{target.name}: 必須見出し欠落: {heading}")
    return errors


def task_errors(root: Path) -> list[str]:
    tasks = sync._scan_tasks(root / "docs/tasks")
    acceptances = sync._scan_acceptances(root / "docs/acceptance")
    report = sync.validate_bidirectional_sync(tasks, acceptances)
    errors = list(report.errors) + list(report.warnings)
    if not tasks or not acceptances:
        errors.append("TaskとAcceptanceの実記録が必要")
    for records, key in ((tasks, "task_id"), (acceptances, "acceptance_id")):
        ids = [getattr(r, key) for r in records]
        if len(ids) != len(set(ids)):
            errors.append(f"{key}が重複")
    task_by_id = {t.task_id: t for t in tasks}
    for acc in acceptances:
        task = task_by_id.get(acc.task_id)
        if not task:
            errors.append(f"{acc.acceptance_id}: Taskが存在しない")
        elif metadata(task.file_path).get("intent_id") != metadata(acc.file_path).get("intent_id"):
            errors.append(f"{acc.acceptance_id}: Taskとintent_idが不一致")
    return errors


def provenance_errors(root: Path) -> list[str]:
    errors = []
    lock = json.loads(read(root / "governance/upstream-lock.json"))
    for entry in lock["files"]:
        path = root / entry["path"]
        if not path.is_file() or sha(path) != entry["sha256_lf"]:
            errors.append(f"upstream hash不一致: {entry['path']}")
    manifest = json.loads(read(root / "docs/research/source-manifest.json"))
    archive = root / manifest["archive"]["path"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["archive"]["sha256_raw"]:
        errors.append("改訂前原稿のraw hash不一致")
    return errors


def branch_errors(root: Path) -> list[str]:
    payload = json.loads(read(root / "governance/branch-protection.expected.json"))
    required = check_branch_protection.load_policy_required_jobs(root / "governance/policy.yaml")
    result = check_branch_protection.validate_branch_protection(
        payload, required_jobs=required, logical_to_check=GATE_MAPPING)
    errors = result.errors + result.warnings
    if not required:
        errors.append("required_jobsが空")
    if payload.get("remote_verified") is not False or payload.get("evidence_kind") != "desired_configuration":
        errors.append("desired設定の証跡種別が不正")
    return errors


def policy_errors(root: Path) -> list[str]:
    text = read(root / "governance/policy.yaml")
    match = re.search(r"^  checker_stages:\n((?:    .+\n)+)", text, re.M)
    if not match:
        return ["checker_stagesが空または対応しない形式"]
    stages = dict(line.strip().split(": ", 1) for line in match.group(1).splitlines())
    expected = {"adoption", "task_acceptance", "birdseye", "docs", "security_posture"}
    errors = []
    if stages.keys() != expected or any(v != "enforce" for v in stages.values()):
        errors.append("checker_stagesはこのrepoの5検査すべてenforceが必要")
    return errors


def check(root: Path) -> dict:
    results = {}

    def run(name, action):
        try:
            errors = action()
            results[name] = {"status": "fail" if errors else "pass", "errors": [str(e) for e in errors]}
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            results[name] = {"status": "fail", "errors": [f"{type(exc).__name__}: {exc}"]}

    def adoption():
        result = check_adoption_tier.assess_repo(root, check_drift=True, template_root=root / "templates")
        return [] if result["current_tier"] == 3 and not result["drifted"] else ["Tier3/template drift不成立"]

    def acceptance():
        invalid = check_acceptance.validate_acceptance_docs(root)
        return [f"{p.relative_to(root)}: {issues}" for p, issues in invalid.items()]

    def freshness():
        return check_birdseye_freshness.evaluate_birdseye_freshness(
            index_doc=json.loads(read(root / INDEX)), hot_doc=json.loads(read(root / HOT)),
            repo_root=root, now=datetime.now(UTC), max_verified_age_days=90).failures

    run("adoption_tier_3", adoption)
    run("downstream_onboarding", lambda: [] if check_downstream_onboarding.assess_downstream_repo(
        root, min_tier=3)["status"] == "ready" else ["onboarding未成立"])
    run("documents", lambda: document_errors(root))
    run("acceptance_format", acceptance)
    run("task_acceptance", lambda: task_errors(root))
    run("generated_artifacts", lambda: artifact_errors(root))
    run("birdseye_freshness", freshness)
    run("ci_gate_matrix", lambda: check_ci_gate_matrix.validate_ci_gate_matrix(
        repo_root=root, policy_path=root / "governance/policy.yaml",
        ci_config_path=root / "docs/ci-config.md").errors)
    run("checker_stages", lambda: policy_errors(root))
    run("branch_desired_configuration", lambda: branch_errors(root))
    run("local_security_posture", lambda: check_security_posture.validate_security_posture(repo_root=root).errors)
    run("source_provenance", lambda: provenance_errors(root))
    return {"schema_version": 1, "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "scope": "local workflow adoption", "python": sys.version.split()[0],
            "status": "pass" if all(r["status"] == "pass" for r in results.values()) else "fail",
            "checks": results, "remote_validation": "not_run", "product_acceptance": "not_run"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check", "check-branch-protection"))
    parser.add_argument("--report", help="check結果のrepo相対出力先")
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            print(dump(generate(ROOT)), end="")
            return 0
        if args.command == "check-branch-protection":
            errors = branch_errors(ROOT)
            print(dump({"scope": "desired_configuration_only", "errors": errors}), end="")
            return int(bool(errors))
        report = check(ROOT)
        if args.report:
            write(ROOT, args.report, dump(report))
        print(dump(report), end="")
        return int(report["status"] != "pass")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"検証/生成に失敗: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
