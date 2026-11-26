"""The diagram kinds the user chooses: sequence, er, state and access (manifest views.kinds)."""

import json
import os
import re
import unittest

from tests import support as S
from tests.unit.test_diagrams import Excalidraw
from tests.unit.test_render import API, DEPOSIT, FLOW, NOTIFY, Fake
from evermap_lib import cli, eraser, excalidraw, kinds
from evermap_lib import model as M
from evermap_lib import render

VIEWS = M.DIR + "/views/"
DIAGRAMS = VIEWS + "diagrams/"
ALL = ["sequence", "er", "state", "access"]
REFERENCES = os.path.join(S.SKILL, "references")


def fake(change=None, chosen=ALL, diagrams=None):
    """The fixture model extended for the kinds (support.kinds_elements), as a Fake."""
    elements = S.elements()
    elements.update({e["id"]: e for e in S.kinds_elements()})
    if change:
        change(elements)
    views = {"kinds": list(chosen)} if chosen is not None else {}
    if diagrams:
        views["diagrams"] = diagrams
    return Fake(list(elements.values()), {"system": {"name": "Parcel lockers"}, "views": views})


def section(page, heading):
    return page.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]


def block(page, heading):
    return re.search(r"```mermaid\n(.*?)```", section(page, heading), re.S).group(1).splitlines()


def flow_page(pages, slug="deposit-parcel"):
    return pages[VIEWS + "workflows/%s.md" % slug]


class Sequence(unittest.TestCase):
    maxDiff = None

    def test_deposit_sequence(self):
        page = flow_page(render.build(fake()))
        self.assertLess(page.index("## Flow"), page.index("## Sequence"))
        self.assertLess(page.index("## Sequence"), page.index("## Steps"))
        lines = block(page, "Sequence")
        self.assertEqual(lines[:6], ["sequenceDiagram", "  actor p0 as Courier", "  participant p1 as Locker API",
                                     "  participant p2 as Locker database", "  participant p3 as Notifier",
                                     "  participant p4 as Parcel outbox"])
        self.assertEqual(lines[6:12], ["  p0->>p1: Deposit parcel (POST /parcels)",
                                       "  p1->>p2: writes Parcel", "  p1->>p2: writes Locker",
                                       "  p1-->>p4: publishes Parcel stored event", "  p1-->>p3: Notify recipient",
                                       "  Note over p1: fails: Refused."])
        self.assertEqual(lines[-2:], ["  p1-->>p3: Notify recipient (Parcel outbox)", "  p3->>p2: reads Parcel"])
        self.assertEqual(len([line for line in lines if "->>" in line]), 12)

    def test_trigger_sources(self):
        def change(elements):
            elements[NOTIFY]["trigger"] = {"type": "schedule", "detail": "*/5 * * * *"}
            elements[DEPOSIT]["relations"][-1]["mechanism"] = "sync-call"
            elements[FLOW]["actors"] = []
        lines = block(flow_page(render.build(fake(change))), "Sequence")
        self.assertIn("  p0->>p0: Deposit parcel (POST /parcels)", lines)   # http, no actor, no previous step
        self.assertIn("  p0->>p2: Notify recipient", lines)                 # sync call: solid
        self.assertIn("  p2->>p2: Notify recipient (*/5 * * * *)", lines)    # schedule: self-message

        def previous(elements):
            change(elements)
            elements[NOTIFY]["trigger"] = {"type": "call"}
        self.assertIn("  p0->>p2: Notify recipient", block(flow_page(render.build(fake(previous))), "Sequence")[-2:])

    def test_rule_only_step_is_a_note_and_an_eraser_self_message(self):
        pages = render.build(fake(diagrams=["eraser"]))
        lines = block(flow_page(pages, "collect-parcel"), "Sequence")
        self.assertEqual(lines[-1], "  Note over p1: Collection window: Within seven days / After seven<br/>days")
        self.assertNotIn("alt", " ".join(lines).split())
        code = pages[DIAGRAMS + "workflows/collect-parcel-2.eraserdiagram"]
        self.assertTrue(code.endswith("\nLocker API > Locker API: Collection window\n"), code)
        self.assertNotIn("Within seven days", code)

    def test_cap_and_left_out_note(self):
        def many(elements):
            elements[FLOW]["steps"] = [{"id": "s%d" % i, "plain": "Deposit.", "operation": DEPOSIT}
                                       for i in range(30)]
            elements[FLOW]["outcomes"] = []
        page = flow_page(render.build(fake(many)))
        self.assertEqual(len([line for line in block(page, "Sequence") if "->>" in line]), kinds.MAX_MESSAGES)
        self.assertIn("The diagram shows 40 of 150 messages; 110 left out.", section(page, "Sequence"))

    def test_skipped_when_empty_and_when_not_chosen(self):
        def bare(elements):
            elements[FLOW]["steps"] = [{"id": "s1", "plain": "Wait.", "evidence": S.ev("api/deposit.py", [4, 4])}]
        self.assertNotIn("## Sequence", flow_page(render.build(fake(bare))))
        self.assertNotIn("## Sequence", flow_page(render.build(fake(chosen=["er"]))))

    def test_labels_are_sanitised(self):
        def odd(elements):
            elements[DEPOSIT]["name"] = 'Deposit; #1 "fast" <now> & {x} | `y`'
        line = block(flow_page(render.build(fake(odd))), "Sequence")[6]
        self.assertEqual(line, "  p0->>p1: Deposit#59; #35;1 #quot;fast#quot; #lt;now#gt; #amp; (x) #124; 'y' "
                               "(POST /parcels)")
        self.assertNotRegex(re.sub(r"#\w+;", "", line.split(": ", 1)[1]), r"[;#<>\"{}|`]")


