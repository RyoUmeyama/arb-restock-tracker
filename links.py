#!/usr/bin/env python3
"""リンク解決: ストア直リンクの確実な対応付けと検索URLフォールバック（check_stock.pyから分割）"""

import re
from urllib.parse import urlparse, parse_qs, unquote, quote

import config
from rules import _upcoming_dates, _item_short_name

# href 抽出（"…" と '…' の両方。2026-09-09 監査: 単一引用符のhrefが拾えていなかった）
_HREF_RE = re.compile(r'href=(["\'])(https?://[^"\']+?)\1', re.I)


def _host_of(url):
    """URLのホスト名（小文字・ポート除去）。解釈できなければ ""。"""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _host_matches(url, domains):
    """ホスト名がいずれかのドメインと一致（host == d または host が ".d" で終わる）。
    URL全体の部分一致ではない（2026-09-09 監査: twitter.com/pokemoncenterPR が
    "pokemon" の部分一致でストアURL扱いされていた）。"""
    host = _host_of(url)
    if not host:
        return False
    for d in domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


def _unwrap_affiliate(url):
    """楽天アフィリエイト/もしも/バリューコマース等のラッパーURLから実際の遷移先を取り出す。
    多段（もしも→楽天afl→商品）は最大3段まで剥がす。既知の中継ホスト以外は触らない
    （Amazon商品URLの url= パラメータ等に乗っ取られない。2026-09-09 監査）。
    取り出せない形式はそのまま返す（アフィリエイト経由でも商品ページには着地する）。"""
    for _ in range(3):
        if not _host_matches(url, config.AFFILIATE_HOSTS):
            return url
        nxt = None
        try:
            q = parse_qs(urlparse(url).query)
            for key in ("pc", "m", "vc_url", "url", "u"):
                vals = q.get(key)
                if vals and vals[0].startswith("http"):
                    nxt = unquote(vals[0])
                    break
        except Exception:
            nxt = None
        if not nxt or nxt == url:
            return url
        url = nxt
    return url


def _norm_link_text(t):
    """アンカーテキスト照合用の正規化（タグ・エンティティ・空白を除去）。"""
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"&[a-z]+;|&#\d+;", " ", t)
    return re.sub(r"\s+", "", t)


def _clean_store_url(href):
    """hrefのHTMLエンティティを復元→アフィリエイト剥がし→ストアドメイン確認（ホスト一致）。
    ストアドメインでなければ None。"""
    href = href.replace("&amp;", "&").replace("&#038;", "&")
    real = _unwrap_affiliate(href)
    if _host_matches(real, ("anime-matsuri.com", "nyuka-now.com")):
        return None
    if _host_matches(real, config.STORE_DOMAINS):
        return real
    return None


def extract_anchors(html):
    """ページ内の <a href>text</a> を (正規化テキスト, href) のリストで返す。
    行→リンクの確実な対応付け（アンカーテキスト一致）に使う。"""
    anchors = []
    if not html:
        return anchors
    for m in re.finditer(r'<a\s[^>]*?href=(["\'])(https?://[^"\']+?)\1[^>]*?>(.*?)</a>', html, re.S | re.I):
        text = _norm_link_text(m.group(3))
        if len(text) >= 10:
            anchors.append((text, m.group(2)))
    return anchors


# 店舗名ヒント経路で「行の位置」を探す際の前方ウィンドウ（表は「行→リンク」の順）
_HINT_FORWARD_WINDOW = 800


def _hint_probes(line):
    """行の位置をHTML内で探すためのプローブ文字列（確実な順）: 行全体 → 末尾18字 → 先頭18字。
    2026-09-09 監査: 先頭18字だけでは「…カードセット ○○・○○が予約できるAmazon抽選情報」の
    ような同一接頭辞の兄弟見出しが全て先頭行に解決され、別商品のamzn.toが付いていた。"""
    s = line.strip()
    probes = [s, s[-18:].strip(), s[:18].strip()]
    out = []
    for p in probes:
        if len(p) >= 4 and p not in out:
            out.append(p)
    return out


def resolve_store_link(html, line, anchors=None):
    """追加行 line に対応する遷移先ストアURLを返す。
    「実際のページでは関係ないものが表示される」誤リンク事故を防ぐため、
    **確実に対応関係が取れる場合だけ**URLを返す（推測で近傍リンクを拾わない）:
      ① 行がリンクのアンカーテキストそのもの（集約ページの商品名リンク）→ そのhref
      ② 行が店舗名を含む → 行の出現位置の直後（前方800字）でドメインが店舗名と一致するリンクのみ。
         行の出現は全箇所を試す（最初の出現だけ見ると兄弟見出しに誤解決する）
    どちらでもなければ None（URLなしで通知。集約ページのURLは従来どおり載る）。"""
    if not html:
        return None
    # ① アンカーテキスト一致（最も確実: その行はリンクの文字列そのもの）
    if anchors is None:
        anchors = extract_anchors(html)
    nl = _norm_link_text(line)
    if len(nl) >= 15:
        for na, href in anchors:
            # 完全包含のみ採用（先頭一致では同一シリーズの別商品に交差マッチするため。
            # 例: METAL ROBOT魂＜SIDE MS＞... は先頭が全商品共通）。
            # 「行 ⊆ アンカー」はアンカーが商品名＋（価格：...）等の装飾付きのケース。
            if len(na) >= 15 and (nl in na or na in nl):
                real = _clean_store_url(href)
                if real:
                    return real
    # ② 店舗名ヒント一致（行が「Amazonで抽選受付」等の場合のみ・ドメイン一致必須）
    hint_domains = None
    for name, domains in config.STORE_NAME_HINTS.items():
        if name in line:
            hint_domains = domains
            break
    if not hint_domains:
        return None
    for probe in _hint_probes(line):
        idx = html.find(probe)
        found_any = False
        while idx >= 0:
            found_any = True
            window = html[idx + len(probe): idx + len(probe) + _HINT_FORWARD_WINDOW]
            for m in _HREF_RE.finditer(window):
                real = _clean_store_url(m.group(2))
                if real and _host_matches(real, hint_domains):
                    return real  # その出現の直後にある最も近い一致リンク
            idx = html.find(probe, idx + 1)
        if found_any:
            # 行は見つかったが直後に一致リンクが無い → 誤リンクを載せない
            # （より曖昧なプローブで探し直すと兄弟行に誤解決するため打ち切る）
            return None
    return None


