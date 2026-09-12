---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
template_version: 1.0.0
---

# Security

このrepoは開発途中のruntime、診断部品、文書・検証ツールを公開している。現在のmainを調査・修正対象とするが、安定版・全MVPの受入済み版はまだない。過去の証跡は記録した版と条件に対する結果として扱う。
製品の信頼境界は [要求草案](docs/requirements.md)、開発上の確認は [SAC](docs/security/SAC.md) に記載する。

秘密値、実credential、個人データをIssueや検証fixtureへ含めない。
問題を発見した場合は機微な内容を公開Issueに記載せず、repo管理者RNA4219へ非公開の連絡方法を確認する。
GitHubの[非公開報告フォーム](https://github.com/RNA4219/guardrail-assurance-harness/security/advisories/new)を公開時に有効化する。利用できない場合は機微な詳細を書かず、管理者へ非公開の連絡方法を確認する。

修正は該当Task・検収記録・CHANGELOGに結び、公開済み版が生じた時点でsupported versionと更新方針を追記する。

