from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import skill_manager
from tools.skill_manager_core import SkillManager
from tools.skill_manager_core import release as release_module
from tools.skill_manager_core.identity import package_identity
from tools.skill_manager_core.journal import append_event, write_plan


class ReleaseBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name)
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir(parents=True)
        (development / "SKILL.md").write_text(
            "---\nname: authoring\ndescription: Authoring helper.\n---\n",
            encoding="utf-8",
        )
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Authoring helper\n",
            encoding="utf-8",
        )
        (self.repository / "skills").mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.com")
        self._git("add", "--", "sk-authoring")
        self._git("commit", "-qm", "initial")
        manager = SkillManager(self.repository)
        promoted = manager.apply(
            manager.plan(
                "promote",
                manager.scan(),
                skills=("authoring",),
            )
        )
        self.assertEqual(promoted.status, "success")
        self.original_head = self._git("rev-parse", "HEAD")
        self.message_file = self.repository / "message.txt"
        self.message_file.write_text("release authoring", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def _plan(self, *, dry_run: bool = False):
        manager = SkillManager(self.repository)
        return manager, manager.plan(
            "record-release",
            manager.scan(),
            skills=("authoring",),
            dry_run=dry_run,
            commit_message_files=(f"authoring={self.message_file}",),
        )

    def test_record_release_backfills_promoted_stable_with_scoped_commit_and_tag(self) -> None:
        manager, plan = self._plan(dry_run=True)
        preview = manager.apply(plan)

        self.assertEqual(preview.status, "success")
        self.assertEqual(preview.results[0].outcome, "would-record-release")
        self.assertRegex(str(preview.results[0].details["release_tag"]), r"^authoring-\d{6}-v1$")
        self.assertEqual(self._git("rev-parse", "HEAD"), self.original_head)
        self.assertEqual(self._git("tag", "--list"), "")

        manager, plan = self._plan()
        report = manager.apply(plan)

        self.assertEqual(report.status, "success", report.diagnostics)
        self.assertTrue(report.changed)
        result = report.results[0]
        self.assertEqual(result.outcome, "release-recorded")
        self.assertEqual(result.details["recording_mode"], "backfill")
        self.assertNotEqual(self._git("rev-parse", "HEAD"), self.original_head)
        self.assertEqual(
            self._git("show", "--format=", "--name-only", "HEAD").splitlines(),
            [
                "skills/authoring/SKILL.md",
                "skills/authoring/agents/openai.yaml",
            ],
        )
        tag = str(result.details["release_tag"])
        self.assertEqual(self._git("tag", "--points-at", "HEAD"), tag)
        annotation = self._git("for-each-ref", f"refs/tags/{tag}", "--format=%(contents)")
        self.assertIn("Recording-Mode: backfill", annotation)
        self.assertIn("Promotion-Transaction:", annotation)

        manager, plan = self._plan()
        repeated = manager.apply(plan)
        self.assertEqual(repeated.status, "no-change")
        self.assertEqual(repeated.results[0].outcome, "no-change")
        self.assertEqual(repeated.results[0].details["release_tag"], tag)

    def test_record_release_refuses_stable_identity_that_no_longer_matches_receipt(self) -> None:
        stable_skill = self.repository / "skills" / "authoring" / "SKILL.md"
        stable_skill.write_text(
            "---\nname: authoring\ndescription: Drifted helper.\n---\n",
            encoding="utf-8",
        )

        manager, plan = self._plan()
        report = manager.apply(plan)

        self.assertEqual(report.status, "failed")
        self.assertIn("PROMOTION_RECEIPT_MISMATCH", [item.code for item in report.diagnostics])
        self.assertEqual(self._git("rev-parse", "HEAD"), self.original_head)
        self.assertEqual(self._git("tag", "--list"), "")

    def test_record_release_refuses_empty_backfill_commit(self) -> None:
        self._git("add", "--", "skills/authoring")
        self._git("commit", "-qm", "track stable", "--", "skills/authoring")
        tracked_head = self._git("rev-parse", "HEAD")

        manager, plan = self._plan()
        report = manager.apply(plan)

        self.assertEqual(report.status, "failed")
        self.assertIn(
            "RELEASE_BACKFILL_NO_GIT_CHANGES",
            [item.code for item in report.diagnostics],
        )
        self.assertEqual(self._git("rev-parse", "HEAD"), tracked_head)
        self.assertEqual(self._git("tag", "--list"), "")

    def test_record_release_refuses_dirty_index_without_mutating_refs(self) -> None:
        unrelated = self.repository / "unrelated.txt"
        unrelated.write_text("staged", encoding="utf-8")
        self._git("add", "--", "unrelated.txt")

        manager, plan = self._plan()
        report = manager.apply(plan)

        self.assertEqual(report.status, "failed")
        self.assertIn("GIT_INDEX_DIRTY", [item.code for item in report.diagnostics])
        self.assertEqual(self._git("rev-parse", "HEAD"), self.original_head)
        self.assertEqual(self._git("tag", "--list"), "")

    def test_record_release_cli_requires_message_and_supports_json_preview(self) -> None:
        with self.assertRaises(SystemExit):
            skill_manager.main(
                ["record-release", "authoring", "--dry-run"],
                repository_root=self.repository,
                stdout=io.StringIO(),
            )

        output = io.StringIO()
        exit_code = skill_manager.main(
            [
                "record-release",
                "authoring",
                "--commit-message-file",
                f"authoring={self.message_file}",
                "--dry-run",
                "--json",
            ],
            repository_root=self.repository,
            stdout=output,
        )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["command"], "record-release")
        self.assertEqual(payload["results"][0]["outcome"], "would-record-release")

    def test_recover_rolls_back_commit_and_tag_after_backfill_interruption(self) -> None:
        self._git("add", "--", "skills/authoring")
        self._git("commit", "-qm", "release authoring", "--", "skills/authoring")
        transaction_id = "backfill-gap"
        tag = "authoring-260908-v1"
        self._git(
            "tag",
            "-a",
            tag,
            "-m",
            f"Release authoring\n\nAtomic-Batch: {transaction_id}",
        )
        branch = self._git("symbolic-ref", "--short", "HEAD")
        transaction = (
            self.repository
            / ".skill-manager"
            / "transactions"
            / transaction_id
        )
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": transaction_id,
                "operation_type": "record-release",
                "release_instant": "2026-09-08T00:00:00Z",
                "skills": [
                    {
                        "skill": "authoring",
                        "package_identity": package_identity(
                            self.repository / "skills" / "authoring"
                        ),
                        "promotion_transaction_id": "promotion-id",
                        "promoted_at": "2026-09-07T00:00:00Z",
                    }
                ],
                "original_head": self.original_head,
                "original_branch": branch,
                "release_tags": {"authoring": tag},
                "commit_messages": {},
            },
        )
        events = transaction / "events.jsonl"
        append_event(events, plan_hash, "APPLYING")
        append_event(
            events,
            plan_hash,
            "PENDING",
            {
                "kind": "release",
                "skill": "authoring",
                "expected_parent": self.original_head,
                "tag": tag,
            },
        )

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success", report.diagnostics)
        self.assertEqual(report.results[0].outcome, "rolled-back")
        self.assertEqual(self._git("rev-parse", "HEAD"), self.original_head)
        self.assertEqual(self._git("tag", "--list"), "")
        self.assertTrue((self.repository / "skills" / "authoring").is_dir())

    def test_git_rollback_does_not_write_refs_when_head_and_index_are_unchanged(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run_git(arguments, repository_root, *, check=False, text=False):
            del repository_root, check, text
            calls.append(tuple(arguments))
            if arguments[0] == "for-each-ref":
                return subprocess.CompletedProcess(arguments, 0, "", "")
            if arguments[:2] == ["rev-parse", "refs/heads/dev"]:
                return subprocess.CompletedProcess(arguments, 0, "original\n", "")
            if arguments[:3] == ["diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(arguments, 0, b"", b"")
            raise AssertionError(f"unexpected mutating Git call: {arguments}")

        with patch.object(release_module, "_run_git", side_effect=fake_run_git):
            release_module.rollback_git_release(
                self.repository,
                "dev",
                "original",
                ("authoring-260908-v1",),
                "transaction-id",
                (),
            )

        self.assertNotIn("update-ref", [arguments[0] for arguments in calls])
        self.assertNotIn("reset", [arguments[0] for arguments in calls])


if __name__ == "__main__":
    unittest.main()
