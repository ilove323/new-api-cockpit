"""Offline public-web policy, real local sockets and real scoped Workspace.

The test-only socket adapter routes a validated public numeric destination to a
loopback fixture. Production has no allow-private flag or test-port exception.
No public request, search subscription, credential or model call is used.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from new_api_cockpit.features.chat import web_tools as web
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import canonical_json
from new_api_cockpit.features.chat.transport import AbortControl
from new_api_cockpit.features.chat.workspace import Workspace
from test_chat_storage import StorageFixture


PUBLIC = "8.8.8.8"
URL = "http://public.test/page"


def context(**changes):
    return {
        "run_id": str(uuid4()),
        "call_id": "call-1",
        "web_enabled": True,
        "deadline": time.monotonic() + 3,
        "abort": AbortControl(),
        "stop_event": threading.Event(),
        **changes,
    }


def answer(ip=PUBLIC, port=80):
    return (
        [socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, [ip, port, 0, 0]]
        if ":" in ip
        else [socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, [ip, port]]
    )


def dns_process(answers=None):
    process = Mock()
    process.communicate.return_value = (
        json.dumps(answers or [answer()]).encode(),
        None,
    )
    process.returncode = 0
    process.poll.return_value = 0
    return process


def reply(body=b"hello", *, status=200, kind="text/plain; charset=utf-8", headers=()):
    head = [
        f"HTTP/1.1 {status} fixture",
        f"Content-Type: {kind}",
        f"Content-Length: {len(body)}",
    ]
    head.extend(f"{name}: {value}" for name, value in headers)
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


@contextmanager
def upstream(
    script=None,
    *,
    body=b"hello",
    kind="text/plain; charset=utf-8",
    responses=None,
    answers=None,
    tls_context=None,
):
    """Preserve production URL/DNS checks; redirect only connect_ex in tests."""
    requests, targets = [], []
    received = threading.Event()
    default = reply(body, kind=kind)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(1)
            try:
                raw = bytearray()
                while not raw.endswith(b"\r\n\r\n"):
                    chunk = self.rfile.read(1)
                    if not chunk:
                        return
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        return
                requests.append(bytes(raw))
                received.set()
                if script:
                    script(self.connection, len(requests))
                else:
                    self.connection.sendall(
                        responses[len(requests) - 1] if responses else default
                    )
            except (OSError, IndexError):
                pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def get_request(self):
            connection, address = super().get_request()
            if tls_context is not None:
                connection.settimeout(1)
                try:
                    connection = tls_context.wrap_socket(connection, server_side=True)
                except OSError:
                    connection.close()
                    raise
            return connection, address

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    real_socket = socket.socket

    class RoutedSocket(real_socket):
        def connect_ex(self, address):
            targets.append(address)
            return super().connect_ex(server.server_address)

    network = SimpleNamespace(**{name: getattr(socket, name) for name in dir(socket)})
    network.socket = RoutedSocket
    state = SimpleNamespace(requests=requests, targets=targets, received=received)
    try:
        with (
            patch.object(web, "socket", network),
            patch.object(
                web.subprocess, "Popen", return_value=dns_process(answers)
            ) as dns,
        ):
            state.dns = dns
            yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


class WebSchemaTest(unittest.TestCase):
    def test_exact_schemas_fresh_and_no_auth_headers_path_or_backend_argument(self):
        schemas = web.schemas()
        self.assertEqual(
            [t["function"]["name"] for t in schemas],
            ["web_search", "http_fetch", "download_url"],
        )
        for spec in schemas:
            self.assertEqual(spec["type"], "function")
            self.assertIs(spec["function"]["parameters"]["additionalProperties"], False)
            self.assertFalse(
                set(spec["function"]["parameters"]["properties"])
                & {"headers", "auth", "backend", "path", "cookies", "method"}
            )
        schemas[0]["function"]["name"] = "shell"
        self.assertEqual(web.schemas()[0]["function"]["name"], "web_search")

    def test_disabled_by_default_and_exact_true_only_no_io(self):
        for enabled in (None, False, 1, "true", []):
            with self.subTest(enabled=enabled), patch.object(web, "_fetch") as fetch:
                result = web.execute(
                    "http_fetch", {"url": URL}, None, context(web_enabled=enabled)
                )
            self.assertEqual(result["code"], "WEB_DISABLED")
            fetch.assert_not_called()

    def test_strict_arguments_unknown_tools_and_context(self):
        invalid = [
            ("shell", {}, {}),
            ("http_fetch", {"url": URL, "headers": {"Authorization": "secret"}}, {}),
            ("http_fetch", {"url": URL, "method": "POST"}, {}),
            ("http_fetch", [], {}),
            ("http_fetch", {}, {}),
            ("web_search", {"query": ""}, {}),
            ("web_search", {"query": "x" * 501}, {}),
            ("web_search", {"query": "x\nsecret"}, {}),
            ("web_search", {"query": "x", "max_results": True}, {}),
            ("web_search", {"query": "x", "max_results": 6}, {}),
            ("web_search", {"query": "x", "endpoint": URL}, {}),
            ("download_url", {"url": URL, "name": "a.md", "path": "/etc"}, {}),
            ("http_fetch", {"url": URL}, {"run_id": "../../other"}),
            ("http_fetch", {"url": URL}, {"call_id": "x/y"}),
        ]
        invalid.extend(
            ("http_fetch", {"url": URL}, {"deadline": deadline})
            for deadline in (None, True, "10", float("nan"), float("inf"))
        )
        for name, args, change in invalid:
            with (
                self.subTest(name=name, args=args, change=change),
                patch.object(web, "_fetch") as fetch,
            ):
                self.assertFalse(web.execute(name, args, None, context(**change))["ok"])
                fetch.assert_not_called()

    def test_download_name_uses_existing_inert_text_whitelist(self):
        for name in (
            "../a.txt",
            "/a.txt",
            "a\\b.txt",
            ".env",
            "a.exe",
            "a.pdf",
            "a.zip",
            "a:txt",
            "x.txt.",
            " a.txt",
            "x\x00.txt",
        ):
            with self.subTest(name=name), patch.object(web, "_fetch") as fetch:
                result = web.execute(
                    "download_url", {"url": URL, "name": name}, None, context()
                )
                self.assertEqual(result["code"], "WEB_INVALID_ARGUMENTS")
                fetch.assert_not_called()
        for name in (
            "a.txt",
            "MEMORY.md",
            "PAGE.HTML",
            "inert.sh",
            "inert.py",
            "数据.csv",
        ):
            self.assertEqual(web._filename(name), name)

    def test_availability_nonsecret_no_probe_or_endpoint_and_independent_fetch(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(web, "_resolve") as resolve,
        ):
            result = web.availability()
            self.assertFalse(result["web_search"])
            self.assertTrue(result["http_fetch"])
            self.assertTrue(result["download_url"])
            resolve.assert_not_called()
        for endpoint in (
            "https://secret:never@search.test/search",
            "http://127.0.0.1/search",
            "file:///secret",
            "https://search.test/search?key=secret",
        ):
            with patch.dict(
                os.environ,
                {
                    "CHAT_WEB_SEARCH_BACKEND": "searxng",
                    "CHAT_WEB_SEARXNG_URL": endpoint,
                },
                clear=True,
            ):
                result = web.availability()
                self.assertEqual(result["search_status"], "invalid_configuration")
                self.assertNotIn("secret", repr(result))
                self.assertNotIn(endpoint, repr(result))
                self.assertTrue(result["http_fetch"])
        with patch.dict(
            os.environ,
            {
                "CHAT_WEB_SEARCH_BACKEND": "searxng",
                "CHAT_WEB_SEARXNG_URL": "https://search.test/search",
            },
            clear=True,
        ):
            settings = web.WebSettings.from_env()
            self.assertNotIn("https://search.test", repr(settings))
            self.assertEqual(web.availability()["search_status"], "configured")


class WebSSRFTest(unittest.TestCase):
    def test_all_special_ranges_ipv4_ipv6_mapped_metadata_and_transition(self):
        denied = (
            "0.0.0.0",
            "0.1.2.3",
            "10.0.0.1",
            "100.64.0.1",
            "100.100.100.200",
            "127.0.0.1",
            "169.254.169.254",
            "169.254.170.2",
            "172.16.0.1",
            "172.31.255.255",
            "192.0.0.1",
            "192.0.0.9",
            "192.0.2.1",
            "192.88.99.1",
            "192.168.1.1",
            "198.18.0.1",
            "198.19.255.255",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "239.1.1.1",
            "240.0.0.1",
            "255.255.255.255",
            "168.63.129.16",
            "::",
            "::1",
            "::ffff:8.8.8.8",
            "::ffff:127.0.0.1",
            "::127.0.0.1",
            "fc00::1",
            "fd00:ec2::254",
            "fe80::1",
            "fec0::1",
            "ff02::1",
            "2001:db8::1",
            "3fff::1",
            "2001::1",
            "2001:20::1",
            "2002:0808:0808::1",
            "64:ff9b::a9fe:a9fe",
            "64:ff9b:1::1",
            "100::1",
            "fe80::1%en0",
        )
        for value in denied:
            with self.subTest(value=value):
                with self.assertRaises(web._WebError):
                    web._public_ip(value)
                raw = f"http://[{value}]/" if ":" in value else f"http://{value}/"
                with patch.object(web, "_resolve") as resolve:
                    result = web.execute("http_fetch", {"url": raw}, None, context())
                self.assertEqual(result["code"], "WEB_URL_BLOCKED")
                resolve.assert_not_called()
        for value in (
            PUBLIC,
            "1.1.1.1",
            "2606:4700:4700::1111",
            "2001:4860:4860::8888",
        ):
            self.assertTrue(web._public_ip(value).is_global)

    def test_invalid_scheme_userinfo_port_controls_hosts_and_auth(self):
        denied = (
            "file:///etc/passwd",
            "ftp://public.test/a",
            "gopher://public.test",
            "http://a:b@public.test/",
            "https://user@public.test/",
            "http://@public.test/",
            "http://public.test:8080/",
            "https://public.test:80/",
            "http://public.test:443/",
            "http://public.test:00080/",
            "http://public.test:/",
            "http://public.test:0/",
            "http://public.test\r\nX-Auth:secret/",
            "http://public.test/a b",
            "http://public.test/\\evil",
            "http://public.test/%zz",
            "http://localhost/",
            "http://a.localhost/",
            "http://a.local/",
            "http://metadata.google.internal/",
            "http://metadata/",
            "http://public.test/#fragment",
            "http://public.test/" + "x" * 4096,
            "http://[fe80::1%25lo0]/",
        )
        for value in denied:
            with self.subTest(value=value), patch.object(web, "_resolve") as resolve:
                result = web.execute("http_fetch", {"url": value}, None, context())
                self.assertEqual(result["code"], "WEB_URL_BLOCKED")
                resolve.assert_not_called()

    def test_mixed_dns_private_second_or_first_rejected_before_socket(self):
        for answers in (
            [answer(), answer("10.0.0.2")],
            [answer("127.0.0.1"), answer()],
            [answer(), answer("::ffff:8.8.8.8")],
        ):
            with (
                patch.object(
                    web.subprocess, "Popen", return_value=dns_process(answers)
                ),
                patch.object(web.socket, "socket") as open_socket,
            ):
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertEqual(result["code"], "WEB_URL_BLOCKED")
                open_socket.assert_not_called()

    def test_all_dns_answers_bound_validate_complete_not_truncated(self):
        variants = (
            [],
            [answer()] * 33,
            [[True, 1, 6, [PUBLIC, 80]]],
            [answer(port=443)],
            [[socket.AF_INET6, 1, 6, [PUBLIC, 80, 0, 0]]],
            [[socket.AF_INET6, 1, 6, ["2606:4700:4700::1111", 80, 0, 1]]],
            [[2, 1, 6, ["hostname.test", 80]]],
        )
        for answers in variants:
            proc = dns_process()
            proc.communicate.return_value = (json.dumps(answers).encode(), None)
            with (
                self.subTest(answers=answers),
                patch.object(web.subprocess, "Popen", return_value=proc),
                patch.object(web.socket, "socket") as sock,
            ):
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertEqual(result["code"], "WEB_DNS_ERROR")
                sock.assert_not_called()

    def test_numeric_connection_pinning_all_dns_verified_and_no_second_parent_lookup(
        self,
    ):
        with upstream(
            answers=[answer(), answer("1.1.1.1"), answer("2606:4700:4700::1111")]
        ) as server:
            with patch.object(
                web.socket,
                "getaddrinfo",
                side_effect=AssertionError("second DNS lookup"),
            ):
                result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertTrue(result["ok"], result)
            self.assertEqual(server.targets, [(PUBLIC, 80)])
            self.assertEqual(server.dns.call_count, 1)
            args, kwargs = server.dns.call_args
            self.assertEqual(kwargs["env"], {})
            self.assertEqual(args[0][-2:], ["public.test", "80"])

    def test_legacy_numeric_hosts_resolved_then_rejected_not_assumed_public(self):
        for host in ("2130706433", "0177.0.0.1", "0x7f000001"):
            with (
                self.subTest(host=host),
                patch.object(
                    web.subprocess,
                    "Popen",
                    return_value=dns_process([answer("127.0.0.1")]),
                ),
            ):
                result = web.execute(
                    "http_fetch", {"url": f"http://{host}/"}, None, context()
                )
                self.assertEqual(result["code"], "WEB_URL_BLOCKED")

    def test_real_isolated_dns_helper_rejects_localhost_and_never_inherits_credentials(
        self,
    ):
        original = subprocess.Popen
        children = []

        def record(*args, **kwargs):
            self.assertEqual(kwargs["env"], {})
            self.assertNotIn("synthetic-secret", repr(args))
            proc = original(*args, **kwargs)
            children.append(proc)
            return proc

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}),
            patch.object(web.subprocess, "Popen", side_effect=record),
        ):
            with self.assertRaises(web._WebError) as error:
                web._resolve("localhost", 80, web._Budget(context()))
            self.assertEqual(error.exception.code, "WEB_URL_BLOCKED")
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)


class WebHTTPTest(unittest.TestCase):
    def test_real_tls_local_socket_validates_chain_hostname_and_original_sni(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "original.test")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("original.test")]),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        with tempfile.TemporaryDirectory() as directory:
            cert_path, key_path = (
                Path(directory) / "certificate.pem",
                Path(directory) / "key.pem",
            )
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_tls.load_cert_chain(cert_path, key_path)
            sni = []
            server_tls.set_servername_callback(lambda sock, name, ctx: sni.append(name))
            trusted = ssl.create_default_context(cafile=str(cert_path))
            with upstream(tls_context=server_tls, answers=[answer(port=443)]) as server:
                with patch.object(
                    web.ssl, "create_default_context", return_value=trusted
                ):
                    valid = web.execute(
                        "http_fetch",
                        {"url": "https://original.test/page"},
                        None,
                        context(),
                    )
                self.assertTrue(valid["ok"], valid)
                self.assertEqual(server.targets, [(PUBLIC, 443)])
                self.assertIn(b"Host: original.test\r\n", server.requests[0])
                self.assertEqual(sni[-1], "original.test")
            with upstream(tls_context=server_tls, answers=[answer(port=443)]) as server:
                with patch.object(
                    web.ssl, "create_default_context", return_value=trusted
                ):
                    mismatch = web.execute(
                        "http_fetch",
                        {"url": "https://wrong.test/page"},
                        None,
                        context(),
                    )
                self.assertEqual(mismatch["code"], "WEB_NETWORK_ERROR")
                self.assertEqual(server.requests, [])
            with upstream(tls_context=server_tls, answers=[answer(port=443)]) as server:
                untrusted = web.execute(
                    "http_fetch", {"url": "https://original.test/page"}, None, context()
                )
                self.assertEqual(untrusted["code"], "WEB_NETWORK_ERROR")
                self.assertEqual(server.requests, [])

    def test_get_fixed_headers_no_credentials_cookie_referer_or_proxy(self):
        env = {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "socks5://127.0.0.1:1",
            "OPENAI_API_KEY": "synthetic-secret",
        }
        with patch.dict(os.environ, env), upstream(body="中文正文".encode()) as server:
            result = web.execute(
                "http_fetch", {"url": URL + "?q=%E4%B8%AD"}, None, context()
            )
            raw = server.requests[0]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "中文正文")
        self.assertTrue(
            raw.startswith(b"GET /page?q=%E4%B8%AD HTTP/1.1\r\nHost: public.test\r\n")
        )
        self.assertIn(b"Accept-Encoding: identity\r\n", raw)
        self.assertIn(b"Connection: close\r\n", raw)
        for forbidden in (
            b"Authorization",
            b"Cookie",
            b"Proxy-",
            b"Referer",
            b"synthetic-secret",
        ):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(result["source_url"], URL + "?q=%E4%B8%AD")
        self.assertIs(result["untrusted"], True)

    def test_html_extracts_readable_text_strips_scripts_styles_navigation_hidden(self):
        html = """<html><head><title>skip-title</title><style>skip-style</style></head><body>
        <nav><br>skip-nav</nav><script>skip-script</script><header>skip-header</header>
        <main><h1>标题 &amp; 内容</h1><p>你好 <b>world</b>!</p><div hidden>skip-hidden</div>
        <span aria-hidden='true'>skip-aria</span><span style='display: none'>skip-css</span>
        <span aria-hidden>visible</span><script src='ignored'/><p>下一段</p></main><footer>skip-footer</footer></body></html>"""
        with upstream(body=html.encode(), kind="text/html; charset=UTF-8"):
            result = web.execute("http_fetch", {"url": URL}, None, context())
        self.assertTrue(result["ok"], result)
        self.assertIn("标题 & 内容", result["text"])
        self.assertIn("你好 world!", result["text"])
        self.assertNotIn("skip-", result["text"])
        self.assertNotIn("<", result["text"])
        self.assertFalse(result["truncated"])

    def test_json_utf8_and_escaped_output_total_budget_not_character_count(self):
        for kind, body in (
            ("text/plain", "界" * 40000),
            ("application/json", '\\"' * 30000),
            ("text/html", "<p>" + "界" * 40000 + "</p>"),
        ):
            with self.subTest(kind=kind), upstream(body=body.encode(), kind=kind):
                result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(canonical_json(result)), 32768)
            self.assertEqual(result["bytes_read"], len(body.encode()))
            result["text"].encode("utf-8", errors="strict")

    def test_strict_utf8_mime_encoding_and_framing_never_return_raw_error(self):
        invalid = [
            reply(b"\xff"),
            reply(b"a\x00b"),
            reply(b"binary", kind="image/png"),
            reply(b"x", kind="text/plain; charset=latin1"),
            reply(b"secret-response", status=500),
            reply(b"x", headers=[("Content-Encoding", "gzip")]),
            reply(b"x", headers=[("Content-Length", "1")]),
            reply(b"x", headers=[("Content-Type", "text/plain")]),
            reply(b"x", headers=[("Transfer-Encoding", "chunked")]),
            b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\nx\r\n",
        ]
        for raw in invalid:
            with self.subTest(raw=raw), upstream(responses=[raw]) as server:
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertFalse(result["ok"], result)
                self.assertNotIn("secret-response", str(result))
                self.assertEqual(len(server.requests), 1)

    def test_chunked_success_and_wire_limit_counts_framing_and_headers(self):
        raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n3\r\none\r\n3\r\ntwo\r\n0\r\n\r\n"
        with upstream(responses=[raw]):
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertEqual(result["text"], "onetwo")
        # Many tiny chunks: decoded body is tiny, but raw framing is still bounded.
        inflated = raw[: raw.index(b"3\r\none")] + b"1\r\nx\r\n" * 300 + b"0\r\n\r\n"
        with (
            upstream(responses=[inflated]),
            patch.object(web, "MAX_RESPONSE_BYTES", 1024),
        ):
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertEqual(result["code"], "WEB_RESPONSE_LIMIT")

    def test_size_limits_declared_raw_unframed_and_headers(self):
        cases = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 2097153\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n"
            + b"x" * (2097152 + 1),
            b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * 40000 + b"\r\n\r\n",
        ]
        for raw in cases:
            with self.subTest(size=len(raw)), upstream(responses=[raw]):
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertEqual(result["code"], "WEB_RESPONSE_LIMIT")

    def test_redirect_each_hop_dns_revalidated_safe_relative_final_source(self):
        redirects = [
            reply(
                b"",
                status=302,
                headers=[("Location", "/final"), ("Set-Cookie", "secret-cookie=yes")],
            ),
            reply(b"final"),
        ]
        with upstream(responses=redirects) as server:
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["source_url"], "http://public.test/final")
            self.assertEqual(result["redirects"], 1)
            self.assertEqual(server.dns.call_count, 2)
            self.assertNotIn(b"Cookie:", server.requests[1])
        for location in (
            "http://127.0.0.1/metadata",
            "http://u:p@public.test/",
            "file:///etc/passwd",
            "http://public.test:8080/",
            "//169.254.169.254/latest/",
        ):
            with (
                self.subTest(location=location),
                upstream(
                    responses=[reply(b"", status=302, headers=[("Location", location)])]
                ) as server,
            ):
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertFalse(result["ok"])
                self.assertEqual(len(server.requests), 1)

    def test_same_host_rebinding_redirect_never_connects_second_private_answer(self):
        raw = reply(b"", status=302, headers=[("Location", "/next")])
        with upstream(responses=[raw]) as server:
            server.dns.side_effect = [dns_process(), dns_process([answer("10.0.0.1")])]
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertEqual(result["code"], "WEB_URL_BLOCKED")
            self.assertEqual(server.targets, [(PUBLIC, 80)])
            self.assertEqual(len(server.requests), 1)

    def test_redirect_limit_three_and_loop_detection(self):
        responses = [
            reply(b"", status=302, headers=[("Location", f"/hop-{i}")])
            for i in range(4)
        ]
        with upstream(responses=responses) as server:
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertEqual(result["code"], "WEB_REDIRECT_ERROR")
            self.assertEqual(len(server.requests), 4)
        with upstream(
            responses=[reply(b"", status=302, headers=[("Location", "/page")])]
        ) as server:
            self.assertEqual(
                web.execute("http_fetch", {"url": URL}, None, context())["code"],
                "WEB_REDIRECT_ERROR",
            )
            self.assertEqual(len(server.requests), 1)

    def test_https_no_downgrade_before_following_and_no_cross_origin_search(self):
        for location in ("http://public.test/plain", "http://other.test/plain"):
            with patch.object(
                web, "_request", return_value={"location": location}
            ) as request:
                result = web.execute(
                    "http_fetch", {"url": "https://public.test/start"}, None, context()
                )
                self.assertEqual(result["code"], "WEB_REDIRECT_ERROR")
                self.assertEqual(request.call_count, 1)
        with patch.object(
            web, "_request", return_value={"location": "https://other.test/"}
        ) as request:
            with self.assertRaises(web._WebError):
                web._fetch(
                    "https://search.test/search", web._Budget(context()), search=True
                )
            self.assertEqual(request.call_count, 1)

    def test_tls_verifies_original_hostname_over_pinned_numeric_connection(self):
        # TLS handshake uses the original hostname, not the public numeric IP.
        sock = Mock()
        sock.connect_ex.return_value = 0
        tls = Mock()
        tls.wrap_socket.side_effect = ssl_error = OSError("secret TLS detail")
        with (
            patch.object(
                web, "_resolve", return_value=(socket.AF_INET, 1, 6, (PUBLIC, 443))
            ),
            patch.object(web.socket, "socket", return_value=sock),
            patch.object(
                web.ssl, "create_default_context", return_value=tls
            ) as factory,
        ):
            result = web.execute(
                "http_fetch", {"url": "https://original.test/a"}, None, context()
            )
            self.assertEqual(result["code"], "WEB_NETWORK_ERROR")
            self.assertNotIn(str(ssl_error), repr(result))
            factory.assert_called_once_with()
            tls.wrap_socket.assert_called_once_with(
                sock, server_hostname="original.test", do_handshake_on_connect=False
            )
            sock.connect_ex.assert_called_once_with((PUBLIC, 443))

    def test_network_failures_never_leak_raw_exception_or_retry(self):
        with patch.object(
            web,
            "_resolve",
            side_effect=OSError("credential=synthetic-secret /host/private"),
        ) as resolve:
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertEqual(result["code"], "WEB_NETWORK_ERROR")
            self.assertNotIn("synthetic-secret", repr(result))
            self.assertNotIn("/host", repr(result))
            self.assertEqual(resolve.call_count, 1)


class WebCancellationTest(unittest.TestCase):
    def test_cancel_and_deadline_before_io(self):
        for mode in ("abort", "stop", "deadline"):
            rc = context()
            if mode == "abort":
                rc["abort"].abort()
            elif mode == "stop":
                rc["stop_event"].set()
            else:
                rc["deadline"] = time.monotonic() - 1
            with self.subTest(mode=mode), patch.object(web, "_resolve") as resolve:
                with self.assertRaises(ChatError) as error:
                    web.execute("http_fetch", {"url": URL}, None, rc)
                self.assertEqual(
                    error.exception.code,
                    "CHAT_DEADLINE" if mode == "deadline" else "CHAT_CANCELLED",
                )
                resolve.assert_not_called()

    def test_blocked_dns_helper_killed_reaped_on_abort_stop_dns_tool_and_run_deadline(
        self,
    ):
        original = subprocess.Popen
        for mode in ("abort", "stop", "dns", "tool", "run"):
            children = []
            rc = context(deadline=time.monotonic() + (0.08 if mode == "run" else 2))

            def record(*args, **kwargs):
                proc = original(*args, **kwargs)
                children.append(proc)
                return proc

            callback = rc["abort"].abort if mode == "abort" else rc["stop_event"].set
            timer = (
                threading.Timer(0.06, callback) if mode in ("abort", "stop") else None
            )
            if timer:
                timer.start()
            started = time.monotonic()
            try:
                with (
                    patch.object(web, "_RESOLVER_CODE", "import time; time.sleep(30)"),
                    patch.object(web.subprocess, "Popen", side_effect=record),
                    patch.object(web, "DNS_SECONDS", 0.08 if mode == "dns" else 2),
                    patch.object(web, "TOOL_SECONDS", 0.08 if mode == "tool" else 2),
                ):
                    if mode in ("abort", "stop", "run"):
                        with self.assertRaises(ChatError) as error:
                            web.execute("http_fetch", {"url": URL}, None, rc)
                        self.assertEqual(
                            error.exception.code,
                            "CHAT_DEADLINE" if mode == "run" else "CHAT_CANCELLED",
                        )
                    else:
                        self.assertEqual(
                            web.execute("http_fetch", {"url": URL}, None, rc)["code"],
                            "WEB_TIMEOUT",
                        )
            finally:
                if timer:
                    timer.join()
            self.assertLess(time.monotonic() - started, 0.7)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)

    def test_abort_or_stop_during_headers_and_body_closes_registered_socket(self):
        def blocked(sock, _):
            sock.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 100\r\n\r\nfirst"
            )
            time.sleep(0.3)

        for mode in ("abort", "stop"):
            with self.subTest(mode=mode), upstream(script=blocked) as server:
                rc, errors = context(), []

                def run():
                    try:
                        web.execute("http_fetch", {"url": URL}, None, rc)
                    except ChatError as exc:
                        errors.append(exc)

                worker = threading.Thread(target=run)
                worker.start()
                self.assertTrue(server.received.wait(1))
                if mode == "abort":
                    rc["abort"].abort()
                else:
                    rc["stop_event"].set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors[0].code, "CHAT_CANCELLED")
                self.assertEqual(rc["abort"]._sockets, set())

    def test_cancel_after_dns_before_registration_never_sends(self):
        rc = context()

        def resolve(*args):
            rc["abort"].abort()
            return socket.AF_INET, 1, 6, (PUBLIC, 80)

        with (
            patch.object(web, "_resolve", side_effect=resolve),
            patch.object(web.socket, "socket") as sock,
        ):
            with self.assertRaises(ChatError):
                web.execute("http_fetch", {"url": URL}, None, rc)
            sock.assert_not_called()

    def test_idle_and_total_deadline_cover_headers_and_trickling_body(self):
        def stalled(sock, _):
            sock.sendall(b"HTTP/1.1 200 OK\r\nX-Partial: ")
            time.sleep(0.3)

        with upstream(script=stalled), patch.object(web, "IDLE_SECONDS", 0.07):
            started = time.monotonic()
            self.assertEqual(
                web.execute("http_fetch", {"url": URL}, None, context())["code"],
                "WEB_TIMEOUT",
            )
            self.assertLess(time.monotonic() - started, 0.5)

        def trickle(sock, _):
            sock.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 100\r\n\r\n"
            )
            for _ in range(20):
                sock.sendall(b"x")
                time.sleep(0.02)

        with (
            upstream(script=trickle),
            patch.object(web, "TOOL_SECONDS", 0.09),
            patch.object(web, "IDLE_SECONDS", 1),
        ):
            self.assertEqual(
                web.execute("http_fetch", {"url": URL}, None, context())["code"],
                "WEB_TIMEOUT",
            )
        with upstream(script=trickle), patch.object(web, "IDLE_SECONDS", 1):
            with self.assertRaises(ChatError) as error:
                web.execute(
                    "http_fetch",
                    {"url": URL},
                    None,
                    context(deadline=time.monotonic() + 0.09),
                )
            self.assertEqual(error.exception.code, "CHAT_DEADLINE")

    def test_connect_timeout_and_cancellation_without_dispatch(self):
        sock = Mock()
        sock.connect_ex.return_value = errno.EINPROGRESS

        def not_ready(*args):
            time.sleep(0.005)
            return [], [], []

        with (
            patch.object(
                web, "_resolve", return_value=(socket.AF_INET, 1, 6, (PUBLIC, 80))
            ),
            patch.object(web.socket, "socket", return_value=sock),
            patch.object(web.select, "select", side_effect=not_ready),
            patch.object(web, "CONNECT_SECONDS", 0.06),
        ):
            result = web.execute("http_fetch", {"url": URL}, None, context())
            self.assertEqual(result["code"], "WEB_TIMEOUT")
        sock.send.assert_not_called()


class WebSearchTest(unittest.TestCase):
    def test_operator_configured_searxng_json_search_no_scraping_keys_or_model(self):
        entries = [
            {
                "title": f"标题{i}",
                "url": f"https://source.test/{i}",
                "content": "bounded snippet",
            }
            for i in range(7)
        ]
        entries.insert(
            0, {"title": "bad", "url": "http://127.0.0.1/", "content": "bad"}
        )
        env = {
            "CHAT_WEB_SEARCH_BACKEND": "searxng",
            "CHAT_WEB_SEARXNG_URL": "http://search.test/search",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            upstream(
                body=json.dumps({"results": entries}).encode(), kind="application/json"
            ) as server,
        ):
            result = web.execute(
                "web_search",
                {"query": "上海 AI", "max_results": 2},
                None,
                SimpleNamespace(**context()),
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["results"]), 2)
            self.assertTrue(result["truncated"])
            self.assertIn(
                b"GET /search?q=%E4%B8%8A%E6%B5%B7+AI&format=json HTTP/1.1",
                server.requests[0],
            )
            self.assertNotIn(b"Authorization", server.requests[0])
            self.assertNotIn("search.test", repr(result))
            self.assertEqual(result["backend"], "searxng")
            self.assertIs(result["untrusted"], True)

    def test_search_disabled_invalid_backend_bad_json_and_oversize_snippets(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(web, "_fetch") as fetch,
        ):
            self.assertEqual(
                web.execute("web_search", {"query": "q"}, None, context())["code"],
                "WEB_SEARCH_UNAVAILABLE",
            )
            fetch.assert_not_called()
        with patch.dict(os.environ, {"CHAT_WEB_SEARCH_BACKEND": "scrape"}, clear=True):
            self.assertEqual(
                web.execute("web_search", {"query": "q"}, None, context())["code"],
                "WEB_CONFIG_INVALID",
            )
        env = {
            "CHAT_WEB_SEARCH_BACKEND": "searxng",
            "CHAT_WEB_SEARXNG_URL": "http://search.test/search",
        }
        for body in (b"secret-malformed", b'{"results":{}}', b"null"):
            with (
                patch.dict(os.environ, env, clear=True),
                upstream(body=body, kind="application/json"),
            ):
                result = web.execute("web_search", {"query": "q"}, None, context())
                self.assertEqual(result["code"], "WEB_CONTENT_INVALID")
                self.assertNotIn("secret-malformed", repr(result))
        entries = [
            {
                "title": "界" * 1000,
                "url": f"https://source.test/{i}",
                "content": "片" * 10000,
            }
            for i in range(10)
        ]
        with (
            patch.dict(os.environ, env, clear=True),
            upstream(
                body=json.dumps({"results": entries}).encode(), kind="application/json"
            ),
        ):
            result = web.execute("web_search", {"query": "q"}, None, context())
            self.assertTrue(result["ok"], result)
            self.assertLessEqual(len(canonical_json(result)), 32768)
            self.assertEqual(len(result["results"]), 5)
            self.assertTrue(
                all(
                    len(r["title"].encode()) <= 512
                    and len(r["snippet"].encode()) <= 2048
                    for r in result["results"]
                )
            )

    def test_single_search_snippet_clipping_sets_truncation_indicator(self):
        env = {
            "CHAT_WEB_SEARCH_BACKEND": "searxng",
            "CHAT_WEB_SEARXNG_URL": "http://search.test/search",
        }
        entries = [
            {"title": "title", "url": "https://source.test/a", "content": "界" * 1000}
        ]
        with (
            patch.dict(os.environ, env, clear=True),
            upstream(
                body=json.dumps({"results": entries}).encode(), kind="application/json"
            ),
        ):
            result = web.execute("web_search", {"query": "q"}, None, context())
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["results"]), 1)


class WebDownloadTest(StorageFixture):
    def test_download_whole_round_replays_tool_pair_without_semantic_fallback(self):
        from new_api_cockpit.features.chat.working_context import project_rounds

        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            args, call_id = {"url": URL, "name": "source.md"}, "outer-download"
            self.store.append(
                self.scope,
                "assistant.message",
                {
                    "message_id": str(uuid4()),
                    "text": "",
                    "complete": True,
                    "finish_reason": "tool_calls",
                    "tool_calls": [
                        {"id": call_id, "name": "download_url", "arguments": args}
                    ],
                },
                run_id=rid,
                lease=lease,
            )
            self.store.append(
                self.scope,
                "tool.call",
                {"call_id": call_id, "name": "download_url", "arguments": args},
                run_id=rid,
                lease=lease,
            )
            with upstream(body=b"downloaded text"):
                result = web.execute(
                    "download_url",
                    args,
                    workspace,
                    context(run_id=rid, call_id=call_id),
                )
            self.assertTrue(result["ok"], result)
            self.store.append(
                self.scope,
                "tool.result",
                {
                    "call_id": call_id,
                    "name": "download_url",
                    "ok": True,
                    "result": {k: v for k, v in result.items() if k != "ok"},
                },
                run_id=rid,
                lease=lease,
            )
            self.store.append(
                self.scope,
                "assistant.message",
                {
                    "message_id": str(uuid4()),
                    "text": "已保存文件。",
                    "complete": True,
                    "finish_reason": "stop",
                    "tool_calls": [],
                },
                run_id=rid,
                lease=lease,
            )
            self.terminal(lease, rid)
            events = self.store._state(self.scope, lease=lease).events
            rounds = project_rounds(events, {}, {"protocol": "chat_completions"})
        self.assertEqual(len(rounds), 1)
        self.assertTrue(rounds[0]["complete"], rounds)
        messages = rounds[0]["messages"]
        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(messages[1]["tool_calls"][0]["id"], call_id)
        self.assertEqual(messages[2]["tool_call_id"], call_id)
        self.assertEqual(
            json.loads(messages[2]["content"])["file_id"], result["file_id"]
        )

    def test_outer_download_at_tool_limit_has_no_extra_call_and_direct_caller_is_bounded(
        self,
    ):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            for number in range(15):
                self.store.append(
                    self.scope,
                    "tool.call",
                    {
                        "call_id": f"call-{number}",
                        "name": "list_files",
                        "arguments": {"kind": "all", "offset": 0},
                    },
                    run_id=rid,
                    lease=lease,
                )
            args = {"url": URL, "name": "a.txt"}
            self.store.append(
                self.scope,
                "tool.call",
                {"call_id": "call-15", "name": "download_url", "arguments": args},
                run_id=rid,
                lease=lease,
            )
            with upstream():
                result = web.execute(
                    "download_url",
                    args,
                    workspace,
                    context(run_id=rid, call_id="call-15"),
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(self.store.files(self.scope)), 1)
            self.assertEqual(self.store.get_run(self.scope, rid)["tool_calls"], 16)
            with upstream():
                rejected = web.execute(
                    "download_url",
                    args,
                    workspace,
                    context(run_id=rid, call_id="call-16"),
                )
            self.assertEqual(rejected["code"], "WEB_WORKSPACE_ERROR")
            self.assertEqual(len(self.store.files(self.scope)), 1)
            self.terminal(lease, rid)

    def test_download_real_workspace_source_idempotency_originals_and_user_chat_isolation(
        self,
    ):
        original = self.store.upload(self.scope, "report.md", b"original", str(uuid4()))
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            rc = context(run_id=rid)
            args = {"url": "http://public.test:80/page", "name": "report.md"}
            # Match the parent's real runner, which logs download_url first.
            self.store.append(
                self.scope,
                "tool.call",
                {"call_id": rc["call_id"], "name": "download_url", "arguments": args},
                run_id=rid,
                lease=lease,
            )
            with upstream(body="# 下载\n内容".encode()) as server:
                first = web.execute("download_url", args, workspace, rc)
                second = web.execute("download_url", args, workspace, rc)
            self.assertTrue(first["ok"], first)
            self.assertEqual(first, second)
            self.assertEqual(first["source_url"], URL)
            self.assertEqual(
                len(server.requests), 2
            )  # No transparent transport retries.
            self.assertEqual(
                self.store.read_file(self.scope, first["file_id"])[1],
                "# 下载\n内容".encode(),
            )
            self.assertEqual(
                self.store.read_file(self.scope, original["id"])[1], b"original"
            )
            self.assertEqual(len(self.store.files(self.scope)), 2)
            calls = [
                event["data"]
                for event in self.store._state(self.scope, lease=lease).events
                if event["type"] == "tool.call"
            ]
            self.assertEqual(
                calls,
                [{"call_id": rc["call_id"], "name": "download_url", "arguments": args}],
            )
            self.assertNotIn("content", calls[0]["arguments"])
            self.assertEqual(self.store.get_run(self.scope, rid)["tool_calls"], 1)
            self.assertNotIn(self.root, repr(first))
            othercid = self.store.create_conversation(self.a)["id"]
            other = Workspace(
                self.store, self.store.open_conversation(self.a, othercid)
            )
            self.assertEqual(
                other.read_file(first["file_id"])["code"], "CHAT_NOT_FOUND"
            )
            otheruid = self.store.create_conversation(self.b)["id"]
            foreign = Workspace(
                self.store, self.store.open_conversation(self.b, otheruid)
            )
            self.assertEqual(
                foreign.read_file(first["file_id"])["code"], "CHAT_NOT_FOUND"
            )
            with patch.object(web, "_fetch") as fetch:
                rejected = web.execute("download_url", args, foreign, rc)
                self.assertEqual(rejected["code"], "WEB_WORKSPACE_ERROR")
                fetch.assert_not_called()
            self.terminal(lease, rid)

    def test_download_durable_dedup_ended_run_and_missing_lease_never_overwrite(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            rc = context(run_id=rid)
            args = {"url": URL, "name": "inert.sh"}
            with upstream(body=b"echo NEVER-EXECUTED"):
                first = web.execute("download_url", args, workspace, rc)
            self.assertTrue(first["ok"], first)
            with upstream(body=b"changed"):
                self.assertEqual(
                    web.execute("download_url", args, workspace, rc), first
                )
            calls = [
                event["data"]
                for event in self.store._state(self.scope, lease=lease).events
                if event["type"] == "tool.call"
            ]
            self.assertEqual(
                calls,
                [{"call_id": rc["call_id"], "name": "download_url", "arguments": args}],
            )
            self.assertEqual(self.store.get_run(self.scope, rid)["tool_calls"], 1)
            self.assertEqual(len(self.store.files(self.scope)), 1)
            self.assertEqual(
                self.store.read_file(self.scope, first["file_id"])[1],
                b"echo NEVER-EXECUTED",
            )
            self.terminal(lease, rid)
            with upstream(body=b"new"):
                rc["call_id"] = "call-2"
                self.assertEqual(
                    web.execute("download_url", args, workspace, rc)["code"],
                    "WEB_WORKSPACE_ERROR",
                )
        without_lease = Workspace(self.store, self.scope, run_id=rid)
        with upstream(body=b"x"):
            self.assertEqual(
                web.execute("download_url", args, without_lease, rc)["code"],
                "WEB_WORKSPACE_ERROR",
            )
        self.assertEqual(len(self.store.files(self.scope)), 1)

    def test_download_content_limit_strict_utf8_nul_mime_and_binary_rejection(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            rc = context(run_id=rid)
            for body, kind, expected in (
                (b"x" * 65537, "text/plain", "WEB_DOWNLOAD_LIMIT"),
                (b"\xff", "text/plain", "WEB_CONTENT_INVALID"),
                (b"a\x00b", "text/plain", "WEB_CONTENT_INVALID"),
                (b"%PDF fake", "application/pdf", "WEB_CONTENT_INVALID"),
            ):
                with (
                    self.subTest(kind=kind, size=len(body)),
                    upstream(body=body, kind=kind),
                ):
                    self.assertEqual(
                        web.execute(
                            "download_url",
                            {"url": URL, "name": "data.txt"},
                            workspace,
                            rc,
                        )["code"],
                        expected,
                    )
            self.assertEqual(self.store.files(self.scope), [])
            with upstream(body=b"x" * 65536, kind="application/octet-stream"):
                result = web.execute(
                    "download_url", {"url": URL, "name": "data.txt"}, workspace, rc
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["size_bytes"], 65536)
            self.terminal(lease, rid)

    def test_stop_before_write_no_output_and_workspace_error_secrets_sanitized(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            rc = context(run_id=rid)
            original = web._text

            def stop_after_text(*args, **kwargs):
                value = original(*args, **kwargs)
                rc["stop_event"].set()
                return value

            with upstream(), patch.object(web, "_text", side_effect=stop_after_text):
                with self.assertRaises(ChatError) as error:
                    web.execute(
                        "download_url", {"url": URL, "name": "a.txt"}, workspace, rc
                    )
                self.assertEqual(error.exception.code, "CHAT_CANCELLED")
            self.assertEqual(self.store.files(self.scope), [])
            rc = context(run_id=rid)
            with (
                upstream(),
                patch.object(
                    workspace, "write_file", side_effect=OSError("secret=/host/private")
                ),
            ):
                result = web.execute(
                    "download_url", {"url": URL, "name": "a.txt"}, workspace, rc
                )
                self.assertEqual(result["code"], "WEB_WORKSPACE_ERROR")
                self.assertNotIn("/host", repr(result))
            self.terminal(lease, rid)


if __name__ == "__main__":
    unittest.main()
