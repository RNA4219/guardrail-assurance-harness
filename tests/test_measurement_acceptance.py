"""GAH-AC09の件数・分母を、固定rawから正規化・集計まで検証する。"""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from tests import test_aggregation as helpers
from gah.aggregation import aggregate
from gah.normalized import normalize_generic
from gah.run_contracts import content_ref, bind_run_manifest
from gah.wire import canonical_bytes


class MeasurementAcceptanceTests(unittest.TestCase):
    def arrange(self):
        f=helpers._fixtures();original=f["case_set"]["cases"][0];cases=[]
        for index in range(20):
            case=deepcopy(original);case.update(case_id="case-"+str(index+1),lineage_group="lineage-"+str(index+1),
                expected_label="positive" if index<10 else "negative")
            case["oracle_ref"]=content_ref("oracle","oracle-"+str(index),{"positive":index<10})
            case["initial_state_ref"]=content_ref("initial_state","state-"+str(index),{"clean":True})
            case["session_steps"][0]["input_ref"]=content_ref("input","input-"+str(index),{"fixed":index})
            case["session_steps"][0]["expected_detection"]="detect" if index<10 else "allow"
            cases.append(case)
        f["case_set"]["cases"]=cases
        f["contract"]["case_set_ref"]=content_ref("case_set",f["case_set"]["case_set_id"],f["case_set"])
        plan=helpers._plan(f);initial=deepcopy(plan["entries"])
        plan["entries"]=[{**entry,"case_id":case["case_id"],"trial_id":"trial-"+str(index+1)}
            for index,case in enumerate(cases) for entry in initial]
        plan["contract_ref"]=content_ref("evaluation_contract",f["contract"]["contract_id"],f["contract"])
        bound=bind_run_manifest(helpers._manifest(f,plan),f["contract"],plan,f["policy"],f["registry"],f["case_set"])
        f["contract_ref_digest"]=bound["manifest"]["contract_ref"]["digest"]
        f["policy_ref_digest"]=bound["manifest"]["policy_ref"]["digest"]
        attempts=[]
        for index,entry in enumerate(plan["entries"]):
            case_index=int(entry["case_id"].split("-")[-1])-1
            mode={"obligation-constraint":"constraint","obligation-mutation":"mutation","obligation-llm":"llm"}[entry["obligation_id"]]
            if mode=="constraint":observations={"check":"PASS"}
            elif mode=="llm":observations={"detection":"detect" if case_index<8 or case_index==10 else "allow","deviation":False}
            else:observations={"baseline":"PASS","mutation_applied":case_index<10,
                "reached":case_index!=9,"detected":case_index<8,"unrelated_failure":False}
            name="attempt-"+str(index)
            binding=helpers._binding(f,plan,entry["obligation_id"],case_id=entry["case_id"],trial_id=entry["trial_id"])
            binding["operation_id"]="operation-"+str(index)
            raw=canonical_bytes({"schema_version":1,"kind":"gah_generic_result","binding":binding,
                "mode":mode,"observations":observations})
            result=normalize_generic(raw,binding,execution_status="COMPLETED",exit_code=0,stop_confirmed=True)
            attempts.append(helpers._record(binding,attempt_id=name,result=result))
        profile={"fixture_digest":helpers.ZERO,"adapter_digests":["1"*64],"isolation_digest":"2"*64}
        return bound,attempts,profile

    def test_twenty_labels_and_mutation_errors_keep_the_required_counts_and_denominators(self):
        bound,attempts,profile=self.arrange();result=aggregate(bound,attempts,execution_profile=profile)
        counts=result["counts"]["variant"]["candidate"]
        self.assertEqual([counts[k] for k in ("tp","fn","fp","tn")],[8,2,1,9])
        self.assertEqual([counts[k] for k in ("killed","survived","no_coverage","mutation_error")],[8,1,1,10])
        wanted={"recall":Fraction(8,10),"fnr":Fraction(2,10),"fpr":Fraction(1,10),"mutation_score":Fraction(8,10)}
        for name,expected in wanted.items():
            metrics=[m for m in result["metrics"] if m["name"]==name]
            self.assertTrue(metrics)
            for metric in metrics:self.assertEqual(Fraction(metric["numerator"],metric["denominator"]),expected)
        self.assertTrue(result["required_missing"])
        self.assertFalse(result["ci_eligible"])

    def test_recovered_retry_keeps_fault_history_without_an_extra_mutation_error(self):
        bound,attempts,profile=self.arrange()
        child=next(a for a in attempts if a["result"]["mode"]=="mutation")
        parent=deepcopy(child);parent["attempt_id"]="failed-parent";parent["execution_status"]="FAILED"
        parent["result"]=normalize_generic(b"",parent["expected_binding"],execution_status="FAILED",exit_code=1,stop_confirmed=True)
        child.update(retry_of="failed-parent",state_restored=True,started_at=112,finished_at=113)
        attempts.append(parent)
        result=aggregate(bound,attempts,execution_profile=profile);counts=result["counts"]["variant"]["candidate"]
        self.assertEqual(counts["killed"],8);self.assertEqual(counts["mutation_error"],10)
        self.assertEqual(counts["fault_count"],1);self.assertEqual(counts["retry_count"],1)

    def test_two_error_stages_count_one_mutation_trial(self):
        f,plan,bound=helpers._two_stage_bound();attempts=[]
        for index,stage in enumerate(("stage-1","stage-2")):
            binding=helpers._binding(f,plan,"obligation-mutation",stage_id=stage)
            result=normalize_generic(b"",binding,execution_status="TIMEOUT",exit_code=1,stop_confirmed=True)
            attempts.append(helpers._record(binding,attempt_id="timeout-"+str(index),status="TIMEOUT",result=result,
                started_at=110+index,finished_at=111+index))
        profile={"fixture_digest":helpers.ZERO,"adapter_digests":["1"*64],"isolation_digest":"2"*64}
        result=aggregate(bound,attempts,execution_profile=profile);counts=result["counts"]["variant"]["candidate"]
        self.assertEqual(counts["mutation_error"],1);self.assertEqual(counts["error"],2)
        self.assertEqual(counts["killed"],0);self.assertTrue(result["required_missing"])

    def test_missing_and_indeterminate_labels_do_not_become_successes(self):
        bound,attempts,profile=self.arrange()
        llm=[a for a in attempts if a["result"]["mode"]=="llm"]
        attempts.remove(llm[0]);llm[1]["result"]["detection"]="indeterminate"
        result=aggregate(bound,attempts,execution_profile=profile);counts=result["counts"]["variant"]["candidate"]
        self.assertEqual(counts["tp"],6);self.assertGreaterEqual(counts["missing"],1)
        self.assertEqual(counts["indeterminate"],1);self.assertGreaterEqual(counts["detection_missing"],1)
        self.assertTrue(result["required_missing"]);self.assertFalse(result["ci_eligible"])


if __name__=="__main__":unittest.main()
