---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 対象限定runの詳細仕様

[通常run](regression-ci-detail-spec.md)と[製品監督](supervised-run-detail-spec.md)を、GAH-R20/R27の既知変更へ接続する。対象は採択済みの固定UC-CI契約である。正本の要求・受入条件は変更しない。

## 入力と範囲

`run_prepare_scoped` はoperator専用で、通常のrun ID・契約series・契約完全参照に `changed_refs` を追加する。変更参照は完全参照の非空リスト、100件以内、重複不可。順序は正規化する。run IDは64文字以内。要求IDは `scope-prepare-<run_id>` に固定し、同一runの準備記録を一意に取得する。

対象と評価器の完全参照をControlへ対応させ、逆依存先とその前提Controlを含める。共有方針・registry・case set等、契約全体の参照が変わる場合は全対象へ広げる。未知のID・kind・digestが一つでもあれば `UNKNOWN_IMPACT_FULL_FALLBACK` として全対象を実行する。空の影響集合を合格扱いにしない。

返すscopeは直接影響・選択・未実施のControl ID、契約・registry完全参照、変更参照、拡大理由を持つ。`executed_scope` は `targeted` または `full`。scope自体はCI成功を発行しない。manifestのactor contextをscopeへ結び、選択範囲に対応するplan、candidate/baseline対、入力実体、baselineの対象参照を同時に絞る。

## 保存と再構成

既存authorityのidempotency transactionへ準備応答を保存する。再構成では要求hash、応答hash、OS認証から保存したoperatorのidentity/contextを照合する。保存された変更参照から範囲と全prepared内容を再生成し、準備応答全体と一致させる。scopeの自己申告や選択IDだけを採用しない。保存失敗では準備全体を巻き戻す。scope欠損・改変後に同runを全体runとして再開しない。

## 監督・CI・出力

`tools.gah_run` のtriggerが `change` のときだけ `changed_refs` を指定できる。未指定のchangeは既存の全体実行へ進む。通常run/resume/cancel/statusは同じOS主体・予約・停止観測・精算・Evidence・fresh CIの経路を使う。固定fixtureは選択義務ごとにcandidate/baselineを実行する。

CI要求は実行済みmanifestの完全参照と対象集合へ一致させる。限定結果を全体の要求へ提出した場合は成功にしない。監督結果、保存出力、JSON/Markdown要約は未実施Controlを明示する。限定結果はbaseline提案時と再検証時に `FULL_SCOPE_REQUIRED` で拒否する。全体へ拡大したrunも通常の採択条件を省略しない。

## 検証状態

範囲算出、未知変更、権限、準備記録の改変、保存失敗、通常実行・再開・レポート・CI・baseline流用拒否の[8試験](evidence/mvp-acceptance-20260913/targeted-summary.json)が105.098秒で成功した。対象不一致は既存のCI方針に従い終了1・CI_TARGET_MISMATCHとする。週次（月曜04:23 UTC）と手動起動のGitHub Actionsを追加した。固定imageをbuildして `verify_baseline_runtime --supervisor` からscheduled_fullの製品監督、限定2件、取消し回収を実行し、check.jsonをworkflowログへ残す。共有サービスやモデルAPIへは接続しない。このworkflowと実Dockerの限定run受入は未検証。結果は[継続レビュー](reviews/mvp-acceptance-20260913.md)へ記録する。
