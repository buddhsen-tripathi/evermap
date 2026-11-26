"""render: workflow pages, component neighbourhood and operation diagrams (SPEC 7)."""

import copy
import re
import unittest

from tests import support as S
from evermap_lib import model as M
from evermap_lib import render

API, NOTIFIER, DB = "component:locker-api", "component:notifier", "component:locker-db"
DEPOSIT, COLLECT, NOTIFY = "operation:parcel.deposit", "operation:parcel.collect", "operation:recipient.notify"
FLOW = "workflow:deposit-parcel"
TECH = M.DIR + "/views/technical/"
FLOWS = M.DIR + "/views/workflows/"


class Fake:
    """Just enough of model.Model for render.build."""

    def __init__(self, elements, manifest=None):
        self.elements = {e["id"]: e for e in elements}
        self.manifest = manifest or {"system": {"name": "Parcel lockers"}}
        self.parsed = {}

    def get(self, reference):
        return self.elements.get(M.base_id(reference))

    def of_kind(self, *kinds):
        return [self.elements[i] for i in sorted(self.elements) if M.kind_of(i) in kinds]


def model(change=None):
    elements = S.elements()
    if change:
        change(elements)
    return Fake(list(elements.values()))


def mermaid(page, heading):
    """(node labels, edges as (source label, target label, edge label)) of the diagram under a heading."""
    section = page.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```mermaid\n(.*?)```", section, re.S).group(1)
    names = dict(re.findall(r'^  (n\d+)\["(.*)"\]$', block, re.M))
    edges = [(names[a], names[b], label) for a, label, b in
             re.findall(r'^  (n\d+) -->(?:\|"(.*)"\| )? ?(n\d+)$', block, re.M)]
    return sorted(names.values()), sorted(edges)


def add(elements, ident, relation):
    elements[ident].setdefault("relations", []).append(relation)


def cross_calls(elements):
    """The notifier's operation calls the deposit operation; the notifier depends on the API."""
    add(elements, NOTIFY, {"type": "calls", "target": DEPOSIT, "mechanism": "sync-http"})
    add(elements, NOTIFIER, {"type": "depends_on", "target": API, "mechanism": "http"})


