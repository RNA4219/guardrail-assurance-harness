---
task_id: 20260910-10
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: 参照元情報の非掲載

## Objective

利用者の「名前も書かないこと」を適用し、参照元を特定する名称・略称・URL・版識別子をGAHの管理ファイルから除く。

## Scope

In: 現行文書、ファイル/ディレクトリ名、保存稿、Task/Acceptance、証跡、調査用補助ファイル、生成索引の匿名化。非匿名化版を新たに保存せず、18ファイルの匿名化した保存版を作成する。
Out: 外部原本の変更、過去の会話の削除、製品実装・製品試験。過去の閲覧履歴自体を取り消したとは扱わない。

## Requirements

参照元の名称を掲載しないという利用者指定を、通常のarchive不変方針より優先する。[情報非掲載の方針](../reference-boundary.md)を現行の指示とし、元の識別子をログや別のバックアップへ書き出さない。GAHの要求・初期数値は維持する。

## Affected Paths

現行の入口・要求・運用方針・資料来歴、Task/Acceptance/Evidence、匿名化対象の保存稿とmanifest、補助ファイル、Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/reference-anonymization-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

名称を含まない現行文書と配置、今後の非掲載ルール、匿名化した18ファイルの保存版、履歴の匿名化注記、保存manifestの現在hash、残存検査と文書検証の記録。

## Plan

1. 対象を現行・履歴・生成物へ分類し、置換後の配置の競合とworkspace内の境界を確認する。
2. 本文とファイル名を匿名化し、残った空の旧配置だけを除去する。
3. 過去の検証結果を当時の履歴として明示し、現在の保存版のhashを更新する。
4. 残存件数、32要求/32受入条件、初期値、保存manifest、文書ワークフローを確認する。

## Tests

本文・配置・構造・保存版の15項目、文書ワークフロー12項目、既存unittest10件が成功した。名称等の残存は本文・配置とも0件。32要求/32受入条件と初期数値を維持し、保存manifest8件のhashを照合した。非掲載対象そのものは検査結果に保存せず、件数のみを記録する。

## Commands

workspace内の対象pathと移動先の未存在を検証して一般化した配置へ変更した。本文とファイルの変換後、空の親ディレクトリの除去が一度停止したため、部分適用を確認し、ファイルを含まないことを確認して残る空ディレクトリだけを除去した。非匿名化版は新規保存していない。過去の証跡20件を履歴として注記し、保存manifest8件を匿名化後のbytesへ照合した。

## Notes

名称とURLは非掲載だが、外部のクローズド資産を持ち込まない規則は維持する。過去のhashや合格記録を匿名化後の再検証として扱わない。[技術検収](../acceptance/AC-20260910-10.md)を参照。
