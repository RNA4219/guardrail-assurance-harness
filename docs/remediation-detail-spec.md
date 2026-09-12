---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# Finding と修復計画の詳細仕様 v1

評価の決定結果から、追跡可能な Finding と、実行しない構造化 Plan を生成する
部品仕様である。評価・ライフサイクル契約の補足として扱い、この部品単独では
保存、OS 認証、採択、外部操作、修復の完了、CI 成功を提供しない。

## 1. 共通境界

入力は JSON 型だけを受け付け、未知 field、重複参照、不正な digest、浮動小数、
非有限値、深すぎる構造、1 MiB を超える文書を固定理由で拒否する。ID は共通契約の
形式、digest は小文字 SHA-256 とする。返却値は入力のコピーであり、`ci_eligible`
は常に `false` である。入力文字列を命令として評価せず、エラーには本文や秘密を
含めない。

## 2. Finding

`generate_findings(assessment, *, context)` は決定結果の各 `reasons` から一件ずつ
Finding を作る。`source_assessment_ref`、Control、Observation、Comparison、
Evidence、必要なら recovery target を内容参照として保持する。原因候補は
`UNKNOWN`、`HYPOTHESIS`、`SUPPORTED` を区別し、候補を原因の確定事実へ昇格させない。
候補がない場合は `cause-unknown` を生成する。

`finding_ref(finding)` は Finding のIDと状態遷移しない内容から作るため、状態・時刻・
再検証候補の追加で参照が変わらない。参照対象の内容digestは引き続き保存時に検査する。

Finding の状態は `OPEN` → `IN_PROGRESS` → `AWAITING_REVALIDATION` → `VERIFIED`
である。初期状態は OPEN で、計画生成・計画出力・状態の手書き変更だけでは
VERIFIED にならない。公開 `verify` 遷移も、現在のEvidenceの有効性とOS主体認証を
証明できないため、具体的な再検証候補を AWAITING_REVALIDATION に保存するだけである。
authority接続側が保存済み根拠と認証を再照合した後にのみ、VERIFIED記録を確定できる。
この部品の `validate_finding` も `VERIFIED` 入力を受け付けない。確認参照の形だけでは
認証にならず、保存済みreceiptを検証するauthority専用入口で最終記録を扱う。
Finding 自体は不変の入力として扱い、遷移は新しい値を返す。

## 3. Plan

`generate_plan` は変更対象、目的、保持すべき閾値・検査条件、再検証手順、展開、
復旧先、必要権限、不足情報を検査して Plan を返す。計画の `plan_status` は計画の
完全性 (`READY` / `NEEDS_INFORMATION`) を表し、元の評価の Assurance や修復成功を
表さない。`execution_status` は常に `NOT_EXECUTED` である。

原因候補の参照は元 Finding の Evidence 集合に限定する。必須の再検証には同じ
binding、同じ条件、閾値不変、検査保持、必要 Evidence、確認主体、手順を含める。
閾値の緩和、検査の削除、データ差替えは保持条件を満たさず、修復証拠にできない。
必要情報が残る場合は `NEEDS_INFORMATION` とし、計画を実行可能・修復済みとは扱わない。
標準骨子が復旧先や確認者を得られない場合は、「復旧先が不明」「確認者が未指定」などの
固定された不足情報を日本語で追加する。

`plan_to_yaml` は追加の YAML 実行器を呼ばず、canonical JSON を YAML 1.2 の JSON
サブセットとして返す。したがって任意タグ、式、コード、外部参照の実行はない。

## 4. 再検証・再発・別処遇

`transition_finding(..., "verify", verification=...)` は AWAITING_REVALIDATION から
だけ許可する。検証候補には元 Finding の完全一致参照、変更対象と元Controlの対応、
policy/contract/registry/case_set/oracle/evaluator/plan/反復設定の具体的な旧条件と新条件、
新Evidenceの内容digest・subject・conditions・producer・観測時刻・期限・撤回/欠損状態、
確認主体を要求する。旧条件と新条件が完全一致し、Evidenceが未撤回・未欠損で期限内でも、
候補には `origin_binding_verified=false` と `authority_connected=false` を明示し、この部品
だけでは元runとの照合・現在有効性を証明できないため、候補をVERIFIEDへ昇格しない。

`record_recurrence` は元 Finding を変更せず、`parent_finding_ref` を持つ新しい OPEN
Finding を作る。基準改訂は `BASELINE_REVISED`、対象廃止は `TARGET_RETIRED` という
別 disposition と理由に記録し、VERIFIED の代用にしない。処遇後の再検証も拒否する。

## 5. 未接続範囲

本部品は SQLite 等への保存、原子的な更新、OS の主体認証、権限付与、runner の停止・
復旧、Evidence の実在性確認を実装しない。呼出側がこれらを行った証拠を参照として
渡し、別の管理境界で照合する必要がある。ここで返す構造だけを根拠に CI 成功や
修復完了を主張してはならない。
