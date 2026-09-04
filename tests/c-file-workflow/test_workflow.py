#!/usr/bin/env python3
"""Integration tests for the c-file-workflow Development or Stable Skill."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SKILL = Path(
    os.environ.get(
        "SKILL_UNDER_TEST",
        str(Path(__file__).resolve().parents[2] / "sk-c-file-workflow"),
    )
).resolve()
SCRIPT = SKILL / "scripts" / "c_file_workflow.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLANG_FORMAT = shutil.which("clang-format")


def invoke(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT)]
    if CLANG_FORMAT:
        command.extend(["--clang-format", CLANG_FORMAT])
    command.extend(arguments)
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="c-file-workflow-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_component_creation_compiles_and_checks(self) -> None:
        result = invoke("uart", "--output", str(self.root))
        self.assert_success(result)
        source = self.root / "uart.c"
        header = self.root / "uart.h"
        self.assertTrue(source.is_file())
        self.assertTrue(header.is_file())
        required = (FIXTURES / "creation" / "component.expected.txt").read_text().splitlines()
        combined = source.read_text() + header.read_text()
        for marker in required[2:]:
            self.assertIn(marker, combined)
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(self.root),
                "-c",
                str(source),
                "-o",
                str(self.root / "uart.o"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
        check = invoke("--check", "--component", str(source))
        self.assert_success(check)
        self.assertIn("unchanged:", check.stdout)
        both = invoke("--check", "--component", str(source), str(header))
        self.assert_success(both)
        self.assertNotIn("duplicate input", both.stderr)

    def test_component_handler_order_is_checked(self) -> None:
        self.assert_success(invoke("uart", "--output", str(self.root)))
        source = self.root / "uart.c"
        text = source.read_text()
        text = text.replace("    st_init,\n    st_end,", "    st_end,\n    st_init,")
        source.write_text(text)
        result = invoke("--check", "--component", str(source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("handler names or order", result.stderr)

    def test_component_minimum_members_and_explicit_idle_are_checked(self) -> None:
        self.assert_success(invoke("uart", "--output", str(self.root)))
        source = self.root / "uart.c"
        text = source.read_text().replace("ST_IDLE = 0", "ST_IDLE", 1)
        text = text.replace("    uint8_t  loglvl;\n", "", 1)
        source.write_text(text)
        result = invoke("--check", "--component", str(source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("ST_IDLE = 0", result.stderr)
        self.assertIn("requires loglvl", result.stderr)

    def test_plain_pair_and_explicit_single_file(self) -> None:
        pair = invoke("--plain", "board_cfg", "--output", str(self.root))
        self.assert_success(pair)
        self.assertIn('#include "board_cfg.h"', (self.root / "board_cfg.c").read_text())
        single_root = self.root / "single"
        single = invoke("--new", "worker.c", "--output", str(single_root), "--author", "")
        self.assert_success(single)
        source = (single_root / "worker.c").read_text()
        self.assertNotIn('#include "worker.h"', source)
        self.assertIn("@author  chenzhipeng3472", source)

    def test_formatting_golden_and_idempotent_mtime(self) -> None:
        source = self.root / "messy.c"
        shutil.copyfile(FIXTURES / "formatting" / "messy.c", source)
        first = invoke(str(source))
        self.assert_success(first)
        self.assertEqual(source.read_text(), (FIXTURES / "formatting" / "expected.c").read_text())
        before = source.stat().st_mtime_ns
        time.sleep(0.01)
        second = invoke(str(source))
        self.assert_success(second)
        self.assertIn("unchanged:", second.stdout)
        self.assertEqual(source.stat().st_mtime_ns, before)

    def test_check_is_read_only_and_reports_diff(self) -> None:
        source = self.root / "messy.c"
        original = (FIXTURES / "formatting" / "messy.c").read_bytes()
        source.write_bytes(original)
        result = invoke("--check", str(source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs formatting:", result.stdout)
        self.assertIn("--- a/", result.stdout)
        self.assertEqual(source.read_bytes(), original)

    def test_conflict_and_force(self) -> None:
        target = self.root / "uart.c"
        target.write_text("user data\n")
        conflict = invoke("uart", "--output", str(self.root))
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(target.read_text(), "user data\n")
        forced = invoke("uart", "--output", str(self.root), "--force")
        self.assert_success(forced)
        self.assertIn("replaced:", forced.stdout)

    def test_multi_file_failure_leaves_first_file_unchanged(self) -> None:
        good = self.root / "good.c"
        bad = self.root / "bad.c"
        good.write_text("int  good(void){return 0;}\n")
        bad.write_bytes(b"\xff\xfe")
        before = good.read_bytes()
        result = invoke(str(good), str(bad))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(good.read_bytes(), before)

    def test_bom_and_crlf_are_preserved(self) -> None:
        source = self.root / "bom.c"
        source.write_bytes(b"\xef\xbb\xbfint  value(void){return 0;}\r\n")
        result = invoke(str(source))
        self.assert_success(result)
        data = source.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_mixed_newlines_are_rejected(self) -> None:
        source = self.root / "mixed.c"
        data = b"int a;\r\nint b;\n"
        source.write_bytes(data)
        result = invoke(str(source))
        self.assertEqual(result.returncode, 2)
        self.assertIn("mixed newline", result.stderr)
        self.assertEqual(source.read_bytes(), data)

    def test_symlink_and_hardlink_policy(self) -> None:
        target = self.root / "target.c"
        target.write_text("int  value(void){return 0;}\n")
        link = self.root / "link.c"
        link.symlink_to(target)
        rejected = invoke(str(link))
        self.assertEqual(rejected.returncode, 2)
        followed = invoke("--follow-symlinks", str(link))
        self.assert_success(followed)
        self.assertTrue(link.is_symlink())
        hard = self.root / "hard.c"
        os.link(target, hard)
        hard_result = invoke(str(target))
        self.assertEqual(hard_result.returncode, 2)
        self.assertIn("hard-linked", hard_result.stderr)

    def test_recursive_exclusions_and_hidden_directories(self) -> None:
        visible = self.root / "src"
        hidden = self.root / ".hidden"
        vendor = self.root / "vendor"
        excluded = self.root / "platform" / "legacy"
        for directory in (visible, hidden, vendor, excluded):
            directory.mkdir(parents=True)
            (directory / "file.c").write_text("int  value(void){return 0;}\n")
        result = invoke("--recursive", "--exclude", "platform/legacy/**", str(self.root))
        self.assert_success(result)
        self.assertIn("formatted:", result.stdout)
        self.assertIn("int  value", (hidden / "file.c").read_text())
        self.assertIn("int  value", (vendor / "file.c").read_text())
        self.assertIn("int  value", (excluded / "file.c").read_text())

    def test_existing_relative_directory_is_not_treated_as_creation(self) -> None:
        source_dir = self.root / "src"
        source_dir.mkdir()
        source = source_dir / "file.c"
        source.write_text("int  value(void){return 0;}\n")
        result = invoke("--recursive", "src", cwd=self.root)
        self.assert_success(result)
        self.assertIn("formatted:", result.stdout)

    def test_missing_component_mate_is_a_finding(self) -> None:
        source = self.root / "uart.c"
        source.write_text("int uart_init(void)\n{\n    return 0;\n}\n")
        result = invoke("--check", "--component", str(source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("mate is missing", result.stderr)

    def test_register_checks(self) -> None:
        valid = self.root / "chip.h"
        valid.write_text(
            "#include <stdint.h>\n"
            "typedef union _chipReg {\n"
            "    uint8_t value;\n"
            "    struct { uint8_t low : 4; uint8_t high : 4; } bit;\n"
            "} chipReg_t;\n"
            "_Static_assert(sizeof(chipReg_t) == 1U, \"one byte\");\n"
        )
        self.assert_success(invoke(str(valid)))
        clean = invoke("--check", "--register", str(valid))
        self.assert_success(clean)
        broken = valid.read_text().replace("uint8_t high : 4", "uint8_t high : 3")
        valid.write_text(broken)
        result = invoke("--check", "--register", str(valid))
        self.assertEqual(result.returncode, 1)
        self.assertIn("must total 8", result.stderr)

    def test_wrong_clang_format_major_is_rejected(self) -> None:
        fake = self.root / "clang-format"
        fake.write_text("#!/bin/sh\necho 'clang-format version 18.1.0'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--clang-format", str(fake), "--new", "x.c", "--output", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("major version 17", result.stderr)

    def test_custom_style_is_used(self) -> None:
        source = self.root / "value.c"
        source.write_text("int value(void){return 0;}\n")
        style = self.root / "style.yaml"
        style.write_text(
            "BasedOnStyle: LLVM\n"
            "IndentWidth: 2\n"
            "BreakBeforeBraces: Allman\n"
            "AllowShortFunctionsOnASingleLine: None\n"
        )
        result = invoke("--style", str(style), str(source))
        self.assert_success(result)
        self.assertIn("\n{\n  return", source.read_text())


if __name__ == "__main__":
    if CLANG_FORMAT is None:
        raise SystemExit("clang-format is required for c-file-workflow tests")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
