---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-09
next_review_due: 2026-10-09
template_version: 1.0.0
---

# Security

このrepoは現時点で製品runtimeを公開していない。支援対象は現在の文書とワークフロー検証ツール。
製品の信頼境界は [要件定義](docs/requirements.md)、開発上の確認は [SAC](docs/security/SAC.md) に記載する。

秘密値、実credential、個人データをIssueや検証fixtureへ含めない。
問題を発見した場合は機微な内容を公開Issueに記載せず、repo管理者RNA4219へ非公開の連絡方法を確認する。
GitHubのprivate vulnerability reportingを利用する場合は、repo公開時に有効化と連絡先を確認する。

修正は該当Task・検収記録・CHANGELOGに結び、公開済み版が生じた時点でsupported versionと更新方針を追記する。

