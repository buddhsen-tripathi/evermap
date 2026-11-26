"""Graph queries over the model: neighbours, references, lineage, impact, search."""

import re

from . import evidence as E
from . import model as M
from . import refs as R


def _list(value):
    return value if isinstance(value, list) else []


def _dicts(value):
    return [v for v in _list(value) if isinstance(v, dict)]


class Graph:
    """Typed edges between elements. Derived edges are computed, never stored."""

    def __init__(self, model):
        self.model = model
        edges = set()
        for ident in sorted(model.elements):
            element = model.elements[ident]
            for rel in _dicts(element.get("relations")):
                if isinstance(rel.get("target"), str) and isinstance(rel.get("type"), str):
                    edges.add((ident, rel["type"], rel["target"]))
            for where, reference, _ in R.iter_refs(element):
                if where.startswith("$.relations["):
                    continue
                edges.add((ident, self._edge_type(element, where), M.base_id(reference)))
            if element.get("kind") == "invariant":
                for item in _dicts(element.get("enforcement")):
                    where = item.get("where")
                    if isinstance(where, str) and item.get("mechanism") != "none-detected":
                        edges.add((M.base_id(where), "enforces", ident))
        self.edges = sorted(e for e in edges if e[0] != e[2])
        self.out, self.into = {}, {}
        for source, kind, target in self.edges:
            self.out.setdefault(source, []).append((kind, target))
            self.into.setdefault(target, []).append((kind, source))

    @staticmethod
    def _edge_type(element, where):
        if where == "$.parent":
            return "child_of"
        if element.get("kind") == "decision":
            return "affects" if where.startswith("$.affects") else where[2:].split("[")[0]
        if where.startswith("$.fields"):
            return "lineage"
        if where.startswith("$.auth"):
            return "authorizes"
        if where.startswith("$.trigger"):
            return "triggered_by"
        if where.startswith("$.steps"):
            return "has_step"
        if where.startswith("$.scope"):
            return "scopes"
        if where.startswith("$.enforcement"):
            return "enforced_at"
        if where.startswith("$.constraints"):
            return "references"
        return "refers_to"

    def neighbors(self, idents, depth=1):
        """{id: {distance, via: [...]}} for elements within depth hops, either direction."""
        found, frontier = {i: 0 for i in idents}, list(idents)
        via = {}
        for distance in range(1, depth + 1):
            following = []
            for current in frontier:
                steps = [(k, t, "out") for k, t in self.out.get(current, [])]
                steps += [(k, s, "in") for k, s in self.into.get(current, [])]
                for kind, other, direction in steps:
                    if other not in self.model.elements:
                        continue
                    if other not in found:
                        found[other] = distance
                        following.append(other)
                    if found[other] == distance:
                        via.setdefault(other, set()).add((current, kind, direction))
            frontier = sorted(following)
        return [dict(describe(self.model, i), distance=found[i],
                     via=[{"from": f, "type": k, "direction": d} for f, k, d in sorted(via[i])])
                for i in sorted(found, key=lambda x: (found[x], x)) if found[i]]

    def references_to(self, ident):
        """Every stored reference to ident, with its location."""
        result = []
        for source in sorted(self.model.elements):
            for where, reference, _ in R.iter_refs(self.model.elements[source]):
                if reference == ident or M.base_id(reference) == ident:
                    result.append({"id": source, "at": where, "reference": reference})
        return result


def describe(model, ident):
    element = model.elements.get(ident, {})
    result = {"id": ident, "name": element.get("name") or element.get("title") or ""}
    if element.get("provenance") and element["provenance"] != "observed":
        result["provenance"] = element["provenance"]
    return result


# --- lineage --------------------------------------------------------------

def lineage_edges(model):
    """[(from field, to field, via operation, transform)]"""
    edges = []
    for entity in model.of_kind("entity"):
        for field in _dicts(entity.get("fields")):
            target = "%s#%s" % (entity["id"], field.get("name"))
            for origin in _dicts(field.get("origins")):
                for source in _list(origin.get("from")):
                    if isinstance(source, str):
                        edges.append((source, target, origin.get("via") or "", origin.get("transform") or ""))
    return sorted(set(edges))


