#!/usr/bin/env python3
"""検知結果 → X投稿文の整形。

Xの文字数カウントは「280 weighted characters」で、日本語などの全角は
1文字=2カウント、URLは実長に関わらず一律23カウント（t.co短縮のため）。
実質、日本語のみなら140文字が上限になる。

方針（在庫速報アカウント）:
  人間らしい口語は狙わない。読み手が求めるのは「何が・いくらで・どこに」
  なので、その3点だけを最短で出す。定型であることを隠さない代わりに、
  情報密度と正確さで価値を出す。

  事実だけ書く: 検知できたのは「ページ上で在庫/告知が動いた」ことだけ。
  定価で買えるとは限らない（転売価格の可能性）ため断定しない。
"""

import re

import config
from rules import _item_short_name

# Xの重み付き文字数上限（全角=2, 半角=1 換算）
X_WEIGHT_LIMIT = 280
# URLはt.coで短縮されるため実長に関わらずこの重みで固定される
URL_WEIGHT = 23

# ジャンルタグ: 監視キーワード → 付けるタグ（複数可）。素直な部分一致で引く。
# 1つ目は検索母数の大きい定番タグ、2つ目以降は届く層を広げる補助タグ。
HASHTAG_MAP = {
    "ポケモンカード": ["#ポケカ", "#ポケモンカード"],
    "ポケカ": ["#ポケカ", "#ポケモンカード"],
    "ワンピースカード": ["#ワンピカ", "#ワンピースカードゲーム"],
    "遊戯王": ["#遊戯王", "#遊戯王OCG"],
    "ドラゴンボール": ["#フュージョンワールド", "#DBFW"],
    "ガンプラ": ["#ガンプラ", "#ガンプラ再販"],
}

# 商品名の特徴 → 補足タグ（BOX/パック等の形態で検索する層に届かせる）
FORM_TAGS = {
    "BOX": "#BOX",
    "カートン": "#カートン",
    "スリーブ": "#スリーブ",
    "デッキシールド": "#スリーブ",
    "プレイマット": "#プレイマット",
}

# 状況タグ: 在庫速報を追っている層が実際に検索/フォローしている語。
# 在庫とお知らせで分ける（誤読を招かないため）。
SITUATION_TAGS = {
    "stock": ["#在庫あり", "#再入荷"],
    "info": ["#再販情報", "#予約開始"],
}

# 全投稿共通で最後に付ける
BASE_TAGS = ["#再販"]

# タグの上限。多すぎるとスパム判定・可読性低下を招くため頭打ちにする。
MAX_HASHTAGS = 6

# ドメイン → 表示用ストア名（「どこに」を1語で出すため）。
# config.STORE_NAME_HINTS の逆引きだと表記ゆれ（アマゾン/Amazon）が入るので
# 表示用として別に持つ。
STORE_LABELS = [
    ("amazon", "Amazon"), ("amzn", "Amazon"),
    ("rakuten", "楽天"), ("yodobashi", "ヨドバシ"),
    ("pokemoncenter", "ポケセン"), ("amiami", "あみあみ"),
    ("animate", "アニメイト"), ("surugaya", "駿河屋"),
    ("biccamera", "ビックカメラ"), ("yamada", "ヤマダ"),
    ("7net", "セブン"), ("omni7", "セブン"),
    ("hmv", "HMV"), ("tsutaya", "TSUTAYA"), ("toei-anim", "東映"),
    ("lawson", "ローソン"), ("toysrus", "トイザらス"),
    ("joshin", "ジョーシン"), ("edion", "エディオン"),
    ("hobbysearch", "ホビーサーチ"),
]

# アフィリエイトタグを付けた場合に必須の開示表記（Amazonアソシエイト規約）
AFFILIATE_DISCLOSURE = "#PR"


def _weighted_len(text):
    """Xの重み付き文字数を数える。URLは23固定、全角2、半角1。"""
    total = URL_WEIGHT * len(re.findall(r"https?://\S+", text))
    body = re.sub(r"https?://\S+", "", text)
    for ch in body:
        # ASCII と半角カナは1、それ以外（日本語含む）は2
        total += 1 if ord(ch) < 0x0080 or 0xFF61 <= ord(ch) <= 0xFF9F else 2
    return total


def _store_label(url):
    """URLのドメインから表示用ストア名を引く。不明なら空文字。"""
    if not url:
        return ""
    for needle, label in STORE_LABELS:
        if needle in url:
            return label
    return ""


