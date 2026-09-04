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


if __name__ == "__main__":
    unittest.main()
