---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# Workflow-Cookbook導入元

コピーした文書基盤の導入元記録: RNA4219/workflow-cookbook、version 1.2.0。記録されたcommit:
`db3b2141c1fef0f4835170dd82f85aabc6000fc3`。

採用基準は上流docs/adoption-tiers.mdのTier 3: Full、docs/adoption-guide.md、HUB.codex.mdと5つのtemplates。GAHで基盤を維持する運用判断は [ADR-0001](ADR/0001-workflow-cookbook-adoption.md)。

[upstream-lock.json](../governance/upstream-lock.json) に取り込んだ8検証器、5雛形、MIT licenseのpathとSHA-256を保存する。hashはUTF-8/LFへ正規化して計算するためGitの改行変換で変化しない。取り込んだソースの著作権・SPDXを保持する。本体のlicenseは未決定。

## ローカル適用

[workflow.py](../tools/workflow.py) は独自の文書索引・Birdseye生成と統合check。上流generatorそのものではない。上流の検証器も併用する。branch protectionのlogical ID対応はdocs-gateを含むこのrepoのmappingを渡す。

上流freshness checkerが提案するtools.codemap.updateコマンドはここでは `python -m tools.workflow generate` に読み替える。

## 更新

必要になった時点で上流差分を確認し、Taskに影響範囲を記録する。雛形変更は既存文書へ反映してtemplate_versionを更新する。取り込み対象とlockを同時に変更し、全検査と回帰テストを実行する。隣接repoを参照することはローカル運用の必須条件ではない。

GAHへは別案件の構成を経由してコピーされた。上記は引き継いだ来歴であり、今回の上流再取得や新たな導入完了の主張ではない。[資料来歴](research/README.md)を参照する。
