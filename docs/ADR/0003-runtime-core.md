---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# ADR 0003: 決定的コアと診断の実装

## 背景

利用者が詳細仕様以降の作成とDGX Qwen/Lunaを使う監督・コードレビューを指示した。製品コードがなかったため、[Task](../tasks/TASK.runtime-core-09-11-2026.md)で、外部実行に依存しない判定と永続台帳を先に実装する。

## 判断

Python 3.11以上の標準ライブラリとSQLiteを採用する。率はFraction、費用は元の十進値を保存して整数の桁演算で切り上げる。SQLiteのDDL/予約/精算をtransactionへまとめ、保存bytesのhashを再読込で照合する。reportは部品診断としてci_eligible=falseを固定する。

Lunaの担当ファイルとAPIを先に固定し、親がコードを読み、指摘を返し、別プロセス/実DBの試験を通す。Qwenは仕様とコードの局所レビューに利用し、既存規則の読み落としや根拠不足の回答を採用しない。

## 代替案と影響

外部サービスや追加DB製品を前提にすると実行境界の検証とコアの検証が結び付きすぎるため、最初はローカルSQLiteにする。汎用JSON Schema評価器を自作せず、公開Schemaと厳密な専用validatorを用いる。Schemaの説明だけで意味検査を代用しない。

診断CLIを通常CIの合格発行機能として公開すると、未認証の集計値で成功が出るため、この版は診断完了でも終了1を返す。OS認証・runner・校正・独立ケース・外部adapter・UseDecisionの接続は後続実装で受入する。

## 状態

コア実装の局所的な技術判断。全MVPの設計凍結ではない。[詳細仕様](../detail-spec.md)と検証記録に実装範囲・未接続・採用した版を記す。
