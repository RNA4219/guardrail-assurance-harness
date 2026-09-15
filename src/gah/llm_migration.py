"""既知の初回合成LLM版の履歴を再照合する。現在の利用許可は発行しない。"""
from .adoption import AdoptionError
from .contracts import ContractError
from . import llm_admission, resources, assurance_authority, run_evidence, baseline_authority
from .run_contracts import content_ref


class _RecordedPermission:
    def __init__(self, generation): self.generation=generation
    def _permission_generation(self, db): return self.generation


def verify(db, now):
    from . import evaluation_authority as authority
    try:
        for table in ('transition_candidates','transition_runs'):
            if db.execute('SELECT 1 FROM '+table).fetchone(): raise AdoptionError('UNSUPPORTED_LLM_STORE')
        allowed={'run_manifest','bound_bundle','run_decision','evidence','resource_closure'}
        if any(row[0] not in allowed for row in db.execute('SELECT DISTINCT kind FROM authority_artifacts')):
            raise AdoptionError('UNSUPPORTED_LLM_STORE')
        prepared={}
        for row in db.execute('SELECT * FROM fixture_admissions'):
            value=llm_admission.verify(row,now);bound=value['prepared']['bound_run']
            if bound['manifest']['use_cases']!=['UC-LLM'] or bound['manifest']['purpose']!='baseline_candidate' or bound['contract']['generation']!=1:
                raise AdoptionError('UNSUPPORTED_LLM_STORE')
            prepared[row['run_id']]=value['prepared']
        for row in db.execute('SELECT * FROM eval_adoptions'):
            contract=authority._load_json(row,'payload_json','digest')
            if contract['generation']!=1 or contract['use_cases']!=['UC-LLM']:raise AdoptionError('UNSUPPORTED_LLM_STORE')
            validation=authority._assert_contract_history(db,row,contract)
            matches=[p for p in prepared.values() if p['bound_run']['contract']==contract]
            if len(matches)!=1:raise AdoptionError('STORAGE_CORRUPT')
            policy=matches[0]['bound_run']['policy']
            expected=authority._validate_state(_RecordedPermission(validation['permission_generation']),db,contract,validation['created_at'],
                policy_state=(policy,contract['policy_ref'],contract['policy_generation']))
            expected.update(proposal_id=row['proposal_id'],proposal_digest=validation['proposal_digest'])
            if authority._load_json(validation,'payload_json','digest')!=expected:raise AdoptionError('STORAGE_CORRUPT')
        sources={}
        for row in db.execute('SELECT * FROM eval_runs'):
            run_id=row['run_id']
            if run_id not in prepared:raise AdoptionError('STORAGE_CORRUPT')
            bound=prepared[run_id]['bound_run']
            if (row['contract_generation']!=1 or resources._unpack(row['manifest_json'],row['manifest_digest'])!=bound['manifest']
                    or resources._unpack(row['plan_json'],row['plan_digest'])!=bound['plan']):raise AdoptionError('STORAGE_CORRUPT')
            history=db.execute('SELECT * FROM eval_adoptions WHERE series_id=? AND generation=1',(row['contract_series_id'],)).fetchone()
            if history is None or history['digest']!=bound['manifest']['contract_ref']['digest']:raise AdoptionError('STORAGE_CORRUPT')
            assurance_authority._resource_binding(db,bound)
            snapshot=resources.ResourceBook(db).snapshot(run_id,now)
            if snapshot['resources']['slots'] or snapshot['resources']['unsettled']:raise AdoptionError('ACTIVE_OPERATIONS')
            exists=db.execute('SELECT 1 FROM bound_runs WHERE run_id=?',(run_id,)).fetchone()
            book=None
            if exists:
                digest=run_evidence.bound_bundle_digest(bound)
                book=run_evidence.RunEvidenceBook(db,now=now,allowed_bindings={run_id:digest})
                view=book.get_run(run_id)
                if view['bundle']!=bound or view['execution_profile']!=prepared[run_id]['execution_profile'] or view['baseline_context'] is not None:
                    raise AdoptionError('STORAGE_CORRUPT')
            receipt_row=db.execute('SELECT * FROM authority_run_receipts WHERE run_id=?',(run_id,)).fetchone()
            if receipt_row:
                if book is None:raise AdoptionError('STORAGE_CORRUPT')
                _,receipt=assurance_authority._receipt(db,run_id,bound,book)
                sources[run_id]={'bound':bound,'receipt':receipt,
                    'decision':assurance_authority._artifact(db,receipt['decision_ref'],run_id),
                    'closure':assurance_authority._artifact(db,receipt['closure_ref'],run_id),
                    'evidences':[assurance_authority._artifact(db,receipt['evidence_ref'],run_id)]}
        for row in db.execute('SELECT * FROM baseline_proposals'):
            proposal=baseline_authority._validate_proposal_row(row)
            if row['expected_generation']!=0 or row['contract_generation']!=1 or row['run_id'] not in sources:
                raise AdoptionError('UNSUPPORTED_LLM_STORE')
            source=sources[row['run_id']]
            revoked=db.execute('SELECT revoked_at FROM authority_run_receipts WHERE run_id=?',(row['run_id'],)).fetchone()[0]
            evidence=source['evidences'][0]
            source={**source,'evidence_states':{evidence['evidence_id']:{'revoked':revoked is not None and revoked<=row['created_at'],'deleted':False}}}
            if source['receipt']['created_at']>row['created_at']:raise AdoptionError('STORAGE_CORRUPT')
            baseline_authority._bind_candidate(proposal,source,row['created_at'],db=db)
        from .baseline_refresh_migration import verify as verify_baseline
        verify_baseline(db,now)
    except (AdoptionError,ContractError,run_evidence.EvidenceError,resources.ResourceError,KeyError,TypeError,ValueError):
        raise AdoptionError('STORAGE_CORRUPT') from None
