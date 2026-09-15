---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

2026-09-15現在: [32条件のMVP技術検収](docs/mvp-completion-audit.md)は完了し、[最終証跡](docs/evidence/mvp-acceptance-20260913/mvp-final-20260915-summary.json)を正本とする。以下の工程別進捗にある未完了は当時の記録で、現在の判定は完了監査へ従う。設計凍結、人間の追加承認、GitHub上の実行・公開反映を意味しない。


日本語で記載する。

# GAHでの作業

製品入口は `python -m tools.gah_run`。[監督仕様](docs/supervised-run-detail-spec.md)と[親レビュー](docs/reviews/mvp-supervisor-20260912.md)に従う。固定contract 2の全30件を扱い、changeはfullへ拡大する。source lockを跨ぐ自動再開、対象限定、CIからの定期起動接続を実装済みにしない。[監督証跡](docs/evidence/mvp-supervisor-20260912/README.md)はDocker実行時とレビュー補正後の試験を区別する。

baseline更新は[詳細仕様](docs/baseline-refresh-detail-spec.md)に従う。baseline_useは最新参照、baseline_resolveは保存世代の現在利用を照合する。世代更新で既存契約の比較条件を変えない。

[取消し復帰](docs/run-recovery-detail-spec.md)ではresource_cancel_claimで期限切れownerの取得と取消しを原子的に確定する。通常の開始検査を緩めず、送信済み操作の停止・usageはvalidatorの観測を必要とする。[証跡](docs/evidence/mvp-recovery-20260912/README.md)を確認する。

[通常run要約](docs/run-report-detail-spec.md)は `python -m tools.gah_report` で出力する。最後のCI照会結果と保存時のAssuranceを区別する。[表示の証跡](docs/evidence/mvp-report-20260912/README.md)は全MVP受入を意味しない。

[通常run取消し](docs/run-cancellation-detail-spec.md)と[取消し工程の証跡](docs/evidence/mvp-cancellation-20260912/README.md)を確認する。停止と未精算を分離し、取消し確定後は現在CI終了3を返す。固定UC-CIの通常run取消しを接続し、534テストと実Docker219項目が成功した。従来523テスト・198項目を全保持し、既存90件と取消し確認用1件の計91件を実行した。停止未確認はCI終了2、停止済み取消しは3とし、後日精算・再起動・遅延結果で元の記録を変更しない。

- [通常run・CI](docs/regression-ci-detail-spec.md)で固定UC-CIの採択後30件、Finding/Plan保存・取得、freshなCI利用まで接続した。[最新証跡](docs/evidence/mvp-regression-20260912/README.md)と[親レビュー](docs/reviews/mvp-regression-20260912.md)を確認する。部品診断・過去receiptはCI成功へ昇格しない。条件変更を伴う契約・baseline更新、UC-LLMの認証・資源管理、CIからの実行監督接続、Findingの修復確認と全32要求の受入は継続中。

- 最初に[HUB](HUB.codex.md)、[GUARDRAILS](GUARDRAILS.md)、[Birdseye](docs/birdseye/index.json)を読む。
- 現在は契約・評価設計v0.3と[コア詳細仕様v1](docs/detail-spec.md)、判定・永続化・診断CLIの初期実装。[要求](docs/requirements.md)と[初期運用方針](docs/operating-policy.md)を正本とする。部品診断は常にci_eligible=false。設計例100件、部品試験、全MVP受入を区別し、未接続のrunner・認証・隔離を成功扱いしない。
- [実行監督](docs/execution-detail-spec.md)は固定した無害fixtureのみ。Dockerの実行検証は明示コマンドで行い、通常unittestから起動しない。全MVPへの未接続範囲は[完了監査](docs/mvp-completion-audit.md)に従う。
- [認証・方針採択](docs/auth-adoption-detail-spec.md)のbrokerはOS peer credentialで認証する。Qwenの限定提案を役割や権限の証拠にしない。採択・固定fixtureの接続検証と全MVP受入を区別する。
- [開始境界](docs/run-contract-detail-spec.md)で初回EvaluationContractと固定fixtureの資源予約・精算を接続した。owner_idは監督lease IDでOS identityとは別。400参照の輸送fixtureを実評価データに数えない。baseline更新と条件変更を伴う旧条件回帰、モデル資源接続、全MVP受入は未完了。
- [評価詳細仕様](docs/evaluation-detail-spec.md)と[配布pack](datasets/synthetic-policy-v1/README.md)で実入力400件・別用途校正/開発、集計、固定LLMの無害操作を実装する。診断runnerはOS authority/resource ledgerへ未接続でci_eligible=false。モデル名を重み版の証明にしない。Promptfooの伏せ字や不透明binding IDを認証として扱わない。
- [保存仕様](docs/run-evidence-detail-spec.md)の診断Decisionを[認証・資源と同DBへ接続](docs/assurance-authority-detail-spec.md)し、固定15件から初回baseline採択まで[実Dockerで検証](docs/evidence/mvp-adoption-20260911/README.md)した。条件差なしの契約gen2採択に続き、条件変更を伴う更新・[修復計画仕様](docs/remediation-detail-spec.md)の元runと新Evidenceへの接続を進める。形だけの参照やVALID自己申告をCI利用・VERIFIEDへ昇格しない。
- 設計・仕様・製品実装へ進む作業では[未決定事項](docs/open-questions.md)とTaskのscopeを先に確認する。
- Task Seedは[TASK.codex.md](TASK.codex.md)、技術検収は[EVALUATION](EVALUATION.md)に従う。
- 文書検証の成功、製品受入、人間の承認を区別する。未実行テスト・未実装CLI・外部設定を捏造しない。
- 独立した読み取りは一つのexec内で並行実行し、書き込み・Git変更・生成後の検査は依存順に行う。
- サブエージェントやモデルへの委任は利用者の指示がある場合に行う。モデル出力は下書きとして検証し、判定権限や実装事実を与えない。
- 文書・Task・検収を更新したら `python -m tools.workflow generate`、`python -m tools.workflow check` を実行する。
- archiveは通常編集しない。利用者が名称や参照情報の非掲載を明示した場合は、必要な匿名化を行い、匿名化後のhashと過去の検証の扱いを記録する。非匿名化版の新しいバックアップは残さない。移動時はrepo内の対象・保存先を解決し、保存先未存在を確認する。
- コピー元のレビュー・Acceptance・証跡はGAHの実績に含めない。[資料来歴](docs/research/README.md)で区別する。
- 外部参照元は利用者指定のクローズド資産。コード・Schema・テスト・文書・データを複製、移植、依存化、同梱しない。名前だけ変えた移植も行わず、一般的な運用の考え方だけを参考にGAHの要求から独立に設計する。[参照境界](docs/reference-boundary.md)を適用し、旧Taskやarchiveの転用方針を現行指示にしない。
- 非掲載指定の参照元の名称・略称・URL・版識別子を、本文、ファイル名、Task、証跡、保存稿、生成索引へ記載しない。検査結果も元の識別子を出力せず、残存件数だけを報告する。
- 外部Issue/PR作成、push、公開、repo設定変更は、その操作が利用者の依頼に含まれる場合に行う。