class EntityRelationship(unittest.TestCase):
    maxDiff = None

    def page(self, change=None, chosen=ALL):
        return render.build(fake(change, chosen)).get(kinds.DATA_MODEL)

    def test_data_model_page(self):
        pages = render.build(fake())
        page = pages[kinds.DATA_MODEL]
        self.assertTrue(page.startswith(render.MARKER))
        self.assertEqual(re.findall(r"^## (.+)$", page, re.M), ["Locker database", "No datastore recorded", "States"])
        lines = block(page, "Locker database")
        self.assertEqual(lines[0], "erDiagram")
        for line in ("  Locker {", "    integer id PK", "    integer locker_id FK", '    text recipient "pii"',
                     "    text pickup_code", '  Parcel }o--o| Locker : "locker_id"'):
            self.assertIn(line, lines)
        self.assertIn("[Data model](data-model.md)", pages[VIEWS + "README.md"])
        self.assertNotIn("data-model", "".join(render.build(fake(chosen=["sequence", "access"])).values()))

    def test_one_to_one_and_mandatory(self):
        def unique(elements):
            parcel = elements["entity:parcel"]
            parcel["fields"][1]["nullable"] = False
            parcel["constraints"].append({"type": "unique", "fields": ["locker_id"]})
        lines = block(self.page(unique), "Locker database")
        self.assertIn('  Parcel |o--|| Locker : "locker_id"', lines)
        self.assertIn("    integer locker_id FK, UK", lines)

    def test_reference_to_another_diagram_is_text(self):
        def cross(elements):
            elements["entity:outbox-row"]["fields"].append({"name": "parcel_id", "type": "text"})
            elements["entity:outbox-row"]["constraints"].append(
                {"type": "foreign-key", "fields": ["parcel_id"], "references": "entity:parcel"})
        page = self.page(cross)
        text = section(page, "No datastore recorded")
        self.assertIn("- Outbox row (`parcel_id`) references Parcel in Locker database.", text)
        self.assertFalse([line for line in block(page, "No datastore recorded") if "--" in line])

    def test_tokens_and_entities_without_fields(self):
        def odd(elements):
            elements["entity:locker"]["fields"] = [{"name": "door code (hashed)", "type": "varchar(20)"},
                                                   {"name": "2nd", "type": "integer (minor units)"}, {"name": "x"}]
            elements["entity:parcel"]["fields"] = []
        lines = block(self.page(odd), "Locker database")
        self.assertIn("    varchar_20 door_code_hashed", lines)
        self.assertIn("    integer_minor_units _2nd", lines)
        self.assertIn("    unknown x", lines)
        self.assertIn("  Parcel", lines)

    def test_skipped_when_no_entity_has_fields(self):
        def bare(elements):
            for ident in ("entity:locker", "entity:parcel", "entity:outbox-row"):
                elements[ident].pop("fields", None)
                elements[ident].pop("constraints", None)
        self.assertIsNone(self.page(bare, ["er"]))
        self.assertEqual(kinds.thin(fake(bare), ["er", "state"]),
                         ["er: 0 of 3 persisted entities have fields", "state: no field records states"])
        self.assertEqual(kinds.thin(fake(), ALL), [])


