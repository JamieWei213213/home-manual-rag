from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.generate import GenerationLimiter  # noqa: E402
from homemanual.index import ManualIndex  # noqa: E402
from homemanual.server import (  # noqa: E402
    ManualApp,
    effective_port,
    make_handler,
    split_host_port,
)
from http.server import ThreadingHTTPServer  # noqa: E402
from tests.support import make_chunk, make_doc, sample_library  # noqa: E402


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(__file__).resolve().parent / "_tmp_server"
        cls.tmp.mkdir(exist_ok=True)
        cls.pdf_bytes = b"%PDF-1.4\n" + b"unit-test-pdf-body\n" * 20 + b"%%EOF\n"
        cls.pdf_path = cls.tmp / "alpha-washer.pdf"
        cls.pdf_path.write_bytes(cls.pdf_bytes)
        cls.original_manuals_dir = config.MANUALS_DIR
        config.MANUALS_DIR = cls.tmp
        docs = [make_doc("alpha-washer", "Alpha", "washer", ["AW1000"], path=cls.pdf_path)]
        chunks = [
            make_chunk(
                "alpha-washer",
                12,
                "CLEAN WASHER CYCLE Use this cycle every 30 washes with affresh washer cleaner.",
                "CLEAN WASHER CYCLE",
                printed="11",
            )
        ]
        cls.index = ManualIndex(docs=docs, chunks=chunks, built_at="test")
        cls.calls: list = []

        def recording_transport(messages, **kwargs):
            cls.calls.append(messages)
            return 'Use the "Clean Washer cycle" every 30 washes [S1].'

        cls.household_path = cls.tmp / "household.db"
        cls.app = ManualApp(
            index=cls.index,
            transport=recording_transport,
            household_path=cls.household_path,
        )
        cls.app.generation_state = {"status": "enabled", "detail": "test"}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app))
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        config.MANUALS_DIR = cls.original_manuals_dir
        cls.pdf_path.unlink()
        for leftover in cls.tmp.glob("*"):
            leftover.unlink()
        cls.tmp.rmdir()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def post(self, path: str, payload, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.url(path), data=body, headers={"Content-Type": "application/json"}
        )
        return urllib.request.urlopen(request, timeout=10)

    def post_with_headers(self, path: str, payload, headers: dict):
        body = json.dumps(payload).encode()
        merged = {"Content-Type": "application/json"}
        merged.update(headers)
        request = urllib.request.Request(self.url(path), data=body, headers=merged)
        return urllib.request.urlopen(request, timeout=10)

    def test_index_page_is_served(self):
        with urllib.request.urlopen(self.url("/"), timeout=10) as response:
            body = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("Home Appliance Manual Assistant", body)
            self.assertIn("text/html", response.headers["Content-Type"])

    def test_static_assets_are_whitelisted(self):
        with urllib.request.urlopen(self.url("/static/app.js"), timeout=10) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.url("/static/../app.py"), timeout=10)
        self.assertEqual(context.exception.code, 404)

    def test_meta_lists_documents_and_generation_state(self):
        with urllib.request.urlopen(self.url("/api/meta"), timeout=10) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["documents"][0]["doc_id"], "alpha-washer")
        self.assertIn("generation", payload)
        self.assertNotIn("api_key", payload["generation"])
        self.assertNotIn("sk-", json.dumps(payload))
        self.assertEqual(payload["household"]["storage"]["kind"], "sqlite")
        self.assertIn("appliances", payload["household"])

    def test_ask_returns_evidence(self):
        response = self.post("/api/ask", {"question": "how do I clean the washer?"})
        payload = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["evidence"][0]["doc_id"], "alpha-washer")
        self.assertEqual(payload["evidence"][0]["url"], "/manuals/alpha-washer#page=12")

    def test_ask_rejects_invalid_json(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post("/api/ask", None, raw=b"{not json")
        self.assertEqual(context.exception.code, 400)

    def test_ask_rejects_unknown_category(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post("/api/ask", {"question": "clean filter", "category": "spaceship"})
        self.assertEqual(context.exception.code, 400)

    def test_ask_rejects_oversized_body(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post("/api/ask", None, raw=b"x" * (40 * 1024))
        self.assertEqual(context.exception.code, 413)

    def test_ask_rejects_non_string_question(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post("/api/ask", {"question": 5})
        self.assertEqual(context.exception.code, 400)

    def test_generate_must_be_a_boolean(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post("/api/ask", {"question": "how do I clean the washer?", "generate": "false"})
        self.assertEqual(context.exception.code, 400)

    def test_text_plain_body_is_refused(self):
        body = json.dumps({"question": "how do I clean the washer?"}).encode()
        request = urllib.request.Request(
            self.url("/api/ask"), data=body, headers={"Content-Type": "text/plain"}
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(context.exception.code, 415)

    def test_cross_origin_post_is_refused_without_calling_the_model(self):
        calls_before = len(self.calls)
        body = json.dumps({"question": "how do I clean the washer?", "generate": True}).encode()
        for origin in ("https://evil.example", "null", f"http://127.0.0.1:1"):
            request = urllib.request.Request(
                self.url("/api/ask"),
                data=body,
                headers={"Content-Type": "application/json", "Origin": origin},
            )
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(context.exception.code, 403, origin)
        self.assertEqual(len(self.calls), calls_before)

    def test_cross_site_fetch_metadata_is_refused(self):
        calls_before = len(self.calls)
        body = json.dumps({"question": "how do I clean the washer?", "generate": True}).encode()
        request = urllib.request.Request(
            self.url("/api/ask"),
            data=body,
            headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(context.exception.code, 403)
        self.assertEqual(len(self.calls), calls_before)

    def test_foreign_host_header_is_refused(self):
        raw = self.raw_request("/api/health", host="evil.example")
        self.assertIn(b" 403 ", raw.split(b"\r\n", 1)[0])

    def test_same_origin_generation_still_works(self):
        body = json.dumps({"question": "how do I clean the washer?", "generate": True}).encode()
        request = urllib.request.Request(
            self.url("/api/ask"),
            data=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["mode"], "generated_answer")
        self.assertTrue(self.calls)

    def ask_with_generation(self):
        response = self.post_with_headers(
            "/api/ask",
            {"question": "how do I clean the washer?", "generate": True},
            {
                "Origin": f"http://127.0.0.1:{self.port}",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        return json.loads(response.read())

    def test_generation_limit_refuses_before_calling_the_transport(self):
        original = self.app.limiter
        self.app.limiter = GenerationLimiter(max_requests=1, max_concurrency=1)
        calls_before = len(self.calls)
        try:
            first = self.ask_with_generation()
            self.assertEqual(first["generation"]["result"]["status"], "generated")
            second = self.ask_with_generation()
            self.assertEqual(second["generation"]["result"]["status"], "rate_limited")
            self.assertEqual(second["mode"], "retrieved_evidence")
        finally:
            self.app.limiter = original
        self.assertEqual(len(self.calls), calls_before + 1)

    # -- My home over HTTP -------------------------------------------
    def test_appliance_lifecycle_over_http(self):
        response = self.post_with_headers(
            "/api/appliances/add",
            {"nickname": "Upstairs washer", "category": "washer", "model": "AW1000"},
            {},
        )
        self.assertEqual(response.status, 201)
        appliance_id = json.loads(response.read())["appliance"]["id"]
        try:
            renamed = self.post_with_headers(
                "/api/appliances/rename",
                {"id": appliance_id, "nickname": "Laundry washer"},
                {},
            )
            self.assertEqual(
                json.loads(renamed.read())["appliance"]["nickname"], "Laundry washer"
            )
            removed = self.post_with_headers(
                "/api/appliances/remove", {"id": appliance_id}, {}
            )
            self.assertTrue(json.loads(removed.read())["removed"])
        finally:
            self.app.household.remove(appliance_id)

    def test_unknown_model_is_refused_over_http(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_with_headers(
                "/api/appliances/add",
                {"nickname": "Ghost", "category": "washer", "model": "ZZ9000XX"},
                {},
            )
        self.assertEqual(context.exception.code, 400)
        self.assertIn("unsupported_model", context.exception.read().decode())

    def test_appliance_write_endpoints_share_the_request_guards(self):
        before = len(self.app.household.list())
        body = {"nickname": "Nope", "category": "washer", "model": "AW1000"}
        for headers in (
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.post_with_headers("/api/appliances/add", body, headers)
            self.assertEqual(context.exception.code, 403, headers)
        self.assertEqual(len(self.app.household.list()), before)

    def test_appliance_write_rejects_non_json_body(self):
        request = urllib.request.Request(
            self.url("/api/appliances/add"),
            data=b'{"nickname": "x"}',
            headers={"Content-Type": "text/plain"},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(context.exception.code, 415)

    def test_foreign_host_blocks_write_endpoints(self):
        raw = self.raw_post(
            "/api/appliances/add",
            json.dumps({"nickname": "x", "category": "washer", "model": "AW1000"}).encode(),
            host="evil.example",
        )
        self.assertIn(b" 403 ", raw.split(b"\r\n", 1)[0])

    # -- origin / host are compared, not merely present ------------------
    def test_origin_must_match_the_serving_origin_exactly(self):
        calls_before = len(self.calls)
        body = {"question": "how do I clean the washer?", "generate": True}
        mismatches = [
            f"http://localhost:{self.port}",  # an alias is not the request host
            f"https://127.0.0.1:{self.port}",  # this server speaks plain http
            "http://127.0.0.1",  # effective port 80, not the serving port
            "http://127.0.0.1:99999",  # malformed port
            "http://127.0.0.1:0",  # unusable port
            f"http://user@127.0.0.1:{self.port}",  # credentials in the origin
        ]
        for origin in mismatches:
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.post_with_headers("/api/ask", body, {"Origin": origin})
            self.assertEqual(context.exception.code, 403, origin)
        self.assertEqual(len(self.calls), calls_before)

    def test_same_origin_is_accepted_on_every_write_route(self):
        headers = {
            "Origin": f"http://127.0.0.1:{self.port}",
            "Sec-Fetch-Site": "same-origin",
        }
        asked = self.post_with_headers(
            "/api/ask", {"question": "how do I clean the washer?"}, headers
        )
        self.assertEqual(asked.status, 200)

        added = self.post_with_headers(
            "/api/appliances/add",
            {"nickname": "Same-origin washer", "category": "washer", "model": "AW1000"},
            headers,
        )
        self.assertEqual(added.status, 201)
        appliance_id = json.loads(added.read())["appliance"]["id"]
        try:
            renamed = self.post_with_headers(
                "/api/appliances/rename",
                {"id": appliance_id, "nickname": "Renamed washer"},
                headers,
            )
            self.assertEqual(renamed.status, 200)
            removed = self.post_with_headers(
                "/api/appliances/remove", {"id": appliance_id}, headers
            )
            self.assertEqual(removed.status, 200)
        finally:
            self.app.household.remove(appliance_id)

    def test_host_port_must_be_the_serving_port(self):
        for host in ("127.0.0.1", "127.0.0.1:abc", "127.0.0.1:99999", "127.0.0.1:0"):
            raw = self.raw_post("/api/appliances/add", b"{}", host=host)
            self.assertIn(b" 403 ", raw.split(b"\r\n", 1)[0], host)

    def test_wrong_but_valid_port_is_refused(self):
        raw = self.raw_post("/api/appliances/add", b"{}", host="127.0.0.1:1")
        self.assertIn(b" 403 ", raw.split(b"\r\n", 1)[0])

    def test_manual_pdf_is_served(self):
        with urllib.request.urlopen(self.url("/manuals/alpha-washer"), timeout=10) as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "application/pdf")
            self.assertEqual(body, self.pdf_bytes)

    def test_manual_range_request(self):
        request = urllib.request.Request(
            self.url("/manuals/alpha-washer"), headers={"Range": "bytes=0-4"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            self.assertEqual(response.status, 206)
            self.assertEqual(body, self.pdf_bytes[:5])
            self.assertEqual(
                response.headers["Content-Range"], f"bytes 0-4/{len(self.pdf_bytes)}"
            )

    def test_head_request_has_no_body(self):
        request = urllib.request.Request(self.url("/api/health"), method="HEAD")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")

    def test_unknown_manual_and_bad_ids(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.url("/manuals/does-not-exist"), timeout=10)
        self.assertEqual(context.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.url("/manuals/alpha-washer.exe"), timeout=10)
        self.assertEqual(context.exception.code, 400)

    def test_path_traversal_is_refused(self):
        for target in (
            "/manuals/..%2f..%2fapp.py",
            "/manuals/%2e%2e%2f%2e%2e%2fREADME.md",
            "/static/..%2fapp.py",
        ):
            raw = self.raw_request(target)
            status_line = raw.split(b"\r\n", 1)[0]
            self.assertTrue(
                b" 400 " in status_line or b" 404 " in status_line,
                f"{target} -> {status_line!r}",
            )
            self.assertNotIn(b"import argparse", raw)
            self.assertNotIn(b"Home Appliance Manual Assistant bundle", raw)

    def raw_request(self, target: str, host: str | None = None) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            request = (
                f"GET {target} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{self.port}'}\r\n"
                "Connection: close\r\n\r\n"
            )
            sock.sendall(request.encode())
            data = b""
            while True:
                block = sock.recv(4096)
                if not block:
                    break
                data += block
        return data

    def raw_post(self, target: str, body: bytes, host: str | None = None) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            head = (
                f"POST {target} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{self.port}'}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            ).encode()
            sock.sendall(head + body)
            data = b""
            while True:
                block = sock.recv(4096)
                if not block:
                    break
                data += block
        return data


class HostParsingTests(unittest.TestCase):
    """Host and Origin are parsed strictly, including their default ports."""

    def test_split_host_port(self):
        self.assertEqual(split_host_port("127.0.0.1:8099"), ("127.0.0.1", "8099"))
        self.assertEqual(split_host_port("127.0.0.1"), ("127.0.0.1", None))
        self.assertEqual(split_host_port("localhost:80"), ("localhost", "80"))
        self.assertEqual(split_host_port("[::1]:8099"), ("[::1]", "8099"))
        self.assertEqual(split_host_port("[::1]"), ("[::1]", None))
        self.assertEqual(split_host_port("::1"), (None, None))
        self.assertEqual(split_host_port("[::1]x"), (None, None))
        self.assertEqual(split_host_port(""), (None, None))
        self.assertEqual(split_host_port("   "), (None, None))

    def test_effective_port(self):
        self.assertEqual(effective_port(None), 80)
        self.assertEqual(effective_port(None, "https"), 443)
        self.assertEqual(effective_port("8099"), 8099)
        self.assertIsNone(effective_port("abc"))
        self.assertIsNone(effective_port(""))
        self.assertIsNone(effective_port("0"))
        self.assertIsNone(effective_port("70000"))
        self.assertIsNone(effective_port("-1"))


class AppLogicTests(unittest.TestCase):
    """Cases that need their own library shape, exercised directly on the app."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_app"
        self.tmp.mkdir(exist_ok=True)
        self.db = self.tmp / "household.db"

    def tearDown(self) -> None:
        for item in sorted(self.tmp.glob("*")):
            item.unlink()
        self.tmp.rmdir()

    def test_saved_appliance_binds_category_and_model(self):
        app = ManualApp(index=sample_library(), household_path=self.db)
        record = app.household.add("Kitchen dishwasher", "dishwasher", "GD3000")
        status, body = app.ask(
            {"question": "How do I clean the filter?", "appliance_id": record["id"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual({item["doc_id"] for item in body["evidence"]}, {"gamma-dishwasher"})
        self.assertEqual(body["appliance"]["id"], record["id"])
        self.assertEqual(body["filters"]["model"], "GD3000")

    def test_unsupported_saved_appliance_fails_explicitly(self):
        app = ManualApp(index=sample_library(), household_path=self.db)
        record = app.household.add("Old dishwasher", "dishwasher", "GD3000")
        app.household.index = ManualIndex(docs=[], chunks=[], built_at="test")
        status, body = app.ask(
            {"question": "How do I clean the filter?", "appliance_id": record["id"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unsupported_appliance")
        self.assertEqual(body["evidence"], [])
        self.assertTrue(body["notes"])

    def test_unknown_and_malformed_appliance_ids(self):
        app = ManualApp(index=sample_library(), household_path=self.db)
        _, body = app.ask(
            {"question": "How do I clean the filter?", "appliance_id": "app_000000000000"}
        )
        self.assertEqual(body["status"], "unknown_appliance")
        status, _ = app.ask(
            {"question": "How do I clean the filter?", "appliance_id": "not-an-id"}
        )
        self.assertEqual(status, 400)

    def test_procedural_generation_asks_for_one_model(self):
        # Two manuals cover the same procedure, so a written answer must not
        # merge them; the app asks which model instead.
        docs = [
            make_doc("alpha-washer", "Alpha", "washer", ["AW1000"]),
            make_doc("delta-washer", "Delta", "washer", ["DW1000"]),
        ]
        text = (
            "CLEAN WASHER CYCLE Use the clean washer cycle every 30 washes with washer "
            "cleaner to keep the basket fresh."
        )
        chunks = [
            make_chunk("alpha-washer", 3, text),
            make_chunk("delta-washer", 3, text),
        ]
        index = ManualIndex(docs=docs, chunks=chunks, built_at="test")
        app = ManualApp(
            index=index,
            transport=lambda messages, **kwargs: 'Use the "clean washer cycle" [S1].',
            household_path=self.db,
        )
        app.generation_state = {"status": "enabled", "detail": "test"}
        _, body = app.ask({"question": "How do I clean the washer?", "generate": True})
        self.assertEqual(body["status"], "needs_clarification")
        self.assertTrue(body["clarification"]["options"])
        self.assertEqual(body["evidence"], [])

    def test_disabled_generation_never_reaches_the_transport(self):
        calls: list = []

        def transport(messages, **kwargs):
            calls.append(kwargs)
            return 'Use the "clean washer cycle" [S1].'

        app = ManualApp(
            index=sample_library(), transport=transport, household_path=self.db
        )
        app.generation_state = {"status": "disabled", "detail": "off"}
        _, body = app.ask({"question": "How do I clean the washer?", "generate": True})
        self.assertEqual(body["mode"], "retrieved_evidence")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
