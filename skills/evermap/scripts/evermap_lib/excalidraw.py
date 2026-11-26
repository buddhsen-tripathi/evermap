"""Excalidraw emitter: a diagram value (render.diagram) as a deterministic .excalidraw file.

Layout is layered, without external libraries: cycles are broken by a depth-first search in
sorted id order (back edges are reversed for layering only), a node's layer is its longest path
from a source, nodes in a layer are ordered by four barycentre sweeps (ties by id), and LR puts
layers in columns, TD in rows. Arrows are straight between box edge midpoints. State values use
this layout; ER values use it with one line per attribute; sequence values have their own
(sequence_elements).
"""

import hashlib
import json
import textwrap

from . import render

CHAR, MIN_W, MAX_W, PAD = 9, 140, 260, 20
LINE, MIN_H = 20, 60                     # 16 px text at line height 1.25
LAYER_GAP, NODE_GAP, TITLE_GAP = 80, 40, 60
BAND = 8   # boxes per column (LR) or row (TD) before a layer wraps
GRID = 4   # boxes per row in the grid of boxes that have no arrow
FONT = 16
INK, PAPER = "#1e1e1e", "#ffffff"
STYLES = (("removed", "#cf222e", "dashed"), ("added", "#2da44e", "solid"), ("modified", "#bf8700", "solid"))


