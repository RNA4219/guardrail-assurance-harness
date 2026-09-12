---
task_id: 20260910-08
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

> この履歴は利用者の非掲載指示に従って匿名化済み。引用中の参照元表記も一般化した表示であり、過去の検証結果は当時の記録として扱う。

# Task: 外部参照元転用元の確定と対応表

## Objective

利用者提示の外部参照元のURLでD04の確認待ちを解消し、実在する評価運用部品とGAHで補う評価機能を区別して設計へ渡す。

## Scope

In: 外部参照元の固定commitの取得と静的確認、改訂前15ファイルの保存、6項目の転用対応表、要求と関連入口の整合、来歴と文書の技術検収。
Out: 外部参照元本体の変更・上流コード/テストの実行、GAH製品へのコード移植・評価データの取り込み、製品実測、ライセンス変更・公開。

## Requirements

利用者が転用元として提示した `[参照先非掲載])の32要求、WARNING成功・初期閾値/予算・生成AIの管理を維持する。

## Affected Paths

要求・受入・運用方針・判断事項・対応表、外部参照元転用計画、README/Blueprint/Hub、契約/datasetsの入口、開発順序、資料来歴、Task/Acceptance/Evidence/Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/reference-confirmation-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

[外部参照元転用対応表](../reference-boundary.md)、固定commit・Git blob・13確認ファイルのhash、D04確認済みの要求文書、保存15ファイル、文書の検証証跡。

## Plan

1. 指定URLから固定commitを取得し、改訂前のGAH文書を保存する。
2. Manifest/Schema・状態更新・判定・保存・CLIとテストを静的に照合する。
3. 再利用範囲、GAH側の追加機能、現在の利用条件を記録して各入口を更新する。
4. 要求ID・元資料のhash・リンク・既存ワークフローを検証する。

## Tests

文書・固定版・来歴の21項目、GAHの文書ワークフロー12項目、既存unittest10件が成功した。32要求/32受入条件、初期値の全表の不変、6転用範囲、15保存ファイル、13上流確認ファイルのhashを照合した。外部参照元のテストとGAHの製品受入は未実行。

## Commands

`git ls-remote`でmainを確認し、調査用`.ga/reference-source-private-revision`へ浅いcloneを取得した。HEADと作業ツリーの未変更を確認。外部コードを実行せず、Python AST・TOML/JSON読取りとソースの静的確認を行った。GAHではbundled Python 3.12.14を`-X utf8`付きで用い、Local Commandsと文書・来歴の一回限りの照合を実行した。

## Notes

外部参照元に存在するのは評価運用の基盤であり、LLMの指標計測・評価器校正・期待ラベル付き評価集合はGAH側で補う。外部参照元の現行license欄はProprietary。利用者指定は反映し、公開時の表記整合を転用工程へ渡す。前Taskの「転用元確認待ち」は当時の記録として保持する。[技術検収](../acceptance/AC-20260910-08.md)を参照。
