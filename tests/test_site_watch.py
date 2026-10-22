import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from seasonal import SiteWatchMixin


class SiteWatchTest(unittest.TestCase):
    def run_watch(self, settings, js):
        pages = {"/": '<script src="/js/manga.js?id=1"></script><script src="/js/jquery.min.js"></script>'}
        bot = SiteWatchMixin()
        bot.session = Mock(get=lambda url, timeout: SimpleNamespace(
            text=pages.get(url[len("https://mangabuff.ru"):], js if "/js/" in url else "")))
        bot.notifier, bot.log = Mock(), Mock()
        with patch("seasonal.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)), \
                patch("seasonal.DataManager.set_setting", side_effect=settings.__setitem__):
            return bot.run_site_watch(), bot

    def test_first_run_is_baseline_then_new_endpoint_alerts(self):
        settings = {}
        new, bot = self.run_watch(settings, 'post("/achievements/diamond-fall")')
        self.assertEqual(new, {})
        bot.notifier.notify_alert.assert_not_called()
        self.assertEqual(settings["site_watch_known"]["scripts"], ["/js/manga.js"])

        settings["site_watch_last_at"] = 0
        new, bot = self.run_watch(settings, '"/achievements/diamond-fall" "/achievements/red-ghost"')
        self.assertEqual(new, {"achievements": ["/achievements/red-ghost"]})
        bot.notifier.notify_alert.assert_called_once()

        new, bot = self.run_watch(settings, '"/achievements/zzz"')  # within 6 h: skipped
        self.assertEqual(new, {})


if __name__ == "__main__":
    unittest.main()


class HalloweenWatchTest(unittest.TestCase):
    def state(self, returned, completed=False, holiday=False, reward=False):
        return {"event": {"returned_candies": returned, "remaining": 1_000_000 - returned, "percent": returned / 10_000,
                          "completed": completed, "holiday_started": holiday, "locks": [True, True, returned >= 1_000_000]},
                "investigation": {"reward_available": reward, "reward_claimed": False}}

    def run_watch(self, settings, data, post_status=200):
        bot = SiteWatchMixin()
        bot.session = Mock()
        bot.session.get.side_effect = lambda url, **kw: SimpleNamespace(
            status_code=200, json=lambda: data, text='<meta name="csrf-token" content="t">')
        bot.session.post.return_value = SimpleNamespace(status_code=post_status)
        bot.notifier, bot.log = Mock(), Mock()
        bot.run_site_watch = Mock(return_value={"scripts": ["/js/event-pack.js"]})
        with patch("seasonal.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)), \
                patch("seasonal.DataManager.set_setting", side_effect=settings.__setitem__):
            return bot.run_halloween_watch(), bot

    def test_opening_is_reported_and_reward_claimed(self):
        settings = {}
        events, bot = self.run_watch(settings, self.state(821_000))
        self.assertEqual(events, [])                              # first look only remembers
        settings["halloween_watch_state"]["at"] -= 600            # ten minutes later
        events, bot = self.run_watch(settings, self.state(905_000))
        self.assertTrue(any("90.5%" in e and "мин" in e for e in events))
        events, bot = self.run_watch(settings, self.state(1_000_000, completed=True, holiday=True, reward=True))
        text = "\n".join(events)
        self.assertIn("вот-вот начнётся", text)
        self.assertIn("ХЭЛЛОУИН НАЧАЛСЯ", text)
        self.assertIn("забрал 10 конфет", text)
        self.assertIn("/js/event-pack.js", text)
        bot.session.post.assert_called_once()
        bot.notifier.notify_alert.assert_called_once()


class EventPackTest(unittest.TestCase):
    HTML = ('<div class="event-page__stat-value js-event-balance"> 1 240 </div>'
            '<div class="event-page__stat-label">Сегодня выпало</div> <div class="event-page__stat-value"> 12 </div>'
            '<b class="js-summer-heat-temp">100°</b>'
            '<button class="event-page__heat-btn js-summer-heat-take" type="button" > Забрать 5 🍬 </button>')

    def test_page_is_read_and_ready_cauldron_claimed(self):
        from seasonal import HalloweenEventMixin, parse_event_pack
        self.assertEqual(parse_event_pack(self.HTML), {"balance": 1240, "today": 12, "temperature": 100, "can_take": True})
        self.assertFalse(parse_event_pack(self.HTML.replace('type="button" >', 'type="button" disabled >'))["can_take"])
        bot = HalloweenEventMixin()
        bot.session, bot.log = Mock(), Mock()
        bot.session.get.return_value = SimpleNamespace(status_code=200, text=self.HTML)
        bot.session.post.return_value = SimpleNamespace(status_code=200)
        with patch("seasonal.DataManager.set_setting"):
            self.assertTrue(bot.run_halloween_event()["claimed"])
        self.assertIn("/event/heat/claim", bot.session.post.call_args.args[0])


    def test_start_reads_candies_without_claiming(self):
        from seasonal import HalloweenEventMixin
        bot = HalloweenEventMixin()
        bot.session = Mock()
        bot.session.get.return_value = SimpleNamespace(status_code=200, text=self.HTML)
        with patch("seasonal.DataManager.get_setting", side_effect=lambda k, d=None: {"x": 1} if k == "halloween_event_info" else d),                 patch("seasonal.DataManager.set_setting") as save:
            bot.refresh_halloween_info()
        save.assert_any_call("halloween_event_info",
                             {"x": 1, "balance": 1240, "today": 12, "temperature": 100, "can_take": True})
        # The site counted 12 drops today (phone, before a restart): our counter catches up
        self.assertEqual(save.call_args.args[0], "halloween_candy_day")
        self.assertEqual(save.call_args.args[1]["n"], 12)
        bot.session.post.assert_not_called()
        bot.session.get.return_value = SimpleNamespace(status_code=200, text="<html>no event</html>")
        with patch("seasonal.DataManager.set_setting") as save:
            bot.refresh_halloween_info()
        save.assert_not_called()


    def test_candy_counter_never_goes_back(self):
        import time
        from seasonal import sync_candy_day
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
        with patch("seasonal.DataManager.get_setting", return_value={"date": today, "n": 20, "misses": 1}),                 patch("seasonal.DataManager.set_setting") as save:
            sync_candy_day({"today": 12})
            sync_candy_day({"today": None})
            save.assert_not_called()
            sync_candy_day({"today": 35})
        save.assert_called_once_with("halloween_candy_day", {"date": today, "n": 35, "misses": 1})


class CauldronLockedTest(unittest.TestCase):
    def test_hot_but_locked_cauldron_alerts_once_a_day_and_burns_nothing(self):
        from seasonal import HalloweenEventMixin
        html = '<b class="js-summer-heat-temp">100°</b><button class="js-summer-heat-take" type="button" disabled >x</button>'
        settings = {}
        h = HalloweenEventMixin()
        h.log, h.notifier, h.session = Mock(), Mock(), Mock()
        h.session.get.side_effect = lambda url, **kw: SimpleNamespace(status_code=200, text=html, json=lambda: {})
        with patch("seasonal.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("seasonal.DataManager.set_setting", side_effect=settings.__setitem__):
            h.run_halloween_event()
            h.run_halloween_event()
        h.notifier.notify_alert.assert_called_once()
        h.session.post.assert_not_called()
