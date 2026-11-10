#!/usr/bin/env python3
"""Tests for validate_spec.py: each planted defect must fail, the clean pack must pass.

Run from the repository root:
    python3 spec/trina-memory/tools/test_validate_spec.py
Standard library only. Every test works on a temporary copy of the pack.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parent
PACK = TOOLS.parent
VALIDATOR = TOOLS / "validate_spec.py"

sys.dont_write_bytecode = True  # keep the pack free of __pycache__
_spec = importlib.util.spec_from_file_location("validate_spec", VALIDATOR)
validate_spec = importlib.util.module_from_spec(_spec)
sys.modules["validate_spec"] = validate_spec
_spec.loader.exec_module(validate_spec)


class PackCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Mirror spec/<pack>/ and the files beside the pack (e.g. spec/workflow.kvx),
        # so links that leave the pack resolve as they do in the repository.
        root = Path(self._tmp.name) / "spec"
        root.mkdir()
        for sibling in PACK.parent.iterdir():
            if sibling.is_file():
                shutil.copy2(sibling, root / sibling.name)
        self.pack = root / PACK.name
        shutil.copytree(PACK, self.pack, ignore=shutil.ignore_patterns("__pycache__"))
        self.spec = self.pack / "spec.kvx"

    def run_validator(self, *args):
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--pack", str(self.pack), *args],
            capture_output=True, text=True, check=False)
        return result.returncode, result.stdout + result.stderr

    def edit(self, section, old, new):
        """Replace `old` with `new` inside one [section] block of the copied spec."""
        text = self.spec.read_text()
        match = re.search(r"(?ms)^\[" + re.escape(section) + r"\]\n.*?(?=^\[|\Z)", text)
        self.assertIsNotNone(match, f"section [{section}] not found")
        block = match.group(0)
        self.assertIn(old, block, f"fixture text not found in [{section}]")
        self.spec.write_text(text[:match.start()] + block.replace(old, new, 1) + text[match.end():])

    def assert_fails(self, expected, *args):
        code, output = self.run_validator(*args)
        self.assertEqual(code, 1, output)
        self.assertRegex(output, expected)
        self.assertIn("FAIL specification-authoring", output)
        receipt = json.loads((self.pack / "authoring-validation.json").read_text())
        self.assertEqual(receipt["status"], "FAIL")
        self.assertTrue(receipt["errors"])
        return output


class UnmodifiedPack(PackCase):
    def test_unmodified_pack_passes(self):
        code, output = self.run_validator()
        self.assertEqual(code, 0, output)
        self.assertNotIn("ERROR", output)
        self.assertIn("PASS specification-authoring", output)
        receipt = json.loads((self.pack / "authoring-validation.json").read_text())
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["scope"], "specification-authoring")
        self.assertEqual(receipt["errors"], [])
        self.assertIs(receipt["product_gates_executed"], False)
        self.assertIn("executes no product gate", receipt["statement"])
        self.assertIs(receipt["rendered"], False)
        self.assertGreater(receipt["acceptance_clauses"], 0)
        self.assertIn("spec.kvx", receipt["inputs_sha256"])
        self.assertEqual(receipt["inputs_sha256"]["spec.kvx"], receipt["spec_sha256"])

    def test_render_is_idempotent_on_clean_pack(self):
        before = {name: (self.pack / name).read_bytes() for name in validate_spec.DERIVED}
        code, output = self.run_validator("--render")
        self.assertEqual(code, 0, output)
        for name, content in before.items():
            self.assertEqual((self.pack / name).read_bytes(), content, name)


class PlantedDefects(PackCase):
    def test_dangling_task_clause_reference(self):
        self.edit("task.0.3", 'reqs = ["11.1", "11.6"]', 'reqs = ["11.1", "11.6", "99.1"]')
        self.assert_fails(r"task 0\.3: reqs references unknown clause 99\.1")

    def test_dangling_task_dependency(self):
        self.edit("task.0.3", 'requires = ["0.1"]', 'requires = ["0.1", "0.99"]')
        self.assert_fails(r"task 0\.3: requires unknown task 0\.99")

    def test_dangling_case_references(self):
        self.edit("case.TRM-002", 'tasks = ["0.3"]', 'tasks = ["0.3", "8.8"]')
        self.edit("case.TRM-002", 'reqs = ["11.1", "11.6"]', 'reqs = ["11.1", "11.6", "11.99"]')
        output = self.assert_fails(r"case TRM-002: tasks references unknown task 8\.8")
        self.assertRegex(output, r"case TRM-002: reqs references unknown clause 11\.99")

    def test_dependency_on_later_wave(self):
        # 1.4 does not depend on 0.4, so this is a wave violation and not a cycle.
        self.edit("task.0.4", 'requires = ["0.1"]', 'requires = ["0.1", "1.4"]')
        output = self.assert_fails(r"task 0\.4: requires 1\.4, which is in a later wave")
        self.assertNotIn("dependency cycle", output)

    def test_dependency_cycle(self):
        self.edit("task.0.2", 'requires = ["0.1"]', 'requires = ["0.1", "0.3"]')
        self.edit("task.0.3", 'requires = ["0.1"]', 'requires = ["0.1", "0.2"]')
        output = self.assert_fails(r"dependency cycle: 0\.2 -> 0\.3 -> 0\.2")
        self.assertNotIn("later wave", output)

    def test_clause_with_no_task(self):
        self.edit("task.6.3", 'reqs = ["11.3", "12.6", "14.5"]', 'reqs = ["11.3", "12.6"]')
        output = self.assert_fails(r"clause 14\.5 is not covered by any task")
        self.assertNotIn("not covered by any case", output)

    def test_clause_with_no_case(self):
        self.edit("case.TRM-024", 'reqs = ["11.3", "12.6", "14.5"]', 'reqs = ["11.3", "12.6"]')
        output = self.assert_fails(r"clause 14\.5 is not covered by any case")
        self.assertNotIn("not covered by any task", output)

    def test_task_lists_case_that_does_not_list_it(self):
        self.edit("task.0.2", 'cases = ["TRM-001"]', 'cases = ["TRM-001", "TRM-002"]')
        self.assert_fails(r"task 0\.2: lists case TRM-002, but case TRM-002 does not list task 0\.2")

    def test_case_lists_task_that_does_not_list_it(self):
        self.edit("case.TRM-001", 'tasks = ["0.2"]', 'tasks = ["0.2", "0.3"]')
        self.assert_fails(r"case TRM-001: lists task 0\.3, but task 0\.3 does not list case TRM-001")

    def test_overlapping_touches_in_one_wave_literal_under_glob(self):
        # 0.2 and 0.3 both depend only on 0.1, so they run in parallel.
        self.edit("task.0.3", 'touches = ["src/trina/config/**"',
                  'touches = ["src/trina/schema/types.py", "src/trina/config/**"')
        self.assert_fails(r"tasks 0\.2 and 0\.3 are parallel in wave 0 with overlapping touches")

    def test_overlapping_touches_in_one_wave_identical_literal(self):
        self.edit("task.0.4", 'touches = ["src/trina/security/**"',
                  'touches = ["tests/cases/test_0_3.py", "src/trina/security/**"')
        self.assert_fails(r"tasks 0\.3 and 0\.4 are parallel in wave 0 with overlapping "
                          r"touches: 'tests/cases/test_0_3\.py'")

    def test_shared_touches_allowed_along_a_dependency_path(self):
        # 0.2 requires 0.1, so they are sequential; the only defect is the stale view.
        self.edit("task.0.2", 'touches = ["src/trina/schema/**"',
                  'touches = ["pyproject.toml", "src/trina/schema/**"')
        output = self.assert_fails(r"derived view is stale")
        self.assertNotIn("overlapping touches", output)

    def test_broken_markdown_link(self):
        with (self.pack / "baseline.md").open("a") as handle:
            handle.write("\nSee [the missing page](no-such-document.md).\n")
        self.assert_fails(r"baseline\.md:\d+: broken link to no-such-document\.md")

    def test_http_and_anchor_links_are_ignored(self):
        with (self.pack / "baseline.md").open("a") as handle:
            handle.write("\n[web](https://example.invalid/x.md) [anchor](#workspace) "
                         "[ok](spec.kvx#meta) `[code](not-a-link.md)`\n")
        code, output = self.run_validator()
        self.assertEqual(code, 0, output)

    def test_stale_derived_view(self):
        with (self.pack / "implementation-plan.md").open("a") as handle:
            handle.write("hand edit\n")
        self.assert_fails(r"implementation-plan\.md: derived view is stale")
        code, output = self.run_validator("--render")
        self.assertEqual(code, 0, output)
        code, output = self.run_validator()
        self.assertEqual(code, 0, output)

    def test_stale_view_after_spec_change(self):
        self.edit("task.0.2", 'title = "Memory, query', 'title = "Renamed memory, query')
        self.edit("req.1", 'title = "Domain-agnostic', 'title = "Renamed domain-agnostic')
        output = self.assert_fails(r"implementation-plan\.md: derived view is stale")
        self.assertRegex(output, r"traceability\.md: derived view is stale")

    def test_missing_derived_view(self):
        (self.pack / "traceability.md").unlink()
        self.assert_fails(r"traceability\.md: derived view is missing")

    def test_duplicate_key(self):
        self.edit("task.0.1", 'wave = 0\n', 'wave = 0\ntitle = "Second title"\n')
        self.assert_fails(r"spec\.kvx:\d+: duplicate key 'title' in \[task\.0\.1\]")

    def test_duplicate_section(self):
        with self.spec.open("a") as handle:
            handle.write('\n[meta]\nfeature = "again"\n')
        self.assert_fails(r"spec\.kvx:\d+: duplicate section \[meta\]")

    def test_unterminated_string(self):
        self.edit("task.0.1", 'owner = "foundation"', 'owner = "foundation')
        self.assert_fails(r"spec\.kvx:\d+: key 'owner': unterminated string")

    def test_unknown_value_syntax(self):
        self.edit("task.0.1", "wave = 0", "wave = zero")
        self.assert_fails(r"spec\.kvx:\d+: key 'wave': unknown value syntax")

    def test_wave_must_match_task_id(self):
        self.edit("task.1.2", "wave = 1", "wave = 2")
        self.assert_fails(r"task 1\.2: wave 2 does not match id prefix 1")

    def test_unknown_statuses_and_tier(self):
        self.edit("task.0.1", 'status = "pending"', 'status = "finished"')
        self.edit("case.TRM-001", 'status = "UNRUN"', 'status = "PASSED"')
        self.edit("case.TRM-002", 'tier = "component"', 'tier = "unit"')
        output = self.assert_fails(r"task 0\.1: unknown status 'finished'")
        self.assertRegex(output, r"case TRM-001: status must be one of")
        self.assertRegex(output, r"case TRM-002: tier must be one of")

    def test_noncontiguous_acceptance_clauses(self):
        text = self.spec.read_text()
        self.assertIn("\nac_2 = ", text)
        self.edit("req.7", "ac_2 = ", "ac_9 = ")
        self.assert_fails(r"req 7: ac_M numbering is not contiguous")

    def test_missing_required_fields(self):
        self.edit("meta", 'version = "0.1.0"\n', "")
        self.edit("task.0.1", 'owner = "foundation"\n', "")
        self.edit("req.1", 'story = "', 'tale = "')
        output = self.assert_fails(r"meta: missing required key 'version'")
        self.assertRegex(output, r"task 0\.1: missing owner")
        self.assertRegex(output, r"req 1: missing story")

    def test_missing_contract_document_warns_unless_strict(self):
        target = self.pack / "data.md"
        if target.exists():
            target.unlink()
        code, output = self.run_validator()
        self.assertRegex(output, r"WARN\s+data\.md: contract document")
        self.assertNotRegex(output, r"ERROR.*data\.md")
        receipt = json.loads((self.pack / "authoring-validation.json").read_text())
        self.assertIn("data.md", receipt["pending_documents"])
        code, output = self.run_validator("--strict")
        self.assertEqual(code, 1, output)
        self.assertRegex(output, r"ERROR data\.md: contract document")


class ParserUnit(unittest.TestCase):
    def test_values(self):
        doc = validate_spec.parse_kvx(
            '# comment\n[a.b]\ns = "x # not a comment \\" q"  # trailing\n'
            'n = 3\nl = ["a", "b"]\naligned   = "v"\nempty = []\n', "t.kvx")
        self.assertEqual(doc.errors, [])
        self.assertEqual(doc.sections["a.b"], {
            "s": 'x # not a comment " q', "n": 3, "l": ["a", "b"], "aligned": "v", "empty": []})
        self.assertEqual(doc.section_lines["a.b"], 2)

    def test_defects_carry_file_and_line(self):
        doc = validate_spec.parse_kvx(
            'orphan = 1\n[s]\nk = "open\nk2 = bare\nk3 = ["a", "b"\nk4 = 1\nk4 = 2\n'
            'k5 = "a" junk\n[s]\n[bad section\nnot a pair\n', "t.kvx")
        joined = "\n".join(doc.errors)
        for expected in ("t.kvx:1: key 'orphan' appears outside any section",
                         "t.kvx:3: key 'k': unterminated string",
                         "t.kvx:4: key 'k2': unknown value syntax",
                         "t.kvx:5: key 'k3': unterminated array",
                         "t.kvx:7: duplicate key 'k4'",
                         "t.kvx:8: key 'k5': unexpected text after value",
                         "t.kvx:9: duplicate section [s]",
                         "t.kvx:10: malformed section header",
                         "t.kvx:11: expected 'key = value'"):
            self.assertIn(expected, joined)

    def test_touches_overlap(self):
        overlap = validate_spec.touches_overlap
        self.assertTrue(overlap("src/a.py", "src/a.py"))
        self.assertTrue(overlap("src/pkg/**", "src/pkg/mod/x.py"))
        self.assertTrue(overlap("docs/**", "docs/guide/*.md"))
        self.assertTrue(overlap("configs/eval/**", "configs/eval/ablation.toml"))
        self.assertTrue(overlap("src/pkg", "src/pkg/x.py"))
        self.assertTrue(overlap("src", "src/pkg/**"))
        self.assertTrue(overlap("configs/train/retriever*.toml", "configs/train/retriever-tiny.toml"))
        self.assertFalse(overlap("src/a.py", "src/b.py"))
        self.assertFalse(overlap("src/pkg/**", "src/pkgother/x.py"))
        self.assertFalse(overlap("configs/train/retriever*.toml", "configs/train/selector*.toml"))
        self.assertFalse(overlap("configs/train/retriever*.toml", "configs/train/base.toml"))
        self.assertFalse(overlap("src/*.py", "src/*.md"))


if __name__ == "__main__":
    unittest.main()
