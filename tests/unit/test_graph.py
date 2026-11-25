"""query (neighbors, impact, lineage, shared-resources, refs) and search."""

import unittest

from tests import support as S

DEPOSIT, COLLECT, NOTIFY = "operation:parcel.deposit", "operation:parcel.collect", "operation:recipient.notify"
RULE, INV, FLOW, PARCEL, LOCKER = ("rule:collection.window", "invariant:one-parcel-per-locker",
                                   "workflow:deposit-parcel", "entity:parcel", "entity:locker")
DECISION = "decision:skip-locked-assignment"


class Query(S.RepoCase):
    def query(self, *args):
        code, out = S.run_json(self.root, "query", "--limit", "0", "--max-chars", "0", *args)
        self.assertEqual(code, 0, out)
        return out

    def test_neighbors(self):
        out = self.query("neighbors", DEPOSIT)
        self.assertEqual(sorted(n["id"] for n in out["neighbors"]), sorted([
            "channel:parcel-outbox", "component:locker-api", DECISION, LOCKER, PARCEL, "entity:parcel-stored", INV,
            "role:courier", FLOW]))
        via = {n["id"]: n["via"] for n in out["neighbors"]}
        self.assertEqual(via["channel:parcel-outbox"], [{"from": DEPOSIT, "type": "publishes", "direction": "out"}])
        self.assertEqual(via[INV], [{"from": DEPOSIT, "type": "enforced_at", "direction": "in"},
                                    {"from": DEPOSIT, "type": "enforces", "direction": "out"}])
        self.assertEqual({n["distance"] for n in out["neighbors"]}, {1})

    def test_neighbors_depth(self):
        near = {n["id"] for n in self.query("neighbors", NOTIFY)["neighbors"]}
        far = {n["id"]: n["distance"] for n in self.query("neighbors", NOTIFY, "--depth", "2")["neighbors"]}
        self.assertEqual({i for i, d in far.items() if d == 1}, near)
        self.assertIn(DEPOSIT, far)
        self.assertEqual(far[DEPOSIT], 2)

    def test_impact_of_a_rule(self):
        out = self.query("impact", RULE)
        self.assertEqual([(d["id"], d["because"]) for d in out["direct"]], [
            (PARCEL, [{"of": RULE, "reason": "lineage"}]), (COLLECT, [{"of": RULE, "reason": "applies"}])])
        self.assertEqual(sorted(d["id"] for d in out["indirect"]), sorted([
            "component:locker-api", "component:locker-db", LOCKER, "entity:parcel-stored", DEPOSIT, NOTIFY]))
        self.assertEqual({d["distance"] for d in out["indirect"]}, {2})
        self.assertEqual([w["id"] for w in out["workflows"]], [FLOW])
        self.assertEqual([i["id"] for i in out["invariants"]], [INV])
        self.assertEqual([d["id"] for d in out["decisions"]], [DECISION])
        self.assertEqual(out["tests"], [{"test": "tests/test_deposit.py:1-2", "elements": [INV]}])
        self.assertEqual(out["statement"], "This lists what the model records. Not found does not mean not affected.")

    def test_impact_depth_one(self):
        out = self.query("impact", RULE, "--depth", "1")
        self.assertEqual(([d["id"] for d in out["direct"]], out["indirect"]), ([PARCEL, COLLECT], []))

    def test_impact_shared_resources_and_channels(self):
        out = self.query("impact", DEPOSIT)
        self.assertEqual([s["resource"] for s in out["shared_resources"]],
                         [LOCKER, LOCKER + "#free", PARCEL, PARCEL + "#status"])
        self.assertEqual(out["channels"], [{"id": "channel:parcel-outbox", "name": "Parcel outbox",
                                            "producers": [DEPOSIT], "consumers": [NOTIFY]}])
        self.assertIn(NOTIFY, [d["id"] for d in out["indirect"]])

    def test_impact_lineage_and_coverage(self):
        out = self.query("impact", "entity:locker#id")
        self.assertEqual(out["lineage_downstream"], [{"from": "entity:locker#id", "to": "entity:parcel#locker_id",
                                                      "distance": 1, "via": DEPOSIT}])
        self.assertEqual(out["coverage_gaps"], {"unmapped": [], "pending": [], "unknown": []})

    def test_impact_of_a_component_includes_its_operations(self):
        out = self.query("impact", "component:notifier")
        self.assertEqual([s["id"] for s in out["seeds"]], ["component:notifier", NOTIFY])

    def test_lineage(self):
        out = self.query("lineage", "entity:parcel#locker_id")
        self.assertEqual(out["upstream"], [{"from": "entity:locker#id", "to": "entity:parcel#locker_id",
                                            "distance": 1, "via": DEPOSIT}])
        self.assertEqual(out["downstream"], [])
        down = self.query("lineage", LOCKER + "#id", "--direction", "down")
        self.assertNotIn("upstream", down)
        self.assertEqual([e["to"] for e in down["downstream"]], ["entity:parcel#locker_id"])

    def test_lineage_across_two_hops(self):
        self.edit("entity:parcel-stored", lambda v: v["fields"].append(
            {"name": "locker", "type": "integer", "origins": [{"type": "copied", "from": ["entity:parcel#locker_id"]}]}))
        out = self.query("lineage", LOCKER + "#id", "--direction", "down")
        self.assertEqual([(e["to"], e["distance"]) for e in out["downstream"]],
                         [("entity:parcel#locker_id", 1), ("entity:parcel-stored#locker", 2)])
        self.assertEqual(len(self.query("lineage", LOCKER + "#id", "--depth", "1")["downstream"]), 1)

    def test_shared_resources(self):
        out = self.query("shared-resources")
        self.assertEqual(out["shared_resources"], [
            {"resource": LOCKER, "writers": [COLLECT, DEPOSIT], "readers": []},
            {"resource": LOCKER + "#free", "writers": [COLLECT, DEPOSIT], "readers": []},
            {"resource": PARCEL, "writers": [COLLECT, DEPOSIT], "readers": [NOTIFY]},
            {"resource": PARCEL + "#status", "writers": [COLLECT, DEPOSIT], "readers": []}])
        self.assertEqual([s["resource"] for s in self.query("shared-resources", LOCKER)["shared_resources"]],
                         [LOCKER, LOCKER + "#free"])

    def test_refs(self):
        self.assertEqual(self.query("refs", RULE)["references"], [
            {"id": PARCEL, "at": "$.fields[4].states.transitions[0].guard", "reference": RULE},
            {"id": COLLECT, "at": "$.relations[0].target", "reference": RULE}])
        self.assertEqual(self.query("refs", PARCEL + "#id")["references"], [
            {"id": "entity:parcel-stored", "at": "$.fields[0].origins[0].from[0]", "reference": PARCEL + "#id"},
            {"id": DEPOSIT, "at": "$.contract.input[0].maps_to", "reference": PARCEL + "#id"},
            {"id": FLOW, "at": "$.data_collected[0]", "reference": PARCEL + "#id"}])

    def test_unknown_ids_are_usage_errors(self):
        self.edit(RULE, lambda v: v.update(aliases=["rule:old-window"]))
        for args, message in ((["neighbors", "rule:gone"], "unknown element 'rule:gone'"),
                              (["impact", "rule:old-window"], "unknown element 'rule:old-window' (now %s)" % RULE),
                              (["refs", PARCEL + "#gone"], "'entity:parcel' has no part 'gone'"),
                              (["lineage"], "query lineage needs at least one element id")):
            code, out, err = S.run(self.root, "query", *args)
            self.assertEqual((code, out), (2, b""), args)
            self.assertIn(message, err)


