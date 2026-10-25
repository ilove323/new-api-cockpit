"""Offline response/security fixtures; no live DNS/provider, paid calls or keys.

TLS is real and verified using an ephemeral fixture certificate. Only after
production IP validation, the fixture redirects the numeric public socket to
its loopback server. No production address policy or TLS checks are disabled.
"""

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from new_api_cockpit.features.image_studio import results, transport
from test_image_transport import (
    GENERATION,
    KEY,
    PARAMETERS,
    raster,
    respond,
    result_body,
    upstream,
)

HOST = "images.fixture.test"
NEXT_HOST = "next.fixture.test"
PUBLIC = "93.184.216.34"
CONTRACT = {"format": "url", "allowed_hosts": [HOST, NEXT_HOST], "allowed_networks": []}


def address(ip=PUBLIC):
    version = ipaddress.ip_address(ip).version
    return (
        socket.AF_INET if version == 4 else socket.AF_INET6,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        (ip, 443) if version == 4 else (ip, 443, 0, 0),
    )


def url_body(url=None, **extra):
    return json.dumps(
        {"data": [{"url": url or f"https://{HOST}/image?signature=fixture"}], **extra}
    ).encode()


def response_headers(body, request_id="req_post-fixture"):
    return [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
        ("X-Request-Id", request_id),
    ]


def raster_reply(data=None, *, status=200, headers=None):
    data = raster() if data is None else data
    headers = (
        [("Content-Type", "image/png"), ("Content-Length", str(len(data)))]
        if headers is None
        else headers
    )
    return respond(data, status=status, headers=headers)


