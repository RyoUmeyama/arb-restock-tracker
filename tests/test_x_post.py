#!/usr/bin/env python3
"""X投稿文生成の単体テスト（ネットワークアクセスなし・CIで実行）。

実行: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from x_post import formatter as f


ITEM = {
    "name": "ポケモンカードゲーム スカーレット&バイオレット 強化拡張パック 再販集約",
    "url": "https://www.amazon.co.jp/dp/B0XXXXX",
    "retail_price": 5940,
}
LONG_ITEM = {
    "name": "ポケモンカードゲーム スカーレット＆バイオレット 強化拡張パック 「ナイトワンダラー」 拡張パック 30パック入りBOX 完全受注生産版 再販集約（横断）",
    "url": "https://www.amazon.co.jp/dp/B0YYYYY",
    "retail_price": 12800,
}


class TestWeightedLen(unittest.TestCase):
    """Xの重み付き文字数: 全角2・半角1・URLは実長に関わらず23。"""

    def test_ascii_is_one(self):
        self.assertEqual(f._weighted_len("abc"), 3)

    def test_japanese_is_two(self):
        self.assertEqual(f._weighted_len("あいう"), 6)

    def test_url_is_fixed_23(self):
        short = f._weighted_len("https://a.co/1")
        long = f._weighted_len("https://www.amazon.co.jp/dp/B0XXXXX?tag=foo-22&ref=bar")
        self.assertEqual(short, 23)
        self.assertEqual(long, 23)

    def test_halfwidth_kana_is_one(self):
        self.assertEqual(f._weighted_len("ｱｲｳ"), 3)


class TestPostLength(unittest.TestCase):
    """どの入力でも280 weighted chars を超えない（超えると投稿できない）。"""

    def test_normal_item_within_limit(self):
        for kind in ("stock", "info"):
            post = f.build_post_text(ITEM, "Amazonで在庫復活を検知", kind)
            self.assertLessEqual(f._weighted_len(post), f.X_WEIGHT_LIMIT)

    def test_long_name_within_limit(self):
        for kind in ("stock", "info"):
            post = f.build_post_text(LONG_ITEM, "検知", kind)
            self.assertLessEqual(f._weighted_len(post), f.X_WEIGHT_LIMIT)

    def test_long_name_is_truncated_with_ellipsis(self):
        # 予算を確実に超える長さ（_item_short_name の除去語では短くならない名前）
        item = dict(LONG_ITEM, name="ポケモンカードゲーム " + "超長商品名" * 30)
        post = f.build_post_text(item, "検知", "stock")
        self.assertIn("…", post)
        self.assertLessEqual(f._weighted_len(post), f.X_WEIGHT_LIMIT)

    def test_affiliate_version_within_limit(self):
        # #PR が増える分で溢れないこと
        post = f.build_post_text(LONG_ITEM, "検知", "stock", affiliate_tag="mytag-22")
        self.assertLessEqual(f._weighted_len(post), f.X_WEIGHT_LIMIT)


class TestAccuracy(unittest.TestCase):
    """検知できていないことを断定しない（転売価格の可能性があるため）。"""

    def test_price_is_labeled_as_retail(self):
        # 「5,940円」単体だと在庫がその値段で買えると誤読される。必ず「定価」を冠する
        post = f.build_post_text(ITEM, None, "stock")
        self.assertIn("定価5,940円", post)

    def test_no_price_when_unknown(self):
        item = dict(ITEM)
        del item["retail_price"]
        post = f.build_post_text(item, None, "stock")
        self.assertNotIn("円", post)

    def test_url_always_present(self):
        post = f.build_post_text(ITEM, None, "stock")
        self.assertIn(ITEM["url"], post)

    def test_info_kind_marked_to_avoid_stock_misread(self):
        # お知らせを在庫と誤読されると害が大きいので明示する
        post = f.build_post_text(ITEM, None, "info")
        self.assertIn("再販告知", post)

    def test_stock_kind_has_no_info_marker(self):
        post = f.build_post_text(ITEM, None, "stock")
        self.assertNotIn("再販告知", post)


class TestHashtags(unittest.TestCase):
    """ジャンル→形態→状況→共通 の順に積み、MAX_HASHTAGSで打ち切る。"""

    def test_genre_tags_added(self):
        tags = f._hashtags(ITEM, "stock")
        self.assertIn("#ポケカ", tags)
        self.assertIn("#ポケモンカード", tags)

    def test_form_tag_from_name(self):
        item = {"name": "ワンピースカードゲーム 新時代の主役 BOX"}
        self.assertIn("#BOX", f._hashtags(item, "stock"))

    def test_situation_differs_by_kind(self):
        self.assertIn("#在庫あり", f._hashtags(ITEM, "stock"))
        self.assertIn("#再販情報", f._hashtags(ITEM, "info"))
        # 在庫と告知の状況タグが混ざらない（誤読防止）
        self.assertNotIn("#在庫あり", f._hashtags(ITEM, "info"))

    def test_capped_at_max(self):
        item = {"name": "ポケモンカードゲーム 拡張パック BOX スリーブ"}
        tags = f._hashtags(item, "stock", is_affiliate=True).split()
        self.assertLessEqual(len(tags), f.MAX_HASHTAGS)

    def test_pr_survives_cap(self):
        # 規約上必須の#PRが打ち切りで消えてはいけない
        item = {"name": "ポケモンカードゲーム 拡張パック BOX スリーブ プレイマット"}
        self.assertIn("#PR", f._hashtags(item, "stock", is_affiliate=True))

    def test_no_duplicates(self):
        tags = f._hashtags(ITEM, "stock").split()
        self.assertEqual(len(tags), len(set(tags)))

    def test_unknown_genre_still_gets_tags(self):
        # ジャンル不明でも状況・共通タグは付く（無タグ投稿を避ける）
        tags = f._hashtags({"name": "謎の商品"}, "stock")
        self.assertTrue(tags.strip())


class TestStoreLabel(unittest.TestCase):
    """「どこに」を1語で出す。不明ドメインは省略（誤った店名を出さない）。"""

    def test_amazon(self):
        self.assertEqual(f._store_label("https://www.amazon.co.jp/dp/B0"), "Amazon")

    def test_rakuten(self):
        self.assertEqual(f._store_label("https://books.rakuten.co.jp/x"), "楽天")

    def test_unknown_domain_is_blank(self):
        self.assertEqual(f._store_label("https://example.com/x"), "")

    def test_store_appears_in_post(self):
        self.assertIn("Amazon", f.build_post_text(ITEM, None, "stock"))


class TestAffiliate(unittest.TestCase):
    """アフィリエイトタグ付与と、規約上必須の開示表記。"""

    def test_no_tag_leaves_url_untouched(self):
        url, is_aff = f.apply_affiliate_tag(ITEM["url"], None)
        self.assertEqual(url, ITEM["url"])
        self.assertFalse(is_aff)

    def test_tag_appended_to_amazon(self):
        url, is_aff = f.apply_affiliate_tag(ITEM["url"], "mytag-22")
        self.assertIn("tag=mytag-22", url)
        self.assertTrue(is_aff)

    def test_non_amazon_not_tagged(self):
        url, is_aff = f.apply_affiliate_tag("https://www.rakuten.co.jp/x", "mytag-22")
        self.assertNotIn("tag=", url)
        self.assertFalse(is_aff)

    def test_existing_query_uses_ampersand(self):
        url, _ = f.apply_affiliate_tag("https://www.amazon.co.jp/dp/B0?ref=x", "mytag-22")
        self.assertIn("?ref=x&tag=mytag-22", url)

    def test_disclosure_added_when_affiliate(self):
        post = f.build_post_text(ITEM, "検知", "stock", affiliate_tag="mytag-22")
        self.assertIn("#PR", post)

    def test_no_disclosure_when_not_affiliate(self):
        post = f.build_post_text(ITEM, "検知", "stock")
        self.assertNotIn("#PR", post)


class TestFormat(unittest.TestCase):
    """在庫速報として「何が・いくらで・どこに」を最短で出す。"""

    def test_deterministic(self):
        a = f.build_post_text(ITEM, None, "stock")
        b = f.build_post_text(ITEM, None, "stock")
        self.assertEqual(a, b)

    def test_no_filler_phrases(self):
        # 口語の煽り・注意書きは載せない（速報の情報密度を落とすため）
        post = f.build_post_text(ITEM, None, "stock")
        for phrase in ("在庫戻ってます", "確認してから", "判断を", "要確認"):
            self.assertNotIn(phrase, post)

    def test_line_structure(self):
        # 商品名 / 定価+ストア / URL / タグ の4行
        lines = f.build_post_text(ITEM, None, "stock").split("\n")
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[2].startswith("https://"))

    def test_omits_meta_line_when_nothing_to_show(self):
        # 定価もストア名も不明なら2行目は省く（タグ行は常に出る）
        item = {"name": "テスト商品", "url": "https://example.com/x"}
        lines = f.build_post_text(item, None, "stock").split("\n")
        self.assertEqual(lines[:2], ["テスト商品", "https://example.com/x"])
        self.assertTrue(lines[2].startswith("#"))


class TestBlock(unittest.TestCase):
    def test_empty_alerts_returns_empty(self):
        self.assertEqual(f.build_post_block([]), [])

    def test_two_tuple_alerts_supported(self):
        # 後方互換: (item, detail) の2要素形式は stock 扱い
        block = f.build_post_block([(ITEM, "検知")])
        text = "\n".join(block)
        self.assertIn(ITEM["url"], text)
        self.assertNotIn("再販告知", text)  # stock 扱いなのでお知らせ印は付かない

    def test_stock_sorted_before_info(self):
        block = f.build_post_block([(ITEM, "告知", "info"), (LONG_ITEM, "在庫", "stock")])
        text = "\n".join(block)
        self.assertLess(text.index("【1件目】"), text.index("【2件目】"))
        # 1件目が stock 側（LONG_ITEM）であること
        self.assertIn("B0YYYYY", text.split("【2件目】")[0])

    def test_max_posts_truncates_with_note(self):
        alerts = [(ITEM, "検知", "stock")] * 5
        block = f.build_post_block(alerts, max_posts=2)
        self.assertTrue(any("残り3件" in ln for ln in block))


if __name__ == "__main__":
    unittest.main()
