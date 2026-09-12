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
| GAHの現行要求 | [要求整理](../requirements.md) | 内容を維持して整形した草案 |
| 整理前のGAH原稿 | [原稿](archive/gah-requirements-original-20260910.md) | byte単位で保存、編集しない |
| コピー混在の整理前構成 | [当時のREADME](archive/scaffold-before-cleanup-20260910/README.md) / [保存manifest](archive/scaffold-before-cleanup-20260910/archive-manifest.json) | Spec Reconstructorの文書・Task・レビュー・承認・証跡・生成物などを保存。GAHの実績ではない |
| 原稿の引用参照 | [出典台帳](citation-register.md) | チャット内参照のみ。原資料とURLは未復元・未検証 |

[source-manifest.json](source-manifest.json)にGAH原稿のraw hash、本文保持の検査方法、整理前構成の保存場所を記録する。旧案件の原稿archiveも変更せず保持している。archive配下は現行Birdseyeと文書metadata検査から除外する。

要求本文の外部参照元実装・外部製品・新規性・制度に関する記述は、提供原稿の主張を残したもの。今回ソースコードやWebで再確認した結果ではない。出典の復元と評価は[未決定事項](../open-questions.md)として別作業にする。
