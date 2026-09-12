---
intent_id: INT-GAH-001
owner: RNA4219
status: draft
last_reviewed_at: 2026-09-12
next_review_due: 2026-10-12
---

# 管理主体の認証と方針採択の詳細仕様

[全MVP Task](tasks/TASK.mvp-completion-09-11-2026.md)の管理境界を具体化する。[LC01/LC02](contracts/lifecycle-contract.md)と[運用方針](operating-policy.md)を正本とし、候補が自分の基準を採択する経路を作らない。第一段階はPolicyProfileの提案・決定的検証・採択を接続する。EvaluationContract、baseline、実評価証拠の採択は後続の同じ管理境界へ接続し、この方針採択を通常CI成功へ使わない。

## 1. 配置と認証

Linux Docker上のbrokerはUID/GID 12000、managerは12001、candidateは12002、validatorは12003、operatorは12004で動く。全clientはcap-drop ALL、no-new-privileges、non-root、read-only root、network none、private namespace、host mountなしで動かす。UID/GIDとimageを固定した監督だけがclientを起動する。host/Docker管理者は信頼起点である。

共有するvolumeはsocket専用のものだけ。broker所有のディレクトリは0755、socketは0666とし、clientにはvolumeをread-onlyでmountする。DB用volumeと他clientの作業領域は共有しない。bootstrap方針は全主体で同じ固定imageに含め、書換えを許さない。socketの置換・削除、DBの直接読取り、setuidによる他主体への移行をclientへ認めない。所属groupは自身の固定GIDだけを許容し、補助group配列にprimary GIDが現れる場合も数値を記録する。

brokerはacceptしたAF_UNIX/SOCK_STREAM接続からSO_PEERCREDを取得し、kernel由来のUID/GIDを配置時の固定表へ照合する。本文のactor/context/role/uidは受け付けない。actorとcontextは固定UIDごとに一対一で管理する。managerへのstdinは提案データの受渡しであり、brokerの認証根拠にしない。SO_PEERCREDが返す接続確立時のpeer credentialを使う。別contextを同じUIDで多重利用しない。根拠は[Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)のSO_PEERCREDおよびpathname socketの権限仕様による。

恒常的な管理AIという論理主体に対し、個々のモデル照会は候補と共有しない独立したmessagesから作る。modelの出力は限定形状のデータとして検査し、採択権限はモデル本文から作らない。実モデル提案の意味、決定的な検査結果、OS認証を別々に記録する。

## 2. 方針とBootstrap

config/bootstrap-policy.v1.jsonは既存の初期値をruntime PolicyProfileへ具体化する。schema_version=1、kind=policy_profileとし、設計資料のartifact_kind/status/sourceだけを除く。初期閾値・予算・最低件数・CI成功状態・管理規則は元の値を保つ。

初期方針より厳しい閾値・小さい予算は許容し、弱い閾値・最低件数削減・鮮度延長・Criticalの弱化・義務削除・母集団推定有効化を拒否する。有限集合・分数の比較は整数で行う。broker imageへbootstrapとvalidatorを固定し、配置側がimage IDを照合する。内容digest単独を配布主体の認証とせず、hostによる配置と読取り専用rootを信頼起点へ含める。

方針採択の世代0は、配置されたbootstrapが存在する場合に限る。既存世代から0へ戻らない。初期PolicyProfile採択は通常回帰の契約・baseline採択ではなく、初回校正等に使う前段である。評価契約・校正・baselineがない状態で通常CIを成功にしない。

## 3. APIと不変入力

