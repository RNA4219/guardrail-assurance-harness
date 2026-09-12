---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# MVP登録・ケース・証拠の実装契約

全MVP完成へ向け、[評価契約](contracts/evaluation-contract.md)のControl、CaseDefinition、Evidenceを具体化する。実装中であり、これらの部品だけで管理AIの認証・隔離・全要求の受入を証明しない。[全体Task](tasks/TASK.mvp-completion-09-11-2026.md)に残る接続を記録する。

## 共通

JSONはUTF-8、1 MiB以下、深さ16以下、整数は絶対値2^53−1以下。重複key、小数、未知field、未知版、不正型を拒否する。boolを整数にしない。IDはASCII英数字から始まる128文字以下の英数字・underscore・dot・colon・hyphen。digestは小文字SHA-256の64桁。参照はkind/id/digestの3field。構造の検査と主体・由来の認証は別に行う。

時刻はUTC Unix時刻の整数秒とし、ISO文字列やミリ秒と混ぜない。JSON Schemaは形状の検査に用い、全体一意性・参照整合・循環・byte上限・意味条件にはruntime validatorを必ず併用する。Schema単独を登録入口にしない。

## Control登録

Registryはschema_version=1、kind=control_registry、registry_id、controlsを持つ。Controlはcontrol_id、owner、invariant（空白でない512文字以下の説明）、criticality（critical/noncritical）、target_ref、dependencies、obligations、mutation_applicabilityを持つ。説明をコードとして評価しない。

obligationはobligation_id、kind（constraint/mutation/llm_metric）、required（bool）、event_policy（forbidden/aggregate/none）、evaluator_refを持つ。IDはregistry全体で一意。各Controlは一つ以上の義務を持ち、criticalでは全義務required=true。依存IDの重複・参照欠落・循環を拒否する。任意化によるCriticalの格下げは採択境界でも拒否する。

mutation_applicabilityはstatus（applicable/not_applicable）とreason（applicableではnull、対象外では空白でない512文字以下）を持つ。applicableならmutation義務を一つ以上必要とし、対象外にはmutation義務を置かない。対象外をMutation実施済みとしない。

`validate_registry(document)`は検査済みの独立したcopyを返す。`dependency_closure(document, selected_ids)`は選択したControlと全依存を返す。`affected_controls(document, changed_ids)`は変更元と逆方向の影響閉包、changed_ids=nullなら全対象を返す。空選択・不正IDを拒否する。これらは実行前の計画に使い、限定範囲を全体合格へ拡大しない。

Controlは最大1000件、一Controlの依存と義務はそれぞれ100件。循環は反復探索で検出してCYCLEを返し、上限までの有効な鎖を言語処理系の再帰上限で拒否しない。直接dict入力でもcanonical UTF-8の1 MiB上限を検査する。

## CaseSetと校正

CaseSetはschema_version=1、kind=case_set、case_set_id、purpose（development/calibration/acceptance）、required_categories（空でない一意ID配列）、casesを持つ。Caseはcase_id、lineage_group、category、expected_label（positive/negative/indeterminate）、oracle_ref、initial_state_ref、session_steps、scored_stage_idを持つ。case_idは一意。段階は1または2個でstage_id、input_ref、expected_detection（detect/allow/indeterminate）、event_policy（forbidden/aggregate/none）を持つ。採点段階は一つの実在するstageを指定し、その分類はcaseのexpected_labelに対応する。

`validate_case_set(document)`は構造・参照形式・段階・ラベルを検査して独立copyを返す。`corpus_report(document, other_sets=())`はラベル・カテゴリ別のlineage件数、同じ実行入力のcase、用途間のsample digest/lineage重複と不足を返す。sample fingerprintはinitial_stateと順序付きinput参照のkind+digestで作り、case/lineage/ref/stage IDの改名で変えない。カテゴリ・期待ラベル・oracle・期待条件等の改訂は同一sampleの条件差分として追跡する。渡された全集合のpairを照合し、現行集合にない重複も報告する。

acceptanceには正負各200lineage、必須カテゴリごとに正負各100を必要とする。同lineageや同一sampleをラベル・カテゴリ・用途をまたいで固有件数へ水増ししない。現行集合の件数充足と用途間重複を分けて報告する。構造上の一意性だけで意味上の独立性を認証したと主張せず、oracle根拠と管理境界での採択を別に必要とする。上限はカテゴリ256、case 10000、比較集合64、校正観測20000件。比較集合iteratorは上限を判定できる最初の65件までしか消費しない。

`check_calibration(case_set, observations)`はcalibration用途に限り、正・負・判定不能が存在し、全段階の一意な観測が期待検知と一致する場合だけpassedとする。観測はcase_id/stage_id/detectionの3field。不足・余剰・重複・誤分類・未知型を成功にしない。これは評価器の固定分類校正であり、校正していない評価器の自己採点を取り込まない。

## ArtifactとEvidenceの保存

`ArtifactStore`は費用台帳とは別のSQLiteファイルを使う。不変artifactはkind/id/canonical bytes/SHA-256、Evidenceはevidence_id/subject_ref/artifact_ref/conditions_ref/producer_ref/observed_at/collected_at/retention_untilを保持する。初期対応documentはControlRegistryとCaseSet。他の型は個別の検査器を接続するまで保存しない。

保存前に容量・厳格JSON・型を検査し、trustedな呼出側が渡した許可表のkind/id/digestへ一致させる。payloadの「合成」などの自己申告を許可根拠にしない。不一致のpayloadやdigestを拒否記録へ保存せず固定codeを返す。この内部許可表の配置保護と管理主体の認証は、後続の管理境界で検証する。

同じkind/idの同じ内容は再配送として元のartifactを返す。別内容は拒否。read時にはbytes/hash/IDを再照合する。新しいEvidenceの時刻は信頼した監督が渡すobserved_atと保存clockのcollected_atを分け、未来観測を拒否する。再配送で観測・保持時刻を延長しない。

保持は初期値90日、現在証拠の鮮度24時間、baseline比較30日。保持と鮮度の境界以下は有効、超過は無効。撤回・削除は期限内でも無効とし、単調増加する失効世代を更新する。削除はartifact payloadを読めなくし、ID/digestの墓標と参照を保持して再取込みによる復活を拒否する。全参照Evidenceへ影響する。OSバックアップやディスクの物理消去を本APIの保証にしない。

時計巻戻り、保存障害、破損、未存在参照は固定codeで拒否する。変更はBEGIN IMMEDIATEの一transactionで行い、BaseExceptionでもrollbackする。期限判定は毎回最新の撤回・削除を読み、昔の成功記録で現在のCIを通さない。

`read_evidence`はevidence本体と最新のuse状態を同じtransactionで返す。`check_evidence`のvalidはartifactの完全性・時刻・失効の範囲であり、ci_eligible=false。subject/conditions/producerの実在・認証、現在対象の一致、実行の完了を証明しない。初期化のSQLite/OS失敗はSTORAGE_FAILURE、clock取得失敗はCLOCK_UNAVAILABLEとし、payloadや例外本文を外へ出さない。

## 受入と次の接続

登録10Control、5 familyに対応する固定fixture、正常/不正/cycle/Critical任意化、影響閉包、用途混同・派生・重複・常時PASS/拒否の校正、Evidenceの境界/削除/撤回/再配送/DB故障を実行する。400ケースの実データと独立oracle、全MVPオブジェクト、採択・実行・CI連携はこの構造部品だけで完成にしない。
