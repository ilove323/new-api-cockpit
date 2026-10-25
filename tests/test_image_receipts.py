"""Receipt unit tests; optional real Redis tests use IMAGE_STUDIO_TEST_REDIS_BINARY.

No New API, model calls, user credentials or production Redis. Integration tests
start a temporary loopback-only Redis with AOF and remove all local test state.
"""

from contextlib import nullcontext
import json
import multiprocessing
import os
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import uuid

import redis
from new_api_cockpit.features.image_studio import receipts

URL = "redis://127.0.0.1:6379/0"
BODY = {
    "token_id": 1,
    "model": "gpt-image-test",
    "prompt": "private words",
    "parameters": {},
}
DIGEST = receipts.digest_request(BODY)


def client_fixture(previous=""):
    client = Mock()
    client.info.side_effect = lambda section: (
        {"run_id": "fixture-epoch"}
        if section == "server"
        else {"aof_enabled": 1, "aof_last_write_status": "ok"}
    )
    client.config_get.return_value = {
        "maxmemory-policy": "noeviction",
        "appendfsync": "always",
    }
    client.eval.return_value = previous
    return client


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "IMAGE_STUDIO_REDIS_URL": URL,
                "IMAGE_STUDIO_RECEIPT_NAMESPACE": "cockpit:test:" + uuid.uuid4().hex,
                "IMAGE_STUDIO_RECEIPT_TTL": "3600",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_canonical_digest_binds_content_order_and_operation(self):
        self.assertEqual(
            DIGEST, receipts.digest_request(dict(reversed(list(BODY.items()))))
        )
        self.assertNotEqual(
            DIGEST, receipts.digest_request({**BODY, "prompt": "another"})
        )
        self.assertNotEqual(
            receipts.digest_request(BODY), receipts.digest_request(BODY, images=[])
        )
        images = [{"data": b"first"}, {"data": b"second"}]
        self.assertNotEqual(
            receipts.digest_request(BODY, images=images),
            receipts.digest_request(BODY, images=images[::-1]),
        )
        self.assertNotEqual(
            receipts.digest_request(BODY, images=images),
            receipts.digest_request(BODY, images=images, mask={"data": b"mask"}),
        )

    def test_atomic_claim_stores_only_digest_owner_and_lifecycle(self):
        client = client_fixture()
        with patch.object(receipts, "_client", return_value=nullcontext(client)):
            receipt = receipts.claim(4, "fixture-request", DIGEST)
        value = json.loads(receipt.claimed)
        self.assertEqual(set(value), {"owner_id", "digest", "state", "started_at"})
        self.assertEqual(value["state"], "dispatched")
        self.assertNotIn("private words", repr(client.mock_calls))
        self.assertNotIn(URL, repr(receipt))
        self.assertIn(":4:", receipt.key)
        self.assertNotIn("fixture-request", receipt.key)
        self.assertEqual(client.eval.call_count, 1)

    def test_duplicates_and_changed_input_never_acquire(self):
        for state in ("dispatched", "succeeded", "failed", "unknown"):
            client = client_fixture(json.dumps({"digest": DIGEST, "state": state}))
            with patch.object(receipts, "_client", return_value=nullcontext(client)):
                with self.assertRaises(receipts.ReceiptError) as caught:
                    receipts.claim(4, "fixture-request", DIGEST)
                self.assertEqual(caught.exception.code, "IMAGE_REQUEST_DUPLICATE")
                self.assertEqual(
                    caught.exception.uncertain, state in {"dispatched", "unknown"}
                )
                with self.assertRaises(receipts.ReceiptError) as caught:
                    receipts.claim(4, "fixture-request", "0" * 64)
                self.assertEqual(caught.exception.code, "IMAGE_REQUEST_CONFLICT")

    def test_invalid_store_configuration_never_uses_network(self):
        for update in (
            {"IMAGE_STUDIO_REDIS_URL": ""},
            {"IMAGE_STUDIO_REDIS_URL": URL + "?socket_timeout=999"},
            {"IMAGE_STUDIO_RECEIPT_TTL": "5"},
            {"IMAGE_STUDIO_RECEIPT_NAMESPACE": "bad key"},
        ):
            with (
                patch.dict(os.environ, update),
                patch.object(receipts, "_client") as client,
            ):
                with self.assertRaises(receipts.ReceiptError):
                    receipts.claim(4, "fixture-request", DIGEST)
                client.assert_not_called()

    def test_store_outage_does_not_retry_or_dispatch(self):
        client = client_fixture()
        client.eval.side_effect = redis.TimeoutError("private connection detail")
        with patch.object(receipts, "_client", return_value=nullcontext(client)):
            with self.assertRaises(receipts.ReceiptError) as caught:
                receipts.claim(4, "fixture-request", DIGEST)
        self.assertEqual(client.eval.call_count, 1)
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(caught.exception.uncertain)

    def test_finish_keeps_ttl_and_failure_does_not_hide_known_result(self):
        value = json.dumps({"digest": DIGEST, "state": "dispatched"})
        receipt = receipts.Receipt(URL, "test-key", value)
        client = client_fixture()
        client.eval.return_value = 1
        with patch.object(receipts, "_client", return_value=nullcontext(client)):
            self.assertTrue(receipt.finish("succeeded"))
            self.assertIn("KEEPTTL", client.eval.call_args.args[0])
            client.eval.side_effect = redis.ConnectionError()
            with self.assertLogs(receipts.__name__, level="WARNING"):
                self.assertFalse(receipt.finish("unknown"))
        with self.assertRaises(ValueError):
            receipt.finish("queued")

    def test_restart_or_unsafe_persistence_fails_closed(self):
        client = client_fixture()
        receipts._ready(client, b"test-epoch")
        client.info.side_effect = lambda section: (
            {"run_id": "new"}
            if section == "server"
            else {"aof_enabled": 1, "aof_last_write_status": "ok"}
        )
        with self.assertRaises(receipts.ReceiptError):
            receipts._ready(client, b"test-epoch")
        client = client_fixture()
        client.config_get.return_value["appendfsync"] = "everysec"
        with self.assertRaises(receipts.ReceiptError):
            receipts._ready(client, b"unsafe")


