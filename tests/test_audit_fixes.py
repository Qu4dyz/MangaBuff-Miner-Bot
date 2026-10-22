import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import CONFIG
from engine import MangaMinerBot
from tg_notifier import TelegramNotifier


def bot():
    b = object.__new__(MangaMinerBot)
    b.log, b.notifier, b.session = Mock(), Mock(), Mock()
    b._alive = lambda: True
    b.auto_upgrade_pickaxe = Mock()
    return b


class MiningTests(unittest.TestCase):
    def step(self, energy, emptied):
        b = bot()
        sel = CONFIG["selectors"]
        b.session.get.return_value = SimpleNamespace(
            status_code=200, text=f'<div class="{sel["energy_class"]}">{energy}</div>')
        b._run_mining = Mock(return_value=emptied)
        out = {"energy": 0}
        b._mining_step("2026-10-18", out)
        return b, out

    def test_small_leftover_energy_is_dug(self):
        b, out = self.step(14, True)
        b._run_mining.assert_called_once()
        self.assertEqual(b._energy_exhausted_date, "2026-10-18")

    def test_failed_dig_is_not_marked_done_for_the_day(self):
        b, out = self.step(80, False)
        self.assertFalse(out["mined"])
        self.assertEqual(out["energy"], 80)
        self.assertIsNone(getattr(b, "_energy_exhausted_date", None))


