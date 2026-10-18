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


if __name__ == "__main__":
    unittest.main()
