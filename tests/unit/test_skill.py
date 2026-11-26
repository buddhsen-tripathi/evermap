"""SKILL.md limits and frontmatter (SPEC 6.2), paths it mentions, and the reference example (M1)."""

import json
import os
import re
import shutil
import tempfile
import unittest

from tests import support as S
from evermap_lib import model as M
from evermap_lib import rules

SKILL_MD = os.path.join(S.SKILL, "SKILL.md")
REFERENCES = os.path.join(S.SKILL, "references")
SPEC = os.path.join(S.REPO, "docs", "SPEC.md")
PATH = re.compile(r"(?:`|\]\()((?:references|scripts|assets)/[^`)\s]*)")


def text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def split_skill():
    match = re.match(r"^---\n(.*?\n)---\n(.*)$", text(SKILL_MD), re.S)
    assert match, "SKILL.md has no frontmatter"
    return match.group(1), match.group(2)


def json_blocks(markdown):
    """(path line before the block, parsed JSON) for every ```json block."""
    found = []
    for match in re.finditer(r"(?:^`([^`\n]+)`\n\n)?```json\n(.*?)\n```", markdown, re.M | re.S):
        found.append((match.group(1), json.loads(match.group(2))))
    return found


class Skill(unittest.TestCase):
    def test_frontmatter_is_exactly_the_spec_draft(self):
        spec = re.search(r"### 6\.2 .*?```yaml\n---\n(.*?\n)---\n```", text(SPEC), re.S)
        self.assertIsNotNone(spec)
        self.assertEqual(split_skill()[0], spec.group(1))

    def test_body_limits(self):
        body = split_skill()[1]
        self.assertLessEqual(len(body.splitlines()), 250)
        # About four characters per token for English prose; 3,000 tokens is the SPEC target.
        self.assertLessEqual(len(body) / 4.0, 3000)

    def test_mentioned_paths_exist(self):
        for path in (SKILL_MD,) + tuple(os.path.join(REFERENCES, n) for n in os.listdir(REFERENCES)):
            mentioned = PATH.findall(text(path))
            if path == SKILL_MD:
                self.assertIn("references/model.md", mentioned)
            for rel in mentioned:
                self.assertTrue(os.path.exists(os.path.join(S.SKILL, *rel.rstrip("/").split("/"))), (path, rel))

    def test_status_says_unmeasured_and_what_is_not_available(self):
        """Intended change (seventh round): the procedures are written, but unmeasured."""
        body = split_skill()[1]
        status = next(p for p in body.split("\n\n") if p.startswith("**Status:"))
        self.assertIn("unmeasured", status)
        for word in ("discovery", "update", "functional", "decisions", "impact"):
            self.assertIn(word, status)
        self.assertIn("Not available:", status)
        for name in ("`reconcile`", "`migrate`", "`regenerate`"):
            self.assertIn(name, status.split("Not available:")[1])

    def test_references_do_not_point_at_each_other(self):
        for name in os.listdir(REFERENCES):
            for rel in PATH.findall(text(os.path.join(REFERENCES, name))):
                self.assertFalse(rel.startswith("references/"), (name, rel))
            self.assertNotRegex(text(os.path.join(REFERENCES, name)), r"\]\((?!https?:)[^)]*\.md\)")


