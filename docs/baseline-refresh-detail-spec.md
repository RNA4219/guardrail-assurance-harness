---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 通常runに基づくbaseline更新

固定UC-CIの採択済み契約による完全な通常runを、baseline generation nからn+1への更新候補にする。nは1以上で、元runが実際に比較した保存基準の世代と一致しなければならない。既存の要求・初期閾値・予算は維持する。契約が固定したbaselineは更新後も同じ完全参照で照合し、current pointerの移動によって既存CIの比較条件を変えない。

## 入力と役割

| 操作 | OS主体 | 入力と意味 |
|---|---|---|
| baseline_propose | manager | proposal_id、series_id、run_id、expected_generation=n。完全な通常runから候補を生成 |
| baseline_validate | validator | proposal_id、validation_id。現在有効なsourceと提案内容を再照合 |
| baseline_adopt | manager | proposal_id、validation_id、expected_generation=n。提案者・世代・有効性を再照合して採択 |
| baseline_current | manager / validator / operator | series_idの最新世代と現在有効性を取得 |
| baseline_use | manager / validator / operator | 最新世代とexpected_baseline_ref、expected_contract_refが完全一致するときに現在利用を確認 |
| baseline_resolve | manager / validator / operator | 指定した保存世代を完全参照で解決し、その根拠の現在有効性を確認 |
| baseline_revoke_ref | operator | 完全なbaseline参照と対応契約参照で、撤回する保存世代を指定 |

全操作にschema_version=1、action、request_idが必要である。基準更新へ任意のrecordや役割の自己申告を渡せない。expected_generationのbool、未知field、不正な参照は拒否する。baseline_current、baseline_use、baseline_resolveは同じrequest_idでも現在状態を再検査する。既存baseline_revokeは最新世代の撤回として維持する。

## 採択の根拠と原子性

sourceはpurpose=regressionの保存された正常terminalに限り、contract generation 2以上、全30入力の充足、現在利用できるDecision/Evidence、停止・精算、固定実装とbindingを確認する。取消しや違反・不足を新しい正常基準へ昇格させない。validationと採択は同じ元runを再検査し、古いvalidationだけで有効性を得ない。

比較contextはsourceが実際に使ったbaseline generation nと、その契約の実世代を参照する。契約の世代を基準の世代から推定しない。元runの作成が前baseline採択より前なら拒否する。baselineのvalid_untilは元Evidenceから生成し、採択によってEvidenceの寿命を延長しない。

提案・検証・採択の全てで、更新前のcurrent本文・digest・世代・採択履歴を照合する。currentが破損している場合に正常な更新で上書きして隠さず、STORAGE_CORRUPTで拒否する。

generation n+1の履歴追加、currentのgeneration nからn+1への条件付き更新、冪等応答を同じtransactionで保存する。競合・保存失敗では全てrollbackし、期待世代が古い別候補を採択しない。元Decision、Evidence、Finding、Plan、receiptは変更しない。

## 固定参照と依存失効

矢印は根拠から利用先を表す。新しいbaselineを採択しても、既存契約の矢印を付け替えない。

```mermaid
flowchart LR
  B1[baseline 1] --> C2[contract 2]
  C2 --> R2[完全な通常run]
  R2 --> B2[baseline 2]
  B2 --> C3[contract 3]
  C3 --> R3[完全な通常run]
  R3 --> B3[baseline 3]
```

既存contract generation 2はbaseline generation 1を固定している。この契約の通常runを開始・利用するときだけ、保存世代をbaseline_resolveで確認する。最新pointerを過去条件にすり替えない。新しい基準が採択されても、既存runのmanifest、比較対象、現在のCI問い合わせ条件は変わらない。

baseline generation 2を単独で撤回しても、基準1だけに依存する既存runのCIは利用できる。基準1または元Evidenceが撤回・失効したときは、それに依存する通常runと基準2の現在利用も拒否する。過去の健康な判定は履歴として保持する。破損・欠落・不明な撤回世代を現在の合格に読み替えない。

## 移行と限定

既知の取消し復帰版からは明示migrationで移行する。旧版に存在しない更新形式を持つ保存DBは拒否し、許可する既知履歴を検査してからextension digestを変更する。自動migrationは行わない。

世代の検査・採択は期待世代と保存履歴に追随する。実SQLiteではbaseline 1→2→3、contract 3までを検証し、条件差なしの実Docker検証を継続している。世代3だけの撤回では基準2を使う既存CIを変えず、基準2の撤回では依存する契約3と基準3の利用を拒否する。対象や条件を変える更新、UC-LLM、後続版の明示migrationは[完了監査](mvp-completion-audit.md)の残件である。

## 検証入口

```sh
python -m unittest tests.test_baseline_refresh tests.test_following_contract_integration -v
python -m tools.prepare_authority_runtime
python -m tools.verify_baseline_runtime --baseline-refresh --output .ga/baseline-refresh-check
```

出力先は未存在のrepo内directoryを指定する。後二つはローカルDockerが必要であり、固定・無害なfixtureだけを使う。--baseline-refreshは--recovery以下の経路を含む。追加のモデル送信はなく、通常runを更新候補に使う。検証末尾は元baselineへの照会をbaseline_resolve、撤回をbaseline_revoke_refへ切り替え、固定参照の意味を保つ。

[親レビュー](reviews/mvp-baseline-refresh-20260912.md)と[工程証跡](evidence/mvp-baseline-refresh-20260912/README.md)に実行結果を記録する。

契約3と基準3までの実環境検証は、`python -m tools.verify_baseline_runtime --following-contract --output .ga/following-baseline-check`を使用する。[継続レビュー](reviews/mvp-acceptance-20260913.md)に未完了の検証も含めて記録する。