class ClubTests(unittest.TestCase):
    def test_timeout_does_not_skip_club_for_the_day(self):
        settings = {}
        b = bot()
        b.session.get.side_effect = TimeoutError("slow")
        with patch("club.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)), \
                patch("club.DataManager.set_setting", side_effect=settings.__setitem__):
            b.run_club_cycle()
        self.assertNotIn("club_none_date", settings)


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.n = object.__new__(TelegramNotifier)
        self.n.get_credentials = lambda: ("t", "1", True)

    def test_successful_retry_after_429_counts_as_sent(self):
        self.n._post = Mock(side_effect=[
            SimpleNamespace(status_code=429, json=lambda: {"parameters": {"retry_after": 0}}),
            SimpleNamespace(status_code=200)])
        with patch("tg_notifier.time.sleep"):
            self.assertEqual(self.n._send_http("hi"), (True, "OK"))

    def test_names_are_escaped(self):
        self.n.send_message = Mock()
        self.n.notify_trade_status("1", "<Hero> & Co", "accepted")
        self.assertIn("&lt;Hero&gt; &amp; Co", self.n.send_message.call_args.args[0])


class StartWhileStoppingTests(unittest.TestCase):
    def test_second_bot_is_not_started_while_the_first_stops(self):
        from buffy.api import BuffyApi

        api = BuffyApi()
        api._status, api._spawn_bot = "stopping", Mock()
        with patch("buffy.api.DataManager.get_credentials", return_value=("a@b.c", "x")):
            self.assertFalse(api.start()["ok"])
        api._spawn_bot.assert_not_called()




class DryReadingTests(unittest.TestCase):
    def test_failed_reads_do_not_switch_cards_off(self):
        settings = {}
        b = bot()
        b.get_reading_stats = Mock(return_value={'chapters_read': 75, 'chapters_max': 75, 'cards_found': 0,
                                                 'cards_max': 10, 'card_ready': True, 'card_cooldown': ''})
        b.read_manga_chapters = Mock(side_effect=lambda **_: setattr(b, '_last_read_count', 0))
        out = {'card_wait_sec': 0, 'chapters_wait_sec': 0, 'ch_read': 0, 'ch_max': 75, 'cards_found': 0, 'cards_max': 10}
        with patch('engine.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)),                 patch('engine.DataManager.set_setting', side_effect=settings.__setitem__):
            for _ in range(4):
                b._reading_step(dict(out))
        self.assertNotIn('bonus_cards_off_date', settings)


class RecheckTests(unittest.TestCase):
    def test_manual_runs_wait_for_a_live_cup_fight(self):
        import collections
        b = bot()
        b._manual_queue = collections.deque(["reading"])
        b.running, b._cup_fight_live = True, True
        b.run_manual_module = Mock()
        b._drain_manual_queue()
        b.run_manual_module.assert_not_called()

    def test_nothing_runs_after_stop(self):
        import threading
        b = bot()
        b._module_abort = threading.Event()
        b.running = True
        b.stop()
        fn = Mock()
        b._run_module("mining", fn, 60)
        fn.assert_not_called()
        b.run_daemon()  # Stop before the thread started: returns at once
        self.assertFalse(b.running)

    def test_anti_bot_403_is_not_an_empty_mine(self):
        import threading
        b = bot()
        b._module_abort, b.running = threading.Event(), True
        del b._alive
        b.API_URL = "https://mangabuff.ru/mine/hit"
        b.session.min_request_interval = b.session.max_request_jitter = 0
        b.update_stats = b.update_progress = Mock()
        b.session.post.return_value = SimpleNamespace(
            status_code=403, text="<html><title>DDoS-Guard</title>check.js</html>", headers={}, url="https://mangabuff.ru/mine/hit")
        with patch("engine.is_anti_bot_page", return_value="ddos_guard"):
            self.assertFalse(b._run_mining())

    def test_login_page_is_not_no_club(self):
        settings = {}
        b = bot()
        b.session.get.return_value = SimpleNamespace(status_code=200, url="https://mangabuff.ru/login",
                                                     text='<input name="password">')
        with patch("club.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("club.DataManager.set_setting", side_effect=settings.__setitem__):
            b.run_club_cycle()
        self.assertNotIn("club_none_date", settings)

    def test_reset_right_after_midnight_is_today(self):
        from datetime import datetime, timedelta, timezone
        from utils import get_seconds_until_midnight_msk
        fake = datetime(2026, 10, 18, 0, 0, 5, tzinfo=timezone(timedelta(hours=3)))
        with patch("datetime.datetime") as dt:
            dt.now.return_value = fake
            self.assertEqual(get_seconds_until_midnight_msk(), 60)  # 5 s, floored to 60

    def test_account_cannot_change_during_manual_run(self):
        from buffy.api import BuffyApi
        api = BuffyApi()
        api._manual_bot = Mock()
        self.assertFalse(api.check_login("a@b.c", "x")["ok"])


class ThirdCheckTests(unittest.TestCase):
    def _bot(self):
        import collections, threading
        b = bot()
        b._module_abort, b._heartbeat_lock = threading.Event(), threading.Lock()
        b._manual_queue, b.manual_callback, b.fatal_exit = collections.deque(), None, Mock()
        b.running = True
        b.refresh_live_panel = Mock()
        return b

    def test_stop_during_manual_run_cancels_the_next_module(self):
        b = self._bot()
        b._manual_queue.append("club")
        b._manual_step = lambda name: b.stop() or "ok"   # user presses Stop mid-run
        fn = Mock()
        b._run_module("daily", fn, 60)
        fn.assert_not_called()
        b.refresh_live_panel.assert_not_called()          # no re-login after Stop


class FourthCheckTests(unittest.TestCase):
    def test_failed_telegram_send_is_retried_but_bad_request_is_not(self):
        n = object.__new__(TelegramNotifier)
        send = Mock(side_effect=[(False, "network failed"), (True, "OK")])
        with patch("tg_notifier.time.sleep"):
            self.assertEqual(n._deliver(send), (True, "OK"))
        bad = Mock(return_value=(False, "HTTP 400: can't parse entities"))
        n._deliver(bad)
        self.assertEqual(bad.call_count, 1)

    def test_other_account_does_not_inherit_old_state(self):
        import json, os, tempfile
        import data_management as dm
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "user_data.json")
            with open(data, "w", encoding="utf-8") as f:
                json.dump({"email": "old@x.ru", "password": "p", "club_slug": "old-club", "tg_token": "keep"}, f)
            open(os.path.join(d, "reading_state.json"), "w").close()
            open(os.path.join(d, "arena_management.json"), "w").close()
            with patch.object(dm, "DATA_FILE", data), patch.object(dm, "SESSION_FILE", os.path.join(d, "s.json")):
                dm.DataManager.clear_credentials()
                dm.DataManager.switch_account("new@x.ru")
                saved = dm.DataManager.load_json(data)
            self.assertNotIn("club_slug", saved)  # removed, so get_setting(key, default) gets the default
            self.assertFalse(os.path.exists(os.path.join(d, "arena_management.json")))
            self.assertEqual(saved["tg_token"], "keep")
            self.assertFalse(os.path.exists(os.path.join(d, "reading_state.json")))


class UserReviewTests(unittest.TestCase):
    def test_manual_daily_does_not_claim_a_failed_visit_and_reward(self):
        settings = {}
        b = bot()
        b.ping_daily_visit = Mock(return_value=False)
        b._find_active_claim_url = Mock(return_value="https://mangabuff.ru/balance/claim")
        b.claim_daily_reward = Mock()
        b.daily_reward_claimed = False
        b._msk_now = lambda: __import__("datetime").datetime(2026, 10, 18, 12)
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)):
            summary = b._manual_step("daily")
        self.assertIn("не засчитался", summary)
        self.assertEqual(type(summary).__name__, "ManualFailed")
        self.assertIn("не получилось", summary)

    def test_proxy_check_does_not_call_a_protection_page_success(self):
        from buffy.api import BuffyApi
        page = SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, content=b"<html>",
                               text="<html><title>DDoS-Guard</title>checking your browser before accessing</html>")
        with patch("buffy.api.requests.get", return_value=page):
            self.assertFalse(BuffyApi().test_proxy("")["ok"])

    def test_tray_offers_stop_during_a_manual_run(self):
        from buffy.api import BuffyApi
        api = BuffyApi()
        api._manual_bot = Mock()
        self.assertTrue(api.is_running())


