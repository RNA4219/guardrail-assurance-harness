"""保持の計画・適用を既存authorityへ委譲し、補助操作の再配送を記録する。"""
from __future__ import annotations
from copy import deepcopy
import hashlib
from pathlib import Path
import time
from .adoption import AdoptionError
from .contracts import ContractError, require_id, require_ref
from . import evidence_retention
from .productization import content_ref, operation_result, workspace_path, write_document
from .productization_journal import OperationJournal
from .wire import canonical_bytes

COMMANDS={"ops.retention.plan":("evidence_retention_plan",12001),
          "ops.retention.apply":("evidence_retention_apply",12004)}
REJECTED={"AUTHORITY_DENIED":"AUTHORITY_DENIED","AUTHORITY_REVOKED":"AUTHORITY_REVOKED",
    "INVALID_REQUEST":"INVALID_INPUT","RETENTION_HOLD":"RETENTION_HOLD",
    "RETENTION_STATE_CONFLICT":"RETENTION_STATE_CONFLICT","RETENTION_PLAN_INVALID":"RETENTION_PLAN_INVALID",
    "RETENTION_NOT_EXPIRED":"RETENTION_NOT_EXPIRED","EVIDENCE_DELETED":"EVIDENCE_UNAVAILABLE",
    "EVIDENCE_BINDING_MISMATCH":"BINDING_MISMATCH","REQUEST_CONFLICT":"IDEMPOTENCY_CONFLICT"}


def _response(request,value):
    base={"schema_version","kind","action","request_id","ci_eligible"}
    extra={"plan_ref","plan","deleted"} if request["action"]=="evidence_retention_plan" else {
        "run_id","evidence_ref","tombstone_ref","deleted","reproduction"}
    if (type(value) is not dict or set(value)!=base|extra
            or type(value["schema_version"]) is not int or value["schema_version"]!=1
            or value["kind"]!="evaluation_authority_result" or value["action"]!=request["action"]
            or value["request_id"]!=request["request_id"] or value["ci_eligible"] is not False):
        raise ContractError("BINDING_MISMATCH")
    if request["action"]=="evidence_retention_plan":
        require_ref(value["plan_ref"])
        plan=value["plan"]
        if (type(plan) is not dict or type(plan.get("schema_version")) is not int or plan["schema_version"]!=1
                or plan.get("kind")!=evidence_retention.PLAN_KIND or value["deleted"] is not False
                or plan.get("run_id")!=request["run_id"] or plan.get("request")!=request
                or plan.get("evidence_ref")!=request["expected_evidence_ref"]
                or plan.get("hold_ref")!=request["expected_hold_ref"]
                or value["plan_ref"]!=content_ref(evidence_retention.PLAN_KIND,plan.get("plan_id"),plan)):
            raise ContractError("BINDING_MISMATCH")
    else:
        require_ref(value["tombstone_ref"])
        if (value["run_id"]!=request["run_id"] or value["evidence_ref"]!=request["expected_evidence_ref"]
                or value["tombstone_ref"]["kind"]!=evidence_retention.TOMBSTONE_KIND
                or value["deleted"] is not True or value["reproduction"]!="REPRODUCTION_UNAVAILABLE"):
            raise ContractError("BINDING_MISMATCH")
    return value


def execute(workspace, runtime, request, command, *, authenticated_principal, clock=None):
    """principalはOS認証済み入口から渡す。要求本文のroleは受け付けない。"""
    now = clock or (lambda:int(time.time()))
    rid=request.get("request_id") if type(request) is dict else None
    if command not in COMMANDS:
        return operation_result("ops.invalid",None,"REJECTED",reasons=["INVALID_INPUT"])
    try:
        require_id(rid);require_id(authenticated_principal)
        normalized=evidence_retention.validate_request(request)
        action,uid=COMMANDS[command]
        if normalized["action"]!=action:
            raise ContractError("INVALID_INPUT")
        root=workspace_path(workspace,".ga/operations/retention")
        root.mkdir(parents=True,exist_ok=True)
        root=workspace_path(workspace,root)
        digest=hashlib.sha256(canonical_bytes(normalized)).hexdigest()
        private_id="opsret-"+hashlib.sha256(canonical_bytes([authenticated_principal,command,rid])).hexdigest()
        broker_request={**deepcopy(normalized),"request_id":private_id}
    except (ContractError,AdoptionError,TypeError,ValueError) as error:
        try:require_id(rid)
        except ContractError:rid=None
        reason="PATH_REJECTED" if getattr(error,"code",None)=="PATH_REJECTED" else "INVALID_INPUT"
        return operation_result(command,rid,"REJECTED",reasons=[reason])
    except OSError:
        return operation_result(command,rid,"INCOMPLETE",reasons=["IO_ERROR"])
    try:
        journal_path=workspace_path(workspace,root/"journal.sqlite")
        with OperationJournal(journal_path,clock=now) as journal:
            intent=journal.begin(authenticated_principal,command,rid,digest)
            if intent["result"] is not None:
                return intent["result"]
            try:
                value=runtime.client(uid,broker_request)
            except AdoptionError as error:
                value={"schema_version":1,"kind":"authority_error","reason":error.code,"ci_eligible":False}
            if type(value) is dict and value.get("kind")=="authority_error":
                if (set(value)!={"schema_version","kind","reason","ci_eligible"}
                        or type(value["schema_version"]) is not int or value["schema_version"]!=1
                        or value["ci_eligible"] is not False):
                    raise ContractError("BINDING_MISMATCH")
                reason=REJECTED.get(value["reason"])
                # 前回commitが不明なら拒否応答だけで未実行と決めない。元の意図を保持する。
                if reason is None or not intent["created"]:
                    return operation_result(command,rid,"INCOMPLETE",reasons=["OPERATION_UNKNOWN"],checked_at=now())
                result=operation_result(command,rid,"REJECTED",reasons=[reason],checked_at=now())
                return journal.finish(authenticated_principal,command,rid,digest,result)
            value=_response(broker_request,value)
            artifact={"schema_version":1,"kind":"retention_operation_result","id":private_id,
                      "command":command,"request_id":rid,"request_digest":digest,
                      "authority_result":value,"ci_eligible":False}
            ref=write_document(workspace,root/(private_id+".json"),artifact)
            result=operation_result(command,rid,"COMPLETED",result_ref=ref,checked_at=now())
            return journal.finish(authenticated_principal,command,rid,digest,result)
    except ContractError as error:
        if error.code=="IDEMPOTENCY_CONFLICT":
            return operation_result(command,rid,"REJECTED",reasons=[error.code])
        reason=error.code if error.code in {"CLOCK_ROLLBACK","CLOCK_UNAVAILABLE","BINDING_MISMATCH","PATH_REJECTED"} else "IO_ERROR"
        return operation_result(command,rid,"INCOMPLETE",reasons=[reason])
    except Exception:
        # 外部commitや保存結果を断定できないので、journalの意図と既存artifactを保持。
        return operation_result(command,rid,"INCOMPLETE",reasons=["OPERATION_UNKNOWN"])
