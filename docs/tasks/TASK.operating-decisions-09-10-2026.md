---
task_id: 20260910-07
intent_id: INT-GAH-001
owner: RNA4219
status: done
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Task: 初期運用方針の決定と要求への反映

## Objective

利用者が回答したCI・閾値/予算の委任・評価資産転用・生成AI管理を具体化し、要求整理を契約・評価設計へ渡せる状態にする。

## Scope

In: v0.3の15ファイル保存、要求v0.4、初期運用方針v1、受入例・判断事項・関連入口の更新、DGX Qwenの指摘照合、文書の技術検収。
Out: 製品実装・実測・実環境CIの変更、未特定OSSのコード/データの転用、外部公開。元OSSの正確な特定と転用対応は次工程へ渡す。

## Requirements

利用者の「CIで許す」「閾値と予算はおまかせ」「評価周りはopsのOSSから転用」「管理主体は生成AI」。[要求](../requirements.md)と[運用方針](../operating-policy.md)に反映し、通常更新に都度の人間承認を前提としない。

## Affected Paths

README/Blueprint/Hub、要求・受入・運用方針・判断事項・対応表、契約/datasetsの入口、開発順序・拡張案・資料来歴、Task/Acceptance/Evidence/Birdseye。

## Local Commands

```sh
python -m tools.workflow generate
python -m tools.workflow check --report docs/evidence/operating-decisions-20260910/workflow-check.json
python -m unittest discover -s tests -v
```

## Deliverables

初期方針と数値・境界、32要求/32受入条件、D02〜D05の回答反映、転用元未特定の明示、Qwen4指摘の処遇、改訂前15ファイルと文書検証証跡。

## Plan

1. 改訂前を保存し、利用者指定と選定委任を分ける。
2. 閾値・予算・管理AIの役割と採択範囲を具体化する。
3. DGX Qwenの指摘を照合し、判定・算術例・判断事項を揃える。
4. 来歴と要求ID、文書ワークフロー、既存テストを確認して技術検収を閉じる。

## Tests

文書構造・算術・来歴の21項目、文書ワークフロー12項目、既存unittest10件が成功した。32要求/32受入条件、原稿17機能/14受入項目、保存15ファイルのhashを確認した。製品受入は未実行。

## Commands

Python 3.12.14のbundled runtimeを実体パスと`-X utf8`付きで使用し、Local Commandsと一回限りの文書・算術・hash照合を実行した。DGXの既存localhost接続からqwen3.8-flash-nextへ文書照合を1回依頼し、全4指摘を親が照合した。

## Notes

[改訂理由と指摘の処遇](../reviews/operating-decisions-20260910.md)、[技術検収](../acceptance/AC-20260910-07.md)を参照。転用方針の決定と実際のコード/データ転用を区別する。
