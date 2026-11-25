"""Evidence anchoring and freshness (ROADMAP M1.5). Every expected class is exact."""

import json
import os
import shutil
import tempfile
import unittest

from tests import support as S
from evermap_lib import evidence as E

COLLECT, DEPOSIT, NOTIFY = "operation:parcel.collect", "operation:parcel.deposit", "operation:recipient.notify"
RULE, NOTIFIER = "rule:collection.window", "component:notifier"
TOTAL = 20  # file evidence items in the fixture model
COLLECT_ITEMS = [(COLLECT, "$.concurrency[0].evidence[0]", [16, 16]), (COLLECT, "$.evidence[0]", [10, 17]),
                 (RULE, "$.evidence[0]", [3, 7])]


def lines_of(root, path):
    return S.read(root, path).split("\n")[:-1]


def write_lines(root, path, lines, ending="\n"):
    S.put(root, path, "".join(line + ending for line in lines))


class Fixture(S.RepoCase):
    def check(self, *extra):
        code, out = S.run_json(self.root, "check-evidence", "--limit", "0", "--max-chars", "0", *extra)
        self.assertEqual(code, 0)
        found = {(r["id"], r["at"]): (r["state"], r.get("now", r.get("reason"))) for r in out["items"]}
        return out["counts"], found

    def expect(self, expected):
        counts, found = self.check()
        self.assertEqual(found, expected)
        states = [state for state, _ in expected.values()]
        wanted = {k: states.count(k) for k in ("moved", "stale", "missing", "unanchored")}
        wanted["fresh"] = TOTAL - len(states)
        self.assertEqual(counts, wanted)

    def moved(self, path, shift=0, to=None):
        return {(i, at): ("moved", {"path": to or path, "lines": [a + shift, b + shift]})
                for i, at, (a, b) in COLLECT_ITEMS}

    def test_unchanged_is_fresh(self):
        self.expect({})

    def test_line_inserted_above(self):
        lines = lines_of(self.root, "api/collect.py")
        write_lines(self.root, "api/collect.py", lines[:1] + ["# Pickup rules."] + lines[1:])
        self.expect(self.moved("api/collect.py", 1))

    def test_line_inserted_above_then_fixed(self):
        lines = lines_of(self.root, "api/collect.py")
        write_lines(self.root, "api/collect.py", lines[:1] + ["# Pickup rules."] + lines[1:])
        _, out = S.run_json(self.root, "check-evidence", "--fix-moved")
        self.assertEqual(out["written"], [".evermap/model/operations/parcel.collect.json",
                                          ".evermap/model/rules/collection.window.json"])
        self.expect({})

    def test_span_moved_within_file(self):
        lines = lines_of(self.root, "api/collect.py")
        write_lines(self.root, "api/collect.py", lines[0:2] + lines[7:17] + ["", ""] + lines[2:7])
        self.expect({(COLLECT, "$.concurrency[0].evidence[0]"): ("moved", {"path": "api/collect.py", "lines": [11, 11]}),
                     (COLLECT, "$.evidence[0]"): ("moved", {"path": "api/collect.py", "lines": [5, 12]}),
                     (RULE, "$.evidence[0]"): ("moved", {"path": "api/collect.py", "lines": [15, 19]})})

    def test_file_renamed_with_git_mv(self):
        S.git(self.root, "mv", "api/collect.py", "api/pickup.py")
        self.expect(self.moved("api/collect.py", to="api/pickup.py"))
        S.run_json(self.root, "check-evidence", "--fix-moved")
        self.expect({})
        self.assertEqual(S.run_json(self.root, "validate")[0], 0)

    def test_file_renamed_without_staging(self):
        os.rename(os.path.join(self.root, "api", "collect.py"), os.path.join(self.root, "api", "pickup.py"))
        self.expect(self.moved("api/collect.py", to="api/pickup.py"))

    def test_file_renamed_and_line_inserted(self):
        lines = lines_of(self.root, "api/collect.py")
        os.remove(os.path.join(self.root, "api", "collect.py"))
        write_lines(self.root, "api/pickup.py", lines[:1] + ["# Pickup rules."] + lines[1:])
        self.expect(self.moved("api/collect.py", 1, to="api/pickup.py"))

    def test_span_edited(self):
        S.put(self.root, "api/collect.py", S.read(self.root, "api/collect.py").replace("MAX_DAYS = 7", "MAX_DAYS = 9"))
        self.expect({(RULE, "$.evidence[0]"): ("stale", "the span changed")})

    def test_span_deleted(self):
        lines = lines_of(self.root, "api/collect.py")
        write_lines(self.root, "api/collect.py", lines[0:2] + lines[7:])
        self.expect({(COLLECT, "$.concurrency[0].evidence[0]"): ("moved", {"path": "api/collect.py", "lines": [11, 11]}),
                     (COLLECT, "$.evidence[0]"): ("moved", {"path": "api/collect.py", "lines": [5, 12]}),
                     (RULE, "$.evidence[0]"): ("stale", "the span changed")})

    def test_file_deleted(self):
        os.remove(os.path.join(self.root, "worker", "notify.py"))
        self.expect({(NOTIFIER, "$.evidence[0]"): ("missing", "file not found"),
                     (NOTIFY, "$.evidence[0]"): ("missing", "file not found")})

    def test_file_deleted_and_committed(self):
        S.git(self.root, "rm", "-q", "worker/notify.py")
        S.commit(self.root, "drop worker")
        self.expect({(NOTIFIER, "$.evidence[0]"): ("missing", "file not found"),
                     (NOTIFY, "$.evidence[0]"): ("missing", "file not found")})

    def test_symbol_gone(self):
        S.put(self.root, "worker/notify.py",
              S.read(self.root, "worker/notify.py").replace("def on_parcel_stored", "def on_stored"))
        reason = "symbol 'on_parcel_stored' not found"
        self.expect({(NOTIFIER, "$.evidence[0]"): ("missing", reason), (NOTIFY, "$.evidence[0]"): ("missing", reason)})

    def test_crlf_checkout_stays_fresh(self):
        for path in ("api/deposit.py", "api/collect.py", "db/schema.sql", "worker/notify.py"):
            write_lines(self.root, path, lines_of(self.root, path), "\r\n")
        self.expect({})

    def test_trailing_whitespace_stays_fresh(self):
        for path in ("api/deposit.py", "db/schema.sql"):
            write_lines(self.root, path, [line + " \t " for line in lines_of(self.root, path)])
        self.expect({})

    def test_missing_final_newline_stays_fresh(self):
        S.put(self.root, "api/collect.py", S.read(self.root, "api/collect.py").rstrip("\n"))
        self.expect({})