class States(unittest.TestCase):
    def test_state_diagram(self):
        page = render.build(fake(chosen=["state"]))[kinds.DATA_MODEL]
        self.assertEqual(re.findall(r"^## (.+)$", page, re.M), ["States"])
        self.assertIn("### Parcel `status`", page)
        lines = block(page, "States")
        self.assertEqual(lines, ["stateDiagram-v2", '  state "stored" as s0', '  state "collected" as s1',
                                 "  s0 --> s1 : Collect parcel [Collection window]"])

    def test_no_invented_states_and_plain_labels(self):
        def more(elements):
            states = elements["entity:parcel"]["fields"][4]["states"]
            states["values"].append("expired")
            states["transitions"].append({"from": "stored", "to": "expired", "guard": "rule:collection.window"})
            states["transitions"].append({"from": "expired", "to": "stored"})
        lines = block(render.build(fake(more, ["state"]))[kinds.DATA_MODEL], "States")
        self.assertNotIn("[*]", "\n".join(lines))
        self.assertIn("  s0 --> s2 : [Collection window]", lines)
        self.assertIn("  s2 --> s0", lines)


class Access(unittest.TestCase):
    maxDiff = None

    def test_component_data_access(self):
        pages = render.build(fake(chosen=["access"]))
        page = pages[VIEWS + "technical/locker-api.md"]
        self.assertLess(page.index("## Operation calls"), page.index("## Data access"))
        self.assertLess(page.index("## Data access"), page.index("## Workflows"))
        from tests.unit.test_render import mermaid
        nodes, edges = mermaid(page, "Data access")
        self.assertEqual(nodes, ["Collect parcel", "Deposit parcel", "Locker", "Parcel", "Parcel outbox"])
        self.assertIn(("Deposit parcel", "Parcel", "writes: id, status"), edges)
        self.assertIn(("Deposit parcel", "Parcel outbox", "publishes"), edges)
        _, notifier = mermaid(pages[VIEWS + "technical/notifier.md"], "Data access")
        self.assertIn(("Parcel outbox", "Notify recipient", "consumes"), notifier)
        self.assertNotIn("## Data access", pages[VIEWS + "technical/locker-db.md"])

    def test_at_most_three_fields(self):
        def wide(elements):
            elements[DEPOSIT]["relations"][0]["fields"] = ["id", "locker_id", "recipient", "status"]
        from tests.unit.test_render import mermaid
        _, edges = mermaid(render.build(fake(wide, ["access"]))[VIEWS + "technical/locker-api.md"], "Data access")
        self.assertIn(("Deposit parcel", "Parcel", "writes: id, locker_id, recipient, ..."), edges)

    def test_events_on_the_index(self):
        from tests.unit.test_render import mermaid
        index = render.build(fake(chosen=["access"]))[VIEWS + "README.md"]
        self.assertLess(index.index("## Context"), index.index("## Events"))
        self.assertEqual(mermaid(index, "Events")[1], [("Locker API", "Parcel outbox", "publishes"),
                                                       ("Parcel outbox", "Notifier", "consumes")])

        def quiet(elements):
            del elements["channel:parcel-outbox"]
            for ident in (DEPOSIT, NOTIFY):
                elements[ident]["relations"] = [r for r in elements[ident]["relations"]
                                                if r["type"] not in ("publishes", "consumes")]
            elements[NOTIFY].pop("trigger")
        self.assertNotIn("## Events", render.build(fake(quiet, ["access"]))[VIEWS + "README.md"])