ソケットは4-byte big-endian長さprefixとUTF-8 JSONで1要求/1応答とする。要求は最大1 MiB、応答も最大1 MiB。broker側の要求読取りdeadlineはフレーム全体で5秒、client側の処理結果待ちは30秒とする。固定Docker clientを監督する外側の待機上限は45秒とする。送信側はフレーム直後にSHUT_WRで書込みをhalf-closeし、受信側はEOFを確認して余分な末尾データを拒否する。half-closeしない相手は期限で拒否する。重複key、小数、BOM、未知field、不正型は拒否する。ログへraw、抜粋、拒否データのdigestを出さず、固定reasonだけを返す。APIの意味は[Python socket公式資料](https://docs.python.org/3.12/library/socket.html)に従う。

応答を取得できなかったことだけで、保存処理がrollbackされたとは判定しない。採択の結果が不明な場合は同一要求IDの再配送と現在状態の照会で確認し、新しい要求IDによる二重適用を避ける。

PolicyProfile APIは次の固定形を使う。全要求はschema_version=1、action、request_idを持つ。actor/contextはtransportからだけ取得する。

| action | 追加field | 許可主体 | 動作 |
|---|---|---|---|
| propose | proposal_id、series_id、expected_generation、policy | manager | 厳格検査済み方針と提案digest、提案actor/context、元世代を不変保存 |
| validate | proposal_id、validation_id | validator | broker内の固定検査で新方針を初期値境界へ再照合し、検証記録を保存。passed等の自己申告を受けない。旧評価契約による回帰評価は後続のEvaluationContract採択で接続 |
| adopt | proposal_id、validation_id、expected_generation | manager | 同じtransactionで現在の権限・世代・提案・検証・失効/期限を再検査し採択 |
| current | series_id | manager/validator/operator | 現在の採択と有効性を返す。元receiptと現在の状態を区別 |
| receipt | adoption_request_id | manager/validator/operator | 閲覧権限を再確認して過去の不変receiptを返す |
| revoke_actor | actor_id | operator | actorの権限を不可逆に失効させ、権限世代を更新 |
| revoke_validation | validation_id | operator | 検証記録の失効世代を更新し、依存する採択の現在利用を不可にする |

candidateと未知UIDは全操作を拒否する。managerはvalidator/operator権限を持たず、validatorは提案/採択権限を持たない。operatorは採択できない。AIの文字列から権限を増やさない。

## 4. 原子的な採択と履歴

actor権限、方針提案、固定検証、現行採択、失効、再配送、監査eventは同一のbroker SQLiteへ保存する。BEGIN IMMEDIATEの一つの確定点で検査と世代更新を行い、外部DBの古いキャッシュを混ぜない。時刻はUTC Unix整数秒とし、永続的な最終時刻を下回ったら採択拒否。validationは観測時刻から最大86400秒で期限切れ、期限一致も不可とする。

proposalはpolicy canonical bytesからbrokerがdigestを計算し、validationはproposal_digest、旧世代、bootstrap digest、validator版digest、作成actor/context、時刻・期限・失効状態へ結ぶ。採択時は提案者と採択者のmanager contextも一致させる。validator権限が失効した検証を新しい採択へ使わない。

expected_generationは現行世代と一致し、proposalに固定された世代とも一致する。初回だけ現行なしを0とする。並行採択は一方だけ成功し、他方はGENERATION_CONFLICT。権限取消し、失効、期限切れ、保存障害では現行参照を変更しない。

request_idはactor/contextと要求全体のcanonical digestに結ぶ。同じ変更要求の再配送は現在の認証・閲覧権限を確認して元receiptだけを返し、時刻や採択を更新しない。別内容・別actor/contextはREQUEST_CONFLICT。currentは同じrequest_idでも時刻と失効を再評価し、過去のvalid=trueをキャッシュから返さない。古い採択receiptを返しても現在の失効は解除しない。receiptの読取りでは保存済みcanonical bytesとresponse digestを照合し、破損を監査履歴として返さない。

## 5. 検証

固定UID/GID・capability・NoNewPrivs、候補によるactor偽装の拒否、socket置換/DB読取り/setuid拒否、managerとvalidatorの権限分離を実Dockerで確認する。実モデルの限定提案をmanager経由で保存し、validatorによる決定的検査の後だけ採択する。

初期値弱化、未知field、内容違いの再配送、再起動後の同一receipt、同一世代の並行採択、検証後の権限取消し・失効・期限切れ・clock rollback、保存障害のrollbackを検査する。PolicyProfile採択の検査が通っても、32要求の全MVP受入は別途必要である。

## 6. 実装と限定したモデル接続

`policy.py`が初期境界、`adoption.py`が原子的な保存と失効、`authority.py`がsocket認証とframingを担当する。固定imageの内容は`config/authority-runtime.lock.json`へ結び、配置前と各containerの開始前後・再起動前後にimage、UID、volume/tmpfs、network、資源上限を照合する。`HostConfig.Tmpfs`を正本としてtmpfsのpathとoptionを完全一致検査し、volumeの追加・DB共有・書込み権限変更を拒否する。回収は所有する固定名とlabelを確認し、停止後の削除と、成功したDocker照会による不存在確認まで行う。

最初の実モデル接続は`management.py`の固定messagesから、DGX Qwenに`timeout_seconds`の90/100/110/120と3種類の理由コードだけを提案させる。最大出力128 token、応答64 KiB、呼出し1回、再試行なし、補助process全体55秒を上限とする。自由文・未知field・モデル不一致・usage不整合・不完全応答は破棄し、rawや拒否内容のhashを保存しない。検査済みの方針だけをUID 12001へ渡し、12003による決定的検査、12001による採択、12004による現在状態の確認へ進む。候補contextや任意のモデル出力から権限・操作・保存先を作らない。

この接続は管理方針の提案・採択を実測する前段である。続いて[開始境界](run-contract-detail-spec.md)で初回EvaluationContractと固定fixtureの予約・実行・精算を接続した。モデル送信と全資源台帳・外部課金の統合、baseline採択、常設運用の再開・失効連鎖は残る。モデル呼出しが失敗した場合は未実施/利用不能として記録し、手書きの提案をモデル成果へ置き換えない。

```sh
python -m tools.prepare_authority_runtime
python -m tools.verify_authority_runtime --output .ga/authority-check-new
python -m tools.verify_management_runtime --output .ga/management-check-new
```

## 7. 明示的な評価Store移行

既知AdoptionStoreのv2/v3からv4への移行は、台帳v1からv2への`db-upgrade`とは別に、次のコマンドで明示実行する。[候補保存形式](contract-transition-detail-spec.md)の2表を追加する。

```sh
python -m tools.migrate_evaluation_store PATH
```

引数は移行対象の既存SQLite path一つだけである。既知v2/v3の移行成功はcanonical JSONを標準出力し終了値0、既にv4・未知Store・不存在path・入力や保存の障害は機密情報を含まない固定error JSONを標準エラーへ出して終了値2とする。成功結果も`ci_eligible=false`であり、通常CIの合格を表さない。自動初期化やv1台帳の移行はこのコマンドの対象外である。

Docker稼働が必要で、最後のコマンドは配置済みのローカルQwen接続へ1回照会する。出力先には未存在のrepo内directoryを指定する。検証用DB volumeは証跡調査用に保持し、稼働containerは回収する。通常unittestからDockerやモデルを起動しない。
