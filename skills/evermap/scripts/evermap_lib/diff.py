"""Comparison of two models."""

import re

from . import evidence as E
from . import graph as G
from . import model as M
from . import render

KEYS = {
    "relations": ("type", "target", "in_transaction"), "fields": ("name",), "steps": ("id",), "outcomes": ("id",),
    "transactions": ("id",), "input": ("name",), "output": ("name",), "errors": ("condition",),
    "config": ("name",), "parameters": ("name",), "options": ("name",), "concurrency": ("resource",),
    "authorization": ("rule",), "branches": ("condition_plain",), "on_failure": ("plain",),
    "failure_handling": ("on",), "enforcement": ("mechanism", "where"), "constraints": ("type", "detail"),
    "evidence": ("type", "path", "symbol"), "verified_by": ("type", "path", "symbol"),
    "transitions": ("from", "to", "via"), "values": None,
}
ABSENT = object()
ANCHOR_END = re.compile(r"\.(?:lines(?:\[\d+\])?|hash)\Z")
EVIDENCE_PART = re.compile(r"\.(?:evidence|verified_by)\[|\.(?:spec|source)\.")
FUNCTIONAL = {"workflow", "rule"}
RISK_PARTS = {"auth": "auth", "transactions": "transactions", "concurrency": "concurrency",
              "idempotency": "idempotency", "failure_handling": "failure handling", "enforcement": "enforcement",
              "exposure": "exposure"}


def _keyed(name, items):
    """{display key: item} when every item has a usable, unique key, else None."""
    if items and all(isinstance(i, str) for i in items) and len(set(items)) == len(items):
        return {i: i for i in items}
    fields = KEYS.get(name)
    if not fields or not all(isinstance(i, dict) for i in items):
        return None
    table = {}
    for item in items:
        parts = ["%s=%s" % (f, item[f]) for f in fields if f in item]
        key = ",".join(parts)
        if not parts or key in table:
            return None
        table[key] = item
    return table


