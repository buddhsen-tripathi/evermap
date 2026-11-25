"""CLI contract (M1.10), determinism (M1.6), bounded output (M1.11), safety (M1.9), sync and status (SPEC 5.3)."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import support as S
from evermap_lib import cli
from evermap_lib import model as M

DEPOSIT, RULE = "operation:parcel.deposit", "rule:collection.window"
EXIT_TEXT = ("exit codes: 0 ok, 1 validation errors, 2 usage error, 3 no Git repository or model, "
             "4 stale (--strict)")
INVOCATIONS = {
    "init": None,  # needs a repository without a model: see test_init
    "apply": None,  # needs a draft: see test_apply
    "packet": None,  # needs a plan: see test_estimate
    "status": ["status"],
    "changes": ["changes"],
    "anchor": ["anchor", "--dry-run"],
    "check-evidence": ["check-evidence"],
    "fmt": ["fmt", "--check"],
    "validate": ["validate"],
    "sync": ["sync", "--mode", "update"],
    "query": ["query", "impact", DEPOSIT],
    "search": ["search", "locker"],
    "coverage": ["coverage"],
    "estimate": ["estimate"],
    "diff": ["diff", "HEAD~1", "HEAD"],
    "render": ["render"],
}
READS = [
    ["status"], ["changes"], ["check-evidence"], ["validate"], ["fmt", "--check"], ["coverage"],
    ["query", "impact", DEPOSIT], ["query", "neighbors", DEPOSIT, "--depth", "2"],
    ["query", "lineage", "entity:parcel#locker_id"], ["query", "shared-resources"], ["query", "refs", "entity:parcel"],
    ["search", "parcel", "locker"], ["diff", "HEAD~1", "HEAD"], ["diff", "HEAD"], ["diff", "HEAD", "--format", "md"],
    ["estimate"], ["estimate", "--changed"], ["render"],
]


def subcommands():
    action = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(action.choices)


def plain_git(cwd, *args):
    """Git with the repository's own configuration (support.git forces autocrlf off)."""
    subprocess.run(["git"] + list(args), cwd=cwd, env=dict(os.environ, **S.GIT_ENV), check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def snapshot(root):
    """{path: bytes or link target} for everything under root, .git included."""
    found = {}
    for folder, dirs, names in os.walk(root):
        for name in dirs + names:
            full = os.path.join(folder, name)
            key = os.path.relpath(full, root).replace(os.sep, "/")
            if os.path.islink(full):
                found[key] = "link:" + os.readlink(full)
            elif os.path.isfile(full):
                with open(full, "rb") as handle:
                    found[key] = handle.read()
    return found


def changed(before, after, model=True):
    """Paths that differ between two snapshots, ignoring .evermap/ unless model is True."""
    keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return keys if model else [k for k in keys if k != ".evermap" and not k.startswith(".evermap/")]


def to_crlf(root, path):
    data = S.read(root, path)
    with open(os.path.join(root, *path.split("/")), "wb") as handle:
        handle.write(data.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def insert_line(root, path, ending="\n"):
    lines = S.read(root, path).replace("\r\n", "\n").split("\n")[:-1]
    lines.insert(1, "# Inserted.")
    S.put(root, path, "".join(line + ending for line in lines))


def symlink(target, link):
    try:
        os.symlink(target, link, target_is_directory=os.path.isdir(target))
    except (OSError, NotImplementedError) as exc:
        raise unittest.SkipTest("symbolic links unavailable: %s" % exc)


class Contract(S.RepoCase):
    def test_every_subcommand_is_covered(self):
        self.assertEqual(sorted(INVOCATIONS), subcommands())

    def test_help_documents_exit_codes(self):
        for command in [[]] + [[name] for name in subcommands()]:
            code, out, err = S.run(self.root, *(command + ["--help"]))
            text = " ".join(out.decode().split())
            self.assertEqual(code, 0, command)
            self.assertIn(EXIT_TEXT, text, command)
            if command:
                self.assertIn("--format {json,md}", text, command)

    def test_every_subcommand_prints_json(self):
        for name, args in sorted(INVOCATIONS.items()):
            if args:
                code, out = S.run_json(self.root, *args)
                self.assertEqual(code, 0, (name, out))
                self.assertIsInstance(out, dict)

    def test_every_subcommand_prints_markdown(self):
        for name, args in sorted(INVOCATIONS.items()):
            if args:
                code, out, err = S.run(self.root, *(args + ["--format", "md"]))
                self.assertEqual(code, 0, (name, err))
                self.assertTrue(out.strip(), name)
                with self.assertRaises(ValueError):
                    json.loads(out.decode())

    def test_init(self):
        root = os.path.join(self.base, "fresh")
        os.makedirs(root)
        S.build(root, with_model=False)
        code, out = S.run_json(root, "init", "--scope", "api/**", "--name", "Fresh")
        self.assertEqual(code, 0)
        self.assertEqual(out["created"], [".evermap/evermap.json", ".evermap/.gitattributes"])
        self.assertEqual(out["manifest"]["synced"], {"commit": S.git(root, "rev-parse", "HEAD").strip(), "mode": "init"})
        self.assertEqual(S.run_json(root, "validate")[0], 0)
        self.assertEqual(S.run(root, "init")[0], 2)


class ExitCodes(S.RepoCase):
    def code(self, *args, **kwargs):
        return S.run(kwargs.get("root", self.root), *args)[0]

    def test_0_ok(self):
        self.assertEqual(self.code("validate", "--strict"), 0)
        self.assertEqual(self.code("status", "--strict"), 0)

    def test_1_validation_errors(self):
        self.edit(RULE, lambda v: v.pop("summary"))
        code, out = S.run_json(self.root, "validate")
        self.assertEqual((code, out["valid"]), (1, False))
        S.put(self.root, M.path_for_id(DEPOSIT), S.read(self.root, M.path_for_id(DEPOSIT)).replace('  "kind"', '   "kind"'))
        self.assertEqual(self.code("fmt", "--check"), 1)

    def test_2_usage_errors(self):
        for args in (["nonsense"], [], ["validate", "--bogus"], ["status", "--limit", "-1"], ["query", "impact"],
                     ["query", "impact", "rule:gone"], ["query", "lineage", "entity:parcel#gone"],
                     ["diff", "a", "b", "c"], ["diff", "no-such-ref"], ["diff", "HEAD", "--overlay", "x"],
                     ["diff", "--overlay", "missing"], ["changes", "--since", "no-such-ref"],
                     ["init"], ["anchor", "--refresh"], ["sync"], ["search"]):
            code, out, _ = S.run(self.root, *args)
            self.assertEqual(code, 2, args)
            self.assertEqual(out, b"", args)

    def test_3_no_repository_or_model(self):
        empty = os.path.join(self.base, "empty")
        os.makedirs(empty)
        for args in (["status"], ["validate"], ["render"], ["init"]):
            self.assertEqual(self.code(*args, root=empty), 3, args)
        S.git(empty, "init", "-q")
        self.assertEqual(self.code("init", root=empty), 3)  # no commits yet
        S.commit(empty, "first")
        for args in (["status"], ["validate"], ["query", "impact", DEPOSIT], ["fmt"], ["sync", "--mode", "init"]):
            self.assertEqual(self.code(*args, root=empty), 3, args)
        self.assertEqual(os.listdir(empty), [".git"])

    def test_4_stale_in_strict_mode(self):
        insert_line(self.root, "api/collect.py")
        code, out = S.run_json(self.root, "status", "--strict")
        self.assertEqual((code, out["up_to_date"]), (4, False))
        self.assertEqual(S.run_json(self.root, "status")[0], 0)


class Determinism(S.RepoCase):
    def mutate(self, root):
        path = M.path_for_id(RULE)
        S.put(root, path, S.read(root, path).replace('"Parcels expire', '"Parcels always expire').replace(
            '  "kind"', '     "kind"'))
        insert_line(root, "api/collect.py")

    def test_repeat_runs_are_byte_identical(self):
        other = os.path.join(self.base, "other")
        shutil.copytree(S._template(), other, symlinks=True)
        for root in (self.root, other):
            self.mutate(root)
        sequence = [["fmt"], ["render"], ["fmt"], ["render"]] + READS
        for args in sequence:
            first = S.run(self.root, *args, PYTHONHASHSEED="1")
            second = S.run(other, *args, PYTHONHASHSEED="2")
            self.assertEqual(first[:2], second[:2], args)
            if args[0] in ("query", "diff", "search"):
                self.assertEqual(S.run(self.root, *args, PYTHONHASHSEED="3")[:2], first[:2], args)
        self.assertEqual(S.tree(self.root, ".evermap"), S.tree(other, ".evermap"))
        self.assertEqual(S.run_json(self.root, "fmt")[1]["formatted"], [])
        self.assertEqual(S.run_json(self.root, "render")[1]["written"], [])

    def test_lf_and_crlf_checkouts_agree(self):
        crlf = os.path.join(self.base, "crlf")
        plain_git(self.base, "clone", "-q", "-c", "core.autocrlf=true", self.root, crlf)
        with open(os.path.join(crlf, "api", "deposit.py"), "rb") as handle:
            self.assertIn(b"\r\n", handle.read())
        for path in S.tree(crlf, ".evermap"):
            if path.endswith((".json", ".md")):
                to_crlf(crlf, path)
        insert_line(self.root, "api/collect.py")
        insert_line(crlf, "api/collect.py", "\r\n")
        for args in READS:
            self.assertEqual(S.run(self.root, *args)[:2], S.run(crlf, *args)[:2], args)
        for root in (self.root, crlf):
            shutil.rmtree(os.path.join(root, ".evermap", "views"))
            self.assertEqual(S.run(root, "render")[0], 0)
        self.assertEqual(S.tree(self.root, ".evermap/views"), S.tree(crlf, ".evermap/views"))


class Bounded(unittest.TestCase):
    """M1.11 on a generated model of 391 elements with 540 changed files."""

    COUNT = 120

    @classmethod
    def setUpClass(cls):
        cls.base = os.path.realpath(tempfile.mkdtemp(prefix="evermap-big-"))
        cls.root = root = os.path.join(cls.base, "big")
        os.makedirs(root)
        source = "def handler_%d_%d(value):\n    return value + %d\n"
        for n in range(cls.COUNT):
            for k in range(5):
                S.put(root, "gen/c%03d/f%d.py" % (n, k), source % (n, k, n * 10 + k))
        for n in range(30):
            S.put(root, "loose/u%02d.py" % n, "LOOSE_%d = %d\n" % (n, n))
        S.git(root, "init", "-q")
        S.commit(root, "base")
        assert S.run(root, "init", "--name", "Generated", "--summary", "Generated.", "--plain", "Generated.")[0] == 0
        for value in cls.elements():
            S.write_element(root, value)
        S.anchor_all(root)
        assert S.run(root, "render")[0] == 0
        S.commit(root, "model")
        for value in cls.elements(version=2):
            S.write_element(root, value)
        S.anchor_all(root)
        assert S.run(root, "render")[0] == 0
        S.commit(root, "model v2")
        for n in range(cls.COUNT):
            for k in (0, 1):
                S.put(root, "gen/c%03d/f%d.py" % (n, k), source % (n, k, n * 10 + k + 1000))
            os.rename(os.path.join(root, "gen", "c%03d" % n, "f3.py"), os.path.join(root, "gen", "c%03d" % n, "g3.py"))
            os.remove(os.path.join(root, "gen", "c%03d" % n, "f4.py"))
        for n in range(60):
            S.put(root, "new/n%03d.py" % n, "NEW_%d = %d\n" % (n, n))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, True)

    @classmethod
    def elements(cls, version=1):
        count, out = cls.COUNT, []
        for n in range(count):
            c, op, ent = "component:c%03d" % n, "operation:c%03d.run" % n, "entity:c%03d" % n
            nxt = (n + 1) % count
            out.append(S.element(c, "Component %d" % n, "Generated component %d%s." % (n, " v2" * (version - 1)),
                                 plain="Part %d of the generated system." % n, type="service",
                                 paths=["gen/c%03d/**" % n], evidence=S.ev("gen/c%03d/f0.py" % n, [1, 2]),
                                 relations=[{"type": "depends_on", "target": "component:c%03d" % nxt}]))
            relations = [{"type": "writes", "target": "entity:shared", "fields": ["total"]},
                         {"type": "reads", "target": ent, "fields": ["v"]}]
            if n + 1 < count:
                relations.append({"type": "calls", "target": "operation:c%03d.run" % (n + 1)})
            out.append(S.element(op, "Run %d" % n, "Runs step %d." % n, plain="Step %d runs." % n, parent=c,
                                 trigger={"type": "call"}, exposure="public" if version == 2 else "internal",
                                 evidence=S.ev("gen/c%03d/f1.py" % n, [1, 2]), relations=relations))
            field = {"name": "v", "type": "integer"}
            if n:
                field["origins"] = [{"type": "derived", "from": ["entity:c%03d#v" % (n - 1)], "via": op}]
            out.append(S.element(ent, "Entity %d" % n, "Stored value %d." % n, parent=c, type="table",
                                 fields=[field], evidence=S.ev("gen/c%03d/f2.py" % n, [1, 2])))
        out.append(S.element("entity:shared", "Shared total", "Total every step writes.", type="dto",
                             fields=[{"name": "total", "type": "integer"}], evidence=S.ev("gen/c000/f3.py", [1, 2])))
        if version == 2:
            out += [S.element("role:r%02d" % n, "Role %d" % n, "Generated role.", plain="A role.", type="user-role",
                              provenance="inferred", rationale="Generated.") for n in range(30)]
        return out

    def test_the_model_is_large(self):
        code, out = S.run_json(self.root, "changes", "--limit", "0", "--max-chars", "0")
        self.assertEqual(code, 0)
        self.assertGreaterEqual(out["total_files"], 500)
        self.assertEqual(len([f for f in out["files"] if f["status"] == "renamed"]), self.COUNT)
        self.assertGreaterEqual(len(M.load_worktree(self.root).elements), 300)
        self.assertEqual(S.run_json(self.root, "validate", "--strict", "--max-chars", "0")[1]["errors"],
                         2 * self.COUNT + 1)  # stale evidence (V7) only

    def test_default_output_is_bounded(self):
        commands = [
            ["status"], ["changes"], ["check-evidence"], ["anchor", "--dry-run"], ["fmt", "--check"], ["validate"],
            ["validate", "--strict"], ["coverage"], ["query", "neighbors", "component:c000", "--depth", "3"],
            ["query", "impact", "entity:shared"], ["query", "impact", "component:c000", "--depth", "5"],
            ["query", "lineage", "entity:c119#v"], ["query", "refs", "entity:shared"], ["query", "shared-resources"],
            ["search", "generated", "run", "step"], ["diff", "HEAD~1", "HEAD"], ["diff", "HEAD~2", "HEAD"],
            ["diff", "HEAD~2", "HEAD", "--format", "md"], ["query", "impact", "entity:shared", "--format", "md"],
            ["estimate"], ["estimate", "--changed"], ["estimate", "--format", "md"], ["render"], ["sync", "--mode", "update"],
        ]
        for args in commands:
            code, out, err = S.run(self.root, *args)
            self.assertLessEqual(len(out.decode("utf-8")), cli.MAX_CHARS, args)
            self.assertIn(code, (0, 1), (args, err))
            if "md" not in args:
                payload = json.loads(out.decode("utf-8"))
                if args in (["changes"], ["check-evidence"], ["validate"], ["diff", "HEAD~2", "HEAD"]):
                    self.assertIn("truncated", payload, args)


class Safety(S.RepoCase):
    """M1.9: nothing outside .evermap/ changes and no repository code runs."""

    COMMANDS = [
        ["status"], ["status", "--strict"], ["changes"], ["changes", "--since", "HEAD~1"], ["check-evidence"],
        ["check-evidence", "--fix-moved"], ["anchor"], ["fmt"], ["validate", "--strict"], ["sync", "--mode", "update"],
        ["query", "impact", DEPOSIT], ["query", "neighbors", DEPOSIT], ["query", "lineage", "entity:parcel#id"],
        ["query", "refs", "entity:parcel"], ["query", "shared-resources"], ["search", "parcel"], ["coverage"],
        ["estimate"], ["estimate", "--changed"], ["diff", "HEAD~1"], ["diff", "HEAD~1", "HEAD", "--format", "md"], ["render"],
    ]

    def setUp(self):
        super().setUp()
        self.sentinels = os.path.join(self.base, "sentinels")
        os.makedirs(self.sentinels)

    def trap(self, name):
        return 'echo hit > "%s/%s"' % (self.sentinels.replace(os.sep, "/"), name)

    def hits(self):
        return sorted(os.listdir(self.sentinels))

    def arm(self):
        root = self.root
        hooks = ("pre-commit", "post-commit", "post-checkout", "post-index-change", "reference-transaction",
                 "pre-auto-gc", "post-rewrite", "fsmonitor-watchman", "pre-push", "post-merge")
        for folder in (".git/hooks", "hooks-dir"):
            for hook in hooks:
                S.put(root, folder + "/" + hook, "#!/bin/sh\n%s\n" % self.trap("hook-" + hook))
                os.chmod(os.path.join(root, *folder.split("/"), hook), 0o755)
        config = [("core.hooksPath", "hooks-dir"), ("core.fsmonitor", self.trap("fsmonitor")),
                  ("diff.external", self.trap("diff-external")), ("diff.trap.textconv", self.trap("textconv") + "; cat"),
                  ("diff.trap.command", self.trap("diff-command")), ("filter.trap.clean", self.trap("clean") + "; cat"),
                  ("filter.trap.smudge", self.trap("smudge") + "; cat"), ("filter.trap.required", "true"),
                  ("core.pager", self.trap("pager")), ("pager.diff", self.trap("pager-diff"))]
        for key, value in config:
            plain_git(root, "config", key, value)
        S.put(root, ".gitattributes", "* diff=trap filter=trap\n")
        python = 'open(r"%s", "w").close()\n'
        for name in ("json.py", "sitecustomize.py", "usercustomize.py", "conftest.py", "setup.py",
                     "evermap_lib/__init__.py", "api/__init__.py"):
            S.put(root, name, python % os.path.join(self.sentinels, "python-" + name.replace("/", "-")))
        S.put(root, "package.json", json.dumps({"name": "trap", "scripts": {
            s: self.trap("npm-" + s) for s in ("preinstall", "install", "postinstall", "prepare", "test", "start")}}))
        S.put(root, "Makefile", "all:\n\t%s\n" % self.trap("make"))
        insert_line(root, "api/collect.py")  # a stat-dirty tracked file makes git hash the working tree

    def test_booby_trapped_repository(self):
        self.arm()
        # Positive control: plain git runs the traps (the attribute's diff command wins over diff.external).
        plain_git(self.root, "--no-pager", "diff", "HEAD")
        plain_git(self.root, "--no-pager", "diff", "--no-ext-diff", "HEAD")
        self.assertTrue({"clean", "diff-command", "textconv"} <= set(self.hits()), self.hits())
        for name in self.hits():
            os.remove(os.path.join(self.sentinels, name))
        before = snapshot(self.root)
        for args in self.COMMANDS:
            code, out, err = S.run(self.root, *args)
            self.assertIn(code, (0, 1, 4), (args, err))
            self.assertEqual(self.hits(), [], args)
        shutil.rmtree(os.path.join(self.root, ".evermap"))
        self.assertEqual(S.run(self.root, "init")[0], 0)
        self.assertEqual(self.hits(), [])
        self.assertEqual(changed(before, snapshot(self.root), model=False), [])
        self.assertEqual(sorted(os.listdir(self.base)), ["parcels", "sentinels"])

    def test_filter_driver_that_cannot_be_disabled(self):
        """-c splits at the first '=', so filter.a=b.clean= cannot switch the driver off: refuse."""
        plain_git(self.root, "config", "filter.a=b.clean", self.trap("clean-ab") + "; cat")
        plain_git(self.root, "config", "filter.a=b.smudge", self.trap("smudge-ab") + "; cat")
        plain_git(self.root, "config", "filter.a=b.required", "true")
        S.put(self.root, ".gitattributes", "* filter=a=b\n")
        insert_line(self.root, "api/collect.py")
        plain_git(self.root, "--no-pager", "diff", "HEAD")  # positive control: the trap is armed
        self.assertIn("clean-ab", self.hits())
        os.remove(os.path.join(self.sentinels, "clean-ab"))
        before = snapshot(self.root)
        for args in self.COMMANDS + [["init"]]:
            code, out, err = S.run(self.root, *args)
            self.assertEqual((code, out), (cli.NO_MODEL, b""), args)
            self.assertIn("filter driver 'a=b' cannot be disabled safely", err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(self.hits(), [], args)
        self.assertEqual(changed(before, snapshot(self.root)), [])

    def tempt(self):
        """Make every writing command want to write: unformatted file, moved evidence, stale views."""
        path = M.path_for_id(RULE)
        S.put(self.root, path, S.read(self.root, path).replace('  "kind"', '     "kind"'))
        self.edit(DEPOSIT, lambda v: v["evidence"][0].pop("hash"))
        insert_line(self.root, "api/collect.py")

    def run_all(self):
        for args in (["fmt"], ["anchor"], ["check-evidence", "--fix-moved"], ["render"], ["sync", "--mode", "update"],
                     ["validate"], ["status"], ["init"]):
            code, _, err = S.run(self.root, *args)
            self.assertNotIn("Traceback", err, args)

    def test_symlinked_model_directory(self):
        self.tempt()
        outside = os.path.join(self.base, "outside")
        shutil.move(os.path.join(self.root, ".evermap"), outside)
        symlink(outside, os.path.join(self.root, ".evermap"))
        before, repo = snapshot(outside), snapshot(self.root)
        for args in (["fmt"], ["render"], ["sync", "--mode", "update"], ["anchor"], ["init"], ["validate"]):
            self.assertEqual(S.run(self.root, *args)[0], 3, args)
        self.run_all()
        self.assertEqual(changed(before, snapshot(outside)), [])
        self.assertEqual(changed(repo, snapshot(self.root)), [])

    def test_symlinked_model_subdirectory(self):
        self.tempt()
        outside = os.path.join(self.base, "outside")
        shutil.move(os.path.join(self.root, ".evermap", "model"), outside)
        symlink(outside, os.path.join(self.root, ".evermap", "model"))
        before = snapshot(outside)
        self.run_all()
        self.assertEqual(changed(before, snapshot(outside)), [])

    def test_symlinked_views_directory(self):
        self.edit(RULE, lambda v: v.update(summary="Changed."))
        outside = os.path.join(self.base, "outside")
        shutil.move(os.path.join(self.root, ".evermap", "views"), outside)
        symlink(outside, os.path.join(self.root, ".evermap", "views"))
        before = snapshot(outside)
        self.assertEqual(S.run(self.root, "render")[0], 3)
        self.assertEqual(changed(before, snapshot(outside)), [])

    def test_symlinked_element_file(self):
        outside = os.path.join(self.base, "outside.json")
        path = os.path.join(self.root, *M.path_for_id(RULE).split("/"))
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write(S.read(self.root, M.path_for_id(RULE)).replace('  "kind"', '     "kind"'))
        os.remove(path)
        symlink(outside, path)
        with open(outside, "rb") as handle:
            before = handle.read()
        self.run_all()
        with open(outside, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_commands_change_nothing_outside_the_model(self):
        self.tempt()
        S.put(self.root, "tools/new.py", "X = 1\n")
        before = snapshot(self.root)
        for args in self.COMMANDS:
            S.run(self.root, *args)
        self.assertEqual(changed(before, snapshot(self.root), model=False), [])


class SyncStatus(S.RepoCase):
    def status(self):
        code, out = S.run_json(self.root, "status", "--strict")
        return code, out

    def sync(self):
        code, out = S.run_json(self.root, "sync", "--mode", "update")
        self.assertEqual(code, 0)
        return out["written"]

    def head(self):
        return S.git(self.root, "rev-parse", "HEAD").strip()

    def synced(self):
        return json.loads(S.read(self.root, M.MANIFEST))["synced"]

    def test_committing_only_the_model_keeps_it_current(self):
        self.assertEqual(self.status()[0], 0)
        self.assertEqual(self.sync(), [])
        self.edit(RULE, lambda v: v.update(summary="Parcels expire after a week."))
        S.run(self.root, "render")
        S.commit(self.root, "model only")
        code, out = self.status()
        self.assertEqual((code, out["up_to_date"], out["changed_files"]), (0, True, 0))
        before = S.tree(self.root)
        self.assertEqual(self.sync(), [])
        self.assertEqual(S.tree(self.root), before)

    def test_second_sync_writes_nothing(self):
        insert_line(self.root, "api/collect.py")
        S.commit(self.root, "code")
        self.assertEqual(self.status()[0], 4)
        self.assertEqual(self.sync(), [M.MANIFEST])
        self.assertEqual(self.synced(), {"commit": self.head(), "mode": "update"})
        before = S.tree(self.root)
        self.assertEqual(self.sync(), [])
        S.commit(self.root, "sync")
        self.assertEqual(self.sync(), [])
        self.assertEqual(S.tree(self.root), before)

    def test_u4_unrelated_change(self):
        S.put(self.root, "docs/notes.md", "Notes.\n")
        S.commit(self.root, "docs")
        _, changes = S.run_json(self.root, "changes")
        self.assertEqual((changes["affected"], changes["unmapped"], changes["out_of_scope"]), ([], [], ["docs/notes.md"]))
        code, out = self.status()
        self.assertEqual((code, out["up_to_date"]), (0, True))
        before = S.tree(self.root, ".evermap")
        self.assertEqual(self.sync(), [M.MANIFEST])
        after = S.tree(self.root, ".evermap")
        self.assertEqual({k for k in after if after[k] != before.get(k)}, {M.MANIFEST})
        old, new = json.loads(before[M.MANIFEST]), json.loads(after[M.MANIFEST])
        self.assertEqual(dict(old, synced=None), dict(new, synced=None))
        self.assertEqual(new["synced"]["commit"], self.head())

    def test_unmapped_in_scope_change_is_reported(self):
        S.put(self.root, "tools/report.py", "X = 1\n")
        S.commit(self.root, "tool")
        _, changes = S.run_json(self.root, "changes")
        self.assertEqual((changes["affected"], changes["unmapped"]), ([], ["tools/report.py"]))
        code, out = self.status()
        self.assertEqual((code, out["up_to_date"], out["unmapped_files"]), (4, False, ["tools/report.py"]))

    def test_u6_dirty_working_tree(self):
        self.edit(RULE, lambda v: v.update(summary="Model edits are not dirt."))
        self.assertFalse(self.status()[1]["working_tree_dirty"])
        insert_line(self.root, "api/collect.py")
        self.assertTrue(self.status()[1]["working_tree_dirty"])
        code, out, err = S.run(self.root, "sync", "--mode", "update")
        self.assertEqual(json.loads(out.decode())["written"], [M.MANIFEST])
        self.assertIn("recording dirty", err)
        self.assertEqual(self.synced(), {"commit": self.head(), "mode": "update", "dirty": True})
        self.assertEqual(self.sync(), [])
        S.git(self.root, "add", "api/collect.py")
        S.git(self.root, "commit", "-q", "-m", "code")
        self.assertEqual(self.sync(), [M.MANIFEST])
        self.assertEqual(self.synced(), {"commit": self.head(), "mode": "update"})

    def install_skill(self):
        """What `npx skills add` leaves in a project: the skill, a symlink to it, and the lock file."""
        S.put(self.root, ".agents/skills/evermap/SKILL.md", "---\nname: evermap\n---\n")
        S.put(self.root, ".agents/skills/evermap/scripts/evermap.py", "X = 1\n")
        S.put(self.root, "skills-lock.json", "{}\n")
        os.makedirs(os.path.join(self.root, ".claude", "skills"))
        symlink(os.path.join("..", "..", ".agents", "skills", "evermap"),
                os.path.join(self.root, ".claude", "skills", "evermap"))
        installed = [".agents/skills/evermap/SKILL.md", ".agents/skills/evermap/scripts/evermap.py",
                     ".claude/skills/evermap", "skills-lock.json"]
        self.assertTrue(set(installed) <= set(cli.gitio.list_files(self.root)))  # Git lists the symlink as one file
        return installed

    def assert_ignored(self, installed):
        code, out = self.status()
        self.assertEqual((code, out["up_to_date"], out["changed_files_relevant"], out["unmapped_files"]),
                         (0, True, [], []))
        _, changes = S.run_json(self.root, "changes", "--limit", "0")
        self.assertEqual((changes["affected"], changes["unmapped"]), ([], []))
        self.assertTrue(all(f["components"] == [] for f in changes["files"]))
        _, coverage = S.run_json(self.root, "coverage", "--limit", "0")
        for path in installed:
            self.assertNotIn(path, coverage["unowned"] + coverage["declared_unmapped"])
        return out

    def test_installed_agent_skills_are_out_of_scope(self):
        installed = self.install_skill()
        out = self.assert_ignored(installed)
        self.assertTrue(out["working_tree_dirty"])  # dirty still means any change outside .evermap/
        S.commit(self.root, "install the skill")  # a commit of only the installed skill
        self.assertFalse(self.assert_ignored(installed)["working_tree_dirty"])
        self.assertEqual(self.sync(), [M.MANIFEST])
        self.assertEqual(self.synced(), {"commit": self.head(), "mode": "update"})
        self.assert_ignored(installed)

    def test_plain_skills_directory_stays_in_scope(self):
        S.put(self.root, "skills/app.py", "X = 1\n")
        _, changes = S.run_json(self.root, "changes")
        self.assertEqual(changes["unmapped"], ["skills/app.py"])
        _, coverage = S.run_json(self.root, "coverage")
        self.assertIn("skills/app.py", coverage["unowned"])

    def test_u6_untracked_file_is_dirty(self):
        S.put(self.root, "api/extra.py", "X = 1\n")
        self.assertTrue(self.status()[1]["working_tree_dirty"])

    def test_u7_non_ancestor_baseline(self):
        model_commit = self.head()
        S.git(self.root, "checkout", "-q", "-b", "side")
        insert_line(self.root, "api/collect.py")
        side = S.commit(self.root, "side change")
        S.git(self.root, "checkout", "-q", "main")
        insert_line(self.root, "worker/notify.py")
        S.commit(self.root, "main change")
        value = json.loads(S.read(self.root, M.MANIFEST))
        value["synced"]["commit"] = side
        S.put(self.root, M.MANIFEST, M.format_value(value, "manifest"))
        code, out, err = S.run(self.root, "changes")
        changes = json.loads(out.decode())
        self.assertEqual(code, 0)
        self.assertEqual(changes["baseline"], model_commit)
        self.assertIn("is not an ancestor of HEAD; using merge base %s" % model_commit[:12], changes["warning"])
        self.assertIn("is not an ancestor", err)
        self.assertEqual([f["path"] for f in changes["files"]], ["worker/notify.py"])
        self.assertEqual([a["id"] for a in changes["affected"]], ["component:notifier", "operation:recipient.notify"])
        code, status = self.status()
        self.assertEqual((code, status["warning"]), (4, changes["warning"]))


if __name__ == "__main__":
    unittest.main()