def lineage(model, references, direction="both", depth=10):
    edges = lineage_edges(model)

    def matches(end, reference):
        return end == reference or ("#" not in reference and M.base_id(end) == reference)

    def trace(forward):
        found, frontier, seen = [], list(references), set()
        for distance in range(1, depth + 1):
            following = []
            for source, target, via, transform in edges:
                near, far = (source, target) if forward else (target, source)
                if any(matches(near, r) for r in frontier) and (source, target, via) not in seen:
                    seen.add((source, target, via))
                    edge = {"from": source, "to": target, "distance": distance}
                    if via:
                        edge["via"] = via
                    if transform:
                        edge["transform"] = transform
                    found.append(edge)
                    following.append(far)
            frontier = sorted(set(following))
            if not frontier:
                break
        return found

    result = {}
    if direction in ("up", "both"):
        result["upstream"] = trace(False)
    if direction in ("down", "both"):
        result["downstream"] = trace(True)
    return result


# --- shared resources -----------------------------------------------------

def access(model):
    """{resource ref: {"writers": set, "readers": set}} for entities and named fields."""
    table = {}
    for op in model.of_kind("operation"):
        for rel in _dicts(op.get("relations")):
            kind = rel.get("type")
            if kind not in ("reads", "writes", "deletes") or not isinstance(rel.get("target"), str):
                continue
            role = "readers" if kind == "reads" else "writers"
            for resource in [rel["target"]] + R.field_refs_in_relation(rel):
                table.setdefault(resource, {"writers": set(), "readers": set()})[role].add(op["id"])
    return table


def shared_resources(model, references=None):
    table, result = access(model), []
    for resource in sorted(table):
        if references and resource not in references and M.base_id(resource) not in references:
            continue
        writers = sorted(table[resource]["writers"])
        if len(writers) >= 2:
            result.append({"resource": resource, "writers": writers,
                           "readers": sorted(table[resource]["readers"] - set(writers))})
    return result


# --- impact ---------------------------------------------------------------

def _dependents(model, graph):
    """{id: [(dependent id, reason)]}: who is affected when id changes."""
    table = {}

    def add(changed, affected, reason):
        if changed != affected:
            table.setdefault(changed, set()).add((affected, reason))

    for source, kind, target in graph.edges:
        if kind in ("calls", "depends_on", "applies", "reads", "authorizes", "uses_role"):
            add(target, source, kind)
        elif kind in ("writes", "deletes", "publishes"):
            add(source, target, kind)
            add(target, source, "%s (writer)" % kind)
        elif kind in ("consumes", "triggered_by"):
            add(target, source, kind)
        elif kind == "lineage" and target in model.elements:
            add(target, source, kind)
        elif kind == "child_of":
            add(source, target, "contains")
    return {k: sorted(v) for k, v in table.items()}