def competing_claim(url, namespace, queue):
    os.environ.update(
        IMAGE_STUDIO_REDIS_URL=url,
        IMAGE_STUDIO_RECEIPT_NAMESPACE=namespace,
        IMAGE_STUDIO_RECEIPT_TTL="3600",
    )
    try:
        receipt = receipts.claim(51, "cross-process-request", DIGEST)
        receipt.finish("succeeded")
        queue.put("sent")
    except receipts.ReceiptError as exc:
        queue.put(exc.code)


@unittest.skipUnless(
    os.environ.get("IMAGE_STUDIO_TEST_REDIS_BINARY"),
    "set local Redis binary to run integration",
)
class ReceiptRedisIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="studio-receipts-test-")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = f"redis://127.0.0.1:{cls.port}/0"
        cls.binary = os.environ["IMAGE_STUDIO_TEST_REDIS_BINARY"]
        cls.start_server()

    @classmethod
    def start_server(cls):
        cls.server = subprocess.Popen(
            [
                cls.binary,
                "--bind",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--dir",
                cls.directory.name,
                "--appendonly",
                "yes",
                "--appendfsync",
                "always",
                "--save",
                "",
                "--maxmemory-policy",
                "noeviction",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.client = redis.Redis.from_url(
            cls.url, socket_connect_timeout=0.1, socket_timeout=0.1
        )
        for _ in range(100):
            try:
                if cls.client.ping():
                    return
            except redis.RedisError:
                time.sleep(0.03)
        raise RuntimeError("Temporary Redis did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.client.close()
        cls.directory.cleanup()

    def test_cross_process_atomic_dispatch_and_restart_persistence(self):
        namespace = "cockpit:test:" + uuid.uuid4().hex
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        children = [
            ctx.Process(target=competing_claim, args=(self.url, namespace, queue))
            for _ in range(4)
        ]
        for child in children:
            child.start()
        states = [queue.get(timeout=15) for _ in children]
        for child in children:
            child.join(timeout=5)
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(states.count("sent"), 1)
        self.assertEqual(states.count("IMAGE_REQUEST_DUPLICATE"), 3)
        keys = self.client.keys(namespace + ":*")
        self.assertEqual(len(keys), 1)
        self.assertGreater(self.client.ttl(keys[0]), 3500)
        stored = self.client.get(keys[0])
        self.assertNotIn(b"private words", stored)
        self.assertEqual(json.loads(stored)["state"], "succeeded")
        self.server.terminate()
        self.server.wait(timeout=5)
        self.client.close()
        self.start_server()
        child = ctx.Process(target=competing_claim, args=(self.url, namespace, queue))
        child.start()
        self.assertEqual(queue.get(timeout=15), "IMAGE_REQUEST_DUPLICATE")
        child.join(timeout=5)
        self.assertEqual(child.exitcode, 0)
        queue.close()
        queue.join_thread()


if __name__ == "__main__":
    unittest.main()
