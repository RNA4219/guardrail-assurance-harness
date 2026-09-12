---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 通常run・成果物・CI利用の検証

固定UC-CIの通常generation 2 run、Finding/Planの保存・取得、現在のCI利用を接続した。523テストと実Docker198項目が成功し、前工程の507テスト・150項目を保持した。初回15件・旧条件15件・新条件30件・通常30件の計90件を実行し、停止・精算・cleanupを確認した。

[統合結果](verification.json)、[全テスト](unittest.log)、[実Docker結果](runtime-check.json)、[仕様](../../regression-ci-detail-spec.md)、[親レビュー](../../reviews/mvp-regression-20260912.md)へ結ぶ。

通常runの必須成果物を同一transactionで保存し、再起動後も参照を照合した。現在のCIチェックではHEALTHY/WARNINGだけを許可し、元Evidenceの撤回後は同じrequestでも利用を拒否する。保存receiptと部品診断のci_eligible=falseは維持する。

検証対象は自作の無害な固定fixtureであり、対象モデル性能や全MVPの受入ではない。通常runの取消しterminal、条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、常設orchestrator、Findingの修復確認と全32要求の受入は継続中。 release_gate=no_go、全体のci_eligible=falseを維持する。

利用者の指示により親が実装・レビュー・検証を担当した。この工程で外部モデルの独立レビューは行っていない。先行テストの後片付け失敗と移行失敗はprior-target-testsの記録として保持する。

非掲載検査は[通常読取り](privacy-primary.json)に残った1か所を[Windowsの読取りによる31ファイルの補完](privacy-supplement.json)で確認した。対象テキスト・パスの残存0、合算読取りエラー0。ACLは変更せず、バイナリ除外と公開依存の分類を記録している。
