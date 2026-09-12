---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# Birdseye生成契約

`python -m tools.workflow generate` はAcceptance索引を先に作り、現行Markdownのタイトル・本文要約・ローカルリンクからindex/hot/capsを生成する。archive、テンプレート、Evidence JSON、vendorはsource集合に含めない。

indexはpathをkeyとするnodesと有向edgesを持つ。capsはsource SHA-256、要約、入出力依存を持つ。hashはUTF-8/LF正規化。mtime/generated_atは上流形式の5桁世代番号で、index・hot・全capsが同じ世代を使う。

入力が同じ場合は再生成してもbyteを変えない。入力更新や破損時に全体を新世代として再生成する。hotのlast_verified_atは生成時に文書参照を読み直した日時で、製品機能の検証日時ではない。90日を超えた場合も再生成する。

`python -m tools.workflow check` はsource集合・hash・edges・capsの内容と余剰caps、ローカル参照を照合し、不整合を失敗として返す。生成物を直接編集しない。

[読み方](../BIRDSEYE.md) / [RUNBOOK](../../RUNBOOK.md)
