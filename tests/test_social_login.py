"""Social sign-in (VK, Discord, Yandex, Google): offline, no real windows or requests."""
import sys
import unittest
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock, patch

from buffy.api import BuffyApi
from engine import MangaMinerBot

PAGE = '<meta name="csrf-token" content="fresh-csrf"><a href="/users/4242">me</a>'


def page(status=200, text=PAGE, url="https://mangabuff.ru/balance"):
    return SimpleNamespace(status_code=status, text=text, url=url)


class RestoreSocialSessionTests(unittest.TestCase):
    def setUp(self):
        self.bot = object.__new__(MangaMinerBot)
        self.bot.auth_log_buffer = []
        self.bot.session = Mock(headers={})
        self.bot.user_id = None
        self.bot.validate_session = Mock(return_value=True)
        self.bot._persist_session_cookies = Mock()
        settings = {"login_method": "social"}
        p = patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d))
        p.start()
        self.addCleanup(p.stop)

    def test_live_cookies_refresh_csrf_without_password_login(self):
        self.bot.session.get.return_value = page()
        self.assertTrue(self.bot.login_and_steal_keys())
        self.assertEqual(self.bot.csrf_token, "fresh-csrf")
        self.assertEqual(self.bot.session.headers["X-CSRF-TOKEN"], "fresh-csrf")
        self.assertEqual(self.bot.user_id, "4242")
        self.bot.session.post.assert_not_called()
        self.bot._persist_session_cookies.assert_called_once()

    def test_expired_cookies_ask_to_sign_in_again(self):
        self.bot.session.get.return_value = page(url="https://mangabuff.ru/login")
        self.assertFalse(self.bot.login_and_steal_keys())
        self.assertTrue(self.bot.login_rejected)
        self.assertIn("Сменить аккаунт", self.bot.auth_log_buffer[-1])

    def test_site_outage_is_not_treated_as_logout(self):
        self.bot.session.get.return_value = page(status=502, text="bad gateway")
        self.assertFalse(self.bot.login_and_steal_keys())
        self.assertFalse(self.bot.login_rejected)

    def test_dead_cookies_use_buffy_relogin_window(self):
        self.bot.session.get.side_effect = [page(url="https://mangabuff.ru/login"), page()]
        self.bot.social_relogin = Mock(return_value=({"mangabuff_session": "new"}, "Edg/130"))
        with patch("engine.load_cookies_into") as load:
            self.assertTrue(self.bot.login_and_steal_keys())
        load.assert_called_once_with(self.bot.session, {"mangabuff_session": "new"})
        self.assertEqual(self.bot.user_agent, "Edg/130")

    def test_relogin_window_closed_asks_to_sign_in(self):
        self.bot.session.get.return_value = page(url="https://mangabuff.ru/login")
        self.bot.social_relogin = Mock(return_value=None)
        self.assertFalse(self.bot.login_and_steal_keys())
        self.assertTrue(self.bot.login_rejected)

    def test_missing_saved_session_fails_without_request(self):
        self.bot.validate_session.return_value = False
        self.assertFalse(self.bot.login_and_steal_keys())
        self.bot.session.get.assert_not_called()


class FakeWindow:
    def __init__(self, urls, close_early=False):
        self.urls = list(urls)
        self.close_early = close_early
        self.events = SimpleNamespace(closed=self)
        self.on_closed = []
        self.destroyed = False

    def __iadd__(self, handler):  # win.events.closed += handler
        self.on_closed.append(handler)
        return self

    def get_current_url(self):
        if self.close_early and len(self.urls) == 1:
            for handler in self.on_closed:
                handler()
        return self.urls.pop(0) if len(self.urls) > 1 else self.urls[0]

    def get_cookies(self):
        jar = SimpleCookie()
        jar["mangabuff_session"] = "abc"
        jar["XSRF-TOKEN"] = "xyz"
        return [jar]

    def evaluate_js(self, code):
        return "Mozilla/5.0 Edg/130"

    def destroy(self):
        self.destroyed = True


