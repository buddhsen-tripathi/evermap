"""Diagrams in Excalidraw and Eraser files next to the Mermaid views (SPEC 7, D19)."""

import json
import os
import posixpath
import re
import unittest
from urllib.parse import unquote

from tests import support as S
from tests.unit.test_render import API, DB, FLOW, NOTIFIER, Fake, cross_calls, model
from evermap_lib import eraser, excalidraw
from evermap_lib import model as M
from evermap_lib import render

VIEWS = M.DIR + "/views/"
DIAGRAMS = VIEWS + "diagrams/"
BOTH = {"views": {"diagrams": ["excalidraw", "eraser"]}}
KEYS = {"id", "type", "x", "y", "width", "height", "angle", "strokeColor", "backgroundColor", "fillStyle",
        "strokeWidth", "strokeStyle", "roughness", "opacity", "groupIds", "frameId", "roundness", "seed",
        "version", "versionNonce", "isDeleted", "boundElements", "updated", "link", "locked"}


def value(nodes, edges, direction="LR", styles=None, title="Component: Locker API - neighbourhood"):
    return render.diagram(title, nodes, edges, styles, direction)


def chain(count, cycle=False):
    nodes = [("c:%d" % i, "Node %d" % i) for i in range(count)]
    edges = [("c:%d" % i, "c:%d" % (i + 1), "") for i in range(count - 1)]
    return nodes, edges + ([("c:%d" % (count - 1), "c:0", "back")] if cycle else [])


def with_formats(elements=None, manifest=BOTH):
    fake = model(elements)
    fake.manifest = dict(fake.manifest, **manifest)
    return fake