class SafeDiagnosticsTest(unittest.TestCase):
    def test_error_categories_preserve_safe_param_code_and_request_id(self):
        cases = [
            (400, "content_policy_violation", "prompt", "moderation"),
            (400, "invalid_size", "size", "params"),
            (400, "mask_not_supported", "mask", "params"),
            (429, "insufficient_quota", None, "quota"),
            (401, "invalid_api_key", None, "auth"),
            (429, "rate_limit_exceeded", None, "rate"),
            (500, "provider_arbitrary_text", "arbitrary_param", "unknown"),
        ]
        for status, code, param, category in cases:
            body = json.dumps(
                {
                    "error": {
                        "code": code,
                        "param": param,
                        "message": f"private {KEY} {PARAMETERS['prompt']} https://secret.invalid/path",
                    }
                }
            ).encode()
            with (
                self.subTest(status=status, code=code),
                upstream(respond(body, status, response_headers(body))) as posts,
                self.assertLogs(transport.logger, level="WARNING") as logs,
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.request_image(GENERATION, KEY, payload=PARAMETERS)
            error = caught.exception
            self.assertEqual(
                set(error.details),
                {
                    "category",
                    "http_status",
                    "provider_code",
                    "param",
                    "upstream_request_id",
                },
            )
            self.assertEqual(error.details["category"], category)
            self.assertEqual(error.details["http_status"], status)
            self.assertEqual(
                error.details["provider_code"], None if category == "unknown" else code
            )
            self.assertEqual(
                error.details["param"], None if category == "unknown" else param
            )
            self.assertEqual(error.details["upstream_request_id"], "req_post-fixture")
            self.assertEqual(error.uncertain, status == 500)
            self.assertEqual(len(posts), 1)
            public = str(error) + json.dumps(error.details) + str(logs.output)
            for secret in (
                KEY,
                PARAMETERS["prompt"],
                "secret.invalid",
                "private",
                "arbitrary_param",
                "provider_arbitrary_text",
            ):
                self.assertNotIn(secret, public)

    def test_message_classification_never_forwards_raw_messages(self):
        for message, category, param in (
            ("The mask is not supported by this model", "params", "mask"),
            ("Unsupported parameter: 'mask'", "params", "mask"),
            ("Rejected by the safety system", "moderation", None),
            ("insufficient balance", "quota", None),
            ("rate limit reached", "rate", None),
        ):
            with self.subTest(message=message):
                details = results.error_details(
                    {"error": {"message": message, "code": "invalid_request_error"}},
                    400,
                )
                self.assertEqual(
                    (details["category"], details["param"]), (category, param)
                )
                self.assertNotIn(message, json.dumps(details))

    def test_specific_error_type_wins_over_generic_invalid_request(self):
        for kind, category in (
            ("insufficient_quota", "quota"),
            ("content_policy_violation", "moderation"),
            ("authentication_error", "auth"),
            ("rate_limit_error", "rate"),
        ):
            with self.subTest(kind=kind):
                details = results.error_details(
                    {"error": {"code": "invalid_request_error", "type": kind}}, 400
                )
                self.assertEqual(details["category"], category)

    def test_default_status_categories_and_malformed_error_bodies(self):
        for status, category in (
            (400, "params"),
            (401, "auth"),
            (403, "auth"),
            (429, "rate"),
            (503, "unknown"),
        ):
            with (
                self.subTest(status=status),
                upstream(respond(b"{", status=status)) as posts,
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.request_image(GENERATION, KEY, payload=PARAMETERS)
            self.assertEqual(caught.exception.details["category"], category)
            self.assertEqual(len(posts), 1)

    def test_200_error_envelope_has_category_and_body_request_id(self):
        body = json.dumps(
            {
                "request_id": "req_body-fixture",
                "error": {"code": "moderation_blocked", "message": KEY},
            }
        ).encode()
        with upstream(respond(body)), self.assertRaises(transport.ImageError) as caught:
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(caught.exception.details["category"], "moderation")
        self.assertEqual(caught.exception.details["http_status"], 200)
        self.assertEqual(
            caught.exception.details["upstream_request_id"], "req_body-fixture"
        )
        self.assertTrue(caught.exception.uncertain)

    def test_request_id_secrets_are_filtered_on_success_and_failure(self):
        prompt = "private-prompt-fixture"
        identifiers = (
            KEY,
            "req_" + KEY,
            KEY.upper(),
            base64.b64encode(KEY.encode()).decode().rstrip("="),
            prompt,
            "req_" + prompt,
            "sk-unrelated-secret",
            "bearer-secret",
            "https://secret.invalid",
            "bad spaces",
            "x" * 129,
        )
        for status in (200, 400):
            for identifier in identifiers:
                body = (
                    result_body(request_id=identifier)
                    if status == 200
                    else json.dumps(
                        {
                            "request_id": identifier,
                            "error": {"code": KEY, "param": prompt, "message": KEY},
                        }
                    ).encode()
                )
                with (
                    self.subTest(status=status, identifier=identifier[:12]),
                    upstream(respond(body, status, response_headers(body, identifier))),
                ):
                    if status == 200:
                        value = transport.request_image(
                            GENERATION, KEY, payload={**PARAMETERS, "prompt": prompt}
                        )
                        self.assertNotIn("upstream_request_id", value)
                    else:
                        with self.assertRaises(transport.ImageError) as caught:
                            transport.request_image(
                                GENERATION,
                                KEY,
                                payload={**PARAMETERS, "prompt": prompt},
                            )
                        self.assertIsNone(
                            caught.exception.details["upstream_request_id"]
                        )
                        self.assertIsNone(caught.exception.details["provider_code"])
                        self.assertIsNone(caught.exception.details["param"])

    def test_duplicate_request_id_headers_not_trusted(self):
        body = result_body()
        with upstream(
            respond(
                body, headers=response_headers(body) + [("X-Request-Id", "req_other")]
            )
        ):
            value = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertNotIn("upstream_request_id", value)

    def test_body_id_preserved_when_raster_invalid(self):
        body = result_body(b"not a raster", request_id="req_bad-raster")
        with upstream(respond(body)), self.assertRaises(transport.ImageError) as caught:
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(
            caught.exception.details["upstream_request_id"], "req_bad-raster"
        )

    def test_details_constructor_rejects_arbitrary_values(self):
        error = transport.ImageError(
            "safe",
            details={
                "category": [],
                "http_status": True,
                "param": KEY,
                "provider_code": KEY,
                "upstream_request_id": "sk-example",
                "message": KEY,
            },
        )
        self.assertEqual(
            error.details,
            {
                "category": "unknown",
                "http_status": None,
                "provider_code": None,
                "param": None,
                "upstream_request_id": None,
            },
        )


class ResponseContractTest(unittest.TestCase):
    def test_invalid_contracts_rejected_before_post(self):
        for contract in (
            {},
            [],
            {"format": "auto"},
            {"format": []},
            {"format": "url"},
            {"format": "url", "allowed_hosts": "example.test"},
            {"format": "url", "allowed_hosts": ["*.example.test"]},
            {"format": "url", "allowed_hosts": ["https://example.test"]},
            {"format": "url", "allowed_hosts": ["example.test:443"]},
            {"format": "url", "allowed_hosts": ["example.test."]},
            {"format": "url", "allowed_networks": ["93.184.216.34/24"]},
            {"format": "url", "allowed_networks": ["bad"]},
            {"format": "url", "allowed_networks": [1]},
            {**CONTRACT, "headers": {"Authorization": KEY}},
        ):
            with (
                self.subTest(contract=contract),
                patch.object(transport, "_connect") as connect,
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.request_image(
                    GENERATION, KEY, payload=PARAMETERS, response_contract=contract
                )
            self.assertFalse(caught.exception.uncertain)
            connect.assert_not_called()

    def test_default_url_denial_makes_no_dns_or_get(self):
        with (
            upstream(respond(url_body())),
            patch.object(results, "_resolve_addresses") as dns,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(caught.exception.code, "IMAGE_URL_ONLY_RESPONSE")
        dns.assert_not_called()

    def test_simple_data_url_wrapper_is_preserved_as_supported_input(self):
        for image_format, mime in (("PNG", "png"), ("JPEG", "jpeg"), ("WEBP", "webp")):
            value = json.loads(result_body(raster(image_format=image_format)))
            value["data"][0]["b64_json"] = (
                f"data:image/{mime};base64," + value["data"][0]["b64_json"]
            )
            value["data"][0]["url"] = "https://never-fetch.invalid/"
            with (
                self.subTest(format=image_format),
                upstream(respond(json.dumps(value).encode())),
                patch.object(results, "resolve_url") as get,
            ):
                result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
            self.assertFalse(result["images"][0]["b64_json"].startswith("data:"))
            self.assertEqual(result["images"][0]["mime"], "image/" + mime)
            get.assert_not_called()

    def test_non_simple_data_url_and_noncanonical_base64_are_rejected(self):
        encoded = base64.b64encode(raster()).decode()
        for prefix in (
            "data:image/png;charset=utf-8;base64,",
            "data:text/html;base64,",
            "data:;base64,",
            "data:image/svg+xml;base64,",
            " data:image/png;base64,",
        ):
            with (
                self.subTest(prefix=prefix),
                upstream(
                    respond(
                        json.dumps({"data": [{"b64_json": prefix + encoded}]}).encode()
                    )
                ),
                self.assertRaises(transport.ImageError),
            ):
                transport.request_image(GENERATION, KEY, payload=PARAMETERS)

    def test_decode_child_timeout_is_bounded_reaped_and_never_reposts(self):
        original = subprocess.run
        child_pids = []

        def run(*args, **kwargs):
            self.assertNotIn(KEY, str(args) + str(kwargs.get("env")))
            try:
                return original(*args, **kwargs)
            except subprocess.TimeoutExpired as exc:
                child_pids.append(int(exc.output.strip()))
                raise

        body = result_body(request_id="req_decode-timeout")
        start = time.monotonic()
        with (
            upstream(respond(body)) as posts,
            patch.object(results, "DECODE_TIMEOUT", 0.15),
            patch.object(
                results,
                "_DECODE_CODE",
                "import os,time; print(os.getpid(),flush=True); time.sleep(5)",
            ),
            patch.object(results.subprocess, "run", side_effect=run),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(caught.exception.code, "IMAGE_TIMEOUT")
        self.assertEqual(
            caught.exception.details["upstream_request_id"], "req_decode-timeout"
        )
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(child_pids), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pids[0], 0)


class DNSPolicyTest(unittest.TestCase):
    def test_all_dns_answers_must_be_public_even_with_explicit_networks(self):
        ips = (
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.100.100.200",
            "168.63.129.16",
            "0.0.0.0",
            "224.0.0.1",
            "240.0.0.1",
            "192.0.2.1",
            "::1",
            "fe80::1",
            "fd00:ec2::254",
            "::",
            "ff02::1",
            "::ffff:127.0.0.1",
            "2002:7f00:1::",
            "64:ff9b::7f00:1",
        )
        policy = results.validate_contract(
            {**CONTRACT, "allowed_networks": ["0.0.0.0/0", "::/0"]}
        )
        watchdog = transport._SocketWatchdog(time.monotonic() + 3)
        try:
            for ip in ips:
                with (
                    self.subTest(ip=ip),
                    patch.object(
                        results,
                        "_resolve_addresses",
                        return_value=[address(), address(ip)],
                    ),
                    self.assertRaises(transport.ImageError) as caught,
                ):
                    results._validated_address(
                        results._url_target(f"https://{HOST}/"), policy, watchdog
                    )
                self.assertEqual(caught.exception.code, "IMAGE_RESULT_URL_DENIED")
        finally:
            watchdog.close()

    def test_network_only_exact_host_and_ipv6_policies(self):
        watchdog = transport._SocketWatchdog(time.monotonic() + 3)
        try:
            for ip, policy in (
                (PUBLIC, {"format": "url", "allowed_networks": ["93.184.216.0/24"]}),
                ("2606:4700:4700::1111", {"format": "url", "allowed_hosts": [HOST]}),
                (
                    "2606:4700:4700::1111",
                    {"format": "url", "allowed_networks": ["2606:4700:4700::/48"]},
                ),
            ):
                with (
                    self.subTest(ip=ip),
                    patch.object(
                        results, "_resolve_addresses", return_value=[address(ip)]
                    ),
                ):
                    chosen = results._validated_address(
                        results._url_target(f"https://{HOST}/"),
                        results.validate_contract(policy),
                        watchdog,
                    )
                self.assertEqual(chosen[4][0], ip)
            for host in ("sub." + HOST, HOST + ".evil.test", "evil.test"):
                with (
                    self.subTest(host=host),
                    patch.object(results, "_resolve_addresses") as dns,
                    self.assertRaises(transport.ImageError),
                ):
                    results._validated_address(
                        results._url_target("https://" + host),
                        results.validate_contract(CONTRACT),
                        watchdog,
                    )
                dns.assert_not_called()
            with (
                patch.object(results, "_resolve_addresses", return_value=[address()]),
                self.assertRaises(transport.ImageError),
            ):
                results._validated_address(
                    results._url_target(f"https://{HOST}/"),
                    results.validate_contract(
                        {"format": "url", "allowed_networks": ["8.8.8.0/24"]}
                    ),
                    watchdog,
                )
        finally:
            watchdog.close()

    def test_numeric_dns_fast_path_and_real_local_child(self):
        watchdog = transport._SocketWatchdog(time.monotonic() + 2)
        try:
            with patch.object(results.subprocess, "run") as child:
                self.assertEqual(
                    results._resolve_addresses(PUBLIC, watchdog)[0][4], (PUBLIC, 443)
                )
                child.assert_not_called()
            records = results._resolve_addresses("localhost", watchdog)
            self.assertTrue(records)
            self.assertTrue(
                all(
                    ipaddress.ip_address(record[4][0]).is_loopback for record in records
                )
            )
        finally:
            watchdog.close()

    def test_dns_child_deadline_and_malformed_answers(self):
        watchdog = transport._SocketWatchdog(time.monotonic() + 0.15)
        try:
            with (
                patch.object(results, "_DNS_CODE", "import time; time.sleep(5)"),
                self.assertRaises(TimeoutError),
            ):
                results._resolve_addresses(HOST, watchdog)
        finally:
            watchdog.close()
        watchdog = transport._SocketWatchdog(time.monotonic() + 3)
        try:
            for records in (
                [],
                [address()] * 33,
                [(0, 0, 0, "", (PUBLIC, 443))],
                [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (PUBLIC, 80),
                    )
                ],
                [
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        ("2606:4700::1111", 443, 0, 1),
                    )
                ],
            ):
                with (
                    self.subTest(records=str(records)[:60]),
                    patch.object(results, "_resolve_addresses", return_value=records),
                    self.assertRaises(transport.ImageError),
                ):
                    results._validated_address(
                        results._url_target(f"https://{HOST}/"),
                        results.validate_contract(CONTRACT),
                        watchdog,
                    )
        finally:
            watchdog.close()


class URLResultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="cockpit-image-tls-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.cert = Path(cls.directory.name) / "fixture.pem"
        cls.key = Path(cls.directory.name) / "fixture-key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-keyout",
                str(cls.key),
                "-out",
                str(cls.cert),
                "-subj",
                f"/CN={HOST}",
                "-addext",
                f"subjectAltName=DNS:{HOST},DNS:{NEXT_HOST}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )

    @contextmanager
    def server(self, reply, *, dns=None, trusted=True):
        requests, pinned = [], []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                requests.append({"path": self.path, "headers": self.headers})
                try:
                    reply(self)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(self.cert, self.key)
        server.socket = server_tls.wrap_socket(server.socket, server_side=True)
        client_tls = (
            ssl.create_default_context(cafile=str(self.cert))
            if trusted
            else ssl.create_default_context()
        )
        self.assertTrue(client_tls.check_hostname)
        self.assertEqual(client_tls.verify_mode, ssl.CERT_REQUIRED)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        real_connect = socket.socket.connect

        def connect(sock, target):
            if target == (PUBLIC, 443):
                pinned.append(target)
                return real_connect(sock, ("127.0.0.1", server.server_port))
            if target[0] == "127.0.0.1":
                return real_connect(sock, target)  # The separate fake POST server.
            raise AssertionError("Unexpected network connection in offline fixture")

        try:
            with (
                patch.object(socket.socket, "connect", connect),
                patch.object(
                    transport.ssl, "create_default_context", return_value=client_tls
                ),
                patch.object(
                    results,
                    "_resolve_addresses",
                    side_effect=dns or (lambda host, watchdog: [address()]),
                ) as resolver,
            ):
                yield requests, pinned, resolver
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def call(self, body=None, *, contract=None):
        body = url_body() if body is None else body
        with upstream(respond(body, headers=response_headers(body))) as posts:
            result = transport.request_image(
                GENERATION,
                KEY,
                payload=PARAMETERS,
                response_contract=CONTRACT if contract is None else contract,
            )
        self.assertEqual(len(posts), 1)
        return result

    def assert_failure(
        self, reply, *, code=None, body=None, contract=None, dns=None, trusted=True
    ):
        body = url_body() if body is None else body
        with (
            self.server(reply, dns=dns, trusted=trusted) as evidence,
            upstream(respond(body, headers=response_headers(body))) as posts,
            self.assertLogs(transport.logger, level="WARNING") as logs,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(
                GENERATION,
                KEY,
                payload=PARAMETERS,
                response_contract=CONTRACT if contract is None else contract,
            )
        self.assertEqual(len(posts), 1)
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(
            caught.exception.details["upstream_request_id"], "req_post-fixture"
        )
        if code:
            self.assertEqual(caught.exception.code, code)
        public = (
            str(caught.exception) + str(caught.exception.details) + str(logs.output)
        )
        for secret in (KEY, PARAMETERS["prompt"], HOST, "signature=fixture"):
            self.assertNotIn(secret, public)
        return evidence

    def test_https_pinned_socket_normalizes_and_never_copies_credentials(self):
        info = PngImagePlugin.PngInfo()
        info.add_text("secret", KEY)
        data = raster(pnginfo=info)
        with (
            self.server(raster_reply(data)) as (gets, pinned, dns),
            patch.object(
                transport, "_resolve_address", wraps=transport._resolve_address
            ) as post_dns,
        ):
            value = self.call()
        self.assertEqual(pinned, [(PUBLIC, 443)])
        self.assertEqual(
            [call.args[0] for call in post_dns.call_args_list], ["127.0.0.1"]
        )
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(len(gets), 1)
        self.assertEqual(gets[0]["headers"]["Host"], HOST + ":443")
        self.assertEqual(
            set(gets[0]["headers"]), {"Host", "Accept", "Accept-Encoding", "Connection"}
        )
        self.assertEqual(value["upstream_request_id"], "req_post-fixture")
        self.assertNotIn("url", value["images"][0])
        self.assertNotIn(KEY, str(value))
        with Image.open(
            io.BytesIO(base64.b64decode(value["images"][0]["b64_json"]))
        ) as image:
            self.assertNotIn("secret", image.info)
            self.assertEqual(image.size, (7, 5))

    def test_short_prompts_do_not_block_valid_result_urls_or_redirects(self):
        for prompt in ("a", "cat"):
            for redirect in (False, True):
                path = f"/cdn/path/{prompt}.png"
                header_id, body_id = f"req_{prompt}_header", f"req_{prompt}_body"
                body = url_body(
                    f"https://{HOST}" + ("/start" if redirect else path),
                    request_id=body_id,
                )

                def reply(handler):
                    if handler.path == "/start":
                        raster_reply(b"", status=302, headers=[("Location", path)])(
                            handler
                        )
                    else:
                        raster_reply()(handler)

                with (
                    self.subTest(prompt=prompt, redirect=redirect),
                    self.server(reply) as (gets, _, _),
                    upstream(
                        respond(body, headers=response_headers(body, header_id))
                    ) as posts,
                ):
                    value = transport.request_image(
                        GENERATION,
                        KEY,
                        payload={**PARAMETERS, "prompt": prompt},
                        response_contract=CONTRACT,
                    )
                self.assertEqual(len(posts), 1)
                self.assertEqual(json.loads(posts[0]["body"])["prompt"], prompt)
                self.assertEqual(len(gets), 2 if redirect else 1)
                self.assertEqual(gets[-1]["path"], path)
                self.assertEqual(value["images"][0]["mime"], "image/png")
                # URL path/host substring matches are allowed, but prompt text
                # in either diagnostic ID must still be filtered.
                self.assertNotIn("upstream_request_id", value)

    def test_short_prompt_ids_stay_filtered_in_url_failure_details_and_logs(self):
        for prompt in ("a", "cat"):
            header_id, body_id = f"req_{prompt}_header", f"req_{prompt}_body"
            body = url_body(f"https://{HOST}/cdn/path/{prompt}.png", request_id=body_id)
            with (
                self.subTest(prompt=prompt),
                self.server(raster_reply(b"invalid raster")) as (gets, _, _),
                upstream(
                    respond(body, headers=response_headers(body, header_id))
                ) as posts,
                self.assertLogs(transport.logger, level="WARNING") as logs,
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.request_image(
                    GENERATION,
                    KEY,
                    payload={**PARAMETERS, "prompt": prompt},
                    response_contract=CONTRACT,
                )
            self.assertEqual((len(posts), len(gets)), (1, 1))
            self.assertEqual(caught.exception.code, "IMAGE_INVALID_RESPONSE")
            self.assertIsNone(caught.exception.details["upstream_request_id"])
            public = str(logs.output) + str(caught.exception.details)
            self.assertNotIn(header_id, public)
            self.assertNotIn(body_id, public)

    def test_explicit_public_network_alone_can_resolve_result(self):
        with self.server(raster_reply()) as (gets, pinned, _):
            value = self.call(
                contract={"format": "url", "allowed_networks": ["93.184.216.0/24"]}
            )
        self.assertEqual(len(gets), 1)
        self.assertEqual(pinned, [(PUBLIC, 443)])
        self.assertEqual(value["images"][0]["mime"], "image/png")

    def test_failed_first_pinned_address_has_no_fallback_or_retry(self):
        original = transport._connect
        attempts = []

        def connect(base, watchdog, *, address=None):
            if base.scheme == "https":
                attempts.append(address)
                raise ConnectionResetError(KEY)
            return original(base, watchdog)

        with patch.object(transport, "_connect", side_effect=connect):
            gets, pinned, dns = self.assert_failure(
                raster_reply(),
                dns=lambda host, watchdog: [address(), address("8.8.8.8")],
                code="IMAGE_CONNECTION_ERROR",
            )
        self.assertEqual((gets, pinned), ([], []))
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(attempts, [address()])

    def test_result_dns_timeout_is_bounded_after_one_post(self):
        original = results._resolve_addresses
        with (
            patch.object(transport, "CONNECT_TIMEOUT", 0.15),
            patch.object(results, "_DNS_CODE", "import time; time.sleep(5)"),
        ):
            gets, pinned, dns = self.assert_failure(
                raster_reply(), dns=original, code="IMAGE_TIMEOUT"
            )
        self.assertEqual((gets, pinned, dns.call_count), ([], [], 1))

    def test_three_redirects_revalidate_dns_and_do_not_forward_cookies_auth_or_referer(
        self,
    ):
        def reply(handler):
            count = int(handler.path.strip("/") or "0")
            if count < 3:
                host = NEXT_HOST if count == 0 else HOST
                return raster_reply(
                    b"",
                    status=302,
                    headers=[
                        ("Location", f"https://{host}/{count + 1}"),
                        ("Set-Cookie", "secret=" + KEY),
                        ("Authorization", "Bearer " + KEY),
                        ("X-Request-Id", "req_get-not-post"),
                    ],
                )(handler)
            raster_reply()(handler)

        with self.server(reply) as (gets, pinned, dns):
            value = self.call(url_body(f"https://{HOST}/0"))
        self.assertEqual(len(gets), 4)
        self.assertEqual(len(pinned), 4)
        self.assertEqual(dns.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in dns.call_args_list], [HOST, NEXT_HOST, HOST, HOST]
        )
        self.assertEqual(value["upstream_request_id"], "req_post-fixture")
        for request in gets:
            self.assertEqual(
                set(request["headers"]),
                {"Host", "Accept", "Accept-Encoding", "Connection"},
            )

    def test_fourth_redirect_is_not_followed(self):
        def reply(handler):
            count = int(handler.path.strip("/") or "0")
            raster_reply(b"", status=307, headers=[("Location", f"/{count + 1}")])(
                handler
            )

        gets, pinned, dns = self.assert_failure(
            reply,
            body=url_body(f"https://{HOST}/0"),
            code="IMAGE_RESULT_REDIRECT_LIMIT",
        )
        self.assertEqual((len(gets), len(pinned), dns.call_count), (4, 4, 4))

    def test_same_host_redirect_rebinding_is_blocked(self):
        replies = iter(([address()], [address("127.0.0.1")]))
        gets, pinned, dns = self.assert_failure(
            raster_reply(b"", status=302, headers=[("Location", "/second")]),
            dns=lambda host, watchdog: next(replies),
            code="IMAGE_RESULT_URL_DENIED",
        )
        self.assertEqual((len(gets), len(pinned), dns.call_count), (1, 1, 2))

    def test_dns_mixed_public_private_answers_block_before_connect(self):
        gets, pinned, _ = self.assert_failure(
            raster_reply(),
            dns=lambda host, watchdog: [address(), address("169.254.169.254")],
            code="IMAGE_RESULT_URL_DENIED",
        )
        self.assertEqual((gets, pinned), ([], []))

    def test_url_and_redirect_schemes_ports_userinfo_localhost_and_secret_denied(self):
        urls = (
            "http://" + HOST + "/",
            f"https://{HOST}:444/",
            f"https://{HOST}:/",
            f"https://user:password@{HOST}/",
            f"https://{HOST}/#fragment",
            f"https://{HOST}./",
            "https://localhost/",
            "https://metadata.google.internal/",
            "file:///tmp/image.png",
            "data:image/png;base64,AAAA",
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://[fe80::1%25lo0]/",
            "https://evil.test/",
            f"https://{HOST}/?key={KEY}",
            f"https://{HOST}/?key=%66{KEY[1:]}",
            f"https://{HOST}/?key={base64.b64encode(KEY.encode()).decode()}",
        )
        for url in urls:
            with self.subTest(url=url, redirect=False):
                gets, pinned, _ = self.assert_failure(
                    raster_reply(), body=url_body(url), code="IMAGE_RESULT_URL_DENIED"
                )
                self.assertEqual((gets, pinned), ([], []))
            with self.subTest(url=url, redirect=True):
                gets, pinned, _ = self.assert_failure(
                    raster_reply(b"", status=302, headers=[("Location", url)]),
                    code="IMAGE_RESULT_URL_DENIED",
                )
                self.assertEqual((len(gets), len(pinned)), (1, 1))

    def test_tls_certificate_and_hostname_verification_not_disabled(self):
        for trusted, host in ((False, HOST), (True, "wrong.fixture.test")):
            with self.subTest(trusted=trusted, host=host):
                gets, pinned, _ = self.assert_failure(
                    raster_reply(),
                    body=url_body(f"https://{host}/"),
                    contract={"format": "url", "allowed_hosts": [host]},
                    trusted=trusted,
                    code="IMAGE_CONNECTION_ERROR",
                )
                self.assertEqual(len(pinned), 1)
                self.assertEqual(gets, [])

    def test_http_errors_and_bad_redirects_never_retry_get_or_post(self):
        for status in (401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                gets, pinned, _ = self.assert_failure(
                    raster_reply(b"private " + KEY.encode(), status=status),
                    code="IMAGE_RESULT_FETCH_FAILED",
                )
                self.assertEqual((len(gets), len(pinned)), (1, 1))
        gets, _, _ = self.assert_failure(
            raster_reply(
                b"", status=302, headers=[("Location", "/one"), ("Location", "/two")]
            )
        )
        self.assertEqual(len(gets), 1)
        gets, _, _ = self.assert_failure(
            raster_reply(
                b"", status=302, headers=[("Location", "/image?signature=fixture")]
            ),
            code="IMAGE_RESULT_REDIRECT_LIMIT",
        )
        self.assertEqual(len(gets), 1)

    def test_url_raster_mime_pixels_bytes_and_framing_bounded(self):
        data = raster()
        for headers in (
            [("Content-Type", "text/html"), ("Content-Length", str(len(data)))],
            [("Content-Type", "image/svg+xml"), ("Content-Length", str(len(data)))],
            [("Content-Type", "image/png"), ("Content-Encoding", "gzip")],
            [
                ("Content-Type", "image/png"),
                ("Content-Length", "10"),
                ("Content-Length", "10"),
            ],
            [
                ("Content-Type", "image/png"),
                ("Content-Length", "10"),
                ("Transfer-Encoding", "chunked"),
            ],
            [
                ("Content-Type", "image/png"),
                ("Content-Length", str(transport.MAX_OUTPUT_BYTES + 1)),
            ],
        ):
            with self.subTest(headers=headers):
                self.assert_failure(raster_reply(data, headers=headers))
        self.assert_failure(
            raster_reply(b"not a raster"), code="IMAGE_INVALID_RESPONSE"
        )
        with patch.object(transport, "MAX_OUTPUT_PIXELS", 34):
            self.assert_failure(raster_reply(data), code="IMAGE_INVALID_RESPONSE")
        for chunked in (False, True):
            with (
                self.subTest(chunked=chunked),
                patch.object(transport, "MAX_OUTPUT_BYTES", 256),
            ):
                oversized = b"a" * 257
                headers = [("Content-Type", "image/png"), ("Connection", "close")]
                if chunked:
                    oversized = b"101\r\n" + oversized + b"\r\n0\r\n\r\n"
                    headers.append(("Transfer-Encoding", "chunked"))
                self.assert_failure(
                    raster_reply(oversized, headers=headers),
                    code="IMAGE_RESPONSE_TOO_LARGE",
                )

    def test_url_exact_byte_limit_chunked_and_eof_success(self):
        data = raster()
        for framing in ("length", "chunked", "eof"):
            headers = [("Content-Type", "image/png"), ("Connection", "close")]
            body = data
            if framing == "length":
                headers.append(("Content-Length", str(len(data))))
            if framing == "chunked":
                headers.append(("Transfer-Encoding", "chunked"))
                body = f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n"
            with (
                self.subTest(framing=framing),
                self.server(raster_reply(body, headers=headers)),
                patch.object(transport, "MAX_OUTPUT_BYTES", len(data)),
            ):
                value = self.call()
            self.assertEqual(value["images"][0]["width"], 7)

    def test_slow_url_headers_and_body_use_absolute_deadline(self):
        def headers(handler):
            for byte in b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n":
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.012)

        def body(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nConnection: close\r\n\r\n"
            )
            for byte in raster():
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.012)

        for reply in (headers, body):
            started = time.monotonic()
            with (
                self.subTest(phase=reply.__name__),
                patch.object(results, "RESULT_TIMEOUT", 0.16),
            ):
                self.assert_failure(reply, code="IMAGE_TIMEOUT")
            self.assertLess(time.monotonic() - started, 1)

    def test_redirects_share_one_absolute_budget(self):
        def reply(handler):
            count = int(handler.path.strip("/") or "0")
            time.sleep(0.1)
            raster_reply(b"", status=302, headers=[("Location", f"/{count + 1}")])(
                handler
            )

        with patch.object(results, "RESULT_TIMEOUT", 0.16):
            gets, _, _ = self.assert_failure(
                reply, body=url_body(f"https://{HOST}/0"), code="IMAGE_TIMEOUT"
            )
        self.assertEqual(len(gets), 2)

    def test_post_and_url_share_transport_total_deadline(self):
        body = url_body()

        def post(handler):
            time.sleep(0.10)
            respond(body, headers=response_headers(body))(handler)

        def get(handler):
            time.sleep(0.25)
            raster_reply()(handler)

        started = time.monotonic()
        with (
            self.server(get) as (gets, _, _),
            upstream(post) as posts,
            patch.object(transport, "TOTAL_TIMEOUT", 0.22),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(
                GENERATION, KEY, payload=PARAMETERS, response_contract=CONTRACT
            )
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(caught.exception.code, "IMAGE_TIMEOUT")
        self.assertEqual(
            caught.exception.details["upstream_request_id"], "req_post-fixture"
        )
        self.assertEqual((len(posts), len(gets)), (1, 1))


if __name__ == "__main__":
    unittest.main()
