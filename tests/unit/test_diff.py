"""diff: added, removed, modified, renamed, re-anchored, relations, functional and risk changes (SPEC 5.7)."""

import json
import os
import re
import unittest

from tests import support as S
from evermap_lib import diff as D
from evermap_lib import model as M
from evermap_lib import render

DEPOSIT, COLLECT, NOTIFY = "operation:parcel.deposit", "operation:parcel.collect", "operation:recipient.notify"
RULE, INV, FLOW, PARCEL = "rule:collection.window", "invariant:one-parcel-per-locker", "workflow:deposit-parcel", "entity:parcel"
ROLE = S.element("role:recipient", "Recipient", "Person collecting a parcel.", plain="Someone picking up a parcel.",
                 type="user-role", provenance="inferred", rationale="Named in the collect handler.")


class Diff(S.RepoCase):
    def committed(self, *args):
        """Commit the working tree and diff it against the previous commit."""
        S.run(self.root, "render")
        S.commit(self.root, "change")
        code, out = S.run_json(self.root, "diff", "HEAD~1", "HEAD", "--limit", "0", "--max-chars", "0", *args)
        self.assertEqual(code, 0)
        return out

    def remove(self, ident):
        os.remove(os.path.join(self.root, *M.path_for_id(ident).split("/")))

    def test_no_change(self):
        out = self.committed()
        self.assertEqual(out["summary"], {"added": 0, "removed": 0, "modified": 0, "renamed": 0, "reanchored": 0,
                                          "unchanged": len(S.ELEMENTS)})

    def test_added_and_removed(self):
        S.write_element(self.root, ROLE)
        self.remove(NOTIFY)
        out = self.committed()
        self.assertEqual(out["added"], [{"id": "role:recipient", "name": "Recipient", "provenance": "inferred"}])
        self.assertEqual(out["removed"], [{"id": NOTIFY, "name": "Notify recipient"}])
        self.assertEqual(out["relations_removed"], [
            {"source": NOTIFY, "type": "consumes", "target": "channel:parcel-outbox"},
            {"source": NOTIFY, "type": "reads", "target": PARCEL}])
        self.assertEqual(out["modified"], [])

    def test_modified_fields_at_json_path_granularity(self):
        self.edit(DEPOSIT, lambda v: v.update(summary="Stores the parcel in a free locker."))
        self.edit(PARCEL, lambda v: v["fields"].append({"name": "size", "type": "text"}))
        out = self.committed()
        self.assertEqual([(m["id"], m["renamed"], m["changes"]) for m in out["modified"]], [
            (PARCEL, False, [{"path": "$.fields[name=size]", "to": {"name": "size", "type": "text"}}]),
            (DEPOSIT, False, [{"path": "$.summary", "from": "Assigns a free locker and stores the parcel.",
                               "to": "Stores the parcel in a free locker."}])])

    def test_renamed_code_keeps_the_id(self):
        S.git(self.root, "mv", "worker/notify.py", "worker/notifier.py")
        S.run(self.root, "check-evidence", "--fix-moved")
        self.edit("component:notifier", lambda v: v.update(name="Notification worker"))
        out = self.committed()
        self.assertEqual(out["summary"]["renamed"], 2)
        self.assertEqual(out["added"] + out["removed"], [])
        self.assertEqual({m["id"]: (m["renamed"], m["behaviour_changed"]) for m in out["modified"]},
                         {"component:notifier": (True, False), NOTIFY: (True, False)})

    def test_rename_with_behaviour_change(self):
        S.git(self.root, "mv", "worker/notify.py", "worker/notifier.py")
        S.run(self.root, "check-evidence", "--fix-moved")
        self.edit(NOTIFY, lambda v: v.update(exposure="public"))
        modified = {m["id"]: m for m in self.committed()["modified"]}
        self.assertEqual((modified[NOTIFY]["renamed"], modified[NOTIFY]["behaviour_changed"]), (True, True))

    def test_reanchored_only(self):
        lines = S.read(self.root, "api/collect.py").split("\n")
        S.put(self.root, "api/collect.py", "\n".join(lines[:1] + ["# Pickup rules."] + lines[1:]))
        S.run(self.root, "check-evidence", "--fix-moved")
        out = self.committed()
        self.assertEqual((out["reanchored"], out["modified"]), ([COLLECT, RULE], []))
        self.assertEqual(out["summary"]["reanchored"], 2)

    def test_alias_based_id_rename(self):
        rule = S.elements()[RULE]
        self.remove(RULE)
        S.write_element(self.root, dict(rule, id="rule:pickup.window", aliases=[RULE]))
        self.edit(COLLECT, lambda v: [r.update(target="rule:pickup.window") for r in v["relations"] if r["type"] == "applies"])
        self.edit(PARCEL, lambda v: v["fields"][4]["states"]["transitions"][0].update(guard="rule:pickup.window"))
        out = self.committed()
        self.assertEqual(out["added"] + out["removed"], [])
        entry = next(m for m in out["modified"] if m["id"] == "rule:pickup.window")
        self.assertEqual((entry["previous_id"], entry["renamed"], entry["behaviour_changed"]), (RULE, True, False))
        self.assertEqual([c["path"] for c in entry["changes"]], ["$.aliases", "$.id"])
        self.assertEqual(out["relations_added"], [{"source": COLLECT, "type": "applies", "target": "rule:pickup.window"}])
        self.assertEqual(out["relations_removed"], [{"source": COLLECT, "type": "applies", "target": RULE}])

    def test_relation_changes(self):
        self.edit(COLLECT, lambda v: v["relations"].append({"type": "reads", "target": "entity:locker"}))
        self.edit(NOTIFY, lambda v: v.update(relations=[r for r in v["relations"] if r["type"] != "reads"]))
        out = self.committed()
        self.assertEqual(out["relations_added"], [{"source": COLLECT, "type": "reads", "target": "entity:locker"}])
        self.assertEqual(out["relations_removed"], [{"source": NOTIFY, "type": "reads", "target": PARCEL}])

    def test_functional_changes(self):
        self.edit(FLOW, lambda v: v["steps"][1].update(plain="Put the parcel in the nearest free locker."))
        self.edit(RULE, lambda v: v.update(statement="days since storage must not exceed 9"))
        self.edit(COLLECT, lambda v: v.update(plain="A recipient types a code and takes the parcel."))
        out = self.committed()
        self.assertEqual([(c["id"], c["path"]) for c in out["functional_changes"]],
                         [(COLLECT, "$.plain"), (RULE, "$.statement"), (FLOW, "$.steps[id=s2].plain")])
        self.assertEqual(out["risk_changes"], [])

    def test_risk_changes(self):
        self.edit(DEPOSIT, lambda v: [v["auth"].update(authentication="optional"),
                                      v["transactions"][0].update(isolation="serializable")])
        self.edit(INV, lambda v: v["enforcement"][0].update(mechanism="application-check"))
        S.write_element(self.root, S.element(
            "invariant:code-matches-parcel", "Code matches", "Only the right code opens a locker.",
            plain="Only the right code opens the locker.", statement="pickup_code equals the entered code",
            category="authorization", provenance="inferred", rationale="Checked in the handler."))
        out = self.committed()
        self.assertEqual([(c["id"], c["area"], c["path"]) for c in out["risk_changes"]], [
            ("invariant:one-parcel-per-locker", "enforcement",
             "$.enforcement[mechanism=application-check,where=operation:parcel.deposit]"),
            ("invariant:one-parcel-per-locker", "enforcement", "$.enforcement[mechanism=lock,where=operation:parcel.deposit]"),
            (DEPOSIT, "auth", "$.auth.authentication"),
            (DEPOSIT, "transactions", "$.transactions[id=tx1].isolation"),
            ("invariant:code-matches-parcel", "invariant", "$")])

    def test_one_ref_compares_with_the_working_tree(self):
        self.edit(RULE, lambda v: v.update(summary="Changed."))
        _, out = S.run_json(self.root, "diff", "HEAD")
        self.assertEqual((out["from"], out["to"], [m["id"] for m in out["modified"]]), ("HEAD", "working tree", [RULE]))

    def test_overlay(self):
        base = ".evermap/proposals/longer-window/"
        S.put(self.root, base + "proposal.json", M.format_value({"title": "Longer window", "status": "draft"}, "proposal"))
        shadow = S.elements()[RULE]
        shadow.update(lifecycle="proposed", statement="days since storage must not exceed 14")
        S.put(self.root, base + "model/rules/collection.window.json", M.format_value(shadow, "rule"))
        S.put(self.root, base + "model/roles/recipient.json", M.format_value(dict(ROLE, lifecycle="proposed"), "role"))
        code, out = S.run_json(self.root, "diff", "--overlay", "longer-window")
        self.assertEqual(code, 0)
        self.assertEqual((out["from"], out["to"]), ("working tree", "proposal longer-window"))
        self.assertEqual([a["id"] for a in out["added"]], ["role:recipient"])
        self.assertEqual([(m["id"], [c["path"] for c in m["changes"]]) for m in out["modified"]],
                         [(RULE, ["$.lifecycle", "$.statement"])])

    def test_markdown_moved_evidence_is_one_line_without_hashes(self):
        S.git(self.root, "mv", "worker/notify.py", "worker/notifier.py")
        S.run(self.root, "check-evidence", "--fix-moved")
        S.run(self.root, "render")
        S.commit(self.root, "rename")
        text = S.run(self.root, "diff", "HEAD~1", "HEAD", "--format", "md")[1].decode()
        self.assertNotIn("sha256:", text)
        self.assertEqual(text.count("evidence moved: worker/notify.py -> worker/notifier.py, "), 2)
        self.assertIn("  - evidence moved: worker/notify.py -> worker/notifier.py, 1 item\n", text)
        self.assertNotIn("$.evidence[", text)
        _, out = S.run_json(self.root, "diff", "HEAD~1", "HEAD")  # JSON keeps the detail, hashes included
        changes = [c for m in out["modified"] for c in m["changes"]]
        self.assertTrue(any(c["path"].startswith("$.evidence[") for c in changes))
        self.assertIn("sha256:", json.dumps(changes))

    def test_markdown_changed_evidence_is_listed_without_hashes(self):
        self.edit(DEPOSIT, lambda v: [v["evidence"][0].update(path="api/routes.py", lines=[6, 8], symbol="register"),
                                      v["evidence"][0].pop("hash")], anchor=True)
        S.run(self.root, "render")
        S.commit(self.root, "new evidence")
        text = S.run(self.root, "diff", "HEAD~1", "HEAD", "--format", "md")[1].decode()
        self.assertNotIn("evidence moved", text)  # a different span, not a move
        self.assertIn("$.evidence[", text)
        self.assertNotIn("sha256:", text)

    def test_markdown(self):
        self.edit(DEPOSIT, lambda v: v.update(summary="Stores the parcel."))
        S.write_element(self.root, ROLE)
        S.run(self.root, "render")
        S.commit(self.root, "change")
        code, out, _ = S.run(self.root, "diff", "HEAD~1", "HEAD", "--format", "md")
        text = out.decode()
        self.assertEqual(code, 0)
        for heading in ("## Model changes", "### Added", "### Modified", "```mermaid", "classDef added"):
            self.assertIn(heading, text)
        self.assertIn("1 added, 0 removed, 1 modified", text)


