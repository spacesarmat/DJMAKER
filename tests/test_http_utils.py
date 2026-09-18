"""Регрессия общих HTTP-хелперов провайдеров: retry, throttle, извлечение JSON."""

from __future__ import annotations

import json
import re
import socket
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from djmaker.plugins.base import MetadataProviderError
from djmaker.plugins.http_utils import (
    RateLimiter,
    collect_react_state_patches,
    extract_embedded_json,
    is_server_error,
    retry_after_seconds,
    strip_html_tags,
    urlopen_with_retry,
)


def _response(data: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = data
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class UrlopenWithRetryTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_returns_body_on_success(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _response(b"ok")

        result = urlopen_with_retry(
            urllib.request.Request("http://example.test"), provider_label="Test"
        )

        self.assertEqual(result, b"ok")

    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_http_error_is_not_retried_and_propagates(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 404, "Not Found", None, None)

        with self.assertRaises(urllib.error.HTTPError):
            urlopen_with_retry(
                urllib.request.Request("http://example.test"), provider_label="Test"
            )
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_connection_error_recovers_on_retry(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, _response(b"ok")]

        result = urlopen_with_retry(
            urllib.request.Request("http://example.test"), provider_label="Test"
        )

        self.assertEqual(result, b"ok")
        mock_sleep.assert_called_once()

    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    @patch("djmaker.plugins.http_utils.urllib.request.urlopen")
    def test_persistent_connection_error_raises_after_configured_retries(
        self, mock_urlopen: MagicMock, mock_sleep: MagicMock
    ) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        mock_urlopen.side_effect = [dns_error, dns_error, dns_error]

        with self.assertRaises(MetadataProviderError) as ctx:
            urlopen_with_retry(
                urllib.request.Request("http://example.test"),
                provider_label="Test",
                connection_retry_delays=(0.1, 0.2),
            )
        self.assertIn("Test недоступен", str(ctx.exception))
        self.assertEqual(mock_sleep.call_count, 2)


class RetryAfterAndServerErrorTests(unittest.TestCase):
    def test_retry_after_reads_header(self) -> None:
        exc = urllib.error.HTTPError("url", 429, "Too Many", {"Retry-After": "5"}, None)
        self.assertEqual(retry_after_seconds(exc), 5.0)

    def test_retry_after_falls_back_to_default(self) -> None:
        exc = urllib.error.HTTPError("url", 429, "Too Many", None, None)
        self.assertEqual(retry_after_seconds(exc, default=3.0), 3.0)

    def test_retry_after_ignores_malformed_header(self) -> None:
        exc = urllib.error.HTTPError("url", 429, "Too Many", {"Retry-After": "soon"}, None)
        self.assertEqual(retry_after_seconds(exc, default=1.5), 1.5)

    def test_is_server_error_true_for_5xx(self) -> None:
        self.assertTrue(is_server_error(urllib.error.HTTPError("u", 503, "x", None, None)))
        self.assertTrue(is_server_error(urllib.error.HTTPError("u", 500, "x", None, None)))

    def test_is_server_error_false_for_4xx(self) -> None:
        self.assertFalse(is_server_error(urllib.error.HTTPError("u", 404, "x", None, None)))


class RateLimiterTests(unittest.TestCase):
    @patch("djmaker.plugins.http_utils.time.sleep", return_value=None)
    def test_second_call_within_interval_sleeps(self, mock_sleep: MagicMock) -> None:
        limiter = RateLimiter(1.0)
        limiter.wait()
        limiter.wait()
        mock_sleep.assert_called_once()

    def test_fresh_limiter_does_not_sleep_on_first_call(self) -> None:
        limiter = RateLimiter(1.0)
        with patch("djmaker.plugins.http_utils.time.sleep") as mock_sleep:
            limiter.wait()
        mock_sleep.assert_not_called()


class ExtractEmbeddedJsonTests(unittest.TestCase):
    def test_extracts_object_after_marker(self) -> None:
        html = '<script id="data">{"a": 1, "b": [1, 2]}</script>'
        self.assertEqual(extract_embedded_json(html, 'id="data"'), {"a": 1, "b": [1, 2]})

    def test_extracts_array_after_marker(self) -> None:
        html = '<script id="data">[1, 2, {"x": 1}]</script>'
        self.assertEqual(extract_embedded_json(html, 'id="data"'), [1, 2, {"x": 1}])

    def test_missing_marker_raises(self) -> None:
        with self.assertRaises(MetadataProviderError):
            extract_embedded_json("<html></html>", 'id="data"')

    def test_marker_with_no_json_after_it_raises(self) -> None:
        with self.assertRaises(MetadataProviderError):
            extract_embedded_json('id="data" no json here', 'id="data"')

    def test_malformed_json_raises(self) -> None:
        with self.assertRaises(MetadataProviderError):
            extract_embedded_json('id="data">{"a": }', 'id="data"')

    def test_stops_at_balanced_close_ignoring_trailing_content(self) -> None:
        html = 'id="data">{"a": 1}<div>unrelated {"b": 2}</div>'
        self.assertEqual(extract_embedded_json(html, 'id="data"'), {"a": 1})


class CollectReactStatePatchesTests(unittest.TestCase):
    def test_collects_matching_paths_across_script_tags(self) -> None:
        script = lambda patches: (
            "<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])"
            f".push({json.dumps(patches)});</script>"
        )
        html = script(
            [{"op": "add", "path": "/search/items/0", "value": {"title": "A"}}]
        ) + script([{"op": "add", "path": "/search/items/1", "value": {"title": "B"}}])

        items = collect_react_state_patches(html, re.compile(r"/search/items/(\d+)"))

        self.assertEqual(items[0], {"title": "A"})
        self.assertEqual(items[1], {"title": "B"})

    def test_ignores_non_matching_paths(self) -> None:
        html = (
            "<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])"
            '.push([{"op": "add", "path": "/other/path", "value": {}}]);</script>'
        )
        items = collect_react_state_patches(html, re.compile(r"/search/items/(\d+)"))
        self.assertEqual(items, {})

    def test_no_script_tags_returns_empty_dict(self) -> None:
        self.assertEqual(
            collect_react_state_patches("<html></html>", re.compile(r"/search/items/(\d+)")), {}
        )

    def test_malformed_json_block_is_skipped_without_raising(self) -> None:
        html = (
            "<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])"
            ".push([not valid json]);</script>"
        )
        self.assertEqual(
            collect_react_state_patches(html, re.compile(r"/search/items/(\d+)")), {}
        )


class StripHtmlTagsTests(unittest.TestCase):
    def test_removes_tags_and_trims(self) -> None:
        self.assertEqual(strip_html_tags("  <b>Hello</b> <i>world</i>  "), "Hello world")


if __name__ == "__main__":
    unittest.main()
