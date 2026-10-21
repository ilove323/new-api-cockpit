"""Offline M01/M02 tests against the real UserContext and ChatStore.

No service, model, network, real keys or paid calls are used.
"""

from dataclasses import FrozenInstanceError
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from new_api_cockpit.features.chat.auto_memory import (
    MemoryProposal,
    apply_proposal,
    build_prompt,
    parse_proposal,
    prepare_update,
    undo_update,
    validate_audit,
)
from new_api_cockpit.features.chat.config import Settings
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import canonical_json
from new_api_cockpit.features.chat.storage import ChatStore
from new_api_cockpit.features.chat.user_context import UserContext
from new_api_cockpit.platform.identity import Principal


A = Principal(42, "owner")
B = Principal(43, "other")
ADMIN = Principal(1, "admin", role=100)
MID = "11111111-1111-4111-8111-111111111111"
MID2 = "22222222-2222-4222-8222-222222222222"
FACT = "我长期偏好用中文沟通。"
NEW_FACT = "我长期喜欢简洁回答。"
FORGET = "请忘记我偏好用中文沟通。"
START = "\n<!-- cockpit:auto-memory:v1 -->\n"
END = "\n<!-- /cockpit:auto-memory:v1 -->"


def add(text=FACT, mid=MID):
    return {
        "op": "add",
        "text": text,
        "sources": [{"message_id": mid, "excerpt": text}],
    }