def model_of(elements):
    return M.Model({M.path_for_id(e["id"]): json.dumps(e).encode() for e in elements})


class Mermaid(unittest.TestCase):
    def test_capped_at_thirty_nodes(self):
        old = model_of([])
        roles = [S.element("role:r%02d" % n, "Role %d" % n, "Generated.", type="user-role",
                           relations=[{"type": "uses_role", "target": "role:r%02d" % ((n + 1) % 40)}]) for n in range(40)]
        new = model_of(roles)
        result = D.diff(old, new)
        self.assertEqual(result["summary"]["added"], 40)
        lines = D.mermaid(old, new, result)
        nodes = [line for line in lines if re.match(r'^  n\d+\["', line)]
        self.assertEqual(len(nodes), render.MAX_NODES)
        names = {re.match(r"^  (n\d+)", line).group(1) for line in nodes}
        for line in lines:
            edge = re.match(r"^  (n\d+) --", line)
            if edge:
                self.assertIn(edge.group(1), names)
                self.assertIn(line.split()[-1], names)

    def test_no_changes_no_diagram(self):
        model = model_of([ROLE])
        self.assertEqual(D.mermaid(model, model, D.diff(model, model)), [])

    def test_styles_mark_each_kind_of_change(self):
        a = S.element("role:a", "A", "A.", type="user-role")
        b = S.element("role:b", "B", "B.", type="user-role")
        old, new = model_of([a, b]), model_of([dict(a, summary="Changed."), ROLE])
        lines = D.mermaid(old, new, D.diff(old, new))
        self.assertIn("  class n2 added", lines)
        self.assertIn("  class n1 removed", lines)
        self.assertIn("  class n0 modified", lines)


if __name__ == "__main__":
    unittest.main()