def _hash(*parts):
    return hashlib.sha256("\0".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _wrap(text):
    width = (MAX_W - 2 * PAD) // CHAR
    return textwrap.wrap(text, width, break_long_words=True, break_on_hyphens=False) or [""]


def _layers(order, edges):
    """{id: layer} from the edges, cycles broken by a DFS in sorted id order."""
    succ = {n: sorted({b for a, b in edges if a == n and b != n}) for n in order}
    state, dag = {}, set()
    for start in sorted(order):
        if start in state:
            continue
        state[start] = 1
        stack = [(start, iter(succ[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
            elif state.get(child) == 1:
                dag.add((child, node))   # back edge: reversed for layering only
            else:
                dag.add((node, child))
                if child not in state:
                    state[child] = 1
                    stack.append((child, iter(succ[child])))
    layer = {n: 0 for n in order}
    preds = {n: sorted(a for a, b in dag if b == n) for n in order}
    done, ready = set(), sorted(n for n in order if not preds[n])
    while ready:
        node = ready.pop(0)
        done.add(node)
        for child in sorted(b for a, b in dag if a == node):
            layer[child] = max(layer[child], layer[node] + 1)
            if all(p in done for p in preds[child]) and child not in done and child not in ready:
                ready.append(child)
        ready.sort()
    return layer, dag


def _order(order, layer, dag):
    """Nodes per layer, ordered by four barycentre sweeps with ties broken by id."""
    rows = [sorted(n for n in order if layer[n] == k) for k in range(max(layer.values()) + 1)]
    pos = {n: i for row in rows for i, n in enumerate(row)}
    for sweep in range(4):
        down = sweep % 2 == 0
        for row in (rows[1:] if down else rows[-2::-1]):
            def centre(n):
                near = [a for a, b in dag if b == n] if down else [b for a, b in dag if a == n]
                return sum(pos[m] for m in near) / float(len(near)) if near else float(pos[n])
            row.sort(key=lambda n: (centre(n), n))
            pos.update((n, i) for i, n in enumerate(row))
    return rows


def _base(ident, kind, x, y, width, height, seed_key, stroke=INK, background="transparent", style="solid",
          stroke_width=2):
    return {
        "id": ident, "type": kind, "x": x, "y": y, "width": width, "height": height, "angle": 0,
        "strokeColor": stroke, "backgroundColor": background, "fillStyle": "solid", "strokeWidth": stroke_width,
        "strokeStyle": style, "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": int(_hash("seed", seed_key)[:8], 16) % 2147483647,
        "version": 1, "versionNonce": int(_hash("nonce", seed_key)[:8], 16) % 2147483647,
        "isDeleted": False, "boundElements": None, "updated": 1, "link": None, "locked": False,
    }


def _text(ident, x, y, lines, original, seed_key, container=None, size=FONT):
    element = _base(ident, "text", x, y, max(len(line) for line in lines) * CHAR * size // FONT,
                    len(lines) * LINE * size // FONT, seed_key)
    element.update({"text": "\n".join(lines), "originalText": original, "fontSize": size, "fontFamily": 1,
                    "textAlign": "center" if container else "left",
                    "verticalAlign": "middle" if container else "top",
                    "containerId": container, "lineHeight": 1.25, "autoResize": True})
    return element


def elements(value, lines=None):
    """Elements of a flowchart value (also a state value). lines: {node id: [line]} for boxes whose
    label is several given lines (ER entities): kept as they are, the box widened to fit."""
    title, lines = value["title"], lines or {}
    nodes, labels = [], {}
    for ident, text in value["nodes"]:
        if ident not in labels:
            nodes.append(ident)
            labels[ident] = "\n".join(lines[ident]) if ident in lines else render.plain(text)
    edges = [(a, b, render.plain(t or "")) for a, b, t in value["edges"] if a in labels and b in labels]
    wrapped = {n: lines[n] if n in lines else _wrap(labels[n]) for n in nodes}
    size = {n: (max(max(len(x) for x in wrapped[n]) * CHAR + 2 * PAD, MIN_W) if n in lines else
                MAX_W if len(wrapped[n]) > 1 else min(max(len(wrapped[n][0]) * CHAR + 2 * PAD, MIN_W), MAX_W),
                max(MIN_H, len(wrapped[n]) * LINE + 2 * PAD)) for n in nodes}
    box = {}
    # Boxes with no arrow go in a grid below the layered part, so arrows never cross them.
    linked = {n for a, b, _ in edges for n in (a, b)}
    connected = [n for n in nodes if n in linked]
    alone = [n for n in nodes if n not in linked]
    if connected:
        layer, dag = _layers(connected, [(a, b) for a, b, _ in edges])
        rows = _order(connected, layer, dag)
        across = value["direction"] == "LR"   # LR: layers are columns; TD: layers are rows
        # LR: widen a gap so the longest label on an edge leaving that column fits beside the arrow.
        level = {n: k for k, row in enumerate(rows) for n in row}
        layer_gaps = [LAYER_GAP] * len(rows)
        if across:
            for a, b, t in edges:
                if t and level[a] != level[b]:
                    k = min(level[a], level[b])
                    layer_gaps[k] = max(layer_gaps[k], min(len(t), 40) * CHAR + 2 * PAD)
        # A long layer wraps into bands of at most BAND boxes, so the diagram is not one tall strip.
        bands, gaps = [], []
        for k, row in enumerate(rows):
            chunks = [row[i:i + BAND] for i in range(0, len(row), BAND)]
            bands += chunks
            gaps += [NODE_GAP] * (len(chunks) - 1) + [layer_gaps[k]]
        rows = bands
        thick = [max(size[n][0 if across else 1] for n in row) for row in rows]
        spans = [sum(size[n][1 if across else 0] for n in row) + NODE_GAP * (len(row) - 1) for row in rows]
        offset = 0
        for k, row in enumerate(rows):
            along = (max(spans) - spans[k]) // 2
            for n in row:
                w, h = size[n]
                if across:
                    box[n] = (offset + (thick[k] - w) // 2, TITLE_GAP + along, w, h)
                    along += h + NODE_GAP
                else:
                    box[n] = (along, TITLE_GAP + offset + (thick[k] - h) // 2, w, h)
                    along += w + NODE_GAP
            offset += thick[k] + gaps[k]
    top = max([y + h for _, y, _, h in box.values()] + [TITLE_GAP - LAYER_GAP]) + LAYER_GAP
    pitch = max([MAX_W] + [size[n][0] for n in alone])
    for i in range(0, len(alone), GRID):
        row = alone[i:i + GRID]
        for j, n in enumerate(row):
            w, h = size[n]
            box[n] = (j * (pitch + NODE_GAP) + (pitch - w) // 2, top, w, h)
        top += max(size[n][1] for n in row) + NODE_GAP
    ids = {n: _hash(title, "node", n)[:20] for n in nodes}
    out = [_text(_hash(title, "title")[:20], 0, 0, [render.plain(title)], render.plain(title), (title, "title"),
                 size=20)]
    rects = {}
    for n in nodes:
        stroke, style, width = INK, "solid", 2
        for name, colour, dash in STYLES:
            if n in (value["styles"].get(name) or ()):
                stroke, style, width = colour, dash, 4
                break
        x, y, w, h = box[n]
        rect = _base(ids[n], "rectangle", x, y, w, h, (title, "node", n), stroke, PAPER, style, width)
        rect.update(roundness={"type": 3}, boundElements=[{"id": ids[n] + "-t", "type": "text"}])
        text_lines = wrapped[n]
        tw, th = max(len(line) for line in text_lines) * CHAR, len(text_lines) * LINE
        rects[n] = rect
        out += [rect, _text(ids[n] + "-t", x + (w - tw) // 2, y + (h - th) // 2, text_lines, labels[n],
                            (title, "text", n), container=ids[n])]
    seen = {}
    for a, b, text in edges:
        key = (a, b, text)
        seen[key] = seen.get(key, 0) + 1
        ident = _hash(title, "edge", a, b, text, seen[key])[:20]
        start, end = _ends(box[a], box[b], value["direction"])
        points = [[0, 0], [end[0] - start[0], end[1] - start[1]]]
        if a == b:   # a self-loop leaves the right side and comes back lower down
            x, y, w, h = box[a]
            start = (x + w, y + h // 3)
            points = [[0, 0], [40, 0], [40, h // 3], [0, h // 3]]
        arrow = _base(ident, "arrow", start[0], start[1], max(p[0] for p in points) - min(p[0] for p in points),
                      max(p[1] for p in points) - min(p[1] for p in points), (title, "edge", ident))
        arrow.update({"roundness": None, "points": points, "lastCommittedPoint": None,
                      "startBinding": {"elementId": ids[a], "focus": 0, "gap": 4},
                      "endBinding": {"elementId": ids[b], "focus": 0, "gap": 4},
                      "startArrowhead": None, "endArrowhead": "arrow", "elbowed": False})
        out.append(arrow)
        for n in (a, b) if a != b else (a,):
            rects[n]["boundElements"].append({"id": ident, "type": "arrow"})
        if text:
            lines = _wrap(text)
            mid = (start[0] + points[-1][0] // 2, start[1] + points[-1][1] // 2) if a != b else \
                (start[0] + 40, start[1] + points[2][1] // 2)
            tw, th = max(len(line) for line in lines) * CHAR, len(lines) * LINE
            arrow["boundElements"] = [{"id": ident + "-t", "type": "text"}]
            out.append(_text(ident + "-t", mid[0] - tw // 2, mid[1] - th // 2, lines, text, (title, "label", ident),
                             container=ident))
    return out


def _ends(source, target, direction):
    """Edge midpoints facing each other: along the layer axis, or across it within a layer."""
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    if direction == "LR" and (tx >= sx + sw or tx + tw <= sx) or direction != "LR" and not (
            ty >= sy + sh or ty + th <= sy):
        if tx >= sx + sw:
            return (sx + sw, sy + sh // 2), (tx, ty + th // 2)
        return (sx, sy + sh // 2), (tx + tw, ty + th // 2)
    if ty >= sy + sh:
        return (sx + sw // 2, sy + sh), (tx + tw // 2, ty)
    return (sx + sw // 2, sy), (tx + tw // 2, ty + th)


ER_LINES = 30


def er_elements(value):
    """An ER value as layered boxes: the entity name, then one line per attribute (at most
    ER_LINES, then "+N more"); an arrow per relationship, labelled with its fields."""
    nodes, lines = [], {}
    for ident, name, attrs in value["entities"]:
        rows = [render.plain("%s %s%s%s" % (n, t, " " + ",".join(k) if k else "", " (%s)" % c if c else ""))
                for n, t, k, c in attrs]
        if len(rows) > ER_LINES:
            rows = rows[:ER_LINES - 1] + ["+%d more" % (len(rows) - ER_LINES + 1)]
        nodes.append((ident, name))
        lines[ident] = [render.plain(name)] + rows
    edges = [(a, b, ", ".join(fields)) for a, b, _, _, fields, _ in value["relationships"]]
    return elements(render.diagram(value["title"], nodes, edges), lines)


NOTE_FILL, ROW, LOOP = "#fff3bf", 56, 40


def sequence_elements(value):
    """A sequence value: participant boxes across the top, a dashed lifeline under each, then
    messages top to bottom as horizontal arrows (dashed when async; a self-message is a small
    loop) and notes as lightly filled boxes over their participant. Each participant box, its
    text and its lifeline share a group, so they move together. Messages bind to nothing: a
    binding would pull them up to the box when it moves. Their labels are free text above the
    arrow (beside a loop), since Excalidraw centres a bound label on the arrow and hides it."""
    title = value["title"]
    people = [p[0] for p in value["participants"]]
    labels = {p[0]: render.plain(p[1]) for p in value["participants"]}
    wrapped = {n: _wrap(labels[n]) for n in people}
    size = {n: (MAX_W if len(wrapped[n]) > 1 else min(max(len(wrapped[n][0]) * CHAR + 2 * PAD, MIN_W), MAX_W),
                max(MIN_H, len(wrapped[n]) * LINE + 2 * PAD)) for n in people}
    longest = max([min(len(render.plain(m[2])), 40) for m in value["messages"]] + [0])
    pitch = max([MAX_W] + [w for w, _ in size.values()]) + max(NODE_GAP, longest * CHAR // 2)
    head = max([h for _, h in size.values()] + [MIN_H])
    centre = {n: i * pitch + pitch // 2 for i, n in enumerate(people)}
    ids = {n: _hash(title, "node", n)[:20] for n in people}
    out = [_text(_hash(title, "title")[:20], 0, 0, [render.plain(title)], render.plain(title), (title, "title"),
                 size=20)]
    rects = {}
    for n in people:
        w, h = size[n]
        rect = _base(ids[n], "rectangle", centre[n] - w // 2, TITLE_GAP + (head - h) // 2, w, h, (title, "node", n),
                     INK, PAPER)
        rect.update(roundness={"type": 3}, boundElements=[{"id": ids[n] + "-t", "type": "text"}],
                    groupIds=[ids[n] + "-g"])
        rects[n] = rect
        tw, th = max(len(x) for x in wrapped[n]) * CHAR, len(wrapped[n]) * LINE
        text = _text(ids[n] + "-t", rect["x"] + (w - tw) // 2, rect["y"] + (h - th) // 2, wrapped[n],
                     labels[n], (title, "text", n), container=ids[n])
        text["groupIds"] = [ids[n] + "-g"]
        out += [rect, text]
    y = TITLE_GAP + head + ROW
    drawn, count = [], {}
    events = [(n[0], 0, n) for n in value["notes"]] + [(i, 1, m) for i, m in enumerate(value["messages"])]
    for index, is_message, item in sorted(events, key=lambda e: (e[0], e[1])):
        if not is_message:
            _, who, text, _ = item
            body = _wrap(render.plain(text))
            w = min(max(max(len(x) for x in body) * CHAR + 2 * PAD, MIN_W), MAX_W)
            h = len(body) * LINE + PAD
            ident = _hash(title, "note", index, who, text)[:20]
            note = _base(ident, "rectangle", centre[who] - w // 2, y, w, h, (title, "note", ident), INK, NOTE_FILL)
            note.update(roundness={"type": 3}, boundElements=[{"id": ident + "-t", "type": "text"}])
            tw = max(len(x) for x in body) * CHAR
            drawn += [note, _text(ident + "-t", note["x"] + (w - tw) // 2, y + PAD // 2, body, render.plain(text),
                                  (title, "note-text", ident), container=ident)]
            y += h + NODE_GAP // 2
            continue
        a, b, text, dashed = item
        key = (a, b, text)
        count[key] = count.get(key, 0) + 1
        ident = _hash(title, "message", a, b, text, count[key])[:20]
        if a == b:
            points = [[0, 0], [LOOP, 0], [LOOP, ROW // 2], [0, ROW // 2]]
        else:
            points = [[0, 0], [centre[b] - centre[a], 0]]
        label = render.plain(text)
        body = _wrap(label) if label else []
        tw, th = (max(len(x) for x in body) * CHAR, len(body) * LINE) if body else (0, 0)
        top = y + (th + 4 if a != b and body else 0)   # a straight arrow's label sits above it
        arrow = _base(ident, "arrow", centre[a], top, max(abs(p[0]) for p in points), max(p[1] for p in points),
                      (title, "message", ident), style="dashed" if dashed else "solid")
        arrow.update({"roundness": None, "points": points, "lastCommittedPoint": None,
                      "startBinding": None, "endBinding": None,
                      "startArrowhead": None, "endArrowhead": "arrow", "elbowed": False})
        drawn.append(arrow)
        if body:
            mid = centre[a] + LOOP + 8 if a == b else (centre[a] + centre[b]) // 2 - tw // 2
            drawn.append(_text(ident + "-t", mid, y if a != b else top, body, label, (title, "label", ident)))
        y = top + (max(ROW // 2, th) if a == b else 0) + ROW // 2
    for n in people:
        top = rects[n]["y"] + rects[n]["height"]
        line = _base(ids[n] + "-l", "line", centre[n], top, 0, y - top, (title, "lifeline", n), style="dashed",
                     stroke_width=1)
        line.update({"groupIds": [ids[n] + "-g"], "roundness": None, "points": [[0, 0], [0, y - top]],
                     "lastCommittedPoint": None, "startBinding": None, "endBinding": None, "startArrowhead": None,
                     "endArrowhead": None})
        out.append(line)
    return out + drawn


def document(value):
    emit = {"er": er_elements, "sequence": sequence_elements}.get(value.get("kind"), elements)
    return {"type": "excalidraw", "version": 2, "source": "evermap", "elements": emit(value),
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}


def dumps(value):
    """The file's text: sorted keys, two-space indent, LF endings."""
    return json.dumps(document(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