def resolve_store_link_from_article(html, title):
    """nyuka-now等の記事ページ（単一商品）から遷移先ストアURLを解決する。
    タイトルの【Amazon】等の店舗タグとドメインが一致するリンクだけを採用し、
    一致が取れなければ None（誤リンクを載せない。呼び出し側が検索URLで代替）。"""
    if not html:
        return None
    hint = None
    for name, domains in config.STORE_NAME_HINTS.items():
        if name in title:
            hint = domains
            break
    if not hint:
        return None  # 店舗が特定できない記事は確実な対応が取れない
    for m in _HREF_RE.finditer(html):
        real = _clean_store_url(m.group(2))
        if real and _host_matches(real, hint):
            return real
    return None


def _store_in_line(line):
    """行に含まれる店舗名（STORE_NAME_HINTS のキー）のうち、行の最も前に出るものを返す。"""
    best = None
    for name in config.STORE_NAME_HINTS:
        pos = line.find(name)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, name)
    return best[1] if best else None


def _strip_store_names(q):
    """クエリから店舗の呼称を落とす（直後の「で/にて/の」等の助詞ごと）。長い表記を先に。"""
    words = list(config.STORE_QUERY_STRIP_WORDS) + list(config.STORE_NAME_HINTS)
    words.sort(key=len, reverse=True)
    for w in words:
        q = re.sub(re.escape(w) + r"(?:にて|では|でも|で|の|より|から|も|と|へ|が|は)?", " ", q)
    return q


def fallback_search_url(line, item):
    """確実な直リンクが無い行に付ける「ストア検索URL」を作る。
    集約ページのURLだけでは『結局どこで買えるのか』が分からないため、
    商品名でのストア検索結果へ直接飛ばす。行に店舗名があればそのストアの検索、
    検索テンプレートの無い店舗ならその店舗のトップURL（STORE_HOME_URL）、
    店舗名が無ければAmazon検索。クエリは行から日付・記号・店舗名を除いた商品名部分
    （短すぎる行は監視対象の商品名を使う）。
    2026-09-09 監査: 店舗名がクエリに残る／「抽選/予約」の "/" が楽天のパスを壊す／
    「2026年6月中旬頃から」が残る、を修正。"""
    store = _store_in_line(line)
    tpl_key = "amazon"
    if store:
        key = config.STORE_SEARCH_KEY.get(store)
        if key:
            tpl_key = key
        elif store in config.STORE_HOME_URL:
            return config.STORE_HOME_URL[store]
    q = line
    q = re.sub(r"【[^】]*】", " ", q)                      # 店舗タグ等
    q = _strip_store_names(q)
    # 日付は全形式除去（2026年7月10日 / 2026.7.10 / 2026/7/10 / 7月10日 /
    # 2026年6月中旬頃から / 6月下旬 / 2026年6月）
    q = re.sub(r"20\d\d[年./]\s*\d{1,2}[月./]\s*\d{1,2}日?|\d{1,2}月\s*\d{1,2}日", " ", q)
    q = re.sub(r"(?:20\d\d年\s*)?\d{1,2}月\s*[上中下]旬頃?(?:から|より|に)?", " ", q)
    q = re.sub(r"20\d\d年\s*\d{1,2}月頃?(?:から|より|に)?", " ", q)
    q = re.sub(r"[（(].*?[)）]", " ", q)                   # 括弧注記
    q = q.replace("「", " ").replace("」", " ").replace("『", " ").replace("』", " ")
    for w in ("再販", "入荷", "抽選", "予約", "受付中", "受付", "先着", "販売開始", "販売",
              "発売", "在庫", "応募", "開始", "期間", "情報", "まとめ", "〜", "～",
              "にて", "継続中", "継続", "です", "ます"):
        q = q.replace(w, " ")
    q = re.sub(r"[、。．！!？?・/／]", " ", q)             # 句読点・記号（"/" は残すとパスを壊す）
    q = re.sub(r"\s(が|を|に|は|で|の|と|から|より)\s", " ", " " + q + " ")  # 浮いた助詞
    q = re.sub(r"(?<=[ぁ-んァ-ヶ一-龯A-Za-z0-9])(が|を|は|に|で)(?=\s|$)", "", q)  # 語末の助詞（「3種が」→「3種」）
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) < 8:  # 行から商品名が取れない（期間行など）→ 監視対象の商品名で検索
        q = _item_short_name(item)
    q = q[:40]
    return config.SEARCH_URL_TEMPLATES[tpl_key].format(q=quote(q, safe=""))