class WorkflowPages(unittest.TestCase):
    maxDiff = None

    def test_one_page_per_workflow_linked_from_index_and_components(self):
        pages = render.build(model())
        page = pages[FLOWS + "deposit-parcel.md"]
        self.assertTrue(page.startswith(render.MARKER))
        for line in ("# Deposit a parcel", "`workflow:deposit-parcel`", "Trigger: A courier arrives with a parcel.",
                     "Actors: `role:courier` Courier", "> How a parcel gets into a locker and the recipient hears about it."):
            self.assertIn(line + "\n", page)
        self.assertIn("[Deposit a parcel](workflows/deposit-parcel.md)", pages[M.DIR + "/views/README.md"])
        for component in ("locker-api", "notifier"):
            self.assertIn("- [Deposit a parcel](../workflows/deposit-parcel.md)", pages[TECH + component + ".md"])
        self.assertNotIn("## Workflows", pages[TECH + "locker-db.md"])  # no step uses its operations

    def test_flowchart_has_steps_branches_failures_and_outcomes(self):
        def branch(elements):
            elements[FLOW]["steps"][1]["branches"] = [
                {"condition_plain": "No locker is free", "outcome": "o-denied"},
                {"condition_plain": "A locker is free", "next": "s3"}]
        nodes, edges = mermaid(render.build(model(branch))[FLOWS + "deposit-parcel.md"], "Flow")
        self.assertEqual(nodes, ["failure: The drop-off is refused.", "s1: Check the person is a courier.",
                                 "s2: Put the parcel in a free locker.", "s3: Tell the recipient.",
                                 "success: Parcel stored and recipient told."])
        s1, s2, s3 = nodes[1:4]
        self.assertEqual(edges, sorted([
            (s1, s2, ""), (s1, nodes[0], "fails: Refused."), (s2, s3, ""), (s2, nodes[0], "No locker is free"),
            (s2, s3, "A locker is free"), (s3, nodes[4], "")]))
        self.assertIn("flowchart TD", render.build(model())[FLOWS + "deposit-parcel.md"])

    def test_steps_table_links_operations_rules_and_evidence(self):
        def rule_step(elements):
            elements[FLOW]["steps"][2]["rules"] = ["rule:collection.window"]
            elements[FLOW]["steps"][2]["evidence"] = S.ev("worker/notify.py", [5, 5])
        page = render.build(model(rule_step))[FLOWS + "deposit-parcel.md"]
        rows = [line for line in page.splitlines() if line.startswith("| `s")]
        self.assertEqual(len(rows), 3)
        self.assertIn("`operation:parcel.deposit` Deposit parcel in [Locker API](../technical/locker-api.md)", rows[0])
        self.assertIn("[api/deposit.py:4-12](../../../api/deposit.py#L4-L12)", rows[0])  # the operation's evidence
        self.assertIn("`rule:collection.window` Collection window", rows[2])
        self.assertIn("[worker/notify.py:5-5](../../../worker/notify.py#L5-L5)", rows[2])  # the step's own
        self.assertNotIn("api/deposit.py", rows[2])

    def test_labels_are_escaped(self):
        def hostile(elements):
            elements[FLOW]["steps"][0]["plain"] = 'Check "x" <b>#1</b> `y`'
            elements[FLOW]["steps"][0]["on_failure"][0]["plain"] = "a | b ]"
        page = render.build(model(hostile))[FLOWS + "deposit-parcel.md"]
        self.assertIn('n0["s1: Check &quot;x&quot; &lt;b&gt;&num;1&lt;/b&gt; \'y\'"]', page)
        self.assertIn('-->|"fails: a &#124; b ]"|', page)  # a pipe would end the edge label
        self.assertNotIn("<b>", page)
        row = next(line for line in page.splitlines() if line.startswith("| `s1`"))
        self.assertIn("Check \"x\" &lt;b&gt;\\#1&lt;/b&gt; \\`y\\`", row)

    def test_large_workflow_is_capped_and_says_so(self):
        def large(elements):
            steps = [{"id": "s%d" % i, "plain": "Step %d." % i, "operation": DEPOSIT,
                      "next": ["s%d" % (i + 1)]} for i in range(1, 40)]
            steps.append({"id": "s40", "plain": "Last.", "operation": DEPOSIT, "outcome": "o-stored"})
            elements[FLOW]["steps"] = steps
        page = render.build(model(large))[FLOWS + "deposit-parcel.md"]
        nodes, _ = mermaid(page, "Flow")
        self.assertEqual(len(nodes), render.MAX_NODES)
        self.assertIn("The diagram shows 30 of 42 steps and outcomes; 12 left out.", page)
        self.assertEqual(len([line for line in page.splitlines() if line.startswith("| `s")]), 40)


