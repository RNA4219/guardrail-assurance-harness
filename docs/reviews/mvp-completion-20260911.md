---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 全MVPへの実装・監督記録

## 目標と現在地

[全体Task](../tasks/TASK.mvp-completion-09-11-2026.md)の目標は全MVP完成であり、部品の合格へ縮めない。[32要求の監査](../mvp-completion-audit.md)で不足と必要証拠を追跡する。以下は進捗であり、全MVP受入は未完了。

## 登録・ケース・証拠の接続準備

Lunaの担当をRegistryとCaseSetに分け、親は共通検査・ArtifactStore・独立統合試験を実装した。別のLunaが32要求の監査と保存部の読取りレビューを実施し、実装担当間でも相互レビューした。各担当の変更後に親がコードを確認し、追加の反例を検査した。

| 確認 | 修正・処遇 |
|---|---|
| Registryの再帰DFSが上限1000Controlの鎖に耐えない | 反復探索へ変更し、最大の正常鎖と循環を実行 |
| Registryの直接dict入力に総byte上限がない | canonical UTF-8の1 MiB上限を追加 |
| Schemaの制御文字・末尾改行の扱いがruntimeとずれる | textと参照patternを補正し負例を検査。横断制約はruntime必須と明示 |
| 比較集合どうしの用途重複が漏れる | 渡された全集合のpairを照合し、元の集合にない重複も報告 |
| ref ID・stage ID・カテゴリ等の改名で同内容を水増しできる | 初期状態と順序付き入力のkind+digestでsampleを識別。ラベル等の変更は条件差分として残す |
| 比較集合iteratorを無制限に消費する | 上限判定に必要な65件までに限定し、超過後を読まない試験を追加 |
| 保存初期化・clock失敗の例外本文が漏れる | STORAGE_FAILURE/CLOCK_UNAVAILABLEへ固定し、失敗経路を実行 |
| Evidence本体のreadに最新の失効状態が伴わない | 同transactionで現在のuse状態とci_eligible=falseを返す。撤回・削除後readを実行 |

保存部の2指摘は修正後に独立レビューLunaが再確認した。親の試験では許可表が拒否した合成markerがDBへ保存されないこと、削除途中の故障がpayload・墓標・失効世代をまとめてrollbackすること、再配送・再起動で時刻を更新しないこと、並行撤回が同じ世代へ収束することを確認した。これらは対象範囲のレビューと試験で、網羅的なセキュリティ監査ではない。

## DGX Qwenの寄与

今回の契約照会はqwen3.8-flash-nextで完了した（入力1,638、出力476、計2,114 token、25.093秒）。提出稿と回答のhashを[実行証跡](../evidence/mvp-completion-20260911/foundation-check.json)で照合した。コードの直接レビューとは数えない。

循環検出の欠落という提案は、提出稿に既に循環拒否があるためそのまま採用しなかった。実装では別のLunaが再帰上限の問題を見つけて修正した。Mutation対象外理由のnull/文字列の提案は既存のstatus連動規則を維持した。時刻基準の明示不足は補い、提案されたISO文字列への変更は採用せず、既存コアと整合するUTC Unix整数秒を明記した。

## 検証結果と次工程

Python 3.12.14 / SQLite 3.53.1 / Windows 11で112テストが成功した。前回80件を保持し、新規32件を追加した。要求・受入・初期方針/数値・100設計例のhashは維持した。[テストlog](../evidence/mvp-completion-20260911/foundation-unittest.log)と対象source hashを保存する。SchemaはJSON構文と境界patternを検査したが、汎用metaschema検証は未実施。

400件の実評価集合・独立oracle・管理AIの認証と採択・全オブジェクト・runner/adapter・実停止・全資源監督・CI利用照合・Finding/Planと再検証が引き続き必要である。Corpusの構造的件数充足や校正passedは、意味上の独立性・認証・実モデル性能の証拠ではない。Artifactのvalidは完全性・時刻・失効の範囲に限定する。

次の隔離実装に向けDocker Engineの版取得に成功した。sandbox内のnamed pipe接続は拒否されたが、読取りの権限付き実行ではLinux/amd64、Engine 29.0.1を確認できた。これはEngineの存在の証拠であり、隔離・停止・子処理制約の受入ではない。[基盤確認](../evidence/mvp-completion-20260911/isolation-probe.json)に記録し、次は固定fixtureの実行・隔離・停止を検証する。