class ManualDailyTests(unittest.TestCase):
    def test_unreachable_rewards_page_is_not_reported_as_nothing_to_claim(self):
        b = bot()
        b.ping_daily_visit = Mock(return_value=True)
        b.session.get.return_value = SimpleNamespace(status_code=500, text="")
        b._msk_now = lambda: __import__("datetime").datetime(2026, 10, 18, 12)
        with patch("engine.DataManager.get_setting", return_value=None):
            summary = b._manual_step("daily")
        self.assertEqual(type(summary).__name__, "ManualFailed")
        self.assertIn("не открылась", summary)


class ManualResultTests(unittest.TestCase):
    def test_failed_manual_run_is_reported_as_error(self):
        from engine import ManualFailed
        import collections, threading
        b = bot()
        b._module_abort, b._heartbeat_lock = threading.Event(), threading.Lock()
        b._manual_queue, b.fatal_exit, b.running = collections.deque(), Mock(), True
        b.refresh_live_panel = Mock()
        b.manual_callback = Mock()
        b._manual_step = lambda name: ManualFailed("визит не засчитался")
        b.run_manual_module("daily")
        self.assertEqual(b.manual_callback.call_args.args[1], "error")


class DeepCheckTests(unittest.TestCase):
    def test_manual_run_with_no_site_answer_is_an_error(self):
        import collections, threading, time as t
        b = bot()
        b._module_abort, b._heartbeat_lock = threading.Event(), threading.Lock()
        b._manual_queue, b.fatal_exit, b.running = collections.deque(), Mock(), True
        b.refresh_live_panel, b.manual_callback = Mock(), Mock()
        b.session.last_ok_at = t.time() - 60

        def step(name):
            b.session._last_request_at = t.time()   # a request went out, nothing came back OK
            return "Башня проверена"
        b._manual_step = step
        self.assertIn("сайт не ответил", b.run_manual_module("tower"))
        self.assertEqual(b.manual_callback.call_args.args[1], "error")

    def test_no_manual_run_while_bot_restarts(self):
        from buffy.api import BuffyApi
        api = BuffyApi()
        api._status = "restarting"
        with patch("buffy.api.DataManager.get_credentials", return_value=("a@b.c", "x")),                 patch("buffy.api.DataManager.get_setting", return_value=True):
            self.assertFalse(api.run_module("reading")["ok"])

    def test_stop_during_request_spacing_cancels_the_request(self):
        import threading, time as t
        from http_session import GuardedSession
        cancelled = threading.Event()
        raw = Mock()
        g = GuardedSession(raw, should_abort=cancelled.is_set)
        g._last_request_mono, g._last_request_at = t.monotonic(), t.time()
        with patch("http_session.time.sleep", side_effect=lambda s: cancelled.set()):
            with self.assertRaises(RuntimeError):
                g.post("https://mangabuff.ru/mine/buy-strong-hit", timeout=1)
        raw.request.assert_not_called()


