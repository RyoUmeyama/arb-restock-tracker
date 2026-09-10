#!/usr/bin/env python3
"""判定規則: 日付解決・実質情報判定・商品ライフサイクル・相場選別（check_stock.pyから分割）
規則の仕様は NOTIFICATION_RULES.md を参照。"""

import re
from datetime import date as _date, datetime

import config


def _this_year():
    """現在の年（西暦・JST）。ポケカ新弾の発売日フィルタを年経過で自動追従させるため。
    Actions は UTC なので naive の now() だと 1/1 0〜9時 JST に前年扱いになる。"""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Tokyo")).year


def _normalize_box_name(s):
    """相場照合用に銘柄名を正規化する。装飾語・記号・空白を落として比較精度を上げる。
    全角/半角スペース・中黒・括弧類を除去し、監視名固有の装飾(ポケカ/BOX/再販集約等)も削る。"""
    s = s or ""
    for w in ("ポケカ ", "ポケモンカード ", " BOX", "BOX", " 再販集約", "再販集約",
              "（横断）", "(横断)", "（在庫）", "(在庫)"):
        s = s.replace(w, "")
    # 空白・中黒・括弧などの照合ノイズを除去
    s = re.sub(r"[\s　・,，()（）\[\]【】「」]", "", s)
    return s


# 監視名の接尾辞（監視元の種別）。生の名前・正規化後の両方の形を持つ
_WATCH_NAME_SUFFIXES = (
    " 抽選/再販まとめ（anime-matsuri）", " 抽選/予約まとめ（anime-matsuri）",
    " 再販告知まとめ（anime-matsuri）", " 再販集約", "（横断）", "（在庫）",
    "（楽天ブックス）", "（東映ストア）",
    "抽選/再販まとめanime-matsuri", "抽選/予約まとめanime-matsuri", "再販告知まとめanime-matsuri",
    "再販集約", "横断", "在庫", "楽天ブックス", "東映ストア",
)
# 公式商品名のカテゴリ接頭辞（長い順に並べること。前方一致で1つだけ剥がす）
_CATEGORY_PREFIXES = (
    "拡張パックデラックス", "強化拡張パック", "拡張パック", "ハイクラスパック",
    "スペシャルカードセット", "スペシャルBOX", "スペシャルセット",
    "プレミアムトレーナーボックス", "デッキビルド", "スターターセット", "構築デッキ",
    "MEGA", "メガ",
)


def _core_product_name(s):
    """監視名／公式商品名を「商品コア名」に揃える（発売カレンダーの監視済み照合用）。
    生の名前でも _normalize_box_name 済みの名前でも同じ結果になる。
    例: "ポケカ 30th CELEBRATION 抽選/予約まとめ（anime-matsuri）" → "30thCELEBRATION"
        "拡張パック「30th CELEBRATION」" → "30thCELEBRATION"
        "ハイクラスパック「MEGAドリームex」" / "ポケカ MEGAドリームex 抽選/…" → "ドリームex" """
    s = s or ""
    for w in _WATCH_NAME_SUFFIXES:
        s = s.replace(w, "")
    s = _normalize_box_name(s)
    for w in ("ポケカ", "ポケモンカード", "ポケモンカードゲーム"):
        if s.startswith(w):
            s = s[len(w):]
    for _ in range(3):  # 「MEGA拡張パック…」のように接頭辞が重なる場合があるため数回剥がす
        hit = next((pre for pre in _CATEGORY_PREFIXES if s.startswith(pre)), None)
        if not hit:
            break
        s = s[len(hit):]
    return s