class Excalidraw(unittest.TestCase):
    maxDiff = None

    def check(self, diagram):
        """Parse the file and check bindings, containers and overlaps; return the elements."""
        doc = json.loads(excalidraw.dumps(diagram))
        self.assertEqual((doc["type"], doc["version"], doc["source"], doc["files"]), ("excalidraw", 2, "evermap", {}))
        self.assertEqual(doc["appState"], {"viewBackgroundColor": "#ffffff", "gridSize": None})
        items = doc["elements"]
        byid = {e["id"]: e for e in items}
        self.assertEqual(len(byid), len(items), "ids are unique")
        rects = {e["id"]: e for e in items if e["type"] == "rectangle"}
        for element in items:
            self.assertLessEqual(KEYS, set(element), element["type"])
            if element["type"] == "text":
                self.assertEqual((element["fontFamily"], element["lineHeight"]), (1, 1.25))
                self.assertEqual(element["text"].replace("\n", " "), element["originalText"])
                if element["containerId"] is not None:
                    container = byid[element["containerId"]]
                    self.assertIn({"id": element["id"], "type": "text"}, container["boundElements"])
                    self.assertEqual((element["textAlign"], element["verticalAlign"]), ("center", "middle"))
            if element["type"] == "rectangle":
                self.assertEqual(element["roundness"], {"type": 3})
            if element["type"] == "arrow":
                for end in ("startBinding", "endBinding"):
                    binding = element[end]
                    self.assertIn(binding["elementId"], rects)
                    self.assertEqual((binding["focus"], binding["gap"]), (0, 4))
                    self.assertIn({"id": element["id"], "type": "arrow"}, rects[binding["elementId"]]["boundElements"])
                self.assertEqual(element["points"][0], [0, 0])
                self.assertEqual((element["startArrowhead"], element["endArrowhead"]), (None, "arrow"))
        boxes = list(rects.values())
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                apart = (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"] or
                         a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])
                self.assertTrue(apart, (a["id"], b["id"]))
        titles = [e for e in items if e["type"] == "text" and e["containerId"] is None]
        self.assertEqual(len(titles), 1)
        self.assertTrue(all(titles[0]["y"] + titles[0]["height"] <= r["y"] for r in boxes))
        return items

    def rect(self, items, label):
        text = next(e for e in items if e["type"] == "text" and e["originalText"] == label)
        return next(e for e in items if e["id"] == text["containerId"])

    def test_layers_are_columns_left_to_right_and_rows_top_down(self):
        nodes, edges = chain(3)
        lr = self.check(value(nodes, edges))
        xs = [self.rect(lr, "Node %d" % i)["x"] for i in range(3)]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len({self.rect(lr, "Node %d" % i)["y"] for i in range(3)}), 1)
        td = self.check(value(nodes, edges, "TD"))
        ys = [self.rect(td, "Node %d" % i)["y"] for i in range(3)]
        self.assertEqual(ys, sorted(ys))
        self.assertLess(ys[0] + self.rect(td, "Node 0")["height"], ys[1])

    def test_a_long_layer_wraps_into_bands(self):
        nodes = [("c:%02d" % i, "Node %d" % i) for i in range(20)] + [("z", "Sink")]
        items = self.check(value(nodes, [("c:%02d" % i, "z", "") for i in range(20)]))
        sink = self.rect(items, "Sink")["id"]
        rects = [e for e in items if e["type"] == "rectangle" and e["id"] != sink]
        columns = {e["x"] for e in rects}
        self.assertEqual(len(columns), 3)
        self.assertEqual(len([e for e in rects if e["x"] == min(columns)]), excalidraw.BAND)

    def test_boxes_without_arrows_go_in_a_grid_below(self):
        nodes = [("a", "Locker API"), ("b", "Notifier")] + [("c:%d" % i, "Alone %d" % i) for i in range(6)]
        items = self.check(value(nodes, [("a", "b", "")]))
        bottom = max(self.rect(items, n)["y"] + self.rect(items, n)["height"] for n in ("Locker API", "Notifier"))
        alone = [self.rect(items, "Alone %d" % i) for i in range(6)]
        self.assertTrue(all(r["y"] > bottom for r in alone))
        self.assertEqual(len({r["y"] for r in alone}), 2)

    def test_left_to_right_gap_fits_the_edge_label(self):
        label = "sends the pickup code by text message"
        items = self.check(value([("a", "Locker API"), ("b", "Notifier")], [("a", "b", label)]))
        a, b = self.rect(items, "Locker API"), self.rect(items, "Notifier")
        text = next(e for e in items if e["type"] == "text" and e["originalText"] == label)
        self.assertGreater(b["x"] - (a["x"] + a["width"]), text["width"])

    def test_nodes_in_one_layer_do_not_overlap_and_arrows_join_box_edges(self):
        nodes = [("a", "Locker API"), ("b", "Locker database"), ("c", "Notifier"), ("d", "Parcel outbox")]
        edges = [("a", "b", "sql"), ("a", "c", ""), ("a", "d", "")]
        items = self.check(value(nodes, edges))
        source = self.rect(items, "Locker API")
        for arrow in [e for e in items if e["type"] == "arrow"]:
            self.assertEqual(arrow["x"], source["x"] + source["width"])
            self.assertEqual(arrow["y"], source["y"] + source["height"] // 2)
            target = next(e for e in items if e["id"] == arrow["endBinding"]["elementId"])
            self.assertEqual(arrow["x"] + arrow["points"][1][0], target["x"])
        label = next(e for e in items if e["type"] == "text" and e["originalText"] == "sql")
        self.assertEqual(next(e for e in items if e["id"] == label["containerId"])["type"], "arrow")

    def test_cycles_self_loops_and_duplicate_edges_do_not_crash(self):
        nodes, edges = chain(4, cycle=True)
        edges += [("c:1", "c:1", "retry"), ("c:0", "c:1", ""), ("c:0", "c:1", ""), ("c:2", "missing", "")]
        items = self.check(value(nodes, edges))
        self.assertEqual(len([e for e in items if e["type"] == "arrow"]), 7)
        self.check(value(nodes, edges, "TD"))
        self.check(value([], []))

    def test_long_labels_wrap_and_grow_the_box(self):
        long = "Check that the locker is free and that the courier may use it " * 2
        items = self.check(value([("a", long), ("b", "Short")], [("a", "b", "")]))
        box, short = self.rect(items, " ".join(long.split())), self.rect(items, "Short")
        self.assertEqual((box["width"], short["width"]), (excalidraw.MAX_W, excalidraw.MIN_W))
        self.assertGreater(box["height"], short["height"])

    def test_styles_colour_and_dash_the_boxes(self):
        nodes = [("a", "Added"), ("r", "Removed"), ("m", "Modified"), ("n", "Neutral")]
        items = self.check(value(nodes, [], styles={"added": {"a"}, "removed": {"r"}, "modified": {"m"}}))
        found = {k: (self.rect(items, k)["strokeColor"], self.rect(items, k)["strokeStyle"])
                 for k in ("Added", "Removed", "Modified", "Neutral")}
        self.assertEqual(found, {"Added": ("#2da44e", "solid"), "Removed": ("#cf222e", "dashed"),
                                 "Modified": ("#bf8700", "solid"), "Neutral": ("#1e1e1e", "solid")})
        self.assertEqual(self.rect(items, "Neutral")["backgroundColor"], "#ffffff")

    def test_labels_are_plain_text(self):
        items = self.check(value([("a", 'Use `a|b` & "c" <d>')], []))
        self.assertTrue(self.rect(items, 'Use `a|b` & "c" <d>'))

    def test_bytes_are_deterministic_and_depend_on_the_title(self):
        nodes, edges = chain(5, cycle=True)
        first = excalidraw.dumps(value(nodes, edges))
        self.assertEqual(first, excalidraw.dumps(value(list(nodes), list(edges))))
        self.assertTrue(first.endswith("}\n") and "\r" not in first)
        other = excalidraw.dumps(value(nodes, edges, title="Workflow: Deposit a parcel - flow"))
        self.assertNotEqual(set(re.findall(r'"id": "(\w+)"', first)), set(re.findall(r'"id": "(\w+)"', other)))


class Eraser(unittest.TestCase):
    def test_header_nodes_then_connections(self):
        text = eraser.dumps(value([("a", "Locker API"), ("b", "Locker database")], [("a", "b", "sql"), ("b", "a", "")]))
        self.assertEqual(text, 'flow-chart\ntitle Component: Locker API - neighbourhood\ndirection right\n'
                               'n0 [label: "Locker API"]\nn1 [label: "Locker database"]\nn0 > n1: sql\nn1 > n0\n')
        self.assertIn("\ndirection down\n", eraser.dumps(value([("a", "A")], [], "TD")))

    def test_labels_are_sanitised(self):
        nodes = [("a", 'Say "hi" [now] {x}; ok\nthen'), ("b", "Plain: a, b")]
        text = eraser.dumps(value(nodes, [("a", "b", 'fails: "slow", [retry]; {x}')]))
        lines = text.splitlines()
        self.assertEqual(lines[3], 'n0 [label: "Say \'hi\' now x, ok then"]')
        self.assertEqual(lines[4], 'n1 [label: "Plain: a, b"]')
        self.assertEqual(lines[5], "n0 > n1: fails 'slow' retry x")

    def test_names_are_unique_and_defined_before_use(self):
        nodes, edges = chain(12, cycle=True)
        nodes.append(("c:3", "Duplicate"))
        lines = eraser.dumps(value(nodes, edges + [("c:4", "c:4", "")])).splitlines()
        defined = [re.match(r"(n\d+) \[", line).group(1) for line in lines if " [label: " in line]
        self.assertEqual(len(defined), len(set(defined)))
        self.assertEqual(defined, ["n%d" % i for i in range(12)])
        first = next(i for i, line in enumerate(lines) if " > " in line)
        self.assertTrue(all(" > " in line for line in lines[first:]))

    def test_styles_become_colours(self):
        text = eraser.dumps(value([("a", "A"), ("r", "R"), ("m", "M")], [],
                                  styles={"added": {"a"}, "removed": {"r"}, "modified": {"m"}}))
        for line in ('n0 [label: "A", color: green]', 'n1 [label: "R", color: red]', 'n2 [label: "M", color: orange]'):
            self.assertIn(line + "\n", text)


class Pages(unittest.TestCase):
    maxDiff = None

    def test_pages_are_unchanged_without_the_setting(self):
        plain = render.build(model())
        self.assertFalse([p for p in plain if p.startswith(DIAGRAMS)])
        self.assertNotIn("Open in:", "".join(plain.values()))
        self.assertEqual(plain, render.build(with_formats(manifest={"views": {}})))

    def test_files_and_open_in_links(self):
        pages = render.build(with_formats(cross_calls))
        for name in ("README-1", "technical/locker-api-1", "technical/locker-api-2", "workflows/deposit-parcel-1"):
            for ext in (".excalidraw", ".eraserdiagram"):
                self.assertIn(DIAGRAMS + name + ext, pages)
        for path, page in pages.items():
            if not path.endswith(".md"):
                continue
            blocks = page.count("```mermaid")
            links = re.findall(r"^Open in: \[Excalidraw\]\(([^)]+)\) · \[Eraser\]\(([^)]+)\)$", page, re.M)
            self.assertEqual(len(links), blocks, path)
            for number, pair in enumerate(links, 1):
                for link in pair:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(link)))
                    self.assertIn(target, pages)
                    self.assertTrue(target.endswith("-%d%s" % (number, os.path.splitext(target)[1])))
        self.assertIn("```\n\nOpen in: [Excalidraw](../diagrams/technical/locker-api-1.excalidraw) · "
                      "[Eraser](../diagrams/technical/locker-api-1.eraserdiagram)\n\n## Operation calls",
                      pages[VIEWS + "technical/locker-api.md"])
        self.assertIn("title Component: Locker API - neighbourhood\n",
                      pages[DIAGRAMS + "technical/locker-api-1.eraserdiagram"])
        self.assertIn("title Workflow: Deposit a parcel - flow\ndirection down\n",
                      pages[DIAGRAMS + "workflows/deposit-parcel-1.eraserdiagram"])
        self.assertIn("title System: Parcel lockers - context\n", pages[DIAGRAMS + "README-1.eraserdiagram"])

    def test_only_the_chosen_format(self):
        pages = render.build(with_formats(manifest={"views": {"diagrams": ["eraser"]}}))
        self.assertFalse([p for p in pages if p.endswith(".excalidraw")])
        self.assertIn("\nOpen in: [Eraser](diagrams/README-1.eraserdiagram)\n", pages[VIEWS + "README.md"])

    def test_diagram_values_match_the_mermaid(self):
        page = render.Page(VIEWS + "technical/locker-api.md")
        lines = render.flowchart([("a", "A"), ("b", "B")], [("a", "b", "x"), ("a", "z", "")], page=page, title="T")
        self.assertEqual(lines, render.flowchart([("a", "A"), ("b", "B")], [("a", "b", "x")]))
        self.assertEqual(page.diagrams, [{"title": "T", "direction": "LR", "nodes": [("a", "A"), ("b", "B")],
                                          "edges": [("a", "b", "x")], "styles": {}}])
        self.assertEqual(page.files(), {})


