"""既知のbaseline更新版だけに適用する保存履歴の移行前検査。"""
from . import baseline_authority as base, baseline_generations, regression_runs, assurance_authority, run_evidence
from .adoption import AdoptionError
from .contracts import ContractError


def verify(db, now, *, following=False):
    """現在の失効を解除せず、保存時の候補と元runの実体を照合する。"""
    try:
        for row in db.execute('SELECT * FROM baseline_proposals'):
            value = base._validate_proposal_row(row)
            if row['created_at'] > now:
                raise AdoptionError('STORAGE_CORRUPT')
            if value['kind'] != baseline_generations.KIND:
                continue
            if not following and (value['expected_generation'] != 1 or value['contract_generation'] != 2):
                raise AdoptionError('STORAGE_CORRUPT')
            baseline_generations.predecessor(db, value)
            run = db.execute('SELECT * FROM eval_runs WHERE run_id=?', (row['run_id'],)).fetchone()
            prepared = regression_runs.for_run(db, run, now)
            bound = prepared['bound_run']
            digest = run_evidence.bound_bundle_digest(bound, prepared['baseline_context'])
            book = run_evidence.RunEvidenceBook(db, now=now, allowed_bindings={row['run_id']: digest})
            _, receipt = assurance_authority._receipt(db, row['run_id'], bound, book)
            source = {'bound': bound, 'receipt': receipt,
                'decision': assurance_authority._artifact(db, receipt['decision_ref'], row['run_id']),
                'closure': assurance_authority._artifact(db, receipt['closure_ref'], row['run_id']),
                'evidences': [assurance_authority._artifact(db, receipt['evidence_ref'], row['run_id'])]}
            from .run_scope import require_full_source
            require_full_source(source)
            candidate = base.build_candidate(source, row['series_id'], row['proposal_id'], row['created_at'], expected_generation=value['expected_generation'])
            if (candidate != {'record': value['record'], 'comparison_context': value['comparison_context']}
                    or base._trusted_binding(bound) != value['binding_digest']
                    or receipt['created_at'] > row['created_at']):
                raise AdoptionError('STORAGE_CORRUPT')
        for row in db.execute('SELECT * FROM baseline_validations'):
            proposal_row = db.execute('SELECT * FROM baseline_proposals WHERE proposal_id=?', (row['proposal_id'],)).fetchone()
            proposal = base._validate_proposal_row(proposal_row)
            base._validation(db, row['validation_id'], proposal_row, proposal)
            if row['created_at'] > now:
                raise AdoptionError('STORAGE_CORRUPT')
        for row in db.execute('SELECT * FROM baseline_adoptions'):
            base._history(db, row)
            if row['adopted_at'] > now:
                raise AdoptionError('STORAGE_CORRUPT')
            current = db.execute('SELECT * FROM baseline_current WHERE series_id=?', (row['series_id'],)).fetchone()
            if current is None or current['generation'] < row['generation']:
                raise AdoptionError('STORAGE_CORRUPT')
        for row in db.execute('SELECT * FROM baseline_current'):
            base._history(db, row)
            base._revoked(db, row)
        for row in db.execute('SELECT * FROM baseline_revocations'):
            if (row['observed_at'] > now
                    or db.execute('SELECT 1 FROM baseline_current WHERE series_id=?', (row['series_id'],)).fetchone() is None):
                raise AdoptionError('STORAGE_CORRUPT')
    except (AdoptionError, ContractError, run_evidence.EvidenceError, KeyError, TypeError, ValueError):
        raise AdoptionError('STORAGE_CORRUPT') from None
