import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken

from worker.checker import check
from worker.classify import classify
from worker.resource_credentials import decrypt_token

URL = "http://site.test/r/x"


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=5)


class CheckerTests(IsolatedAsyncioTestCase):
    async def test_bearer_token_survives_get_fallback(self):
        # Authenticate each request to the configured endpoint.
        requests = []

        def handler(request):
            requests.append((request.method, request.headers.get("Authorization")))
            if request.headers.get("Authorization") != "Bearer demo-token":
                return httpx.Response(401)
            return httpx.Response(405 if request.method == "HEAD" else 200)

        async with client(handler) as c:
            url = "https://site.example.com/r/x"
            self.assertEqual(classify(await check(c, url, api_token="wrong")), "auth")
            self.assertEqual(classify(await check(c, url, api_token="demo-token")), "ok")
        self.assertEqual(requests, [
            ("HEAD", "Bearer wrong"), ("HEAD", "Bearer demo-token"), ("GET", "Bearer demo-token"),
        ])

    async def test_authenticated_check_does_not_follow_redirects(self):
        # Keep the configured endpoint as the only recipient of the token.
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://other.example.com/r/x"})

        url = "https://site.example.com/r/x"
        async with client(handler) as c:
            self.assertEqual((await check(c, url, api_token="demo-token")).status, 302)
        self.assertEqual(requests, [url])

    async def test_cleartext_is_limited_to_opt_in_fake_site(self):
        # Reject HTTP before transport unless it is the local test fixture.
        async with client(lambda request: httpx.Response(200)) as c:
            fake_url = "http://fake-site:8000/r/x"
            with patch.dict("os.environ", EDUPULSE_LOCAL_TESTING="0"):
                observation = await check(c, fake_url, api_token="demo")
                self.assertEqual(observation.error, "insecure_credential_url")
            with patch.dict("os.environ", EDUPULSE_LOCAL_TESTING="1"):
                self.assertEqual((await check(c, URL, api_token="demo")).error, "insecure_credential_url")
                self.assertEqual((await check(c, fake_url, api_token="demo")).status, 200)

    def test_ciphertext_requires_matching_key_resource_and_url(self):
        # Recover valid tokens and reject altered ciphertext and resource bindings.
        cipher = Fernet(Fernet.generate_key())
        resource = uuid4()
        url = "https://site.example.com/r/x"
        ciphertext = cipher.encrypt(resource.bytes + url.encode() + b"\x00demo-token")
        self.assertEqual(decrypt_token(cipher, resource, url, ciphertext), "demo-token")
        for args in (
            (None, resource, url, ciphertext),
            (Fernet(Fernet.generate_key()), resource, url, ciphertext),
            (cipher, uuid4(), url, ciphertext),
            (cipher, resource, url + "2", ciphertext),
            (cipher, resource, url, b"invalid"),
        ):
            with self.assertRaises(InvalidToken):
                decrypt_token(*args)

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
