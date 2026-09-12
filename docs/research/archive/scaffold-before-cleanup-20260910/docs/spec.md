---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
---

# 仕様の入口

[詳細要件](requirements.md) を規範とする。以下は実装着手時の確認導線であり、製品の実装完了を示さない。

## 入出力と判定

入力の識別・snapshot・scopeを固定し、観測Evidence、推定、未確認、矛盾を区別する。出力の契約と受入条件は [contracts](contracts/README.md) と要件定義に接続する。根拠が不足する項目はunknownとして残す。

## 互換性と失敗

未対応構文・取得失敗・予算超過・中断を成功と混同しない。部分結果には対象範囲と欠損理由を保持する。Schema変更時はバージョンと移行方針を同時に決める。

## セキュリティ

既定はsourceの読み取り。外部送信、実行観測、認証情報、出力先への書込みは要件定義の権限境界に従う。[SAC](security/SAC.md) は開発基盤の検証状況を管理する。

## この段階で実行できる操作

[RUNBOOK](../RUNBOOK.md) のgenerate/check/unittestのみ。CLIの解析やExplorerの動作、性能・精度は未検証。
