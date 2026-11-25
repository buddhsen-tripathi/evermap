"""apply: write a draft of elements, then anchor, format and validate in one call (SPEC 6.3)."""

import json
import os
import shutil
import unittest

from tests import support as S
from evermap_lib import model as M

DRAFT = ".evermap/draft.json"
RULE, DECISION = "rule:collection.window", "decision:skip-locked-assignment"
ROLE = S.element("role:recipient", "Recipient", "Person collecting a parcel.", plain="Someone picking up a parcel.",
                 type="user-role", evidence=S.ev("api/collect.py", [10, 12], "collect_parcel"))


def symlink(target, link):
    try:
        os.symlink(target, link, target_is_directory=os.path.isdir(target))
    except (OSError, NotImplementedError) as exc:
        raise unittest.SkipTest("symbolic links unavailable: %s" % exc)


class Apply(S.RepoCase):
    def draft(self, value, path=DRAFT):
        S.put(self.root, path, json.dumps(value))

    def apply(self, *args):
        code, out = S.run_json(self.root, "apply", "--limit", "0", "--max-chars", "0", *args)
        return code, out

    def element(self, ident):
        return json.loads(S.read(self.root, M.path_for_id(ident)))

    def outside_model(self):
        return {p: d for p, d in S.tree(self.root).items() if not p.startswith(".evermap/")}

    def test_writes_anchors_formats_validates_and_deletes_the_draft(self):
        rule = S.elements()[RULE]
        rule["summary"] = "Parcels expire after a week."
        rule["evidence"][0].pop("hash", None)
        self.draft([ROLE, rule])
        before = S.tree(self.root)
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertEqual((out["elements"], out["written"], out["unchanged"], out["rejected"]), (2, 2, 0, []))
        self.assertTrue(out["draft_deleted"])
        self.assertFalse(os.path.exists(os.path.join(self.root, *DRAFT.split("/"))))
        self.assertTrue(out["valid"])
        self.assertEqual({f["rule"] for f in out["findings"]}, {"V15"})  # views need render: a warning
        role = self.element("role:recipient")
        self.assertRegex(role["evidence"][0]["hash"], r"^sha256:[0-9a-f]{16}$")
        self.assertEqual(S.read(self.root, M.path_for_id("role:recipient")),
                         M.format_value(role, "role"))
        changed = {p for p, d in S.tree(self.root).items() if before.get(p) != d}
        self.assertEqual(changed, {M.path_for_id("role:recipient"), M.path_for_id(RULE)})
        self.assertEqual(self.element(RULE)["evidence"], json.loads(before[M.path_for_id(RULE)])["evidence"])

    def test_single_object_and_unchanged(self):
        self.draft(self.element(RULE))
        code, out = self.apply()
        self.assertEqual((code, out["written"], out["unchanged"]), (0, 0, 1))

    def test_unchanged_span_keeps_its_hash_so_staleness_stays_visible(self):
        S.put(self.root, "api/collect.py", S.read(self.root, "api/collect.py").replace("MAX_DAYS = 7", "MAX_DAYS = 9"))
        old = self.element(RULE)
        draft = json.loads(json.dumps(old))
        draft["evidence"][0]["hash"] = "sha256:" + "0" * 16  # draft hashes are never trusted
        draft["summary"] = "Changed."
        self.draft([draft])
        code, out = self.apply()
        self.assertEqual(self.element(RULE)["evidence"][0]["hash"], old["evidence"][0]["hash"])
        _, check = S.run_json(self.root, "check-evidence")
        self.assertIn(RULE, [i["id"] for i in check["items"] if i["state"] == "stale"])

    def test_changed_span_is_re_anchored(self):
        old = self.element(RULE)
        draft = json.loads(json.dumps(old))
        draft["evidence"][0]["lines"] = [3, 3]
        self.draft([draft])
        self.assertEqual(self.apply()[0], 0)
        new = self.element(RULE)["evidence"][0]
        self.assertNotEqual(new["hash"], old["evidence"][0]["hash"])
        self.assertRegex(new["hash"], r"^sha256:[0-9a-f]{16}$")

    def test_invalid_element_is_rejected_others_written_draft_kept(self):
        bad = dict(ROLE, id="role:bad", type="robot")
        self.draft([bad, ROLE])
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertEqual(out["written"], 1)
        self.assertEqual({(r["index"], r["id"], r["rule"]) for r in out["rejected"]}, {(0, "role:bad", "V1")})
        self.assertFalse(out["draft_deleted"])
        self.assertTrue(os.path.exists(os.path.join(self.root, ".evermap", "draft.json")))
        self.assertFalse(os.path.exists(os.path.join(self.root, *M.path_for_id("role:bad").split("/"))))
        self.assertIn({"rule": "V1", "severity": "warning", "path": DRAFT,
                       "message": "leftover draft: run apply to write it, or delete it"}, out["findings"])

    def test_malicious_ids_write_nothing(self):
        before = S.tree(self.root)
        ids = ["rule:../../escape", "rule:a/b", "../../escape", "rule:x\u0000y", "decision:../x", "rule:", 42, None,
               "Rule:x", "rule:x ", "rule:x.json/..", "evermap.json", "rule:..", "rule:x..y", "/etc/passwd",
               "component:" + "a" * 5000 + "/b"]
        self.draft([dict(ROLE, id=i, kind="rule") for i in ids])
        code, out = self.apply()
        self.assertEqual((code, out["written"], out["unchanged"]), (1, 0, 0))
        self.assertEqual(sorted({r["index"] for r in out["rejected"]}), list(range(len(ids))))
        self.assertTrue(all(r["rule"] == "V2" for r in out["rejected"]))
        after = S.tree(self.root)
        self.assertEqual({p for p in set(before) | set(after) if before.get(p) != after.get(p)}, {DRAFT})
        self.assertEqual(sorted(os.listdir(self.base)), ["parcels"])

    def test_kind_must_match_the_id(self):
        self.draft([dict(ROLE, kind="rule")])
        _, out = self.apply()
        self.assertEqual([(r["rule"], r["at"]) for r in out["rejected"]], [("V2", "$.kind")])

    def test_duplicate_ids_are_all_rejected(self):
        self.draft([ROLE, dict(ROLE, summary="Other.")])
        code, out = self.apply()
        self.assertEqual((code, out["written"], [r["index"] for r in out["rejected"]]), (1, 0, [0, 1]))

    def test_never_overwrites_another_element(self):
        S.put(self.root, M.path_for_id("role:recipient"), M.format_value(dict(ROLE, id="role:other"), "role"))
        before = S.read(self.root, M.path_for_id("role:recipient"))
        self.draft([ROLE])
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("holds something else", out["rejected"][0]["message"])
        self.assertEqual(S.read(self.root, M.path_for_id("role:recipient")), before)

    def test_other_elements_are_not_anchored_or_formatted(self):
        path = M.path_for_id(RULE)
        S.put(self.root, path, S.read(self.root, path).replace('  "kind"', '     "kind"'))
        self.edit("role:courier", lambda v: v["evidence"][0].pop("hash"))
        before = S.tree(self.root, ".evermap/model")
        self.draft([ROLE])
        _, out = self.apply()
        after = S.tree(self.root, ".evermap/model")
        self.assertEqual({p for p in after if after[p] != before.get(p)}, {M.path_for_id("role:recipient")})
        self.assertIn("V13", {f["rule"] for f in out["findings"]})  # still reported by validation

    def test_conflicted_file_is_replaced_after_a_merge(self):
        path = M.path_for_id(RULE)
        good = S.read(self.root, path)
        S.put(self.root, path, "<<<<<<< ours\n" + good + "=======\n" + good.replace("7", "9") + ">>>>>>> theirs\n")
        self.draft([json.loads(good)])
        code, out = self.apply()
        self.assertEqual((out["rejected"], out["written"]), ([], 1))
        self.assertEqual(json.loads(S.read(self.root, path))["id"], RULE)
        S.put(self.root, path, "not json at all\n")  # unparseable without conflict markers: kept
        self.draft([json.loads(good)])
        _, out = self.apply()
        self.assertIn("holds something else", out["rejected"][0]["message"])

    def test_conflicted_decision_needs_allow_decisions(self):
        path = M.path_for_id(DECISION)
        good = S.read(self.root, path)
        S.put(self.root, path, "<<<<<<< ours\n" + good + "=======\n" + good + ">>>>>>> theirs\n")
        self.draft([json.loads(good)])
        _, out = self.apply()
        self.assertEqual(out["rejected"][0]["rule"], "V11")
        self.draft([json.loads(good)])
        self.assertEqual(self.apply("--allow-decisions")[1]["rejected"], [])

    def test_secrets_and_proposed_lifecycle_are_rejected(self):
        leaky = dict(ROLE, id="role:leaky", summary="Key sk_live_" + "a" * 24 + " is used.")
        proposed = dict(ROLE, id="role:later", lifecycle="proposed")
        self.draft([leaky, proposed])
        _, out = self.apply()
        self.assertEqual({(r["id"], r["rule"]) for r in out["rejected"]}, {("role:leaky", "V9"), ("role:later", "V5")})

    def test_decisions_need_allow_decisions(self):
        existing = self.element(DECISION)
        changed = dict(existing, title="Assign lockers with row locks")
        confirmed = {"id": "decision:new-choice", "kind": "decision", "title": "A new choice", "status": "proposed",
                     "problem": {"text": "Why.", "provenance": "confirmed"},
                     "decision": {"text": "What.", "provenance": "inferred"},
                     "evidence": [{"type": "stated", "who": "maintainer", "date": "2026-01-01", "source": "conversation"}]}
        inferred = dict(confirmed, id="decision:candidate", problem={"text": "Why.", "provenance": "inferred"})
        self.draft([changed, confirmed, inferred])
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertEqual({(r["id"], r["rule"]) for r in out["rejected"]}, {(DECISION, "V11"), ("decision:new-choice", "V11")})
        self.assertEqual(self.element(DECISION), existing)
        self.assertEqual(self.element("decision:candidate")["problem"]["provenance"], "inferred")
        self.draft([changed, confirmed])
        code, out = self.apply("--allow-decisions")
        self.assertEqual((out["rejected"], out["written"]), ([], 2))
        self.assertEqual(self.element(DECISION)["title"], "Assign lockers with row locks")

    def test_unchanged_decision_is_accepted(self):
        self.draft([self.element(DECISION)])
        code, out = self.apply()
        self.assertEqual((code, out["unchanged"], out["rejected"]), (0, 1, []))

    def test_draft_path_must_stay_inside_the_model(self):
        outside = os.path.join(self.base, "outside.json")
        S.put(self.base, "outside.json", json.dumps([ROLE]))
        S.put(self.root, "app.json", json.dumps([ROLE]))
        self.draft([ROLE], ".evermap/views/x.json")
        for path in ("../outside.json", outside, "app.json", ".evermap/../app.json", ".evermap/./draft.json",
                     ".evermap/evermap.json", M.path_for_id(RULE), ".evermap/views/x.json", ".evermap\\draft.json",
                     ".evermap/missing.json", ".evermap"):
            code, out, err = S.run(self.root, "apply", path)
            self.assertEqual((code, out), (2, b""), (path, err))
        self.assertFalse(os.path.exists(os.path.join(self.root, *M.path_for_id("role:recipient").split("/"))))

    def test_symlinked_draft_and_target_are_refused(self):
        outside = os.path.join(self.base, "outside.json")
        S.put(self.base, "outside.json", json.dumps([ROLE]))
        symlink(outside, os.path.join(self.root, ".evermap", "draft.json"))
        code, _, err = S.run(self.root, "apply")
        self.assertEqual(code, 2)
        self.assertIn("symbolic link", err)
        os.remove(os.path.join(self.root, ".evermap", "draft.json"))
        target = os.path.join(self.base, "roles")
        os.makedirs(target)
        shutil.rmtree(os.path.join(self.root, ".evermap", "model", "roles"))
        symlink(target, os.path.join(self.root, ".evermap", "model", "roles"))
        self.draft([ROLE])
        code, out = self.apply()
        self.assertEqual((code, out["written"], out["rejected"][0]["rule"]), (1, 0, "V2"))
        self.assertEqual(os.listdir(target), [])

    def test_invalid_json_keeps_the_draft(self):
        S.put(self.root, DRAFT, "[{")
        code, out = self.apply()
        self.assertEqual((code, out["draft_deleted"], out["rejected"][0]["rule"]), (1, False, "V1"))
        self.assertTrue(os.path.exists(os.path.join(self.root, ".evermap", "draft.json")))

    def test_leftover_draft_is_a_warning_not_a_change_or_a_view(self):
        self.draft([ROLE])
        _, status = S.run_json(self.root, "status")
        self.assertEqual((status["changed_files"], status["views_out_of_sync"]), (0, []))
        code, out = S.run_json(self.root, "validate", "--strict", "--limit", "0")
        self.assertEqual(code, 0)
        self.assertEqual([(f["rule"], f["severity"]) for f in out["findings"]], [("V1", "warning")])
        before = S.tree(self.root, ".evermap/views")
        S.run(self.root, "render")
        self.assertEqual(S.tree(self.root, ".evermap/views"), before)

    def test_changes_nothing_outside_the_model(self):
        before = self.outside_model()
        self.draft([ROLE, dict(ROLE, id="role:bad", type="robot")])
        self.apply()
        self.assertEqual(self.outside_model(), before)


if __name__ == "__main__":
    unittest.main()