class Search(S.RepoCase):
    def search(self, *args):
        code, out = S.run_json(self.root, "search", "--limit", "0", *args)
        self.assertEqual(code, 0)
        return out["results"]

    def test_best_match_first(self):
        results = self.search("collection", "window")
        self.assertEqual(results[0]["id"], RULE)
        self.assertEqual(results[0]["matched"], ["collection", "window"])
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_more_matched_terms_rank_higher(self):
        ids = [r["id"] for r in self.search("notify", "recipient")]
        self.assertEqual(ids[0], NOTIFY)

    def test_ties_are_ordered_by_id(self):
        results = self.search("locker")
        top = [r for r in results if r["score"] == results[0]["score"]]
        self.assertEqual([r["id"] for r in top], sorted(r["id"] for r in top))

    def test_prefix_and_camel_case(self):
        self.assertIn(RULE, [r["id"] for r in self.search("expir")])
        self.assertEqual(self.search("collectParcel")[0]["matched"], ["collect", "parcel"])

    def test_decision_text_is_searched(self):
        self.assertEqual([r["id"] for r in self.search("mutex")], [DECISION])

    def test_kind_filter_and_no_match(self):
        self.assertEqual({r["id"].split(":")[0] for r in self.search("parcel", "--kind", "entity")}, {"entity"})
        self.assertEqual(self.search("zebra"), [])


if __name__ == "__main__":
    unittest.main()