def match_altema_price(name, prices):
    """altema相場辞書から監視名 name に対応する買取価格を選ぶ。
    正規化後、(1)完全一致を最優先。(2)無ければ『監視名コアが altema銘柄名に含まれる』
    候補のうち最短(=余計な装飾やセット品でない単品)を選ぶ。
    altemaは単品BOX名が正解で、長い名前はセット/同梱品など別物の罠のため最短を採る。
    返り値: price(int) | None。"""
    core = _normalize_box_name(name)
    if len(core) < 3:  # 短すぎるコアは誤マッチしやすいので照合しない
        return None
    forward = []   # (正規化altema名長, price) : core in nk
    backward = []  # (正規化altema名長, price) : nk in core
    for k, v in prices.items():
        nk = _normalize_box_name(k)
        if len(nk) < 3:
            continue
        if core == nk:
            return v  # 完全一致が最優先
        # 双方向の部分一致を見る。方向ごとに「正しい候補」の選び方が逆になる点に注意。
        if core in nk:
            # (a) 監視名が短く、altema側が装飾付き。
            #     長い名前はセット/同梱品など別物の罠なので【最短】を採る。
            forward.append((len(nk), v))
        elif nk in core:
            # (b) altema側が単品BOX名で、監視名に「抽選/再販まとめ（anime-matsuri）」等の
            #     装飾が付くケース。監視銘柄の大半（anime-matsuriまとめページ）はこちら。
            #     2026-07-29まで (a) しか見ておらずポケカ15銘柄が1件も相場評価されていなかった。
            #     この方向では【最長】が正しい。短い名前は上位概念への誤マッチになるため。
            #     例)「ロケット団の栄光」に対し "ロケット団"(別商品・13万円) が
            #        "ロケット団の栄光"(2.55万円) より短いという理由で勝ってしまう。
            backward.append((len(nk), v))
    if forward:
        forward.sort(key=lambda x: x[0])       # 最短
        return forward[0][1]
    if backward:
        backward.sort(key=lambda x: -x[0])     # 最長
        return backward[0][1]
    return None