class Headless(unittest.TestCase):
    """references/headless.md: lean, never bypasses permissions, linked from SKILL.md (SPEC 5.12)."""

    def setUp(self):
        self.text = text(os.path.join(REFERENCES, "headless.md"))

    def claude_command(self):
        block = re.search(r"## Claude Code\n\n```\n(.*?)```", self.text, re.S).group(1)
        return block.replace("\\\n", " ").splitlines()[0]

    def test_no_permission_bypass_and_no_bare(self):
        for flag in ("--dangerously-skip-permissions", "bypassPermissions", "danger-full-access", "--yolo"):
            self.assertNotIn(flag, self.text)
        command = self.claude_command()
        self.assertNotIn("--bare", command)
        self.assertNotRegex(self.text.replace("never use `--bare`", ""), r"--bare")

    def test_lean_flags_in_the_claude_command(self):
        command = self.claude_command()
        self.assertTrue(command.startswith("claude -p "), command)
        for flag in ("--strict-mcp-config", "--disable-slash-commands", '--tools "Read,Grep,Glob,Edit,Write,Bash"',
                     "--no-session-persistence", "--output-format json", "--max-budget-usd",
                     "--permission-mode default", '"Edit(.evermap/**)"', '"Write(.evermap/**)"', "--allowedTools"):
            self.assertIn(flag, command)
        allowed = re.search(r"--allowedTools (.*?) --permission-mode", command).group(1)
        # Tested once on Claude Code 2.1.x: no bare Read, Grep or Glob; reads limited to the repository
        # (the working directory) and the skill, writes to .evermap/, one plain script command.
        self.assertEqual(re.findall(r'"([^"]+)"', allowed),
                         ["Read(/<absolute skill dir>/**)", "Edit(.evermap/**)", "Write(.evermap/**)",
                          "Bash(python3 <absolute skill dir>/scripts/evermap.py:*)"])

    def test_packet(self):
        packet = re.search(r"## Task packet\n.*?```\n(.*?)```", self.text, re.S).group(1)
        for phrase in ("references/model.md", "Do not read assets/schema/", ".evermap/draft.json with a single Write",
                       "scripts/evermap.py apply", "only the corrected elements", "one plain python3",
                       "no cd,\nvariables, pipes or &&", "no helper scripts", "Start no other agents",
                       "at most 900 characters: pending items first", "counts per kind", "open questions"):
            self.assertIn(phrase, packet)
        for phrase in ("scripts/evermap.py packet <n>", "page with --offset", "run anchor <id> --refresh, naming the\n"
                       "element", "Do not run render or sync"):
            self.assertIn(phrase, packet)
        # Constant text: no file or id lists, only the packet number, the skill path and the <id> wording.
        self.assertEqual(set(re.findall(r"<[^>]+>", packet)), {"<n>", "<absolute skill dir>", "<id>"})
        self.assertIn("[:1200]", self.text)  # the calling agent still cuts at 1,200
        self.assertIn("`search` or `coverage`", self.text)
        self.assertIn("cannot see ignored files", self.text)
        self.assertIn("expected_billed_input_tokens", self.text)

    def test_codex_command(self):
        block = re.search(r"## Codex\n\n```\n(.*?)```", self.text, re.S).group(1)
        self.assertIn("codex exec --ephemeral --sandbox workspace-write -o ", block)
        self.assertIn("< /dev/null", block)

    def test_size_and_link(self):
        # Raised from 90 for the unowned-file continuation and the pending lines (trial fixes).
        self.assertLessEqual(len(self.text.splitlines()), 105)
        self.assertIn("`references/headless.md`", split_skill()[1])
        self.assertNotIn("](", self.text)  # no links to other reference files


SCHEMA_DIR = os.path.join(S.SKILL, "assets", "schema")
TOKEN = re.compile(r'\s*(?:([{}\[\],:])|"([^"]*)"|(\+[AE])|([A-Za-z][\w-]*))')


_SCHEMAS = {}


def schema(name):
    if name not in _SCHEMAS:
        with open(os.path.join(SCHEMA_DIR, name), encoding="utf-8") as handle:
            _SCHEMAS[name] = json.load(handle)
    return _SCHEMAS[name]


def resolve(name, node):
    """(file name, node) with $ref followed."""
    while "$ref" in node:
        target, _, pointer = node["$ref"].partition("#")
        name = target or name
        node = schema(name)
        for part in pointer.strip("/").split("/"):
            node = node[part]
    return name, node


def parse_shape(text):
    """Parse the Shapes notation into ("obj", members) / ("arr", item) / ("enum", values) / ("word", w)."""
    tokens, pos = [], 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise ValueError("cannot parse shape at %r" % text[pos:pos + 20])
        punct, string, macro, word = match.groups()
        tokens.append(("p", punct) if punct else ("s", string) if string is not None else
                      ("m", macro[1]) if macro else ("w", word))
        pos = match.end()
        while pos < len(text) and text[pos] == " ":
            pos += 1
    tokens.append(("end", None))

    def value(i):
        kind, item = tokens[i]
        if (kind, item) == ("p", "{"):
            members, i = [], i + 1
            while tokens[i] != ("p", "}"):
                kind, item = tokens[i]
                if kind == "m":
                    members.append(("+", item, None))
                    i += 1
                else:
                    assert kind == "s", tokens[i]
                    key, child, i = item, None, i + 1
                    if tokens[i] == ("p", ":"):
                        child, i = value(i + 1)
                    members.append((key.rstrip("?"), not key.endswith("?"), child))
                if tokens[i] == ("p", ","):
                    i += 1
            return ("obj", members), i + 1
        if (kind, item) == ("p", "["):
            child, i = value(i + 1)
            assert tokens[i] == ("p", "]"), tokens[i]
            return ("arr", child), i + 1
        if kind == "s":
            return ("enum", set(item.split("|"))), i + 1
        assert kind == "w", tokens[i]
        return ("word", item), i + 1

    result, end = value(0)
    assert tokens[end][0] == "end", tokens[end:]
    return result