class Rendered(S.RepoCase):
    def manifest(self):
        return json.loads(S.read(self.root, M.MANIFEST))

    def stale(self):
        return render.out_of_sync(S.load(self.root))

    def test_render_sets_writes_detects_and_clears(self):
        code, out = S.run_json(self.root, "render", "--diagrams", "Eraser, excalidraw,eraser")
        self.assertEqual(code, 0)
        self.assertEqual(self.manifest()["views"], {"diagrams": ["excalidraw", "eraser"]})
        self.assertEqual(S.read(self.root, M.MANIFEST), M.format_value(self.manifest(), "manifest"))
        self.assertEqual(out["diagrams"], ["mermaid", "excalidraw", "eraser"])
        files = sorted(p for p in out["written"] if p.startswith(DIAGRAMS))
        self.assertIn(DIAGRAMS + "technical/locker-api-1.excalidraw", files)
        blocks = sum(d.decode("utf-8").count("```mermaid") for p, d in S.tree(self.root, ".evermap/views").items()
                     if p.endswith(".md"))
        self.assertEqual(len(files), 2 * blocks)
        self.assertEqual(self.stale(), [])
        self.assertEqual(S.run(self.root, "validate")[0], 0)
        self.assertEqual(S.run_json(self.root, "render")[1]["written"], [])  # plain render keeps the setting

        excal = DIAGRAMS + "technical/locker-api-1.excalidraw"
        os.remove(os.path.join(self.root, *excal.split("/")))
        self.assertEqual(self.stale(), [excal])
        S.put(self.root, excal, "{}\n")
        self.assertEqual(self.stale(), [excal])
        S.run(self.root, "render")
        text = S.read(self.root, DIAGRAMS + "workflows/deposit-parcel-1.eraserdiagram")
        S.put(self.root, DIAGRAMS + "workflows/deposit-parcel-1.eraserdiagram", text.replace("\n", "\r\n"))
        self.assertEqual(self.stale(), [])  # a CRLF checkout is not stale

        S.put(self.root, DIAGRAMS + "technical/gone-1.excalidraw", "{}\n")
        S.put(self.root, DIAGRAMS + "notes.txt", "mine\n")
        S.put(self.root, VIEWS + "technical/mine.md", "hand written\n")
        self.assertEqual(self.stale(), [DIAGRAMS + "technical/gone-1.excalidraw"])
        code, out = S.run_json(self.root, "render", "--diagrams", "none")
        self.assertEqual(code, 0)
        self.assertNotIn("views", self.manifest())
        self.assertIn(DIAGRAMS + "technical/gone-1.excalidraw", out["removed"])
        self.assertEqual(sorted(S.tree(self.root, ".evermap/views/diagrams")), [DIAGRAMS + "notes.txt"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, *(VIEWS + "technical/mine.md").split("/"))))
        self.assertNotIn("Open in:", S.read(self.root, VIEWS + "technical/locker-api.md"))
        self.assertEqual(self.stale(), [])

    def test_changing_the_setting_makes_views_stale(self):
        manifest = dict(self.manifest(), views={"diagrams": ["eraser"]})
        S.put(self.root, M.MANIFEST, M.format_value(manifest, "manifest"))
        stale = self.stale()
        self.assertIn(VIEWS + "README.md", stale)
        self.assertIn(DIAGRAMS + "README-1.eraserdiagram", stale)
        self.assertEqual(S.run_json(self.root, "status")[0], 0)

    def test_bad_values_are_usage_errors_and_the_schema_rejects_duplicates(self):
        for bad in ("svg", "", "excalidraw,svg", "none,eraser"):
            self.assertEqual(S.run(self.root, "render", "--diagrams", bad)[0], 2, bad)
        self.assertNotIn("views", self.manifest())
        manifest = dict(self.manifest(), views={"diagrams": ["eraser", "eraser"]})
        S.put(self.root, M.MANIFEST, M.format_value(manifest, "manifest"))
        code, findings = self.findings()
        self.assertEqual(code, 1)
        self.assertTrue(any("unique" in f["message"] for f in findings), findings)


if __name__ == "__main__":
    unittest.main()
