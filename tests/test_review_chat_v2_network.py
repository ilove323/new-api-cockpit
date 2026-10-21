"""Offline review regressions: strict chunk framing and decoded downloads.

Only local fixture sockets and temporary storage are used. These assertions
describe required behavior and pass after strict chunk framing repairs;
neither the HTTP parser nor Workspace is mocked.
"""

import unittest

from new_api_cockpit.features.chat import web_tools as web
from new_api_cockpit.features.chat.workspace import Workspace
from test_chat_storage import StorageFixture
from test_chat_web_tools_v2 import URL, context, upstream


def chunked_reply(body, transfer=b"chunked"):
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
        b"Transfer-Encoding: " + transfer + b"\r\n\r\n" + body
    )


class ReviewChunkedHTTPTest(unittest.TestCase):
    def test_optional_transfer_encoding_whitespace_is_decoded(self):
        for transfer in (b"chunked ", b"chunked\t"):
            with (
                self.subTest(transfer=transfer),
                upstream(responses=[chunked_reply(b"3\r\nabc\r\n0\r\n\r\n", transfer)]),
            ):
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["text"], "abc")
                self.assertEqual(result["bytes_read"], 3)

    def test_malformed_or_truncated_chunk_framing_is_not_success(self):
        cases = {
            "bad_data_crlf": b"3\r\nabcXX0\r\n\r\n",
            "missing_final_header_terminator": b"3\r\nabc\r\n0\r\n",
            "truncated_final_chunk_size": b"3\r\nabc\r\n0",
            "signed_chunk_size": b"+3\r\nabc\r\n0\r\n\r\n",
            "underscored_chunk_size": b"0_3\r\nabc\r\n0\r\n\r\n",
        }
        for name, body in cases.items():
            with self.subTest(case=name), upstream(responses=[chunked_reply(body)]):
                result = web.execute("http_fetch", {"url": URL}, None, context())
                self.assertFalse(result["ok"], result)


class ReviewChunkedDownloadTest(StorageFixture):
    def download(self, raw):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            with upstream(responses=[raw]):
                result = web.execute(
                    "download_url",
                    {"url": URL, "name": "review-body.txt"},
                    workspace,
                    context(run_id=rid),
                )
            files = self.store.files(self.scope, lease=lease)
            contents = [
                self.store.read_file(self.scope, entry["id"], lease=lease)[1]
                for entry in files
            ]
            self.terminal(lease, rid)
            return result, contents

    def test_control_valid_chunked_download_saves_decoded_body(self):
        result, contents = self.download(chunked_reply(b"3\r\nabc\r\n0\r\n\r\n"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(contents, [b"abc"])

    def test_chunked_with_optional_whitespace_does_not_save_wire_framing(self):
        result, contents = self.download(
            chunked_reply(b"3\r\nabc\r\n0\r\n\r\n", b"chunked ")
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(contents, [b"abc"])

    def test_truncated_chunked_download_does_not_commit_a_file(self):
        result, contents = self.download(chunked_reply(b"3\r\nabc\r\n0"))
        self.assertEqual((result["ok"], contents), (False, []), result)


if __name__ == "__main__":
    unittest.main()
