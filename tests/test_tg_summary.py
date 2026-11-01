import unittest
from unittest.mock import Mock, patch

import tg_notifier
from tg_notifier import TelegramNotifier, daily_summary


class DailySummaryTests(unittest.TestCase):
    def notifier(self, settings):
        n = object.__new__(TelegramNotifier)
        n.send_message = Mock(return_value=(True, "OK"))
        n._last_heartbeat_time = 0
        get = patch("tg_notifier.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d))
        put = patch("tg_notifier.DataManager.set_setting", side_effect=settings.__setitem__)
        get.start(), put.start()
        self.addCleanup(patch.stopall)
        return n

    def test_routine_events_are_counted_not_sent(self):
        settings = {}
        n = self.notifier(settings)
        n.notify_ads_watched(3, 30)
        n.notify_reading_summary(15, 75)
        n.notify_reading_summary(10, 75)
        n.notify_battle_summary(5, 4, 1, 120)
        n.send_message.assert_not_called()
        day = settings["tg_day"]
        self.assertEqual((day["ads_diamonds"], day["chapters"], day["battles"], day["wins"]), (30, 25, 5, 4))

    def test_new_day_sends_yesterdays_summary_once(self):
        settings = {"tg_day": {"date": "2000-01-01", "chapters": 75, "dia_first": 1000, "dia_last": 1500},
                    "tg_day_prev": {"date": "1999-12-31", "dia_first": 0, "dia_last": 300}}
        n = self.notifier(settings)
        n.tally(cards=1)
        n.tally(cards=1)
        n.send_message.assert_called_once()
        text = n.send_message.call_args.args[0]
        self.assertIn("1 января", text)
        self.assertIn("+500, вчера +300", text)
        self.assertIn("Главы 75", text)
        self.assertEqual(settings["tg_day"]["cards"], 2)
        self.assertEqual(settings["tg_day_prev"]["chapters"], 75)

    def test_summary_lists_only_what_happened(self):
        text = daily_summary({"date": "2026-10-31", "comment_diamonds": 91, "comments": 13,
                              "cup_fights": 12, "cup_wins": 8})
        self.assertIn("31 октября", text)
        self.assertIn("комментарии +91", text)
        self.assertIn("Кубок: 12 боёв, побед 8", text)
        self.assertNotIn("Баланс", text)
        self.assertNotIn("Арена", text)

    def test_heartbeat_keeps_the_days_balance_and_is_short(self):
        settings = {"tg_heartbeat_interval_hours": 4}
        n = self.notifier(settings)
        n.is_night_time = lambda: False
        n.notify_cycle_heartbeat(10, 5000, 3, "4/10", "2ч 5м", "Башня", 30, diamonds=1200, chapters_str="75/75")
        n.notify_cycle_heartbeat(10, 5000, 3, "4/10", "2ч 5м", "Башня", 30, diamonds=1250, chapters_str="75/75")
        self.assertEqual((settings["tg_day"]["dia_first"], settings["tg_day"]["dia_last"]), (1200, 1250))
        n.send_message.assert_called_once()              # second one is inside the 4 h interval
        text = n.send_message.call_args.args[0]
        self.assertIn("Сегодня: главы 75/75 · реклама 3/3 · карты 4/10", text)
        self.assertLessEqual(text.count("\n"), 4)


if __name__ == "__main__":
    unittest.main()