class Formats(unittest.TestCase):
    """Excalidraw and Eraser files of the new kinds."""

    maxDiff = None

    def files(self, change=None):
        return render.build(fake(change, diagrams=["excalidraw", "eraser"]))

    def test_open_in_links_number_every_diagram(self):
        pages = self.files()
        for path, page in pages.items():
            if path.endswith(".md"):
                links = re.findall(r"^Open in: \[Excalidraw\]\(([^)]+)\)", page, re.M)
                self.assertEqual(len(links), page.count("```mermaid"), path)
        for name in ("data-model-1", "data-model-3", "workflows/deposit-parcel-2", "technical/locker-api-3",
                     "README-2"):
            self.assertIn(DIAGRAMS + name + ".excalidraw", pages)

    def test_first_lines_name_the_eraser_type(self):
        pages = self.files()
        first = {name: pages[DIAGRAMS + name + ".eraserdiagram"].split("\n", 1)[0]
                 for name in ("data-model-1", "data-model-3", "workflows/deposit-parcel-2", "technical/locker-api-3",
                              "README-2", "README-1")}
        self.assertEqual(first, {"data-model-1": "entity-relationship-diagram", "data-model-3": "flow-chart",
                                 "workflows/deposit-parcel-2": "sequence-diagram", "technical/locker-api-3": "flow-chart",
                                 "README-2": "flow-chart", "README-1": "flow-chart"})

    def test_eraser_sequence_syntax(self):
        value = render.sequence("Workflow: T - sequence", [
            ("r", "Courier", True), ("a", "Locker API: v2", False), ("t", "Title deeds", False)],
            [("r", "a", "Deposit: now", False), ("a", "t", "writes", True)], [(0, "a", "note", "")])
        self.assertEqual(eraser.dumps(value), 'sequence-diagram\ntitle Workflow: T - sequence\n'
                                              'Courier > "Locker API: v2": Deposit now\n'
                                              '"Locker API: v2" --> "Title deeds": writes\n')

    def test_eraser_er_syntax(self):
        value = render.er("Data model: T", [
            ("p", "Parcel", [("id", "text", ("PK",), ""), ("locker_id", "integer", ("FK", "UK"), ""),
                             ("title", "character varying", (), "pii")]),
            ("l", "Locker room", [("id", "integer", ("PK",), "")]),
            ("n", "Notation", [])],
            [("p", "l", "zero-or-one", "one", ("locker_id",), None), ("p", "l", "many", "one", ("id",), "id")])
        self.assertEqual(eraser.dumps(value).splitlines(), [
            "entity-relationship-diagram", "title Data model: T", "notation crows-foot",
            "Parcel {", "  id text pk", "  locker_id integer fk unique", '  "title" "character varying"', "}",
            '"Locker room" {', "  id integer pk", "}", '"Notation" {', "}",
            'Parcel.locker_id - "Locker room".id', 'Parcel.id > "Locker room".id'])

    def test_excalidraw_sequence(self):
        doc = json.loads(self.files()[DIAGRAMS + "workflows/deposit-parcel-2.excalidraw"])
        items = doc["elements"]
        byid = {e["id"]: e for e in items}
        self.assertEqual(len(byid), len(items))
        rects = [e for e in items if e["type"] == "rectangle"]
        arrows = [e for e in items if e["type"] == "arrow"]
        lines = [e for e in items if e["type"] == "line"]
        self.assertEqual((len(arrows), len(lines)), (12, 5))
        for line in lines:
            self.assertEqual((line["startBinding"], line["endBinding"], line["strokeStyle"]), (None, None, "dashed"))
            self.assertEqual(line["points"][0], [0, 0])
            self.assertEqual(line["points"][1][0], 0)
        for line in lines:   # each lifeline moves with its participant box and text
            group = line["groupIds"]
            self.assertEqual(len(group), 1)
            self.assertEqual(sorted(e["type"] for e in items if e["groupIds"] == group), ["line", "rectangle", "text"])
        for arrow in arrows:
            # Unbound, so moving a box does not pull its messages up to it.
            self.assertEqual((arrow["startBinding"], arrow["endBinding"], arrow["boundElements"]), (None, None, None))
            self.assertEqual(len({p[1] for p in arrow["points"]}), 1 if len(arrow["points"]) == 2 else 2)
            label = byid.get(arrow["id"] + "-t")
            if label:   # free text above the arrow, or beside a loop, never covering it
                self.assertIsNone(label["containerId"])
                self.assertLessEqual(label["y"] + label["height"], arrow["y"]) if len(arrow["points"]) == 2 else \
                    self.assertGreater(label["x"], arrow["x"] + arrow["width"])
        self.assertEqual(sum(a["strokeStyle"] == "dashed" for a in arrows), 5)
        notes = [r for r in rects if r["backgroundColor"] == excalidraw.NOTE_FILL]
        self.assertEqual(len(notes), 1)
        ys = [a["y"] for a in arrows]
        self.assertEqual(ys, sorted(ys))
        self.no_overlap(rects)
        self.no_overlap([byid[a["id"] + "-t"] for a in arrows if a["id"] + "-t" in byid])
        for text in [e for e in items if e["type"] == "text" and e["containerId"]]:
            self.assertIn({"id": text["id"], "type": "text"}, byid[text["containerId"]]["boundElements"])

    def test_self_message_is_a_loop(self):
        value = render.sequence("T", [("a", "Locker API", False)], [("a", "a", "tick", False)])
        arrow = next(e for e in excalidraw.document(value)["elements"] if e["type"] == "arrow")
        self.assertEqual(len(arrow["points"]), 4)
        self.assertEqual(arrow["points"][0][0], arrow["points"][-1][0])

    def no_overlap(self, rects):
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertTrue(a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"] or
                                a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"], (a["id"], b["id"]))

    def test_excalidraw_er_keeps_line_breaks_and_caps(self):
        items = json.loads(self.files()[DIAGRAMS + "data-model-1.excalidraw"])["elements"]
        parcel = next(e for e in items if e["type"] == "text" and e["text"].startswith("Parcel\n"))
        self.assertEqual(parcel["text"], parcel["originalText"])
        self.assertEqual(parcel["text"].split("\n"), ["Parcel", "id text PK", "locker_id integer FK", "pickup_code text",
                                                      "recipient text (pii)", "status text"])
        box = next(e for e in items if e["id"] == parcel["containerId"])
        self.assertGreaterEqual(box["width"], parcel["width"])
        self.assertGreaterEqual(box["height"], parcel["height"])
        arrow = next(e for e in items if e["type"] == "arrow")
        self.assertNotEqual(arrow["startBinding"]["elementId"], arrow["endBinding"]["elementId"])
        self.no_overlap([e for e in items if e["type"] == "rectangle"])

        value = render.er("T", [("w", "Wide", [("field_%02d" % i, "text", (), "") for i in range(45)]),
                                ("n", "Narrow", [])], [])
        texts = [e for e in excalidraw.document(value)["elements"] if e["type"] == "text" and e["containerId"]]
        wide = next(t for t in texts if t["text"].startswith("Wide"))
        rows = wide["text"].split("\n")
        self.assertEqual((len(rows), rows[-1]), (excalidraw.ER_LINES + 1, "+16 more"))
        self.no_overlap([e for e in excalidraw.document(value)["elements"] if e["type"] == "rectangle"])

    def test_excalidraw_state_and_access_use_the_flowchart_layout(self):
        pages = self.files()
        for name in ("data-model-3", "technical/locker-api-3", "README-2"):
            items = json.loads(pages[DIAGRAMS + name + ".excalidraw"])["elements"]
            self.assertTrue(items)
        value = kinds.state_values(fake())[0][2]
        Excalidraw("check").check(value)

    def test_deterministic(self):
        self.assertEqual(self.files(), self.files())
        self.assertEqual(render.build(fake()), render.build(fake()))


class Setting(unittest.TestCase):
    def test_absent_and_empty_leave_the_pages_as_they_are(self):
        from tests.unit.test_render import model
        plain = render.build(model())
        for views in ({}, {"kinds": []}):
            other = model()
            other.manifest = dict(other.manifest, views=views)
            self.assertEqual(render.build(other), plain)
        self.assertEqual(render.kinds({"views": {"kinds": ["access", "er", "sequence"]}}), ["sequence", "er", "access"])
        self.assertFalse(render.kinds_asked({}))
        self.assertFalse(render.kinds_asked({"views": {"diagrams": ["eraser"]}}))
        self.assertTrue(render.kinds_asked({"views": {"kinds": []}}))

    def test_worker_reads(self):
        for chosen, extra in (([], []), (["sequence", "access"], []), (["er"], ["data.md"]),
                              (["state"], ["data.md"]), (ALL, ["data.md"])):
            manifest = {"views": {"kinds": chosen}}
            self.assertEqual(cli.worker_reads("all", manifest), cli.WORKER_READS["all"] + extra)
            self.assertEqual(cli.worker_reads("changed", manifest), cli.WORKER_READS["changed"] + extra)
        self.assertEqual(cli.worker_reads(None, {}), cli.WORKER_READS["all"])


class Rendered(S.RepoCase):
    def manifest(self):
        return json.loads(S.read(self.root, M.MANIFEST))

    def test_render_sets_and_clears_the_kinds(self):
        self.assertFalse(S.run_json(self.root, "status")[1]["kinds_asked"])
        code, out = S.run_json(self.root, "render", "--kinds", "State, sequence")
        self.assertEqual((code, self.manifest()["views"]), (0, {"kinds": ["sequence", "state"]}))
        self.assertEqual((out["kinds"], out["thin"]), (["sequence", "state"], []))
        self.assertIn(kinds.DATA_MODEL, out["written"])
        self.assertTrue(S.run_json(self.root, "status")[1]["kinds_asked"])
        self.assertEqual(S.run(self.root, "validate")[0], 0)
        self.assertEqual(S.run_json(self.root, "render", "--diagrams", "eraser")[1]["kinds"], ["sequence", "state"])
        self.assertEqual(self.manifest()["views"], {"diagrams": ["eraser"], "kinds": ["sequence", "state"]})
        S.run(self.root, "render", "--diagrams", "none")
        self.assertEqual(self.manifest()["views"], {"kinds": ["sequence", "state"]})
        code, out = S.run_json(self.root, "render", "--kinds", "none")
        self.assertEqual((code, self.manifest()["views"]), (0, {"kinds": []}))
        self.assertIn(kinds.DATA_MODEL, out["removed"])
        self.assertTrue(S.run_json(self.root, "status")[1]["kinds_asked"])
        for bad in ("svg", "", "er,svg", "none,er"):
            self.assertEqual(S.run(self.root, "render", "--kinds", bad)[0], 2, bad)
        self.assertEqual(self.manifest()["views"], {"kinds": []})

    def test_the_setting_is_in_the_digest(self):
        before = render.digest(S.load(self.root))
        manifest = dict(self.manifest(), views={"kinds": []})
        S.put(self.root, M.MANIFEST, M.format_value(manifest, "manifest"))
        self.assertNotEqual(render.digest(S.load(self.root)), before)
        self.assertEqual(render.out_of_sync(S.load(self.root)), [VIEWS + "README.md"])

    def test_thin_reports_missing_data(self):
        self.edit("entity:locker", lambda v: v.pop("fields"))
        code, out = S.run_json(self.root, "render", "--kinds", "er,access")
        self.assertEqual((code, out["thin"]), (0, ["er: 1 of 2 persisted entities have fields"]))

    def test_crlf_data_model_is_not_stale(self):
        S.run(self.root, "render", "--kinds", "er,state")
        text = S.read(self.root, kinds.DATA_MODEL)
        S.put(self.root, kinds.DATA_MODEL, text.replace("\n", "\r\n"))
        self.assertEqual(render.out_of_sync(S.load(self.root)), [])

    def test_extended_model_validates(self):
        S.extend_for_kinds(self.root, anchor=True)
        S.run(self.root, "render")
        code, findings = self.findings("--strict")
        self.assertEqual((code, findings), (0, []))

    def test_packet_and_estimate_read_data_md_only_for_er_or_state(self):
        S.run(self.root, "estimate", "--write-plan")
        plain = S.run_json(self.root, "estimate")[1]
        self.assertEqual(S.run_json(self.root, "packet", "1")[1]["reads"], cli.WORKER_READS["all"])
        S.run(self.root, "render", "--kinds", "sequence,access")
        self.assertEqual(S.run_json(self.root, "packet", "1")[1]["reads"], cli.WORKER_READS["all"])
        S.run(self.root, "render", "--kinds", "er")
        self.assertEqual(S.run_json(self.root, "packet", "1")[1]["reads"], cli.WORKER_READS["all"] + ["data.md"])
        out = S.run_json(self.root, "estimate")[1]
        self.assertEqual(out["worker_reads"], cli.WORKER_READS["all"] + ["data.md"])
        self.assertEqual(out["overhead_per_worker"] - plain["overhead_per_worker"], cli._read_tokens(["data.md"]))
        self.assertGreater(cli._read_tokens(["data.md"]), 0)
        changed = S.run_json(self.root, "estimate", "--changed")[1]
        self.assertEqual(changed["worker_reads"], ["model.md", "update.md", "data.md"])

    def test_references_are_validated(self):
        for ref, message in (("entity:nowhere", "unknown element"), ("entity:locker#door", "has no part"),
                             ("rule:collection.window", "must refer to: entity")):
            def change(value, ref=ref):
                value["constraints"] = [{"type": "foreign-key", "fields": ["locker_id"], "references": ref}]
            self.edit("entity:parcel", change)
            code, findings = self.findings()
            self.assertEqual(code, 1, ref)
            self.assertTrue(any(f["rule"] == "V3" and message in f["message"] and
                                f.get("at") == "$.constraints[0].references" for f in findings), findings)
        self.edit("entity:parcel", lambda v: v["constraints"][0].update(references="entity:locker#id"))
        self.assertEqual(self.findings()[0], 0)
        self.edit("entity:parcel", lambda v: v["constraints"][0].update(references="not a ref"))
        code, findings = self.findings()
        self.assertTrue(any(f["rule"] == "V1" for f in findings), findings)


class Init(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.root = os.path.join(os.path.realpath(tempfile.mkdtemp(prefix="evermap-test-")), "parcels")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.root), True)
        S.build(self.root, with_model=False)

    def test_init_saves_the_choice(self):
        code, out = S.run_json(self.root, "init", "--kinds", "er,sequence", "--diagrams", "eraser")
        self.assertEqual((code, out["manifest"]["views"]), (0, {"diagrams": ["eraser"], "kinds": ["sequence", "er"]}))
        self.assertTrue(S.run_json(self.root, "status")[1]["kinds_asked"])

    def test_init_none_and_absent(self):
        self.assertEqual(S.run(self.root, "init", "--kinds", "bogus")[0], 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, *M.MANIFEST.split("/"))))
        code, out = S.run_json(self.root, "init", "--kinds", "none", "--diagrams", "none")
        self.assertEqual((code, out["manifest"]["views"]), (0, {"kinds": []}))


class Guides(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(REFERENCES, name), encoding="utf-8") as handle:
            return handle.read()

    def test_data_guide_is_short_and_worker_only(self):
        body = self.read("data.md")
        self.assertLessEqual(len(body.splitlines()), 40)
        for word in ("`references`", "`states.values`", "`states.transitions`", "`via`", "`guard`", "`nullable`",
                     "`sensitivity`"):
            self.assertIn(word, body)

    def test_discover_asks_once(self):
        calling = self.read("discover.md").split("## Worker")[0]
        step = calling.split("\n2. ", 1)[1].split("\n3. ", 1)[0]
        for phrase in ("Ask once", "`init`", "`--kinds` and `--diagrams`", "`kinds_asked` false", "`render --kinds`",
                       "costs more tokens"):
            self.assertIn(phrase, step)
        self.assertLessEqual(len(step.splitlines()), 6)
        self.assertNotIn("kinds", self.read("headless.md"))
        self.assertEqual(self.read("update.md").count("kinds_asked"), 1)

    def test_diagrams_guide_names_the_eraser_type(self):
        body = self.read("diagrams.md")
        for word in ("sequence-diagram", "entity-relationship-diagram", "thin"):
            self.assertIn(word, body)


if __name__ == "__main__":
    unittest.main()
