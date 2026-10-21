"""Offline T05 tests: real SafeFS/flock, independent users and frozen run contexts."""

from contextlib import contextmanager
import errno
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import SafeFS, canonical_json
from new_api_cockpit.features.chat.locks import FileLock
from new_api_cockpit.features.chat.user_context import (
    MAX_REVISION,
    UserContext,
    validate_credentials,
)
from new_api_cockpit.platform.identity import Principal, RequestAccess

A = Principal(12, "member", session_id="session-SECRET-sentinel")
B = Principal(13, "other")
ADMIN = Principal(1, "admin", role=100)
CID = "11111111-1111-4111-8111-111111111111"
RID = "22222222-2222-4222-8222-222222222222"
TIME = "2026-10-15T00:00:00.000000Z"


def skill(name="weekly-summary", body="整理本周材料。", description="中文周报"):
    return f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n{body}"


class SettingsStore:
    """Narrow shared-store protocol fixture, using actual safety and lock code.

    The owner conversation and accepted lookup are fixture collaborators. A
    separate integration test below will bind these calls to the real ChatStore.
    """

    def __init__(self, root):
        self.fs = SafeFS(root)
        self.prefix = "site-a"
        self.fs.mkdir(f"{self.prefix}/.locks/users", parents=True)
        self.fs.mkdir(f"{self.prefix}/.locks/conversations", parents=True)
        self.conversations = {(A.user_id, CID)}
        self.accepted = {}
        self.last_required = 0
        self.user_limit = config.USER_STORAGE_BYTES
        self.total_limit = config.TOTAL_STORAGE_BYTES
        self.other_reserved = 0

    @contextmanager
    def profile_transaction(self, principal, required_bytes):
        with FileLock(self.fs, f"{self.prefix}/.locks/storage.lock"):
            self.last_required = required_bytes
            root = f"{self.prefix}/users/{principal.user_id}"
            user_size = self.fs.disk_usage(root) if self.fs.exists(root) else 0
            context_size = sum(
                self.fs.stat(f"{root}/{name}").st_size
                if name == "context-manifest.json"
                else self.fs.disk_usage(f"{root}/{name}")
                for name in (
                    "context-manifest.json",
                    "profiles",
                    "skills",
                    ".context-tmp",
                    ".context-quarantine",
                )
                if self.fs.exists(f"{root}/{name}")
            )
            if (
                context_size + required_bytes > config.USER_CONTEXT_STORAGE_BYTES
                or user_size + required_bytes > self.user_limit
                or self.fs.disk_usage() + self.other_reserved + required_bytes
                > self.total_limit
            ):
                raise ChatError("存储空间不足。", 507, "CHAT_STORAGE_LIMIT")
            if self.fs.free_bytes() < config.DISK_FREE_MIN_BYTES + required_bytes:
                raise ChatError("存储空间不足。", 507, "CHAT_STORAGE_FULL")
            yield

    def open_conversation(self, principal, cid):
        if (principal.user_id, cid) not in self.conversations:
            raise ChatError("内容不存在。", 404, "CHAT_NOT_FOUND")
        return SimpleNamespace(
            user_id=principal.user_id,
            cid=cid,
            path=f"{self.prefix}/users/{principal.user_id}/conversations/{cid}",
        )

    def read_context(self, scope, rid):
        try:
            ref = self.accepted[(scope.user_id, scope.cid, rid)]["context_snapshot"]
        except KeyError:
            raise ChatError("内容不存在。", 404, "CHAT_NOT_FOUND") from None
        path = f"{scope.path}/contexts/{rid}.json"
        try:
            raw = self.fs.read_bytes(path)
        except FileNotFoundError:
            raise ChatError("快照缺失。", 503, "CHAT_STORAGE_CORRUPT") from None
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ChatError("快照损坏。", 503, "CHAT_STORAGE_CORRUPT")
        return json.loads(raw)

    def read_events(self, scope, after_seq=0, until_seq=None):
        events = [
            {"type": "run.accepted", "run_id": rid, "data": run, "seq": n + 1}
            for n, ((uid, cid, rid), run) in enumerate(self.accepted.items())
            if uid == scope.user_id and cid == scope.cid
        ]
        return {
            "events": events,
            "until_seq": len(events),
            "has_more": False,
            "next_after_seq": len(events),
        }

    def publish(self, snapshot, *, principal=A, cid=CID, rid=RID, commit=True):
        path = f"{self.prefix}/users/{principal.user_id}/conversations/{cid}/contexts/{rid}.json"
        self.fs.mkdir(path.rsplit("/", 1)[0], parents=True)
        raw = canonical_json(snapshot) + b"\n"
        self.fs.atomic_write(path, raw, exclusive=True)
        ref = {
            "file": f"contexts/{rid}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "context_revision": snapshot["context_revision"],
            "skill_ids": list(snapshot["skill_ids"]),
        }
        if commit:
            self.accepted[(principal.user_id, cid, rid)] = {"context_snapshot": ref}
        return path, ref


def competing_save(root, barrier, output, name):
    store = SettingsStore(root)
    context = UserContext(store, clock=lambda: TIME)
    barrier.wait(10)
    try:
        result = context.update_profile(A, name, 0, 0, name)
        output.put(("ok", result["context_revision"]))
    except ChatError as exc:
        output.put((exc.code, exc.status))
    finally:
        store.fs.close()


class UserContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.temp.name)
        self.store = SettingsStore(self.root)
        self.context = UserContext(self.store, clock=lambda: TIME)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.fs.close)

    def assert_code(self, code, function, *args, status=None, **kwargs):
        with self.assertRaises(ChatError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        return caught.exception

    def manifest(self, principal=A):
        return f"site-a/users/{principal.user_id}/context-manifest.json"

    def disk(self, path):
        return Path(self.root, path)

    def create(self, name="weekly-summary", body="材料", revision=None):
        if revision is None:
            revision = self.context.read_context(A)["context_revision"]
        return self.context.create_skill(A, revision, skill(name, body))["skill"]

    def test_initial_read_has_exact_empty_documents_and_no_user_data_files(self):
        self.assertFalse(self.store.fs.exists("site-a/users"))
        view = self.context.read_context(A)
        self.assertEqual(view["context_revision"], 0)
        self.assertIsNone(view["updated_at"])
        self.assertEqual(
            [p["name"] for p in view["profiles"]],
            ["MEMORY.md", "SOUL.md", "IDENTITY.md"],
        )
        for profile in view["profiles"]:
            self.assertEqual(profile["content"], "")
            self.assertEqual(profile["revision"], 0)
            self.assertEqual(profile["sha256"], hashlib.sha256(b"").hexdigest())
            self.assertEqual(profile["size_bytes"], 0)
            self.assertIsNone(profile["updated_at"])
        self.assertEqual(
            self.context.list_skills(A),
            {"context_revision": 0, "skills": [], "total": 0},
        )
        self.assertFalse(self.store.fs.exists("site-a/users"))
        self.assertEqual(
            view["limits"]["profile_bytes"]["MEMORY.md"], config.MEMORY_BYTES
        )
        self.assertEqual(
            view["limits"]["personal_context_bytes"], config.PERSONAL_CONTEXT_BYTES
        )

    def test_all_three_profiles_versions_normalization_noop_and_clear(self):
        global_revision = 0
        for name in ("MEMORY.md", "SOUL.md", "IDENTITY.md"):
            updated = self.context.update_profile(
                A, name, global_revision, 0, "\ufeff中文\r\n下一行\r"
            )
            global_revision += 1
            self.assertTrue(updated["changed"])
            self.assertEqual(updated["context_revision"], global_revision)
            self.assertEqual(updated["profile"]["content"], "中文\n下一行\n")
            unchanged = self.context.update_profile(
                A, name, global_revision, 1, "中文\n下一行\n"
            )
            self.assertFalse(unchanged["changed"])
            self.assertEqual(unchanged["context_revision"], global_revision)
            cleared = self.context.update_profile(A, name, global_revision, 1, "")
            global_revision += 1
            self.assertEqual(cleared["profile"]["revision"], 2)
            self.assertEqual(cleared["profile"]["content"], "")
            version_dir = self.disk(f"site-a/users/12/profiles/{name[:-3]}/versions")
            self.assertEqual(
                [p.name for p in version_dir.iterdir() if p.is_file()], ["2.md"]
            )
        self.assertEqual(self.context.read_context(A)["context_revision"], 6)
        self.assertEqual(
            self.context.update_profile(B, "MEMORY.md", 0, 0, "")["context_revision"], 0
        )
        self.assertFalse(self.store.fs.exists("site-a/users/13"))

    def test_exactly_one_bom_is_removed_and_retained_bom_survives_storage_and_snapshot(
        self,
    ):
        result = self.context.update_profile(A, "MEMORY.md", 0, 0, "\ufeff\ufeff正文")
        self.assertEqual(result["profile"]["content"], "\ufeff正文")
        self.assertEqual(
            self.context.read_context(A)["profiles"][0]["content"], "\ufeff正文"
        )
        snapshot = self.context.freeze_context(A, CID, RID, 1, [])
        self.store.publish(snapshot)
        self.assertEqual(
            self.context.get_run_context(A, CID, RID)["profiles"][0]["content"],
            "\ufeff正文",
        )

    def test_strict_input_profile_size_unicode_and_invalid_identities(self):
        for name, limit in self.context._profile_limits().items():
            self.assert_code(
                "CHAT_PROFILE_TOO_LARGE",
                self.context.update_profile,
                A,
                name,
                0,
                0,
                "x" * (limit + 1),
                status=413,
            )
            updated = self.context.update_profile(
                B,
                name,
                self.context.read_context(B)["context_revision"],
                0,
                "x" * limit,
            )
            self.assertEqual(updated["profile"]["size_bytes"], limit)
        for value in [None, {}, True, 0, "\x00", "\udfff"]:
            self.assert_code(
                "CHAT_INVALID_REQUEST",
                self.context.update_profile,
                A,
                "MEMORY.md",
                0,
                0,
                value,
            )
        for name in ["memory.md", "../MEMORY.md", "/etc/passwd", "SOUL", None, []]:
            self.assert_code(
                "CHAT_INVALID_REQUEST", self.context.update_profile, A, name, 0, 0, "x"
            )
        for principal in [
            {"user_id": 12},
            RequestAccess(A, "request-secret"),
            Principal(12, auth_kind="pat"),
            None,
        ]:
            self.assert_code(
                "CHAT_SESSION_REQUIRED", self.context.read_context, principal
            )
        for value in [True, None, "0", 0.0, -1, MAX_REVISION + 1]:
            self.assert_code(
                "CHAT_INVALID_REQUEST",
                self.context.update_profile,
                A,
                "MEMORY.md",
                value,
                0,
                "x",
            )
            self.assert_code(
                "CHAT_INVALID_REQUEST",
                self.context.update_profile,
                A,
                "MEMORY.md",
                0,
                value,
                "x",
            )

    def test_conflicts_global_first_even_on_unchanged_content(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "one")
        error = self.assert_code(
            "CHAT_CONTEXT_CHANGED",
            self.context.update_profile,
            A,
            "MEMORY.md",
            0,
            99,
            "one",
            status=409,
        )
        self.assertEqual(error.details, {"current_context_revision": 1})
        self.assert_code(
            "CHAT_PROFILE_REVISION_CONFLICT",
            self.context.update_profile,
            A,
            "MEMORY.md",
            1,
            0,
            "two",
        )
        self.assertEqual(self.context.read_context(A)["profiles"][0]["content"], "one")

    def test_skill_crud_rename_sort_uniqueness_and_id_never_reused(self):
        z = self.create("z-last")
        a = self.create("a-first")
        self.assertEqual(
            [s["name"] for s in self.context.list_skills(A)["skills"]],
            ["a-first", "z-last"],
        )
        self.assert_code(
            "CHAT_SKILL_NAME_CONFLICT",
            self.context.create_skill,
            A,
            2,
            skill("a-first"),
        )
        self.assert_code("CHAT_CONTEXT_CHANGED", self.context.get_skill, A, z["id"], 1)
        self.assertEqual(self.context.get_skill(A, z["id"], 2)["skill"], z)
        self.assert_code(
            "CHAT_SKILL_REVISION_CONFLICT",
            self.context.update_skill,
            A,
            z["id"],
            2,
            0,
            skill("new-name"),
        )
        self.assert_code(
            "CHAT_SKILL_NAME_CONFLICT",
            self.context.update_skill,
            A,
            z["id"],
            2,
            1,
            skill("a-first"),
        )
        same = self.context.update_skill(A, z["id"], 2, 1, z["content"])
        self.assertFalse(same["changed"])
        changed = self.context.update_skill(
            A, z["id"], 2, 1, skill("new-name", "updated")
        )
        self.assertEqual(changed["skill"]["id"], z["id"])
        self.assertEqual(changed["skill"]["created_at"], z["created_at"])
        self.assertEqual(changed["skill"]["revision"], 2)
        self.assertFalse(
            self.store.fs.exists(
                f"site-a/users/12/skills/{z['id']}/versions/1/SKILL.md"
            )
        )
        self.assert_code(
            "CHAT_SKILL_REVISION_CONFLICT", self.context.delete_skill, A, z["id"], 3, 1
        )
        removed = self.context.delete_skill(A, z["id"], 3, 2)
        self.assertEqual(
            removed, {"context_revision": 4, "deleted_id": z["id"], "deleted": True}
        )
        self.assert_code("CHAT_NOT_FOUND", self.context.delete_skill, A, z["id"], 4, 2)
        self.assertNotEqual(self.create("new-name")["id"], z["id"])
        self.assertEqual(a["revision"], 1)

    def test_two_users_and_admin_have_no_ownership_bypass(self):
        owned = self.create()
        self.context.update_profile(A, "MEMORY.md", 1, 0, "private A")
        for other in [B, ADMIN]:
            for operation, args in [
                (self.context.get_skill, (other, owned["id"])),
                (self.context.update_skill, (other, owned["id"], 2, 1, skill())),
                (self.context.delete_skill, (other, owned["id"], 2, 1)),
                (self.context.freeze_context, (other, CID, RID, 2, [owned["id"]])),
                (self.context.get_run_context, (other, CID, RID)),
            ]:
                self.assert_code("CHAT_NOT_FOUND", operation, *args, status=404)
            self.assertEqual(self.context.read_context(other)["context_revision"], 0)
            self.assertEqual(self.context.list_skills(other)["total"], 0)
        # Same numeric user on another configured instance never sees this data.
        self.store.prefix = "site-b"
        self.assertEqual(self.context.read_context(A)["context_revision"], 0)

    def test_skill_count_limit_and_reuse_of_deleted_name(self):
        revision = 0
        first = None
        for n in range(config.SKILLS_PER_USER):
            result = self.context.create_skill(A, revision, skill(f"s-{n}"))
            revision = result["context_revision"]
            first = first or result["skill"]
        self.assertEqual(self.context.list_skills(A)["total"], 64)
        self.assert_code(
            "CHAT_SKILL_LIMIT", self.context.create_skill, A, revision, skill("excess")
        )
        self.context.delete_skill(A, first["id"], revision, first["revision"])
        replacement = self.context.create_skill(A, revision + 1, skill(first["name"]))[
            "skill"
        ]
        self.assertNotEqual(replacement["id"], first["id"])
        self.assertEqual(self.context.list_skills(A)["total"], 64)

    def test_freeze_selection_order_unselected_not_read_and_deep_immutability(self):
        one = self.create("one")
        two = self.create("two")
        self.create("unselected")
        self.context.update_profile(A, "MEMORY.md", 3, 0, "private memory")
        reads = []
        original = self.store.fs.read_bytes

        def read(path, *args, **kwargs):
            reads.append(path)
            return original(path, *args, **kwargs)

        with patch.object(self.store.fs, "read_bytes", side_effect=read):
            snapshot = self.context.freeze_context(
                A, CID, RID, 4, [two["id"], one["id"]]
            )
        self.assertEqual(snapshot["skill_ids"], [two["id"], one["id"]])
        self.assertEqual([s["name"] for s in snapshot["skills"]], ["two", "one"])
        self.assertEqual(len([p for p in reads if p.endswith("SKILL.md")]), 2)
        self.assertEqual(snapshot["profiles"][0]["content"], "private memory")
        for mutate in [
            lambda: snapshot.update(context_revision=99),
            lambda: snapshot["profiles"].append({}),
            lambda: snapshot["profiles"][0].update(content="bad"),
            lambda: snapshot["skill_ids"].reverse(),
        ]:
            with self.assertRaises(TypeError):
                mutate()
        self.assertIsInstance(json.loads(canonical_json(snapshot)), dict)
        self.assertIs(copy.deepcopy(snapshot), snapshot)
        self.assertIs(copy.deepcopy(snapshot["profiles"]), snapshot["profiles"])
        self.context.update_profile(A, "MEMORY.md", 4, 1, "new memory")
        self.context.delete_skill(A, two["id"], 5, 1)
        self.assertEqual(snapshot["profiles"][0]["content"], "private memory")
        self.assertEqual(snapshot["skills"][0]["name"], "two")
        self.assertEqual(
            self.context.freeze_context(A, CID, str(uuid4()), 6, [])["profiles"][0][
                "content"
            ],
            "new memory",
        )

    def test_freeze_rejects_invalid_selection_stale_revision_and_oversized_block(self):
        one = self.create()
        for selected in [
            None,
            (),
            [one["id"]] * 2,
            [str(uuid4()) for _ in range(4)],
            ["../bad"],
            [True],
        ]:
            self.assert_code(
                "CHAT_INVALID_REQUEST",
                self.context.freeze_context,
                A,
                CID,
                RID,
                1,
                selected,
            )
        self.assert_code(
            "CHAT_NOT_FOUND",
            self.context.freeze_context,
            A,
            CID,
            RID,
            1,
            [str(uuid4())],
        )
        self.assert_code(
            "CHAT_CONTEXT_CHANGED",
            self.context.freeze_context,
            A,
            CID,
            RID,
            0,
            [one["id"]],
        )
        self.context.update_profile(A, "MEMORY.md", 1, 0, "m" * config.MEMORY_BYTES)
        self.context.update_profile(A, "SOUL.md", 2, 0, "s" * config.SOUL_BYTES)
        self.context.update_profile(A, "IDENTITY.md", 3, 0, "i" * config.IDENTITY_BYTES)
        ids = []
        for n in range(3):
            prefix = skill(f"large-{n}", "")
            document = self.context.create_skill(
                A,
                4 + n,
                prefix + "x" * (config.SKILL_FILE_BYTES - len(prefix.encode())),
            )["skill"]
            ids.append(document["id"])
        self.assert_code(
            "CHAT_CONTEXT_TOO_LARGE",
            self.context.freeze_context,
            A,
            CID,
            RID,
            7,
            ids,
            status=413,
        )
        self.assertFalse(self.store.fs.exists(f"site-a/users/12/conversations/{CID}"))

    def test_freeze_counts_json_escaping_and_snapshot_serialization_limit(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, '"' * config.MEMORY_BYTES)
        self.context.update_profile(A, "SOUL.md", 1, 0, "\t" * config.SOUL_BYTES)
        self.context.update_profile(
            A, "IDENTITY.md", 2, 0, "\t" * config.IDENTITY_BYTES
        )
        prefix = skill("escaped", "")
        selected = self.context.create_skill(
            A, 3, prefix + "x" + "\t" * (3999 - len(prefix.encode()))
        )["skill"]
        # Raw files are < 20 KiB; their JSON-escaped prompt is > 32 KiB.
        self.assert_code(
            "CHAT_CONTEXT_TOO_LARGE",
            self.context.freeze_context,
            A,
            CID,
            RID,
            4,
            [selected["id"]],
        )
        with patch.object(config, "CONTEXT_SNAPSHOT_BYTES", 1):
            self.assert_code(
                "CHAT_CONTEXT_TOO_LARGE",
                self.context.freeze_context,
                A,
                CID,
                RID,
                4,
                [],
            )

    def test_snapshot_get_reads_published_accepted_not_current_settings(self):
        selected = self.create()
        snapshot = self.context.freeze_context(A, CID, RID, 1, [selected["id"]])
        path, ref = self.store.publish(snapshot, commit=False)
        self.assert_code("CHAT_NOT_FOUND", self.context.get_run_context, A, CID, RID)
        self.store.accepted[(12, CID, RID)] = {"context_snapshot": ref}
        original = self.store.fs.read_bytes(path)
        self.context.delete_skill(A, selected["id"], 1, 1)
        self.context.update_profile(A, "MEMORY.md", 2, 0, "future")
        result = self.context.get_run_context(A, CID, RID)
        self.assertEqual(result, snapshot)
        self.assertEqual(original, self.store.fs.read_bytes(path))
        self.assert_code(
            "CHAT_NOT_FOUND", self.context.get_run_context, A, str(uuid4()), RID
        )
        self.assert_code(
            "CHAT_NOT_FOUND", self.context.get_run_context, A, CID, str(uuid4())
        )

    def test_snapshot_corruption_reference_schema_hash_budget_and_ids(self):
        snapshot = self.context.freeze_context(A, CID, RID, 0, [])
        path, reference = self.store.publish(snapshot)
        original = self.store.fs.read_bytes(path)

        def write_corrupt(mutator):
            value = json.loads(original)
            mutator(value)
            raw = canonical_json(value) + b"\n"
            self.store.fs.atomic_write(path, raw)
            reference["sha256"] = hashlib.sha256(raw).hexdigest()

        for mutation in [
            lambda value: value.update(v=2),
            lambda value: value.update(conversation_id=str(uuid4())),
            lambda value: value.update(run_id=str(uuid4())),
            lambda value: value.update(session="forbidden-field"),
            lambda value: value["profiles"][0].update(content="tampered"),
            lambda value: value["budget"].update(personal_block_bytes=0),
            lambda value: value.update(context_revision=1),
            lambda value: value.update(skill_ids=[str(uuid4())]),
            lambda value: value.update(policy_version="new-policy"),
        ]:
            write_corrupt(mutation)
            self.assert_code(
                "CHAT_CONTEXT_CORRUPT",
                self.context.get_run_context,
                A,
                CID,
                RID,
                status=503,
            )
        self.store.fs.atomic_write(path, original)
        reference["sha256"] = "0" * 64
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT", self.context.get_run_context, A, CID, RID
        )
        reference["sha256"] = hashlib.sha256(original).hexdigest()
        reference["file"] = "../../other-user"
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT", self.context.get_run_context, A, CID, RID
        )
        reference["file"] = f"contexts/{RID}.json"
        self.store.fs.unlink(path)
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT", self.context.get_run_context, A, CID, RID
        )

    def test_manifest_missing_with_orphan_is_corrupt_not_empty(self):
        path = "site-a/users/12/profiles/MEMORY/versions/1.md"
        self.store.fs.mkdir(path.rsplit("/", 1)[0], parents=True)
        self.store.fs.atomic_write(path, b"orphan")
        self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT",
            self.context.update_profile,
            A,
            "MEMORY.md",
            0,
            0,
            "new",
        )
        self.assertEqual(self.store.fs.read_bytes(path), b"orphan")

    def test_manifest_and_referenced_content_corruption_never_falls_back(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old")
        original = self.store.fs.read_bytes(self.manifest())
        for raw in [
            b"not-json",
            b'{"v":1,"v":1}',
            b'{"v":NaN}',
            original.replace(b'"v":1', b'"v":2'),
            original.replace(b'"v":1', b'"v":true'),
        ]:
            self.store.fs.atomic_write(self.manifest(), raw)
            self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)
        self.store.fs.atomic_write(self.manifest(), original)
        path = "site-a/users/12/profiles/MEMORY/versions/1.md"
        self.store.fs.atomic_write(path, b"new")
        self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)
        self.store.fs.unlink(path)
        self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)

    def test_manifest_rejects_schema_types_metadata_and_oversized_file(self):
        selected = self.create()
        original = self.store.fs.read_bytes(self.manifest())
        for mutate in [
            lambda value: value.update(owner_user_id=12),
            lambda value: value.update(context_revision=True),
            lambda value: value.update(updated_at="2026-02-30T00:00:00Z"),
            lambda value: value["profiles"]["MEMORY.md"].update(size_bytes=True),
            lambda value: value["profiles"]["MEMORY.md"].update(revision=2),
            lambda value: value["skills"][selected["id"]].update(id=str(uuid4())),
            lambda value: value["skills"][selected["id"]].update(revision=False),
            lambda value: value["skills"][selected["id"]].update(name="../outside"),
            lambda value: value["skills"][selected["id"]].update(description="\ud800"),
        ]:
            value = json.loads(original)
            mutate(value)
            raw = (json.dumps(value, ensure_ascii=True) + "\n").encode()
            self.store.fs.atomic_write(self.manifest(), raw)
            self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)
        self.store.fs.atomic_write(
            self.manifest(), b"x" * (config.CONTEXT_MANIFEST_BYTES + 1)
        )
        self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)

    def test_skill_hash_and_metadata_must_match_full_file(self):
        selected = self.create()
        path = f"site-a/users/12/skills/{selected['id']}/versions/1/SKILL.md"
        self.store.fs.atomic_write(path, skill("other-name").encode())
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT", self.context.get_skill, A, selected["id"]
        )
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT",
            self.context.freeze_context,
            A,
            CID,
            RID,
            1,
            [selected["id"]],
        )
        manifest = self.store.fs.read_json(self.manifest())
        raw = self.store.fs.read_bytes(path)
        manifest["skills"][selected["id"]].update(
            sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
        )
        self.store.fs.write_json(self.manifest(), manifest)
        self.assert_code(
            "CHAT_CONTEXT_CORRUPT", self.context.get_skill, A, selected["id"]
        )
        self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.list_skills, A)
        self.assert_code("CHAT_CONTEXT_CORRUPT", self.context.read_context, A)

    def test_symlink_hardlink_fifo_and_path_traversal_fail_closed(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old")
        path = self.disk("site-a/users/12/profiles/MEMORY/versions/1.md")
        path.unlink()
        outside = Path(self.temp.name, "external")
        outside.write_text("outside secret")
        path.symlink_to(outside)
        with self.assertRaises(ChatError):
            self.context.read_context(A)
        path.unlink()
        os.link(outside, path)
        with self.assertRaises(ChatError):
            self.context.read_context(A)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ChatError):
            self.context.read_context(A)
        self.assertEqual(outside.read_text(), "outside secret")
        for value in ["../12", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", 1, None]:
            self.assert_code("CHAT_INVALID_REQUEST", self.context.get_skill, A, value)

    def test_quotas_include_temporary_quarantine_user_total_and_disk_free(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old")
        manifest = self.store.fs.read_bytes(self.manifest())
        for size in [1, 100]:
            with patch.object(config, "USER_CONTEXT_STORAGE_BYTES", size):
                self.assert_code(
                    "CHAT_STORAGE_LIMIT",
                    self.context.update_profile,
                    A,
                    "MEMORY.md",
                    1,
                    1,
                    "new",
                )
        for directory in [".context-tmp", ".context-quarantine"]:
            path = f"site-a/users/12/{directory}/leftover"
            self.store.fs.mkdir(path.rsplit("/", 1)[0], parents=True)
            self.store.fs.atomic_write(path, b"x" * 200)
            with patch.object(config, "USER_CONTEXT_STORAGE_BYTES", 200):
                self.assert_code(
                    "CHAT_STORAGE_LIMIT",
                    self.context.update_profile,
                    A,
                    "MEMORY.md",
                    1,
                    1,
                    "new",
                )
        self.store.user_limit = 10
        self.assert_code(
            "CHAT_STORAGE_LIMIT",
            self.context.update_profile,
            A,
            "MEMORY.md",
            1,
            1,
            "new",
        )
        self.store.user_limit = config.USER_STORAGE_BYTES
        self.store.other_reserved = config.TOTAL_STORAGE_BYTES
        self.assert_code(
            "CHAT_STORAGE_LIMIT",
            self.context.update_profile,
            A,
            "MEMORY.md",
            1,
            1,
            "new",
        )
        self.store.other_reserved = 0
        with patch.object(self.store.fs, "free_bytes", return_value=1):
            self.assert_code(
                "CHAT_STORAGE_FULL",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "new",
            )
        self.assertEqual(self.store.fs.read_bytes(self.manifest()), manifest)

    def test_atomic_failure_before_manifest_keeps_old_state_and_quarantines_orphan(
        self,
    ):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old")
        original = self.store.fs.atomic_write

        def fail_manifest(path, data, **kwargs):
            if path == self.manifest():
                raise OSError(errno.ENOSPC, "do not expose path or content")
            return original(path, data, **kwargs)

        with patch.object(self.store.fs, "atomic_write", side_effect=fail_manifest):
            self.assert_code(
                "CHAT_STORAGE_FULL",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "uncommitted",
            )
        view = self.context.read_context(A)
        self.assertEqual(
            (view["context_revision"], view["profiles"][0]["content"]), (1, "old")
        )
        self.context.update_profile(A, "MEMORY.md", 1, 1, "new")
        quarantine = "site-a/users/12/.context-quarantine"
        self.assertTrue(self.store.fs.listdir(quarantine))
        self.assertIn(
            b"uncommitted",
            [
                self.store.fs.read_bytes(f"{quarantine}/{name}")
                for name in self.store.fs.listdir(quarantine)
            ],
        )
        self.assertEqual(self.context.read_context(A)["profiles"][0]["content"], "new")

    def test_delete_commit_survives_cleanup_failure(self):
        selected = self.create()
        with patch.object(
            self.store.fs, "unlink", side_effect=OSError(errno.EIO, "cleanup failure")
        ):
            result = self.context.delete_skill(A, selected["id"], 1, 1)
        self.assertTrue(result["deleted"])
        self.assertEqual(self.context.list_skills(A)["total"], 0)
        self.assert_code("CHAT_NOT_FOUND", self.context.get_skill, A, selected["id"])
        self.assertTrue(
            self.store.fs.exists(
                f"site-a/users/12/skills/{selected['id']}/versions/1/SKILL.md"
            )
        )

    def test_version_exhaustion_never_wraps_or_modifies_valid_content(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old")
        manifest = self.store.fs.read_json(self.manifest())
        manifest["context_revision"] = MAX_REVISION
        self.store.fs.write_json(self.manifest(), manifest)
        original = self.store.fs.read_bytes(self.manifest())
        self.assert_code(
            "CHAT_STORAGE_UNAVAILABLE",
            self.context.update_profile,
            A,
            "MEMORY.md",
            MAX_REVISION,
            1,
            "new",
        )
        self.assertEqual(self.store.fs.read_bytes(self.manifest()), original)

    def test_profile_lock_is_bounded_cross_process_and_does_not_reenter_conversation_lock(
        self,
    ):
        with self.context._lock(A):
            self.assert_code(
                "CHAT_PROFILE_BUSY", self.context.read_context, A, status=409
            )
            self.assert_code(
                "CHAT_PROFILE_BUSY", self.context.freeze_context, A, CID, RID, 0, []
            )
            self.assertEqual(self.context.read_context(B)["context_revision"], 0)
        path = f"site-a/.locks/conversations/12.{CID}.lock"
        with FileLock(self.store.fs, path):
            self.assertEqual(
                self.context.freeze_context(A, CID, RID, 0, [])["context_revision"], 0
            )

    def test_multiprocess_writers_cannot_lose_updates(self):
        ctx = multiprocessing.get_context("spawn")
        barrier, output = ctx.Barrier(2), ctx.Queue()
        processes = [
            ctx.Process(target=competing_save, args=(self.root, barrier, output, name))
            for name in ["MEMORY.md", "SOUL.md"]
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join()
                self.fail("profile lock operation did not finish")
            self.assertEqual(process.exitcode, 0)
        results = [output.get(timeout=2) for _ in processes]
        self.assertEqual(sum(item[0] == "ok" for item in results), 1, results)
        self.assertIn(
            next(item[0] for item in results if item[0] != "ok"),
            {"CHAT_PROFILE_BUSY", "CHAT_CONTEXT_CHANGED"},
            results,
        )
        self.assertEqual(self.context.read_context(A)["context_revision"], 1)
        output.close()
        output.join_thread()

    def test_credentials_rejected_and_platform_access_never_serialized(self):
        forbidden = [
            "Authorization: Bearer token",
            "cookie = session",
            "X-Cockpit-Session: marker",
            "sk-" + "A" * 16,
            "eyJ" + "A" * 8 + "." + "B" * 8 + "." + "C" * 8,
            *[
                f"-----BEGIN {kind}PRIVATE KEY-----"
                for kind in ["", "RSA ", "EC ", "OPENSSH ", "DSA "]
            ],
        ]
        for content in forbidden:
            error = self.assert_code(
                "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
                self.context.update_profile,
                A,
                "MEMORY.md",
                0,
                0,
                content,
                status=422,
            )
            self.assertNotIn(content, str(error))
            self.assertEqual(error.details, {})
            self.assert_code(
                "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
                self.context.create_skill,
                A,
                0,
                skill(body=content),
            )
        encoded = skill().replace('"中文周报"', '"\\u0073k-' + "A" * 16 + '"')
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            self.context.create_skill,
            A,
            0,
            encoded,
        )
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            validate_credentials,
            "contains exact-secret-sentinel",
            "exact-secret-sentinel",
        )
        escaped_description = skill(description="current-credential-sentinel").replace(
            "current", "\\u0063urrent"
        )
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            validate_credentials,
            escaped_description,
            "current-credential-sentinel",
        )
        validate_credentials("KEY user_id 12 my-name")
        access = RequestAccess(A, "key-SECRET-sentinel")
        self.context.update_profile(A, "MEMORY.md", 0, 0, "allowed text")
        selected = self.create()
        snapshot = self.context.freeze_context(
            access.principal, CID, RID, 2, [selected["id"]]
        )
        self.store.publish(snapshot)
        for path in Path(self.root).rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                self.assertNotIn(access.credential.encode(), raw)
                self.assertNotIn(A.session_id.encode(), raw)


class ChatStoreContextIntegrationTest(unittest.TestCase):
    """Bind the T05 service to the actual shared store, not a mock API."""

    def setUp(self):
        from new_api_cockpit.features.chat.storage import ChatStore

        self.temp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.temp.name)
        self.store = ChatStore(
            config.Settings(data_dir=self.root, instance_id="site-a", enabled=True)
        )
        self.context = UserContext(self.store, clock=lambda: TIME)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ChatError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def create_scope(self):
        self.store.create_conversation(A, cid=CID)
        return self.store.open_conversation(A, CID)

    @staticmethod
    def accepted(reference):
        return {
            "client_request_id": str(uuid4()),
            "message_id": str(uuid4()),
            "text": "No paid request",
            "attachment_ids": [],
            "token_id": 123,
            "model": "offline-test",
            "mode": "chat",
            "capability_revision": "sha256:" + "a" * 64,
            "context_snapshot": reference,
            "context_from_run_ids": [],
        }

    def test_real_store_empty_read_profile_skill_transactions_and_reopen(self):
        self.assertEqual(self.context.read_context(A)["context_revision"], 0)
        self.assertFalse(self.store.fs.exists("site-a/users/12"))
        self.context.update_profile(A, "MEMORY.md", 0, 0, "中文\r\n记忆")
        created = self.context.create_skill(A, 1, skill())["skill"]
        self.assertEqual(self.context.get_skill(A, created["id"], 2)["skill"], created)
        self.assertEqual(self.store.fs.listdir("site-a/reservations"), [])
        from new_api_cockpit.features.chat.storage import ChatStore

        reopened = ChatStore(self.store.settings)
        try:
            context = UserContext(reopened)
            view = context.read_context(
                Principal(12, "renamed", session_id="new-session")
            )
            self.assertEqual(view["profiles"][0]["content"], "中文\n记忆")
            self.assertEqual(view["context_revision"], 2)
            self.assertEqual(context.read_context(B)["context_revision"], 0)
            self.assertEqual(context.list_skills(ADMIN)["total"], 0)
        finally:
            reopened.close()

    def test_real_snapshot_is_invisible_until_accepted_and_remains_immutable(self):
        scope = self.create_scope()
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old memory")
        selected = self.context.create_skill(A, 1, skill())["skill"]
        with self.store.lease(scope, run=True) as lease:
            snapshot = self.context.freeze_context(A, CID, RID, 2, [selected["id"]])
            reference = self.store.write_context(scope, RID, snapshot, lease=lease)
            self.assert_code(
                "CHAT_NOT_FOUND", self.context.get_run_context, A, CID, RID
            )
            self.store.append(
                scope, "run.accepted", self.accepted(reference), run_id=RID, lease=lease
            )
            loaded = self.context.get_run_context(A, CID, RID)
            self.assertEqual(loaded, snapshot)
            self.assertNotIn("context_snapshot", loaded)  # HTTP wraps exactly once.
            before = self.store.fs.read_bytes(f"{scope.path}/contexts/{RID}.json")
            self.context.update_profile(A, "MEMORY.md", 2, 1, "new memory")
            self.context.delete_skill(A, selected["id"], 3, 1)
            self.assertEqual(self.context.get_run_context(A, CID, RID), snapshot)
            self.assertEqual(
                before, self.store.fs.read_bytes(f"{scope.path}/contexts/{RID}.json")
            )
            self.assert_code(
                "CHAT_NOT_FOUND", self.context.get_run_context, B, CID, RID
            )
            self.assert_code(
                "CHAT_NOT_FOUND", self.context.get_run_context, ADMIN, CID, RID
            )
        self.assertEqual(self.store.fs.listdir("site-a/reservations"), [])

    def test_real_commit_high_water_hides_complete_but_unpublished_accepted(self):
        scope = self.create_scope()
        with self.store.lease(scope, run=True) as lease:
            snapshot = self.context.freeze_context(A, CID, RID, 0, [])
            reference = self.store.write_context(scope, RID, snapshot, lease=lease)
            commit = self.store.fs.read_json(f"{scope.path}/commit.json")
            event = {
                "v": 1,
                "seq": commit["seq"] + 1,
                "type": "run.accepted",
                "conversation_id": CID,
                "run_id": RID,
                "at": TIME,
                "data": self.accepted(reference),
            }
            raw = canonical_json(event) + b"\n"
            fd = self.store.fs.open_file(
                f"{scope.path}/events.jsonl", os.O_WRONLY | os.O_APPEND
            )
            try:
                self.store.fs.write_all(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            self.assert_code(
                "CHAT_NOT_FOUND", self.context.get_run_context, A, CID, RID
            )
            self.assertEqual(
                self.store.fs.read_json(f"{scope.path}/commit.json"), commit
            )
            self.store.fs.write_json(
                f"{scope.path}/commit.json",
                {
                    "v": 1,
                    "seq": event["seq"],
                    "byte_offset": commit["byte_offset"] + len(raw),
                },
            )
            self.assertEqual(self.context.get_run_context(A, CID, RID), snapshot)

    def test_real_reference_revision_mismatch_and_missing_blob_are_context_corruption(
        self,
    ):
        scope = self.create_scope()
        with self.store.lease(scope, run=True) as lease:
            snapshot = self.context.freeze_context(A, CID, RID, 0, [])
            reference = self.store.write_context(scope, RID, snapshot, lease=lease)
            reference["context_revision"] = 9
            self.assert_code(
                "CHAT_STORAGE_CORRUPT",
                self.store.append,
                scope,
                "run.accepted",
                self.accepted(reference),
                run_id=RID,
                lease=lease,
            )
            reference["context_revision"] = 0
            self.store.append(
                scope, "run.accepted", self.accepted(reference), run_id=RID, lease=lease
            )
            self.store.fs.unlink(f"{scope.path}/contexts/{RID}.json")
            self.assert_code(
                "CHAT_CONTEXT_CORRUPT", self.context.get_run_context, A, CID, RID
            )

    def test_real_profile_subquota_global_user_reservations_and_disk_failure(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "m" * config.MEMORY_BYTES)
        manifest_path = "site-a/users/12/context-manifest.json"
        before = self.store.fs.read_bytes(manifest_path)
        with patch.object(config, "USER_CONTEXT_STORAGE_BYTES", 20000):
            self.assert_code(
                "CHAT_STORAGE_LIMIT",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "x" * 2000,
            )
        with patch.object(config, "USER_STORAGE_BYTES", 1):
            self.assert_code(
                "CHAT_STORAGE_LIMIT",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "new",
            )
        from dataclasses import replace

        with patch.object(
            self.store, "settings", replace(self.store.settings, total_storage_bytes=1)
        ):
            self.assert_code(
                "CHAT_STORAGE_LIMIT",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "new",
            )
        with patch.object(self.store.fs, "free_bytes", return_value=1):
            self.assert_code(
                "CHAT_STORAGE_FULL",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "new",
            )
        self.assertEqual(self.store.fs.read_bytes(manifest_path), before)
        self.assertEqual(self.store.fs.listdir("site-a/reservations"), [])

    def test_real_failed_manifest_commit_releases_reservation_and_keeps_old(self):
        self.context.update_profile(A, "MEMORY.md", 0, 0, "old")
        original = self.store.fs.atomic_write

        def fail(path, data, **kwargs):
            if path == "site-a/users/12/context-manifest.json":
                raise OSError(errno.ENOSPC, "disk full")
            return original(path, data, **kwargs)

        with patch.object(self.store.fs, "atomic_write", side_effect=fail):
            self.assert_code(
                "CHAT_STORAGE_FULL",
                self.context.update_profile,
                A,
                "MEMORY.md",
                1,
                1,
                "new",
            )
        self.assertEqual(self.context.read_context(A)["profiles"][0]["content"], "old")
        self.assertEqual(self.store.fs.listdir("site-a/reservations"), [])


if __name__ == "__main__":
    unittest.main()