def parse_jp_release_date(text):
    """「2026年 9月16日（水）」形式の発売日を date に変換する。解釈できなければ None。"""
    if not text:
        return None
    m = re.search(r"(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not m:
        return None
    try:
        return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_price_yen(text):
    """「27,500円（税込）」形式の価格を int に変換する。解釈できなければ None。"""
    if not text:
        return None
    m = re.search(r"([\d,]+)\s*円", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def upcoming_releases(products, today, watched_names, min_price, window_days):
    """公式APIの商品リストから「これから発売＝定価入手の機会が来る」ものを返す。

    A方針（既存カテゴリの続弾を早く掴む）の中核。
    docs/17 の実データで、ポケカは新弾ごとに定価超の鞘が繰り返し発生することが
    確認できている。発売前に把握できれば抽選応募・予約の機会を逃さない。

    絞り込み:
      - 発売日が今日以降〜window_days 以内（未来の確定日程のみ）
      - 価格が min_price 以上（デッキシールド等の小物を除外）。
        ただし拡張パックは【1パックの価格】(200〜360円)で載るため価格では切れない。
        転売単位はBOX(1箱=30パック前後)なので、拡張パックは価格に関わらず必ず拾う。
      - プレイマット/ファイル等の周辺グッズは転売対象外として除外
      - 既存の監視銘柄と重複しないもの

    返り値: [(発売日, タイトル, 価格, リンク)] を発売日の昇順で。
    """
    out = []
    for p in (products or {}).values():
        rd = parse_jp_release_date(p.get("releaseDate"))
        if not rd or rd < today:
            continue
        if (rd - today).days > window_days:
            continue
        title = p.get("title") or ""
        if any(kw in title for kw in config.RELEASE_EXCLUDE_KEYWORDS):
            continue
        price = parse_price_yen(p.get("price"))
        # 拡張パックはBOX単位で転売するため、1パック価格による足切りをしない
        is_pack = any(kw in title for kw in config.RELEASE_ALWAYS_KEYWORDS)
        if not is_pack:
            if price is None or price < min_price:
                continue
        nk = _core_product_name(title)
        if not nk:
            continue
        # 監視名は呼び出し側で _normalize_box_name 済み（"30thCELEBRATION抽選/予約まとめanime-matsuri"）
        # なので、ここで双方をコア名（カテゴリ接頭辞・監視元の接尾辞を剥がした形）に揃えて照合する
        # （2026-09-09 監査: 以前は生の正規化名同士で「w in nk or nk in w」を見ており本番では
        # 一度も一致せず、監視済み銘柄が発売カレンダーに毎回載っていた）。
        # 方向は「商品コア ⊆ 監視コア」のみ。逆（監視 "30thCELEBRATION" ⊂ 商品
        # "30thCELEBRATIONFUTURISTIC"）まで除外すると、別商品のFUTURISTIC BOX等が消える
        cores = [_core_product_name(w) for w in watched_names if w]
        if any(len(w) >= 3 and nk in w for w in cores):
            continue
        out.append((rd, title, price or 0, p.get("link") or ""))
    out.sort(key=lambda x: (x[0], -x[2]))
    return out


def net_proceeds(retail, market):
    """定価と相場から手残り（円）を返す。

    market の取得元は altema の【買取価格】= 店に持ち込んで受け取る額であり、
    店側の利ざやが既に差し引かれた「手取り」である（fetch_altema_box_prices 参照）。
    さらに docs/15 の出口優先順位は 1位=店頭買取(手数料・送料ゼロ)、
    メルカリ(10%)は7位で「反復即売りに非推奨」。
    したがって買取価格からさらに販売手数料を引くのは二重控除になる。

    販売コスト（送料・梱包）は出口が店頭持込なら実質ゼロ。宅配買取を使う場合も
    もえたく!/トレトクは全手数料無料のため、既定では控除しない。
    フリマ経由を前提に評価したい場合のみ config.SELL_COST_RATE を上げる。
    """
    if not retail or not market or retail <= 0 or market <= 0:
        return None
    return market * (1 - config.SELL_COST_RATE) - retail


def passes_profit(retail, market, is_pokeca):
    """相場選別: 市場価格と定価から、監視ON/除外を判定する。
    返り値: 'active'(監視ON) / 'dropped'(除外) / 'unknown'(判定不能=安全側で監視継続)。
    ポケカは別格で閾値を下げる(定価以上の鞘が出れば監視)。"""
    net = net_proceeds(retail, market)
    if net is None:
        return "unknown"
    spread = market / retail
    spread_in = config.POKECA_SPREAD_IN if is_pokeca else config.PROFIT_SPREAD_IN
    if spread >= spread_in and net > 0:
        return "active"
    if spread < config.PROFIT_SPREAD_OUT:
        return "dropped"
    return "unknown"  # 中間帯は監視継続（安全側）


def _is_supply_noise(line):
    """サプライ語を「商品そのもの」として含むか。「ラバーマット付き プレミアムデッキセット」の
    ように本体の付属品として現れる（直後が 付/つき/同梱）場合はサプライ扱いしない
    （2026-09-09 監査: 30thのラバーマット付きデッキセットの抽選が落ちていた）。"""
    for kw in config.SUPPLY_NOISE_KEYWORDS:
        for m in re.finditer(re.escape(kw), line):
            if not re.match(r"(付|つき|同梱)", line[m.end(): m.end() + 2]):
                return True
    return False


def _deck_supply_rule(line, is_pokeca, today):
    """商品ライフサイクル規則（サプライ/デッキ系）を適用する。
    返り値: (excluded, forced)。excluded=True なら通知対象外。
    forced=True はポケカのデッキ系初回販売（明示的に通知対象）。
    差分通知と朝のダイジェストの両方で共通に使う。"""
    if _is_supply_noise(line):
        return True, False
    # スターターセット/構築デッキ系（「スタートデッキ」は別扱いで常に許可）
    if any(kw in line for kw in config.DECK_PRODUCT_KEYWORDS) and "スタートデッキ" not in line:
        if not is_pokeca:
            return True, False
        if any(w in line for w in ("再販", "再入荷", "再販売")):
            return True, False  # ポケカのデッキ系は初回販売のみ（再販は転売不可）
        dates = _upcoming_dates(line, today) if today else []
        if not dates:
            # 日付のない行は「予約/抽選/受付開始」の告知に限り初回販売期とみなす
            # （2026-09-09 監査: 公式infoの「デッキビルドBOX 予約受付開始」が日付なしで落ちていた。
            # 「好評発売中」のような継続状態の行は従来どおり通知しない）
            if any(w in line for w in ("予約", "抽選", "受付開始")):
                return False, True
            return True, False
        if not any(abs((d - today).days) <= config.INITIAL_SALE_WINDOW_DAYS for d in dates):
            return True, False  # 発売前後の初回販売期でなければ通知しない
        return False, True
    return False, False


def passes_require_keywords(line, require):
    """require が指定された監視項目では、その語のいずれかを含む行だけを通知対象にする。

    カードラボ等の実店舗ページは1ページに全TCG（遊戯王/ヴァイス/ワンピ…）の告知や
    イベント・定型文が混在する。行動語（予約/抽選/販売…）だけで絞ると
    「ヴァイスシュヴァルツ交流会」「商品ご予約について」のような無関係な行まで通ってしまう。
    → 対象商材の名前を含むことを追加条件にして、通知が来たら本物という精度を守る。
    require が空/未指定なら常に True（従来動作）。
    """
    if not require:
        return True
    return any(kw in line for kw in require)


def _is_actionable_line(line, today=None, strict=False, is_pokeca=False):
    """追加行が「通知する価値のある実質情報」か判定する。
    (1)再販/入荷/抽選/予約/コラボ/発売等の行動語を含む、または
    (2)近い将来の日付＋日付以外の中身を含む（strict=Falseの場合のみ）
    かつ、定型文・ナビ断片でなく、商品ライフサイクル規則に適合すること:
      - サプライ類: 常に通知しない
      - スターターセット/構築デッキ系: ポケカのみ初回販売（発売前後60日の告知）だけ通知。
        再販は通知しない。ポケカ以外は通知しない
      - スタートデッキ: 再販でも人気のため常に通知対象（例外）"""
    if not (config.DIGEST_LINE_MINLEN <= len(line) <= 120):
        return False
    # サイドバーのカテゴリ件数「抽選販売・予約 (46)」はナビ断片（2026-09-09 監査）
    if re.match(r"^[^\d]{2,20}\s*[（(]\d+[)）]$", line.strip()):
        return False
    if any(mk in line for mk in config.DIGEST_EXCLUDE_MARKERS):
        return False
    # カード以外の商品（雑誌/書籍/フィギュア等のカテゴリタグ）は対象外。
    # ただしカード付録（Vジャンプのプロモカード等）を含むものは購入対象になり得るため通知する。
    if any(tag in line for tag in config.NON_CARD_CATEGORY_TAGS):
        if not (("カード" in line) and any(mk in line for mk in config.MAGAZINE_CARD_MARKERS)):
            return False
    # 「販売継続中」等は状態の継続であって新しいチャンスではない
    if any(mk in line for mk in config.STATUS_QUO_MARKERS):
        return False
    excluded, forced = _deck_supply_rule(line, is_pokeca, today)
    if excluded:
        return False
    if forced:
        return True  # ポケカのデッキ系・初回販売期（規則で明示的に許可）
    if any(kw in line for kw in config.NOTIFY_ACTION_KEYWORDS):
        return True
    if strict:
        return False
    if today is not None:
        dates = _upcoming_dates(line, today)
        if any(0 <= (d - today).days <= config.OPPORTUNITY_WINDOW_DAYS for d in dates):
            # 日付以外の中身があること（「2026.7.10」のような日付セル単独の行は
            # 情報ゼロなので通知しない。中身は隣の題名行が別途拾われる）
            residue = re.sub(
                r"20\d\d[年./]\s*\d{1,2}[月./]\s*\d{1,2}日?|\d{1,2}月\s*\d{1,2}日"
                r"|[〜~（）()（）\s、。・!！?？-]",
                "", line)
            return len(residue) >= 8
    return False


def _expired_pokeca_titles(prev, new_state, today):
    """ポケカ公式APIの商品リストから「発売から1年半超」の商品タイトル（正規化済み）を返す。
    これらの商品は再販されないため、言及する行を通知から除外する。"""
    officials = new_state.get("pokecard_official") or prev.get("pokecard_official") or []
    out = []
    for key in officials:
        parts = key.split("|")
        if len(parts) != 2:
            continue
        title, rdate = parts
        m = re.search(r"(20\d\d)年\s*(\d{1,2})月\s*(\d{1,2})日", rdate)
        if not m:
            continue
        from datetime import date
        try:
            released = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if (today - released).days > config.MAX_PRODUCT_AGE_DAYS:
            nt = _normalize_box_name(title)
            # 「拡張パック」等のカテゴリ接頭辞を剥がしてコア名で照合できるようにする
            # （通知行は「超電ブレイカーBOX再販」のようにコア名だけで言及されるため）
            for pre in ("拡張パックデラックス", "強化拡張パック", "拡張パック",
                        "ハイクラスパック", "スペシャルカードセット", "スペシャルBOX",
                        "デッキビルドBOX", "プレミアムトレーナーボックス"):
                if nt.startswith(pre):
                    nt = nt[len(pre):]
                    break
            if len(nt) >= 5:
                out.append(nt)
    return out


def _mentions_expired(line, expired_titles):
    """行が「1年半超で再販の来ない商品」に言及しているか。"""
    nl = _normalize_box_name(line)
    return any(t in nl for t in expired_titles)


def _item_short_name(item):
    """監視名から商品コア名を取り出す（検索クエリ・ダイジェスト表示用）。"""
    short = item.get("name", "")
    for w in (" 抽選/再販まとめ（anime-matsuri）", " 抽選/予約まとめ（anime-matsuri）",
              " 再販告知まとめ（anime-matsuri）", " 再販集約", "（横断）", "（在庫）",
              "（楽天ブックス）", "（東映ストア）"):
        short = short.replace(w, "")
    return short.strip()


def _upcoming_dates(text, today):
    """テキスト中の日付(202X年M月D日 / M月D日 / 202X/M/D / 202X.M.D)を解決して返す。
    年つき日付はその年で確定。年なしのM月D日は「今日から180日以上過去なら来年」と
    解釈する（年跨ぎ対応）。年つき部分を除外してから年なしを探す
    （「2024年7月19日」の「7月19日」を今年と誤解釈しないため）。"""
    from datetime import date
    found = []
    for y, m, d in re.findall(r"(20\d\d)年\s*(\d{1,2})月\s*(\d{1,2})日", text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    stripped = re.sub(r"20\d\d年\s*\d{1,2}月\s*\d{1,2}日", "", text)
    for m, d in re.findall(r"(\d{1,2})月\s*(\d{1,2})日", stripped):
        try:
            dt = date(today.year, int(m), int(d))
        except ValueError:
            continue
        if (today - dt).days > 180:
            dt = date(today.year + 1, int(m), int(d))
        elif (dt - today).days > 180:
            # 逆方向の年跨ぎ: 1月に見た「12月26日に発売」は昨年（2026-09-09 監査で追加。
            # 以前は11ヶ月先の未来と解釈され、古い発売告知が「近い将来」扱いになり得た）
            dt = date(today.year - 1, int(m), int(d))
        found.append(dt)
    for y, m, d in re.findall(r"(202\d)[/.](\d{1,2})[/.](\d{1,2})", text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found

_DATE_SPAN_RE = re.compile(
    r"(?:(20\d\d)\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"|(202\d)[/.](\d{1,2})[/.](\d{1,2})")
_RANGE_SEP_RE = re.compile(r"^\s*(?:[（(][^)）]{1,4}[)）])?\s*[〜~～\-－ー]\s*")
# 日付の直前の文脈で「応募期間ではない日付」を示す語（発売日・当選発表・注文期限・お届け）
_NOT_APPLY_LABELS = ("発売", "当選", "発表", "注文", "お届け", "出荷", "配送")
# 「応募期間」を示す語
_APPLY_LABELS = ("応募", "受付", "申込", "抽選期間", "エントリー", "抽選")


def _date_spans(text, today):
    """テキスト中の日付を (開始位置, 終了位置, date) で返す。年なしは _upcoming_dates と同じ
    年跨ぎ規則で解決する。"""
    from datetime import date
    out = []
    for m in _DATE_SPAN_RE.finditer(text):
        try:
            if m.group(2):
                y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
                if y:
                    dt = date(int(y), mo, d)
                else:
                    dt = date(today.year, mo, d)
                    if (today - dt).days > 180:
                        dt = date(today.year + 1, mo, d)
                    elif (dt - today).days > 180:
                        dt = date(today.year - 1, mo, d)
            else:
                dt = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            continue
        out.append((m.start(), m.end(), dt))
    return out


def _nearest_label(ctx):
    """直前文脈（約30字）で日付に最も近いラベル語を返す: 'apply' / 'not_apply' / None。
    「当選発表 9月12日 応募受付 9月1日〜」のように複数ラベルがある場合は近い方が勝つ。"""
    best = (-1, None)
    for w in _NOT_APPLY_LABELS:
        p = ctx.rfind(w)
        if p > best[0]:
            best = (p, "not_apply")
    for w in _APPLY_LABELS:
        p = ctx.rfind(w)
        if p > best[0]:
            best = (p, "apply")
    return best[1]


def _apply_period(line, today, window_days=60):
    """行から応募期間 (apply_start, apply_end) を推定する。どちらも date | None。
    2026-09-09 監査: 以前は「行内の最大日付=締切」だったため、発売日・当選発表日・注文期限が
    締切として台帳に登録されていた。方針:
      - 「A〜B」の範囲で直前文脈が 応募/受付/申込/抽選期間/エントリー → 最優先で採用
      - 単独日付は直前約30字のラベルが 発売/当選/発表/注文/お届け/出荷/配送 なら除外。
        直後が「発売」（「9月16日発売」）の単独日付も除外
      - 残った日付が無ければ (None, None)（発売日しか無い行は締切が分からない＝登録しない）"""
    spans = _date_spans(line, today)
    if not spans:
        return None, None

    def in_window(d):
        return 0 <= (d - today).days <= window_days

    ranges, used = [], set()
    for i in range(len(spans) - 1):
        s1, e1, d1 = spans[i]
        s2, e2, d2 = spans[i + 1]
        if _RANGE_SEP_RE.match(line[e1:s2]) and d1 <= d2:
            ranges.append((s1, d1, d2))
            used.update((i, i + 1))
    apply_ranges, neutral_ranges = [], []
    for s1, d1, d2 in ranges:
        label = _nearest_label(line[max(0, s1 - 30): s1])
        if label == "not_apply":
            continue
        (apply_ranges if label == "apply" else neutral_ranges).append((d1, d2))
    for d1, d2 in apply_ranges + neutral_ranges:
        if in_window(d2):
            return (d1 if in_window(d1) else None), d2
    singles = []
    for i, (s, e, d) in enumerate(spans):
        if i in used:
            continue
        if _nearest_label(line[max(0, s - 30): s]) == "not_apply":
            continue
        if re.match(r"(?:[（(][^)）]{1,4}[)）])?\s*(?:に|より|から|頃)?\s*発売", line[e: e + 10]):
            continue
        if in_window(d):
            singles.append(d)
    if not singles:
        return None, None
    return (min(singles) if len(singles) > 1 else None), max(singles)


def extract_lottery_candidate(line, item, today, store_hints):
    """検知行から「応募台帳に登録できる構造化された抽選候補」を抽出する。
    条件（すべて必須・精度優先）:
      - 行に店舗名（store_hints のキー）がある
      - 抽選/応募/予約/先着 のいずれかを含む
      - 応募期間の締切と解釈できる今日以降の日付がある（_apply_period。発売日・当選発表日・
        注文期限は締切に採らない。2026-09-09 監査）
    返り値: dict（channel/product/apply_start/apply_end） | None。
    商品名は監視アイテム名から取る（行の断片より確実）。"""
    channel = next((name for name in store_hints if name in line), None)
    if not channel:
        return None
    if not any(kw in line for kw in ("抽選", "応募", "予約", "先着")):
        return None
    start, end = _apply_period(line, today)
    if not end:
        return None
    return {
        "channel": channel,
        "product": _item_short_name(item),
        "apply_start": start.isoformat() if start else None,
        "apply_end": end.isoformat(),
    }

