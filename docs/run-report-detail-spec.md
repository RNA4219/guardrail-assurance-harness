---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 通常runの人間向け要約とJSON

固定UC-CIの通常runと取消しrunを、同じ保存根拠から日本語MarkdownまたはJSONで表示する。
[通常run・CI](regression-ci-detail-spec.md)と[取消し](run-cancellation-detail-spec.md)の読取りAPIを使う。

```sh
python -m tools.gah_report --runtime .ga/existing-authority --request .ga/ci-request.json
python -m tools.gah_report --runtime .ga/existing-authority --request .ga/ci-request.json --format json
```

runtimeは既存のdeployment.jsonを含むrepo内ディレクトリ、requestは通常CIと同じ完全参照を持つ
ci_check JSONである。CLI自身はbrokerを起動しない。書込み可能な入力内容から実行先、保存先、
役割、追加コマンドを選ばない。operatorとして既存brokerへ照会する。

## 読取り順と整合性

1. run_outputsで必須成果物集合を取得し、kind・run・manifest参照と内容digestを照合する。
2. manifest、Decision、Evidence、Finding report、Plan report、receiptをrun_artifactで取得し、各参照と本文を照合する。
3. manifestの契約・baseline・対象・用途を要求の完全参照と照合する。
4. 最後にci_checkを呼び、最新応答の型・対象・終了値・成果物集合を検査する。
5. この一つのreportからMarkdownまたはJSONを生成し、出力・flushが成功した後に終了値を返す。

取得中に根拠が失効した場合は最後のCI照会結果を表示する。読取り済みのHEALTHYや過去の成功だけで
現在利用を可にしない。CANCELLEDとAssuranceは別項目である。Markdownの「CI照会のAssurance」、
「保存時のAssurance」、「現在のCI利用」を区別し、基準時刻の後も有効であり続けるとは表示しない。

## 出力

JSONはschema_version=1、kind=run_reportを持ち、run_id、対象範囲、基準/観測時刻、有効期限、
実行状態、Assurance、保存時Assurance、指標・比較可否、判定理由、現在CI理由、Finding/Planの完全参照、
成果物集合、保存根拠、ci_eligible、exit_codeを含む。

指標は保存Decisionの値を保持し、比較できる場合だけ現在値からbaseline値を引いた分数をdifferenceへ
追加する。片方が欠損ならdifference=null、comparison=NOT_COMPARABLEとする。0分母、不正型、
boolを整数として渡した値を正常な指標へ変換しない。

MarkdownはUTC日時と元のepoch秒、指標と差分の表、欠損/判定理由、Finding/Plan・対象・保存根拠の
参照表を表示する。文字列は引用・escapeして制御文字や表の区切りをそのまま出力しない。
入力・応答・参照・通信・出力の障害、対応できない成果物は終了2とし、固定のREPORT_UNAVAILABLEだけを返す。
有効な応答はCIと同じ終了0/1/2/3を返す。表示の完成を検査成功として扱わない。

## 検証範囲

[証跡](evidence/mvp-report-20260912/README.md)と[親レビュー](reviews/mvp-report-20260912.md)に、
専用7テストと実DBを使った製品CLIの15項目を記録する。全体回帰534件は直前の取消し工程の証拠であり、
その実行後に追加した要約7件は別の対象検証として区別する。全MVP受入は継続中である。

## 保存した件数の表示

Decisionの`aggregate_digest`に一致する同一runの集計を、既存の`run_artifact`または`candidate_artifact`で取得する。元の出力集合・Decision・Evidenceの照合を通した後で、そのDecisionが参照する集計だけを許す。別run・別digest・欠損・本文改変・削除済みEvidenceを拒否する。過去のreceiptと出力集合の保存形式・digestは変更しない。

JSONの`measurements`に元のcounts、用途/対象範囲/版ごとのcount_rows、issues、集計の完全参照を保持する。MarkdownにもTP/FP/TN/FN、検出判定欠損、ERROR、未確定、完了/予定段階、再試行、重複配信、Mutation分類を示す。約分した率から件数を逆算せず、段階・再試行・重複を独立標本へ読み替えない。取得不能を0件に補完しない。

[除外審査](mutation-review-detail-spec.md)は、前提が成立しMutationが未適用だった保存根拠を独立validatorが検証し、managerが承認する。元のERROR・必須欠損・Decisionは保持し、現在有効な除外、未確定除外、審査後に残るERRORを別計数する。表示だけでCI利用や全MVP受入を許可しない。

未回復のMutation ERRORは採点段階で一試行につき一度だけ計数する。段階ごとの総ERRORとは単位を区別し、正規の再試行で回復した試行にMutation ERRORを重ねて加算しない。元のfault_countとretry_countは保持する。

## 保存された予算警告

保存成果物を取得できる通常reportの`budget_warning`は保存Decisionの予算根拠を返す。根拠がない取消し・従来診断ではnullとし、
Markdownには「未取得」と表示する。警告がないことと未取得を混同しない。
取得済みなら閉鎖時の使用量・上限と閾値以上の軸を表示し、現在の資源値から過去の根拠を作り直さない。
WARNING単独はfreshなCI照会で終了0になり得るが、撤回・停止不明・未精算等の現在の拒否を打ち消さない。
算出と保存の契約は[authority仕様](assurance-authority-detail-spec.md)に従う。