class Duplicates(unittest.TestCase):
    """The recorded span appears twice after an edit."""

    BEFORE = '"""Boxes."""\ndef store(box):\n    with box.lock():\n        box.put()\n'
    AFTER = ("def fetch(box):\n    with box.lock():\n        box.put()\n\n\n"
             "def store(box):\n    with box.lock():\n        box.put()\n")

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="evermap-dup-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def classify(self, after, symbol=None, lines=(3, 4)):
        S.put(self.root, "boxes.py", self.BEFORE)
        span = E.split_lines(self.BEFORE.encode())[lines[0] - 1:lines[1]]
        item = {"type": "code", "path": "boxes.py", "lines": list(lines), "hash": E.hash_lines(span)}
        if symbol:
            item["symbol"] = symbol
        S.put(self.root, "boxes.py", after)
        return E.Checker(self.root).classify(item)

    def test_without_symbol_is_stale(self):
        self.assertEqual(self.classify(self.AFTER), {"state": "stale", "reason": "the span occurs more than once"})

    def test_recorded_symbol_does_not_pick_a_copy(self):
        """The enclosing definition is not guessed from the lines above: that could re-anchor to the wrong one."""
        self.assertEqual(self.classify(self.AFTER, "store"), {"state": "stale", "reason": "the span occurs more than once"})

    def test_recorded_symbol_before_the_copy_is_stale(self):
        after = ("def store(box):\n    with box.lock():\n        box.put()\n\n\n"
                 "def fetch(box):\n    with box.lock():\n        box.put()\n")
        self.assertEqual(self.classify(after, "store"), {"state": "stale", "reason": "the span occurs more than once"})

    def test_symbol_in_every_copy_is_stale(self):
        self.assertEqual(self.classify(self.AFTER, "box"), {"state": "stale", "reason": "the span occurs more than once"})

    def test_single_copy_with_symbol_is_moved(self):
        after = '"""Boxes."""\ndef store(box):\n\n    with box.lock():\n        box.put()\n'
        self.assertEqual(self.classify(after, "store"), {"state": "moved", "path": "boxes.py", "lines": [4, 5]})

    def test_symbol_gone_with_copies_is_missing(self):
        self.assertEqual(self.classify(self.AFTER, "keep"), {"state": "missing", "reason": "symbol 'keep' not found"})

    def test_copy_added_while_span_stays_put_is_fresh(self):
        self.assertEqual(self.classify(self.BEFORE + self.AFTER, "store"), {"state": "fresh"})

    def test_hash_normalisation(self):
        self.assertEqual(E.split_lines(b"a \r\nb\t\r\n\r\n"), [b"a", b"b", b""])
        self.assertEqual(E.hash_lines([b"a"]), E.hash_lines(E.split_lines(b"a  \r\n")))
        self.assertRegex(E.hash_lines([b"a"]), r"^sha256:[0-9a-f]{16}$")


