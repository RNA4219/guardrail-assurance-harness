---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 仕様の入口（未作成）

規範的な実装仕様はまだない。[要求草案](requirements.md)中のYAML、JSON、Python、図は概念例であり、確定したSchema・API・CLI・状態遷移ではない。

仕様化ではManifestのdesired/observed分離、Control、Evidence、Mutation結果、Assurance判定、修復計画の契約を具体化する。評価不能・未実行・期限切れ・失敗と成功を混同しない要求を、機械的に検査可能な条件へ落とす。

採否と未定義部分は[未決定事項](open-questions.md)、契約への導線は[contracts](contracts/README.md)で管理する。今回の整理では仕様を執筆・確定しない。実行可能な開発コマンドは[RUNBOOK](../RUNBOOK.md)のみ。

## セキュリティ

仕様化では要求草案のrunner分離、権限と承認、証拠の完全性・鮮度、Criticalの不明・未検証時の扱いを具体化する。現在の開発基盤の確認対象は[SAC](security/SAC.md)に記載しており、これらの製品制御の動作検証は未実施。
