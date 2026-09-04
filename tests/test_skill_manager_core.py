from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import skill_manager
from tools.skill_manager_core import CandidateSource, SkillManager
from tools.skill_manager_core import service as manager_service


class SkillManagerCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name)
        (self.repository / "sk-authoring").mkdir()
        (self.repository / "sk-authoring" / "SKILL.md").write_text(
            "---\nname: authoring\ndescription: Development candidate.\n---\n",
            encoding="utf-8",
        )
        (self.repository / "skills" / "released").mkdir(parents=True)
        (self.repository / "skills" / "released" / "SKILL.md").write_text(
            "---\nname: released\ndescription: Stable candidate.\n---\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_scan_plan_apply_reports_development_and_stable_candidates(self) -> None:
        manager = SkillManager(self.repository)

        scan = manager.scan()
        report = manager.apply(manager.plan("list", scan))

        self.assertEqual(
            [(candidate.source, candidate.name) for candidate in scan.candidates],
            [
                (CandidateSource.DEVELOPMENT, "sk-authoring"),
                (CandidateSource.STABLE, "released"),
            ],
        )
        self.assertEqual(report.command, "list")
        self.assertFalse(report.changed)
        self.assertEqual(
            [(result.source, result.skill) for result in report.results],
            [("development", "sk-authoring"), ("stable", "released")],
        )

    def test_list_json_uses_the_common_versioned_report_envelope(self) -> None:
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["list", "--json"],
            repository_root=self.repository,
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "list")
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["status"], "success")
        self.assertFalse(payload["changed"])
        self.assertEqual(
            [(item["source"], item["skill"]) for item in payload["results"]],
            [("development", "sk-authoring"), ("stable", "released")],
        )

    def test_human_list_report_identifies_the_command_and_diagnostics(self) -> None:
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["list"],
            repository_root=self.repository,
            stdout=stdout,
        )

        report = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Command: list", report)
        self.assertIn("Status: success", report)
        self.assertIn("Diagnostics: none", report)
        self.assertIn("development sk-authoring", report)

    def test_direct_script_execution_runs_the_list_command(self) -> None:
        script = Path(skill_manager.__file__).resolve()

        completed = subprocess.run(
            [sys.executable, str(script), "list", "--json"],
            check=False,
            capture_output=True,
            cwd=self.repository,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["command"], "list")

    def test_unprefixed_development_symlink_is_not_followed_during_discovery(self) -> None:
        target = self.repository / "target"
        target.mkdir()
        (target / "SKILL.md").write_text("content", encoding="utf-8")
        (self.repository / "linked-development").symlink_to(target, target_is_directory=True)

        scan = SkillManager(self.repository).scan()

        self.assertNotIn("linked-development", [candidate.name for candidate in scan.candidates])

    def test_unprefixed_directory_with_broken_metadata_link_remains_a_candidate(self) -> None:
        attempted = self.repository / "legacy-development"
        attempted.mkdir()
        (attempted / "SKILL.md").symlink_to("missing-skill.md")

        scan = SkillManager(self.repository).scan()

        self.assertIn("legacy-development", [candidate.name for candidate in scan.candidates])

    def test_unprefixed_directory_with_symlinked_agents_remains_a_candidate(self) -> None:
        attempted = self.repository / "legacy-agents-development"
        attempted.mkdir()
        external_agents = self.repository / "external-agents"
        external_agents.mkdir()
        (external_agents / "openai.yaml").write_text("interface: {}", encoding="utf-8")
        (attempted / "agents").symlink_to(external_agents, target_is_directory=True)

        scan = SkillManager(self.repository).scan()

        self.assertIn("legacy-agents-development", [candidate.name for candidate in scan.candidates])

    def test_symlinked_stable_namespace_is_reported_without_scanning_external_entries(self) -> None:
        stable_namespace = self.repository / "skills"
        released = stable_namespace / "released"
        (released / "SKILL.md").unlink()
        released.rmdir()
        stable_namespace.rmdir()
        external_stable = self.repository / "external-stable"
        external_stable.mkdir()
        (external_stable / "outside-skill").mkdir()
        stable_namespace.symlink_to(external_stable, target_is_directory=True)

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("list", SkillManager(self.repository).scan())
        )

        self.assertEqual(report.status, "failed")
        self.assertEqual([diagnostic.code for diagnostic in report.diagnostics], ["SYMLINKED_STABLE_NAMESPACE"])
        self.assertNotIn("outside-skill", [result.skill for result in report.results])

    def test_missing_repository_uses_a_failed_versioned_diagnostic_report(self) -> None:
        missing_repository = self.repository / "missing"
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["list", "--json"],
            repository_root=missing_repository,
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["diagnostics"][0]["code"], "REPOSITORY_NOT_FOUND")

    def test_development_discovery_io_failure_uses_a_failed_diagnostic_report(self) -> None:
        manager = SkillManager(self.repository)
        original_iterdir = Path.iterdir

        def denied_repository_entries(path: Path):
            if path == self.repository:
                raise OSError("permission denied")
            return original_iterdir(path)

        with patch.object(
            Path,
            "iterdir",
            autospec=True,
            side_effect=denied_repository_entries,
        ):
            report = manager.apply(manager.plan("list", manager.scan()))

        self.assertEqual(report.status, "failed")
        self.assertEqual([diagnostic.code for diagnostic in report.diagnostics], ["DISCOVERY_IO_ERROR"])

    def test_status_reports_identity_and_promoted_clean_lifecycle(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        stable_skill = stable / "SKILL.md"
        stable_skill.write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "empty").mkdir()
        (stable / "empty").mkdir()
        (development / "__pycache__").mkdir()
        (development / "__pycache__" / "ignored.pyc").write_bytes(b"development")
        (stable / "__pycache__").mkdir()
        (stable / "__pycache__" / "ignored.pyc").write_bytes(b"stable")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        authoring = next(result for result in report.results if result.skill == "authoring")
        self.assertEqual(authoring.outcome, "promoted-clean")
        self.assertEqual(authoring.details["validation_state"], "not-run")
        self.assertEqual(
            authoring.details["development_identity"],
            authoring.details["stable_identity"],
        )

    def test_status_reports_modified_when_managed_bytes_differ(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text("stable differs", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        authoring = next(result for result in report.results if result.skill == "authoring")
        self.assertEqual(authoring.outcome, "modified")

    def test_status_reports_unpublished_and_stable_lifecycle_states(self) -> None:
        manager = SkillManager(self.repository)

        report = manager.apply(manager.plan("status", manager.scan()))

        outcomes = {result.skill: result.outcome for result in report.results}
        self.assertEqual(outcomes["authoring"], "unpublished")
        self.assertEqual(outcomes["released"], "stable")

    def test_status_json_reports_lifecycle_details(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["status", "--json"],
            repository_root=self.repository,
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        authoring = next(item for item in payload["results"] if item["skill"] == "authoring")
        self.assertEqual(exit_code, 0)
        self.assertEqual(authoring["outcome"], "promoted-clean")
        self.assertEqual(authoring["details"]["validation_state"], "not-run")

    def test_diff_reports_added_removed_and_modified_managed_files(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "shared.md").write_text("development", encoding="utf-8")
        (development / "added.md").write_text("added", encoding="utf-8")
        (stable / "shared.md").write_text("stable", encoding="utf-8")
        (stable / "removed.md").write_text("removed", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("diff", manager.scan(), skill="authoring"))

        self.assertEqual(report.status, "success")
        self.assertEqual(
            [(result.path, result.outcome) for result in report.results],
            [
                ("added.md", "added"),
                ("removed.md", "removed"),
                ("shared.md", "modified"),
            ],
        )
        changes = {result.path: result for result in report.results}
        self.assertEqual(changes["added.md"].details["content_kind"], "text")
        self.assertIn("+added", changes["added.md"].details["unified_diff"])

    def test_diff_reports_executable_and_binary_changes_without_binary_content(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "run").write_text("same", encoding="utf-8")
        (stable / "run").write_text("same", encoding="utf-8")
        (development / "run").chmod(0o755)
        (development / "data.bin").write_bytes(b"\x00development")
        (stable / "data.bin").write_bytes(b"\x00stable")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("diff", manager.scan(), skill="authoring"))

        changes = {result.path: result for result in report.results}
        self.assertEqual(changes["run"].outcome, "executable-changed")
        self.assertEqual(changes["data.bin"].details["content_kind"], "binary")
        self.assertNotIn("development", changes["data.bin"].details.get("unified_diff", ""))

    def test_status_reports_identity_error_for_symbolic_link_content(self) -> None:
        development = self.repository / "sk-authoring"
        external = self.repository / "external-content"
        external.write_text("external", encoding="utf-8")
        (development / "linked.md").symlink_to(external)

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        self.assertEqual(report.status, "failed")
        self.assertIn(
            "PACKAGE_IDENTITY_ERROR",
            [diagnostic.code for diagnostic in report.diagnostics],
        )

    def test_status_reports_identity_error_for_nfc_path_collision(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "é.md").write_text("composed", encoding="utf-8")
        (development / "é.md").write_text("decomposed", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        self.assertEqual(report.status, "failed")
        self.assertIn(
            "PACKAGE_IDENTITY_ERROR",
            [diagnostic.code for diagnostic in report.diagnostics],
        )

    def test_status_rejects_non_nfc_managed_path(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "é.md").write_text("decomposed", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        self.assertEqual(report.status, "failed")
        self.assertIn(
            "PACKAGE_IDENTITY_ERROR",
            [diagnostic.code for diagnostic in report.diagnostics],
        )

    def test_diff_limits_text_hunks_unless_full_is_requested(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "changed.md").write_text("a\nb\nc\nd\n", encoding="utf-8")
        (stable / "changed.md").write_text("w\nx\ny\nz\n", encoding="utf-8")
        manager = SkillManager(self.repository)
        scan = manager.scan()

        with patch.object(manager_service, "TEXT_DIFF_MAX_LINES", 2):
            limited = manager.apply(manager.plan("diff", scan, skill="authoring"))
            full = manager.apply(
                manager.plan("diff", scan, skill="authoring", full_diff=True)
            )

        limited_change = next(result for result in limited.results if result.path == "changed.md")
        full_change = next(result for result in full.results if result.path == "changed.md")
        self.assertEqual(limited_change.details["diff_truncated"], "true")
        self.assertEqual(full_change.details["diff_truncated"], "false")
        self.assertLess(
            len(limited_change.details["unified_diff"].splitlines()),
            len(full_change.details["unified_diff"].splitlines()),
        )

    def test_status_keeps_invalid_name_candidate_visible_with_a_diagnostic(self) -> None:
        invalid = self.repository / "skills" / "bad--name"
        invalid.mkdir()

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        self.assertEqual(report.status, "failed")
        self.assertIn(
            ("stable", "bad--name", "invalid-candidate"),
            [(result.source, result.skill, result.outcome) for result in report.results],
        )
        self.assertIn("INVALID_SKILL_NAME", [diagnostic.code for diagnostic in report.diagnostics])

    def test_status_does_not_report_clean_when_both_identities_fail(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        external = self.repository / "external"
        external.write_text("external", encoding="utf-8")
        (development / "linked").symlink_to(external)
        (stable / "linked").symlink_to(external)

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        authoring = next(result for result in report.results if result.skill == "authoring")
        self.assertEqual(authoring.outcome, "unknown")

    def test_diff_reports_content_and_executable_changes_together(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "run").write_text("development", encoding="utf-8")
        (stable / "run").write_text("stable", encoding="utf-8")
        (development / "run").chmod(0o755)

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("diff", manager.scan(), skill="authoring"))

        change = next(result for result in report.results if result.path == "run")
        self.assertEqual(change.outcome, "modified")
        self.assertEqual(change.details["executable_changed"], "true")

    def test_status_rejects_file_form_version_control_metadata(self) -> None:
        development = self.repository / "sk-authoring"
        (development / ".git").write_text("gitdir: elsewhere", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("status", manager.scan()))

        self.assertEqual(report.status, "failed")
        self.assertIn("PACKAGE_IDENTITY_ERROR", [diagnostic.code for diagnostic in report.diagnostics])

    def test_human_diff_renders_change_kind_and_text_hunk(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "changed.md").write_text("development\n", encoding="utf-8")
        (stable / "changed.md").write_text("stable\n", encoding="utf-8")
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["diff", "authoring"],
            repository_root=self.repository,
            stdout=stdout,
        )

        rendered = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("changed.md: modified", rendered)
        self.assertIn("--- stable", rendered)

    def test_diff_reports_file_type_changes_without_following_symlinks(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        external = self.repository / "external"
        external.write_text("external", encoding="utf-8")
        (development / "changed").symlink_to(external)
        (stable / "changed").write_text("regular", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("diff", manager.scan(), skill="authoring"))

        change = next(result for result in report.results if result.path == "changed")
        self.assertEqual(change.outcome, "file-type-changed")
        self.assertEqual(change.details["development_type"], "symlink")
        self.assertEqual(change.details["stable_type"], "regular")

    def test_human_diff_renders_both_file_types_for_type_changes(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        external = self.repository / "external"
        external.write_text("external", encoding="utf-8")
        (development / "changed").symlink_to(external)
        (stable / "changed").write_text("regular", encoding="utf-8")
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["diff", "authoring"],
            repository_root=self.repository,
            stdout=stdout,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("file type: stable=regular development=symlink", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
