import io
import json
import unittest
import urllib.error
from contextlib import redirect_stderr
from unittest.mock import call, patch

import add_ics_event as calendar


class RequestEventsTests(unittest.TestCase):
    def response(self):
        return io.BytesIO(json.dumps({
            "candidates": [{"content": {"parts": [{"text": '{"schema_version": 1, "events": []}'}]}}],
        }).encode("utf-8"))

    def http_error(self, code):
        return urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com", code, "API error",
            {}, io.BytesIO(b'{"error":{"message":"high demand"}}'),
        )

    @patch("add_ics_event.time.sleep")
    @patch("add_ics_event.urllib.request.urlopen")
    def test_transient_http_errors_recover(self, urlopen, sleep):
        for code in calendar.RETRYABLE_STATUS_CODES:
            with self.subTest(code=code):
                urlopen.reset_mock()
                sleep.reset_mock()
                urlopen.side_effect = [self.http_error(code), self.response()]
                with redirect_stderr(io.StringIO()) as stderr:
                    result = calendar.request_events("予定", 2026, "test-key")
                self.assertEqual(result, {"schema_version": 1, "events": []})
                self.assertEqual(urlopen.call_count, 2)
                sleep.assert_called_once_with(10)
                self.assertIn(f"HTTP {code}", stderr.getvalue())
                self.assertIn("high demand", stderr.getvalue())
                request = urlopen.call_args.args[0]
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
                self.assertEqual(json.loads(request.data)["generationConfig"], {
                    "temperature": 0, "responseMimeType": "application/json",
                })

    @patch("add_ics_event.time.sleep")
    @patch("add_ics_event.urllib.request.urlopen")
    def test_persistent_503_exhausts_attempts(self, urlopen, sleep):
        urlopen.side_effect = [self.http_error(503) for _ in range(3)]
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503.*high demand"):
                calendar.request_events("予定", 2026, "test-key")
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(10), call(20)])

    @patch("add_ics_event.time.sleep")
    @patch("add_ics_event.urllib.request.urlopen")
    def test_non_retryable_http_errors_fail_immediately(self, urlopen, sleep):
        for code in (400, 401, 403):
            with self.subTest(code=code):
                urlopen.reset_mock()
                urlopen.side_effect = self.http_error(code)
                with self.assertRaisesRegex(RuntimeError, f"HTTP {code}"):
                    calendar.request_events("予定", 2026, "test-key")
                urlopen.assert_called_once()
                sleep.assert_not_called()

    @patch("add_ics_event.time.sleep")
    @patch("add_ics_event.urllib.request.urlopen")
    def test_network_errors_recover(self, urlopen, sleep):
        for error in (urllib.error.URLError("connection reset"), TimeoutError("timeout")):
            with self.subTest(error=error):
                sleep.reset_mock()
                urlopen.side_effect = [error, self.response()]
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        calendar.request_events("予定", 2026, "test-key")["schema_version"], 1,
                    )
                sleep.assert_called_once_with(10)

    @patch("add_ics_event.time.sleep")
    @patch("add_ics_event.urllib.request.urlopen")
    def test_persistent_network_error_fails(self, urlopen, sleep):
        urlopen.side_effect = TimeoutError("timeout")
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "通信に失敗"):
                calendar.request_events("予定", 2026, "test-key")
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(10), call(20)])

    @patch("add_ics_event.time.sleep")
    @patch("add_ics_event.urllib.request.urlopen")
    def test_invalid_json_fails_without_retry(self, urlopen, sleep):
        urlopen.return_value = io.BytesIO(b'{"candidates": []}')
        with self.assertRaisesRegex(RuntimeError, "JSON 生成に失敗"):
            calendar.request_events("予定", 2026, "test-key")
        urlopen.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
