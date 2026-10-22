import json
import logging
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from buffy.api import BuffyApi, ISSUE_URL_LIMIT, build_issue_url, mask_secrets


class DiagnosticReportTests(unittest.TestCase):
    def test_export_keeps_rotated_logs_long_lines_and_masks_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            long_line = 'LONG-' + 'я' * 1800
            (root / 'buffy.log.1').write_text('OLD-FIRST\n' + long_line + '\n', encoding='utf-8')
            (root / 'buffy.log').write_text('NEW-LAST password="two words secret"\n', encoding='utf-8')
            api = BuffyApi()
            api._raw.extend(['memory-only', 'login example@mail.com mypassword'])
            with patch('buffy.api._data_file', return_value=str(root / 'user_data.json')), \
                    patch('buffy.api.DataManager.get_credentials', return_value=('example@mail.com', 'mypassword')), \
                    patch('buffy.api.DataManager.load_json', return_value={}), \
                    patch('buffy.api._file_log', return_value=logging.getLogger('test.report')), \
                    patch.object(api, '_reveal_report') as reveal:
                result = api.export_logs()
            self.assertTrue(result['ok'])
            text = pathlib.Path(result['path']).read_text(encoding='utf-8')
            for retained in ('OLD-FIRST', long_line, 'NEW-LAST', 'memory-only'):
                self.assertIn(retained, text)
            for secret in ('two words secret', 'example@mail.com', 'mypassword'):
                self.assertNotIn(secret, text)
            self.assertLess(text.index('OLD-FIRST'), text.index('NEW-LAST'))
            reveal.assert_called_once()

    def test_report_failure_still_opens_github_with_short_log(self):
        api = BuffyApi()
        with patch.object(api, '_save_report', side_effect=OSError('disk full')), \
                patch.object(api, '_report_secrets', return_value=()), \
                patch('buffy.api.webbrowser.open') as browser:
            result = api.report_problem('reading broken')
        self.assertTrue(result['ok'])
        self.assertIn('warning', result)
        body = parse_qs(urlsplit(browser.call_args.args[0]).query)['body'][0]
        self.assertNotIn('Buffy-diagnostic.txt', body)

    def test_report_creates_file_before_opening_github_and_mentions_attachment(self):
        api = BuffyApi()
        with patch.object(api, '_save_report', return_value='C:/reports/Buffy-diagnostic.txt') as save, \
                patch.object(api, '_report_secrets', return_value=()), \
                patch.object(api, '_reveal_report'), patch('buffy.api.webbrowser.open') as browser:
            result = api.report_problem('reading broken')
        self.assertTrue(result['ok'])
        save.assert_called_once_with('reading broken')
        body = parse_qs(urlsplit(browser.call_args.args[0]).query)['body'][0]
        self.assertIn('Buffy-diagnostic.txt', body)
        self.assertIn('прикреп', body.lower())

    def test_truncated_issue_explicitly_requests_full_file(self):
        url = build_issue_url('broken', ['old ' + 'я' * 900] * 500,
                              report_name='Buffy-diagnostic.txt')
        self.assertLessEqual(len(url), ISSUE_URL_LIMIT)
        body = parse_qs(urlsplit(url).query)['body'][0]
        self.assertIn('Buffy-diagnostic.txt', body)
        self.assertIn('сокращ', body.lower())

    def test_masking_handles_quoted_multiline_values_and_auth_headers(self):
        text = 'password="two words"\nCookie: session=abc; other=xyz\nAuthorization: Bearer short-secret'
        masked = mask_secrets(text)
        for value in ('two words', 'abc', 'xyz', 'short-secret'):
            self.assertNotIn(value, masked)

    def test_reading_response_summary_keeps_error_but_no_reward_tokens_or_html(self):
        from diagnostics import reading_response_details
        response = SimpleNamespace(status_code=200, headers={'Content-Type': 'application/json'},
                                   text=json.dumps({'success': False, 'message': 'already read',
                                                    'token': 'short-secret', 'unknown': 'private-value'}))
        summary = json.dumps(reading_response_details(response))
        self.assertIn('already read', summary)
        self.assertNotIn('short-secret', summary)
        self.assertNotIn('private-value', summary)
        response.text = '<html><input value="private-html"></html>'
        self.assertNotIn('private-html', json.dumps(reading_response_details(response)))

    def test_folder_failure_keeps_export_and_gives_its_path(self):
        api = BuffyApi()
        with patch.object(api, '_save_report', return_value='C:/reports/Buffy-diagnostic.txt'), \
                patch.object(api, '_reveal_report', side_effect=OSError('explorer failed')):
            result = api.export_logs()
        self.assertTrue(result['ok'])
        self.assertIn(result['path'], result['warning'])

    def test_huge_description_with_emoji_still_fits_issue_url(self):
        url = build_issue_url('📖' * 10000, [], report_name='Buffy-diagnostic.txt')
        self.assertLessEqual(len(url), ISSUE_URL_LIMIT)

    def test_reading_response_redacts_known_secrets_and_omits_nested_validation(self):
        from diagnostics import reading_response_details
        response = SimpleNamespace(status_code=403, headers={}, text=json.dumps({
            'message': 'login=my-password', 'errors': {'password': ['my-password']},
            'token': 'short-token'}))
        text = json.dumps(reading_response_details(response, secrets=['my-password']))
        self.assertIn('403', text)
        self.assertNotIn('my-password', text)
        self.assertNotIn('short-token', text)


if __name__ == '__main__':
    unittest.main()
