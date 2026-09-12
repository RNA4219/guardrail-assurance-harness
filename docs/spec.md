---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 基本仕様と詳細仕様の構成

通常runの人間向け/機械向け表示は[要約仕様](run-report-detail-spec.md)と[検証記録](evidence/mvp-report-20260912/README.md)へ結ぶ。

通常runの停止済み取消しは[取消し仕様](run-cancellation-detail-spec.md)へ接続した。停止確認・未精算・不変成果物・CI終了3の[実証](evidence/mvp-cancellation-20260912/README.md)を参照する。

要求の振る舞いを[評価契約v0.3](contracts/evaluation-contract.md)へ具体化し、[詳細仕様v1](detail-spec.md)の範囲で判定・SQLite・診断CLIを実装した。全MVPのwire Schema・実行監督・管理AI認証・外部接続は未完成で、全体の設計凍結は行っていない。要求と数値は[要求v0.4](requirements.md)・[初期運用方針v1](operating-policy.md)が正本。

| 論点 | 現在の定義 |
|---|---|
| 論理構成・信頼起点・採択 | [設計](design.md) |
| 部品API・入力Schema・保存・費用・診断CLI | [コア詳細仕様v1](detail-spec.md)。部品実装あり、通常CIの合格には使わない |
| 台帳v2・回収・取消し・終了確定 | [状態管理の詳細仕様](lifecycle-detail-spec.md)。元deadlineと終了記録を保持する |
| adapter・管理AIとの接続 | [adapter接続仕様](adapter-spec.md)。Promptfoo単一行mappingを実装し、認証された評価runへの接続は継続中 |
| 実評価集合・段階実行・集計 | [評価詳細仕様](evaluation-detail-spec.md)。受入400件と校正/開発、固定LLMの無害操作、scope別指標と欠損の扱い |
| 保存Attempt・診断Decision・現在状態 | [保存詳細仕様](run-evidence-detail-spec.md)と[同DBの認証接続](assurance-authority-detail-spec.md)。不変保存・精算・破損/失効照合と固定UC-CIの実接続 |
| Finding・定型計画・再検証候補 | [修復計画の詳細仕様](remediation-detail-spec.md)。計画の不足と修復完了を分け、authorityなしのVERIFIEDを拒否 |
| baseline採択・更新契約の次工程 | [採択更新の詳細仕様](baseline-adoption-detail-spec.md)と[比較契約への移行](contract-transition-detail-spec.md)。固定UC-CIの初回採択、旧条件回帰、候補runとgen2採択を接続。条件変更を伴う更新は継続中 |
| 通常run・必須成果物・現在のCI利用 | [通常run・CI仕様](regression-ci-detail-spec.md)。固定UC-CIの30件、Finding/Plan保存・取得、fresh gateを接続 |
| 固定入力・oracle・初期状態の実体 | [materialization仕様](fixture-materialization-detail-spec.md)。15entryの実体と36vector測定校正を同DBへ結ぶ |
| 固定fixture・実行・隔離・停止回収 | [実行監督の詳細仕様](execution-detail-spec.md)。Docker固定fixture、generic正規化、送信journalを実装 |
| OS認証・管理方針の採択 | [認証・方針採択の詳細仕様](auth-adoption-detail-spec.md)。固定UID、PolicyProfile、世代・失効、Qwen限定提案を実装 |
| 初回評価契約・実行計画・資源管理 | [開始境界の詳細仕様](run-contract-detail-spec.md)。原子的run開始、固定fixture予約/実行/精算を接続。baseline更新・モデル送信は残る |
| Registry・CaseSet・Evidenceの構造と保存 | [実行契約の詳細](runtime-contract-spec.md)。採択・実行全体への接続を区別する |
| オブジェクト、binding、結果採用 | [評価契約](contracts/evaluation-contract.md) §1〜§3 |
| 指標・比較、予算・時刻 | 同契約 §4〜§5、[初期値JSON](contracts/initial-policy.v1.json) |
| Assurance・終了コード・Finding | 同契約 §6〜§7 |
| 初回・採択・再開・データ境界・同時障害 | [補足契約](contracts/lifecycle-contract.md) LC01〜LC06 |
| ケース、oracle、32要求との対応 | [評価設計](contracts/evaluation-design.md) |
| 境界の期待値 | [算術例](contracts/decision-examples.v1.json)、[v0.2追加例](contracts/review-examples.v2.json)、[v0.3状態例](contracts/state-examples.v3.json)の計100例。製品試験は未実行 |

Manifestの固定設定と観測結果を分離する。正式Schema作成時には必須field、enum、追加field、版互換性、digest対象と正規化、YAML計画の構造検査を確定する。実行可能な開発コマンドは[RUNBOOK](../RUNBOOK.md)に記載する。

## セキュリティ

runner分離・管理identity・実行中の境界強制は設計上の必須条件。実環境で方式を検証するまでは外部実行を有効にしない。部品試験の実行範囲は[監督レビュー](reviews/runtime-core-20260911.md)、開発基盤は[SAC](security/SAC.md)、残事項は[判断事項](open-questions.md)で追跡する。
