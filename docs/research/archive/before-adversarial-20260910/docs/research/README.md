---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 資料来歴

GAHの要求原稿と、コピー元が残っていた整理前構成を分けて保存する。archiveは現行の要求・指示・検収として適用しない。

| 資料 | 保存場所 | 扱い |
|---|---|---|
| GAHの現行要求 | [要求明確化案](../requirements.md) | 24要求と判定条件。元原稿からの継承・補強・後期対象は[対応表](../requirements-traceability.md)で管理 |
| 初期対象の補足前のv0.1 | [保存稿](archive/before-dual-usecases-20260910/docs/requirements.md) / [保存manifest](archive/before-dual-usecases-20260910/archive-manifest.json) | coding agentの開発・CIを初期対象と仮定した時点。利用者の補足により現行v0.2はLLMガードレール評価も対象とする。保存時の配置を基準とするリンクを含む |
| 明確化前の整形済み原稿 | [保存稿](archive/before-clarification-20260910/docs/requirements.md) / [保存manifest](archive/before-clarification-20260910/archive-manifest.json) | 構成図・YAML等の概念例・技術候補・外部主張を含む参考資料。保存時の配置を基準とするリンクを含む |
| 整理前のGAH原稿 | [原稿](archive/gah-requirements-original-20260910.md) | byte単位で保存、編集しない |
| コピー混在の整理前構成 | [当時のREADME](archive/scaffold-before-cleanup-20260910/README.md) / [保存manifest](archive/scaffold-before-cleanup-20260910/archive-manifest.json) | Spec Reconstructorの文書・Task・レビュー・承認・証跡・生成物などを保存。GAHの実績ではない |
| 原稿の引用参照 | [出典台帳](citation-register.md) | チャット内参照のみ。原資料とURLは未復元・未検証 |

[source-manifest.json](source-manifest.json)に原稿のraw hash、体裁整理時の本文保持記録、要求明確化前の保存版と改訂範囲を記録する。体裁整理時の本文一致は当時の版に対する結果であり、意味を具体化した現行要求とのbyte一致を主張しない。旧案件の原稿archiveも変更せず保持している。archive配下は現行Birdseyeと文書metadata検査から除外する。

保存稿の外部参照元実装・外部製品・新規性・制度に関する記述は、提供原稿の主張を残したもの。今回ソースコードやWebで再確認した結果ではない。現行要求の成立根拠からは分離し、出典の復元と評価は[未決定事項](../open-questions.md)として別作業にする。