def facts(content):
    return json.loads(content.split(START, 1)[1].split(END, 1)[0])["facts"]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AutoMemoryV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name).resolve())
        self.store = ChatStore(
            Settings(data_dir=self.root, instance_id="memory-v2", enabled=True)
        )
        self.context = UserContext(self.store)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.sources = [{"message_id": MID, "text": f"{FACT} {NEW_FACT} {FORGET}"}]

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ChatError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def current(self, principal=A):
        view = self.context.read_context(principal)
        return view, next(p for p in view["profiles"] if p["name"] == "MEMORY.md")

    def save(self, content, *, principal=A, name="MEMORY.md"):
        view = self.context.read_context(principal)
        profile = next(p for p in view["profiles"] if p["name"] == name)
        return self.context.update_profile(
            principal, name, view["context_revision"], profile["revision"], content
        )

    def proposal(self, operations=None, *, content=None, sources=None):
        if operations is None:
            operations = [add()]
        if content is None:
            content = self.current()[1]["content"]
        return parse_proposal(
            json.dumps({"v": 1, "operations": operations}, ensure_ascii=False),
            self.sources if sources is None else sources,
            content,
        )

    def apply(self, proposal=None, principal=A):
        view, memory = self.current(principal)
        return apply_proposal(
            self.context,
            principal,
            view["context_revision"],
            memory["revision"],
            self.proposal() if proposal is None else proposal,
        )

    def update_op(self, fid, text=NEW_FACT):
        return {
            "op": "update",
            "fact_id": fid,
            "text": text,
            "sources": [{"message_id": MID, "excerpt": text}],
        }

    def delete_op(self, fid):
        return {
            "op": "delete",
            "fact_id": fid,
            "sources": [{"message_id": MID, "excerpt": FORGET}],
        }

    def test_prompt_is_portable_bounded_untrusted_data_and_never_calls_model(self):
        with patch(
            "socket.create_connection", side_effect=AssertionError("no network")
        ):
            messages = build_prompt(self.sources, "# 手工记忆\n保留。\n")
            proposal = self.proposal(content="# 手工记忆\n保留。\n")
            prepared = prepare_update("# 手工记忆\n保留。\n", proposal)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertTrue(all(set(m) == {"role", "content"} for m in messages))
        self.assertIn("untrusted data", messages[0]["content"])
        self.assertIn("NOT independently verified truth", messages[0]["content"])
        self.assertIn("Never edit SOUL", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["user_sources"], self.sources)
        self.assertEqual(payload["memory_content"], "# 手工记忆\n保留。\n")
        self.assertNotIn("profile", prepared)

    def test_deterministic_prepare_matches_manifest_commit_and_complete_audit(self):
        self.save("手工正文\n\n  保留空白\t与中文。")
        proposal = self.proposal()
        self.assertIsInstance(proposal, MemoryProposal)
        with self.assertRaises(FrozenInstanceError):
            proposal.base_memory_sha256 = "x"
        before = self.current()[1]["content"]
        intent = prepare_update(before, proposal)
        self.assertEqual(intent, prepare_update(before, self.proposal()))
        self.assertEqual(
            set(intent),
            {"before", "after", "before_sha256", "after_sha256", "source_message_ids"},
        )
        audit = self.apply(proposal)
        self.assertEqual(
            set(audit),
            {
                "v",
                "kind",
                "owner_user_id",
                "profile",
                "changed",
                "before",
                "after",
                "source_ids",
                "fact_ids",
            },
        )
        self.assertEqual(audit["owner_user_id"], A.user_id)
        self.assertTrue(audit["changed"])
        self.assertEqual(audit["profile"], "MEMORY.md")
        self.assertEqual(audit["before"]["content"], intent["before"])
        self.assertEqual(audit["after"]["content"], intent["after"])
        self.assertEqual(audit["after"]["sha256"], intent["after_sha256"])
        self.assertEqual(audit["before"]["sha256"], intent["before_sha256"])
        self.assertEqual(audit["before"]["context_revision"], 1)
        self.assertEqual(audit["after"]["context_revision"], 2)
        self.assertEqual(audit["before"]["memory_revision"], 1)
        self.assertEqual(audit["after"]["memory_revision"], 2)
        self.assertEqual(audit["source_ids"], [MID])
        stored_fact = facts(audit["after"]["content"])[0]
        self.assertEqual(stored_fact["kind"], "user_stated")
        self.assertEqual(
            stored_fact["sources"][0]["message_sha256"], digest(self.sources[0]["text"])
        )
        self.assertEqual(audit["fact_ids"], [stored_fact["id"]])
        self.assertEqual(self.current()[1]["content"], intent["after"])

    def test_add_update_delete_preserve_manual_prefix_suffix_and_stable_ids(self):
        manual = "# 手写\n逐字保留尾空格   \n\n"
        self.save(manual)
        initial = self.apply()
        fid = initial["fact_ids"][0]
        # An explicit manual edit outside the block is preserved, too.
        suffix = "\n\n手工后记，不能被自动覆盖。\t"
        self.save(initial["after"]["content"] + suffix)
        updated = self.apply(self.proposal([self.update_op(fid)]))
        self.assertTrue(updated["after"]["content"].startswith(manual + START))
        self.assertTrue(updated["after"]["content"].endswith(END + suffix))
        changed_fact = facts(updated["after"]["content"])[0]
        self.assertEqual(changed_fact["id"], fid)
        self.assertEqual(changed_fact["text"], NEW_FACT)
        deleted = self.apply(self.proposal([self.delete_op(fid)]))
        self.assertEqual(
            deleted["after"]["content"].encode(), (manual + suffix).encode()
        )
        self.assertNotIn("cockpit:auto-memory", deleted["after"]["content"])

    def test_undo_is_memory_only_and_restores_original_bytes(self):
        manual = "手工\t内容\n\n  尾空格   "
        self.save(manual)
        self.save("不要修改灵魂", name="SOUL.md")
        self.save("不要修改身份", name="IDENTITY.md")
        before = self.context.read_context(A)
        audit = self.apply()
        result = undo_update(self.context, A, audit, audit["after"]["context_revision"])
        self.assertTrue(result["changed"])
        self.assertEqual(set(result), {"context_revision", "changed", "profile"})
        self.assertEqual(result["profile"]["content"].encode(), manual.encode())
        view = self.context.read_context(A)
        self.assertEqual(view["profiles"][1:], before["profiles"][1:])
        self.assertEqual(
            result["profile"]["revision"], audit["after"]["memory_revision"] + 1
        )

    def test_persisted_bom_survives_apply_and_undo_byte_for_byte(self):
        self.save("\ufeff\ufeff中文手工记忆\n")
        manual = self.current()[1]["content"]
        self.assertTrue(manual.startswith("\ufeff"))
        audit = self.apply()
        self.assertTrue(audit["after"]["content"].startswith(manual))
        result = undo_update(self.context, A, audit, audit["after"]["context_revision"])
        self.assertEqual(result["profile"]["content"].encode(), manual.encode())

    def test_actual_owner_chat_sources_and_settings_are_isolated_even_for_admin(self):
        cid = self.store.create_conversation(A)["id"]
        scope = self.store.open_conversation(A, cid)
        rid, mid = str(uuid4()), str(uuid4())
        with self.store.lease(scope, run=True) as lease:
            snapshot = self.context.freeze_context(A, cid, rid, 0, [])
            reference = self.store.write_context(scope, rid, snapshot, lease=lease)
            self.store.append(
                scope,
                "run.accepted",
                {
                    "client_request_id": str(uuid4()),
                    "message_id": mid,
                    "text": FACT,
                    "attachment_ids": [],
                    "token_id": 123,
                    "model": "offline-only",
                    "mode": "chat",
                    "capability_revision": "sha256:" + "a" * 64,
                    "context_snapshot": reference,
                    "context_from_run_ids": [],
                },
                run_id=rid,
                lease=lease,
            )
        accepted = next(
            e
            for e in self.store.read_events(scope)["events"]
            if e["type"] == "run.accepted"
        )
        source = {k: accepted["data"][k] for k in ("message_id", "text")}
        proposal = self.proposal([add(mid=mid)], sources=[source])
        audit = self.apply(proposal)
        self.assertEqual(audit["source_ids"], [mid])
        for principal in (B, ADMIN):
            self.assert_code(
                "CHAT_NOT_FOUND", self.store.open_conversation, principal, cid
            )
            self.assertEqual(self.current(principal)[1]["content"], "")
            self.assert_code(
                "CHAT_MEMORY_UNDO_CONFLICT",
                undo_update,
                self.context,
                principal,
                audit,
                0,
            )
            self.assertEqual(self.current(principal)[0]["context_revision"], 0)
        self.save("other-user-only", principal=B)
        self.assertNotIn("other-user-only", self.current()[1]["content"])

    def test_pat_cannot_apply_or_undo_personal_context(self):
        pat = Principal(A.user_id, "same-id", auth_kind="pat")
        proposal = self.proposal()
        self.assert_code(
            "CHAT_SESSION_REQUIRED", apply_proposal, self.context, pat, 0, 0, proposal
        )
        audit = self.apply(proposal)
        self.assert_code(
            "CHAT_SESSION_REQUIRED", undo_update, self.context, pat, audit, 1
        )

    def test_sources_accept_only_exact_caller_asserted_user_dto(self):
        bad_inputs = [
            [{"message_id": MID, "text": FACT, "role": "assistant"}],
            [{"message_id": MID, "text": FACT, "origin": "webpage"}],
            [{"message_id": MID, "text": FACT}, {"message_id": MID, "text": NEW_FACT}],
            [{"text": FACT}],
            [{"message_id": "../other-user", "text": FACT}],
            [{"message_id": MID, "text": ""}],
            {"message_id": MID, "text": FACT},
        ]
        for sources in bad_inputs:
            with self.subTest(sources=sources):
                self.assert_code(
                    "CHAT_MEMORY_PROPOSAL_INVALID", build_prompt, sources, ""
                )

    def test_unknown_fields_nested_duplicate_json_and_nonfinite_are_rejected(self):
        responses = [
            '{"v":1,"v":1,"operations":[]}',
            '{"v":1,"operations":[{"op":"add","op":"delete"}]}',
            '{"v":1,"operations":[{"op":"add","text":"x","sources":[{"message_id":"x","message_id":"y","excerpt":"x"}]}]}',
            '{"v":1,"operations":[],"SOUL.md":"edit"}',
            '{"v":true,"operations":[]}',
            '{"v":NaN,"operations":[]}',
            '{"v":1,"operations":[{"op":[]}]}',
            '```json\n{"v":1,"operations":[]}\n```',
            '{"v":1,"operations":[]} trailing',
            '{"v":1,"operations":[{"op":"add","text":"\\ud800","sources":[]}]}',
        ]
        for response in responses:
            with self.subTest(response=response):
                self.assert_code(
                    "CHAT_MEMORY_PROPOSAL_INVALID",
                    parse_proposal,
                    response,
                    self.sources,
                    "",
                )
        for extra in ("role", "kind", "confidence", "target", "SOUL.md"):
            op = {**add(), extra: "unsupported"}
            self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", self.proposal, [op])
        op = add()
        op["sources"][0]["role"] = "user"
        self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", self.proposal, [op])

    def test_source_id_must_exist_and_all_evidence_must_be_verbatim(self):
        ops = [
            add(mid=MID2),
            {**add(), "text": "用户会说英文。"},
            {**add(), "sources": [{"message_id": MID, "excerpt": "我偏好英文。"}]},
            {
                **add(),
                "sources": [
                    {"message_id": MID, "excerpt": FACT},
                    {"message_id": MID, "excerpt": FACT},
                ],
            },
            add(
                "我长期偏好用中文沟通"
            ),  # This is a valid exact substring, not a guess.
        ]
        for op in ops[:-1]:
            self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", self.proposal, [op])
        self.assertEqual(len(self.proposal([ops[-1]]).operations), 1)
        # A paraphrase cannot add guessed facts while citing a genuine excerpt.
        op = {**add(), "text": FACT + " 我是中国人。"}
        self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", self.proposal, [op])

    def test_model_generated_fact_id_and_manual_target_update_delete_are_rejected(self):
        self.save("手工事实 ID: manual_fact。")
        for op in (
            {**add(), "fact_id": "fact_" + "a" * 24},
            self.update_op("fact_" + "a" * 24),
            self.delete_op("fact_" + "a" * 24),
            self.update_op("manual_fact"),
            {"op": "replace", "text": FACT, "sources": add()["sources"]},
        ):
            self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", self.proposal, [op])
        self.assertEqual(self.current()[1]["content"], "手工事实 ID: manual_fact。")

    def test_duplicate_operations_and_duplicate_facts_are_rejected(self):
        self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", self.proposal, [add(), add()])
        audit = self.apply()
        fid = audit["fact_ids"][0]
        self.assert_code(
            "CHAT_MEMORY_PROPOSAL_INVALID",
            self.proposal,
            [self.update_op(fid), self.delete_op(fid)],
        )
        other = self.sources + [{"message_id": MID2, "text": FACT}]
        self.assert_code(
            "CHAT_MEMORY_PROPOSAL_INVALID",
            self.proposal,
            [add(mid=MID2)],
            sources=other,
        )

    def test_credentials_rejected_before_prompt_parse_and_without_secret_in_error(self):
        synthetic = [
            "sk-" + "syntheticfixture" * 3,
            "Authorization: Bearer synthetic-only",
            "Bearer synthetic-only",
            "Cookie: synthetic-only",
            "api_key=synthetic-only",
            '"password": "synthetic-only"',
            "client_secret: synthetic-only",
            "access_token=synthetic-only",
            "AKIA" + "Z" * 16,
            "ghp_" + "z" * 36,
            "https://demo:synthetic-only@example.test/",
            "https://example.test/?token=synthetic-only",
            "-----BEGIN PRIVATE KEY-----",
            "eyJabcdefghijk.abcdefghijk.abcdefghijk",
        ]
        for value in synthetic:
            with self.subTest(value=value):
                sources = [{"message_id": MID, "text": value}]
                exc = self.assert_code(
                    "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN", build_prompt, sources, ""
                )
                self.assertNotIn(value, str(exc))
                self.assert_code(
                    "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
                    self.proposal,
                    [add(value)],
                    sources=sources,
                )
                self.assert_code(
                    "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN", build_prompt, [], value
                )

    def test_known_opaque_credential_cannot_be_saved_or_restored(self):
        opaque = "synthetic-opaque-credential-not-a-real-key"
        text = f"我长期使用代号 {opaque}。"
        proposal = self.proposal(
            [add(text)], sources=[{"message_id": MID, "text": text}]
        )
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            apply_proposal,
            self.context,
            A,
            0,
            0,
            proposal,
            credential=opaque,
        )
        self.assertEqual(self.current()[0]["context_revision"], 0)
        self.save(text)
        audit = self.apply(self.proposal([]))
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            undo_update,
            self.context,
            A,
            audit,
            1,
            credential=opaque,
        )

    def test_known_credential_json_escaping_cannot_bypass_apply_or_undo_filter(self):
        opaque = 'synthetic-opaque-"credential\\fixture'
        text = f"我长期使用代号 {opaque}。"
        source = [{"message_id": MID, "text": text}]
        proposal = self.proposal([add(text)], sources=source)
        # The literal credential does not occur in encoded JSON managed bytes.
        intent = prepare_update("", proposal)
        self.assertNotIn(opaque, intent["after"])
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            apply_proposal,
            self.context,
            A,
            0,
            0,
            proposal,
            credential=opaque,
        )
        audit = self.apply(proposal)
        self.assert_code(
            "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN",
            undo_update,
            self.context,
            A,
            audit,
            1,
            credential=opaque,
        )

    def test_malformed_truncated_unknown_version_or_manually_edited_block_fails_safe(
        self,
    ):
        audit = self.apply()
        valid = audit["after"]["content"]
        variants = [
            START + "{}" + END,
            valid.replace("auto-memory:v1", "auto-memory:v2"),
            valid.replace(FACT, NEW_FACT),
            valid.replace('"v":1', '"v":1,"v":1'),
            valid.replace('"facts":', '"facts": '),
            valid + valid,
            valid.replace(END, ""),
            valid.replace(START, START.lstrip("\n")),
            "manual <!-- cockpit:auto-memory:v1 --> broken",
            valid.replace("cockpit:auto-memory", "COCKPIT:AUTO-MEMORY"),
        ]
        for content in variants:
            with self.subTest(content=content[:80]):
                self.assert_code(
                    "CHAT_MEMORY_BLOCK_INVALID", build_prompt, self.sources, content
                )
                self.assert_code(
                    "CHAT_MEMORY_BLOCK_INVALID", self.proposal, [], content=content
                )
        self.save(variants[2])
        before = self.current()
        self.assert_code("CHAT_MEMORY_BLOCK_INVALID", self.proposal)
        self.assertEqual(self.current(), before)

    def test_manual_edits_between_extraction_and_apply_conflict_without_overwrite(self):
        proposal = self.proposal()
        self.save("later manual update")
        self.assert_code(
            "CHAT_CONTEXT_CHANGED", apply_proposal, self.context, A, 0, 0, proposal
        )
        self.assert_code(
            "CHAT_MEMORY_CHANGED", apply_proposal, self.context, A, 1, 1, proposal
        )
        self.assert_code(
            "CHAT_PROFILE_REVISION_CONFLICT",
            apply_proposal,
            self.context,
            A,
            1,
            0,
            proposal,
        )
        self.assertEqual(self.current()[1]["content"], "later manual update")

    def test_apply_final_manifest_cas_closes_race_after_read(self):
        proposal = self.proposal()
        original = self.context.update_profile

        def racing(principal, name, context_revision, expected_revision, content):
            original(A, "MEMORY.md", 0, 0, "concurrent manual")
            return original(
                principal, name, context_revision, expected_revision, content
            )

        with patch.object(self.context, "update_profile", side_effect=racing) as called:
            self.assert_code(
                "CHAT_CONTEXT_CHANGED", apply_proposal, self.context, A, 0, 0, proposal
            )
        self.assertEqual(called.call_count, 1)
        self.assertEqual(self.current()[1]["content"], "concurrent manual")

    def test_undo_rejects_later_manual_edits_and_edit_then_revert_aba(self):
        audit = self.apply()
        self.save("later manual")
        self.assert_code(
            "CHAT_MEMORY_UNDO_CONFLICT", undo_update, self.context, A, audit, 2
        )
        self.save(audit["after"]["content"])
        self.assert_code(
            "CHAT_MEMORY_UNDO_CONFLICT", undo_update, self.context, A, audit, 3
        )
        self.assertEqual(self.current()[1]["content"], audit["after"]["content"])

    def test_undo_rejects_stale_context_and_closes_final_manifest_race(self):
        audit = self.apply()
        self.assert_code("CHAT_CONTEXT_CHANGED", undo_update, self.context, A, audit, 0)
        original = self.context.update_profile

        def racing(principal, name, context_revision, expected_revision, content):
            original(A, "MEMORY.md", 1, 1, "manual wins over undo")
            return original(
                principal, name, context_revision, expected_revision, content
            )

        with patch.object(self.context, "update_profile", side_effect=racing):
            self.assert_code(
                "CHAT_CONTEXT_CHANGED", undo_update, self.context, A, audit, 1
            )
        self.assertEqual(self.current()[1]["content"], "manual wins over undo")

    def test_undo_allows_unrelated_settings_change_only_with_fresh_context(self):
        audit = self.apply()
        self.save("本人风格", name="SOUL.md")
        self.assert_code("CHAT_CONTEXT_CHANGED", undo_update, self.context, A, audit, 1)
        result = undo_update(self.context, A, audit, 2)
        self.assertEqual(result["profile"]["content"], "")
        self.assertEqual(
            self.context.read_context(A)["profiles"][1]["content"], "本人风格"
        )

    def test_undo_rejects_forged_or_corrupt_audit_and_never_restores_manual_diff(self):
        audit = self.apply()
        variants = []
        for key, value in (
            ("v", 2),
            ("owner_user_id", B.user_id),
            ("profile", "SOUL.md"),
            ("changed", False),
        ):
            corrupt = copy.deepcopy(audit)
            corrupt[key] = value
            variants.append(corrupt)
        corrupt = copy.deepcopy(audit)
        corrupt["before"]["sha256"] = "0" * 64
        variants.append(corrupt)
        corrupt = copy.deepcopy(audit)
        corrupt["before"]["content"] = "forged manual overwrite"
        corrupt["before"]["sha256"] = digest(corrupt["before"]["content"])
        variants.append(corrupt)
        variants.append({**audit, "unknown": "field"})
        for corrupt in variants:
            with self.subTest(corrupt=corrupt):
                self.assert_code(
                    "CHAT_MEMORY_UNDO_CONFLICT",
                    undo_update,
                    self.context,
                    A,
                    corrupt,
                    1,
                )
        self.assertEqual(self.current()[1]["content"], audit["after"]["content"])

    def test_public_validate_audit_is_pure_and_enforces_recovery_whitelist(self):
        audit = self.apply()
        with patch.object(
            self.context, "read_context", side_effect=AssertionError("pure validation")
        ):
            self.assertIsNone(validate_audit(audit, A))
        self.assert_code("CHAT_MEMORY_UNDO_CONFLICT", validate_audit, audit, B)
        for key in ("source_ids", "fact_ids"):
            corrupt = copy.deepcopy(audit)
            corrupt[key] = []
            self.assert_code("CHAT_MEMORY_UNDO_CONFLICT", validate_audit, corrupt, A)
        corrupt = copy.deepcopy(audit)
        corrupt["before"]["memory_revision"] = 9
        self.assert_code("CHAT_MEMORY_UNDO_CONFLICT", validate_audit, corrupt, A)

    def test_empty_proposal_is_noop_but_still_calls_existing_cas(self):
        self.save("手写内容")
        proposal = self.proposal([])
        with patch.object(
            self.context, "update_profile", wraps=self.context.update_profile
        ) as updated:
            audit = self.apply(proposal)
        updated.assert_called_once_with(A, "MEMORY.md", 1, 1, "手写内容")
        self.assertFalse(audit["changed"])
        self.assertEqual(audit["before"], audit["after"])
        self.assertEqual(audit["source_ids"], [])
        self.assertEqual(audit["fact_ids"], [])
        result = undo_update(self.context, A, audit, 1)
        self.assertFalse(result["changed"])
        self.assertEqual(result["context_revision"], 1)
        self.assertEqual(self.proposal([], sources=[]).operations, ())

    def test_operation_count_and_utf8_byte_limits(self):
        source = {
            "message_id": MID,
            "text": " ".join(f"durable preference {i}" for i in range(9)),
        }
        self.assert_code(
            "CHAT_MEMORY_PROPOSAL_INVALID",
            self.proposal,
            [add(f"durable preference {i}") for i in range(9)],
            sources=[source],
        )
        proposal = self.proposal(
            [add(f"durable preference {i}") for i in range(8)], sources=[source]
        )
        self.assertEqual(len(proposal.operations), 8)
        for text in ("字" * 100, "x" * 300):
            self.assertGreater(len(canonical_json(add(text))), 512)
            self.assert_code(
                "CHAT_MEMORY_PROPOSAL_INVALID",
                self.proposal,
                [add(text)],
                sources=[{"message_id": MID, "text": text}],
            )

    def test_total_memory_exact_8k_boundary_and_no_space_overflow(self):
        proposal = self.proposal(content="")
        overhead = len(prepare_update("", proposal)["after"].encode())
        manual = "m" * (8192 - overhead)
        self.save(manual)
        exact = self.apply(self.proposal())
        self.assertEqual(len(exact["after"]["content"].encode()), 8192)
        result = undo_update(self.context, A, exact, exact["after"]["context_revision"])
        self.assertEqual(result["profile"]["content"], manual)
        self.save(manual + "m")
        self.assert_code("CHAT_PROFILE_TOO_LARGE", self.proposal)
        self.assertEqual(self.current()[1]["content"], manual + "m")
        self.assert_code("CHAT_PROFILE_TOO_LARGE", build_prompt, [], "字" * 2731)
        self.save("m" * 8192)
        noop = self.apply(self.proposal([]))
        self.assertFalse(noop["changed"])

    def test_source_and_response_budgets_control_characters_and_invalid_unicode(self):
        oversized = [{"message_id": MID, "text": "x" * 32769}]
        self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", build_prompt, oversized, "")
        many = [{"message_id": f"message-{i}", "text": FACT} for i in range(65)]
        self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", build_prompt, many, "")
        total = [{"message_id": f"message-{i}", "text": "x" * 22000} for i in range(3)]
        self.assert_code("CHAT_MEMORY_PROPOSAL_INVALID", build_prompt, total, "")
        for text in ("contains\x00control", "bad\ud800unicode"):
            self.assert_code(
                "CHAT_MEMORY_PROPOSAL_INVALID",
                build_prompt,
                [{"message_id": MID, "text": text}],
                "",
            )
        self.assert_code(
            "CHAT_MEMORY_PROPOSAL_INVALID", parse_proposal, " " * 8193, self.sources, ""
        )

    def test_revision_types_are_strict_and_storage_errors_propagate(self):
        proposal = self.proposal()
        for rev in (True, -1, "0", None):
            self.assert_code(
                "CHAT_MEMORY_PROPOSAL_INVALID",
                apply_proposal,
                self.context,
                A,
                rev,
                0,
                proposal,
            )
        error = ChatError("storage unavailable", 503, "CHAT_STORAGE_UNAVAILABLE")
        with patch.object(self.context, "update_profile", side_effect=error):
            with self.assertRaises(ChatError) as caught:
                self.apply(proposal)
        self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
