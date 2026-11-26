"""The procedure guides in references/: lean, consistent with the CLI and the schemas, safe."""

import argparse
import json
import os
import re
import unittest

from tests import support as S
from tests.unit.test_skill import SCHEMA_DIR, property_names, schema, split_skill
from evermap_lib import cli
from evermap_lib import model as M
from evermap_lib import rules

REFERENCES = os.path.join(S.SKILL, "references")
GUIDES = ["discover.md", "update.md", "functional.md", "decisions.md", "assess.md"]
CALLING = ["discover.md", "update.md", "decisions.md", "assess.md"]  # functional.md is for workers only
TEMPLATE = os.path.join(S.SKILL, "assets", "templates", "assessment.md")
HOST = ("claude -p", "codex exec")


def text(name):
    with open(os.path.join(REFERENCES, name), encoding="utf-8") as handle:
        return handle.read()


def fenced(markdown):
    """(info string, body) for every fenced block."""
    return re.findall(r"^```([^\n]*)\n(.*?)^```", markdown, re.M | re.S)


def prose(markdown):
    """The text outside fenced blocks."""
    return re.sub(r"^```[^\n]*\n.*?^```", "", markdown, flags=re.M | re.S)


def split_model_section(heading):
    return text("model.md").split("\n## " + heading + "\n", 1)[1].split("\n## ", 1)[0]


def inline(markdown):
    return re.findall(r"`([^`\n]+)`", prose(markdown))


def subparsers():
    action = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices


def options(parser):
    return {o for a in parser._actions for o in a.option_strings}


def schema_words():
    words = set()
    for name in os.listdir(SCHEMA_DIR):
        node = schema(name)
        words |= property_names(node, name, set())
        stack = [node]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                words |= {v for v in item.get("enum", []) if isinstance(v, str)}
                stack += list(item.values())
            elif isinstance(item, list):
                stack += item
    return words


BASES = {
    "operation": S.element("operation:parcel.collect", "Collect parcel", "Checks the code and frees the locker.",
                           plain="A recipient takes a parcel.", parent="component:locker-api",
                           trigger={"type": "http", "detail": "POST /parcels/<id>/collect"}, exposure="public",
                           evidence=S.ev("api/collect.py", [10, 17], "collect_parcel")),
    "rule": S.element("rule:collection.window", "Collection window", "Parcels expire after some days.",
                      plain="A parcel waits for a limited time.", type="eligibility",
                      statement="days since storage must not exceed max_days",
                      evidence=S.ev("api/collect.py", [3, 7], "is_expired")),
    "workflow": S.element("workflow:collect-parcel", "Collect a parcel", "From code entry to release.",
                          plain="How a recipient gets a parcel out of a locker.",
                          steps=[{"id": "s1", "plain": "Take the parcel.", "operation": "operation:parcel.collect",
                                  "outcome": "o-done"}],
                          outcomes=[{"id": "o-done", "type": "success", "plain": "The parcel is collected."},
                                    {"id": "o-denied", "type": "failure", "plain": "The locker stays shut."}],
                          evidence=S.ev("api/collect.py", [10, 17], "collect_parcel")),
    "decision": {"id": "decision:skip-locked", "kind": "decision", "title": "Skip locked rows", "status": "proposed",
                 "date": None, "problem": {"text": "Deposits collided.", "provenance": "inferred"},
                 "decision": {"text": "Lock one row and skip locked rows.", "provenance": "inferred"}},
}


def element_findings(element):
    """Schema, evidence rules and the per-element rules (V1, V4, V5, V8, V10, V11)."""
    kind = element["kind"]
    problems = [m for _, m in rules.check_schema(element, M.REGISTRY.load(kind + ".schema.json"), M.REGISTRY)]
    report = rules.Report()
    for where, item in rules.E.walk(element):
        rules._v1_evidence(report, "x", where, item, element["id"])
    rules._element(report, "x", element["id"], element, overlay=False)
    return problems + [f["rule"] + ": " + f["message"] for f in report.findings]


