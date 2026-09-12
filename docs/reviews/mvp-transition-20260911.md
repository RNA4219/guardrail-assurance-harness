---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-11
next_review_due: 2026-10-11
---

# 契約移行の事前検査と監督レビュー

[初回採択の最終照合](../evidence/mvp-adoption-20260911/final-check-02.json)を終え、
[比較契約への移行仕様](../contract-transition-detail-spec.md)に沿って事前検査を追加した。
全MVPの目標は維持する。generation 2の候補実行・旧条件回帰・採択、baseline更新、
UC-LLMの認証と資源管理、Finding/Plan、通常CIの接続は残る。

## 分担と親レビュー

Lunaへ純粋な条件検査、同DBでの統合テスト、実Docker検証ツールの三つを分担した。
親はauthorityへの接続、完全参照とsource系列の照合、仕様、実行監督、結果の検収を担当した。
編集対象を分け、draftをそのまま採択せず、次の修正を反映した。

| 指摘・発見 | 処遇 |
|---|---|
| 既存validatorは条件差のない比較でもchanged_axesを空にできない | 空配列を許可した。比較必須のbaseline参照、対の実行、条件差の照合は維持した。要求や初期方針の変更はない |
| runの再bindingだけでは契約のRegistry/CaseSet/方針refとsource実体の一致が不足する | 旧契約自身の参照を実体から再計算し、再連結した不一致入力でも拒否する回帰テストを追加した |
| baselineを別の契約系列の同形payloadから参照できる可能性 | source runの保存契約系列・世代もproposal系列へ照合した |
| 一部draftテストは新契約をexpected_contract_refへ指定した | 旧現行契約への期待参照へ修正し、成功経路が到達してから負例を確認した |
| Docker receiptのローカル配列とdeepcopyの一致だけでは保存結果の不変性を証明できない | brokerへ同一evidence_finalize要求を再配送し、以前の不変receiptと完全一致を検査した |
| preflightのフラグとdict型だけでは空のtransitionを成功扱いできる | action別の応答kind、版、要求ID、旧新契約・baseline・sourceの完全参照、本文、内部フラグを照合した |
| readinessで不正JSONのContractErrorが監督の固定errorへ正規化されない | client応答のdecode失敗を固定errorへ変換し、最大3回のreadiness確認後にBROKER_NOT_READYとする。実decoderと全probe回収を通す単体試験を追加した |

Lunaの設計レビューにあった「旧runが最新eval_currentを読む」という指摘は、
現行コードが`eval_adoptions`から保存世代を解決しているため不採用とした。
新世代の採択だけを理由に旧runの履歴やreceiptを変更する処理も追加していない。

## DGX Qwenの処遇

自作の移行仕様と初回・採択契約だけを固定したモデル窓口へ送り、1回、4,487 tokenで
完了応答を得た。Qwenはpreflightが読むsourceと、後続の実行・採択時に再照合する条件の
対応を明記するよう提案した。両方を移行仕様へ反映した。提出文、返答、usageとdigestは
[今回の証跡](../evidence/mvp-transition-20260911/README.md)へ保存した。
この応答を実行、測定性能、主体認証、採択の証明には使っていない。

## 検証範囲

純粋な構造テストのDecision/Evidence/closureは参照用の入力であり、保存実体の存在を
証明しない。同DB統合は固定workerをプロセス内で実行して管理操作と結合し、
実Docker検証では別に15件の隔離実行・停止・精算・Evidence・初回baselineから
preflight、再起動、撤回後の同一要求の拒否まで確認する。

最終の件数、実行時ソースhash、image、保持した旧検査、過程の結果は
[検証manifest](../evidence/mvp-transition-20260911/verification.json)に記録する。
preflightは新しいrun、operation、採択validation、current pointerを作らない。
通常のgeneration 2 validationは、候補run未接続を理由に拒否したままである。

初回採択packetの追加照合では、Birdseyeの改行正規化済みhashとraw byte hashを
比較する検査側の誤りを訂正した。最初の失敗結果を保持し、正規化規則を合わせた
再照合で全7項目が通った。製品の採択結果や過去の実行ログは書き換えていない。
