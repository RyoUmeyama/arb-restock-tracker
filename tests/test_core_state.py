"""2026-09-09 監査（網羅レビュー）: 状態管理・通知順序・方式分割の回帰テスト。

対象は check_stock.py の中核。すべてネットワークを使わない（http_get/fetch を差し替える）。
"""
import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_stock as cs
import config


def _ok_resp(text):
    import types
    r = types.SimpleNamespace()
    r.content = text.encode("utf-8")
    r.text = text
    r.json = lambda: json.loads(text)
    return r


class TestHoldPrevOnFailure(unittest.TestCase):
    """H1: 取得失敗時に既定値（[]/False）を書くと cache miss 直後の失敗が「空の基準」になり、
    次パスで全件が新着として偽通知される。"""

    def test_hold_prev_writes_nothing_when_key_missing(self):
        new_state = {}
        cs._hold_prev({}, new_state, "k")
        self.assertNotIn("k", new_state)

    def test_hold_prev_copies_when_present(self):
        new_state = {}
        cs._hold_prev({"k": ["a"]}, new_state, "k")
        self.assertEqual(new_state["k"], ["a"])

    def test_pokecen_failure_then_success_is_first_run(self):
        item = {"name": "ポケセン", "method": "pokecen_news_ids", "url": "https://x/",
                "key": "pk", "retail_price": 0}
        calls = {"n": 0}

        def fake_get(url, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("timeout")
            return _ok_resp('<a href="/news/?id=20260904">a</a><a href="/news/?id=20260821">b</a>')

        orig = cs.http_get
        cs.http_get = fake_get
        try:
            s1, alerts1, health = {}, [], {"ok": [], "fail": [], "suppressed": 0}
            cs._process_item(item, {}, s1, alerts1, health, [])
            self.assertNotIn("pk", s1, "失敗パスは基準を書かない")
            s2, alerts2, health2 = {}, [], {"ok": [], "fail": [], "suppressed": 0}
            cs._process_item(item, s1, s2, alerts2, health2, [])
            self.assertEqual(alerts2, [], "復旧パスは初回扱い＝通知しない")
            self.assertEqual(s2["pk"], ["20260904", "20260821"])
        finally:
            cs.http_get = orig

    def test_stock_failure_then_in_stock_is_first_run(self):
        item = {"name": "在庫", "method": "toei_stock_status", "url": "https://x/",
                "key": "st", "retail_price": 1000}
        orig = cs.check_item
        seq = iter([(False, False, ""), (True, True, "在庫あり")])
        cs.check_item = lambda it: next(seq)
        orig_sleep = cs.time.sleep
        cs.time.sleep = lambda s: None
        try:
            s1, a1 = {}, []
            cs._process_item(item, {}, s1, a1, {"ok": [], "fail": [], "suppressed": 0}, [])
            self.assertNotIn("st", s1)
            s2, a2 = {}, []
            cs._process_item(item, s1, s2, a2, {"ok": [], "fail": [], "suppressed": 0}, [])
            self.assertEqual(a2, [], "cache miss→失敗→在庫あり で「復活！」を出さない")
        finally:
            cs.check_item = orig
            cs.time.sleep = orig_sleep


class TestPokecardAllCategoriesRequired(unittest.TestCase):
    """H3: 一部カテゴリの失敗を ok 扱いにすると、復旧時に旧商品が「新商品」になる。"""

    def test_partial_failure_is_not_ok(self):
        def fake_get(url, params=None, **kw):
            if params["productType"] == "peripheral":
                raise RuntimeError("500")
            return _ok_resp(json.dumps({"products": [
                {"productTitle": "拡張パック「X」", "releaseDate": "2026年 10月", "priceTxt": "", "link_detailPage": ""}]}))
        orig, orig_sleep = cs.http_get, cs.time.sleep
        cs.http_get, cs.time.sleep = fake_get, (lambda s: None)
        try:
            products, ok = cs.fetch_pokecard_new_products()
            self.assertFalse(ok)
            self.assertTrue(products, "取れたカテゴリのデータ自体は返す")
        finally:
            cs.http_get, cs.time.sleep = orig, orig_sleep


class TestSaveStateAtomic(unittest.TestCase):
    def test_writes_via_tmp_and_drops_private_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            orig = config.STATE_FILE
            config.STATE_FILE = os.path.join(d, "state.json")
            try:
                cs.save_state({"a": 1, "_tmp": 2})
                self.assertEqual(json.load(open(config.STATE_FILE)), {"a": 1})
                self.assertFalse(os.path.exists(config.STATE_FILE + ".tmp"))
            finally:
                config.STATE_FILE = orig


class TestCarryKeysNoNone(unittest.TestCase):
    """M1: prev に無い carry キーを None で保存すると「初回」判定（キー不在）が壊れる。"""

    def test_missing_carry_key_not_written(self):
        new_state, alerts = {}, []
        health = {"ok": [], "fail": [], "suppressed": 0}
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
        prev = {"last_heartbeat": today}  # 送信済み扱い → carry だけ実行して return（ネットワークに出ない）
        cs.append_heartbeat(prev, new_state, alerts, health)
        self.assertNotIn("am_pages_seen", new_state)
        self.assertNotIn("auto_watch", new_state)
        self.assertEqual(new_state.get("last_heartbeat"), today)


class TestPokecenPeriodColonTime(unittest.TestCase):
    """M2: 「12:00〜」表記（8/3の実記事の書式）で応募期間が取れなかった。"""

    def test_colon_time_period(self):
        text = "応募受付期間 8月10日（月）12:00～8月14日（金）16:59\n抽選です"
        c = cs._pokecen_lottery_candidate("抽選販売のお知らせ", text, date(2026, 8, 3))
        self.assertIsNotNone(c)
        self.assertEqual((c["apply_start"], c["apply_end"]), ("2026-08-10", "2026-08-14"))


class TestPokecenRetryHandling(unittest.TestCase):
    """M4: 枠外（6件目以降）を未検査のまま既知化しない／恒久404は再試行上限で諦める。"""

    def _item(self):
        return {"name": "ポケセン", "method": "pokecen_news_ids", "url": "https://x/",
                "key": "pk", "retail_price": 0, "require_keywords": ["抽選"]}

    def test_overflow_ids_are_retried_next_pass(self):
        ids = [f"2026090{i}" for i in range(1, 8)]  # 7件
        top = "".join(f'<a href="/news/?id={i}">x</a>' for i in ids)

        def fake_get(url, **kw):
            if url == "https://x/":
                return _ok_resp(top)
            return _ok_resp("<h1>t</h1><main>本文</main>")
        orig, orig_sleep = cs.http_get, cs.time.sleep
        cs.http_get, cs.time.sleep = fake_get, (lambda s: None)
        try:
            new_state = {}
            cs._process_item(self._item(), {"pk": []}, new_state, [],
                             {"ok": [], "fail": [], "suppressed": 0}, [])
            known = set(new_state["pk"])
            self.assertEqual(len(known), cs.POKECEN_ARTICLES_PER_PASS)
            self.assertTrue(all(i in known for i in sorted(ids, reverse=True)[:5]))
        finally:
            cs.http_get, cs.time.sleep = orig, orig_sleep

    def test_permanent_fetch_failure_gives_up_after_limit(self):
        def fake_get(url, **kw):
            if url == "https://x/":
                return _ok_resp('<a href="/news/?id=20260901">x</a>')
            raise RuntimeError("404")
        orig, orig_sleep = cs.http_get, cs.time.sleep
        cs.http_get, cs.time.sleep = fake_get, (lambda s: None)
        try:
            prev = {"pk": [], "pokecen_fetch_fail": {"20260901": cs.POKECEN_FETCH_MAX_RETRY - 1}}
            new_state = {}
            cs._process_item(self._item(), prev, new_state, [],
                             {"ok": [], "fail": [], "suppressed": 0}, [])
            self.assertIn("20260901", new_state["pk"], "上限到達で既知化して枠を空ける")
            reasons = [e.get("reason") for e in new_state.get("suppressed_log", [])]
            self.assertIn("本文取得の再試行上限", reasons)
            # 途中（1回目）なら既知化しない
            new_state2 = {}
            cs._process_item(self._item(), {"pk": []}, new_state2, [],
                             {"ok": [], "fail": [], "suppressed": 0}, [])
            self.assertNotIn("20260901", new_state2["pk"])
            self.assertEqual(new_state2["pokecen_fetch_fail"], {"20260901": 1})
        finally:
            cs.http_get, cs.time.sleep = orig, orig_sleep


class TestNotifyBeforeSave(unittest.TestCase):
    """H2: 通知失敗時に sys.exit せず False を返し、呼び出し側が保存を見送る。"""

    def test_notify_returns_false_when_both_channels_fail(self):
        orig_m, orig_w = cs._notify_email, cs._notify_webhook
        cs._notify_email = lambda *a: False
        cs._notify_webhook = lambda *a: False
        try:
            item = {"name": "x", "url": "https://x/", "retail_price": 0}
            self.assertFalse(cs.notify([(item, "d", "info")]))
        finally:
            cs._notify_email, cs._notify_webhook = orig_m, orig_w


class TestXPostOnlyForStock(unittest.TestCase):
    """M6: ヘルスレポート等の info 通知に X 投稿ブロックを付けない。"""

    def test_info_alerts_not_passed_to_post_block(self):
        captured = {}
        orig = cs.build_post_block

        def fake(norm, affiliate_tag=None):
            captured["kinds"] = [a[2] for a in norm]
            return []
        cs.build_post_block = fake
        try:
            hb = {"name": "📊 日次ヘルスレポート", "url": "https://x/", "retail_price": 0}
            st = {"name": "在庫", "url": "https://y/", "retail_price": 100}
            cs.build_messages([(hb, "\n・a", "info"), (st, "在庫あり", "stock")])
            self.assertEqual(captured["kinds"], ["stock"])
        finally:
            cs.build_post_block = orig


class TestPageSignatureCap(unittest.TestCase):
    """L1: sig は保存する行集合（上限適用後）から計算する。"""

    def test_sig_uses_capped_lines(self):
        html = "<html><body>" + "".join(
            f"<p>再販 商品{i} 9月{(i % 28) + 1}日</p>" for i in range(10)) + "</body></html>"
        orig_fetch, orig_keep = cs.fetch, config.PAGE_LINES_KEEP
        cs.fetch = lambda url, enc: html
        config.PAGE_LINES_KEEP = 3
        try:
            item = {"name": "t", "url": "https://x/", "key": "k"}
            sig, lines, ok, _ = cs.compute_page_signature(item)
            self.assertTrue(ok)
            self.assertEqual(len(lines), 3)
            import hashlib
            expect = hashlib.sha256("\n".join(sorted(set(lines))).encode("utf-8")).hexdigest()
            self.assertEqual(sig, expect)
        finally:
            cs.fetch, config.PAGE_LINES_KEEP = orig_fetch, orig_keep


class TestAutoWatchIncluded(unittest.TestCase):
    """M3: ダイジェスト・候補提案・カレンダーが動的監視を含めて見る。"""

    def test_all_watch_items_includes_auto(self):
        orig = cs.auto_watch_items
        cs.auto_watch_items = lambda prev: [{"name": "動的 新弾まとめ", "method": "page_update",
                                            "url": "https://anime-matsuri.com/new/", "key": "auto_new"}]
        try:
            names = [i["name"] for i in cs._all_watch_items({})]
            self.assertIn("動的 新弾まとめ", names)
            self.assertEqual(len(names), len(config.WATCH_ITEMS) + 1)
        finally:
            cs.auto_watch_items = orig

    def test_suggest_excludes_auto_watched(self):
        orig = cs.auto_watch_items
        cs.auto_watch_items = lambda prev: [{"name": "ポケカ ゼロの島 抽選/再販まとめ（anime-matsuri）",
                                            "method": "page_update", "url": "https://a/", "key": "auto_x"}]
        try:
            cands = cs.suggest_watch_candidates({"ゼロの島 BOX": 12000}, ["拡張パック「ゼロの島」"], prev={})
            self.assertEqual(cands, [])
        finally:
            cs.auto_watch_items = orig


class TestMethodDispatch(unittest.TestCase):
    def test_known_methods_registered(self):
        for m in ("pokecen_news_ids", "pokecard_official_list", "onepiece_news", "page_update"):
            self.assertIn(m, cs.METHOD_HANDLERS)

    def test_unknown_method_falls_back_to_stock(self):
        self.assertIs(cs.METHOD_HANDLERS.get("toei_stock_status", cs._process_stock), cs._process_stock)


class TestDetectedLotteriesSummary(unittest.TestCase):
    def test_summary_counts_and_last_date(self):
        import tempfile
        orig = cs.DETECTED_LOTTERIES_FILE
        with tempfile.TemporaryDirectory() as d:
            cs.DETECTED_LOTTERIES_FILE = os.path.join(d, "dl.json")
            try:
                self.assertIn("書き出しなし", cs._detected_lotteries_summary())
                json.dump([{"id": "a", "detected_at": "2026-09-04"}, {"id": "b", "detected_at": "2026-09-09"}],
                          open(cs.DETECTED_LOTTERIES_FILE, "w"))
                s = cs._detected_lotteries_summary()
                self.assertIn("2件", s)
                self.assertIn("2026-09-09", s)
            finally:
                cs.DETECTED_LOTTERIES_FILE = orig


class TestDiscoveryFailureRewarn(unittest.TestCase):
    def test_warns_on_day3_and_weekly_after(self):
        def run(streak_prev):
            alerts, ns = [], {}
            cs._track_discovery_failure({"am_discovery_fail_streak": streak_prev}, ns, alerts)
            return bool(alerts)
        self.assertFalse(run(1))
        self.assertTrue(run(2))    # 3日目
        self.assertFalse(run(3))
        self.assertTrue(run(9))    # 10日目
        self.assertTrue(run(16))   # 17日目


if __name__ == "__main__":
    unittest.main()