class PartialAnswerTests(unittest.TestCase):
    def test_ads_counter_that_did_not_load_is_not_zero_of_three(self):
        b = bot()
        b.watch_daily_ads = Mock()
        b.session.get.return_value = SimpleNamespace(status_code=500, text="")
        self.assertEqual(type(b._manual_step("ads")).__name__, "ManualFailed")

    def test_tower_page_that_did_not_load_is_not_checked(self):
        b = bot()
        b.session.get.return_value = SimpleNamespace(status_code=404, text="not found")
        with patch("engine.DataManager.get_setting", return_value=True):
            self.assertEqual(type(b._manual_step("tower")).__name__, "ManualFailed")

    def test_stop_during_proxy_retry_wait_cancels_the_retry(self):
        import threading
        from http_session import GuardedSession
        cancelled = threading.Event()
        raw = Mock()
        raw.request.side_effect = RuntimeError("CONNECT tunnel failed")
        g = GuardedSession(raw, should_abort=cancelled.is_set)
        g.PROXY_RETRY_MARKERS = ("CONNECT tunnel failed",)
        with patch("http_session.random.uniform", return_value=0.001),                 patch("http_session.time.sleep", side_effect=lambda s: cancelled.set()):
            with self.assertRaises(RuntimeError):
                g._raw_request("POST", "https://mangabuff.ru/action", timeout=1)
        self.assertEqual(raw.request.call_count, 1)


class TowerAndReplayTests(unittest.TestCase):
    def test_failed_tower_claim_is_an_error(self):
        b = bot()
        b.upgrade_tower_headquarters = Mock()
        b.csrf_token = "x"
        html = '<html><div class="mbf-abyss-tower" data-expedition-complete="1" data-expedition-active="0"></div></html>'
        b.session.get.return_value = SimpleNamespace(status_code=200, text=html)
        b.session.post.return_value = SimpleNamespace(status_code=500, text="claim failed")
        with patch("engine.DataManager.get_setting", return_value=True):
            summary = b._manual_step("tower")
        self.assertEqual(type(summary).__name__, "ManualFailed")

    def test_stop_during_antibot_pause_cancels_the_replay(self):
        import threading
        from http_session import GuardedSession
        cancelled = threading.Event()
        raw = Mock()
        raw.request.return_value = SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, content=b"<html>",
                                                   text="<html><title>DDoS-Guard</title>checking your browser before accessing</html>")
        g = GuardedSession(raw, should_abort=cancelled.is_set)
        with patch("http_session.try_refresh_ddos_guard", return_value=True),                 patch("http_session.time.sleep", side_effect=lambda s: cancelled.set()):
            with self.assertRaises(RuntimeError):
                g.post("https://mangabuff.ru/action", timeout=1)
        self.assertEqual(raw.request.call_count, 1)


class EventCandyTests(unittest.TestCase):
    def test_candy_dropped_by_reading_is_picked_up(self):
        b = bot()
        b.session.post.return_value = SimpleNamespace(status_code=200)
        settings = {"halloween_event_info": {"balance": 10}}
        b._msk_now = lambda: __import__("datetime").datetime(2026, 10, 18, 12)
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            b._take_event_drop({"token": "abc", "type": "candy"}, {"X-CSRF-TOKEN": "t"})
        self.assertEqual(settings["halloween_event_info"]["balance"], 11)
        self.assertEqual(settings["halloween_candy_day"]["n"], 1)
        url = b.session.post.call_args.args[0]
        self.assertIn("/halloween/takeCandy", url)
        self.assertEqual(b.session.post.call_args.kwargs["data"], {"token": "abc"})


