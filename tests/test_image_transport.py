"""Image transport contract: synthetic rasters, fake keys and loopback only.

No live providers, tokens, config files or databases are consulted. Deadline
tests shorten the constants and deliberately drip bytes faster than inactivity
timeouts. This verifies socket cancellation, not merely a mocked clock.
"""

import base64
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, PngImagePlugin

from new_api_cockpit.features.image_studio import transport

KEY = "fixture-not-a-real-provider-key"
GENERATION = "/v1/images/generations"
EDIT = "/v1/images/edits"
PARAMETERS = {
    "model": "gpt-image-fixture",
    "prompt": "synthetic 中文 fixture",
    "size": "auto",
}


def raster(size=(7, 5), mode="RGB", image_format="PNG", color=None, **options):
    with Image.new(
        mode, size, color or ("red" if mode == "RGB" else (20, 30, 40, 0))
    ) as image:
        buffer = io.BytesIO()
        image.save(buffer, format=image_format, **options)
        return buffer.getvalue()


def result_body(data=None, **extra):
    data = raster() if data is None else data
    return json.dumps(
        {"data": [{"b64_json": base64.b64encode(data).decode("ascii")}], **extra}
    ).encode()


def respond(body=None, status=200, headers=None):
    body = result_body() if body is None else body
    headers = (
        [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        if headers is None
        else headers
    )

    def reply(handler):
        handler.send_response(status)
        for name, value in headers:
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)

    return reply