class SocialLoginApiTests(unittest.TestCase):
    def run_login(self, window, alive=True):
        api = BuffyApi()
        webview = SimpleNamespace(create_window=Mock(return_value=window))
        bot = Mock(user_id="4242")
        bot.adopt_cookie_session.return_value = alive
        bot.session.headers = {}
        with patch.dict(sys.modules, {"webview": webview}), \
                patch("engine.MangaMinerBot", return_value=bot), \
                patch("buffy.api.DataManager.switch_account") as switch, \
                patch("buffy.api.DataManager.set_credentials") as save, \
                patch("buffy.api.DataManager.set_setting") as setting, \
                patch("buffy.api.threading.Event.wait"), \
                patch.object(api, "_log"):
            result = api.social_login("vkid")
        return result, webview, bot, switch, save, setting

    def test_signed_in_window_saves_social_account(self):
        win = FakeWindow(["https://id.vk.com/auth", "https://mangabuff.ru/login/callback/vkid", "https://mangabuff.ru/"])
        result, webview, bot, switch, save, setting = self.run_login(win)
        self.assertTrue(result["ok"])
        self.assertIn("/login/redirect/vkid", webview.create_window.call_args.args[1])
        self.assertFalse(webview.create_window.call_args.kwargs["hidden"])
        bot.use_cookies.assert_called_once_with({"mangabuff_session": "abc", "XSRF-TOKEN": "xyz"},
                                                "Mozilla/5.0 Edg/130")
        switch.assert_called_once_with("ВКонтакте · id4242")
        save.assert_called_once_with("ВКонтакте · id4242", None, login_method="social")
        setting.assert_called_once_with("login_provider", "vkid")
        self.assertTrue(win.destroyed)

    def test_closed_window_cancels_without_saving(self):
        win = FakeWindow(["https://id.vk.com/auth"], close_early=True)
        result, _, bot, _, save, _ = self.run_login(win)
        self.assertFalse(result["ok"])
        bot.adopt_cookie_session.assert_not_called()
        save.assert_not_called()

    def test_guest_cookies_are_not_saved(self):
        win = FakeWindow(["https://mangabuff.ru/"])
        result, _, _, _, save, _ = self.run_login(win, alive=False)
        self.assertFalse(result["ok"])
        save.assert_not_called()

    def test_silent_relogin_shows_window_only_after_hidden_attempt(self):
        win = FakeWindow(["https://id.vk.com/auth"])
        win.show = Mock()
        api = BuffyApi()
        webview = SimpleNamespace(create_window=Mock(return_value=win))
        clock = iter(range(0, 10_000, 10))  # every time.time() call moves 10 s
        with patch.dict(sys.modules, {"webview": webview}), \
                patch("buffy.api.DataManager.get_setting", return_value="vkid"), \
                patch("buffy.api.time.time", side_effect=lambda: next(clock)), \
                patch("buffy.api.threading.Event.wait"), patch.object(api, "_log"):
            self.assertIsNone(api._silent_social_relogin())
        self.assertTrue(webview.create_window.call_args.kwargs["hidden"])
        win.show.assert_called_once()
        self.assertTrue(win.destroyed)

    def test_unknown_provider_opens_nothing(self):
        result = BuffyApi().social_login("myspace")
        self.assertFalse(result["ok"])


PROFILE = '''<div class="profile__background" style="background-image: url('/img/covers/54.jpg')">
<video preload="metadata" class="profile__cover" autoplay="true" muted="muted" loop="true">
 <source src="/img/covers/54.mp4" type="video/mp4"> </video>
<a href="javascript:void(0)" class="profile__avatar"> <img src="/img/avatars/x150/558.gif" class="my-avatar" alt="Аватар"> </a>
<div class="profile__name" data-name="Tom &amp; Jerry">Tom &amp; Jerry</div>'''


class RefreshProfileTests(unittest.TestCase):
    def setUp(self):
        self.bot = object.__new__(MangaMinerBot)
        self.bot.user_id = None
        self.bot.session = Mock()

    def test_reads_name_animated_avatar_and_cover(self):
        self.bot.session.get.side_effect = [page(), page(text=PROFILE)]
        with patch("engine.DataManager.set_setting") as save, \
                patch("engine.DataManager.get_setting", return_value=None):
            profile = self.bot.refresh_profile()
        self.assertEqual(self.bot.session.get.call_args.args[0], "https://mangabuff.ru/users/4242")
        self.assertEqual(profile, {
            "id": "4242", "name": "Tom & Jerry",
            "avatar": "https://mangabuff.ru/img/avatars/x150/558.gif",
            "cover": "https://mangabuff.ru/img/covers/54.jpg",
            "cover_video": "https://mangabuff.ru/img/covers/54.mp4",
            "theme": {}, "theme_key": "",  # no skin on this profile: Buffy keeps its own colours
        })
        save.assert_called_once_with("account_profile", profile)

    def test_skin_colours_come_from_site_css_once(self):
        skinned = ('<link rel="stylesheet" href="/css/app.css?id=v1"><body class="isAuth profile-skin profile-skin--12">'
                   + PROFILE)
        css = ".profile-skin--12 { --main-purple-color: #b3852d !important; }"
        self.bot.user_id = "4242"
        self.bot.session.get.side_effect = [page(text=skinned), page(text=css)]
        with patch("engine.DataManager.set_setting"), patch("engine.DataManager.get_setting", return_value=None):
            profile = self.bot.refresh_profile()
        self.assertEqual(self.bot.session.get.call_args.args[0], "https://mangabuff.ru/css/app.css?id=v1")
        self.assertEqual(profile["theme"]["--pink"], "#b3852d")
        # Same skin and stylesheet version next time: no 3 MB download
        self.bot.session.get.side_effect = [page(text=skinned)]
        with patch("engine.DataManager.set_setting"), patch("engine.DataManager.get_setting", return_value=profile):
            self.assertEqual(self.bot.refresh_profile()["theme"], profile["theme"])
        self.assertEqual(self.bot.session.get.call_count, 3)

    def test_unreadable_profile_keeps_old_one_and_never_raises(self):
        self.bot.user_id = "4242"
        for reply in (page(status=500, text=""), page(text="<html>guest</html>")):
            self.bot.session.get.side_effect = [reply]
            with patch("engine.DataManager.set_setting") as save:
                self.assertIsNone(self.bot.refresh_profile())
            save.assert_not_called()
        self.bot.session.get.side_effect = OSError("offline")
        self.assertIsNone(self.bot.refresh_profile())


class HasLoginTests(unittest.TestCase):
    def check(self, email, password, method):
        data = {"email": email, "password": password, "login_method": method}
        with patch("data_management.DataManager.load_json", return_value=data):
            from data_management import DataManager
            return DataManager.has_login()

    def test_cases(self):
        self.assertTrue(self.check("a@b.c", "pw", None))
        self.assertTrue(self.check("ВКонтакте · id1", None, "social"))
        self.assertFalse(self.check("a@b.c", None, None))
        self.assertFalse(self.check(None, None, "social"))


if __name__ == "__main__":
    unittest.main()