class EventReadingTests(unittest.TestCase):
    def test_reads_for_candies_after_the_daily_quest_and_stops_when_dry(self):
        import datetime as dt
        settings = {"halloween_event_info": {"balance": 10}}
        b = bot()
        b._msk_now = lambda: dt.datetime(2026, 10, 18, 19)
        b.get_reading_stats = Mock(return_value={"chapters_read": 75, "chapters_max": 75, "cards_found": 10,
                                                 "cards_max": 10, "card_ready": False, "card_cooldown": ""})
        b.read_manga_chapters = Mock(side_effect=lambda **kw: setattr(b, '_last_read_count', 2))  # read, no drop
        out = {"card_wait_sec": 0, "chapters_wait_sec": 0, "ch_read": 0, "ch_max": 75, "cards_found": 0, "cards_max": 10}
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            for _ in range(8):
                o = dict(out)
                b._reading_step(o)
        self.assertEqual(b.read_manga_chapters.call_count, 6)   # no drops: gives up after 6 reads
        self.assertTrue(b.read_manga_chapters.call_args.kwargs["for_event"])
        self.assertNotIn("event_wait_sec", o)


class HalloweenReviewTests(unittest.TestCase):
    def step(self, settings, stats, read_count):
        import datetime as dt
        b = bot()
        b._msk_now = lambda: dt.datetime(2026, 10, 18, 12)
        b.get_reading_stats = Mock(return_value=stats)
        b.read_manga_chapters = Mock(side_effect=lambda **kw: setattr(b, "_last_read_count", read_count))
        out = {"card_wait_sec": 0, "chapters_wait_sec": 0, "ch_read": 0, "ch_max": 75, "cards_found": 0, "cards_max": 10}
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            b._reading_step(out)
        return b, out

    def test_candies_do_not_wait_for_bonus_cards_on_cooldown(self):
        settings = {"halloween_event_info": {"balance": 10}}
        b, out = self.step(settings, {"chapters_read": 75, "chapters_max": 75, "cards_found": 5, "cards_max": 10,
                                      "card_ready": False, "card_cooldown": "40 мин"}, 2)
        self.assertTrue(b.read_manga_chapters.call_args.kwargs.get("for_event"))

    def test_failed_reads_back_off_but_do_not_end_the_day(self):
        settings = {"halloween_event_info": {"balance": 10}}
        stats = {"chapters_read": 75, "chapters_max": 75, "cards_found": 10, "cards_max": 10, "card_ready": False, "card_cooldown": ""}
        for _ in range(8):
            b, out = self.step(settings, stats, 0)
        self.assertEqual(settings["halloween_candy_day"]["misses"], 0)
        self.assertGreaterEqual(out["event_wait_sec"], 8 * 60)

    def test_after_a_bonus_card_read_candies_come_back_in_minutes(self):
        settings = {"halloween_event_info": {"balance": 10}}
        b, out = self.step(settings, {"chapters_read": 75, "chapters_max": 75, "cards_found": 5, "cards_max": 10,
                                      "card_ready": True, "card_cooldown": ""}, 2)
        self.assertFalse(b.read_manga_chapters.call_args.kwargs.get("for_event"))   # the card read
        self.assertLessEqual(out["event_wait_sec"], 240)

    def test_cauldron_takes_the_balance_the_server_reports(self):
        from seasonal import HalloweenEventMixin
        html = '<div class="js-event-balance">10</div><b class="js-summer-heat-temp">100°</b><button class="js-summer-heat-take">x</button>'
        h = HalloweenEventMixin()
        h.log, h.session = Mock(), Mock()
        h.session.get.side_effect = lambda url, **kw: SimpleNamespace(status_code=200, text=html, json=lambda: {})
        h.session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {"balance": 15})
        with patch("seasonal.DataManager.set_setting"):
            self.assertEqual(h.run_halloween_event()["balance"], 15)


