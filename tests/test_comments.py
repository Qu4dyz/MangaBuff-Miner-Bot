import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from engine import MangaMinerBot

BALANCE = '<div><span>Комментарии</span> <b>{}/13</b></div>'


class CommentTests(unittest.TestCase):
    def run_once(self, settings, done=3, post_status=200, items=None):
        b = object.__new__(MangaMinerBot)
        b.log, b.notifier, b.session, b.csrf_token = Mock(), Mock(), Mock(), "t"
        b._msk_now = lambda: dt.datetime(2026, 10, 31, 12)
        b.session.get.return_value = SimpleNamespace(status_code=200, text=BALANCE.format(done))
        b.session.post.return_value = SimpleNamespace(status_code=post_status)
        items = items or [{"comment_id": "2235", "slug": "petlya", "vol": 1, "ch": 14.0, "url": "https://mangabuff.ru/manga/petlya/1/14"}]
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)), \
                patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            b._comment_after_read(items)
        return b

    def test_off_by_default(self):
        b = self.run_once({})
        b.session.post.assert_not_called()

    def test_sticker_goes_under_the_read_chapter(self):
        settings = {"comments_enabled": True}
        b = self.run_once(settings)
        data = b.session.post.call_args.kwargs["data"]
        self.assertEqual((data["commentable_id"], data["commentable_type"], data["text"]), ("2235", "mangaChapter", ""))
        self.assertIn(data["sticker_id"], ("8336", "7463", "8337"))
        self.assertEqual(settings["comment_day"]["titles"], ["petlya"])

    def test_one_per_title_a_day_and_a_pause_between(self):
        settings = {"comments_enabled": True}
        self.run_once(settings)
        settings["comment_day"]["last_at"] = 0          # pause over
        b = self.run_once(settings)                       # same title again
        b.session.post.assert_not_called()
        b = self.run_once(settings, items=[{"comment_id": "9", "slug": "other", "vol": 1, "ch": 1.0, "url": "u"}])
        b.session.post.assert_called_once()

    def test_stops_at_the_quest_limit_and_makes_up_deleted_ones(self):
        settings = {"comments_enabled": True}
        self.run_once(settings, done=13).session.post.assert_not_called()
        self.run_once(settings, done=12).session.post.assert_called_once()   # one was deleted: send again

    def test_refusal_stops_for_the_day(self):
        settings = {"comments_enabled": True}
        b = self.run_once(settings, post_status=403)
        self.assertTrue(settings["comment_day"]["stopped"])
        b.notifier.notify_alert.assert_called_once()
        settings["comment_day"]["last_at"] = 0
        self.run_once(settings, items=[{"comment_id": "9", "slug": "other", "vol": 1, "ch": 1.0, "url": "u"}]).session.post.assert_not_called()


class BookmarkTests(unittest.TestCase):
    def test_bookmarked_titles_are_collected_from_every_folder_and_page(self):
        pages = {
            "": '<a href="?folder_id=1">Читаю</a><a href="?folder_id=3">Прочитано</a>',
            "1-1": '<a class="bookmark__manga-name" href="/manga/aaa">A</a><a href="/manga/top-widget">x</a>',
            "1-2": '<a href="https://mangabuff.ru/manga/bbb" class="bookmark__manga-image">B</a>',
            "1-3": "",
            "3-1": '<a class="bookmark__manga-name" href="/manga/ccc">C</a>',
            "3-2": '<a class="bookmark__manga-name" href="/manga/ccc">C</a>',
        }

        def get(url, **kw):
            q = url.split("?")[1] if "?" in url else ""
            key = "" if not q else q.replace("folder_id=", "").replace("&page=", "-")
            return SimpleNamespace(status_code=200, text=pages.get(key, ""))
        settings = {}
        b = object.__new__(MangaMinerBot)
        b.log, b.user_id = Mock(), "279379"
        b.session = Mock(get=get)
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            self.assertEqual(b._bookmarked_slugs(), {"aaa", "bbb", "ccc"})
            b.session = Mock()                      # cached for 6 h: no new requests
            self.assertEqual(b._bookmarked_slugs(), {"aaa", "bbb", "ccc"})
            b.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