class Guides(unittest.TestCase):
    maxDiff = None

    def test_every_guide_exists_and_is_lean(self):
        for name in GUIDES:
            body = text(name)
            self.assertLessEqual(len(body.splitlines()), 130, name)
            self.assertLessEqual((len(body) + 3) // 4, 2000, name)
            self.assertIn("Unmeasured", body.split("\n## ", 1)[0], name)

    def test_calling_agent_part_comes_first(self):
        for name in CALLING:
            self.assertEqual(re.findall(r"^## (.+)$", text(name), re.M)[0], "Calling agent", name)
        self.assertNotIn("## Calling agent", text("functional.md"))
        lines = [line for line in text("discover.md").split("## Worker")[0].split("## Calling agent")[1].splitlines()]
        self.assertLessEqual(len(lines), 31)
        self.assertIn('"Calling agent" part of a guide', split_skill()[1])

    def test_router_points_at_existing_guides_one_per_task(self):
        rows = [line for line in split_skill()[1].splitlines() if line.startswith("| ") and "`references/" in line]
        self.assertGreaterEqual(len(rows), 5)
        named = set()
        for row in rows:
            cell = row.split("|")[2]
            targets = re.findall(r"`references/([^`]+)`", cell)
            for target in targets:
                self.assertTrue(os.path.isfile(os.path.join(REFERENCES, target)), target)
            self.assertLessEqual(len([t for t in targets if t != "headless.md"]), 1, row)
            named |= set(targets)
        self.assertTrue(set(CALLING) <= named, named)

    def test_workers_read_the_guides_their_packet_names(self):
        headless = text("headless.md")
        packet = re.search(r"## Task packet\n.*?```\n(.*?)```", headless, re.S).group(1)
        self.assertIn("(init: discover.md, functional.md; update: update.md)", packet)
        self.assertEqual(cli.WORKER_READS, {"all": ["model.md", "discover.md", "functional.md"],
                                            "changed": ["model.md", "update.md"]})

    def test_commands_and_flags_exist(self):
        parsers = subparsers()
        every = set().union(*(options(p) for p in parsers.values()))
        # Flags of the host CLIs (claude, codex) are those in their command blocks; --bare is named only to forbid it.
        host = {"--bare"}
        for name in GUIDES + ["headless.md"]:
            for _, block in fenced(text(name)):
                if any(h in block for h in HOST):
                    host |= set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", block))
        every |= host
        queries = {"neighbors", "impact", "lineage", "shared-resources", "refs"}
        for name in GUIDES + ["headless.md"]:
            body = text(name)
            for span in inline(body):
                words = span.split()
                if words and words[0] in parsers:
                    for flag in [w for w in words if w.startswith("--")]:
                        self.assertIn(flag, options(parsers[words[0]]), (name, span))
                    if words[0] == "query" and len(words) > 1 and not words[1].startswith(("<", "...", "…")):
                        self.assertTrue(set(words[1].replace("\\|", "|").split("|")) <= queries, (name, span))
            outside_host = prose(body) + "".join(b for _, b in fenced(body) if not any(h in b for h in HOST))
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", outside_host):
                self.assertIn(flag, every, (name, flag))
            for command in re.findall(r"evermap\.py ([a-z-]+)", body):
                if command != "command":  # "one plain … evermap.py command per call"
                    self.assertIn(command, sorted(parsers), (name, command))

    def test_backticked_fields_and_values_exist(self):
        words = schema_words() | set(subparsers()) | {"neighbors", "impact", "lineage", "shared-resources", "refs"}
        words |= self.output_keys()
        extra = {"conversation", "snake_case", "s1", "s2", "o-", "o-stored", "init", "update", "regenerate",
                 "reconcile", "migrate", "md"}  # values and words defined in the guides themselves
        for name in GUIDES:
            for span in inline(text(name)):
                parts = span.split(": ") if re.match(r"^[a-z_]+: [a-z-]+$", span) else [span]
                for part in parts:
                    if part.endswith((".md", ".json")) and "/" not in part:
                        self.assertTrue(os.path.isfile(os.path.join(REFERENCES, part)), (name, part))
                    elif re.match(r"^[a-z][a-z0-9_.\[\]-]*$", part):
                        for piece in filter(None, re.split(r"\.|\[\]", part)):
                            self.assertIn(piece, words | extra, (name, span))

    def output_keys(self):
        """Every key the read-only commands print on the fixture, so guides can name them."""
        if not hasattr(Guides, "_keys"):
            case = S.RepoCase("setUp")
            case.setUp()
            try:
                found = set()
                S.run(case.root, "estimate", "--write-plan")
                for args in (["status"], ["estimate"], ["packet", "1"], ["coverage"], ["check-evidence"],
                             ["changes"], ["validate"], ["query", "impact", "operation:parcel.deposit"]):
                    stack = [S.run_json(case.root, *args)[1]]
                    while stack:
                        item = stack.pop()
                        if isinstance(item, dict):
                            found |= set(item)
                            stack += list(item.values())
                        elif isinstance(item, list):
                            stack += item
            finally:
                case.doCleanups()
            Guides._keys = found
        return Guides._keys

    def test_right_and_wrong_examples(self):
        count = 0
        for name in GUIDES:
            for info, body in fenced(text(name)):
                if not info.startswith("json"):
                    continue
                label = info.split()
                self.assertEqual(len(label), 3, (name, info))  # every JSON example is labelled and tested
                _, verdict, kind = label
                element = json.loads(json.dumps(BASES[kind]))
                element.update(json.loads(body))
                findings = element_findings(element)
                if verdict == "right":
                    self.assertEqual(findings, [], (name, body))
                else:
                    self.assertEqual(verdict, "wrong")
                    self.assertNotEqual(findings, [], (name, body))
                count += 1
        self.assertGreaterEqual(count, 10)
        for base in BASES.values():
            self.assertEqual(element_findings(json.loads(json.dumps(base))), [], base["id"])

    def test_assess_worker_cannot_write(self):
        blocks = [b for _, b in fenced(text("assess.md"))]
        claude = next(b for b in blocks if "claude -p" in b).replace("\\\n", " ")
        tools = re.search(r'--tools "([^"]+)"', claude).group(1).split(",")
        self.assertEqual(sorted(tools), ["Glob", "Grep", "Read"])
        allowed = re.search(r"--allowedTools (.*?) --permission-mode", claude).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', allowed), ["Read(/<absolute skill dir>/**)"])
        for word in ("Edit", "Write", "Bash"):
            self.assertNotIn(word, claude.split("python3 -c")[0])
        self.assertIn("[:4000]", claude)
        codex = next(b for b in blocks if "codex exec" in b)
        self.assertIn("--sandbox read-only", codex)
        self.assertNotIn("workspace-write", codex)
        self.assertIn("head -c 4000", codex)

    def test_no_permission_bypass_and_no_bare_anywhere(self):
        for folder, _, names in os.walk(S.SKILL):
            for name in names:
                if not name.endswith((".md", ".json", ".py")):
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    body = handle.read()
                for flag in ("--dangerously-skip-permissions", "bypassPermissions", "danger-full-access", "--yolo"):
                    self.assertNotIn(flag, body, name)
                self.assertEqual(body.count("--bare"), body.count("never use `--bare`"), name)

    def test_report_template_order(self):
        with open(TEMPLATE, encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
        self.assertTrue(lines[0].startswith("Freshness:"), lines[0])
        self.assertTrue(lines[-1].startswith("Coverage:"), lines[-1])
        self.assertIn("not complete", lines[-1])
        headings = [line[3:] for line in lines if line.startswith("## ")]
        self.assertEqual(headings, ["Summary", "Possibly redundant with", "Conflicts with decisions",
                                    "Affected elements", "Invariants at risk", "Findings", "Relevant tests",
                                    "Coverage gaps and unknowns", "Suggested next steps"])

    def test_worker_parts_say_model_changes_are_not_dirt(self):
        for name in ("discover.md", "update.md"):
            worker = text(name).split("## Worker", 1)[1].lstrip()
            first = worker.split("\n\n", 1)[0]
            self.assertIn('The "Calling agent" part above is not for you.', first, name)
            self.assertIn("`.evermap/plan.json`", first, name)
            self.assertIn("never make the tree dirty", first, name)

    def test_trial_fixes(self):
        order = text("discover.md").split("### Order", 1)[1].split("###", 1)[0]
        self.assertRegex(order, r"\n\d\. Reachability: list every handler defined in your files")
        self.assertLess(order.index("Reachability"), order.index("Schema"))
        step = text("update.md").split("Meaning changed:", 1)[1].split("\n   - ", 1)[0]
        self.assertIn("`anchor <id>\n     --refresh`", step)
        assess = text("assess.md")
        self.assertIn("at most 3,500 characters, most severe finding first", assess)
        self.assertIn("[:4000]", assess)

    def test_relation_direction_guidance(self):
        relations = split_model_section("Relations")
        for phrase in ("`depends_on` points from the consumer to the provider",
                       "`calls` points from the caller to the callee", "never a signature or a comment",
                       "calls the server's handler, never the reverse", "Data received as an argument is\nnot a call"):
            self.assertIn(phrase, relations)
        wrong, right = [json.loads(b) for i, b in fenced(relations) if i == "json"]
        self.assertEqual((wrong["type"], right["type"]), ("calls", "calls"))
        self.assertEqual(right["target"], "operation:parcel.deposit")
        self.assertIn("Direction (", text("discover.md"))

    def test_regenerate_is_said_to_be_unavailable(self):
        self.assertIn("Regenerate is not available", text("update.md"))
        self.assertNotIn("regenerate", subparsers())
        self.assertNotIn("reconcile", subparsers())

    def test_only_the_calling_agent_passes_allow_decisions(self):
        decisions = text("decisions.md")
        self.assertIn("Only you pass\n   `--allow-decisions`, and only after the user confirmed the text in this conversation",
                      decisions)
        self.assertNotIn("--allow-decisions", text("headless.md") + text("discover.md") + text("functional.md"))


if __name__ == "__main__":
    unittest.main()
