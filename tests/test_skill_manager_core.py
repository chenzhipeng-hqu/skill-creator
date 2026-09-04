from __future__ import annotations

import io
import json
from datetime import datetime
import subprocess
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import skill_manager
from tools.skill_manager_core import CandidateSource, SkillManager
from tools.skill_manager_core import service as manager_service
from tools.skill_manager_core.journal import append_event, write_plan
from tools.skill_manager_core.targets import AgentTarget, hermes_target, is_exact_managed_link, opencode_discovery_roots
from tools.skill_manager_core.provenance import stable_provenance
from tools.skill_manager_core.identity import package_identity
from tools.skill_manager_core.release import release_tag_name
from tools.skill_manager_core.tui import TuiState, advance


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
        (self.repository / "skills" / "released" / "agents").mkdir()
        (self.repository / "skills" / "released" / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Released\n  short_description: Stable candidate\n",
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

    def test_target_requires_real_config_root_and_exact_absolute_link(self) -> None:
        target = AgentTarget("codex", self.repository / ".codex")
        stable = self.repository / "skills" / "authoring"
        stable.mkdir(exist_ok=True)
        self.assertFalse(target.detected())
        target.config_root.mkdir()
        target.skills_root.mkdir()
        destination = target.skills_root / "authoring"
        destination.symlink_to(stable)

        self.assertTrue(target.detected())
        self.assertTrue(is_exact_managed_link(destination, stable))

    def test_target_install_and_uninstall_preserve_conflicts(self) -> None:
        target = AgentTarget("codex", self.repository / ".codex")
        target.config_root.mkdir()
        stable = self.repository / "skills" / "authoring"
        stable.mkdir(exist_ok=True)

        self.assertEqual(target.install(stable), "installed")
        self.assertEqual(target.install(stable), "no-change")
        self.assertEqual(target.uninstall(stable), "uninstalled")
        target.skills_root.mkdir(exist_ok=True)
        conflict = target.skills_root / "authoring"
        conflict.mkdir()
        self.assertEqual(target.install(stable), "conflict")
        self.assertTrue(conflict.is_dir())

    def test_hermes_and_opencode_targets_use_explicit_local_roots(self) -> None:
        home = self.repository / "home"
        active_hermes = self.repository / "active-hermes"

        self.assertEqual(hermes_target(home, active_hermes).config_root, active_hermes)
        self.assertEqual(
            opencode_discovery_roots(home),
            (home / ".config" / "opencode" / "skills", home / ".agents" / "skills", home / ".claude" / "skills"),
        )

    def test_stable_provenance_classifies_clean_drifted_and_unknown(self) -> None:
        stable = self.repository / "skills" / "authoring"
        stable.mkdir(exist_ok=True)
        (stable / "SKILL.md").write_text("stable", encoding="utf-8")
        state = self.repository / ".skill-manager"
        state.mkdir()
        (state / "state.json").write_text(
            json.dumps({"receipts": {"authoring": {"identity": package_identity(stable)}}}), encoding="utf-8"
        )

        self.assertEqual(stable_provenance(self.repository, "authoring", stable), "clean")
        (stable / "SKILL.md").write_text("drifted", encoding="utf-8")
        self.assertEqual(stable_provenance(self.repository, "authoring", stable), "drifted")
        self.assertEqual(stable_provenance(self.repository, "other", stable), "unknown")

    def test_release_tag_uses_greatest_existing_daily_version(self) -> None:
        instant = datetime(2026, 9, 4)

        self.assertEqual(
            release_tag_name("authoring", instant, {"authoring-260904-v1", "authoring-260904-v3"}),
            "authoring-260904-v4",
        )

    def test_tui_requires_literal_confirmation_and_cancellation_never_applies(self) -> None:
        state = advance(advance(TuiState(), "\n"), "\n")
        self.assertEqual(state.screen, 3)
        self.assertFalse(advance(state, "\n").applied)
        self.assertFalse(advance(state, "y", has_errors=True).applied)
        self.assertTrue(advance(state, "y").applied)
        self.assertTrue(advance(state, "q").cancelled)

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
        (released / "agents" / "openai.yaml").unlink()
        (released / "agents").rmdir()
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
        self.assertEqual(authoring.details["validation_state"], "invalid")
        self.assertEqual(authoring.details["test_state"], "not-run")
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

    def test_status_reports_structural_validation_without_running_tests(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        metadata = development / "agents" / "openai.yaml"
        metadata.write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        manager = SkillManager(self.repository)

        valid_report = manager.apply(manager.plan("status", manager.scan()))
        metadata.unlink()
        invalid_report = manager.apply(manager.plan("status", manager.scan()))

        valid_authoring = next(result for result in valid_report.results if result.skill == "authoring")
        invalid_authoring = next(
            result for result in invalid_report.results if result.skill == "authoring"
        )
        self.assertEqual(valid_authoring.details["validation_state"], "valid")
        self.assertEqual(invalid_authoring.details["validation_state"], "invalid")
        self.assertIn(
            "MISSING_OPENAI_METADATA",
            [diagnostic.code for diagnostic in invalid_report.diagnostics],
        )

    def test_status_json_reports_lifecycle_details(self) -> None:
        development = self.repository / "sk-authoring"
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        (stable / "agents").mkdir()
        (stable / "agents" / "openai.yaml").write_text(
            (development / "agents" / "openai.yaml").read_text(encoding="utf-8"),
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
        self.assertEqual(authoring["details"]["validation_state"], "valid")

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

    def test_validate_selects_development_and_reports_missing_openai_metadata(self) -> None:
        manager = SkillManager(self.repository)

        report = manager.apply(
            manager.plan("validate", manager.scan(), skill="authoring")
        )

        self.assertEqual(report.status, "failed")
        self.assertEqual(
            [diagnostic.code for diagnostic in report.diagnostics],
            ["MISSING_OPENAI_METADATA"],
        )

    def test_validate_stable_selects_only_the_stable_candidate(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir(exist_ok=True)
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text(
            (development / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (stable / "agents").mkdir()
        (stable / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n",
            encoding="utf-8",
        )

        manager = SkillManager(self.repository)
        development_report = manager.apply(
            manager.plan("validate", manager.scan(), skill="authoring")
        )
        stable_report = manager.apply(
            manager.plan(
                "validate",
                manager.scan(),
                skill="authoring",
                validate_stable=True,
            )
        )

        self.assertEqual(development_report.status, "success")
        self.assertEqual(stable_report.status, "failed")
        self.assertIn(
            "INVALID_OPENAI_METADATA",
            [diagnostic.code for diagnostic in stable_report.diagnostics],
        )

    def test_validate_all_reports_each_development_candidate(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir(exist_ok=True)
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        (self.repository / "sk-broken").mkdir()

        manager = SkillManager(self.repository)
        report = manager.apply(
            manager.plan("validate", manager.scan(), validate_all=True)
        )

        self.assertEqual(report.status, "failed")
        self.assertEqual(
            [result.skill for result in report.results],
            ["sk-authoring", "sk-broken"],
        )
        self.assertIn("MISSING_SKILL_MD", [diagnostic.code for diagnostic in report.diagnostics])

    def test_validate_reports_missing_local_markdown_reference(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        (development / "SKILL.md").write_text(
            "---\nname: authoring\ndescription: Development candidate.\n---\n"
            "\n[Missing guide](references/guide.md)\n"
            "\n[External](https://example.com)\n",
            encoding="utf-8",
        )

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        self.assertEqual(report.status, "failed")
        self.assertIn(
            "MISSING_LOCAL_REFERENCE",
            [diagnostic.code for diagnostic in report.diagnostics],
        )

    def test_validate_rejects_known_metadata_type_errors_but_warns_unknown_fields(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n"
            "brand_color: blue\n"
            "future_field: accepted-later\n",
            encoding="utf-8",
        )

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        self.assertEqual(report.status, "failed")
        codes = [diagnostic.code for diagnostic in report.diagnostics]
        self.assertIn("INVALID_OPENAI_METADATA", codes)
        self.assertIn("UNKNOWN_OPENAI_METADATA_FIELD", codes)

    def test_validate_rejects_symlinks_and_executable_script_without_shebang(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        target = self.repository / "outside"
        target.write_text("outside", encoding="utf-8")
        (development / "linked.txt").symlink_to(target)
        script = development / "scripts" / "run"
        script.parent.mkdir()
        script.write_text("echo missing shebang\n", encoding="utf-8")
        script.chmod(0o755)

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        codes = [diagnostic.code for diagnostic in report.diagnostics]
        self.assertIn("SYMBOLIC_LINK_NOT_ALLOWED", codes)
        self.assertIn("INVALID_SCRIPT_SHEBANG", codes)

    def test_validate_requires_test_entrypoint_for_managed_scripts(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        script = development / "scripts" / "check"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        self.assertIn(
            "MISSING_SKILL_TEST_ENTRYPOINT",
            [diagnostic.code for diagnostic in report.diagnostics],
        )

    def test_validate_checks_icon_containment_and_dependency_tool_shape(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n"
            "icon_small: ../outside.png\n"
            "dependencies:\n  tools:\n    - type: unsupported\n      value: x\n"
            "    - type: mcp\n      value: server\n      optional: true\n",
            encoding="utf-8",
        )

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        self.assertIn(
            "INVALID_OPENAI_METADATA",
            [diagnostic.code for diagnostic in report.diagnostics],
        )

    def test_validate_does_not_follow_symlinks_for_local_references_or_icons(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n"
            "icon_small: assets/icon.png\n",
            encoding="utf-8",
        )
        outside = self.repository / "outside"
        outside.mkdir()
        (outside / "guide.md").write_text("outside", encoding="utf-8")
        (outside / "icon.png").write_bytes(b"outside")
        (development / "assets").symlink_to(outside, target_is_directory=True)
        (development / "SKILL.md").write_text(
            "---\nname: authoring\ndescription: Development candidate.\n---\n"
            "\n[Guide](assets/guide.md)\n",
            encoding="utf-8",
        )

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        codes = [diagnostic.code for diagnostic in report.diagnostics]
        self.assertIn("SYMBOLIC_LINK_NOT_ALLOWED", codes)
        self.assertIn("INVALID_OPENAI_METADATA", codes)
        self.assertIn("MISSING_LOCAL_REFERENCE", codes)

    def test_validate_rejects_version_control_metadata_and_bad_dependency_field_types(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n"
            "dependencies:\n  tools:\n    - type: mcp\n      value: server\n"
            "      description: 1\n",
            encoding="utf-8",
        )
        (development / ".git").mkdir()

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("validate", manager.scan(), skill="authoring"))

        codes = [diagnostic.code for diagnostic in report.diagnostics]
        self.assertIn("VERSION_CONTROL_METADATA_NOT_ALLOWED", codes)
        self.assertIn("INVALID_OPENAI_METADATA", codes)

    def test_validate_reports_invalid_frontmatter_instead_of_raising(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        (development / "SKILL.md").write_text(
            "---\nname: [authoring\ndescription: broken\n---\n",
            encoding="utf-8",
        )

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan(
                "validate", SkillManager(self.repository).scan(), skill="authoring"
            )
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("INVALID_SKILL_MD", [diagnostic.code for diagnostic in report.diagnostics])

    def test_validate_named_invalid_candidate_is_reported_instead_of_skipped(self) -> None:
        invalid = self.repository / "skills" / "bad--name"
        invalid.mkdir()

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan(
                "validate",
                SkillManager(self.repository).scan(),
                skill="bad--name",
                validate_stable=True,
            )
        )

        self.assertEqual(report.status, "failed")
        self.assertEqual([(result.skill, result.outcome) for result in report.results], [("bad--name", "invalid")])
        self.assertIn("INVALID_SKILL_NAME", [diagnostic.code for diagnostic in report.diagnostics])

    def test_validate_rejects_non_nfc_managed_path(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        (development / "é.md").write_text("decomposed", encoding="utf-8")

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan(
                "validate", SkillManager(self.repository).scan(), skill="authoring"
            )
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("INVALID_MANAGED_PATH", [diagnostic.code for diagnostic in report.diagnostics])

    def test_validate_rejects_icon_that_names_the_skill_directory(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n"
            "icon_small: .\n",
            encoding="utf-8",
        )

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan(
                "validate", SkillManager(self.repository).scan(), skill="authoring"
            )
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("INVALID_OPENAI_METADATA", [diagnostic.code for diagnostic in report.diagnostics])

    def test_validate_runs_skill_test_entrypoint_and_records_private_logs(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        script = development / "scripts" / "tool"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        entrypoint = self.repository / "tests" / "authoring" / "run"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("#!/bin/sh\nprintf '%s' \"$SKILL_SOURCE_KIND:$SKILL_NAME\"\n", encoding="utf-8")
        entrypoint.chmod(0o755)

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan(
                "validate", SkillManager(self.repository).scan(), skill="authoring"
            )
        )

        self.assertEqual(report.status, "success")
        result = report.results[0]
        self.assertEqual(result.details["test_state"], "passed")
        self.assertIn("development:authoring", result.details["stdout_tail"])
        self.assertTrue(Path(result.details["stdout_path"]).is_file())
        self.assertEqual(stat.S_IMODE(Path(result.details["stdout_path"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(result.details["stdout_path"]).parent.stat().st_mode), 0o700)

    def test_validate_rejects_nonzero_test_entrypoint_and_managed_mutation(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n", encoding="utf-8"
        )
        script = development / "scripts" / "tool"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        entrypoint = self.repository / "tests" / "authoring" / "run"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("#!/bin/sh\nprintf changed > \"$SKILL_UNDER_TEST/changed.txt\"\nexit 4\n", encoding="utf-8")
        entrypoint.chmod(0o755)

        failed = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("validate", SkillManager(self.repository).scan(), skill="authoring")
        )
        (development / "changed.txt").unlink()
        entrypoint.write_text("#!/bin/sh\nprintf changed > \"$SKILL_UNDER_TEST/changed.txt\"\n", encoding="utf-8")
        mutated = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("validate", SkillManager(self.repository).scan(), skill="authoring")
        )

        self.assertIn("SKILL_TEST_FAILED", [item.code for item in failed.diagnostics])
        self.assertIn("SKILL_TEST_MUTATED_CONTENT", [item.code for item in failed.diagnostics])
        self.assertIn("SKILL_TEST_MUTATED_CONTENT", [item.code for item in mutated.diagnostics])

    def test_validate_times_out_test_process_group(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n", encoding="utf-8"
        )
        script = development / "scripts" / "tool"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        entrypoint = self.repository / "tests" / "authoring" / "run"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("#!/bin/sh\nsleep 1\n", encoding="utf-8")
        entrypoint.chmod(0o755)

        with patch("tools.skill_manager_core.test_runner.TEST_TIMEOUT_SECONDS", 0.01):
            report = SkillManager(self.repository).apply(
                SkillManager(self.repository).plan("validate", SkillManager(self.repository).scan(), skill="authoring")
            )

        self.assertIn("SKILL_TEST_TIMEOUT", [item.code for item in report.diagnostics])

    def test_promote_copies_valid_development_to_stable_and_records_receipt(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n", encoding="utf-8"
        )
        (development / "guide.md").write_text("development", encoding="utf-8")

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("promote", SkillManager(self.repository).scan(), skill="authoring")
        )

        self.assertEqual(report.status, "success")
        self.assertTrue(report.changed)
        self.assertIsNotNone(report.transaction_id)
        self.assertEqual((self.repository / "skills" / "authoring" / "guide.md").read_text(), "development")
        self.assertTrue((self.repository / ".skill-manager" / "state.json").is_file())

    def test_recover_removes_completed_transaction_directory(self) -> None:
        transaction = self.repository / ".skill-manager" / "transactions" / "test-id"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(transaction / "plan.json", {"journal_schema_version": 1, "transaction_id": "test-id", "skill": "authoring"})
        append_event(transaction / "events.jsonl", plan_hash, "COMMITTED")

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("recover", SkillManager(self.repository).scan())
        )

        self.assertEqual(report.status, "success")
        self.assertFalse(transaction.exists())

    def test_promote_dry_run_does_not_create_manager_state_or_stable_copy(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n", encoding="utf-8"
        )

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan(
                "promote", SkillManager(self.repository).scan(), skill="authoring", dry_run=True
            )
        )

        self.assertEqual(report.status, "success")
        self.assertTrue(report.dry_run)
        self.assertFalse((self.repository / ".skill-manager").exists())
        self.assertFalse((self.repository / "skills" / "authoring").exists())

    def test_recover_restores_backup_for_uncommitted_promotion(self) -> None:
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "new.txt").write_text("new", encoding="utf-8")
        transaction = self.repository / ".skill-manager" / "transactions" / "test-id"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(transaction / "plan.json", {"journal_schema_version": 1, "transaction_id": "test-id", "skill": "authoring"})
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        backup = transaction / "backup"
        backup.mkdir()
        (backup / "old.txt").write_text("old", encoding="utf-8")

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("recover", SkillManager(self.repository).scan())
        )

        self.assertEqual(report.results[0].outcome, "rolled-back")
        self.assertEqual((stable / "old.txt").read_text(), "old")
        self.assertFalse((stable / "new.txt").exists())

    def test_promote_all_rejects_batch_without_writing_when_one_skill_invalid(self) -> None:
        valid = self.repository / "sk-authoring"
        (valid / "agents").mkdir()
        (valid / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n", encoding="utf-8"
        )
        (self.repository / "sk-broken").mkdir()

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("promote", SkillManager(self.repository).scan(), skill=None, validate_all=True)
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("MISSING_SKILL_MD", [item.code for item in report.diagnostics])
        self.assertFalse((self.repository / "skills" / "authoring").exists())
        self.assertFalse((self.repository / ".skill-manager").exists())

    def test_promote_all_promotes_every_valid_skill(self) -> None:
        for name in ("authoring", "beta"):
            directory = self.repository / f"sk-{name}"
            directory.mkdir(exist_ok=True)
            (directory / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name}.\n---\n", encoding="utf-8"
            )
            (directory / "agents").mkdir(exist_ok=True)
            (directory / "agents" / "openai.yaml").write_text(
                f"interface:\n  display_name: {name}\n  short_description: {name}\n", encoding="utf-8"
            )

        report = SkillManager(self.repository).apply(
            SkillManager(self.repository).plan("promote", SkillManager(self.repository).scan(), skill=None, validate_all=True)
        )

        self.assertEqual(report.status, "success")
        self.assertTrue((self.repository / "skills" / "authoring").is_dir())
        self.assertTrue((self.repository / "skills" / "beta").is_dir())

    def test_promote_all_rolls_back_already_replaced_skill_on_failure(self) -> None:
        for name in ("authoring", "beta"):
            directory = self.repository / f"sk-{name}"
            directory.mkdir(exist_ok=True)
            (directory / "agents").mkdir(exist_ok=True)
            (directory / "agents" / "openai.yaml").write_text(
                f"interface:\n  display_name: {name}\n  short_description: {name}\n", encoding="utf-8"
            )
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "old.txt").write_text("old", encoding="utf-8")
        original_replace = manager_service.os.replace
        calls = 0

        def failing_second_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected")
            return original_replace(source, destination)

        with patch.object(manager_service.os, "replace", side_effect=failing_second_replace):
            report = SkillManager(self.repository).apply(
                SkillManager(self.repository).plan("promote", SkillManager(self.repository).scan(), skill=None, validate_all=True)
            )

        self.assertEqual(report.status, "failed")
        self.assertEqual((stable / "old.txt").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
