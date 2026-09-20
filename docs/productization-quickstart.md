---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-15
next_review_due: 2026-10-15
---

# 固定sampleの導入手順

repo rootで実行する。Python 3.11以上、対応するDocker Linux engine、固定lockに対応するimageが必要。固定imageの新規取得・構築には `python -m tools.gah_images prepare --destination <新規配布先>` を使い、完了後に配布先へ移動する。公開済み製品imageの配信は未実施で、新規OSでの5コマンド/30分受入は未完了。[実装Task](tasks/TASK.productization-implementation-09-15-2026.md)に検証範囲を記録する。既存環境の初期化や時計設定の変更は不要で、不成立時には理由を返して停止する。

現在のCLIでは固定sampleの書込み上限が未実証のため、doctorのcapacityはUNKNOWNとなり、setup applyは開始前に停止する。以下は完成時の操作順序を示す。容量checkの省略や成功値の注入を導入手順にしない。

最初に用途を一つ選ぶ。`sample-ci`は制約・検査系の固定fixture、`sample-llm`は自作合成400ケースの固定ガードレールを使う。有料API・秘密・外部モデルは使用しない。実案件の評価対象と混同しない。

```sh
python -m tools.gah_ops doctor --workspace . --phase bootstrap --profile sample-ci --json
python -m tools.gah_ops setup preview --workspace . --profile sample-ci --output .ga/sample-plan.json
python -m tools.gah_ops setup apply --workspace . --plan .ga/sample-plan.json
python -m tools.gah_run run --setup-plan .ga/sample-plan.json
python -m tools.gah_report --setup-plan .ga/sample-plan.json --format markdown
```

LLMを選ぶ場合は1・2本目のprofileを`sample-llm`へ変える。run/reportは完了setupから保存済み要求と固定runnerを解決し、JSON内部のpathを手入力しない。同じworkspaceで別用途も始める場合は別のplan出力名を使う。明示runtime/requestとsetup-planの同時指定は拒否する。

各行の終了値を確認して次へ進む。doctorは120秒の時計観測を含む。setup applyは分離されたmanager/validator/operatorの認証でpolicy・初回契約・baseline・比較gen2を準備する。CIは初回15・候補旧15/新30件、LLMは初回400・候補旧400/新800件を実行し、部分集合を全件完了へ換算しない。setupの終了0は準備完了を表し、製品CIの合格ではない。

4本目は通常評価を実行し、5本目は結果を読み終えてから現在CIを再照会する。保存当時のHEALTHYと現在利用可否を別に表示する。JSON表示が必要なら5本目を`--format json`へ変える。

setup applyの同じplan/requestは、確定済みの処理を同じIDで回収する。応答不明や出力途中を削除して新規runへ置き換えない。通常runの中断は同じsetup-planで`gah_run resume`、停止要求は`gah_run cancel`を使う。古いsourceのplanを無理に再利用せず、stale時は残る証拠と未精算を確認する。

管理AIが固定計画と通常採択を扱い、独立validatorが内容と根拠を検査する。候補AIの説明・自己申告は認証や採択根拠にならない。実案件への接続はpilot binding/planの別経路で、対象選定・出所のimport・独立oracleの確認が揃うまで受入へ進めない。

固定sampleの予算推定はCIで約7GiB、LLMで約227GiB。実消費量や強制された上限ではない。予算推定だけではcapacityをPASSにせず、書込み量の積算・強制上限・Docker保存先の空きと予約を検証するまでUNKNOWNを維持する。


image準備は元のcheckoutを変更しない。baseを取得済みの環境では `--offline` を指定できる。容量診断のUNKNOWNと全導入受入の未完了は、この準備が成功しても維持する。
