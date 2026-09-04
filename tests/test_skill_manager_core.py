from __future__ import annotations

import io
import json
import fcntl
import curses
from datetime import datetime
import subprocess
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import skill_manager
from tools.skill_manager_core import CandidateSource, SkillManager
from tools.skill_manager_core import service as manager_service
from tools.skill_manager_core.journal import append_event, read_events, read_plan, write_plan
from tools.skill_manager_core.targets import AgentTarget, hermes_target, is_exact_managed_link, opencode_discovery_roots
from tools.skill_manager_core.provenance import stable_provenance
from tools.skill_manager_core.identity import package_identity
from tools.skill_manager_core.filesystem import _mount_type
from tools.skill_manager_core.release import release_tag_name
from tools.skill_manager_core.tui import TuiState, _aggregate_installation_state, _choose_skill_states, _choose_targets, advance


class FakeWindow:
    def __init__(self, keys: list[int]) -> None:
        self.keys = iter(keys)

    def erase(self) -> None:
        pass

    def addnstr(self, *args: object) -> None:
        pass

    def getmaxyx(self) -> tuple[int, int]:
        return 40, 160

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return next(self.keys)


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

    def test_filesystem_detection_uses_deepest_mount(self) -> None:
        mountinfo = (
            "1 0 0:1 / / rw - ext4 /dev/root rw\n"
            "2 1 0:2 / /mnt/nas rw - nfs server:/share rw\n"
        )

        self.assertEqual(_mount_type(Path("/tmp/repo"), mountinfo), "ext4")
        self.assertEqual(_mount_type(Path("/mnt/nas/skills"), mountinfo), "nfs")

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

    def test_install_core_uses_injected_home_and_manages_exact_link(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)

        installed = manager.apply(
            manager.plan(
                "install",
                manager.scan(),
                skills=("released",),
                agents=("codex",),
            )
        )
        destination = home / ".codex" / "skills" / "released"
        installed_link_is_exact = is_exact_managed_link(
            destination, self.repository / "skills" / "released"
        )
        uninstalled = manager.apply(
            manager.plan(
                "uninstall",
                manager.scan(),
                skills=("released",),
                agents=("codex",),
            )
        )

        self.assertEqual(installed.status, "success")
        self.assertTrue(installed.changed)
        self.assertTrue(installed_link_is_exact)
        self.assertEqual(uninstalled.status, "success")
        self.assertTrue(uninstalled.changed)
        self.assertFalse(destination.exists())

    def test_install_batch_preflights_all_destinations_before_mutation(self) -> None:
        home = self.repository / "home"
        for key in (".codex", ".claude"):
            (home / key / "skills" / "released").mkdir(parents=True)
        codex_conflict = home / ".codex" / "skills" / "released"
        codex_conflict.rmdir()
        claude_conflict = home / ".claude" / "skills" / "released"
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan(
                "install",
                manager.scan(),
                skills=("released",),
                agents=("codex", "claude"),
            )
        )

        self.assertEqual(report.status, "failed")
        self.assertFalse(codex_conflict.exists())
        self.assertTrue(claude_conflict.is_dir())
        self.assertIn("INSTALLATION_CONFLICT", [item.code for item in report.diagnostics])

    def test_install_dry_run_does_not_create_skill_root(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan(
                "install",
                manager.scan(),
                skills=("released",),
                agents=("codex",),
                dry_run=True,
            )
        )

        self.assertEqual(report.results[0].outcome, "would-install")
        self.assertFalse((home / ".codex" / "skills").exists())

    def test_dry_run_reports_filesystem_gate_without_writing(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)

        with patch.object(manager_service, "mutation_filesystem_error", return_value="UNSUPPORTED_FILESYSTEM"):
            report = manager.apply(
                manager.plan(
                    "install",
                    manager.scan(),
                    skills=("released",),
                    agents=("codex",),
                    dry_run=True,
                )
            )

        self.assertEqual(report.status, "failed")
        self.assertIn("UNSUPPORTED_FILESYSTEM", [item.code for item in report.diagnostics])
        self.assertFalse((self.repository / ".skill-manager").exists())
        self.assertFalse((home / ".codex" / "skills").exists())

    def test_install_cli_json_uses_injected_home(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["install", "released", "--agent", "codex", "--json"],
            repository_root=self.repository,
            home=home,
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["command"], "install")
        self.assertEqual(payload["results"][0]["outcome"], "installed")
        self.assertEqual(payload["results"][0]["source"], "stable")
        self.assertEqual(payload["results"][0]["target"], "codex")
        self.assertEqual(payload["results"][0]["details"]["config_root"], str(home / ".codex"))
        self.assertTrue((home / ".codex" / "skills" / "released").is_symlink())

    def test_install_rejects_invalid_stable_but_exact_uninstall_remains_allowed(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)
        stable = self.repository / "skills" / "released"
        destination = home / ".codex" / "skills" / "released"
        destination.parent.mkdir()
        destination.symlink_to(stable)
        (stable / "agents" / "openai.yaml").unlink()

        install = manager.apply(
            manager.plan("install", manager.scan(), skills=("released",), agents=("codex",))
        )
        uninstall = manager.apply(
            manager.plan("uninstall", manager.scan(), skills=("released",), agents=("codex",))
        )

        self.assertEqual(install.status, "failed")
        self.assertIn("MISSING_OPENAI_METADATA", [item.code for item in install.diagnostics])
        self.assertEqual(uninstall.status, "success")
        self.assertFalse(destination.exists())

    def test_opencode_duplicate_exposure_blocks_native_install(self) -> None:
        home = self.repository / "home"
        (home / ".config" / "opencode").mkdir(parents=True)
        duplicate = home / ".agents" / "skills" / "released"
        duplicate.mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan("install", manager.scan(), skills=("released",), agents=("opencode",))
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("DUPLICATE_EXPOSURE", [item.code for item in report.diagnostics])
        self.assertFalse((home / ".config" / "opencode" / "skills").exists())

    def test_opencode_exact_uninstall_is_not_blocked_by_duplicate_preflight(self) -> None:
        home = self.repository / "home"
        config_root = self.repository / "opencode-active"
        skills_root = config_root / "skills"
        skills_root.mkdir(parents=True)
        destination = skills_root / "released"
        destination.symlink_to(self.repository / "skills" / "released")
        manager = SkillManager(
            self.repository,
            home=home,
            environ={"OPENCODE_CONFIG_DIR": str(config_root)},
        )

        report = manager.apply(
            manager.plan("uninstall", manager.scan(), skills=("released",), agents=("opencode",))
        )

        self.assertEqual(report.status, "success")
        self.assertFalse(destination.is_symlink())

    def test_uninstall_removes_dangling_exact_managed_link(self) -> None:
        home = self.repository / "home"
        (home / ".codex" / "skills").mkdir(parents=True)
        missing_stable = self.repository / "skills" / "missing"
        destination = home / ".codex" / "skills" / "missing"
        destination.symlink_to(missing_stable)
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan("uninstall", manager.scan(), skills=("missing",), agents=("codex",))
        )

        self.assertEqual(report.status, "success")
        self.assertEqual(report.results[0].outcome, "uninstalled")
        self.assertFalse(destination.is_symlink())

    def test_install_preflight_error_still_reports_every_requested_pair(self) -> None:
        home = self.repository / "home"
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan(
                "install",
                manager.scan(),
                skills=("released", "missing"),
                agents=("codex", "claude"),
            )
        )

        self.assertEqual(len(report.results), 4)
        self.assertEqual(
            {(result.skill, result.target) for result in report.results},
            {
                ("released", "codex"),
                ("missing", "codex"),
                ("released", "claude"),
                ("missing", "claude"),
            },
        )

    def test_install_all_and_agent_environment_resolution(self) -> None:
        home = self.repository / "home"
        hermes_home = self.repository / "hermes-active"
        hermes_home.mkdir()
        manager = SkillManager(
            self.repository,
            home=home,
            environ={"HERMES_HOME": str(hermes_home)},
        )

        report = manager.apply(
            manager.plan(
                "install",
                manager.scan(),
                agents=("hermes",),
                select_all=True,
                dry_run=True,
            )
        )

        self.assertEqual(report.status, "success")
        self.assertEqual([result.skill for result in report.results], ["released"])
        self.assertEqual(report.results[0].details["config_root"], str(hermes_home))

    def test_install_uses_journaled_transaction_and_target_lock(self) -> None:
        home = self.repository / "home"
        config_root = home / ".codex"
        config_root.mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan("install", manager.scan(), skills=("released",), agents=("codex",))
        )

        self.assertEqual(report.status, "success")
        self.assertIsNotNone(report.transaction_id)
        self.assertTrue((config_root / ".skill-manager.lock").is_file())
        self.assertEqual(stat.S_IMODE((config_root / ".skill-manager.lock").stat().st_mode), 0o600)
        transactions = self.repository / ".skill-manager" / "transactions"
        self.assertEqual(list(transactions.iterdir()), [])

    def test_cross_filesystem_installation_is_proven_or_explicitly_skipped(self) -> None:
        shared_memory = Path("/dev/shm")
        if not shared_memory.is_dir() or self.repository.stat().st_dev == shared_memory.stat().st_dev:
            self.skipTest("no second writable filesystem device is available")
        try:
            other_device = tempfile.TemporaryDirectory(dir=shared_memory)
        except OSError as exc:
            self.skipTest(f"second filesystem is not writable: {exc}")
        with other_device:
            home = Path(other_device.name)
            (home / ".codex").mkdir()
            manager = SkillManager(self.repository, home=home)
            installed = manager.apply(
                manager.plan("install", manager.scan(), skills=("released",), agents=("codex",))
            )
            uninstalled = manager.apply(
                manager.plan("uninstall", manager.scan(), skills=("released",), agents=("codex",))
            )
            destination = home / ".codex" / "skills" / "released"
            destination.symlink_to(self.repository / "skills" / "released")
            transaction = self.repository / ".skill-manager" / "transactions" / "cross-device"
            transaction.mkdir(parents=True)
            plan_hash = write_plan(
                transaction / "plan.json",
                {
                    "journal_schema_version": 1, "transaction_id": transaction.name,
                    "operation_type": "install",
                    "operations": [{
                        "skill": "released", "target": "codex",
                        "stable": str(self.repository / "skills" / "released"),
                        "stable_identity": package_identity(self.repository / "skills" / "released"),
                        "destination": str(destination),
                        "skills_root": str(destination.parent), "skills_root_existed": True,
                        "before": {"kind": "absent"},
                        "after": {"kind": "symlink", "target": str(self.repository / "skills" / "released")},
                    }],
                },
            )
            append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
            recovered = manager.apply(manager.plan("recover", manager.scan()))
            self.assertFalse(destination.is_symlink())
        self.assertEqual((installed.status, uninstalled.status), ("success", "success"))
        self.assertEqual(recovered.status, "success")

    def test_install_failure_rolls_back_every_link_and_created_skill_root(self) -> None:
        second = self.repository / "skills" / "second"
        (second / "agents").mkdir(parents=True)
        (second / "SKILL.md").write_text(
            "---\nname: second\ndescription: Second stable candidate.\n---\n",
            encoding="utf-8",
        )
        (second / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Second\n  short_description: Stable candidate\n",
            encoding="utf-8",
        )
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)
        real_symlink_to = Path.symlink_to
        calls = 0

        def fail_second_symlink(path: Path, target: object, *args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected link failure")
            real_symlink_to(path, target, *args, **kwargs)

        with patch.object(Path, "symlink_to", autospec=True, side_effect=fail_second_symlink):
            report = manager.apply(
                manager.plan(
                    "install",
                    manager.scan(),
                    skills=("released", "second"),
                    agents=("codex",),
                )
            )

        self.assertEqual(report.status, "failed")
        self.assertIn("INSTALLATION_APPLY_FAILED", [item.code for item in report.diagnostics])
        self.assertFalse((home / ".codex" / "skills").exists())
        transactions = self.repository / ".skill-manager" / "transactions"
        self.assertEqual(list(transactions.iterdir()), [])

    def test_sigkill_during_installation_is_recovered_without_partial_link(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        program = """
import sys, time
from pathlib import Path
from tools.skill_manager_core import SkillManager
from tools.skill_manager_core import service, transaction
root, home = map(Path, sys.argv[1:])
skills_root = home / '.codex' / 'skills'
real_fsync = service._fsync_directory
def pause_after_link(path):
    real_fsync(path)
    if path == skills_root:
        print('LINK_APPLIED', flush=True)
        time.sleep(60)
service._fsync_directory = pause_after_link
manager = SkillManager(root, home=home)
manager.apply(manager.plan('install', manager.scan(), skills=('released',), agents=('codex',)))
"""
        child = subprocess.Popen(
            [sys.executable, "-c", program, str(self.repository), str(home)],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert child.stdout is not None
        marker = child.stdout.readline().strip()
        self.assertEqual(marker, "LINK_APPLIED")
        child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        assert child.stderr is not None
        child.stderr.close()
        destination = home / ".codex" / "skills" / "released"
        self.assertTrue(destination.is_symlink())
        transactions = list((self.repository / ".skill-manager" / "transactions").iterdir())
        self.assertEqual(len(transactions), 1)
        journal_plan, _ = read_plan(transactions[0] / "plan.json")
        self.assertTrue(str(journal_plan["operations"][0]["stable_identity"]).startswith("sha256:"))
        for private_directory in (transactions[0], transactions[0] / "staging", transactions[0] / "backup"):
            self.assertEqual(stat.S_IMODE(private_directory.stat().st_mode), 0o700)

        manager = SkillManager(self.repository, home=home)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success")
        self.assertFalse(destination.is_symlink())

    def test_sigkill_during_recovery_can_be_recovered_again(self) -> None:
        home = self.repository / "home"
        skills_root = home / ".codex" / "skills"
        skills_root.mkdir(parents=True)
        stable = self.repository / "skills" / "released"
        destination = skills_root / "released"
        destination.symlink_to(stable)
        transaction = self.repository / ".skill-manager" / "transactions" / "retry-recovery"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": transaction.name,
                "operation_type": "install",
                "operations": [{
                    "skill": "released", "target": "codex", "stable": str(stable),
                    "destination": str(destination), "skills_root": str(skills_root),
                    "skills_root_existed": True, "before": {"kind": "absent"},
                    "after": {"kind": "symlink", "target": str(stable)},
                }],
            },
        )
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        program = """
import sys, time
from pathlib import Path
from tools.skill_manager_core import SkillManager
from tools.skill_manager_core import service, transaction
root, home = map(Path, sys.argv[1:])
real_restore = transaction.restore_link_state
def pause_after_restore(path, state):
    real_restore(path, state)
    print('RESTORED', flush=True)
    time.sleep(60)
transaction.restore_link_state = pause_after_restore
manager = SkillManager(root, home=home)
manager.apply(manager.plan('recover', manager.scan()))
"""
        child = subprocess.Popen(
            [sys.executable, "-c", program, str(self.repository), str(home)],
            cwd=Path(__file__).resolve().parents[1], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "RESTORED")
        child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        assert child.stderr is not None
        child.stderr.close()

        manager = SkillManager(self.repository, home=home)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success")
        self.assertFalse(destination.is_symlink())
        self.assertFalse(transaction.exists())

    def test_recover_rolls_back_interrupted_installation(self) -> None:
        home = self.repository / "home"
        config_root = home / ".codex"
        skills_root = config_root / "skills"
        skills_root.mkdir(parents=True)
        stable = self.repository / "skills" / "released"
        destination = skills_root / "released"
        destination.symlink_to(stable)
        transaction = self.repository / ".skill-manager" / "transactions" / "install-id"
        transaction.mkdir(parents=True)
        operation = {
            "skill": "released",
            "target": "codex",
            "stable": str(stable),
            "destination": str(destination),
            "skills_root": str(skills_root),
            "skills_root_existed": True,
            "before": {"kind": "absent"},
            "after": {"kind": "symlink", "target": str(stable)},
        }
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": "install-id",
                "operation_type": "install",
                "operations": [operation],
            },
        )
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        append_event(transaction / "events.jsonl", plan_hash, "PENDING", {"operation": 0})

        manager = SkillManager(self.repository, home=home)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success")
        self.assertEqual(report.results[0].outcome, "rolled-back")
        self.assertFalse(destination.is_symlink())
        self.assertFalse(transaction.exists())

    def test_installation_rollback_preserves_journal_on_unexpected_root_removal_error(self) -> None:
        skills_root = self.repository / "target" / "skills"
        skills_root.mkdir(parents=True)
        destination = skills_root / "released"
        transaction = self.repository / ".skill-manager" / "transactions" / "rmdir-error"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {"journal_schema_version": 1, "transaction_id": transaction.name},
        )
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        operations = [{
            "destination": str(destination), "skills_root": str(skills_root),
            "skills_root_existed": False, "before": {"kind": "absent"},
            "after": {"kind": "symlink", "target": str(self.repository / "skills" / "released")},
        }]
        real_rmdir = Path.rmdir

        def fail_selected_root(path: Path) -> None:
            if path == skills_root:
                raise OSError(13, "permission denied")
            real_rmdir(path)

        with patch.object(Path, "rmdir", autospec=True, side_effect=fail_selected_root):
            diagnostics = manager_service._rollback_installation(
                transaction, operations, plan_hash
            )

        self.assertEqual(diagnostics[0].code, "RECOVERY_REQUIRED")
        self.assertTrue(transaction.exists())

    def test_recover_preserves_committed_installation_after_cleaning_event(self) -> None:
        home = self.repository / "home"
        skills_root = home / ".codex" / "skills"
        skills_root.mkdir(parents=True)
        stable = self.repository / "skills" / "released"
        destination = skills_root / "released"
        destination.symlink_to(stable)
        transaction = self.repository / ".skill-manager" / "transactions" / "committed-install"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": transaction.name,
                "operation_type": "install",
                "operations": [{
                    "skill": "released",
                    "target": "codex",
                    "stable": str(stable),
                    "destination": str(destination),
                    "skills_root": str(skills_root),
                    "skills_root_existed": True,
                    "before": {"kind": "absent"},
                    "after": {"kind": "symlink", "target": str(stable)},
                }],
            },
        )
        append_event(transaction / "events.jsonl", plan_hash, "COMMITTED")
        append_event(transaction / "events.jsonl", plan_hash, "CLEANING")

        manager = SkillManager(self.repository, home=home)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success")
        self.assertTrue(is_exact_managed_link(destination, stable))
        self.assertFalse(transaction.exists())

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

    def test_promote_record_release_creates_one_scoped_commit_and_tag(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repository, check=True)
        subprocess.run(["git", "add", "--", "sk-authoring", "skills"], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.repository, check=True)
        message_file = self.repository / "message.txt"
        message_file.write_text("release authoring", encoding="utf-8")
        manager = SkillManager(self.repository)

        report = manager.apply(
            manager.plan(
                "promote",
                manager.scan(),
                skills=("authoring",),
                record_release=True,
                commit_message_files=(f"authoring={message_file}",),
            )
        )

        self.assertEqual(report.status, "success")
        result = report.results[0]
        self.assertRegex(str(result.details["release_tag"]), r"^authoring-\d{6}-v1$")
        self.assertEqual(
            subprocess.run(
                ["git", "show", "--format=", "--name-only", "HEAD"],
                cwd=self.repository,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip().splitlines(),
            [
                "skills/authoring/SKILL.md",
                "skills/authoring/agents/openai.yaml",
            ],
        )
        self.assertEqual(
            subprocess.run(
                ["git", "tag", "--points-at", "HEAD"],
                cwd=self.repository,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip(),
            result.details["release_tag"],
        )

    def test_release_hook_mutation_rolls_back_commit_tag_and_stable(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repository, check=True)
        subprocess.run(["git", "add", "--", "sk-authoring", "skills"], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.repository, check=True)
        initial_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repository, check=True, text=True, capture_output=True
        ).stdout.strip()
        hook = self.repository / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nprintf 'hook edit' >> skills/authoring/SKILL.md\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        message_file = self.repository / "message.txt"
        message_file.write_text("release authoring", encoding="utf-8")
        manager = SkillManager(self.repository)

        report = manager.apply(
            manager.plan(
                "promote",
                manager.scan(),
                skills=("authoring",),
                record_release=True,
                commit_message_files=(f"authoring={message_file}",),
            )
        )

        self.assertEqual(report.status, "failed")
        self.assertFalse((self.repository / "skills" / "authoring").exists())
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.repository, check=True, text=True, capture_output=True
            ).stdout.strip(),
            initial_head,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "tag", "--list"], cwd=self.repository, check=True, text=True, capture_output=True
            ).stdout.strip(),
            "",
        )

    def test_recover_release_commit_before_applied_event_uses_pending_cas(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repository, check=True)
        subprocess.run(["git", "add", "--", "sk-authoring", "skills"], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.repository, check=True)
        original_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repository, check=True, text=True, capture_output=True
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.repository, check=True, text=True, capture_output=True
        ).stdout.strip()
        stable = self.repository / "skills" / "authoring"
        shutil.copytree(development, stable)
        subprocess.run(["git", "add", "--", "skills/authoring"], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-qm", "release authoring", "--", "skills/authoring"], cwd=self.repository, check=True)
        transaction_id = "release-gap"
        tag = "authoring-260904-v1"
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", f"Release authoring\n\nAtomic-Batch: {transaction_id}"],
            cwd=self.repository, check=True,
        )
        transaction = self.repository / ".skill-manager" / "transactions" / transaction_id
        transaction.mkdir(parents=True)
        after_identity = manager_service._tree_identity(stable)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1, "transaction_id": transaction_id,
                "operation_type": "promote", "release_instant": "2026-09-04T00:00:00Z",
                "skills": [{
                    "skill": "authoring", "before_exists": False,
                    "before_identity": "absent", "after_identity": after_identity,
                    "package_identity": package_identity(stable),
                }],
                "original_head": original_head, "original_branch": branch,
                "release_tags": {"authoring": tag}, "commit_messages": {},
            },
        )
        events = transaction / "events.jsonl"
        append_event(events, plan_hash, "APPLYING")
        append_event(events, plan_hash, "PENDING", {
            "kind": "release", "skill": "authoring",
            "expected_parent": original_head, "tag": tag,
        })

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success")
        self.assertFalse(stable.exists())
        self.assertEqual(
            subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repository, check=True, text=True, capture_output=True).stdout.strip(),
            original_head,
        )
        self.assertEqual(
            subprocess.run(["git", "tag", "--list"], cwd=self.repository, check=True, text=True, capture_output=True).stdout.strip(),
            "",
        )

    def test_tui_requires_literal_confirmation_and_cancellation_never_applies(self) -> None:
        state = advance(advance(TuiState(), "\n"), "\n")
        self.assertEqual(state.screen, 3)
        self.assertFalse(advance(state, "\n").applied)
        self.assertFalse(advance(state, "y", has_errors=True).applied)
        self.assertTrue(advance(state, "y").applied)
        self.assertTrue(advance(state, "q").cancelled)

    def test_curses_tui_uses_arrows_space_and_enter_for_three_screen_selection(self) -> None:
        first = AgentTarget("codex", self.repository / "codex")
        second = AgentTarget("claude", self.repository / "claude")
        selected = _choose_targets(
            FakeWindow([ord(" "), curses.KEY_DOWN, ord(" "), 10]),
            {"codex": first, "claude": second},
        )
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        manager = SkillManager(self.repository, home=home)
        status = manager.apply(
            manager.plan("status", manager.scan(), agents=("codex",))
        )
        stable = tuple(result for result in status.results if result.skill == "released")
        desired = _choose_skill_states(
            FakeWindow([ord(" "), 10]),
            stable,
            ("codex",),
        )

        self.assertEqual(selected, ("codex", "claude"))
        self.assertEqual(desired, {"released": True})

    def test_three_screen_tui_previews_then_applies_through_core(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        output = io.StringIO()

        exit_code = skill_manager.main(
            [],
            repository_root=self.repository,
            home=home,
            stdin=io.StringIO("codex\ninstall released\ny\n"),
            stdout=output,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Screen 1/3", output.getvalue())
        self.assertIn("Screen 2/3", output.getvalue())
        self.assertIn("Screen 3/3", output.getvalue())
        self.assertTrue((home / ".codex" / "skills" / "released").is_symlink())

    def test_three_screen_tui_dry_run_and_cancel_do_not_write(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)

        dry_run = skill_manager.main(
            ["tui", "--dry-run"],
            repository_root=self.repository,
            home=home,
            stdin=io.StringIO("codex\ninstall released\n"),
            stdout=io.StringIO(),
        )
        cancelled = skill_manager.main(
            [],
            repository_root=self.repository,
            home=home,
            stdin=io.StringIO("q\n"),
            stdout=io.StringIO(),
        )

        self.assertEqual((dry_run, cancelled), (0, 0))
        self.assertFalse((home / ".codex" / "skills").exists())

    def test_tui_reports_recovery_required_before_target_selection(self) -> None:
        transaction = self.repository / ".skill-manager" / "transactions" / "pending"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {"journal_schema_version": 1, "transaction_id": "pending", "skill": "authoring"},
        )
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        output = io.StringIO()

        exit_code = skill_manager.main(
            [],
            repository_root=self.repository,
            stdin=io.StringIO(""),
            stdout=output,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("Recovery Required", output.getvalue())
        self.assertIn("recover --dry-run", output.getvalue())

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

    def test_read_only_command_reports_busy_without_reading_through_writer_lock(self) -> None:
        lock_path = self.repository / ".skill-manager" / "repository.lock"
        lock_path.parent.mkdir()
        lock_path.touch()
        manager = SkillManager(self.repository)
        with lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = manager.apply(manager.plan("list", manager.scan()))

        self.assertEqual(report.status, "busy")
        self.assertEqual(report.diagnostics[0].code, "MANAGER_BUSY")

    def test_cli_returns_failure_for_recovery_required_report(self) -> None:
        transaction = self.repository / ".skill-manager" / "transactions" / "pending-cli"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {"journal_schema_version": 1, "transaction_id": transaction.name, "skill": "authoring"},
        )
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["list", "--json"], repository_root=self.repository, stdout=stdout
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "recovery-required")

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

    def test_status_reports_selected_agent_installation_state_and_paths(self) -> None:
        home = self.repository / "home"
        config_root = home / ".codex"
        skills_root = config_root / "skills"
        skills_root.mkdir(parents=True)
        (skills_root / "released").symlink_to(self.repository / "skills" / "released")
        stdout = io.StringIO()

        exit_code = skill_manager.main(
            ["status", "released", "--agent", "codex", "--json"],
            repository_root=self.repository,
            home=home,
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual([item["skill"] for item in payload["results"]], ["released"])
        installation = payload["results"][0]["details"]["installation_state"]["codex"]
        self.assertEqual(installation["state"], "installed")
        self.assertEqual(installation["config_root"], str(config_root))
        self.assertEqual(installation["skill_root"], str(skills_root))

    def test_status_explicit_undetected_agent_fails_without_hiding_repository_state(self) -> None:
        home = self.repository / "home"
        manager = SkillManager(self.repository, home=home)

        report = manager.apply(
            manager.plan("status", manager.scan(), agents=("codex",))
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("TARGET_NOT_DETECTED", [item.code for item in report.diagnostics])
        self.assertTrue(any(result.skill == "released" for result in report.results))

    def test_tui_blocks_only_invalid_stable_not_invalid_development(self) -> None:
        home = self.repository / "home"
        (home / ".codex").mkdir(parents=True)
        development = self.repository / "sk-released"
        development.mkdir()
        (development / "SKILL.md").write_text(
            "---\nname: released\ndescription: Invalid Development copy.\n---\n",
            encoding="utf-8",
        )
        manager = SkillManager(self.repository, home=home)
        report = manager.apply(
            manager.plan("status", manager.scan(), agents=("codex",))
        )
        released = next(result for result in report.results if result.skill == "released")

        current, blocked = _aggregate_installation_state(released, ("codex",))

        self.assertFalse(current)
        self.assertFalse(blocked)
        self.assertEqual(released.details["validation_state"], "invalid")
        self.assertEqual(released.details["stable_validation_state"], "valid")

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

    def test_sigkill_during_promotion_is_recovered_to_previous_state(self) -> None:
        development = self.repository / "sk-authoring"
        (development / "agents").mkdir()
        (development / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Authoring\n  short_description: Development\n",
            encoding="utf-8",
        )
        program = """
import sys, time
from pathlib import Path
from tools.skill_manager_core import SkillManager
from tools.skill_manager_core import service
root = Path(sys.argv[1])
stable = root / 'skills' / 'authoring'
real_replace = service.os.replace
def pause_after_replace(source, destination):
    real_replace(source, destination)
    if Path(destination) == stable and 'staging' in Path(source).parts:
        print('PROMOTED', flush=True)
        time.sleep(60)
service.os.replace = pause_after_replace
manager = SkillManager(root)
manager.apply(manager.plan('promote', manager.scan(), skills=('authoring',)))
"""
        child = subprocess.Popen(
            [sys.executable, "-c", program, str(self.repository)],
            cwd=Path(__file__).resolve().parents[1], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "PROMOTED")
        child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        assert child.stderr is not None
        child.stderr.close()
        self.assertTrue((self.repository / "skills" / "authoring").is_dir())

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "success")
        self.assertFalse((self.repository / "skills" / "authoring").exists())

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

    def test_journal_append_discards_only_incomplete_final_record(self) -> None:
        transaction = self.repository / "transaction"
        transaction.mkdir()
        plan_hash = write_plan(
            transaction / "plan.json",
            {"journal_schema_version": 1, "transaction_id": "tail"},
        )
        events = transaction / "events.jsonl"
        append_event(events, plan_hash, "PREPARED")
        with events.open("ab") as output:
            output.write(b'{"sequence":2')

        append_event(events, plan_hash, "APPLYING")

        self.assertEqual(
            [event["type"] for event in read_events(events, plan_hash)],
            ["PREPARED", "APPLYING"],
        )

    def test_recover_rejects_unknown_journal_schema(self) -> None:
        transaction = self.repository / ".skill-manager" / "transactions" / "future"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {"journal_schema_version": 999, "transaction_id": "future", "skill": "authoring"},
        )
        append_event(transaction / "events.jsonl", plan_hash, "PREPARED")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "recovery-required")
        self.assertTrue(transaction.exists())

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

    def test_recover_refuses_to_overwrite_external_promotion_change(self) -> None:
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "new.txt").write_text("planned", encoding="utf-8")
        after_identity = manager_service._tree_identity(stable)
        transaction = self.repository / ".skill-manager" / "transactions" / "promotion-cas"
        backup = transaction / "backup" / "authoring"
        backup.mkdir(parents=True)
        (backup / "old.txt").write_text("old", encoding="utf-8")
        before_identity = manager_service._tree_identity(backup)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": transaction.name,
                "operation_type": "promote",
                "release_instant": "2026-09-04T00:00:00Z",
                "skills": [{
                    "skill": "authoring",
                    "before_exists": True,
                    "before_identity": before_identity,
                    "after_identity": after_identity,
                }],
                "original_head": None,
                "original_branch": None,
                "release_tags": {},
            },
        )
        append_event(transaction / "events.jsonl", plan_hash, "APPLYING")
        (stable / "new.txt").write_text("external edit", encoding="utf-8")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(report.status, "recovery-required")
        self.assertEqual((stable / "new.txt").read_text(encoding="utf-8"), "external edit")
        self.assertTrue((backup / "old.txt").is_file())

    def test_recover_committed_promotion_recreates_missing_receipt(self) -> None:
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        (stable / "SKILL.md").write_text("stable", encoding="utf-8")
        transaction = self.repository / ".skill-manager" / "transactions" / "promotion-receipt"
        transaction.mkdir(parents=True)
        identity = manager_service._tree_identity(stable)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": transaction.name,
                "operation_type": "promote",
                "release_instant": "2026-09-04T00:00:00Z",
                "skills": [{
                    "skill": "authoring",
                    "before_exists": False,
                    "before_identity": "absent",
                    "after_identity": identity,
                }],
                "original_head": None,
                "original_branch": None,
                "release_tags": {},
            },
        )
        append_event(transaction / "events.jsonl", plan_hash, "COMMITTED")

        manager = SkillManager(self.repository)
        report = manager.apply(manager.plan("recover", manager.scan()))

        state = json.loads((self.repository / ".skill-manager" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(report.status, "success")
        self.assertEqual(state["receipts"]["authoring"]["transaction_id"], transaction.name)
        self.assertFalse(transaction.exists())

    def test_recover_committed_promotion_refuses_to_adopt_external_drift(self) -> None:
        stable = self.repository / "skills" / "authoring"
        stable.mkdir()
        skill_file = stable / "SKILL.md"
        skill_file.write_text("committed", encoding="utf-8")
        committed_identity = manager_service._tree_identity(stable)
        transaction = self.repository / ".skill-manager" / "transactions" / "committed-drift"
        transaction.mkdir(parents=True)
        plan_hash = write_plan(
            transaction / "plan.json",
            {
                "journal_schema_version": 1,
                "transaction_id": transaction.name,
                "operation_type": "promote",
                "release_instant": "2026-09-04T00:00:00Z",
                "skills": [{
                    "skill": "authoring", "before_exists": False,
                    "before_identity": "absent", "after_identity": committed_identity,
                }],
                "original_head": None, "original_branch": None, "release_tags": {},
            },
        )
        append_event(transaction / "events.jsonl", plan_hash, "COMMITTED")
        skill_file.write_text("external drift", encoding="utf-8")

        manager = SkillManager(self.repository)
        dry_run = manager.apply(
            manager.plan("recover", manager.scan(), dry_run=True)
        )
        report = manager.apply(manager.plan("recover", manager.scan()))

        self.assertEqual(dry_run.status, "recovery-required")
        self.assertEqual(report.status, "recovery-required")
        self.assertFalse((self.repository / ".skill-manager" / "state.json").exists())
        self.assertTrue(transaction.exists())

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
