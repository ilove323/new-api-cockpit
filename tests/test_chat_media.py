"""Real offline parsers + owner-scoped runs; no provider/network requests."""

import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from PIL import Image
from chat_media_fixtures import docx, pdf, scanned_pdf, png, samples
from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat import (
    config,
    media,
    protocols,
    tools,
    working_context,
)
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.storage import ChatStore
from new_api_cockpit.features.chat.workspace import Workspace


class MediaParserTest(unittest.TestCase):
    def test_real_document_parsers_and_original_bytes(self):
        for name, raw in samples().items():
            with self.subTest(name=name):
                parsed = media.extract(name, raw)
                if name == "picture.png":
                    self.assertEqual(len(parsed["images"]), 1)
                elif name == "scanned.pdf":
                    self.assertEqual(len(parsed["images"]), 1)
                    self.assertIn("扫描", parsed["text"])
                else:
                    self.assertIn("1234", parsed["text"])
                self.assertEqual(ChatStore.normalize_file(name, raw), raw)
        x = media.extract("report.xlsx", samples()["report.xlsx"])["text"]
        self.assertIn("成本明细", x)
        self.assertIn("=SUM(B2:B2)", x)
        self.assertIn("尚未计算", x)

    @unittest.skipUnless(
        Path("/usr/bin/antiword").is_file(),
        "Legacy .doc parser is installed in the Linux runtime image",
    )
    def test_legacy_word_in_runtime_image(self):
        raw = (
            Path(__file__).parent / "fixtures/chat-media/apache-poi-test.doc"
        ).read_bytes()
        parsed = media.extract("legacy.doc", raw)
        self.assertTrue(parsed["text"].strip())
        self.assertEqual(ChatStore.normalize_file("legacy.doc", raw), raw)

    def test_extension_spoofing_corruption_and_fake_binary_outputs_are_rejected(self):
        for name, raw in [
            ("x.pdf", b"%PDF-fake"),
            ("x.docx", b"not a zip"),
            ("x.xlsx", docx()),
            ("x.png", pdf()),
            ("x.jpg", png()),
            ("x.xls", b"not OLE"),
        ]:
            with self.subTest(name=name), self.assertRaises(ChatError):
                ChatStore.normalize_file(name, raw)
        for name in ["x.pdf", "x.docx", "x.xlsx", "x.png"]:
            with self.assertRaises(ChatError):
                ChatStore.normalize_file(name, b"fake", "output")
            self.assertEqual(tools._filename(name), name)

    def test_zip_bombs_traversal_duplicate_members_and_xml_entities_fail_closed(self):
        for members in [
            [("word/document.xml", b"x" * 2097152)],
            [("word/../../evil.xml", b"x")],
            [("word/document.xml", b"x"), ("word/document.xml", b"x")],
            [
                (
                    "word/document.xml",
                    b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
                )
            ],
        ]:
            out = BytesIO()
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("[Content_Types].xml", "<Types/>")
                for name, data in members:
                    z.writestr(name, data)
            with self.assertRaises(ChatError):
                media.extract("bad.docx", out.getvalue())

    def test_image_exif_orientation_metadata_stripped_and_decompression_limits(self):
        out = BytesIO()
        img = Image.new("RGB", (12, 8), "red")
        exif = img.getexif()
        exif[274] = 6
        exif[270] = "private exif"
        img.save(out, format="JPEG", exif=exif)
        part = media.extract("photo.jpg", out.getvalue())["images"][0]
        with Image.open(BytesIO(base64.b64decode(part["data"]))) as normalized:
            self.assertEqual(normalized.size, (8, 12))
            self.assertFalse(normalized.getexif())
        old_pixels = Image.MAX_IMAGE_PIXELS
        with patch.object(config, "IMAGE_MAX_PIXELS", 20):
            # Parent validation remains bounded; the worker uses installed limits.
            from new_api_cockpit.features.chat.document_worker import image

            try:
                with self.assertRaises(Image.DecompressionBombError):
                    image(png())
            finally:
                Image.MAX_IMAGE_PIXELS = old_pixels

    def test_supported_static_image_formats_and_animation_rejection(self):
        for extension, format_name in [
            ("jpg", "JPEG"),
            ("webp", "WEBP"),
            ("gif", "GIF"),
            ("bmp", "BMP"),
        ]:
            out = BytesIO()
            Image.new("RGB", (20, 20), "red").save(out, format=format_name)
            self.assertEqual(
                len(media.extract("test." + extension, out.getvalue())["images"]), 1
            )
        out = BytesIO()
        Image.new("RGB", (20, 20), "red").save(
            out,
            format="GIF",
            save_all=True,
            append_images=[Image.new("RGB", (20, 20), "blue")],
        )
        with self.assertRaises(ChatError):
            media.extract("animated.gif", out.getvalue())

    def test_multimodal_wire_fields_are_protocol_specific_and_never_remote_urls(self):
        part = media.extract("p.png", png())["images"][0]
        content = [
            {"type": "text", "text": "inspect"},
            {
                "type": "image_url",
                "image_url": {"url": media.image_url(part), "detail": "auto"},
            },
        ]
        for protocol in protocols.PROTOCOLS:
            wire = protocols.wire_body(
                protocol,
                {
                    "model": "synthetic",
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": 50,
                },
            )
            if protocol == "responses":
                self.assertEqual(wire["input"][0]["content"][1]["type"], "input_image")
            elif protocol == "anthropic":
                self.assertEqual(
                    wire["messages"][0]["content"][1]["source"]["type"], "base64"
                )
            else:
                self.assertEqual(wire["messages"][0]["content"][1]["type"], "image_url")
        content[1]["image_url"]["url"] = "https://internal.invalid/a.png"
        with self.assertRaises(ChatError):
            protocols.wire_body(
                "responses",
                {
                    "model": "synthetic",
                    "messages": [{"role": "user", "content": content}],
                },
            )
        with self.assertRaises(ChatError):
            protocols.validate_content(content, "assistant")

    def test_native_window_preserves_only_bounded_data_images(self):
        part = media.extract("p.png", png())["images"][0]
        window = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "picture"},
                    {
                        "type": "input_image",
                        "image_url": media.image_url(part),
                        "detail": "auto",
                    },
                ],
            }
        ]
        self.assertEqual(protocols.validate_window(window), window)
        window[0]["content"][1]["image_url"] = "https://example.invalid/test.png"
        with self.assertRaises(ChatError):
            protocols.validate_window(window)


class MediaRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.stream = make_service(self.tmp.name)
        self.actor = access()
        self.cid = create(self.service, self.actor)

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def upload(self, name, raw, cid=None, actor=None):
        fid = str(uuid4())
        self.service.upload(
            (actor or self.actor).principal, cid or self.cid, fid, name, raw
        )
        return fid

    def run_turn(self, **changes):
        active = self.service.accept_run(
            self.actor, self.cid, body(self.service, self.actor, self.cid, **changes)
        )
        list(self.service.stream(active))
        self.assertEqual(
            self.service.get_run(self.actor.principal, self.cid, active.run_id)[
                "status"
            ],
            "completed",
        )
        return active

    def test_pdf_word_excel_are_real_text_in_model_prompt_and_tools(self):
        ids = [
            self.upload(name, raw)
            for name, raw in samples().items()
            if name in ["report.pdf", "report.docx", "report.xlsx", "report.xls"]
        ]
        self.run_turn(mode="files", attachment_ids=ids)
        text = json.dumps(self.stream.calls[-1], ensure_ascii=False)
        self.assertIn("PDF Alpha cost 1234", text)
        self.assertIn("中文成本报告", text)
        self.assertIn("成本明细", text)
        scope = self.service._scope(self.actor.principal, self.cid)
        ws = Workspace(self.service.store, scope)
        self.assertIn("中文成本报告", ws.read_file(ids[1])["text"])
        found = ws.search_files("1234", ids, False)
        self.assertTrue(found["ok"])
        self.assertEqual(len({m["file_id"] for m in found["matches"]}), len(ids))
        events = self.service._history(scope)
        self.assertNotIn("中文成本报告", json.dumps(events, ensure_ascii=False))

    def test_images_and_scanned_pdf_survive_followup_without_binary_in_events(self):
        for name, raw in [("picture.png", png()), ("scanned.pdf", scanned_pdf())]:
            fid = self.upload(name, raw)
            self.run_turn(mode="files", attachment_ids=[fid])
            message = self.stream.calls[-1]["messages"][-1]
            self.assertIsInstance(message["content"], list)
            encoded = message["content"][1]["image_url"]["url"]
            self.assertTrue(encoded.startswith("data:image/jpeg;base64,"))
            self.run_turn(mode="files", text="continue")
            self.assertIn(encoded, json.dumps(self.stream.calls[-1]))
            scope = self.service._scope(self.actor.principal, self.cid)
            events = self.service._history(scope)
            self.assertNotIn("base64,", json.dumps(events))
            self.assertEqual(self.service.store.read_file(scope, fid)[1], raw)

    def test_base64_transport_is_not_mistaken_for_text_context(self):
        out = BytesIO()
        Image.effect_noise((600, 600), 100).convert("RGB").save(out, format="PNG")
        fid = self.upload("noise.png", out.getvalue())
        self.run_turn(mode="files", attachment_ids=[fid])
        messages = self.stream.calls[-1]["messages"]
        self.assertGreater(len(json.dumps(messages)), config.CONTEXT_BYTES)
        self.assertLess(working_context.input_bytes(messages), config.CONTEXT_BYTES)

    def test_other_user_or_chat_images_are_rejected_before_dispatch(self):
        other = access(99)
        cid = create(self.service, other)
        fid = self.upload("p.png", png(), cid, other)
        with self.assertRaises(ChatError) as caught:
            self.service.accept_run(
                self.actor,
                self.cid,
                body(self.service, self.actor, self.cid, attachment_ids=[fid]),
            )
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.stream.calls, [])

    def test_oversized_extracted_text_rejects_before_model_request(self):
        fid = self.upload("report.docx", docx())
        with patch(
            "new_api_cockpit.features.chat.media.extract",
            return_value={"text": "x" * (config.CONTEXT_BYTES + 1), "images": []},
        ):
            with self.assertRaises(ChatError):
                self.service.accept_run(
                    self.actor,
                    self.cid,
                    body(self.service, self.actor, self.cid, attachment_ids=[fid]),
                )
        self.assertEqual(self.stream.calls, [])

    def test_binary_file_entries_survive_reopen_and_image_tool_never_utf8_decodes_bytes(
        self,
    ):
        fid = self.upload("p.png", png())
        scope = self.service._scope(self.actor.principal, self.cid)
        self.assertEqual(
            self.service.get_conversation(self.actor.principal, self.cid)[
                "storage_state"
            ],
            "ok",
        )
        entry = self.service.files(self.actor.principal, self.cid)["files"][0]
        self.assertEqual(entry["mime"], "image/png")
        ws = Workspace(self.service.store, scope)
        result = ws.read_file(fid)
        self.assertTrue(result["ok"])
        self.assertIn("视觉读取", result["text"])
        self.assertEqual(
            self.service.download(self.actor.principal, self.cid, fid)[0], png()
        )
