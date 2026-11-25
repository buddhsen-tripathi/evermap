"""Enumerates every reference an element makes to other elements."""

from .model import base_id

ANY = None
# Relation type -> (allowed source kinds, allowed target kinds)
RELATIONS = {
    "depends_on": (("component",), ("component",)),
    "calls": (("operation",), ("operation",)),
    "reads": (("operation",), ("entity",)),
    "writes": (("operation",), ("entity",)),
    "deletes": (("operation",), ("entity",)),
    "publishes": (("operation",), ("channel",)),
    "consumes": (("operation",), ("channel",)),
    "applies": (("operation",), ("rule",)),
    "uses_role": (("role",), ("role",)),
}


def _dicts(value):
    return [(i, v) for i, v in enumerate(value) if isinstance(v, dict)] if isinstance(value, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


def iter_refs(element):
    """Yield (json path, reference, allowed kinds or None) for each stored reference."""
    def one(where, value, kinds=ANY):
        if isinstance(value, str):
            yield where, value, kinds

    def many(where, values, kinds=ANY):
        if isinstance(values, list):
            for index, value in enumerate(values):
                for found in one("%s[%d]" % (where, index), value, kinds):
                    yield found

    found = []
    add = found.extend
    kind = element.get("kind")
    add(one("$.parent", element.get("parent"), ("component",)))
    for i, rel in _dicts(element.get("relations")):
        where = "$.relations[%d]" % i
        kinds = RELATIONS.get(rel.get("type"), (ANY, ANY))[1] if isinstance(rel.get("type"), str) else ANY
        add(one(where + ".target", rel.get("target"), kinds))
        add(one(where + ".message", rel.get("message"), ("entity",)))
    if kind == "operation":
        add(one("$.trigger.channel", _dict(element.get("trigger")).get("channel"), ("channel",)))
        contract = _dict(element.get("contract"))
        for side in ("input", "output"):
            for i, param in _dicts(contract.get(side)):
                add(one("$.contract.%s[%d].maps_to" % (side, i), param.get("maps_to"), ("entity",)))
        for i, auth in _dicts(_dict(element.get("auth")).get("authorization")):
            add(many("$.auth.authorization[%d].roles" % i, auth.get("roles"), ("role",)))
        for i, item in _dicts(element.get("concurrency")):
            add(one("$.concurrency[%d].resource" % i, item.get("resource"), ("entity",)))
    elif kind == "entity":
        for i, field in _dicts(element.get("fields")):
            where = "$.fields[%d]" % i
            for j, origin in _dicts(field.get("origins")):
                add(many("%s.origins[%d].from" % (where, j), origin.get("from"), ("entity",)))
                add(one("%s.origins[%d].via" % (where, j), origin.get("via"), ("operation",)))
            for j, move in _dicts(_dict(field.get("states")).get("transitions")):
                add(one("%s.states.transitions[%d].via" % (where, j), move.get("via"), ("operation",)))
                add(one("%s.states.transitions[%d].guard" % (where, j), move.get("guard"), ("rule",)))
    elif kind == "channel":
        add(one("$.message", element.get("message"), ("entity",)))
    elif kind == "workflow":
        add(many("$.actors", element.get("actors"), ("role",)))
        add(many("$.data_collected", element.get("data_collected"), ("entity",)))
        for i, step in _dicts(element.get("steps")):
            add(one("$.steps[%d].operation" % i, step.get("operation"), ("operation",)))
            add(many("$.steps[%d].rules" % i, step.get("rules"), ("rule",)))
    elif kind == "invariant":
        add(many("$.scope", element.get("scope")))
        for i, item in _dicts(element.get("enforcement")):
            add(one("$.enforcement[%d].where" % i, item.get("where"), ("operation", "entity")))
    elif kind == "decision":
        add(many("$.affects", element.get("affects")))
        add(many("$.supersedes", element.get("supersedes"), ("decision",)))
        add(many("$.superseded_by", element.get("superseded_by"), ("decision",)))
    return found


def sub_elements(element):
    """Names that may follow '#' in a reference to this element."""
    kind, names = element.get("kind"), set()
    if kind == "entity":
        names = {f.get("name") for _, f in _dicts(element.get("fields"))}
    elif kind == "workflow":
        names = {s.get("id") for _, s in _dicts(element.get("steps"))}
    elif kind == "operation":
        names = {t.get("id") for _, t in _dicts(element.get("transactions"))}
    return {n for n in names if isinstance(n, str)}


def resolves(reference, elements):
    """None when the reference is live, otherwise why it is not."""
    target = elements.get(base_id(reference))
    if target is None:
        return "unknown element %r" % base_id(reference)
    if "#" in reference:
        part = reference.split("#", 1)[1]
        if part not in sub_elements(target):
            return "%r has no part %r" % (base_id(reference), part)
    return None


def field_refs_in_relation(relation):
    target = relation.get("target")
    fields = relation.get("fields")
    if not isinstance(target, str) or not isinstance(fields, list):
        return []
    return ["%s#%s" % (target, f) for f in fields if isinstance(f, str)]
