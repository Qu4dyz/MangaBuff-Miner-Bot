"""Offline reading recovery: no real account requests or state writes."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from engine import MangaMinerBot


class ReadingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.bot = object.__new__(MangaMinerBot)
        self.bot.log = Mock()
        self.bot.notifier = Mock()
        self.bot._alive = Mock(return_value=True)
        self.bot._touch_heartbeat = Mock()
        self.bot.csrf_token = 'test'
        self.bot.session = Mock()
        self.slugs = ['elised', 'fresh', 'third']
        self.chapters = {s: list(range(93, 111)) for s in self.slugs}
        self.bot.get_popular_manga_slugs = lambda: self.slugs
        self.stats = dict(chapters_read=62, chapters_max=75, cards_found=0,
                          cards_max=10, card_ready=False)
        self.bot.get_reading_stats = lambda: dict(self.stats)
        self.state = {'current_manga': 'elised', 'progress': {'elised': {'vol': 1, 'ch': 92}}}
        self.submitted = []
        self.counting_slugs = {'fresh', 'third'}
        self.reply = {}
        self.status = 200
        self.body = None
        self.bot.session.get.side_effect = self.get
        self.bot.session.post.side_effect = self.post
        settings = {'reading_delay_min': 0, 'reading_delay_max': 0}
        for p in (
            patch('engine.DataManager.get_credentials', return_value=('', '')),
            patch('engine.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)),
            patch('engine.DataManager.load_reading_state', side_effect=lambda: copy.deepcopy(self.state)),
            patch('engine.DataManager.save_reading_state', side_effect=self.save),
        ):
            p.start()
            self.addCleanup(p.stop)

    def save(self, state):
        self.state = copy.deepcopy(state)

    def get(self, url, **kwargs):
        parts = url.split('/manga/')[1].split('/')
        slug = parts[0]
        if len(parts) == 1:
            html = ''.join(f'<a href="/manga/{slug}/1/{ch}">chapter</a>' for ch in self.chapters[slug])
        else:
            html = 'window.current_chapter = ' + json.dumps({'id': slug, 'chapter_id': int(parts[2])}) + ';'
        return SimpleNamespace(status_code=200, text=html)

    def post(self, url, data, **kwargs):
        batch = [value for key, value in data.items() if key.endswith('[manga_id]')]
        self.submitted.extend(batch)
        if self.status == 200 and self.reply == {}:
            self.stats['chapters_read'] = min(75, self.stats['chapters_read'] +
                                              sum(s in self.counting_slugs for s in batch))
        return SimpleNamespace(status_code=self.status, text=self.body if self.body is not None else json.dumps(self.reply),
                               json=lambda: self.reply, headers={})

    def test_stale_title_switches_to_fresh_and_counts_only_site_progress(self):
        self.bot.read_manga_chapters(target_count=3)
        self.assertEqual(self.stats['chapters_read'], 65)
        self.assertEqual(self.bot._last_read_count, 3)
        self.assertEqual(self.submitted[:4], ['elised'] * 4)
        self.assertEqual(self.state['current_manga'], 'fresh')

    def test_two_stale_titles_stop_with_next_title_saved_for_restart(self):
        self.counting_slugs.clear()
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.bot._last_read_count, 0)
        self.assertEqual(len(self.submitted), 8)
        self.assertEqual(self.state['current_manga'], 'third')
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.submitted[8:12], ['third'] * 4)

    def test_http_error_does_not_skip_to_later_chapters(self):
        self.status = 500
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.state['progress']['elised']['ch'], 92)
        self.assertEqual(self.bot._last_read_count, 0)

    def test_explicit_error_in_http_200_does_not_advance_history(self):
        self.reply = {'success': False, 'message': 'not accepted'}
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.state['progress']['elised']['ch'], 92)
        self.assertEqual(self.bot._last_read_count, 0)

    def test_last_single_chapter_is_submitted_before_switching_title(self):
        self.chapters['elised'] = [93]
        self.counting_slugs.add('elised')
        self.bot.read_manga_chapters(target_count=2)
        self.assertEqual(self.bot._last_read_count, 2)
        self.assertEqual(self.submitted, ['elised', 'fresh'])

    def test_capped_counter_does_not_prevent_card_drop_processing(self):
        self.stats.update(chapters_read=75, card_ready=True)
        self.reply = {'name': 'Card', 'image': '/card.png', 'copy_number': 123}
        self.bot._download_image_bytes = Mock(return_value=b'image')
        self.bot.user_id = 1
        self.bot.read_manga_chapters(target_count=2)
        self.bot.notifier.notify_card_dropped.assert_called_once()
        self.assertEqual(self.bot._last_read_count, 2)

    def test_html_response_is_not_saved_as_read(self):
        self.body = '<html>login required</html>'
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.state['progress']['elised']['ch'], 92)
        self.assertEqual(self.bot._last_read_count, 0)

    def test_timeout_stops_without_advancing_local_cursor(self):
        self.bot.session.post.side_effect = TimeoutError('unknown outcome')
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.bot.session.post.call_count, 1)
        self.assertEqual(self.state['progress']['elised']['ch'], 92)
        self.assertEqual(self.bot._last_read_count, 0)

    def test_guest_chapter_never_submits_history(self):
        get = self.get

        def guest(url, **kwargs):
            res = get(url, **kwargs)
            if '/1/' in url:
                res.text = 'window.isAuth = 0;' + res.text
            return res

        self.bot.session.get.side_effect = guest
        self.bot.read_manga_chapters(target_count=13)
        self.bot.session.post.assert_not_called()
        self.assertEqual(self.state['progress']['elised']['ch'], 92)

    def test_normal_empty_response_can_still_credit_daily_progress(self):
        self.body = ''
        self.counting_slugs.add('elised')
        self.bot.read_manga_chapters(target_count=3)
        self.assertEqual(self.stats['chapters_read'], 65)
        self.assertEqual(self.bot._last_read_count, 3)
        self.assertEqual(len(self.submitted), 3)

    def test_plain_text_no_reward_response_can_still_credit_daily_progress(self):
        self.body = 'ok'
        self.counting_slugs.add('elised')
        self.bot.read_manga_chapters(target_count=2)
        self.assertEqual(self.stats['chapters_read'], 64)
        self.assertEqual(self.bot._last_read_count, 2)

    def test_card_reward_is_processed_even_if_balance_temporarily_unavailable(self):
        self.bot.get_reading_stats = Mock(side_effect=[dict(self.stats), None])
        self.reply = {'name': 'Card', 'image': '/card.png', 'copy_number': 123}
        self.bot._download_image_bytes = Mock(return_value=b'image')
        self.bot.user_id = 1
        self.bot.read_manga_chapters(target_count=2)
        self.bot.notifier.notify_card_dropped.assert_called_once()
        self.assertEqual(self.bot._last_read_count, 0)

    def test_event_reward_is_processed_with_capped_counter(self):
        self.stats.update(chapters_read=75, cards_found=10)
        self.reply = {'token': 'event-token', 'type': 'candy'}
        self.bot._take_event_drop = Mock()
        self.bot.read_manga_chapters(target_count=2, for_event=True)
        self.bot._take_event_drop.assert_called_once()
        self.assertEqual(self.bot._last_read_count, 2)

    def test_partial_credit_does_not_overstate_chapters_or_exceed_target(self):
        post = self.post
        self.counting_slugs.add('elised')

        def partial(*args, **kwargs):
            before = self.stats['chapters_read']
            result = post(*args, **kwargs)
            self.stats['chapters_read'] = before + 1
            return result

        self.bot.session.post.side_effect = partial
        self.bot.read_manga_chapters(target_count=3)
        self.assertEqual(self.bot._last_read_count, 3)
        self.assertEqual(self.stats['chapters_read'], 65)

    def test_reaching_daily_cap_stops_without_testing_other_titles(self):
        self.stats['chapters_read'] = 74
        self.counting_slugs.add('elised')
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.bot._last_read_count, 1)
        self.assertEqual(self.submitted, ['elised'])
        self.assertEqual(self.stats['chapters_read'], 75)

    def test_short_stale_titles_are_also_bounded(self):
        self.counting_slugs.clear()
        self.chapters = {s: [93] for s in self.slugs}
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.bot._last_read_count, 0)
        self.assertEqual(self.submitted, ['elised', 'fresh'])
        self.assertEqual(self.state['current_manga'], 'third')

    def test_trailing_stale_batch_after_growth_does_not_count_as_stalled_title(self):
        self.chapters['elised'] = [93, 94, 95]
        self.counting_slugs = {'elised'}
        post = self.post

        def elised_counts_once(url, data, **kwargs):
            res = post(url, data, **kwargs)
            self.counting_slugs.discard('elised')
            return res
        self.bot.session.post.side_effect = elised_counts_once
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.bot._last_read_count, 2)
        self.assertIn('third', self.submitted)

    def test_unavailable_balance_stops_without_searching_other_titles(self):
        self.bot.get_reading_stats = Mock(side_effect=[dict(self.stats), None])
        self.bot.read_manga_chapters(target_count=13)
        self.assertEqual(self.bot._last_read_count, 0)
        self.assertEqual(self.submitted, ['elised', 'elised'])
        self.assertEqual(self.state['current_manga'], 'elised')

    def test_reading_diagnostics_link_response_to_counter_without_token(self):
        self.counting_slugs.add('elised')
        self.bot.read_manga_chapters(target_count=2)
        messages = [c.args[0] for c in self.bot.log.call_args_list]
        responses = [json.loads(m.split('reading_response ', 1)[1]) for m in messages if 'reading_response ' in m]
        credits = [json.loads(m.split('reading_credit ', 1)[1]) for m in messages if 'reading_credit ' in m]
        self.assertEqual(responses[0]['run'], credits[0]['run'])
        self.assertEqual(responses[0]['batch'], credits[0]['batch'])
        self.assertEqual((credits[0]['before'], credits[0]['after']), (62, 64))
        self.assertNotIn('X-CSRF-TOKEN', '\n'.join(messages))


if __name__ == '__main__':
    unittest.main()