class Anchor(S.RepoCase):
    def rule(self):
        return json.loads(S.read(self.root, ".evermap/model/rules/collection.window.json"))

    def make_stale(self):
        text = S.read(self.root, "api/collect.py").replace("MAX_DAYS = 7", "MAX_DAYS = 9")
        S.put(self.root, "api/collect.py", text.replace("status = 'stored'\"", "status = 'ready'\""))

    def stale_ids(self):
        _, out = S.run_json(self.root, "check-evidence", "--limit", "0", "--max-chars", "0")
        return sorted((r["id"], r["at"]) for r in out["items"] if r["state"] == "stale")

    def test_fills_only_absent_hashes(self):
        self.make_stale()
        self.edit(RULE, lambda v: v["evidence"].append(
            {"type": "code", "path": "api/collect.py", "lines": [14, 15], "symbol": "is_expired"}))
        old_hash = self.rule()["evidence"][0]["hash"]
        code, out = S.run_json(self.root, "anchor")
        self.assertEqual(code, 0)
        self.assertEqual(out["anchored"], [{"id": RULE, "at": "$.evidence[1]"}])
        self.assertEqual(out["written"], [".evermap/model/rules/collection.window.json"])
        evidence = self.rule()["evidence"]
        self.assertEqual(evidence[0]["hash"], old_hash)
        self.assertRegex(evidence[1]["hash"], r"^sha256:[0-9a-f]{16}$")

    def test_never_hides_staleness(self):
        self.make_stale()
        before = S.tree(self.root)
        stale = self.stale_ids()
        self.assertEqual(stale, [(COLLECT, "$.concurrency[0].evidence[0]"), (COLLECT, "$.evidence[0]"),
                                 (RULE, "$.evidence[0]")])
        for args in ((), (RULE,), (RULE, COLLECT)):
            code, out = S.run_json(self.root, "anchor", *args)
            self.assertEqual((code, out["anchored"], out["written"]), (0, [], []))
        self.assertEqual(S.tree(self.root), before)
        self.assertEqual(self.stale_ids(), stale)
        code, findings = self.findings("--strict")
        self.assertEqual(code, 1)
        self.assertEqual(sorted((f["id"], f["at"]) for f in findings if f["rule"] == "V7"), stale)

    def test_refresh_requires_explicit_ids(self):
        self.make_stale()
        before = S.tree(self.root)
        code, out, err = S.run(self.root, "anchor", "--refresh")
        self.assertEqual((code, out), (2, b""))
        self.assertIn("--refresh needs explicit element ids", err)
        self.assertEqual(S.tree(self.root), before)

    def test_refresh_replaces_only_named_elements(self):
        self.make_stale()
        code, out = S.run_json(self.root, "anchor", "--refresh", RULE)
        self.assertEqual(code, 0)
        self.assertEqual(out["anchored"], [{"id": RULE, "at": "$.evidence[0]"}])
        self.assertEqual(self.stale_ids(), [(COLLECT, "$.concurrency[0].evidence[0]"), (COLLECT, "$.evidence[0]")])

    def test_refresh_dry_run_writes_nothing(self):
        self.make_stale()
        before = S.tree(self.root)
        code, out = S.run_json(self.root, "anchor", "--refresh", "--dry-run", RULE)
        self.assertEqual((code, out["anchored"], out["written"]), (0, [{"id": RULE, "at": "$.evidence[0]"}], []))
        self.assertEqual(S.tree(self.root), before)

    def test_unknown_or_partial_ids_are_usage_errors(self):
        for ident in ("rule:gone", RULE + "#part"):
            self.assertEqual(S.run(self.root, "anchor", "--refresh", ident)[0], 2)

    def test_reports_bad_ranges_without_writing(self):
        self.edit(RULE, lambda v: v["evidence"].append({"type": "code", "path": "api/collect.py", "lines": [40, 41]}))
        before = S.tree(self.root)
        code, out = S.run_json(self.root, "anchor")
        self.assertEqual(code, 1)
        self.assertEqual([p["at"] for p in out["problems"]], ["$.evidence[1]"])
        self.assertEqual(S.tree(self.root), before)


if __name__ == "__main__":
    unittest.main()
