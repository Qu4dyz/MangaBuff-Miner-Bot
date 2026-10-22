"""Settings writes survive a file that Windows briefly keeps locked."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import data_management
from data_management import DataManager


class LockedSettingsFileTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'user_data.json')
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'email': 'a@b.c', 'tg_token': 't'}, f)
        sleep = patch('data_management.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_replace_is_retried_while_the_file_is_locked(self):
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError(13, 'locked')
            real(src, dst)
        with patch('data_management.os.replace', side_effect=flaky):
            DataManager.save_json(self.path, {'x': 1})
        self.assertEqual(DataManager.load_json(self.path), {'email': 'a@b.c', 'tg_token': 't', 'x': 1})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['user_data.json'])  # no temp left behind

    def test_unreadable_file_is_never_overwritten(self):
        real_open = open

        def locked(path, *a, **kw):
            if str(path) == self.path and 'r' in (a[0] if a else kw.get('mode', 'r')):
                raise PermissionError(13, 'locked')
            return real_open(path, *a, **kw)
        with patch('builtins.open', side_effect=locked):
            DataManager.save_json(self.path, {'x': 1})  # logged and skipped
        with real_open(self.path, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'email': 'a@b.c', 'tg_token': 't'})


if __name__ == '__main__':
    unittest.main()