class UpdateCacheTests(unittest.TestCase):
    def test_new_version_drops_the_old_page_cache_once(self):
        import os, tempfile
        from buffy import app, __version__
        settings = {"app_ui_version": "11.0.6"}
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "EBWebView", "Default", "Cache")
            os.makedirs(cache)
            with patch("buffy.app.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                     patch("buffy.app.DataManager.set_setting", side_effect=settings.__setitem__):
                app._drop_stale_ui_cache(d)
                self.assertFalse(os.path.exists(cache))
                os.makedirs(cache)
                app._drop_stale_ui_cache(d)       # same version again: cache is kept
                self.assertTrue(os.path.exists(cache))
                with patch("buffy.app._ui_stamp", return_value=__version__ + "-changedpage"):
                    app._drop_stale_ui_cache(d)   # same version, edited page: cache is dropped
                self.assertFalse(os.path.exists(cache))
        self.assertEqual(settings["app_ui_version"], __version__ + "-changedpage")


class CupRegistrationTests(unittest.TestCase):
    def runner(self, start_result):
        from arena import ManualArenaController
        page = ('<meta name="csrf-token" content="fresh"><div class="battle-tournament" '
                'data-cup-queue-url="/battle/tournament/queue" data-cup-start-url="/battle/tournament/start"></div>')
        b = Mock()
        b.csrf_token = "stale"
        b.prepare_arena_deck.return_value = True
        clock = [0.0]
        c = ManualArenaController(b, clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + max(s, 1)))
        queue = {"status": "idle", "server_time": 100, "tournament": {"opens_at": 0, "closes_at": 1000, "attempt_number": 6}}
        c.get = Mock(return_value=SimpleNamespace(json=lambda: queue))
        c.post = Mock(side_effect=start_result)
        with patch("arena.get_html", return_value=SimpleNamespace(text=page, raise_for_status=lambda: None)):
            return c, c.run(max_seconds=20)

    def test_refused_start_does_not_block_later_registrations(self):
        c, result = self.runner(RuntimeError("HTTP Error 422"))
        self.assertIsNone(c.registration)
        self.assertEqual(c.post.call_args.args[2], "fresh")   # the page token, not the stale one

    def test_old_unconfirmed_registration_expires(self):
        from arena import ManualArenaController
        c, _ = self.runner([{"status": "searching"}])
        c.registration = {"signature": ["x"], "at": 0}          # left over from hours ago
        c.post.reset_mock()
        c.post.side_effect = [{"status": "searching"}]
        with patch("arena.get_html", return_value=SimpleNamespace(
                text='<meta name="csrf-token" content="fresh"><div class="battle-tournament" data-cup-queue-url="/battle/tournament/queue" data-cup-start-url="/battle/tournament/start"></div>',
                raise_for_status=lambda: None)):
            self.assertNotEqual(c.run(max_seconds=20), "registration_pending")
        c.post.assert_called_once()