def shapes():
    section = text(os.path.join(REFERENCES, "model.md")).split("## Shapes", 1)[1].split("\n## ", 1)[0]
    return {m.group(1): parse_shape(m.group(2)) for m in re.finditer(r"^- `(\w+)`: (.+)$", section, re.M)}


def compare(note, name, node, where, named):
    """Problems where the notation and the schema disagree."""
    name, node = resolve(name, node)
    if note[0] == "word" and note[1][:1].isupper():
        return compare(named[note[1]], name, node, where, named)
    if "properties" in node:
        if note[0] != "obj":
            return ["%s: needs an object shape" % where]
        members = {}
        for key, required, child in note[1]:
            if key == "+":
                for k, r, c in named["Envelope" if required == "E" else "Annotation"][1]:
                    members.setdefault(k, (r, c))
        for key, required, child in note[1]:
            if key != "+":
                members[key] = (required, child)
        props, required = node["properties"], set(node.get("required", []))
        problems = ["%s: keys %s missing, %s extra" % (where, sorted(set(props) - set(members)),
                                                        sorted(set(members) - set(props)))] \
            if set(members) != set(props) else []
        for key in sorted(set(members) & set(props)):
            if members[key][0] != (key in required):
                problems.append("%s.%s: required is %s in the schema" % (where, key, key in required))
            child = members[key][1]
            if child is not None:
                problems += compare(child, name, props[key], where + "." + key, named)
            else:
                _, sub = resolve(name, props[key])
                if len(sub.get("enum", [])) > 1 or "properties" in sub or sub.get("type") == "array":
                    problems.append("%s.%s: needs a shape or its values" % (where, key))
        return problems
    if node.get("type") == "array":
        if note[0] != "arr":
            return ["%s: needs a list shape" % where]
        return compare(note[1], name, node["items"], where + "[]", named)
    if "enum" in node:
        if note[0] != "enum" or note[1] != set(node["enum"]):
            return ["%s: values must be %s" % (where, "|".join(node["enum"]))]
    elif note[0] == "enum":
        return ["%s: the schema has no list of values" % where]
    return []


def property_names(node, name, seen):
    """Every property name at every depth of a schema, following $ref."""
    name, node = resolve(name, node)
    key = (name, id(node))
    if key in seen:
        return set()
    seen.add(key)
    found = set(node.get("properties", {}))
    for child in list(node.get("properties", {}).values()) + [node.get("items")]:
        if isinstance(child, dict) and child:
            found |= property_names(child, name, seen)
    for child in node.get("$defs", {}).values():
        found |= property_names(child, name, seen)
    return found


class Shapes(unittest.TestCase):
    """references/model.md gives every structured field's shape, and the shapes match the schemas."""

    FILES = [k + ".schema.json" for k in M.KINDS] + ["manifest.schema.json", "proposal.schema.json"]

    def test_every_file_has_a_shape_that_matches_its_schema(self):
        named = shapes()
        for name in self.FILES:
            kind = name.split(".")[0]
            self.assertIn(kind, named)
            self.assertEqual(compare(named[kind], name, schema(name), kind, named), [], kind)

    def test_every_property_at_every_depth_is_named(self):
        section = text(os.path.join(REFERENCES, "model.md")).split("## Shapes", 1)[1].split("\n## ", 1)[0]
        named = set(re.findall(r'"(\w+)\??"', section))
        for name in self.FILES + ["common.schema.json"]:
            missing = property_names(schema(name), name, set()) - named
            self.assertEqual(missing, set(), name)

    def test_comparison_catches_mistakes(self):
        """Positive controls: a wrong shape is reported."""
        named = shapes()
        role = schema("role.schema.json")
        for wrong in ('{+E, "type": "user-role|external-actor"}', '{+E, "type?": "user-role|service-identity|external-actor"}',
                      '{+E, "type": "user-role|service-identity|external-actor", "extra?"}', '{+E}',
                      '{+E, "type": "user-role|service-identity|external-actor", "relations?": [text]}'):
            self.assertNotEqual(compare(parse_shape(wrong), "role.schema.json", role, "role", named), [], wrong)

    HINTS = {"id": "role:example", "ref": "entity:example#field", "text": "x", "glob": "api/**", "bool": True,
             "int": 1, "bool-or-null": None, "string-number-or-bool": 7, "date-or-null": None}
    BY_KEY = {"commit": "0" * 40, "schema_version": "0.1", "date": "2026-01-01", "hash": "sha256:" + "0" * 16,
              "lines": [1, 2]}

    def example(self, note, named, key=None, optional=False):
        """An instance built from a shape: required members only, or every member when optional."""
        kind, item = note
        if kind == "obj":
            members = []
            for k, required, child in item:
                members += named["Envelope" if required == "E" else "Annotation"][1] if k == "+" else []
            members += [m for m in item if m[0] != "+"]
            result = {}
            for k, required, child in members:
                if required or optional:
                    result[k] = self.BY_KEY[k] if k in self.BY_KEY else (
                        self.example(child, named, k, optional) if child else "x")
            return result
        if kind == "arr":
            return [self.example(item, named, key, optional)]
        if kind == "enum":
            return sorted(item)[0]
        if item[:1].isupper():
            return self.example(named[item], named, key, optional)
        return self.HINTS[item]

    def test_shape_examples_validate(self):
        named = shapes()
        for optional in (False, True):
            for name in self.FILES:
                kind = name.split(".")[0]
                value = self.example(named[kind], named, optional=optional)
                if "kind" in value:
                    value["kind"] = kind
                self.assertEqual(rules.check_schema(value, schema(name), M.REGISTRY), [], (kind, optional))

    def test_size(self):
        self.assertLessEqual(len(text(os.path.join(REFERENCES, "model.md"))), 18000)