def compare(old, new, path="$", name=None):
    """Yield (json path, old, new) for each difference; ABSENT marks a missing side."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            for found in compare(old.get(key, ABSENT), new.get(key, ABSENT), "%s.%s" % (path, key), key):
                yield found
    elif isinstance(old, list) and isinstance(new, list):
        left, right = _keyed(name, old), _keyed(name, new)
        if left is None and not old:
            left = {}
        if right is None and not new:
            right = {}
        if left is not None and right is not None:
            for key in sorted(set(left) | set(right)):
                for found in compare(left.get(key, ABSENT), right.get(key, ABSENT), "%s[%s]" % (path, key)):
                    yield found
            common_old = [k for k in left if k in right]
            common_new = [k for k in right if k in left]
            if common_old != common_new and name not in M.SORTED_OBJECTS | M.SORTED_STRINGS:
                yield path + ".order", common_old, common_new
        else:
            for index in range(max(len(old), len(new))):
                a = old[index] if index < len(old) else ABSENT
                b = new[index] if index < len(new) else ABSENT
                for found in compare(a, b, "%s[%d]" % (path, index)):
                    yield found
    elif old is ABSENT or new is ABSENT or M.dumps(old) != M.dumps(new):
        yield path, old, new


def _is_anchor_only(path):
    """True for line and hash changes inside an evidence item."""
    return bool(ANCHOR_END.search(path) and EVIDENCE_PART.search(path))


def _paths(element):
    return sorted({i["path"] for _, i in E.walk(element) if isinstance(i.get("path"), str)})


def _without_paths(element):
    """The element without evidence locations, to tell a rename from a behaviour change."""
    if isinstance(element, dict):
        return {k: _without_paths(v) for k, v in element.items()
                if k not in ("evidence", "verified_by", "name", "aliases", "id")}
    if isinstance(element, list):
        return [_without_paths(v) for v in element]
    return element


def _change(path, old, new):
    item = {"path": path}
    if old is not ABSENT:
        item["from"] = old
    if new is not ABSENT:
        item["to"] = new
    return item


def _relations(element):
    return {(r.get("type"), r.get("target")) for r in element.get("relations") or [] if isinstance(r, dict)}


def diff(old, new):
    """Structured difference between two models."""
    before, after = old.elements, new.elements
    pairs, added, removed = [], [], []
    renamed_ids = {}
    for ident in sorted(set(after) - set(before)):
        for alias in after[ident].get("aliases") or []:
            if alias in before and alias not in after:
                renamed_ids[alias] = ident
    for ident in sorted(set(before) | set(after)):
        if ident in before and ident in after:
            pairs.append((ident, ident))
        elif ident in renamed_ids:
            pairs.append((ident, renamed_ids[ident]))
        elif ident in before:
            removed.append(ident)
        elif ident not in renamed_ids.values():
            added.append(ident)

    modified, reanchored, unchanged, rel_added, rel_removed = [], [], 0, [], []
    functional, risk = [], []
    for a, b in pairs:
        changes = [_change(*c) for c in compare(before[a], after[b])]
        real = [c for c in changes if not _is_anchor_only(c["path"])]
        if not changes:
            unchanged += 1
            continue
        if not real:
            reanchored.append(b)
            continue
        entry = dict(G.describe(new, b), renamed=False, changes=real)
        moved = _paths(before[a]) != _paths(after[b])
        if a != b:
            entry["previous_id"] = a
        if a != b or before[a].get("name") != after[b].get("name") or moved:
            entry["renamed"] = True
            entry["behaviour_changed"] = (
                M.dumps(_without_paths(before[a])) != M.dumps(_without_paths(after[b])))
        modified.append(entry)
        rel_added += [(b,) + r for r in _relations(after[b]) - _relations(before[a])]
        rel_removed += [(b,) + r for r in _relations(before[a]) - _relations(after[b])]
        for change in real:
            part = change["path"][2:].split(".")[0].split("[")[0]
            plain = change["path"].rsplit(".", 1)[-1]
            if M.kind_of(b) in FUNCTIONAL or plain == "plain" or plain.endswith("_plain"):
                functional.append(dict(change, id=b))
            if part in RISK_PARTS or (M.kind_of(b) == "invariant" and part in ("statement", "scope", "criticality")):
                risk.append(dict(change, id=b, area=RISK_PARTS.get(part, "invariant")))
    for ident in added:
        rel_added += [(ident,) + r for r in _relations(after[ident])]
    for ident in removed:
        rel_removed += [(ident,) + r for r in _relations(before[ident])]
    for label, idents, model in (("added", added, new), ("removed", removed, old)):
        for ident in idents:
            if M.kind_of(ident) in FUNCTIONAL:
                functional.append({"id": ident, "path": "$", "change": label})
            if M.kind_of(ident) == "invariant":
                risk.append({"id": ident, "path": "$", "change": label, "area": "invariant"})

    def relations(items):
        return [{"source": s, "type": t, "target": g} for s, t, g in sorted(items, key=str)]

    return {
        "summary": {"added": len(added), "removed": len(removed), "modified": len(modified),
                    "renamed": sum(1 for m in modified if m["renamed"]),
                    "reanchored": len(reanchored), "unchanged": unchanged},
        "added": [G.describe(new, i) for i in added],
        "removed": [G.describe(old, i) for i in removed],
        "modified": modified,
        "reanchored": reanchored,
        "relations_added": relations(rel_added),
        "relations_removed": relations(rel_removed),
        "functional_changes": functional,
        "risk_changes": risk,
    }


def mermaid(old, new, result):
    """Affected subgraph with added, removed and modified elements styled."""
    styles = {"added": {e["id"] for e in result["added"]}, "removed": {e["id"] for e in result["removed"]},
              "modified": {e["id"] for e in result["modified"]}}
    changed = set().union(*styles.values())
    if not changed:
        return []
    edges, near = set(), set()
    for model in (old, new):
        for source, kind, target in G.Graph(model).edges:
            if source in changed or target in changed:
                edges.add((source, target, kind))
                near |= {source, target}
    order = sorted(changed) + sorted(near - changed)
    shown = order[:render.MAX_NODES]

    def title(ident):
        model = new if ident in new.elements else old
        return "%s: %s" % (M.kind_of(ident), G.describe(model, ident)["name"])

    return render.flowchart([(i, title(i)) for i in shown],
                            [e for e in sorted(edges) if e[0] in shown and e[1] in shown], styles)


def _no_hash(value):
    """The value without evidence hashes: Markdown never prints them."""
    if isinstance(value, dict):
        return {k: _no_hash(v) for k, v in value.items() if k != "hash"}
    if isinstance(value, list):
        return [_no_hash(v) for v in value]
    return value


def _value(value):
    if value is ABSENT:
        return "—"
    value = _no_hash(value)
    flat = value if isinstance(value, str) else M.dumps(value)
    return render.text(flat if len(flat) <= 160 else flat[:157] + "...")


def _moved_evidence(changes):
    """One line when every evidence change of an element only moved (same hashes, new path or
    lines), else None. Returns (line or None, evidence changes)."""
    evidence = [c for c in changes if EVIDENCE_PART.search(c["path"])]
    old_hashes, new_hashes, old_paths, new_paths = [], [], set(), set()
    for change in evidence:
        before, after = change.get("from", ABSENT), change.get("to", ABSENT)
        if isinstance(before, dict) or isinstance(after, dict):
            for value, hashes, paths in ((before, old_hashes, old_paths), (after, new_hashes, new_paths)):
                if isinstance(value, dict):
                    if not value.get("hash"):
                        return None, evidence
                    hashes.append(value["hash"])
                    paths.add(str(value.get("path")))
        elif change["path"].endswith(".path"):
            old_hashes.append(change["path"])
            new_hashes.append(change["path"])
            old_paths.add(str(before))
            new_paths.add(str(after))
        else:
            return None, evidence
    if not old_hashes or sorted(old_hashes) != sorted(new_hashes):
        return None, evidence
    count = len(old_hashes)
    return ("evidence moved: %s -> %s, %d item%s" % (
        render.text(", ".join(sorted(old_paths))), render.text(", ".join(sorted(new_paths))),
        count, "" if count == 1 else "s")), evidence


def markdown(result, diagram):
    summary = result["summary"]
    out = ["## Model changes", "",
           "%(added)d added, %(removed)d removed, %(modified)d modified (%(renamed)d renamed or moved), "
           "%(reanchored)d re-anchored, %(unchanged)d unchanged." % summary, ""]
    for title in ("added", "removed"):
        if result[title]:
            out += ["### " + title.capitalize(), ""]
            out += ["- %s %s" % (render.code(e["id"]), render.text(e["name"])) for e in result[title]] + [""]
    if result["modified"]:
        out += ["### Modified", ""]
        for entry in result["modified"]:
            note = ""
            if entry["renamed"]:
                note = " (renamed or moved%s)" % ("" if entry["behaviour_changed"] else ", behaviour unchanged")
            out.append("- %s %s%s" % (render.code(entry["id"]), render.text(entry["name"]), note))
            moved, evidence = _moved_evidence(entry["changes"])
            if moved:
                out.append("  - " + moved)
            for change in entry["changes"]:
                if moved and change in evidence:
                    continue
                out.append("  - %s: %s → %s" % (render.code(change["path"]), _value(change.get("from", ABSENT)),
                                                _value(change.get("to", ABSENT))))
        out.append("")
    for key, title in (("relations_added", "Relations added"), ("relations_removed", "Relations removed")):
        if result[key]:
            out += ["### " + title, ""]
            out += ["- %s %s %s" % (render.code(r["source"]), render.text(r["type"]), render.code(r["target"]))
                    for r in result[key]] + [""]
    if result["functional_changes"]:
        out += ["### Functional changes", ""]
        out += ["- %s %s%s" % (render.code(c["id"]), render.code(c["path"]), " " + c["change"] if "change" in c else "")
                for c in result["functional_changes"]] + [""]
    if result["risk_changes"]:
        out += ["### Risk-relevant changes", ""]
        out += ["- %s: %s %s%s" % (render.text(c["area"]), render.code(c["id"]), render.code(c["path"]),
                                  " " + c["change"] if "change" in c else "")
                for c in result["risk_changes"]] + [""]
    if diagram:
        out += diagram + [""]
    return "\n".join(out).rstrip("\n") + "\n"
