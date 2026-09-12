---
intent_id: INT-GAH-001
owner: RNA4219
status: active
last_reviewed_at: 2026-09-10
next_review_due: 2026-10-10
---

# 資料来歴

GAHの要求原稿と、コピー元が残っていた整理前構成を分けて保存する。archiveは現行の要求・指示・検収として適用しない。

利用者の非掲載指示により、参照元を特定する名称・URL・版識別子は保存稿や過去の証跡も匿名化した。現在のarchive-manifestのhashは匿名化した保存版に対するもの。過去の検証結果とhashは当時の記録として区別し、非匿名化原本とのbyte一致や、元の識別子を保持していることを示さない。[匿名化記録](../evidence/reference-anonymization-20260910/anonymization.json)を参照する。

| 資料 | 保存場所 | 扱い |
|---|---|---|
| GAHの現行要求 | [要求明確化案](../requirements.md) / [初期運用方針](../operating-policy.md) | v0.4の32要求と判定条件、利用者決定と委任による初期値。継承・補強・後期対象は[対応表](../requirements-traceability.md)で管理 |
| 外部参照元の現行参照境界 | [参照境界](../reference-boundary.md) | 利用者の訂正によりクローズド資産として直接利用しない。旧移植方針は撤回。GAHは要求から独立に設計する |
| 外部参照元の直接利用禁止を反映する前の文書 | [保存稿](archive/before-reference-boundary-20260910/docs/requirements.md) / [保存manifest](archive/before-reference-boundary-20260910/archive-manifest.json) | 18ファイルを保存。移植に言及する旧案は現行の許可・実装方針ではない |
| 外部参照元の確認記録 | [匿名化した取得記録](../evidence/reference-confirmation-20260910/source-inspection.json) | 過去の静的確認の履歴。調査用コピーは削除済みで、資産利用の許可ではない |
| 外部参照元のURL提示前のv0.4 | [保存稿](archive/before-reference-confirmation-20260910/docs/requirements.md) / [保存manifest](archive/before-reference-confirmation-20260910/archive-manifest.json) | 初期運用値を選定し、転用元の名前/pathが確認待ちだった時点 |
| 運用方針の回答前のv0.3 | [保存稿](archive/before-operating-decisions-20260910/docs/requirements.md) / [保存manifest](archive/before-operating-decisions-20260910/archive-manifest.json) | 敵対的検証を反映した32要求。CI・数値・管理主体が判断待ちだった時点。v0.4で利用者回答を反映。保存時の配置を基準とするリンクを含む |
| 敵対的検証前のv0.2 | [保存稿](archive/before-adversarial-20260910/docs/requirements.md) / [保存manifest](archive/before-adversarial-20260910/archive-manifest.json) | 二つの利用場面を明確化した24要求。v0.3は反例検討で8要求を追加。保存時の配置を基準とするリンクを含む |
| 初期対象の補足前のv0.1 | [保存稿](archive/before-dual-usecases-20260910/docs/requirements.md) / [保存manifest](archive/before-dual-usecases-20260910/archive-manifest.json) | coding agentの開発・CIを初期対象と仮定した時点。利用者の補足によりv0.2以降はLLMガードレール評価も対象とする。保存時の配置を基準とするリンクを含む |
| 明確化前の整形済み原稿 | [保存稿](archive/before-clarification-20260910/docs/requirements.md) / [保存manifest](archive/before-clarification-20260910/archive-manifest.json) | 構成図・YAML等の概念例・技術候補・外部主張を含む参考資料。保存時の配置を基準とするリンクを含む |
| 整理前のGAH原稿 | [匿名化した保存稿](archive/gah-requirements-original-20260910.md) | 元の内容を基に、利用者指定の参照情報を匿名化した版 |
| コピー混在の整理前構成 | [当時のREADME](archive/scaffold-before-cleanup-20260910/README.md) / [保存manifest](archive/scaffold-before-cleanup-20260910/archive-manifest.json) | Spec Reconstructorの文書・Task・レビュー・承認・証跡・生成物などを保存。GAHの実績ではない |
| 原稿の引用参照 | [出典台帳](citation-register.md) | チャット内参照のみ。原資料とURLは未復元・未検証 |

[source-manifest.json](source-manifest.json)に匿名化した原稿の現在hash、体裁整理時の履歴、要求明確化前の保存版と改訂範囲を記録する。体裁整理時の本文一致は当時の版に対する結果であり、現行要求や匿名化後の原稿とのbyte一致を主張しない。archive配下は現行Birdseyeと通常の文書metadata検査から除外するが、非掲載対象の残存検査には含める。

保存稿の外部参照元実装・外部製品・新規性・制度に関する記述は歴史資料。外部参照元の固定版の静的確認を行った履歴は保持するが、原稿の全主張の確認や資産利用の許可を意味しない。現在は利用者のクローズド資産という指定と参照境界を優先する。残る外部出典の復元と評価は[未決定事項](../open-questions.md)として別作業にする。