class TowerPlannerTests(unittest.TestCase):
    def test_downtime_counts_the_evening_cup(self):
        import datetime as dt
        from engine import tower_downtime_hours
        self.assertAlmostEqual(tower_downtime_hours(dt.datetime(2026, 10, 18, 12, 0), 2), 0.15)
        self.assertAlmostEqual(tower_downtime_hours(dt.datetime(2026, 10, 18, 18, 30), 2), 1 + 35 / 60)  # back 20:30 -> 22:05
        self.assertAlmostEqual(tower_downtime_hours(dt.datetime(2026, 10, 18, 18, 30), 2, cup=False), 0.15)

    def plan(self, hour):
        import datetime as dt
        from bs4 import BeautifulSoup
        html = ('<div id="t" data-preview-url="https://mangabuff.ru/tower/preview">'
                + "".join(f'<button data-tower-option="difficulty" data-value="{d}"></button>' for d in ("dangerous", "nightmare"))
                + "".join(f'<button data-tower-option="duration" data-value="{h}"></button>' for h in (2, 12))
                + "".join(f'<button data-tower-option="route" data-value="{r}"></button>' for r in ("frontier", "depths"))
                + '</div>')
        soup = BeautifulSoup(html, "html.parser")
        rate = {"dangerous": 590, "nightmare": 765}
        calls = []

        def post(url, json=None, **kw):
            calls.append(json)
            hours = json["duration_minutes"] / 60
            progress = hours * (1.0 if json["route_type"] == "frontier" else 0.5)
            crystals = rate[json["difficulty_type"]] * hours
            return Mock(status_code=200, json=lambda: {"data": {
                "power_ratio": 1.5, "estimated_progress": progress, "warning": None,
                "rewards": {"dark_crystals": {"min": crystals, "max": crystals}}}})
        b = bot()
        b.session = Mock(post=post)
        b.csrf_token = "t"
        b._module_cancelled = lambda: False
        b._msk_now = lambda: dt.datetime(2026, 10, 18, hour, 30)
        payload = {"card_user_ids": [1, 2, 3, 4], "duration_minutes": 120, "route_type": "frontier",
                   "difficulty_type": "dangerous", "order_type": "balanced"}
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: d):
            return b._plan_tower_expedition(soup.select_one("#t"), soup, payload), calls

    def test_every_difficulty_and_duration_is_checked(self):
        chosen, calls = self.plan(10)
        pairs = {(c["difficulty_type"], c["duration_minutes"]) for c in calls}
        self.assertEqual(pairs, {("dangerous", 120), ("dangerous", 720), ("nightmare", 120), ("nightmare", 720)})
        self.assertEqual(chosen["difficulty_type"], "nightmare")
        self.assertEqual(chosen["route_type"], "frontier")

    def test_short_trip_that_returns_during_the_cup_loses_to_a_long_one(self):
        chosen, _ = self.plan(18)      # 2 h from 18:30 comes back at 20:30, in the middle of the cup
        self.assertEqual(chosen["duration_minutes"], 720)


class Expired419Tests(unittest.TestCase):
    def test_419_logs_in_again_and_repeats_the_action_once(self):
        from http_session import GuardedSession
        sent = []

        def raw_request(method, url, **kw):
            sent.append(kw["headers"]["X-CSRF-TOKEN"])
            code = 419 if len(sent) == 1 else 200
            return SimpleNamespace(status_code=code, text="{}", headers={"content-type": "application/json"}, content=b"{}")
        raw = Mock()
        raw.request.side_effect = raw_request
        relogin = Mock(return_value="fresh")
        g = GuardedSession(raw, on_csrf_expired=relogin)
        g.min_request_interval = g.max_request_jitter = 0
        r = g.post("https://mangabuff.ru/tower/expedition/claim", json={}, headers={"X-CSRF-TOKEN": "old"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sent, ["old", "fresh"])
        relogin.assert_called_once()

    def test_a_second_419_is_not_retried_forever(self):
        from http_session import GuardedSession
        raw = Mock()
        raw.request.return_value = SimpleNamespace(status_code=419, text="", headers={}, content=b"")
        g = GuardedSession(raw, on_csrf_expired=Mock(return_value="fresh"))
        g.min_request_interval = g.max_request_jitter = 0
        self.assertEqual(g.post("https://mangabuff.ru/x", data={"_token": "old"}).status_code, 419)
        self.assertEqual(raw.request.call_count, 2)


class DroppedSessionTests(unittest.TestCase):
    def check(self, response):
        b = bot()
        b.session.get.return_value = response
        b.login_and_steal_keys = Mock(return_value=True)
        b._keep_logged_in(force=True)
        return b.login_and_steal_keys.call_count

    def test_guest_page_triggers_a_new_login(self):
        self.assertEqual(self.check(SimpleNamespace(status_code=200, url="https://mangabuff.ru/login", text="")), 1)

    def test_site_outage_is_not_a_logout(self):
        self.assertEqual(self.check(SimpleNamespace(status_code=500, url="https://mangabuff.ru/mine", text="")), 0)
        self.assertEqual(self.check(SimpleNamespace(status_code=200, url="https://mangabuff.ru/mine", text="<html>")), 0)


if __name__ == "__main__":
    unittest.main()
