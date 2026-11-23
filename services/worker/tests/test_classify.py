from unittest import TestCase

from worker.classify import Observation, classify, needs_get_fallback

URL = "http://fake-site:8000/r/lecture5"


def seen(status=None, final_url=URL, latency_ms=50, error=None):
    return Observation(URL, status=status, final_url=final_url, latency_ms=latency_ms, error=error)


class ClassifyTests(TestCase):
    # One case per fake-site mode, plus the edges around each rule.

    def test_ok(self):
        self.assertEqual(classify(seen(200)), "ok")
        self.assertEqual(classify(seen(204)), "ok")

    def test_slow_success_is_degraded_but_slow_error_is_still_error(self):
        self.assertEqual(classify(seen(200, latency_ms=2000)), "ok")  # boundary: not over
        self.assertEqual(classify(seen(200, latency_ms=3001)), "slow")
        self.assertEqual(classify(seen(500, latency_ms=3001)), "error")

    def test_not_found(self):
        self.assertEqual(classify(seen(404)), "not_found")
        self.assertEqual(classify(seen(410)), "not_found")

    def test_server_and_transient_errors(self):
        for status in (500, 502, 503, 429, 400):
            self.assertEqual(classify(seen(status)), "error", status)

    def test_transport_errors(self):
        for error in ("timeout", "connect_error", "too_many_redirects"):
            self.assertEqual(classify(seen(error=error, final_url=None, latency_ms=None)), "error")

    def test_unauthorized_and_forbidden(self):
        self.assertEqual(classify(seen(401)), "auth")
        self.assertEqual(classify(seen(403)), "auth")

    def test_login_redirect_ending_in_200_is_auth(self):
        # The fake site's login_redirect mode: 302 to /login, which answers 200.
        login = "http://fake-site:8000/login?next=/r/lecture5"
        self.assertEqual(classify(seen(200, final_url=login)), "auth")
        for path in ("/signin", "/sign-in", "/log-in", "/sso/start", "/cas/login", "/auth/login"):
            self.assertEqual(classify(seen(200, final_url="https://idp.example.com" + path)), "auth", path)

    def test_ordinary_redirects_are_not_auth(self):
        self.assertEqual(classify(seen(200, final_url=URL + "/")), "ok")
        self.assertEqual(classify(seen(200, final_url="https://cdn.example.com/blog/login-tips")), "ok")
        self.assertEqual(classify(seen(200, final_url="https://example.com/author/x")), "ok")

    def test_resource_whose_own_url_contains_login_is_checked_normally(self):
        url = "https://example.com/login"
        self.assertEqual(classify(Observation(url, status=200, final_url=url, latency_ms=10)), "ok")

    def test_head_fallback(self):
        # The fake site's head_not_allowed mode answers HEAD with 405.
        self.assertTrue(needs_get_fallback(405))
        self.assertTrue(needs_get_fallback(501))
        for status in (200, 404, 403, 500):
            self.assertFalse(needs_get_fallback(status), status)
