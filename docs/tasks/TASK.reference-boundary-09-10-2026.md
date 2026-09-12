---
task_id: 20260910-09
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

> この履歴は利用者の非掲載指示に従って匿名化済み。引用中の参照元表記も一般化した表示であり、過去の検証結果は当時の記録として扱う。

# Task: 外部参照元クローズド資産の参照境界への訂正

## Objective

外部参照元をそのまま使わないという利用者の訂正を反映し、旧移植方針を撤回してGAHの独立設計へ切り替える。

## Scope

In: 訂正前18ファイルの保存、現行要求・運用方針・入口・AGENTSの修正、この作業で取得した未変更の調査コピーの削除、来歴と文書の技術検収。
Out: 外部参照元本体の変更・公開化、外部参照元資産の複製/移植/依存化、製品実装、製品試験。

## Requirements

利用者指定「外部参照元自体はクローズド資産なんでそのまま使わないでね」。コード・Schema・テスト・文書・データの持込みと名前だけ変えた移植を行わず、[参照境界](../reference-boundary.md)を適用する。二つの利用場面・32要求/32受入条件・初期値・生成AI管理は維持する。

## Affected Paths

AGENTS/Guardrails、README/Blueprint/Hub、要求・受入・運用方針・判断事項・対応表、外部参照元参照境界、契約/datasetsの入口、開発順序、資料来歴、Task/Acceptance/Evidence/Birdseye、取得した調査コピー。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/reference-boundary-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

クローズド資産の参照境界、旧移植方針の撤回、GAHの要求から独立に設計する規則、調査コピーの削除記録、保存18ファイルと文書検証証跡。

## Plan

1. 訂正前を保存し、旧記録と現行指示を区別する。
2. この作業で取得した調査コピーの絶対path・commit・未変更・再解析ポイント不在を確認し、そのコピーのみ削除する。
3. 直接利用の記載を訂正し、AGENTSとGuardrailsで今後の作業にも適用する。
4. 要求ID・初期値・来歴の保持、コピー不在、文書生成・既存テストを確認する。

## Tests

参照境界・来歴・コピー不在の18項目、文書ワークフロー12項目、既存unittest10件が成功した。32要求/32受入条件と初期数値の全表、保存18ファイルのhashを維持した。製品への外部参照元資産の移植は行っていない。過去の静的確認の履歴は保存し、クリーンルーム工程を実施済みとは主張しない。

## Commands

前Taskで取得した`.ga/reference-source-private-revision`について、GAH配下の想定した絶対path、commit nonpublic-revision、作業ツリー未変更、再解析ポイント不在を確認した。PowerShellの同一処理内でそのコピーだけを削除し、不在を確認した。外部参照元の元リポジトリは変更していない。

## Notes

過去のTask/Acceptance/保存稿は当時の記録として保持するが、資産の利用許可としては適用しない。公開OSSを使う場合は公式の原典から別に確認する。外部参照元のライセンス変更・公開化はGAHの着手条件にしない。[技術検収](../acceptance/AC-20260910-09.md)を参照。
