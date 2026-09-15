---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-13
next_review_due: 2026-10-13
---

# 後続世代・対象限定runのDB移行

既存の明示migrationへ、検証済みソースの後続世代版と対象限定版を追加する。[baseline更新](baseline-refresh-detail-spec.md)、[対象限定run](targeted-run-detail-spec.md)で保存した記録を維持するための互換移行であり、採択や新しいCI成功を発行しない。

## 対応する保存形式

| 既知版 | 対応範囲 |
|---|---|
| 後続世代版 | 契約2以降、baselineの期待世代から次世代への更新、旧・新候補、通常run、取消し |
| 対象限定版 | 上記に加え、run_prepare_scopedの準備済み・実行済み記録 |

各版のextension digestは保存した実ソースから算出した固定値であり、validator digestとの許可された組だけを受け付ける。後続版では既知の実runtime validatorと既存の検証用validatorを区別して保持する。任意の旧digestや未知の組を現行版へ書き換えない。従来版に後続契約・新しいbaseline・対象限定応答が混入した場合は、その版が生成できない形式として拒否する。

## 検査と保存

BEGIN IMMEDIATE内で列構成、canonical JSON/hash、提案・validation・採択履歴、currentの同世代行、前世代の存在を照合する。契約3以降と候補形式2は、それを生成できた既知版でだけ認める。通常run・取消し・全保存出力は元のmanifest、plan、入力実体、baseline contextへ再結合する。

対象限定の準備応答は、開始前でも検査する。idempotencyの主キー、応答の要求ID、run ID由来の固定要求IDを一致させ、operatorのidentity/context、要求hash、応答hash、導出範囲と全prepared内容を照合する。

baseline候補は保存されたexpected_generationで再生成する。旧版の固定1→2条件を新形式にも流用しない。限定runのbaseline昇格は移行でも拒否する。履歴の時刻と参照先を確認し、失効を解除せず、過去receiptの未確認flagを成功へ変更しない。

成功時に変更するのはadoption_configのextension_digestだけである。validator版、採択履歴、Evidence、Decision、clock、権限世代、未完了・取消し状態を保持する。読取検査はSAVEPOINT内で行い、既存Evidence・資源時計の一時更新もROLLBACKで破棄する。他の保存行変更は不正として拒否する。検査・更新失敗では同transactionを巻き戻す。現在のCI可否は移行後のfresh照会で判断する。

## 検証状態

既存と対象限定の31試験が145.324秒、対象限定版5試験と契約3・baseline3の統合1試験が1167.523秒で成功した。これらは旧版識別子を設定した合成SQLite fixtureである。別に、実Dockerの後続世代DBと対象限定DBを読み取り専用でコピーし、移行でextension digest以外の行・schemaが変わらないことを確認した。後続世代DBでは正規readerの保存時点照会も一致し、未完了CIを成功へ変えていない。資源時計の追加修正は回帰試験中。製品監督checkpointの版跨ぎ回収は未完了。

[継続レビュー](reviews/mvp-acceptance-20260913.md)と[完了監査](mvp-completion-audit.md)へ結果を追記する。
