from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "manage.py"
SPEC = importlib.util.spec_from_file_location("skill_manager", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
skill_manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = skill_manager
SPEC.loader.exec_module(skill_manager)


class FakeWindow:
    def __init__(self, keys: list[int]) -> None:
        self.keys = iter(keys)

    def erase(self) -> None:
        pass

    def getmaxyx(self) -> tuple[int, int]:
        return (40, 160)

    def addnstr(self, *_args) -> None:
        pass

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return next(self.keys)


class SkillManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.skills_root = self.root / "repo" / "skills"
        self.skills_root.mkdir(parents=True)
        self.agent = skill_manager.AgentTarget(
            key="test",
            label="Test Agent",
            config_dir=self.root / "agent",
            skills_dir=self.root / "agent" / "skills",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_skill(self, name: str = "example-skill"):
        path = self.skills_root / name
        (path / "agents").mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Example skill.\n---\n\n# Example\n",
            encoding="utf-8",
        )
        (path / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Example\n",
            encoding="utf-8",
        )
        return skill_manager.Skill(name=name, path=path.resolve())

    def test_discovers_only_complete_skills(self) -> None:
        self.make_skill()
        (self.skills_root / "__pycache__").mkdir()
        incomplete = self.skills_root / "incomplete-skill"
        incomplete.mkdir()
        (incomplete / "SKILL.md").write_text(
            "---\nname: incomplete-skill\ndescription: Incomplete.\n---\n",
            encoding="utf-8",
        )

        skills, errors = skill_manager.discover_skills(self.skills_root)

        self.assertEqual([skill.name for skill in skills], ["example-skill"])
        self.assertEqual(errors, ["incomplete-skill: missing agents/openai.yaml"])

    def test_description_must_be_inside_frontmatter(self) -> None:
        path = self.skills_root / "misplaced-description"
        (path / "agents").mkdir(parents=True)
        (path / "SKILL.md").write_text(
            "---\nname: misplaced-description\n---\n\ndescription: Not frontmatter.\n",
            encoding="utf-8",
        )
        (path / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")

        skills, errors = skill_manager.discover_skills(self.skills_root)

        self.assertEqual(skills, [])
        self.assertEqual(
            errors,
            ["misplaced-description: missing non-empty description in SKILL.md"],
        )

    def test_folded_frontmatter_description_is_supported(self) -> None:
        path = self.skills_root / "folded-description"
        (path / "agents").mkdir(parents=True)
        (path / "SKILL.md").write_text(
            "---\nname: folded-description\ndescription: >\n  A folded description.\n---\n",
            encoding="utf-8",
        )
        (path / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")

        skills, errors = skill_manager.discover_skills(self.skills_root)

        self.assertEqual([skill.name for skill in skills], ["folded-description"])
        self.assertEqual(errors, [])

    def test_install_and_uninstall_exact_symlink(self) -> None:
        skill = self.make_skill()

        install_results = skill_manager.set_skill_state(skill, [self.agent], True)
        destination = self.agent.skills_dir / skill.name

        self.assertTrue(destination.is_symlink())
        self.assertEqual(destination.resolve(), skill.path)
        self.assertIn("did link", install_results[0])
        self.assertIs(
            skill_manager.installation_state(skill, self.agent),
            skill_manager.InstallState.INSTALLED,
        )

        uninstall_results = skill_manager.set_skill_state(skill, [self.agent], False)

        self.assertFalse(destination.exists())
        self.assertIn("did unlink", uninstall_results[0])

    def test_dry_run_does_not_create_target(self) -> None:
        skill = self.make_skill()

        results = skill_manager.set_skill_state(skill, [self.agent], True, dry_run=True)

        self.assertFalse(self.agent.skills_dir.exists())
        self.assertIn("would link", results[0])

    def test_regular_directory_is_a_conflict_and_is_preserved(self) -> None:
        skill = self.make_skill()
        destination = self.agent.skills_dir / skill.name
        destination.mkdir(parents=True)
        marker = destination / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaises(skill_manager.ManagerError):
            skill_manager.set_skill_state(skill, [self.agent], True)

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_unrelated_symlink_is_a_conflict_and_is_preserved(self) -> None:
        skill = self.make_skill()
        other = self.root / "other"
        other.mkdir()
        self.agent.skills_dir.mkdir(parents=True)
        destination = self.agent.skills_dir / skill.name
        destination.symlink_to(other, target_is_directory=True)

        with self.assertRaises(skill_manager.ManagerError):
            skill_manager.set_skill_state(skill, [self.agent], False)

        self.assertTrue(destination.is_symlink())
        self.assertEqual(destination.resolve(), other.resolve())

    def test_aggregate_state_reports_partial(self) -> None:
        skill = self.make_skill()
        second = skill_manager.AgentTarget(
            key="second",
            label="Second Agent",
            config_dir=self.root / "second",
            skills_dir=self.root / "second" / "skills",
        )
        skill_manager.set_skill_state(skill, [self.agent], True)

        self.assertIs(
            skill_manager.aggregate_state(skill, [self.agent, second]),
            skill_manager.InstallState.PARTIAL,
        )

    def test_overlapping_agent_targets_are_rejected(self) -> None:
        targets = skill_manager.default_agent_targets(self.root)
        codex = next(target for target in targets if target.key == "codex")
        shared = next(target for target in targets if target.key == "agents")

        with self.assertRaises(skill_manager.ManagerError):
            skill_manager.validate_agent_selection([codex, shared])

    def test_duplicate_agent_keys_are_deduplicated(self) -> None:
        selected = skill_manager._select_agents(
            [self.agent],
            [self.agent.key, self.agent.key],
        )

        self.assertEqual(selected, [self.agent])

    def test_agent_tui_selects_multiple_targets(self) -> None:
        second = skill_manager.AgentTarget(
            key="second",
            label="Second Agent",
            config_dir=self.root / "second",
            skills_dir=self.root / "second" / "skills",
        )
        window = FakeWindow(
            [
                ord(" "),
                skill_manager.curses.KEY_DOWN,
                ord(" "),
                10,
            ]
        )

        selected = skill_manager.choose_agents(window, [self.agent, second])

        self.assertEqual(selected, [self.agent, second])

    def test_skill_tui_marks_missing_skill_for_installation(self) -> None:
        skill = self.make_skill()
        window = FakeWindow([ord(" "), 10])

        changes = skill_manager.choose_skills(window, [skill], [self.agent])

        self.assertEqual(changes, {skill.name: True})


if __name__ == "__main__":
    unittest.main()
