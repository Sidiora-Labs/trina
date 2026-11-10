#!/usr/bin/env python3
"""Validate the trina-memory specification pack. Authoring checks only.

Parses spec.kvx with a strict, self-contained KVX parser, checks references,
dependency order, clause coverage, parallel scope overlap and markdown links,
compares (or with --render regenerates) the derived views
implementation-plan.md and traceability.md, and writes the receipt
authoring-validation.json.

This tool never executes a task's verify_cmd or any other product gate.
Standard library only; Python 3.12.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


DEFAULT_PACK = Path(__file__).resolve().parents[1]
SPEC_NAME = "spec.kvx"
RECEIPT_NAME = "authoring-validation.json"
DERIVED = ("implementation-plan.md", "traceability.md")
STATEMENT = ("This receipt validates specification authoring only. It executes no "
             "product gate: no verify_cmd was run and no case status is evidence.")

# Requirement number -> contract document that owns it.
CONTRACTS = {
    1: "design.body.md", 2: "data.md", 3: "security.md", 4: "data.md",
    5: "data.md", 6: "labeling.md", 7: "models.md", 8: "models.md",
    9: "models.md", 10: "models.md", 11: "training.md", 12: "evaluation.md",
    13: "serving.md", 14: "remote-execution.md", 15: "README.md",
}

REQUIRED_META = ("feature", "name", "version", "status", "stage")
TASK_STATUSES = ("pending", "in_progress", "blocked", "implemented", "done", "superseded")
CASE_STATUSES = ("UNRUN", "SKIPPED", "PASS", "FAIL")
CASE_TIERS = ("component", "integration", "system", "campaign")
PLAIN_SECTIONS = {"meta", "decisions", "design", "documentation", "tasks"}
INPUT_SUFFIXES = {".md", ".kvx", ".py", ".json", ".mmd"}

SECTION_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
INT_RE = re.compile(r"[+-]?\d+")
ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "/": "/"}


# --------------------------------------------------------------------------
# Strict KVX parser
# --------------------------------------------------------------------------

class KvxSyntaxError(ValueError):
    """A defect in one KVX line."""


class Kvx:
    """Parsed KVX document with source line numbers for sections and keys."""

    def __init__(self, name: str):
        self.name = name
        self.sections: dict[str, dict] = {}
        self.section_lines: dict[str, int] = {}
        self.key_lines: dict[tuple[str, str], int] = {}
        self.errors: list[str] = []

    def where(self, section: str, key: str | None = None) -> str:
        line = self.key_lines.get((section, key)) if key else None
        line = line or self.section_lines.get(section)
        return f"{self.name}:{line}" if line else self.name


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    return pos


def _parse_string(text: str, pos: int) -> tuple[str, int]:
    out = []
    pos += 1
    while pos < len(text):
        char = text[pos]
        if char == '"':
            return "".join(out), pos + 1
        if char == "\\":
            pos += 1
            if pos >= len(text):
                break
            esc = text[pos]
            if esc in ESCAPES:
                out.append(ESCAPES[esc])
            elif esc == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", text[pos + 1:pos + 5]):
                out.append(chr(int(text[pos + 1:pos + 5], 16)))
                pos += 4
            else:
                raise KvxSyntaxError(f"unknown string escape \\{esc}")
        else:
            out.append(char)
        pos += 1
    raise KvxSyntaxError("unterminated string")


def _parse_scalar(text: str, pos: int) -> tuple[object, int]:
    if pos >= len(text):
        raise KvxSyntaxError("missing value")
    if text[pos] == '"':
        return _parse_string(text, pos)
    match = re.compile(r"[^\s,\]#]+").match(text, pos)
    token = match.group(0) if match else ""
    if INT_RE.fullmatch(token):
        return int(token), match.end()
    if token in ("true", "false"):
        return token == "true", match.end()
    raise KvxSyntaxError(f"unknown value syntax: {text[pos:].strip()[:40]!r}")


def _parse_value(text: str, pos: int) -> tuple[object, int]:
    if pos < len(text) and text[pos] == "[":
        items: list = []
        pos = _skip_space(text, pos + 1)
        if pos < len(text) and text[pos] == "]":
            return items, pos + 1
        while True:
            if pos >= len(text):
                raise KvxSyntaxError("unterminated array")
            if text[pos] == "[":
                raise KvxSyntaxError("nested arrays are not supported")
            item, pos = _parse_scalar(text, pos)
            items.append(item)
            pos = _skip_space(text, pos)
            if pos >= len(text):
                raise KvxSyntaxError("unterminated array")
            if text[pos] == "]":
                return items, pos + 1
            if text[pos] != ",":
                raise KvxSyntaxError("expected ',' or ']' in array")
            pos = _skip_space(text, pos + 1)
            if pos < len(text) and text[pos] == "]":  # trailing comma
                return items, pos + 1
    return _parse_scalar(text, pos)


def parse_kvx(text: str, name: str = SPEC_NAME) -> Kvx:
    """Parse KVX text. Defects are collected in .errors as 'file:line: message'."""
    doc = Kvx(name)
    current: str | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        here = f"{name}:{number}"
        if line.startswith("["):
            match = re.fullmatch(r"\[([^\[\]]*)\]\s*(#.*)?", line)
            if not match or not SECTION_RE.fullmatch(match.group(1).strip()):
                doc.errors.append(f"{here}: malformed section header: {line[:60]}")
                current = None
                continue
            current = match.group(1).strip()
            if current in doc.sections:
                doc.errors.append(f"{here}: duplicate section [{current}] "
                                  f"(first at line {doc.section_lines[current]})")
            else:
                doc.sections[current] = {}
                doc.section_lines[current] = number
            continue
        key, sep, rest = line.partition("=")
        key = key.strip()
        if not sep or not KEY_RE.fullmatch(key):
            doc.errors.append(f"{here}: expected 'key = value' or '[section]': {line[:60]}")
            continue
        if current is None:
            doc.errors.append(f"{here}: key '{key}' appears outside any section")
            continue
        try:
            value, pos = _parse_value(rest, _skip_space(rest, 0))
            pos = _skip_space(rest, pos)
            if pos < len(rest) and rest[pos] != "#":
                raise KvxSyntaxError(f"unexpected text after value: {rest[pos:][:40]!r}")
        except KvxSyntaxError as exc:
            doc.errors.append(f"{here}: key '{key}': {exc}")
            continue
        if key in doc.sections[current]:
            doc.errors.append(f"{here}: duplicate key '{key}' in [{current}] "
                              f"(first at line {doc.key_lines[(current, key)]})")
            continue
        doc.sections[current][key] = value
        doc.key_lines[(current, key)] = number
    return doc


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def numeric(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def numbered(section: dict, prefix: str) -> list[int]:
    return sorted(int(key[len(prefix):]) for key in section
                  if re.fullmatch(re.escape(prefix) + r"\d+", key))


def nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value) -> bool:
    return isinstance(value, list) and all(nonempty_string(item) for item in value)


def refs(section: dict, key: str) -> list[str]:
    """A list field as strings, tolerant of invalid input (already reported)."""
    value = section.get(key, [])
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def is_int(value) -> bool:
    return type(value) is int


GLOB_RE = re.compile(r"[*?\[]")


def _glob_regex(pattern: str) -> re.Pattern:
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out))


def _norm_path(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def touches_overlap(first: str, second: str) -> bool:
    """Conservative overlap test between two scope entries (literal or glob)."""
    a, b = _norm_path(first), _norm_path(second)
    glob_a, glob_b = GLOB_RE.search(a), GLOB_RE.search(b)
    if not glob_a and not glob_b:
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")
    if glob_a and glob_b:
        pre_a, pre_b = a[:glob_a.start()], b[:glob_b.start()]
        if not (pre_a.startswith(pre_b) or pre_b.startswith(pre_a)):
            return False
        suf_a = re.split(r"[*?\]]", a)[-1]
        suf_b = re.split(r"[*?\]]", b)[-1]
        # Different literal file suffixes (e.g. *.py and *.md) cannot match one path.
        return suf_a.endswith(suf_b) or suf_b.endswith(suf_a)
    pattern, literal, found = (a, b, glob_a) if glob_a else (b, a, glob_b)
    if _glob_regex(pattern).fullmatch(literal):
        return True
    prefix = pattern[:found.start()]
    # A literal directory that contains (or is) the glob's base directory.
    return prefix.startswith(literal + "/") or prefix.rstrip("/") == literal


# --------------------------------------------------------------------------
# Specification checks
# --------------------------------------------------------------------------

class Model:
    def __init__(self, doc: Kvx):
        self.doc = doc
        self.meta = doc.sections.get("meta", {})
        self.requirements: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.cases: dict[str, dict] = {}
        self.clauses: list[str] = []


def ancestors(tasks: dict, ident: str) -> set[str]:
    seen: set[str] = set()
    stack = [ident]
    while stack:
        for dep in refs(tasks.get(stack.pop(), {}), "requires"):
            if dep in tasks and dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def find_cycles(tasks: dict) -> list[list[str]]:
    """Return one representative path per dependency cycle found by DFS."""
    state: dict[str, int] = {}
    cycles: list[list[str]] = []
    seen_sets: set[frozenset] = set()

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        path.append(node)
        for dep in sorted(set(refs(tasks[node], "requires")), key=numeric):
            if dep not in tasks:
                continue
            if state.get(dep) == 1:
                cycle = path[path.index(dep):] + [dep]
                if frozenset(cycle) not in seen_sets:
                    seen_sets.add(frozenset(cycle))
                    cycles.append(cycle)
            elif dep not in state:
                visit(dep, path)
        path.pop()
        state[node] = 2

    for ident in sorted(tasks, key=numeric):
        if ident not in state:
            visit(ident, [])
    return cycles


def validate_spec(doc: Kvx) -> tuple[list[str], list[str], Model]:
    errors: list[str] = []
    warnings: list[str] = []
    model = Model(doc)
    sections = doc.sections

    def err(section: str, message: str, key: str | None = None) -> None:
        errors.append(f"{doc.where(section, key)}: {message}")

    # Section classification.
    for name in sections:
        if name in PLAIN_SECTIONS:
            continue
        kind, _, ident = name.partition(".")
        if kind == "req" and re.fullmatch(r"[1-9]\d*", ident):
            model.requirements[ident] = sections[name]
        elif kind == "task" and re.fullmatch(r"\d+\.[1-9]\d*", ident):
            model.tasks[ident] = sections[name]
        elif kind == "case" and re.fullmatch(r"[A-Z][A-Z0-9]*-\d{3}", ident):
            model.cases[ident] = sections[name]
        elif kind in ("req", "task", "case"):
            err(name, f"malformed {kind} section id [{name}]")
        else:
            warnings.append(f"{doc.where(name)}: unknown section [{name}] is not checked")
    requirements, tasks, cases = model.requirements, model.tasks, model.cases

    # Meta.
    if "meta" not in sections:
        errors.append(f"{doc.name}: missing [meta] section")
    for key in REQUIRED_META:
        if "meta" in sections and not nonempty_string(model.meta.get(key)):
            err("meta", f"meta: missing required key '{key}'")
    if not requirements:
        errors.append(f"{doc.name}: no [req.N] sections")
    if not tasks:
        errors.append(f"{doc.name}: no [task.W.N] sections")
    if not cases:
        errors.append(f"{doc.name}: no [case.ID] sections")

    # Requirements and clauses.
    for ident in sorted(requirements, key=int):
        req, name = requirements[ident], f"req.{ident}"
        for field in ("title", "story"):
            if not nonempty_string(req.get(field)):
                err(name, f"req {ident}: missing {field}")
        acs = numbered(req, "ac_")
        if not acs:
            err(name, f"req {ident}: needs at least one ac_M clause")
        elif acs != list(range(1, len(acs) + 1)):
            err(name, f"req {ident}: ac_M numbering is not contiguous from 1 "
                      f"(found {', '.join(map(str, acs))})")
        for number in acs:
            if not nonempty_string(req[f"ac_{number}"]):
                err(name, f"req {ident}.{number}: clause text must be a non-empty string",
                    f"ac_{number}")
            model.clauses.append(f"{ident}.{number}")
        if int(ident) not in CONTRACTS:
            err(name, f"req {ident}: no owning contract document in CONTRACTS")
    clauses = set(model.clauses)

    # Tasks.
    for ident in sorted(tasks, key=numeric):
        task, name = tasks[ident], f"task.{ident}"
        for field in ("title", "owner", "verify_cmd"):
            if not nonempty_string(task.get(field)):
                err(name, f"task {ident}: missing {field}")
        if "status" not in task:
            err(name, f"task {ident}: missing status")
        elif task["status"] not in TASK_STATUSES:
            err(name, f"task {ident}: unknown status {task['status']!r} "
                      f"(allowed: {', '.join(TASK_STATUSES)})", "status")
        if not is_int(task.get("wave")):
            err(name, f"task {ident}: wave must be an integer", "wave")
        elif task["wave"] != int(ident.split(".")[0]):
            err(name, f"task {ident}: wave {task['wave']} does not match id prefix "
                      f"{ident.split('.')[0]}", "wave")
        for field in ("touches", "reqs", "cases"):
            value = task.get(field)
            if field not in task:
                err(name, f"task {ident}: missing {field}")
            elif not string_list(value) or not value:
                err(name, f"task {ident}: {field} must be a non-empty list of strings", field)
            elif len(value) != len(set(value)):
                err(name, f"task {ident}: duplicate entry in {field}", field)
        if "requires" in task:
            if not string_list(task["requires"]):
                err(name, f"task {ident}: requires must be a list of task ids", "requires")
            elif len(task["requires"]) != len(set(task["requires"])):
                err(name, f"task {ident}: duplicate entry in requires", "requires")
        steps = numbered(task, "do_")
        if not steps:
            err(name, f"task {ident}: needs at least one do_N obligation")
        elif steps != list(range(1, len(steps) + 1)):
            err(name, f"task {ident}: do_N numbering is not contiguous from 1")
        for number in steps:
            if not nonempty_string(task[f"do_{number}"]):
                err(name, f"task {ident}: do_{number} must be a non-empty string", f"do_{number}")
        for path in refs(task, "touches"):
            if path.startswith("/") or ".." in path.split("/"):
                err(name, f"task {ident}: scope escapes the repository: {path}", "touches")
        for dep in refs(task, "requires"):
            if dep not in tasks:
                err(name, f"task {ident}: requires unknown task {dep}", "requires")
            elif (is_int(task.get("wave")) and is_int(tasks[dep].get("wave"))
                  and tasks[dep]["wave"] > task["wave"]):
                err(name, f"task {ident}: requires {dep}, which is in a later wave "
                          f"({tasks[dep]['wave']} > {task['wave']})", "requires")
        for ref in refs(task, "reqs"):
            if ref not in clauses:
                err(name, f"task {ident}: reqs references unknown clause {ref}", "reqs")
        for ref in refs(task, "cases"):
            if ref not in cases:
                err(name, f"task {ident}: cases references unknown case {ref}", "cases")
            elif ident not in refs(cases[ref], "tasks"):
                err(name, f"task {ident}: lists case {ref}, but case {ref} does not list "
                          f"task {ident} (task/case mismatch)", "cases")

    for cycle in find_cycles(tasks):
        err(f"task.{cycle[0]}", "dependency cycle: " + " -> ".join(cycle), "requires")

    # Cases.
    for ident in sorted(cases):
        case, name = cases[ident], f"case.{ident}"
        for field in ("title", "oracle"):
            if not nonempty_string(case.get(field)):
                err(name, f"case {ident}: missing {field}")
        if case.get("status") not in CASE_STATUSES:
            err(name, f"case {ident}: status must be one of {', '.join(CASE_STATUSES)} "
                      f"(found {case.get('status')!r})", "status")
        if case.get("tier") not in CASE_TIERS:
            err(name, f"case {ident}: tier must be one of {', '.join(CASE_TIERS)} "
                      f"(found {case.get('tier')!r})", "tier")
        for field in ("reqs", "tasks"):
            value = case.get(field)
            if field not in case:
                err(name, f"case {ident}: missing {field}")
            elif not string_list(value) or not value:
                err(name, f"case {ident}: {field} must be a non-empty list of strings", field)
            elif len(value) != len(set(value)):
                err(name, f"case {ident}: duplicate entry in {field}", field)
        for ref in refs(case, "reqs"):
            if ref not in clauses:
                err(name, f"case {ident}: reqs references unknown clause {ref}", "reqs")
        for ref in refs(case, "tasks"):
            if ref not in tasks:
                err(name, f"case {ident}: tasks references unknown task {ref}", "tasks")
            elif ident not in refs(tasks[ref], "cases"):
                err(name, f"case {ident}: lists task {ref}, but task {ref} does not list "
                          f"case {ident} (task/case mismatch)", "tasks")

    # Clause coverage.
    by_task = {ref for task in tasks.values() for ref in refs(task, "reqs")}
    by_case = {ref for case in cases.values() for ref in refs(case, "reqs")}
    for clause in model.clauses:
        number, _, field = clause.partition(".")
        if clause not in by_task:
            err(f"req.{number}", f"clause {clause} is not covered by any task", f"ac_{field}")
        if clause not in by_case:
            err(f"req.{number}", f"clause {clause} is not covered by any case", f"ac_{field}")

    # Parallel scope overlap: same wave, no dependency path either way.
    closure = {ident: ancestors(tasks, ident) for ident in tasks}
    ordered = sorted(tasks, key=numeric)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if not is_int(tasks[first].get("wave")) or tasks[first].get("wave") != tasks[second].get("wave"):
                continue
            if first in closure[second] or second in closure[first]:
                continue
            pairs = sorted({(a, b) for a in refs(tasks[first], "touches")
                            for b in refs(tasks[second], "touches") if touches_overlap(a, b)})
            for a, b in pairs:
                shown = f"'{a}'" if a == b else f"'{a}' and '{b}'"
                err(f"task.{second}", f"tasks {first} and {second} are parallel in wave "
                                      f"{tasks[first]['wave']} with overlapping touches: {shown}",
                    "touches")
    return errors, warnings, model


# --------------------------------------------------------------------------
# Derived views
# --------------------------------------------------------------------------

def task_order(tasks: dict) -> list[str]:
    """Wave order; inside a wave, dependencies first, then numeric id."""
    ordered: list[str] = []
    for wave in sorted({task["wave"] for task in tasks.values()}):
        remaining = {ident for ident, task in tasks.items() if task["wave"] == wave}
        while remaining:
            ready = sorted((ident for ident in remaining
                            if not set(refs(tasks[ident], "requires")) & remaining), key=numeric)
            if not ready:
                raise ValueError("task graph cannot be ordered")
            ordered.append(ready[0])
            remaining.remove(ready[0])
    return ordered


def cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_plan(model: Model) -> str:
    feature = model.meta["feature"]
    tasks, cases = model.tasks, model.cases
    heading = model.doc.sections.get("tasks", {}).get("heading")
    if not nonempty_string(heading):
        heading = f"Implementation plan: {feature}"
    lines = [
        f"# {heading}", "",
        "Generated from [spec.kvx](spec.kvx); regenerate with "
        f"`python3 spec/{feature}/tools/validate_spec.py --render`. Do not edit by hand.", "",
        "Task statuses below record current implementation progress. "
        "Verification commands are task deliverables; a command being listed is not "
        "evidence that it exists or passes.", "",
        "Dependencies determine eligibility. Tasks in one wave with no dependency path "
        "between them run in parallel and have disjoint scopes. Component success does "
        "not qualify a complete product case or campaign.", "",
        "| Task | Wave | Scope | Requires | Owner | Status |",
        "|---|---:|---|---|---|---|",
    ]
    order = task_order(tasks)
    for ident in order:
        task = tasks[ident]
        lines.append(f"| {ident} | {task['wave']} | {cell(task['title'])} | "
                     f"{', '.join(refs(task, 'requires')) or 'None'} | {cell(task['owner'])} | "
                     f"{task['status']} |")
    for ident in order:
        task = tasks[ident]
        lines += ["", f"## {ident}: {task['title']}", "",
                  f"Status: **{task['status']}**. Owner: `{task['owner']}`. "
                  f"Dependencies: {', '.join(refs(task, 'requires')) or 'none'}.", "",
                  "Implementation scope:", ""]
        lines += [f"- `{path}`" for path in task["touches"]]
        lines += ["", f"Focused gate: `{task['verify_cmd']}`.", "",
                  "Acceptance clauses: " + ", ".join(task["reqs"]) + ".",
                  "Canonical cases: " + ", ".join(
                      f"{case} ({cases[case]['status']})" for case in task["cases"]) + ".", "",
                  "Implementation and qualification obligations:", ""]
        lines += [f"{index}. {task[f'do_{number}']}"
                  for index, number in enumerate(numbered(task, "do_"), 1)]
    return "\n".join(lines) + "\n"


def render_traceability(model: Model) -> str:
    feature = model.meta["feature"]
    requirements, tasks, cases = model.requirements, model.tasks, model.cases
    lines = [
        f"# Traceability: {feature}", "",
        "Generated from [spec.kvx](spec.kvx); regenerate with "
        f"`python3 spec/{feature}/tools/validate_spec.py --render`. Do not edit by hand. "
        "A clause `N.M` is `[req.N] ac_M`. Mappings describe coverage ownership, not "
        "execution evidence.", "",
        "| Clause | Requirement | Implementation tasks | Product cases | Contract |",
        "|---|---|---|---|---|",
    ]
    for number in sorted(requirements, key=int):
        req = requirements[number]
        contract = CONTRACTS[int(number)]
        for field in numbered(req, "ac_"):
            clause = f"{number}.{field}"
            owners = [key for key in sorted(tasks, key=numeric) if clause in tasks[key]["reqs"]]
            checks = [key for key in sorted(cases) if clause in cases[key]["reqs"]]
            lines.append(f"| {clause} | {cell(req['title'])} | {', '.join(owners)} | "
                         f"{', '.join(checks)} | [{contract}]({contract}) |")
    lines += ["", "## Canonical case ownership", "",
              "A case's tasks are the implementation packages responsible for its driver, "
              "behavior or final integration. A case status is authored state in spec.kvx; "
              "a component passing never certifies a full case.", "",
              "| Case | Scope | Status | Tasks | Independent oracle |", "|---|---|---|---|---|"]
    for ident in sorted(cases):
        case = cases[ident]
        lines.append(f"| {ident} | {cell(case['title'])} ({case['tier']}) | {case['status']} | "
                     f"{', '.join(case['tasks'])} | {cell(case['oracle'])} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

LINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\s*\)")


def pack_files(pack: Path) -> list[Path]:
    return [path for path in sorted(pack.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
            and not any(part.startswith(".") for part in path.relative_to(pack).parts)]


def check_documents(pack: Path, strict: bool) -> tuple[list[str], list[str], list[str]]:
    """Return (errors, warnings, pending_documents)."""
    errors: list[str] = []
    warnings: list[str] = []
    pending = sorted({name for name in CONTRACTS.values() if not (pack / name).is_file()})
    pending_paths = {(pack / name).resolve() for name in pending}
    derived_paths = {(pack / name).resolve() for name in DERIVED}
    pending_links: dict[str, int] = {}
    for path in pack_files(pack):
        if path.suffix != ".md":
            continue
        relative = path.relative_to(pack).as_posix()
        fenced = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced:
                continue
            for target in LINK_RE.findall(re.sub(r"`[^`]*`", "", line)):
                parsed = urlsplit(target.strip("<>"))
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                if parsed.path.startswith("/"):
                    errors.append(f"{relative}:{number}: absolute link target {target}")
                    continue
                resolved = (path.parent / unquote(parsed.path)).resolve()
                if resolved.exists():
                    continue
                if resolved in derived_paths:
                    continue  # reported once as a missing derived view
                if resolved in pending_paths and not strict:
                    pending_links[resolved.name] = pending_links.get(resolved.name, 0) + 1
                    continue
                errors.append(f"{relative}:{number}: broken link to {target}")
    missing = []
    for name in pending:
        owners = ", ".join(str(n) for n, doc in sorted(CONTRACTS.items()) if doc == name)
        message = f"{name}: contract document for requirement(s) {owners} does not exist yet"
        if pending_links.get(name):
            message += f" ({pending_links[name]} link(s) point at it)"
        missing.append(message)
    if strict:
        errors = missing + errors
    else:
        warnings = missing + warnings
    return errors, warnings, pending


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(pack: Path, render: bool, strict: bool) -> tuple[dict, int]:
    errors: list[str] = []
    warnings: list[str] = []
    pending: list[str] = []
    spec_path = pack / SPEC_NAME
    model = None
    if not spec_path.is_file():
        errors.append(f"{SPEC_NAME}: specification not found in {pack}")
    else:
        try:
            doc = parse_kvx(spec_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            errors.append(f"{SPEC_NAME}: not valid UTF-8: {exc}")
        else:
            errors += doc.errors
            spec_errors, spec_warnings, model = validate_spec(doc)
            errors += spec_errors
            warnings += spec_warnings

    rendered = False
    if model is not None and not errors:
        views = {DERIVED[0]: render_plan(model), DERIVED[1]: render_traceability(model)}
        for name, content in views.items():
            path = pack / name
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if render:
                if current != content:
                    path.write_text(content, encoding="utf-8")
            elif current is None:
                errors.append(f"{name}: derived view is missing; run with --render")
            elif current != content:
                errors.append(f"{name}: derived view is stale; run with --render")
        rendered = render
    elif render:
        warnings.append("derived views were not rendered because the specification has errors")

    if pack.is_dir():
        doc_errors, doc_warnings, pending = check_documents(pack, strict)
        errors += doc_errors
        warnings += doc_warnings

    requirements = model.requirements if model else {}
    tasks = model.tasks if model else {}
    cases = model.cases if model else {}

    def tally(items: dict) -> dict:
        values = [item.get("status") for item in items.values() if isinstance(item.get("status"), str)]
        return {status: values.count(status) for status in sorted(set(values))}

    receipt = {
        "status": "FAIL" if errors else "PASS",
        "scope": "specification-authoring",
        "statement": STATEMENT,
        "product_gates_executed": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "feature": model.meta.get("feature") if model else None,
        "spec_sha256": sha256(spec_path) if spec_path.is_file() else None,
        "requirements": len(requirements),
        "acceptance_clauses": len(model.clauses) if model else 0,
        "tasks": len(tasks),
        "cases": len(cases),
        "waves": sorted({task["wave"] for task in tasks.values() if is_int(task.get("wave"))}),
        "task_statuses": tally(tasks),
        "case_statuses": tally(cases),
        "rendered": rendered,
        "strict": strict,
        "errors": errors,
        "warnings": warnings,
        "pending_documents": pending,
        "inputs_sha256": {
            path.relative_to(pack).as_posix(): sha256(path)
            for path in (pack_files(pack) if pack.is_dir() else [])
            if path.suffix in INPUT_SUFFIXES and path.name != RECEIPT_NAME
        },
    }
    return receipt, int(bool(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the specification pack (authoring only; runs no product gate).")
    parser.add_argument("--render", action="store_true",
                        help="regenerate implementation-plan.md and traceability.md")
    parser.add_argument("--strict", action="store_true",
                        help="treat missing contract documents as errors")
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK,
                        help="pack directory to validate (default: this tool's pack)")
    parser.add_argument("--no-receipt", action="store_true",
                        help=f"do not write {RECEIPT_NAME}")
    args = parser.parse_args(argv)
    pack = args.pack.resolve()
    receipt, code = run(pack, args.render, args.strict)
    if not args.no_receipt and pack.is_dir():
        (pack / RECEIPT_NAME).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    for message in receipt["errors"]:
        print(f"ERROR {message}")
    for message in receipt["warnings"]:
        print(f"WARN  {message}")
    waves = receipt["waves"]
    print(f"{receipt['status']} specification-authoring {receipt['feature'] or '?'}: "
          f"{receipt['requirements']} requirements, {receipt['acceptance_clauses']} clauses, "
          f"{receipt['tasks']} tasks, {receipt['cases']} cases, "
          f"waves {waves[0]}-{waves[-1]}; " if waves else
          f"{receipt['status']} specification-authoring {receipt['feature'] or '?'}: ", end="")
    print(f"{len(receipt['errors'])} error(s), {len(receipt['warnings'])} warning(s), "
          f"{len(receipt['pending_documents'])} pending document(s); "
          f"rendered={str(receipt['rendered']).lower()}; no product gate executed")
    return code


if __name__ == "__main__":
    sys.exit(main())
