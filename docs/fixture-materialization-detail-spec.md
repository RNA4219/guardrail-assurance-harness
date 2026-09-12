---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 固定fixture materialization 詳細仕様 v1

固定workerの実入力、期待観測、実行scenarioを、既存のrun bindingへ決定的に結び付ける。
輸送用の400件参照や表示用データを実評価・性能根拠・全MVP受入・baseline採択へ読み替えない。
Docker実行、外部通信、任意command・外部target、OS認証、Evidence保存、CI合格は対象外である。

## 1. APIと境界

```python
build_fixture_pack(
    policy, worker_source, runtime_lock, execution_profile, now, run_id,
    *, policy_generation=1
) -> dict

materialize_fixture_manifest(
    *, bound_run, worker_source, runtime_lock, execution_profile, now
) -> dict

validate_fixture_manifest(
    manifest, *, bound_run, worker_source, runtime_lock, execution_profile, now
) -> dict
```

`build_fixture_pack`は固定workerを実行せず、良好状態のUC-CI acceptance packを作る。
返却`pack`は`schema_version=1, kind=fixture_pack, pack_id, run_id, worker_digest,
 runtime_lock, execution_profile, materials, calibration_materials`を持ち、各materialに入力・期待oracle・初期状態の
 payload本体を保持する。返却`bound_run`は`bind_run_manifest`の結果にこのpackを付加したもの。
 `calibration_case_set`も返し、`policy_generation`は契約へ固定記録する。

`bound_run`は保存済みのManifest、Contract、Policy、Registry、CaseSet、TrialPlan、
`selected_controls`、`ci_eligible=false`を含む。materializerは開始時に
`run_contracts.bind_run_manifest(..., baseline_context=...)`を再実行し、callerのcoreと完全一致させる。
packを含まないbound、自己申告だけのcore、再bind失敗は拒否する。

返却は常に`structurally_bound=true`、`authority_connected=false`、`ci_eligible=false`であり、
実行済み・認証済み・採択済みを意味しない。

## 2. source、runtime、profile

`worker_source`はbytesのままSHA-256を計算し、runtime lockの`worker_digest`、profileの
`fixture_digest`と一致させる。lockは固定image ID、`python@sha256:`のbase、worker・Docker
binary digest、entrypoint`["/usr/local/bin/python", "-I", "-B", "/opt/gah/fixture_worker.py"]`、
`linux/amd64`、環境配列を厳密に検査する。profileは
`fixture_digest`、`adapter_digest`、`isolation_digest`の小文字SHA-256だけを持つ。

各scenarioの`target_ref`は、次のpayloadのcanonical SHA-256をdigestとする。

```text
{"worker_digest": <worker digest>, "scenario": <fixed scenario>}
```

`evaluator_ref`は固定worker evaluatorのIDとworker digestを持つ。`environment_ref`は既存の
固定Docker profileの`kind=environment,id=fixed-docker-profile,digest=isolation_digest`へ結び付ける。
lock/profileの全体はpack本体で保持して別途照合する。別source、target、evaluator、環境、段階の
差替えは固定エラーで拒否する。

## 3. acceptance packとpayload

初期acceptance packは、Registryの15 Control（constraint C01〜C10、mutation F01〜F05）、
CaseSetの15 case、TrialPlanの15 candidate entryを一対一で持つ。各entryは一つの固定scenario
だけに対応し、同じentryをgood/badやhealthy/decayedへ使い回さない。最低要件の10 Controlと
5 mutation familyを満たし、UC-LLMの400件参照を流用しない。

acceptance scenarioと正規化後の期待値は次の15件である。

```text
constraint:C01:good ... constraint:C10:good  -> observation=PASS
mutation:F01:healthy ... mutation:F05:healthy -> mutation_outcome=KILLED
```

各Caseの`input_ref`、`oracle_ref`、`initial_state_ref`（kindは既存契約の`input`、`oracle`、
`initial_state`）はpackに保存したpayload本体から作り、
CaseSet、entry、materializer recordで完全一致させる。入力payloadはworkerの固定関数が使う
good/healthy candidateを再現し、oracle payloadはworker出力を`normalized`が得る固定期待値を
持つ。target digestはworker digestとscenarioから再計算し、control・entryのtargetと照合する。
Caseのstageは一つだけで、entryのstage、Caseのinput、scenarioの対応が一致しなければ拒否する。

good/bad、healthy/decayedの30対照と異常probeは、acceptance entryを再利用しない独立した
calibration工程へ分離する。factoryはworker scenarioではない36個のmeasurement calibration
metadataを`calibration_materials`とcalibration CaseSetへ保持し、各input/oracle/initial_state
refを実体照合する。calibrationのlabel/detectionは`indeterminate`で、LLMのpositive/detectへ
読み替えない。probeは評価case数やControl/mutationの成功数へ含めない。
固定probeの期待境界は次の通りである。

| scenario | status | reason |
|---|---|---|
| `probe:isolation` | `COMPLETED` | `null` |
| `probe:child_timeout` | `TIMEOUT` | `TIMEOUT` |
| `probe:oversized` | `FAILED` | `OUTPUT_TOO_LARGE` |
| `probe:malformed` | `FAILED` | `OUTPUT_REJECTED` |
| `probe:rejected_marker` | `FAILED` | `OUTPUT_REJECTED` |
| `probe:crash` | `FAILED` | `EXECUTION_FAILURE` |

## 4. manifestと再検査

manifestは`schema_version=1, kind=fixture_materialization, materialization_id, run_id,
worker_ref, image_ref, profile, source_digest, records, probes, created_at, ci_eligible=false`
を持つ。recordはmaterialのscenario、Control、義務、case、trial、stage、candidate variant、
入力ref、oracle ref、期待値を保持する。recordのrefだけを新規生成してCaseSet実体と異なる
payloadを隠すことは許さない。

`validate_fixture_manifest`は同じpack、core binding、source、lock、profile、作成時刻から
manifestを再構成し、全payload、ref、target、evaluator、stage、期待値を比較する。未知field、
未登録scenario、packの欠落・重複、entryの使い回し、CaseSet参照改変、別target、段階欠損、
source/profile/image差替え、record/probeの追加・削除・並べ替えは拒否する。

## 5. 受入シナリオと未接続範囲

1. 実worker sourceのdigestとlock/profileを固定し、実在する15 Control、15 Case、15 entryを
   bindしたpackと36件のmeasurement calibration CaseSetを生成できる。
2. packの各payloadとCaseSet/Planを一対一で照合し、target/evaluator digestを再計算できる。
3. 参照だけの輸送fixture、10未満のControl、5未満のmutation family、別scenarioの使い回しを拒否する。
4. 同一入力からmanifestを再生成でき、変更後のpayload・ref・target・段階を拒否する。
5. probeを通常のPASS/KILLEDや評価case数へ混ぜず、authority・CIへ昇格させない。

この部品は実Docker実測、workerの実観測、独立calibration、Evidence/Decision保存、OS主体認証、
resource closure、baseline採択、通常CI判定、全MVP受入を完了した証拠ではない。