def router_commands(intent):
    """Backticked commands in the Scripts cell of the router row that starts with intent."""
    row = next(line for line in split_skill()[1].splitlines() if line.startswith("| " + intent))
    return re.findall(r"`([^`]+)`", row.rstrip(" |").rsplit("|", 1)[1])


class InitSequence(unittest.TestCase):
    """The init steps, run in the order SKILL.md gives them, leave a model that passes --strict."""

    maxDiff = None
    INIT = ["init", "--name", "Parcel lockers", "--summary", "Locker deposits and collection.",
            "--plain", "Holds parcels until people pick them up."]

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="evermap-seq-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def run_sequence(self, steps):
        root = os.path.join(self.base, "r%d" % len(os.listdir(self.base)))
        S.build(root, with_model=False)
        for step in steps:
            args = self.INIT if step == "init" else step.split()
            code, out, err = S.run(root, *args)
            self.assertEqual(code, 0, (step, err))
            if step == "init":
                # Code is committed while the model is being written, so sync has a new HEAD to record.
                S.put(root, "tools/report.py", "X = 1\n")
                S.commit(root, "more code")
                for value in S.ELEMENTS:
                    S.write_element(root, value)
        code, out = S.run_json(root, "validate", "--strict", "--limit", "0", "--max-chars", "0")
        return code, out["findings"]

    def test_skill_md_order(self):
        steps = router_commands("map or document")
        # The workers between estimate and anchor write the element files (references/headless.md).
        self.assertEqual(steps, ["init", "estimate --write-plan", "anchor", "fmt", "validate", "sync --mode init",
                                 "render"])
        self.assertEqual(self.run_sequence(steps), (0, []))

    def test_render_before_sync_leaves_views_out_of_date(self):
        """Positive control: with the order reversed, V15 reports the views."""
        steps = ["init", "anchor", "fmt", "validate", "render", "sync --mode init"]
        code, findings = self.run_sequence(steps)
        self.assertEqual([f["rule"] for f in findings], ["V15"])

    def test_update_row_renders_after_sync(self):
        steps = router_commands("update or check")
        self.assertEqual(steps[-2:], ["sync --mode update", "render"])
        validation = split_skill()[1].split("## Validation loop", 1)[1].split("\n## ", 1)[0]
        self.assertLess(validation.index("`sync --mode"), validation.index("`render`"))


class ReferenceExample(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.root = os.path.join(os.path.realpath(tempfile.mkdtemp(prefix="evermap-ref-")), "parcels")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.root), True)
        S.build(self.root, with_model=False)

    def test_example_validates(self):
        blocks = json_blocks(text(os.path.join(REFERENCES, "model.md")))
        examples = [(where, value) for where, value in blocks if isinstance(value, dict) and "id" in value]
        self.assertGreaterEqual(len(examples), 3)
        self.assertEqual(S.run(self.root, "init", "--name", "Parcel lockers", "--summary", "Parcel locker service.",
                               "--plain", "Holds parcels until people pick them up.")[0], 0)
        for where, value in examples:
            self.assertEqual(where, M.path_for_id(value["id"]))
            self.assertNotIn("hash", json.dumps(value))  # hashes come from anchor
            S.write_element(self.root, value)
        S.anchor_all(self.root)
        self.assertEqual(S.run(self.root, "fmt")[0], 0)
        self.assertEqual(S.run(self.root, "render")[0], 0)
        code, out = S.run_json(self.root, "validate", "--strict", "--limit", "0", "--max-chars", "0")
        self.assertEqual((code, out["findings"]), (0, []))


if __name__ == "__main__":
    unittest.main()