def _hashtags(item, kind="stock", is_affiliate=False):
    """商品名と検知種別からハッシュタグを組み立てる。重複は除く。

    優先順に積んで MAX_HASHTAGS で打ち切る:
      #PR（アフィリエイト時は規約上必須なので最優先）
      → ジャンル（#ポケカ 等・最も検索される）
      → 形態（#BOX 等）
      → 状況（#在庫あり 等）
      → 共通（#再販）
    """
    name = item.get("name", "")
    tags = [AFFILIATE_DISCLOSURE] if is_affiliate else []

    def add(tag):
        if tag not in tags:
            tags.append(tag)

    for kw, genre_tags in HASHTAG_MAP.items():
        if kw in name:
            for t in genre_tags:
                add(t)
    for kw, tag in FORM_TAGS.items():
        if kw in name:
            add(tag)
    for t in SITUATION_TAGS.get(kind, SITUATION_TAGS["stock"]):
        add(t)
    for t in BASE_TAGS:
        add(t)

    return " ".join(tags[:MAX_HASHTAGS])


def apply_affiliate_tag(url, tag=None):
    """AmazonのURLにアソシエイトタグを付ける。tagが無ければ原URLのまま返す。

    Amazon以外のURLは対象外（各ストアのアフィリエイトは別途ASP経由のため）。
    tagは環境変数 AMAZON_ASSOCIATE_TAG から渡す想定。

    Returns:
        (url, is_affiliate): 付与後URLと、実際に付与したかのフラグ
    """
    if not tag or not url:
        return url, False
    if "amazon.co.jp" not in url and "amzn.to" not in url:
        return url, False
    if "tag=" in url:  # 既に付いている
        return url, True
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={tag}", True


def build_post_text(item, detail=None, kind="stock", affiliate_tag=None):
    """1件の検知を、Xにそのまま貼れる投稿文にする。

    在庫速報として「何が・いくらで・どこに」を最短で出す:
        商品名
        価格 ストア名
        URL
        ハッシュタグ

    価格は定価が分かっている場合のみ「定価」と明記して出す。検知した在庫が
    その価格とは限らない（転売価格の可能性がある）ため、単に「5,940円」とは
    書かない。

    Args:
        item: 監視対象dict（name, url, retail_price を使う）
        detail: 未使用（呼び出し側の互換のため受け取るだけ）
        kind: "stock"(在庫検知) / "info"(お知らせ)
        affiliate_tag: Amazonアソシエイトタグ（Noneなら付与しない）
    Returns:
        str: 280 weighted chars 以内の投稿文
    """
    name = _item_short_name(item)
    url, is_aff = apply_affiliate_tag(item.get("url", ""), affiliate_tag)
    tags = _hashtags(item, kind, is_aff)
    store = _store_label(url)

    # 2行目: 「定価5,940円 Amazon」。どちらも無ければ行ごと省く。
    meta = []
    if item.get("retail_price"):
        meta.append(f"定価{item['retail_price']:,}円")
    if store:
        meta.append(store)
    # お知らせ系は在庫と区別が付くよう1語だけ添える（在庫と誤読されると害があるため）
    if kind != "stock":
        meta.append("再販告知")
    meta_line = "\n" + " ".join(meta) if meta else ""

    # 商品名以外を先に確定させ、残りを商品名の予算にする
    fixed = f"{meta_line}\n{url}"
    if tags:
        fixed += f"\n{tags}"
    budget = X_WEIGHT_LIMIT - _weighted_len(fixed)

    short_name = name
    while short_name and _weighted_len(short_name) > budget - 2:
        short_name = short_name[:-1]
    if short_name != name:
        short_name = short_name.rstrip() + "…"

    return f"{short_name}{fixed}"


def build_post_block(alerts, max_posts=3, affiliate_tag=None):
    """通知メール/Discordの末尾に付ける「Xコピペ用」ブロックを行リストで返す。

    Args:
        alerts: [(item, detail, kind)] — build_messages と同じ形
        max_posts: 載せる最大件数（通知が長くなりすぎるのを防ぐ）
        affiliate_tag: Amazonアソシエイトタグ（Noneなら付与しない）
    Returns:
        list[str]: 通知本文に足す行。alertsが空なら空リスト。
    """
    if not alerts:
        return []

    norm = [a if len(a) == 3 else (a[0], a[1], "stock") for a in alerts]
    # 在庫検知を優先して載せる（お知らせより投稿価値が高い）
    norm.sort(key=lambda a: 0 if a[2] == "stock" else 1)

    lines = ["", "─" * 20, "📋 X投稿用（コピペしてそのまま貼れます）", ""]
    for i, (item, detail, kind) in enumerate(norm[:max_posts], 1):
        if len(norm) > 1:
            lines.append(f"【{i}件目】")
        lines.append(build_post_text(item, detail, kind, affiliate_tag))
        lines.append("")
    if len(norm) > max_posts:
        lines.append(f"（残り{len(norm) - max_posts}件は投稿文を省略）")
    return lines
