"""Eraser emitter: a diagram value (render.diagram) as a deterministic .eraserdiagram file.

The first line names the Eraser diagram type: "flow-chart" for flowchart and state values (and
the access diagrams, which are flowcharts), "sequence-diagram" or "entity-relationship-diagram";
the rest is that type's code. Flow-chart nodes are named n0, n1 ... in node order, so names are
unique and never a keyword, and every node is defined before the first connection.
"""

import re

from . import render

COLOURS = (("removed", "red"), ("added", "green"), ("modified", "orange"))
_LABEL = re.compile(r"[\[\]{}]")
_EDGE = re.compile(r"[\[\]{}:,]")


def node_label(value):
    # A semicolon becomes a comma (allowed inside the quoted label) so clauses stay apart.
    return _LABEL.sub("", render.plain(value).replace('"', "'").replace(";", ","))


def edge_label(value):
    return " ".join(_EDGE.sub("", render.plain(value).replace('"', "'").replace(";", " ")).split())


_RESERVED = re.compile(r"[\[\]{}:,<>\-.\"'#;/|()]")
KEYWORDS = ("title", "legend", "colormode", "stylemode", "typeface", "textsize", "notation")
KEYS = {"PK": "pk", "FK": "fk", "UK": "unique"}


def name(value, keywords=(), spaces=False):
    """A participant, entity or field name: in double quotes when it holds reserved characters (with
    spaces, also a space) or starts with a keyword (double quotes inside become single ones)."""
    flat = render.plain(value).replace('"', "'")
    if _RESERVED.search(flat) or flat.lower().startswith(keywords) or not flat or spaces and " " in flat:
        return '"%s"' % flat
    return flat


def _unique(labels):
    """Labels made unique with " 2", " 3" ... in order."""
    out, used = [], set()
    for text in labels:
        base, n = render.plain(text) or "unnamed", 1
        while (base if n == 1 else "%s %d" % (base, n)) in used:
            n += 1
        out.append(base if n == 1 else "%s %d" % (base, n))
        used.add(out[-1])
    return out


def sequence(value):
    """Eraser sequence-diagram code: "A > B: label" solid, "A --> B: label" dashed. Notes are left
    out, except a rule-only step, which becomes a self-message naming the rule."""
    people = {p[0]: name(n, KEYWORDS) for p, n in zip(value["participants"],
                                                      _unique(p[1] for p in value["participants"]))}
    out = ["sequence-diagram", "title " + node_label(value["title"])]
    messages = value["messages"]
    for index in range(len(messages) + 1):
        for after, who, _, rule in value["notes"]:
            if after == index and edge_label(rule):
                out.append("%s > %s: %s" % (people[who], people[who], edge_label(rule)))
        if index < len(messages):
            source, target, text, dashed = messages[index]
            out.append("%s %s %s: %s" % (people[source], "-->" if dashed else ">", people[target],
                                         edge_label(text) or "message"))
    return "\n".join(out) + "\n"


def entity_relationship(value):
    """Eraser entity-relationship-diagram code: entities with typed fields and key metadata, then
    "a.field > b.field" (many-to-one) or "a.field - b.field" (one-to-one)."""
    names = {e[0]: name(n, KEYWORDS, True)
             for e, n in zip(value["entities"], _unique(e[1] for e in value["entities"]))}
    out = ["entity-relationship-diagram", "title " + node_label(value["title"]), "notation crows-foot"]
    attributes = {}
    for ident, _, attrs in value["entities"]:
        attributes[ident] = attrs
        out.append(names[ident] + " {")
        for field, kind, keys, _ in attrs:
            kind = render.plain(kind).replace('"', "'") or "unknown"
            kind = '"%s"' % kind if _RESERVED.search(kind) or " " in kind else kind
            meta = " ".join(KEYS[k] for k in keys if k in KEYS)
            out.append("  %s %s%s" % (name(field, KEYWORDS, True), kind, " " + meta if meta else ""))
        out.append("}")
    for source, target, near, _, fields, field in value["relationships"]:
        here = "%s.%s" % (names[source], name(fields[0], KEYWORDS, True)) if fields else names[source]
        keys = [a[0] for a in attributes[target] if "PK" in a[2]]
        field = field or (keys[0] if len(keys) == 1 else None)
        there = "%s.%s" % (names[target], name(field, KEYWORDS, True)) if field else names[target]
        out.append("%s %s %s" % (here, "-" if near == "zero-or-one" else ">", there))
    return "\n".join(out) + "\n"


def dumps(value):
    if value.get("kind") == "sequence":
        return sequence(value)
    if value.get("kind") == "er":
        return entity_relationship(value)
    names, out = {}, ["flow-chart", "title " + node_label(value["title"]),
                      "direction " + ("right" if value["direction"] == "LR" else "down")]
    styles = value["styles"]
    for ident, text in value["nodes"]:
        if ident in names:
            continue
        names[ident] = "n%d" % len(names)
        colour = next((c for s, c in COLOURS if ident in (styles.get(s) or ())), None)
        out.append('%s [label: "%s"%s]' % (names[ident], node_label(text), ", color: " + colour if colour else ""))
    for source, target, text in value["edges"]:
        if source in names and target in names:
            label = edge_label(text or "")
            out.append("%s > %s%s" % (names[source], names[target], ": " + label if label else ""))
    return "\n".join(out) + "\n"