def impact(model, references, depth=2):
    """Deterministic impact set. It reports what the model knows, not all effects."""
    graph = Graph(model)
    seeds = sorted({M.base_id(r) for r in references})
    expanded = set(seeds)
    for ident in seeds:
        if M.kind_of(ident) == "component":
            expanded |= {s for k, s in graph.into.get(ident, []) if k == "child_of"}
    table = _dependents(model, graph)
    found, reasons, frontier = {i: 0 for i in expanded}, {}, sorted(expanded)
    for distance in range(1, depth + 1):
        following = []
        for current in frontier:
            for other, reason in table.get(current, []):
                if other not in model.elements:
                    continue
                if other not in found:
                    found[other] = distance
                    following.append(other)
                if found[other] == distance:
                    reasons.setdefault(other, set()).add((current, reason))
        # Containment is context: do not spread from a component to everything in it.
        frontier = sorted(i for i in following if M.kind_of(i) != "component")
    everything = set(found)

    def listed(distance_test):
        return [dict(describe(model, i), distance=found[i],
                     because=[{"of": o, "reason": r} for o, r in sorted(reasons.get(i, ()))])
                for i in sorted(found, key=lambda x: (found[x], x)) if distance_test(found[i])]

    table_access = access(model)
    touched = {r for r in table_access
               if M.base_id(r) in everything and (
                   M.base_id(r) in expanded
                   or (table_access[r]["writers"] | table_access[r]["readers"]) & expanded)}
    channels = []
    for ident in sorted(i for i in everything if M.kind_of(i) == "channel"):
        channels.append(dict(
            describe(model, ident),
            producers=sorted({s for k, s in graph.into.get(ident, []) if k == "publishes"}),
            consumers=sorted({s for k, s in graph.into.get(ident, []) if k in ("consumes", "triggered_by")})))

    def related(kinds, kind):
        return [describe(model, i) for i in sorted(
            {s for e in everything for k, s in graph.into.get(e, []) if k in kinds and M.kind_of(s) == kind}
            | {i for i in everything if M.kind_of(i) == kind})]

    invariants = related(("scopes", "enforced_at"), "invariant")
    tests = {}
    for ident in sorted(everything | {i["id"] for i in invariants}):
        for _, item in E.walk(model.elements.get(ident, {})):
            if item.get("type") == "test":
                tests.setdefault("%s:%s" % (item.get("path"), "-".join(map(str, item.get("lines", [])))), set()).add(ident)
    coverage = model.manifest.get("coverage") or {}
    pending = [i for i in _list(coverage.get("pending")) if i in everything
               or any(model.elements.get(e, {}).get("parent") == i for e in everything)]
    unknown = sorted(i for i in everything if model.elements.get(i, {}).get("provenance") == "unknown")
    return {
        "seeds": [describe(model, i) for i in sorted(expanded)],
        "direct": listed(lambda d: d == 1),
        "indirect": listed(lambda d: d > 1),
        "shared_resources": [s for s in shared_resources(model) if s["resource"] in touched],
        "channels": channels,
        "workflows": related(("has_step", "refers_to"), "workflow"),
        "invariants": invariants,
        "decisions": related(("affects",), "decision"),
        "tests": [{"test": t, "elements": sorted(e)} for t, e in sorted(tests.items())],
        "lineage_downstream": lineage(model, references + [
            i for i in seeds if M.kind_of(i) == "entity"], "down")["downstream"],
        "coverage_gaps": {"unmapped": _list(coverage.get("unmapped")), "pending": pending, "unknown": unknown},
        "statement": "This lists what the model records. Not found does not mean not affected.",
    }


# --- search ---------------------------------------------------------------

WORD = re.compile(r"[a-z0-9]+")
WEIGHTS = {"id": 4, "name": 4, "title": 4, "aliases": 3, "tags": 3, "statement": 2, "plain": 2, "summary": 2,
           "fields": 2}


def _words(value):
    if isinstance(value, list):
        value = " ".join(v for v in value if isinstance(v, str))
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value if isinstance(value, str) else "")
    return WORD.findall(spaced.lower())


def search(model, query, kinds=None):
    terms = sorted(set(_words(query)))
    results = []
    for ident in sorted(model.elements):
        element = model.elements[ident]
        if kinds and M.kind_of(ident) not in kinds:
            continue
        text = {k: _words(element.get(k)) for k in WEIGHTS if k != "fields"}
        text["fields"] = _words([str(f.get("name")) for f in _dicts(element.get("fields"))])
        if M.kind_of(ident) == "decision":
            parts = [element.get("problem"), element.get("decision")] + _list(element.get("drivers"))
            parts += _list(element.get("options")) + _list(element.get("consequences"))
            text["summary"] = _words([str(p.get("text") or p.get("name") or "") for p in parts if isinstance(p, dict)]
                                     + [str((o.get("reasons_rejected") or {}).get("text") or "")
                                        for o in _dicts(element.get("options"))])
        score, matched = 0, set()
        for term in terms:
            best = 0
            for key, words in text.items():
                if term in words:
                    best = max(best, 2 * WEIGHTS[key])
                elif len(term) >= 4 and any(w.startswith(term) or (len(w) >= 4 and term.startswith(w)) for w in words):
                    best = max(best, WEIGHTS[key])
            if best:
                matched.add(term)
                score += best
        if matched:
            results.append(dict(describe(model, ident), score=score * len(matched), matched=sorted(matched),
                                summary=element.get("summary") or (element.get("decision") or {}).get("text", "")))
    return sorted(results, key=lambda r: (-r["score"], r["id"]))
