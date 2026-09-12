---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 管理主体の認証・方針採択の監督記録

[全MVP Task](../tasks/TASK.mvp-completion-09-11-2026.md)の途中工程として、[管理境界の詳細仕様](../auth-adoption-detail-spec.md)を実装した。PolicyProfileに範囲を固定し、EvaluationContract/baselineや全資源台帳の接続まで完了したことにはしない。

## 担当と処遇

| 担当 | 作業と指摘 | 処遇 |
|---|---|---|
| Luna / decision | 初期PolicyProfileと境界検査、配置監督のレビュー・修正 | 初期値の弱化を拒否。親が見つけたglobal window短縮を固定86400秒へ修正。tmpfs/volume、開始/再起動前後、所有対象、削除後不存在、platformを検査 |
| Luna / cli review | AdoptionStore、並行採択、receipt、管理接続のレビュー | 親の指摘に従いcurrentのキャッシュ利用を廃止。receiptのcanonical/digest破損、同じ世代への競合を試験。モデル入力token上限を親が追加 |
| Luna / ledger | 設計レビュー、authorityのframing/例外試験、管理モデル応答の検査 | socket peerを認証根拠にし、提案・検証・権限・失効を同じDBで照合。短いpeer credentialと想定外例外を固定reasonへ閉じる試験を追加 |
| 親 | socket認証、固定image、実Docker検証、モデル送信、全体レビュー | image内容とsourceを固定。実測とmockの不一致、EOF/half-closeの仕様漏れを修正。Qwenの出力は限定データとして検査してから別UIDの処理へ渡す |
| DGX Qwen | 自作の管理詳細仕様の限定レビュー1回 | 55秒で時間切れ、応答なし。レビュー成功や承認へ換算しない。提出稿hashと失敗receiptを保存 |
| DGX Qwen | 独立messagesによる限定管理提案 | 受信互換修正後の試験で120秒維持・keep_initialを提案。入力135、出力24、合計159 token。独立validatorの決定的検査後に世代1へ採択 |

管理モデル試験の初回は提案受理前に失敗した。当時は固定された詳細理由を保存しておらず、原因を断定する証拠はない。接続先の公開OpenAPIで`UsageInfo`の補足2項目を確認し、受理後に破棄する処理と試験を追加した。修正後の新規試験は成功した。各起動は1要求・内部再試行なしで、旧失敗を上書きしない。管理試験は合計2回の起動であり、仕様レビューの時間切れとは別に数える。

## 実測で修正した点

- 補助group配列はprimary GID自身を含んでいた。数値を証跡へ残し、自身以外のgroupを拒否する検査にした。
- Docker 29.0.1のlegacy tmpfsは`Mounts`に現れず、`HostConfig.Tmpfs`へ記録される。実測に合わせて各表を完全照合した。
- network noneでも`NetworkSettings.Networks`に`none`が現れ、開始時に内部IDが割り当てられる。IP/Gateway/MACや追加networkは許容しない。
- 空のimage Cmdはinspectで省略される。空/未存在を許容し、非空の起動引数を拒否する。
- 同じcurrent requestの再配送でも期限と失効を再評価する。採択receiptは過去の不変履歴として返し、現在の有効性とは分ける。
- モデルusageは入力32768・出力128以下と加算整合性を検査する。公開Schemaの補足値を採択判断や費用へ使わない。

## 検証と残る工程

[認証・採択22項目](../evidence/mvp-authority-20260911/runtime-check.json)と[実Qwen提案・採択7項目](../evidence/mvp-authority-20260911/management-check.json)が成功した。固定imageは`sha256:24aa4a726566692c8627b328f74a0c5aeaa76b9e3c7e85f9178d2e9803bed308`。候補/管理/検証/運用のUID/GID、自己申告拒否、DB/socket保護、自己検証拒否、弱化拒否、再起動、不変receipt、失効後の再配送、停止削除を確認した。状態volumeは合成DBの調査用として保持し、所有containerは回収した。

[統合検証](../evidence/mvp-authority-20260911/verification.json)では203テストが成功し、以前の151件をすべて保持した。追加52件は部品の境界試験であり、52要求が完了したという意味ではない。要求・初期方針・100設計例、実行対象sourceのhashも一致した。その後、仕様のvalidate説明を実装どおり「初期値境界への再照合」と明確化し、旧評価契約による回帰との混同を除いた。文書改訂と実行ソース不変の照合は[最終確認](../evidence/mvp-authority-20260911/final-check.json)へ記録する。

単体試験はDockerや外部モデルを起動しない。全MVPのreleaseはno_go、ci_eligible=falseを維持する。次は採択済みの全評価契約、RunManifest/TrialPlan、runner/資源台帳、校正・評価器、Finding/CIを接続する。
