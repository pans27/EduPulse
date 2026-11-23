import asyncio
from unittest import IsolatedAsyncioTestCase

import httpx

from worker.checker import check
from worker.classify import classify

URL = "http://site.test/r/x"


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=5)


class CheckerTests(IsolatedAsyncioTestCase):
    async def test_head_fallback_to_get(self):
        def handler(request):
            return httpx.Response(405 if request.method == "HEAD" else 200)
        async with client(handler) as c:
            seen = await check(c, URL, deadline_seconds=5)
        self.assertEqual((seen.status, classify(seen)), (200, "ok"))

    async def test_login_redirect_is_followed(self):
        def handler(request):
            if request.url.path == "/login":
                return httpx.Response(200)
            return httpx.Response(302, headers={"location": "/login?next=/r/x"})
        async with client(handler) as c:
            seen = await check(c, URL, deadline_seconds=5)
        self.assertEqual(classify(seen), "auth")

    async def test_whole_check_is_bounded_even_when_each_request_is_fast_enough(self):
        # HEAD is refused after 0.3 s, then every hop of a redirect chain takes 0.3 s: no single
        # request is slow, but the sum would be ~2 s. The deadline cuts it at 0.5 s.
        async def handler(request):
            await asyncio.sleep(0.3)
            if request.method == "HEAD":
                return httpx.Response(405)
            hop = int(request.url.params.get("hop", "0"))
            return httpx.Response(302, headers={"location": f"/r/x?hop={hop + 1}"}) if hop < 4 else httpx.Response(200)
        loop = asyncio.get_running_loop()
        started = loop.time()
        async with client(handler) as c:
            seen = await check(c, URL, deadline_seconds=0.5)
        self.assertLess(loop.time() - started, 0.9)
        self.assertEqual((seen.error, classify(seen)), ("deadline_exceeded", "error"))

    async def test_transport_error(self):
        def handler(request):
            raise httpx.ConnectError("refused")
        async with client(handler) as c:
            seen = await check(c, URL, deadline_seconds=5)
        self.assertEqual(seen.error, "connect_error")
