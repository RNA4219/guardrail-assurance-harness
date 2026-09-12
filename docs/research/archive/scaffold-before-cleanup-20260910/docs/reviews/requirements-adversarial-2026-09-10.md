---
intent_id: INT-SR-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 要件定義の敵対的レビュー

## 1. 対象と判定の意味

対象は [要件定義](../requirements.md) v0.2。比較用に [変更前全文](../research/archive/requirements-before-adversarial-2026-09-10.md) をbyte単位で保存した。変更前SHA-256は `940c93624ec2c4ddb4f68b1798eac3490fb5a34e29382a7d37e229649d9fb67b`。

仕様書の執筆を停止し、要件に対して18の悪条件を検討した。10件で仕様化前に必要な判断規則・受入条件を補強し、8件は既存要件で明確に拒否できると判定した。10件の小さな有限モデルを実行して、不適切な判断を採用した場合の反例を確認した。

これは実装前の契約レビューである。製品の不具合・脆弱性を実証した結果ではなく、製品が安全・正確に動くことの検証でもない。上位原則が既に禁じている結果についても、適用順・状態遷移・評価器の条件が曖昧な箇所は補強対象にした。

## 2. 仕様化前に補強した10件

P1は誤った合格・公開・版選択・削除につながる判断規則の不足、P2は境界適用や資源制御を具体化する不足を示す。CVSSや実装上の脆弱性severityではない。旧版の行番号は保存した変更前全文を基準とする。

| ID | 優先度 | 元要件・位置 | 反例と不足 | v0.3の対応 |
|---|---|---|---|---|
| AR-01 | P1 | EVAL-01、精度表・Coverage（旧L1933–1956、2039–2041） | 同じ正解を99回と誤り1回出すと、単純集計は99%。gold1件へ複数TPを割り当てる条件が未定義 | EVAL-02: 意味単位、一対一照合、重複違反、TP/FP/FN対応表 |
| AR-02 | P1 | 評価dataset（旧L2112–2114） | held-outを分けても、goldファイルや過去のgold由来cacheを解析入力に含められる | EVAL-03: 入力とoracleの隔離、予測固定後の照合、汚染時の無効化 |
| AR-03 | P1 | RUN-01（旧L1259） | A開始→B選択→B完了→A完了。atomic保存だけでは表示中BをAへ戻す実装を排除できない | RUN-02: 保存と昇格を分離し選択世代・検索束を固定 |
| AR-04 | P1 | RETENTION-01（旧L1279） | A/Bが共有するEvidenceをA削除で回収するとBが壊れる。古いworkerからの再公開も未定義 | RETENTION-02: membershipと内容purgeを分離、削除進捗・再公開防止 |
| AR-05 | P1 | RUN-01と二重Secret Gate（旧L1259、1844） | v1 checkpointとrunは一致するが、途中でv1が失効している。過去cache再検査だけではin-flight公開を防げない | POLICY-01: 利用直前の有効性と失効世代を確認 |
| AR-06 | P2 | SECRET-GATE、SANITIZE-01、OUTPUT-01 | 一般原則は漏出を禁じるが、本文だけ先に秘匿化してpath由来ID/logを作る順序が明示されていない | METADATA-01: 本文外もID生成・inventory永続化より前に検査 |
| AR-07 | P2 | INPUT-01、SNAP-01（旧L1210、1239） | 別rootに同じrepository IDを指定するとdictionaryの後勝ちで一方を失う。識別衝突の拒否条件が不足 | IDENTITY-01: namespace/record/locator衝突と明示alias |
| AR-08 | P2 | ADAPTER-01、NFR-01 | 512MiB制限のworkerを32個起動すると合計16GiB。単体上限だけではhost予算を守れない | BUDGET-01: 全run/worker・候補生成を含む総量予算 |
| AR-09 | P1 | RUN-01（旧L1255–1257） | 10資産すべてUNSUPPORTEDでも会計率100%。coverage gateが実行時に何を要求するか未定義 | RUN-03: 事前completion policy、目的別の完了、全件未対応の扱い |
| AR-10 | P1 | SNAP-01、OUTPUT-01（旧L1234–1245、1269） | metadata/Evidenceを除いたexportの内容hashは元snapshotと異なり、claimから参照が切れる | OUTPUT-02: originとexport ID、projection契約、省略stubと検証状態 |

## 3. 反例モデルの実行

