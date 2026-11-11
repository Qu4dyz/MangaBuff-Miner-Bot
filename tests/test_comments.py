import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from engine import MangaMinerBot

BALANCE = '<div><span>Комментарии</span> <b>{}/13</b></div>'


class CommentTests(unittest.TestCase):
    def run_once(self, settings, done=3, post_status=200, items=None):
        import time
        settings.setdefault("owned_stickers", {"at": time.time(), "ids": ["8336", "7463", "8337"]})
        b = object.__new__(MangaMinerBot)
        b.log, b.notifier, b.session, b.csrf_token = Mock(), Mock(), Mock(), "t"
        b._msk_now = lambda: dt.datetime(2026, 10, 31, 12)
        chapter = '<div class="comments" data-type="mangaChapter" data-id="777">'
        b.session.get.side_effect = lambda url, **kw: SimpleNamespace(
            status_code=200, text=chapter if "/manga/" in url else BALANCE.format(done))
        b.session.post.return_value = SimpleNamespace(status_code=post_status)
        b._bookmarked_slugs = lambda: set(settings.get("bookmarks", []))
        items = items or [{"comment_id": "2235", "slug": "petlya", "vol": 1, "ch": 14.0, "url": "https://mangabuff.ru/manga/petlya/1/14"}]
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)), \
                patch("engine.DataManager.set_setting", side_effect=settings.__setitem__), \
                patch("engine.DataManager.load_reading_state", return_value={"history": settings.get("history", [])}):
            b._comment_after_read(items)
        return b

    def test_off_by_default(self):
        b = self.run_once({})
        b.session.post.assert_not_called()

    def test_sticker_goes_under_the_read_chapter(self):
        import time
        settings = {"comments_enabled": True, "owned_stickers": {"at": time.time(), "ids": ["8336", "9999"]}}
        b = self.run_once(settings)
        data = b.session.post.call_args.kwargs["data"]
        self.assertEqual((data["commentable_id"], data["commentable_type"], data["text"]), ("2235", "mangaChapter", ""))
        self.assertEqual(data["sticker_id"], "8336")     # 9999 is bought but not a thank-you sticker
        self.assertEqual(settings["comment_day"]["chapters"], ["2235"])

    def test_without_thank_you_stickers_a_short_phrase_is_sent(self):
        import time
        from engine import THANKS_PHRASES
        settings = {"comments_enabled": True, "owned_stickers": {"at": time.time(), "ids": []}}
        data = self.run_once(settings).session.post.call_args.kwargs["data"]
        self.assertIn(data["text"], THANKS_PHRASES)
        self.assertEqual(data["sticker_id"], "")

    def test_owned_stickers_come_from_bought_packs_only(self):
        picker = ('<button class="tabs__item sticker__pack-item" data-page="248"><span class="sticker__pack-badge sticker__pack-badge--owned">Куплен</span></button>'
                  '<button class="tabs__item sticker__pack-item" data-page="231"><span class="sticker__pack-badge sticker__pack-badge--locked">x</span></button>')
        pack = '<button type="button"\n class="sticker__item"\n data-id="8336"\n data-image="/img/stickers/247/6.png">'
        settings = {}
        b = object.__new__(MangaMinerBot)
        b.csrf_token = "t"
        b.session = Mock()
        b.session.post.side_effect = lambda url, **kw: SimpleNamespace(
            status_code=200, json=lambda: {"content": pack if url.endswith("/248") else picker})
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)),                 patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            self.assertEqual(b._owned_stickers(), {"8336"})
        self.assertEqual([c.args[0] for c in b.session.post.call_args_list][-1], "https://mangabuff.ru/stickers/248")

    def test_one_per_chapter_and_a_pause_between(self):
        settings = {"comments_enabled": True}
        self.run_once(settings)
        settings["comment_day"]["last_at"] = 0          # pause over
        b = self.run_once(settings)                       # same chapter again
        b.session.post.assert_not_called()
        b = self.run_once(settings, items=[{"comment_id": "9", "slug": "petlya", "vol": 1, "ch": 15.0, "url": "u"}])
        b.session.post.assert_called_once()

    def test_at_most_three_per_title_and_30_to_60_minutes_apart(self):
        import time
        settings = {"comments_enabled": True}
        self.run_once(settings)
        gap = settings["comment_day"]["gap"]
        self.assertTrue(30 * 60 <= gap <= 60 * 60)
        settings["comment_day"]["last_at"] = time.time() - gap + 60   # a minute of pause left
        self.run_once(settings, items=[{"comment_id": "8", "slug": "other", "vol": 1, "ch": 1.0, "url": "u"}]).session.post.assert_not_called()
        for cid in ("9", "10", "11", "12"):
            settings["comment_day"]["last_at"] = 0
            self.run_once(settings, items=[{"comment_id": cid, "slug": "petlya", "vol": 1, "ch": 15.0, "url": "u"}])
        # the remembered "other" chapter goes next, then petlya again; a fourth petlya is refused
        self.assertEqual(settings["comment_day"]["chapters"], ["2235", "8", "10", "11"])
        self.assertEqual(settings["comment_day"]["titles"], {"petlya": 3, "other": 1})

    def test_one_long_read_does_not_push_other_titles_out_of_the_pool(self):
        settings = {"comments_enabled": True}
        self.run_once(settings, done=13, items=[{"comment_id": "o1", "slug": "other", "vol": 1, "ch": 1.0, "url": "u"}])
        long_read = [{"comment_id": str(i), "slug": "petlya", "vol": 1, "ch": float(i), "url": "u"} for i in range(400)]
        self.run_once(settings, done=13, items=long_read)
        slugs = [b["slug"] for b in settings["comment_pool"]]
        self.assertEqual((slugs.count("petlya"), slugs.count("other")), (30, 1))

    def test_chapters_read_earlier_are_remembered_for_later_comments(self):
        settings = {"comments_enabled": True}
        self.run_once(settings, done=13)                                   # quest full: only remembered
        settings["comment_day"]["date"] = "2026-10-30"                     # next day
        b = self.run_once(settings, items=[{"comment_id": "2235", "slug": "petlya", "vol": 1, "ch": 14.0, "url": "u"}])
        self.assertEqual(b.session.post.call_args.kwargs["data"]["commentable_id"], "2235")
        self.assertTrue(settings["comment_pool"][0]["done"])

    def test_reading_one_title_still_spreads_comments_over_other_titles_from_history(self):
        settings = {"comments_enabled": True, "bookmarks": ["liked"],
                    "history": ["https://mangabuff.ru/manga/liked/1/3", "https://mangabuff.ru/manga/old-title/1/5"]}
        self.run_once(settings)                                            # petlya, the title being read
        settings["comment_day"]["last_at"] = 0
        b = self.run_once(settings)                                        # only petlya read: take history
        self.assertEqual(b.session.post.call_args.kwargs["data"]["commentable_id"], "777")
        self.assertEqual(settings["comment_day"]["titles"], {"petlya": 1, "old-title": 1})   # never the bookmarked one

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

    def test_unknown_account_id_is_looked_up_and_a_failed_load_blocks_reading(self):
        settings = {}
        b = object.__new__(MangaMinerBot)
        b.log, b.user_id = Mock(), None
        b.session = Mock(get=lambda url, **kw: SimpleNamespace(
            status_code=200, text='<a href="/users/279379">me</a>' if "balance" in url else
            '<a class="bookmark__manga-name" href="/manga/lukizm">L</a>'))
        with patch("engine.DataManager.get_setting", side_effect=lambda k, d=None: settings.get(k, d)), \
                patch("engine.DataManager.set_setting", side_effect=settings.__setitem__):
            self.assertEqual(b._bookmarked_slugs(), {"lukizm"})
            self.assertEqual(b.user_id, "279379")
            settings.clear()
            b.user_id = None
            b.session = Mock(get=Mock(side_effect=OSError))
            self.assertIsNone(b._bookmarked_slugs())    # never loaded: the caller must not read


if __name__ == "__main__":
    unittest.main()
