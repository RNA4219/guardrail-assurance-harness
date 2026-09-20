"""固定sample専用の保守的な空き容量予算。一般runの残予約を推測しない。"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.productization import content_ref, workspace_path, write_document


def capacity_profile(profile, *, workspace=None):
    """Return a fixed-sample budget; it never claims a verified bound.

    ``bound_verified=True`` is reserved for trusted internal observation
    adapters or test injection.  The CLI and pilot have no free-form JSON
    entry point that can create that claim.
    """
    if type(profile) is not str or profile not in {"sample-ci", "sample-llm"}:
        raise ContractError("INVALID_INPUT")
    # 初回baseline、candidate旧/新、通常1回。LLMは1/2段階の全件を数える。
    stages = [15, 15, 30, 30] if profile == "sample-ci" else [600, 600, 1200, 1200]
    # 固定runnerのrequest/response/checkpoint/receipt/authority行を最大文書長で
    # 丸め、SQLiteの一時書込を二重計上する。source変更時はsetup planを失効する。
    document = {"schema_version": 1, "kind": "resource_profile",
                "id": "fixed-" + profile + "-capacity-v1", "profile": profile,
                "scope": "new_fixed_sample_setup_and_first_run",
                "stage_counts": stages, "documents_per_stage_budget": 32,
                "document_max_bytes": MAX_DOCUMENT_BYTES, "sqlite_copy_factor": 2,
                "fixed_management_bytes": 1024 ** 3, "reserved_stage_budget": 4,
                "image_download_included": False, "measured_slo_evidence": False,
                "bound_verified": False}
    unit = document["documents_per_stage_budget"] * MAX_DOCUMENT_BYTES * document["sqlite_copy_factor"]
    ref = content_ref("resource_profile", document["id"], document)
    if workspace is not None:
        folder = workspace_path(workspace, ".ga/operations/capacity")
        folder.mkdir(parents=True, exist_ok=True)
        write_document(workspace, folder / (document["id"] + ".json"), document)
    return {"profile_ref": ref,
            "remaining_write_upper_bound_bytes": sum(stages) * unit + document["fixed_management_bytes"],
            "reserved_bytes": document["reserved_stage_budget"] * unit,
            "bound_verified": False}