[runner](../../tools/review/requirements_adversarial.py) は実リポジトリのscan、外部通信、負荷投入、削除を実行しない。小さな数値・集合・イベント順序・JSONのモデルだけを扱う。

```sh
python tools/review/requirements_adversarial.py --output docs/evidence/requirements-adversarial-20260910/models.json
```

[実行結果](../evidence/requirements-adversarial-20260910/models.json): 10/10ケースの反例を再現。各caseの対策条件は例を拒否することを確認した。これらは要求する制御の例であり、実装がその制御を満たすことの証拠ではない。

- AR-01: naive precision 0.99、意味単位ではprecision 0.50 / recall 0.50、重複98件。
- AR-03: 2通りの完了順のうち1通りで、最後に完了したrunへの切替が選択を戻す。
- AR-04: 無条件回収では生存snapshot Bのshared-evidence参照が切れる。
- AR-08: 個別制限を守っても合計16,384MiBとなり、モデルのhost予算8,192MiBを超える。
- AR-10: policyで内容を変えるとhashが一致せず、Evidence省略だけではe1参照が未解決になる。

数値はこの反例モデルの条件であり、製品の性能測定・検出精度ではない。AR-06のmarkerは合成文字列で、本物のcredentialやsecret detectorの評価ではない。

## 4. 既存要件で拒否できた8件

| ID | 悪条件 | 拒否できる根拠 | 判定 |
|---|---|---|---|
| AR-11 | 実在するが無関係なEvidence IDを付けて確定claimにする | PROOF-01: 数量・条件等まで支持が必要 | 既存要件で拒否 |
| AR-12 | null / 0 / false / 未設定、retryの回数定義を同一視する | MODEL-01: 型・単位・値状態を区別 | 既存要件で拒否 |
| AR-13 | staging観測やproductionというファイル名を本番全体へ一般化 | ENV-01: deployment/actor/時刻/scopeを要求 | 既存要件で拒否 |
| AR-14 | scannerが失敗してもraw sourceを一般parserへ流す | SANITIZE-01と二重Secret Gate: 内容遮断、fail closed | 既存要件で拒否 |
| AR-15 | API 202やemit呼出から非同期処理の完了を推定 | FLOW-01: 受理・配送・完了を区別 | 既存要件で拒否 |
| AR-16 | すべてUNKNOWNでTraceability 100%を達成し製品精度に合格 | EVAL-01: 解答可能goldのUNKNOWNはrecall欠落 | 既存要件で拒否 |
| AR-17 | 読取中のsource変更を無視し、削除済みEvidenceを最新branchで代用 | INPUT-01 / Evidence契約: INPUT_CHANGED / SOURCE_UNAVAILABLE | 既存要件で拒否 |
| AR-18 | LLMの同意・高score・生成名称でFactや決定論的snapshotを更新 | NO-AUTHORITY-LLM / PROOF-01 / SNAP-01 | 既存要件で拒否 |

上記8件は文書上の判定であり、実装を実行して防御を確認したものではない。

## 5. 改訂後の整合確認

v0.3では10要件IDを追加し、追加契約の受入対応表へ1対1で接続した。M0/M1/M2の機能区分、既存の精度・性能閾値、目的と非目的は維持した。仕様書の具体化と製品実装は進めていない。

[要件・来歴の機械突合](../evidence/requirements-adversarial-20260910/review-check.json) の14項目と、[文書検証](../evidence/requirements-adversarial-20260910/workflow-check.json) の12項目が成功した。

追加規則同士も次を確認した。

- run完了、表示中版、公開可否、release判定を混同しない。
- policyの失効・削除状態をFactの暗黙書換として実装しない。
- snapshot削除後の共有参照維持と、内容purgeによる非公開化を区別する。
- exportの省略stubを支持Evidenceの代替にしない。
- inventory-onlyの完了をBehavior再構成完了にしない。
- 評価用oracleと、対象ソースに元からあるtest/docsを混同しない。

実装前に残る決定は、機械可読Schema、completion policyの具体的既定値、資源配分、保存・失効世代の方式、評価照合ruleと独立gold corpusである。要件が補強されたことと、これらの実装・実測が済んだことは別である。

## 6. 関連記録

[Task](../tasks/TASK.requirements-adversarial-09-10-2026.md) /
[Acceptance](../acceptance/AC-20260910-01.md) /
[資料来歴](../research/README.md) /
[CHANGELOG](../../CHANGELOG.md)