class ComponentDiagrams(unittest.TestCase):
    maxDiff = None

    def test_neighbourhood_shows_dependencies_both_ways_and_call_edges(self):
        pages = render.build(model(cross_calls))
        self.assertEqual(mermaid(pages[TECH + "locker-api.md"], "Neighbourhood"),
                         (["Locker API", "Locker database", "Notifier"],
                          [("Locker API", "Locker database", "sql"), ("Notifier", "Locker API", "http")]))
        self.assertEqual(mermaid(pages[TECH + "notifier.md"], "Neighbourhood"),
                         (["Locker API", "Notifier"], [("Notifier", "Locker API", "http")]))
        # A call alone, without a declared dependency, still links the components.
        only_call = render.build(model(lambda e: add(e, NOTIFY, {"type": "calls", "target": DEPOSIT})))
        self.assertEqual(mermaid(only_call[TECH + "notifier.md"], "Neighbourhood")[1],
                         [("Notifier", "Locker API", "")])

    def test_isolated_component_says_so(self):
        page = render.build(model())[TECH + "notifier.md"]
        self.assertIn("No dependency or operation call links this component to another one in the model.", page)
        self.assertEqual(mermaid(page, "Neighbourhood"), (["Notifier"], []))

    def test_operation_diagram_labels_targets_with_their_component(self):
        def calls(elements):
            cross_calls(elements)
            add(elements, DEPOSIT, {"type": "calls", "target": COLLECT, "mechanism": "sync-call"})
        pages = render.build(model(calls))
        self.assertEqual(mermaid(pages[TECH + "notifier.md"], "Operation calls"),
                         (["Deposit parcel (Locker API)", "Notify recipient"],
                          [("Notify recipient", "Deposit parcel (Locker API)", "sync-http")]))
        self.assertEqual(mermaid(pages[TECH + "locker-api.md"], "Operation calls"),
                         (["Collect parcel", "Deposit parcel"], [("Deposit parcel", "Collect parcel", "sync-call")]))
        self.assertNotIn("No operation here calls", pages[TECH + "locker-api.md"])
        self.assertNotIn("## Operation calls", pages[TECH + "locker-db.md"])  # it has no operations

    def test_neighbourhood_is_capped_and_says_so(self):
        def many(elements):
            for i in range(35):
                ident = "component:client-%02d" % i
                elements[ident] = S.element(ident, "Client %02d" % i, "A client.", plain="A client.",
                                            type="frontend", relations=[{"type": "depends_on", "target": API}])
        page = render.build(model(many))[TECH + "locker-api.md"]
        nodes, edges = mermaid(page, "Neighbourhood")
        self.assertEqual(len(nodes), render.MAX_NODES)
        self.assertIn("Locker API", nodes)
        self.assertIn("The diagram shows 30 of 37 components; 7 left out.", page)
        self.assertTrue(all(a in nodes and b in nodes for a, b, _ in edges))

    def test_operation_diagram_is_capped_and_says_so(self):
        def many(elements):
            for i in range(32):
                ident = "operation:parcel.step-%02d" % i
                elements[ident] = S.element(ident, "Step %02d" % i, "A step.", plain="A step.", parent=NOTIFIER,
                                            trigger={"type": "call"}, exposure="internal",
                                            relations=[{"type": "calls", "target": DEPOSIT}])
        page = render.build(model(many))[TECH + "notifier.md"]
        nodes, _ = mermaid(page, "Operation calls")
        self.assertEqual(len(nodes), render.MAX_NODES)
        self.assertIn("The diagram shows 30 of 34 operations and call targets; 4 left out.", page)

    def test_render_is_deterministic(self):
        first = render.build(model(cross_calls))
        second = render.build(model(cross_calls))
        self.assertEqual(first, second)
        shuffled = model(cross_calls)
        shuffled.elements = dict(reversed(list(copy.deepcopy(shuffled.elements).items())))
        self.assertEqual(render.build(shuffled), first)


class Rendered(S.RepoCase):
    def test_workflow_pages_are_written_checked_and_removed(self):
        self.assertTrue(S.read(self.root, FLOWS + "deposit-parcel.md").startswith(render.MARKER))
        self.assertEqual(self.findings("--strict"), (0, []))
        S.put(self.root, FLOWS + "deposit-parcel.md", S.read(self.root, FLOWS + "deposit-parcel.md") + "edit\n")
        self.assertEqual([f["path"] for f in self.findings()[1]], [FLOWS + "deposit-parcel.md"])  # V15
        S.os.remove(S.os.path.join(self.root, *M.path_for_id(FLOW).split("/")))
        code, out = S.run_json(self.root, "render")
        self.assertEqual((code, out["removed"]), (0, [FLOWS + "deposit-parcel.md"]))
        self.assertNotIn("## Workflows", S.read(self.root, M.DIR + "/views/README.md"))


if __name__ == "__main__":
    unittest.main()
