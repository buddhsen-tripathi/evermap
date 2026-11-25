"""estimate: approximate input tokens per component (SPEC 5.12, 6.3)."""

import json
import os
import unittest

from tests import support as S
from evermap_lib import cli

GROUPS = {
    "component:locker-api": ["api/collect.py", "api/deposit.py", "api/routes.py"],
    "component:locker-db": ["db/schema.sql"],
    "component:notifier": ["worker/notify.py"],
    "unowned:tests/": ["tests/test_deposit.py"],
}


def chars(root, paths):
    return sum(len(S.read(root, p).replace("\r\n", "\n")) for p in paths)


def symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        raise unittest.SkipTest("symbolic links unavailable: %s" % exc)


class Estimate(S.RepoCase):
    def estimate(self, *args):
        code, out = S.run_json(self.root, "estimate", "--limit", "0", *args)
        self.assertEqual(code, 0, out)
        return out

    def reference_tokens(self, names=("model.md", "discover.md", "functional.md")):
        """Tokens of the guides a worker reads (init packet by default)."""
        return sum((os.path.getsize(os.path.join(S.SKILL, "references", n)) + 3) // 4 for n in names)

    def test_overhead_counts_the_guides_each_packet_type_reads(self):
        out = self.estimate()
        self.assertEqual(out["worker_reads"], ["model.md", "discover.md", "functional.md"])
        changed = self.estimate("--changed")
        self.assertEqual(changed["worker_reads"], ["model.md", "update.md"])
        self.assertEqual(changed["overhead_per_worker"],
                         cli.STARTUP_TOKENS + self.reference_tokens(("model.md", "update.md")))

    def test_final_pass_only_for_init_plans_of_several_packets(self):
        self.assertEqual(self.estimate()["final_pass"]["workers"], 0)
        out = self.estimate("--packet-tokens", "200")
        self.assertGreater(out["workers"], 1)
        final = out["final_pass"]
        self.assertEqual(final["workers"], 1)
        self.assertEqual(final["expected_billed_input_tokens"],
                         {"low": 8 * out["overhead_per_worker"], "high": 25 * out["overhead_per_worker"]})
        S.put(self.root, "api/collect.py", S.read(self.root, "api/collect.py") + "# x\n" * 400)
        S.put(self.root, "api/deposit.py", S.read(self.root, "api/deposit.py") + "# x\n" * 400)
        self.assertEqual(self.estimate("--changed", "--packet-tokens", "200")["final_pass"]["workers"], 0)

    def test_counts_per_component(self):
        out = self.estimate()
        expected = [{"group": g, "files": len(p), "characters": chars(self.root, p),
                     "tokens": (chars(self.root, p) + 3) // 4} for g, p in sorted(GROUPS.items())]
        self.assertEqual(out["groups"], expected)
        total = chars(self.root, [p for paths in GROUPS.values() for p in paths])
        self.assertEqual((out["files"], out["characters"], out["code_tokens"]), (6, total, (total + 3) // 4))
        self.assertEqual(out["skipped"], {"binary": 0, "link": 0, "unreadable": 0})
        overhead = cli.STARTUP_TOKENS + self.reference_tokens()
        # A small repository fits one packet, so one worker pays the overhead once.
        self.assertEqual((out["workers"], out["overhead_per_worker"]), (1, overhead))
        self.assertEqual(out["packets"], [{"packet": 1, "tokens": sum(g["tokens"] for g in expected),
                                           "files": 6, "groups": sorted(GROUPS)}])
        self.assertEqual(out["estimated_input_tokens"], out["code_tokens"] + overhead)
        self.assertIs(out["approximate"], True)
        self.assertIn("Approximate", out["note"])

    def test_billed_range_counts_resent_context(self):
        out = self.estimate("--packet-tokens", "1")
        contexts = [out["overhead_per_worker"] + p["tokens"] for p in out["packets"]]
        billed = out["expected_billed_input_tokens"]
        self.assertEqual((billed["low"], billed["high"]), (8 * sum(contexts), 25 * sum(contexts)))
        self.assertIn("An assumption until EverBench calibrates it", billed["assumption"])
        self.assertIn("8 to 25 turns", billed["assumption"])
        self.assertIn("cached", billed["assumption"])

    def test_packets_keep_components_whole(self):
        tokens = {g["group"]: g["tokens"] for g in self.estimate()["groups"]}
        limit = tokens["component:locker-api"]
        out = self.estimate("--packet-tokens", str(limit))
        self.assertEqual(out["packets"][0]["groups"], ["component:locker-api"])
        flat = [g for p in out["packets"] for g in p["groups"]]
        self.assertEqual(flat, sorted(GROUPS))  # deterministic order, each group once
        self.assertTrue(all(p["tokens"] <= limit for p in out["packets"]))
        self.assertEqual(out["workers"], len(out["packets"]))
        self.assertEqual(out["estimated_input_tokens"], out["code_tokens"] + out["workers"] * out["overhead_per_worker"])

    def test_a_component_over_the_limit_is_split_by_file(self):
        sizes = {p: (chars(self.root, [p]) + 3) // 4 for p in GROUPS["component:locker-api"]}
        limit = max(sizes.values())
        out = self.estimate("--packet-tokens", str(limit))
        parts = [g for p in out["packets"] for g in p["groups"] if isinstance(g, dict)]
        self.assertTrue(parts)
        self.assertEqual({g["group"] for g in parts}, {"component:locker-api"})
        self.assertEqual([g["part"] for g in parts], list(range(1, len(parts) + 1)))
        self.assertEqual({g["of"] for g in parts}, {len(parts)})
        self.assertEqual(parts[0]["first"], "api/collect.py")
        self.assertEqual(parts[-1]["last"], "api/routes.py")
        self.assertEqual(sum(p["files"] for p in out["packets"]), 6)

    def test_no_packet_limit_and_no_files(self):
        self.assertEqual(self.estimate("--packet-tokens", "0")["workers"], 1)
        nothing = self.estimate("--changed")
        self.assertEqual((nothing["packets"], nothing["expected_billed_input_tokens"]["high"]), ([], 0))

    def test_startup_tokens_option(self):
        out = self.estimate("--startup-tokens", "0")
        self.assertEqual(out["overhead_per_worker"], self.reference_tokens())

    def test_unowned_files_are_grouped_by_top_level_directory(self):
        S.put(self.root, "tools/report.py", "X = 1\n")
        S.put(self.root, "setup.cfg", "[x]\n")
        S.put(self.root, "docs/guide.md", "Out of scope.\n")
        groups = {g["group"]: g["files"] for g in self.estimate()["groups"]}
        self.assertEqual((groups["unowned:tools/"], groups["unowned:."]), (1, 1))
        self.assertNotIn("unowned:docs/", groups)

    def test_paging(self):
        _, out = S.run_json(self.root, "estimate", "--limit", "1", "--offset", "1")
        self.assertEqual(([g["group"] for g in out["groups"]], out["total_groups"]), (["component:locker-db"], 4))

    def test_binary_files_are_skipped_and_counted(self):
        S.put(self.root, "api/logo.bin", "PNG\0data")
        S.put(self.root, "api/late_nul.txt", "x" * 8000 + "\0")  # NUL after the first 8,000 bytes: text
        out = self.estimate()
        self.assertEqual(out["skipped"]["binary"], 1)
        api = out["groups"][0]
        self.assertEqual((api["files"], api["characters"]),
                         (4, chars(self.root, GROUPS["component:locker-api"]) + 8001))

    def test_crlf_counts_like_lf(self):
        before = self.estimate()
        lines = S.read(self.root, "api/collect.py").replace("\r\n", "\n")  # a Windows checkout has CRLF already
        S.put(self.root, "api/collect.py", lines.replace("\n", "\r\n"))
        self.assertEqual(self.estimate(), before)
        S.put(self.root, "api/collect.py", lines)
        self.assertEqual(self.estimate(), before)

    def test_changed_only(self):
        nothing = self.estimate("--changed")
        self.assertEqual((nothing["files"], nothing["workers"], nothing["estimated_input_tokens"]), (0, 0, 0))
        S.put(self.root, "api/collect.py", S.read(self.root, "api/collect.py") + "# Changed.\n")
        S.put(self.root, "docs/guide.md", "Out of scope.\n")
        os.remove(os.path.join(self.root, "db", "schema.sql"))
        out = self.estimate("--changed")
        self.assertEqual(out["mode"], "changed")
        self.assertEqual(out["groups"], [{"group": "component:locker-api", "files": 1,
                                          "characters": chars(self.root, ["api/collect.py"]),
                                          "tokens": (chars(self.root, ["api/collect.py"]) + 3) // 4}])
        S.commit(self.root, "change")
        self.assertEqual(self.estimate("--changed", "--since", "HEAD")["files"], 0)
        self.assertEqual(self.estimate("--changed", "--since", "HEAD~1")["files"], 1)

    def test_since_needs_changed(self):
        code, out, err = S.run(self.root, "estimate", "--since", "HEAD")
        self.assertEqual((code, out), (2, b""))
        self.assertIn("--since needs --changed", err)

    def test_symlinks_are_never_followed(self):
        outside = os.path.join(self.base, "outside.py")
        S.put(self.base, "outside.py", "SECRET = 1\n" * 1000)
        symlink(outside, os.path.join(self.root, "api", "outside.py"))
        symlink("collect.py", os.path.join(self.root, "api", "again.py"))
        out = self.estimate()
        self.assertEqual(out["skipped"]["link"], 2)
        self.assertEqual(out["groups"][0]["characters"], chars(self.root, GROUPS["component:locker-api"]))
        self.assertNotIn("SECRET", S.run(self.root, "estimate", "--format", "md")[1].decode())

    def test_writes_nothing_and_prints_no_contents(self):
        before = S.tree(self.root)
        code, out, _ = S.run(self.root, "estimate")
        self.assertEqual(code, 0)
        self.assertEqual(S.tree(self.root), before)
        self.assertNotIn(b"collect_parcel", out)


PLAN = ".evermap/plan.json"


class Plan(S.RepoCase):
    def write_plan(self, *args):
        code, out = S.run_json(self.root, "estimate", "--write-plan", *args)
        self.assertEqual(code, 0, out)
        return out

    def plan(self):
        return json.loads(S.read(self.root, PLAN))

    def packet(self, number, *args):
        code, out = S.run_json(self.root, "packet", str(number), *args)
        self.assertEqual(code, 0, out)
        return out

    def test_plan_holds_file_lists_the_output_does_not(self):
        out = self.write_plan()
        self.assertEqual(out["plan"], PLAN)
        self.assertEqual([sorted(p) for p in out["packets"]], [["files", "groups", "packet", "tokens"]])
        self.assertEqual(out["packets"][0]["files"], 6)
        plan = self.plan()
        self.assertEqual(plan["packets"][0]["files"], sorted(p for paths in GROUPS.values() for p in paths))
        self.assertEqual(plan["scope"]["include"], ["**"])
        text = S.read(self.root, PLAN)
        self.assertEqual(text, json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        self.assertNotIn("collect_parcel", text)  # no file contents
        self.assertNotIn(b"api/collect.py", S.run(self.root, "estimate", "--write-plan")[1])

    def test_plan_is_deterministic(self):
        first = (S.run(self.root, "estimate", "--write-plan", "--packet-tokens", "300")[1], S.read(self.root, PLAN))
        second = (S.run(self.root, "estimate", "--write-plan", "--packet-tokens", "300")[1], S.read(self.root, PLAN))
        self.assertEqual(first, second)
        self.assertEqual(S.run(self.root, "packet", "1")[1], S.run(self.root, "packet", "1")[1])

    def test_packet_lists_scope_files_and_current_ids(self):
        self.write_plan()
        out = self.packet(1, "--limit", "0")
        self.assertEqual((out["packet"], out["of"], out["mode"]), (1, 1, "all"))
        self.assertEqual(out["reads"], ["model.md", "discover.md", "functional.md"])
        self.assertEqual(out["files"], self.plan()["packets"][0]["files"])
        self.assertEqual(out["missing"], [])
        self.assertEqual(out["scope"], self.plan()["scope"])
        self.assertEqual(out["ids"], [{"id": i, "name": e.get("name") or e.get("title")}
                                      for i, e in sorted(S.elements().items())])
        self.assertNotIn("summary", json.dumps(out["ids"]))
        S.write_element(self.root, S.element("role:recipient", "Recipient", "Collects.", type="user-role",
                                             provenance="inferred", rationale="Named."))
        self.assertIn({"id": "role:recipient", "name": "Recipient"}, self.packet(1, "--limit", "0")["ids"])

    def test_numbering_is_stable_after_components_are_added(self):
        self.write_plan("--packet-tokens", "200")
        self.assertGreater(len(self.plan()["packets"]), 2)
        before = [self.packet(n, "--limit", "0")["files"] for n in range(1, len(self.plan()["packets"]) + 1)]
        S.write_element(self.root, S.element("component:tests", "Tests", "Test suite.", plain="The tests.",
                                             type="module", paths=["tests/**"], provenance="inferred",
                                             rationale="Directory name."))
        S.put(self.root, "api/new.py", "X = 1\n" * 300)
        self.assertNotEqual(S.run_json(self.root, "estimate", "--packet-tokens", "200")[1]["packets"][0]["groups"],
                            self.plan()["packets"][0]["groups"])
        after = [self.packet(n, "--limit", "0")["files"] for n in range(1, len(before) + 1)]
        self.assertEqual(after, before)

    def test_missing_files_are_marked(self):
        self.write_plan()
        os.remove(os.path.join(self.root, "db", "schema.sql"))
        out = self.packet(1, "--limit", "0")
        self.assertEqual(out["missing"], ["db/schema.sql"])
        self.assertIn("db/schema.sql", out["files"])

    def test_paging_and_bounds(self):
        self.write_plan()
        out = self.packet(1, "--limit", "1", "--offset", "1")
        self.assertEqual((out["files"], out["total_files"], len(out["ids"]), out["total_ids"]),
                         (["api/deposit.py"], 6, 1, len(S.ELEMENTS)))
        code, out, _ = S.run(self.root, "packet", "1", "--max-chars", "400")
        self.assertLessEqual(len(out.decode()), 1000)
        self.assertIn("truncated", json.loads(out.decode()))

    def test_packet_writes_nothing(self):
        self.write_plan()
        before = S.tree(self.root)
        self.packet(1)
        self.assertEqual(S.tree(self.root), before)

    def test_usage_errors(self):
        code, out, err = S.run(self.root, "packet", "1")
        self.assertEqual((code, out), (2, b""))
        self.assertIn("no plan", err)
        self.write_plan()
        for number in ("0", "2", "-1"):
            code, out, err = S.run(self.root, "packet", number)
            self.assertEqual((code, out), (2, b""), number)
        code, _, err = S.run(self.root, "apply", PLAN)
        self.assertEqual(code, 2)

    def test_links_are_refused(self):
        outside = os.path.join(self.base, "outside.json")
        S.put(self.base, "outside.json", json.dumps({"packets": [{"files": ["/etc/passwd"]}]}))
        symlink(outside, os.path.join(self.root, ".evermap", "plan.json"))
        code, out, err = S.run(self.root, "packet", "1")
        self.assertEqual((code, out), (2, b""))
        self.assertIn("symbolic link", err)
        code, _, err = S.run(self.root, "estimate", "--write-plan")
        self.assertNotEqual(code, 0)
        self.assertIn("symbolic link", err)
        with open(outside, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"packets": [{"files": ["/etc/passwd"]}]})
        code, out = S.run_json(self.root, "sync", "--mode", "update")
        self.assertEqual((code, out["removed"]), (0, []))
        self.assertTrue(os.path.islink(os.path.join(self.root, ".evermap", "plan.json")))

    def test_sync_deletes_the_plan_and_status_reports_it(self):
        self.write_plan("--packet-tokens", "200")
        _, status = S.run_json(self.root, "status")
        self.assertEqual(status["plan"], {"packets": len(self.plan()["packets"])})
        self.assertEqual((status["changed_files"], status["views_out_of_sync"]), (0, []))
        code, out = S.run_json(self.root, "validate", "--strict", "--limit", "0")
        self.assertEqual((code, out["findings"]), (0, []))  # validate ignores the plan
        code, out = S.run_json(self.root, "sync", "--mode", "update")
        self.assertEqual((code, out["removed"]), (0, [PLAN]))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".evermap", "plan.json")))
        self.assertNotIn("plan", S.run_json(self.root, "status")[1])

    def test_plan_is_scanned_for_secrets(self):
        S.put(self.root, "api/sk_live_" + "a" * 24 + ".py", "X = 1\n")
        self.write_plan()
        _, out = S.run_json(self.root, "validate", "--limit", "0")
        self.assertIn((PLAN, "V9"), [(f["path"], f["rule"]) for f in out["findings"]])

    def test_odd_high_entropy_path_in_the_plan_does_not_block(self):
        odd = "api/Xq7Lm2Pz9Rw4Tk8Vn3Bc6Hd.py"  # random-looking name, no credential pattern
        S.put(self.root, odd, "X = 1\n")
        from evermap_lib import secrets
        self.assertEqual(secrets.scan_entropy(odd.split("/")[1]), ["high-entropy string"])  # control
        self.write_plan()
        self.assertIn(odd, self.plan()["packets"][0]["files"])
        code, out = S.run_json(self.root, "validate", "--limit", "0")
        self.assertNotIn(PLAN, [f["path"] for f in out["findings"]])
        self.assertEqual(code, 0)

    def test_budget_stop_keeps_the_plan_for_resuming(self):
        self.write_plan("--packet-tokens", "200")
        total = len(self.plan()["packets"])
        before = S.read(self.root, PLAN)
        self.packet(total)  # a later session runs the remaining numbers
        self.assertEqual(S.read(self.root, PLAN), before)
        self.assertEqual(S.run_json(self.root, "status")[1]["plan"], {"packets": total})


if __name__ == "__main__":
    unittest.main()
