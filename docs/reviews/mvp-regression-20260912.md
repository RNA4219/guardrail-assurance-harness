---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 通常run接続の親レビュー

利用者の「残りの未完了は自分で片付けて」に従い、親が実装・コードレビュー・検証を引き取った。外部モデルによる独立レビューを実施済みとはしない。

## 修正した問題

- 採択したgen2を通常runへ解決できなかったため、不変の比較contextと固定実入力を新runへ結んだ。候補purposeを通常CIへ転用しない。
- 必須成果物のFinding/Planが保存経路に無かったため、Decision/Evidenceと同じtransactionへ接続した。保存故障時のrollbackと再試行を確認した。
- Planの権限指定を構造化し、状態付きFindingと識別用投影の両方を保存して参照切れを修正した。obligation単位の理由を無関係なControlへ付けない。
- 古い履歴の全世代をcurrentと同一視していたmigrationを修正した。既知の採択版のみgen1履歴とgen2 currentを許し、旧版を装う新通常runは拒否する。
- CIの新しい成功経路を固定実装のfresh照合へ限定した。保存・実行の整合を確認してから現在の失効を判定し、通信成功や任意extensionの自己宣言で成功にしない。
- 試験用SQLiteのバックアップ接続を明示的に閉じ、Windowsの後片付け失敗を修正した。

## 検証結果

固定UC-CIの通常generation 2 run、Finding/Planの保存・取得、現在のCI利用を接続した。523テストと実Docker198項目が成功し、前工程の507テスト・150項目を保持した。初回15件・旧条件15件・新条件30件・通常30件の計90件を実行し、停止・精算・cleanupを確認した。

役割・型・ID衝突、否定観測、成果物参照の取得、保存故障、成果物欠落、再起動、撤回、明示移行、consumerの出力失敗を検査した。[証跡](../evidence/mvp-regression-20260912/README.md)に各実行とhashを保存する。

## 継続範囲

通常runの取消しterminal、条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、常設orchestrator、Findingの修復確認と全32要求の受入は継続中。

非掲載検査の旧読取り不能箇所はnative読取りで補完した。過去の未検査記録は消さず、今回の解消を別証跡にした。要求・初期閾値・予算の7正本は変更していない。
