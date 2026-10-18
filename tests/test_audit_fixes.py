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


if __name__ == "__main__":
    unittest.main()
