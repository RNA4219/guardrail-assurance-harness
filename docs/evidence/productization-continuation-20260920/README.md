---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-20
next_review_due: 2026-10-20
---

# 通常run 800件の継続試験

採択済み候補checkpointから通常run 800件を実行し、保存・再開・status・出力refの不変性、current CI照会、baseline世代2の採択とcurrent世代照合まで確認した。テストは1件、失敗・error・skipは0件、所要7,359.23秒。

機械可読結果、milestone、試験対象sourceのfile hash manifest、限定driver、全ログを同梱する。入力checkpointのSHA-256は結果JSONに記録した。source manifest SHA-256は 97d77c054cae3a0b791f9fd2b3c3dacd3f4afedc273edba61f02460df89638e8、driver SHA-256は 0257ca456bb207eb356473057e9cd7ba0f1bffbbe8c795f039e238c8c03ff2d4、log SHA-256は e5a015f130023a977dec3a9739e64e3c99733423dcf22ed788893ed64b69c1ea。試験中sourceの変更は0件。

workerは固定in-process workerである。製品CLIの別process起動、実Docker worker、全子resource counter、性能SLO、実案件データを検証していない。この記録は通常runの一経路を通した実行結果であり、GAH-PAC07や拡張14要件の製品受入状態を変更しない。

[機械可読結果](normal-800-resume-v1.json) / [milestone](milestones.json) / [source manifest](source-manifest.json) / [test driver](resume-normal-800.py) / [実行ログ](tests.log)