@contextmanager
def upstream(reply, *, read_body=True):
    """A disposable loopback fixture; all POST attempts are captured."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            if not read_body:
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            length = int(self.headers["Content-Length"])
            if not 0 <= length <= transport.MAX_UPLOAD_BYTES:
                raise AssertionError("Unbounded fixture request")
            body = self.rfile.read(length) if read_body else b""
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": self.headers,
                    "body": body,
                }
            )
            try:
                reply(self)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # Expected when the watchdog shuts a deliberately slow peer.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    with patch.dict(
        os.environ,
        {
            "NEW_API_INTERNAL_URL": f"http://127.0.0.1:{server.server_port}",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        },
    ):
        try:
            yield requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


def multipart(request):
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: "
        + request["headers"]["Content-Type"].encode()
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + request["body"]
    )
    return [
        (part.get_param("name", header="content-disposition"), part)
        for part in message.iter_parts()
    ]


class InspectImageTest(unittest.TestCase):
    def test_frozen_constants_and_error_fields(self):
        self.assertEqual(transport.MAX_UPLOAD_BYTES, 24 * 1024 * 1024)
        self.assertEqual(transport.MAX_INPUT_BYTES, 8 * 1024 * 1024)
        self.assertEqual(transport.MAX_IMAGES, 4)
        self.assertEqual(transport.MAX_MASK_BYTES, 4_000_000)
        self.assertEqual(transport.MAX_JSON_BYTES, 48 * 1024 * 1024)
        self.assertEqual(
            (
                transport.CONNECT_TIMEOUT,
                transport.UPLOAD_TIMEOUT,
                transport.TOTAL_TIMEOUT,
            ),
            (10, 30, 300),
        )
        error = transport.ImageError("safe", 504, "FIXTURE", True)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(
            (error.status, error.code, error.uncertain), (504, "FIXTURE", True)
        )

    def test_decodes_actual_raster_preserves_format_and_alpha(self):
        for image_format, mime, extension in (
            ("PNG", "image/png", "png"),
            ("JPEG", "image/jpeg", "jpg"),
            ("WEBP", "image/webp", "webp"),
        ):
            with self.subTest(image_format=image_format):
                inspected = transport.inspect_image(raster(image_format=image_format))
                self.assertEqual(
                    set(inspected), {"data", "mime", "width", "height", "filename"}
                )
                self.assertEqual(
                    (inspected["mime"], inspected["width"], inspected["height"]),
                    (mime, 7, 5),
                )
                self.assertEqual(inspected["filename"], "image." + extension)
                with Image.open(io.BytesIO(inspected["data"])) as image:
                    image.load()
                    self.assertEqual(image.format, image_format)
        record = transport.inspect_image(raster(mode="RGBA"))
        with Image.open(io.BytesIO(record["data"])) as image:
            self.assertEqual(image.getpixel((0, 0)), (20, 30, 40, 0))

    def test_exif_is_applied_once_and_removed_with_metadata(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "private-exif-text"
        data = raster(size=(9, 4), image_format="JPEG", exif=exif)
        first = transport.inspect_image(data)
        second = transport.inspect_image(first["data"])
        self.assertEqual((first["width"], first["height"]), (4, 9))
        self.assertEqual((second["width"], second["height"]), (4, 9))
        with Image.open(io.BytesIO(first["data"])) as image:
            self.assertFalse(image.getexif())
        self.assertNotIn(b"private-exif-text", first["data"])
        info = PngImagePlugin.PngInfo()
        info.add_text("secret", "private-png-text")
        normalized = transport.inspect_image(raster(pnginfo=info))
        self.assertNotIn(b"private-png-text", normalized["data"])

    def test_rejects_non_raster_empty_corrupt_and_truncated_files(self):
        valid = raster()
        corrupted = bytearray(valid)
        corrupted[45] ^= 0xFF
        for data in (
            b"",
            b"<svg><image /></svg>",
            b"<html>secret</html>",
            b"not an image",
            valid[:40],
            bytes(corrupted),
            raster(image_format="JPEG")[:100],
            raster(image_format="GIF"),
        ):
            with (
                self.subTest(data=data[:12]),
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.inspect_image(data)
            self.assertFalse(caught.exception.uncertain)
            self.assertNotIn("secret", str(caught.exception))

    def test_rejects_png_and_webp_animation(self):
        for image_format in ("PNG", "WEBP"):
            with (
                Image.new("RGBA", (4, 4), "red") as first,
                Image.new("RGBA", (4, 4), "blue") as second,
            ):
                data = io.BytesIO()
                first.save(
                    data,
                    format=image_format,
                    save_all=True,
                    append_images=[second],
                    duration=100,
                )
            with (
                self.subTest(image_format=image_format),
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.inspect_image(data.getvalue())
            self.assertEqual(caught.exception.code, "IMAGE_ANIMATED")

    def test_pixel_header_bomb_is_rejected_before_load(self):
        data = bytearray(raster())
        data[16:24] = struct.pack(">II", 5000, 4000)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
        with (
            patch.object(
                Image.Image,
                "load",
                side_effect=AssertionError("must not allocate pixels"),
            ),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.inspect_image(bytes(data))
        self.assertEqual(caught.exception.code, "IMAGE_TOO_MANY_PIXELS")

    def test_pillow_decompression_bomb_is_sanitized(self):
        data = bytearray(raster())
        data[16:24] = struct.pack(">II", 20000, 20000)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
        with self.assertRaises(transport.ImageError) as caught:
            transport.inspect_image(bytes(data))
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn("400000000", str(caught.exception))

    def test_custom_pixel_limit_cannot_relax_default(self):
        with self.assertRaises(transport.ImageError):
            transport.inspect_image(raster(), max_pixels=34)
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(transport.ImageError):
                transport.inspect_image(raster(), max_pixels=limit)
        with (
            patch.object(transport, "MAX_INPUT_PIXELS", 34),
            self.assertRaises(transport.ImageError),
        ):
            transport.inspect_image(raster(), max_pixels=1_000_000)

    def test_raw_and_normalized_byte_limits(self):
        data = raster()
        with (
            patch.object(transport, "MAX_INPUT_BYTES", len(data) - 1),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.inspect_image(data)
        self.assertEqual(caught.exception.status, 413)
        # The original JPEG is small but a decoded, re-encoded PNG can inflate.
        with Image.frombytes(
            "RGB", (32, 32), bytes((i * 97) % 256 for i in range(32 * 32 * 3))
        ) as image:
            original = io.BytesIO()
            image.save(original, format="JPEG", quality=10)
            source = original.getvalue()
        with (
            patch.object(transport, "MAX_INPUT_BYTES", len(source) + 10),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.inspect_image(source)  # quality 95 normalized JPEG exceeds cap.
        self.assertEqual(caught.exception.status, 413)

    def test_masks_require_png_alpha_and_preserve_transparency_direction(self):
        for data in (
            raster(),
            raster(mode="RGBA", image_format="WEBP"),
            raster(image_format="JPEG"),
        ):
            with (
                self.subTest(data=data[:12]),
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.inspect_image(data, mask=True)
            self.assertEqual(caught.exception.code, "IMAGE_INVALID_MASK")
        with Image.new("RGBA", (2, 1), (20, 30, 40, 255)) as mask:
            mask.putpixel((0, 0), (20, 30, 40, 0))
            buffer = io.BytesIO()
            mask.save(buffer, format="PNG")
        record = transport.inspect_image(buffer.getvalue(), mask=True)
        self.assertEqual(record["filename"], "mask.png")
        with Image.open(io.BytesIO(record["data"])) as image:
            self.assertEqual([image.getpixel((i, 0))[3] for i in range(2)], [0, 255])

    def test_mask_palette_transparency_is_actual_alpha(self):
        with Image.new("P", (2, 2)) as mask:
            buffer = io.BytesIO()
            mask.save(buffer, format="PNG", transparency=0)
        record = transport.inspect_image(buffer.getvalue(), mask=True)
        with Image.open(io.BytesIO(record["data"])) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_mask_limit_is_strict_on_raw_and_encoded_bytes(self):
        data = raster(mode="RGBA")
        with (
            patch.object(transport, "MAX_MASK_BYTES", len(data)),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.inspect_image(data, mask=True)
        self.assertEqual(caught.exception.status, 413)
        for size in (transport.MAX_MASK_BYTES, transport.MAX_MASK_BYTES + 1):
            with (
                self.subTest(size=size),
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.inspect_image(data + bytes(size - len(data)), mask=True)
            self.assertEqual(caught.exception.status, 413)
        # Production writer enforces normalized size, independent of input size.
        with transport._BoundedBuffer(5) as buffer:
            with self.assertRaises(transport.ImageError):
                buffer.write(b"123456")


class ImageRequestTest(unittest.TestCase):
    def call(self, **changes):
        return transport.request_image(
            GENERATION, KEY, payload={**PARAMETERS, **changes}
        )

    def test_image_field_allowlist_and_optional_mask(self):
        record = transport.inspect_image(raster())
        mask = transport.inspect_image(raster(mode="RGBA"), mask=True)
        for field in ("image", "image[]"):
            for supplied_mask in (None, mask):
                with (
                    self.subTest(field=field, mask=supplied_mask is not None),
                    upstream(respond()) as requests,
                ):
                    transport.request_image(
                        EDIT,
                        KEY,
                        fields=PARAMETERS,
                        images=[record, record],
                        mask=supplied_mask,
                        image_field=field,
                    )
                parts = multipart(requests[0])
                self.assertEqual([name for name, _ in parts].count(field), 2)
                self.assertEqual(
                    [name for name, _ in parts].count("mask"),
                    int(supplied_mask is not None),
                )
                self.assertEqual(len(requests), 1)
        for field in (
            None,
            [],
            "IMAGE",
            "image[0]",
            "image\r\nX-Evil: yes",
            'image"; filename="evil',
        ):
            with (
                self.subTest(field=field),
                patch.object(transport, "_connect") as connect,
                self.assertRaises(transport.ImageError),
            ):
                transport.request_image(
                    EDIT, KEY, fields=PARAMETERS, images=[record], image_field=field
                )
            connect.assert_not_called()

    def test_mask_rejection_does_not_downgrade_or_retry(self):
        body = json.dumps(
            {"error": {"message": "mask is not supported: " + KEY, "param": "mask"}}
        ).encode()
        with (
            upstream(respond(body, status=400)) as requests,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(
                EDIT,
                KEY,
                fields=PARAMETERS,
                images=[transport.inspect_image(raster())],
                mask=transport.inspect_image(raster(mode="RGBA"), mask=True),
                image_field="image",
            )
        self.assertEqual(len(requests), 1)
        self.assertIn("mask", dict(multipart(requests[0])))
        self.assertEqual(caught.exception.details["category"], "params")
        self.assertEqual(caught.exception.details["param"], "mask")
        self.assertEqual(caught.exception.details["http_status"], 400)
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn(KEY, str(caught.exception) + str(caught.exception.details))

    def test_url_response_format_requires_contract_and_does_not_inject_fields(self):
        contract = {"format": "url", "allowed_hosts": ["images.fixture.test"]}
        for edit in (False, True):
            with self.subTest(edit=edit), upstream(respond()) as requests:
                kwargs = (
                    {
                        "fields": {**PARAMETERS, "response_format": "url"},
                        "images": [transport.inspect_image(raster())],
                    }
                    if edit
                    else {"payload": {**PARAMETERS, "response_format": "url"}}
                )
                transport.request_image(
                    EDIT if edit else GENERATION,
                    KEY,
                    response_contract=contract,
                    **kwargs,
                )
            if edit:
                self.assertEqual(
                    dict(multipart(requests[0]))["response_format"].get_payload(
                        decode=True
                    ),
                    b"url",
                )
            else:
                self.assertEqual(
                    json.loads(requests[0]["body"])["response_format"], "url"
                )
        with upstream(respond()) as requests:
            transport.request_image(
                GENERATION, KEY, payload=PARAMETERS, response_contract=contract
            )
        self.assertEqual(json.loads(requests[0]["body"]), {**PARAMETERS, "n": 1})

    def test_no_moderation_override_or_prompt_rewrite(self):
        prompt = "  untouched prompt\nwith punctuation!  "
        with upstream(respond()) as requests:
            self.call(prompt=prompt)
        self.assertEqual(json.loads(requests[0]["body"])["prompt"], prompt)
        with (
            patch.object(transport, "_connect") as connect,
            self.assertRaises(transport.ImageError),
        ):
            self.call(moderation="low")
        connect.assert_not_called()

    def test_explicit_base64_compatibility_reaches_json_and_multipart(self):
        for edit in (False, True):
            with self.subTest(edit=edit), upstream(respond()) as requests:
                if edit:
                    transport.request_image(
                        EDIT,
                        KEY,
                        fields={**PARAMETERS, "response_format": "b64_json"},
                        images=[transport.inspect_image(raster())],
                    )
                else:
                    self.call(response_format="b64_json")
                self.assertEqual(len(requests), 1)
                if edit:
                    parts = dict(multipart(requests[0]))
                    self.assertEqual(
                        parts["response_format"].get_payload(decode=True), b"b64_json"
                    )
                else:
                    self.assertEqual(
                        json.loads(requests[0]["body"])["response_format"], "b64_json"
                    )
        for value in ("url", "auto", None):
            with self.subTest(value=value), self.assertRaises(transport.ImageError):
                self.call(response_format=value)

    def test_json_request_n_one_fixed_headers_and_safe_response_metadata(self):
        body = result_body(
            usage={
                "input_tokens": 4,
                "output_tokens": 8,
                "total_tokens": 12,
                "input_tokens_details": {
                    "image_tokens": 2,
                    "text_tokens": 2,
                    "secret": KEY,
                },
                "secret": KEY,
                "password": "private",
            },
            model="private-upstream",
            key=KEY,
        )
        with upstream(
            respond(
                body,
                headers=[
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("X-Request-Id", "req_fixture:123"),
                ],
            )
        ) as requests:
            result = self.call(n=9)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual((request["method"], request["path"]), ("POST", GENERATION))
        self.assertEqual(json.loads(request["body"]), {**PARAMETERS, "n": 1})
        self.assertEqual(request["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(request["headers"]["Accept-Encoding"], "identity")
        for name in (
            "X-Forwarded-For",
            "X-Real-IP",
            "Forwarded",
            "New-Api-User",
            "Cookie",
            "Proxy-Authorization",
        ):
            self.assertIsNone(request["headers"].get(name))
        self.assertEqual(
            set(result), {"images", "duration_seconds", "usage", "upstream_request_id"}
        )
        self.assertEqual(result["upstream_request_id"], "req_fixture:123")
        self.assertEqual(
            result["usage"],
            {
                "input_tokens": 4,
                "output_tokens": 8,
                "total_tokens": 12,
                "input_tokens_details": {"image_tokens": 2, "text_tokens": 2},
            },
        )
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(result["images"][0]["mime"], "image/png")
        self.assertGreaterEqual(result["duration_seconds"], 0)

    def test_edit_repeated_image_array_order_mask_and_scalar_fields(self):
        first = transport.inspect_image(raster(image_format="JPEG"))
        first["filename"] = 'evil"\r\nX-Forwarded-For: fake.jpg'
        second = transport.inspect_image(raster(color="blue"))
        mask = transport.inspect_image(raster(mode="RGBA"), mask=True)
        with upstream(respond()) as requests:
            transport.request_image(
                EDIT,
                KEY,
                fields={**PARAMETERS, "output_compression": 75, "n": 12},
                images=[first, second],
                mask=mask,
            )
        self.assertEqual(len(requests), 1)
        entries = multipart(requests[0])
        scalars = {
            name: part.get_payload(decode=True).decode()
            for name, part in entries
            if not part.get_filename()
        }
        self.assertEqual(scalars, {**PARAMETERS, "output_compression": "75", "n": "1"})
        files = [(name, part) for name, part in entries if part.get_filename()]
        self.assertEqual([name for name, _ in files], ["image[]", "image[]", "mask"])
        self.assertEqual(
            [part.get_filename() for _, part in files],
            ["image.png", "image.png", "mask.png"],
        )
        for _, part in files:
            with Image.open(io.BytesIO(part.get_payload(decode=True))) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (7, 5))
        with Image.open(io.BytesIO(files[1][1].get_payload(decode=True))) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))
        self.assertNotIn(b"X-Forwarded-For", requests[0]["body"])
        self.assertEqual(first["mime"], "image/jpeg")  # No mutation of caller snapshot.

    def test_four_references_accepted_without_mask(self):
        records = [transport.inspect_image(raster(size=(i + 1, 2))) for i in range(4)]
        with upstream(respond()) as requests:
            transport.request_image(EDIT, KEY, fields=PARAMETERS, images=records)
        files = [part for _, part in multipart(requests[0]) if part.get_filename()]
        self.assertEqual(
            [
                Image.open(io.BytesIO(part.get_payload(decode=True))).size
                for part in files
            ],
            [(1, 2), (2, 2), (3, 2), (4, 2)],
        )

    def test_invalid_calls_fail_before_connect(self):
        record = transport.inspect_image(raster())
        mask = transport.inspect_image(raster(size=(2, 2), mode="RGBA"), mask=True)
        calls = [
            ("http://127.0.0.1/v1/images/generations", {"payload": PARAMETERS}),
            (GENERATION + "?target=x", {"payload": PARAMETERS}),
            (GENERATION, {"payload": PARAMETERS, "fields": {}}),
            (GENERATION, {"payload": PARAMETERS, "images": []}),
            (GENERATION, {"payload": {**PARAMETERS, "stream": True}}),
            (GENERATION, {"payload": {**PARAMETERS, "url": "http://elsewhere"}}),
            (GENERATION, {"payload": {**PARAMETERS, "user": "attacker"}}),
            (GENERATION, {"payload": {**PARAMETERS, "prompt": ["not", "scalar"]}}),
            (GENERATION, {"payload": {**PARAMETERS, "prompt": "\ud800"}}),
            (GENERATION, {"payload": {**PARAMETERS, "prompt": ""}}),
            (EDIT, {"payload": PARAMETERS, "fields": PARAMETERS, "images": [record]}),
            (EDIT, {"fields": PARAMETERS, "images": []}),
            (EDIT, {"fields": PARAMETERS, "images": [record] * 5}),
            (EDIT, {"fields": PARAMETERS, "images": [record], "mask": mask}),
            (EDIT, {"fields": PARAMETERS, "images": [record], "mask": [mask, mask]}),
        ]
        for path, kwargs in calls:
            with (
                self.subTest(path=path, kwargs=list(kwargs)),
                patch.object(transport, "_connect") as connect,
                self.assertRaises(transport.ImageError) as caught,
            ):
                transport.request_image(path, KEY, **kwargs)
            self.assertFalse(caught.exception.uncertain)
            connect.assert_not_called()

    def test_forged_raster_metadata_is_not_trusted(self):
        record = transport.inspect_image(raster())
        for changes in (
            {"width": 1},
            {"height": True},
            {"mime": "image/jpeg"},
            {"data": b"<svg />"},
        ):
            with (
                self.subTest(changes=list(changes)),
                patch.object(transport, "_connect") as connect,
                self.assertRaises(transport.ImageError),
            ):
                transport.request_image(
                    EDIT, KEY, fields=PARAMETERS, images=[{**record, **changes}]
                )
            connect.assert_not_called()
        exif = Image.Exif()
        exif[274] = 6
        with self.assertRaises(transport.ImageError):
            transport._checked_record({**record, "data": raster(exif=exif)})

    def test_total_pixels_and_upload_size_including_multipart_overhead(self):
        record = transport.inspect_image(raster())
        with (
            patch.object(transport, "MAX_TOTAL_PIXELS", 69),
            patch.object(transport, "_connect") as connect,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(
                EDIT, KEY, fields=PARAMETERS, images=[record, record]
            )
        self.assertEqual(caught.exception.code, "IMAGE_TOO_MANY_PIXELS")
        connect.assert_not_called()
        with (
            patch.object(transport, "MAX_UPLOAD_BYTES", len(record["data"]) + 10),
            patch.object(transport, "_connect") as connect,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(EDIT, KEY, fields=PARAMETERS, images=[record])
        self.assertEqual(caught.exception.status, 413)
        connect.assert_not_called()

    def test_aggregate_bytes_fail_before_raster_decode(self):
        with (
            patch.object(transport, "MAX_UPLOAD_BYTES", 20),
            patch.object(transport, "_checked_record") as checked,
            self.assertRaises(transport.ImageError),
        ):
            transport.request_image(
                EDIT,
                KEY,
                fields=PARAMETERS,
                images=[{"data": b"a" * 11}, {"data": b"b" * 11}],
            )
        checked.assert_not_called()

    def test_configuration_credentials_and_ports_sanitized(self):
        urls = (
            "ftp://127.0.0.1",
            "http://user:password@127.0.0.1",
            "http://127.0.0.1?x=y",
            "http://127.0.0.1#secret",
            "http://127.0.0.1:99999",
            "http://127.0.0.1:0",
            "http://127.0.0.1\r\nsecret",
            "http://127.0.0.1/../private",
            "http://[bad-ip",
            "http://127.0.0.1\\evil",
        )
        for url in urls:
            with (
                self.subTest(url=url),
                patch.dict(os.environ, {"NEW_API_INTERNAL_URL": url}),
                patch.object(transport, "_connect") as connect,
                self.assertRaises(transport.ImageError) as caught,
            ):
                self.call()
            self.assertEqual(caught.exception.status, 503)
            self.assertFalse(caught.exception.uncertain)
            self.assertNotIn("password", str(caught.exception))
            connect.assert_not_called()
        for key in ("", "with space", "fake\r\nX-Forwarded-For:evil", "中文", None):
            with (
                self.subTest(key=key),
                patch.dict(os.environ, {"NEW_API_INTERNAL_URL": "http://127.0.0.1"}),
                patch.object(transport, "_connect") as connect,
                self.assertRaises(transport.ImageError),
            ):
                transport.request_image(GENERATION, key, payload=PARAMETERS)
            connect.assert_not_called()

    def test_no_redirects_or_retries_and_no_raw_error_disclosure(self):
        for status, uncertain in (
            (302, True),
            (400, False),
            (401, False),
            (403, False),
            (429, False),
            (500, True),
            (503, True),
        ):
            with (
                self.subTest(status=status),
                upstream(
                    respond(
                        b"private " + KEY.encode(),
                        status=status,
                        headers=[("Location", "http://127.0.0.1:1/forbidden")],
                    )
                ) as requests,
                self.assertRaises(transport.ImageError) as caught,
            ):
                self.call()
            self.assertEqual(len(requests), 1)
            self.assertEqual(caught.exception.uncertain, uncertain)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn("private", str(caught.exception))

    def test_parameter_rejection_reports_safe_reason_and_original_http_status(self):
        body = json.dumps(
            {"error": {"code": "background_not_supported", "message": KEY}}
        ).encode()
        with (
            upstream(respond(body, status=400)) as requests,
            self.assertRaises(transport.ImageError) as caught,
        ):
            self.call()
        self.assertEqual(len(requests), 1)
        self.assertEqual(caught.exception.status, 400)
        self.assertFalse(caught.exception.uncertain)
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertIn("background", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    def test_unknown_error_body_is_not_disclosed_even_when_json(self):
        body = json.dumps(
            {"error": {"code": KEY, "message": "private " + KEY}}
        ).encode()
        with (
            upstream(respond(body, status=422)),
            self.assertRaises(transport.ImageError) as caught,
        ):
            self.call()
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("HTTP 422", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_connection_failure_is_certain_and_no_second_attempt(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        # Release the bound port: macOS can silently drop SYNs to a bound but
        # non-listening socket instead of replying with connection refused.
        with (
            patch.dict(
                os.environ, {"NEW_API_INTERNAL_URL": f"http://127.0.0.1:{port}"}
            ),
            self.assertRaises(transport.ImageError) as caught,
        ):
            self.call()
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(caught.exception.status, 503)


class ImageResponseTest(unittest.TestCase):
    def invalid(self, body, headers=None):
        with (
            upstream(respond(body, headers=headers)) as requests,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(len(requests), 1)
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn(KEY, str(caught.exception))
        return caught.exception

    def test_url_only_error_envelopes_and_multiple_outputs_refused(self):
        for value in (
            {"data": [{"url": "http://127.0.0.1:1/private"}]},
            {"data": [{"url": "data:image/png;base64,AAAA"}]},
            {"error": {"message": KEY}},
            {"success": False},
            {"data": []},
            {"data": [1]},
            {"data": [{}, {}]},
            [],
        ):
            with self.subTest(value=type(value)):
                self.invalid(json.dumps(value).encode())

    def test_url_only_has_actionable_code_without_fetching_or_leaking_url(self):
        url = "https://private.invalid/image?signature=" + KEY
        with self.assertLogs(
            "new_api_cockpit.features.image_studio.transport", level="WARNING"
        ) as logs:
            error = self.invalid(json.dumps({"data": [{"url": url}]}).encode())
        self.assertEqual(error.code, "IMAGE_URL_ONLY_RESPONSE")
        self.assertIn("Base64", str(error))
        self.assertNotIn(url, str(error))
        self.assertNotIn(KEY, "\n".join(logs.output))
        self.assertIn("phase=response_decode", "\n".join(logs.output))

    def test_json_utf8_duplicates_constants_and_shape_strict(self):
        for body in (
            b"",
            b"not json",
            b"\xff",
            b'{"data": [], "data": []}',
            b'{"data":[],"usage":{"total_tokens":NaN}}',
            b'{"usage":Infinity}',
            b"[" * 2000 + b"]" * 2000,
        ):
            with self.subTest(body=body[:20]):
                self.invalid(body)

    def test_base64_is_strict_and_actual_pixels_mime_verified(self):
        for encoded in (
            "!!!!",
            "a===",
            "AAAA\n",
            "AAAA=",
            "中文",
            "data:image/png;base64,AA==",
            base64.b64encode(b"<html>" + KEY.encode()).decode(),
        ):
            with self.subTest(encoded=encoded[:15]):
                self.invalid(
                    json.dumps(
                        {
                            "data": [
                                {
                                    "b64_json": encoded,
                                    "mime": "image/png",
                                    "width": 7,
                                    "height": 5,
                                }
                            ]
                        }
                    ).encode()
                )
        for image_format, mime in (("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with (
                self.subTest(image_format=image_format),
                upstream(respond(result_body(raster(image_format=image_format)))),
            ):
                result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
            self.assertEqual(result["images"][0]["mime"], mime)

    def test_output_pixel_limit_not_inferred_from_json(self):
        with patch.object(transport, "MAX_OUTPUT_PIXELS", 34):
            self.invalid(result_body(raster()))
        body = json.loads(result_body())
        body["data"][0].update(width=99999, height=99999, mime="text/html")
        with upstream(respond(json.dumps(body).encode())):
            result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(
            (result["images"][0]["width"], result["images"][0]["height"]), (7, 5)
        )

    def test_output_exif_normalized_and_metadata_stripped(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = KEY
        with upstream(respond(result_body(raster(image_format="JPEG", exif=exif)))):
            result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        image = result["images"][0]
        self.assertEqual((image["width"], image["height"]), (5, 7))
        with Image.open(io.BytesIO(base64.b64decode(image["b64_json"]))) as decoded:
            self.assertFalse(decoded.getexif())

    def test_invalid_content_type_encoding_and_framing(self):
        body = result_body()
        headers = (
            [],
            [("Content-Type", "text/html")],
            [("Content-Type", "text/event-stream")],
            [("Content-Type", "application/json"), ("Content-Encoding", "gzip")],
            [
                ("Content-Type", "application/json"),
                ("Content-Type", "application/json"),
            ],
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Content-Length", "2"),
            ],
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Transfer-Encoding", "chunked"),
            ],
            [("Content-Type", "application/json"), ("Content-Length", "-1")],
            [("Content-Type", "application/json"), ("Transfer-Encoding", "gzip")],
        )
        for value in headers:
            with self.subTest(headers=value):
                self.invalid(body, headers=value)

    def test_content_length_over_limit_rejected_without_body(self):
        error = self.invalid(
            b"",
            headers=[
                ("Content-Type", "application/json"),
                ("Content-Length", str(transport.MAX_JSON_BYTES + 1)),
            ],
        )
        self.assertEqual(error.code, "IMAGE_RESPONSE_TOO_LARGE")

    def test_lengthless_and_chunked_responses_are_bounded(self):
        body = b" " * 257
        with patch.object(transport, "MAX_JSON_BYTES", 256):
            error = self.invalid(
                body,
                headers=[("Content-Type", "application/json"), ("Connection", "close")],
            )
            self.assertEqual(error.code, "IMAGE_RESPONSE_TOO_LARGE")
            error = self.invalid(
                b"101\r\n" + body + b"\r\n0\r\n\r\n",
                headers=[
                    ("Content-Type", "application/json"),
                    ("Transfer-Encoding", "chunked"),
                ],
            )
            self.assertEqual(error.code, "IMAGE_RESPONSE_TOO_LARGE")

    def test_exact_body_limit_and_chunked_success(self):
        body = result_body()
        with (
            patch.object(transport, "MAX_JSON_BYTES", len(body)),
            upstream(respond(body)),
        ):
            result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(len(result["images"]), 1)
        split = len(body) // 2
        chunked = (
            f"{split:x}\r\n".encode()
            + body[:split]
            + b"\r\n"
            + f"{len(body) - split:x}\r\n".encode()
            + body[split:]
            + b"\r\n0\r\n\r\n"
        )
        with upstream(
            respond(
                chunked,
                headers=[
                    ("Content-Type", "application/json"),
                    ("Transfer-Encoding", "chunked"),
                ],
            )
        ):
            self.assertEqual(
                len(
                    transport.request_image(GENERATION, KEY, payload=PARAMETERS)[
                        "images"
                    ]
                ),
                1,
            )

    def test_connection_close_success_for_length_chunked_and_eof_framing(self):
        # Go/New API closes connections requested with Connection: close. The
        # response releases its final socket reference as read1 completes.
        body = result_body() + b" " * (transport._CHUNK + 1)
        split = len(body) // 2
        chunked = (
            f"{split:x}\r\n".encode()
            + body[:split]
            + b"\r\n"
            + f"{len(body) - split:x}\r\n".encode()
            + body[split:]
            + b"\r\n0\r\n\r\n"
        )
        for name, payload, framing in (
            ("length", body, [("Content-Length", str(len(body)))]),
            ("chunked", chunked, [("Transfer-Encoding", "chunked")]),
            ("eof", body, []),
        ):
            with (
                self.subTest(framing=name),
                upstream(
                    respond(
                        payload,
                        headers=[("Content-Type", "application/json")]
                        + framing
                        + [("Connection", "close")],
                    )
                ) as requests,
            ):
                result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
                self.assertEqual(len(result["images"]), 1)
                self.assertEqual(len(requests), 1)

    def test_incomplete_declared_length_and_malformed_chunk_fail(self):
        body = result_body()
        self.invalid(
            body,
            headers=[
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body) + 2)),
                ("Connection", "close"),
            ],
        )
        self.invalid(
            b"not-a-chunk\r\n",
            headers=[
                ("Content-Type", "application/json"),
                ("Transfer-Encoding", "chunked"),
                ("Connection", "close"),
            ],
        )

    def test_request_ids_and_usage_are_allowlisted(self):
        for request_id in (KEY, "contains spaces", "<script>", "x" * 129):
            with (
                self.subTest(request_id=request_id[:10]),
                upstream(
                    respond(
                        result_body(
                            usage={
                                "input_tokens": True,
                                "output_tokens": -1,
                                "total_tokens": 2**64,
                                "secret": KEY,
                            }
                        ),
                        headers=[
                            ("Content-Type", "application/json"),
                            (
                                "Content-Length",
                                str(
                                    len(
                                        result_body(
                                            usage={
                                                "input_tokens": True,
                                                "output_tokens": -1,
                                                "total_tokens": 2**64,
                                                "secret": KEY,
                                            }
                                        )
                                    )
                                ),
                            ),
                            ("X-Request-Id", request_id),
                        ],
                    )
                ),
            ):
                result = transport.request_image(GENERATION, KEY, payload=PARAMETERS)
            self.assertNotIn("upstream_request_id", result)
            self.assertNotIn("usage", result)


class ImageDeadlineTest(unittest.TestCase):
    def timeout(self, reply):
        started = time.monotonic()
        with (
            patch.object(transport, "TOTAL_TIMEOUT", 0.16),
            upstream(reply) as requests,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.8)
        self.assertEqual(caught.exception.code, "IMAGE_TIMEOUT")
        self.assertEqual(caught.exception.status, 504)
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(requests), 1)

    def test_slow_headers_deadline_not_inactivity_timeout(self):
        def reply(handler):
            for byte in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n":
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.012)

        self.timeout(reply)

    def test_slow_body_deadline_with_connection_close_detached_socket(self):
        body = result_body()

        def reply(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            for byte in body:
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.012)

        self.timeout(reply)

    def test_slow_chunk_length_line_requires_socket_watchdog(self):
        def reply(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n1;"
            )
            for _ in range(100):
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.012)

        self.timeout(reply)

    def test_upload_is_bounded_by_its_own_30_second_phase(self):
        def reply(handler):
            time.sleep(0.4)  # Do not read the POST body, so send buffers fill.

        started = time.monotonic()
        with (
            patch.object(transport, "UPLOAD_TIMEOUT", 0.12),
            patch.object(transport, "TOTAL_TIMEOUT", 1.0),
            patch.object(
                transport,
                "_request_body",
                return_value=([b"x" * (8 * 1024 * 1024)], "application/json"),
            ),
            upstream(reply, read_body=False) as requests,
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(caught.exception.code, "IMAGE_TIMEOUT")
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(requests), 1)

    def test_headers_and_body_share_one_absolute_budget(self):
        def reply(handler):
            time.sleep(0.1)
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"
            )
            for _ in range(100):
                handler.wfile.write(b" ")
                handler.wfile.flush()
                time.sleep(0.012)

        self.timeout(reply)

    def test_connect_timeout_has_distinct_certain_error(self):
        with (
            patch.dict(os.environ, {"NEW_API_INTERNAL_URL": "http://127.0.0.1:1"}),
            patch.object(transport, "_connect", side_effect=TimeoutError(KEY)),
            self.assertRaises(transport.ImageError) as caught,
        ):
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertEqual(caught.exception.code, "IMAGE_TIMEOUT")
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn(KEY, str(caught.exception))

    def test_dns_resolution_is_inside_connect_budget_and_child_is_reaped(self):
        with (
            upstream(respond()) as requests,
            patch.dict(os.environ, {"NEW_API_INTERNAL_URL": "http://localhost:1"}),
            patch.object(transport, "CONNECT_TIMEOUT", 0.12),
            patch.object(transport, "TOTAL_TIMEOUT", 1.0),
            patch.object(transport, "_RESOLVER_CODE", "import time; time.sleep(2)"),
            self.assertRaises(transport.ImageError) as caught,
        ):
            started = time.monotonic()
            transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(caught.exception.code, "IMAGE_TIMEOUT")
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(requests, [])

    def test_dns_literal_address_fast_path_and_localhost_resolver(self):
        watchdog = transport._SocketWatchdog(time.monotonic() + 2)
        try:
            watchdog.arm()
            with patch.object(transport.subprocess, "run") as resolver:
                address = transport._resolve_address("127.0.0.1", 1234, watchdog)
                self.assertEqual(address[4], ("127.0.0.1", 1234))
                resolver.assert_not_called()
            address = transport._resolve_address("localhost", 1234, watchdog)
            self.assertIn(address[0], (socket.AF_INET, socket.AF_INET6))
            self.assertIn(address[4][0], ("127.0.0.1", "::1"))
            self.assertEqual(address[4][1], 1234)
        finally:
            watchdog.close()

    def test_partial_header_send_failure_is_uncertain(self):
        with (
            patch.dict(os.environ, {"NEW_API_INTERNAL_URL": "http://127.0.0.1:1"}),
            patch.object(transport, "_connect") as connect,
        ):
            from unittest.mock import MagicMock

            connection = MagicMock()
            connection.endheaders.side_effect = OSError(KEY)
            connect.return_value = connection
            with (
                patch.object(transport._SocketWatchdog, "attach"),
                patch.object(
                    transport._SocketWatchdog, "__init__", autospec=True
                ) as init,
            ):

                def initialize(watchdog, deadline):
                    watchdog.deadline = watchdog.phase_deadline = deadline
                    watchdog.expired = watchdog.closed = False
                    watchdog.sock = MagicMock()
                    watchdog.timer = None
                    watchdog.lock = threading.Lock()

                init.side_effect = initialize
                with self.assertRaises(transport.ImageError) as caught:
                    transport.request_image(GENERATION, KEY, payload=PARAMETERS)
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn(KEY, str(caught.exception))

    def test_watchdog_closes_bound_socket_and_timer_is_cancelled(self):
        watchdog = transport._SocketWatchdog(time.monotonic() + 0.08)
        left, right = socket.socketpair()
        try:
            watchdog.arm()
            watchdog.attach(left)
            right.sendall(b"x")
            self.assertEqual(left.recv(1), b"x")
            with self.assertRaises((TimeoutError, OSError)):
                left.recv(1)
            self.assertTrue(watchdog.expired or time.monotonic() >= watchdog.deadline)
        finally:
            watchdog.close()
            right.close()
        self.assertTrue(watchdog.closed)
        self.assertTrue(watchdog.timer.finished.is_set())

    def test_expired_upload_phase_cannot_be_extended(self):
        watchdog = transport._SocketWatchdog(time.monotonic() + 1)
        try:
            watchdog.phase_deadline = time.monotonic() - 1
            with self.assertRaises(TimeoutError):
                watchdog.arm()
        finally:
            watchdog.close()


if __name__ == "__main__":
    unittest.main()
