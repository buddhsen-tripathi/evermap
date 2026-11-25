"""Validation rules V1 to V15 (see docs/MODEL.md section 9)."""

import re

from . import evidence as E
from . import model as M
from . import refs as R
from . import render, secrets
from .jsonschema import validate as check_schema

PLAIN_REQUIRED = {"component", "operation", "workflow", "rule", "invariant", "role"}
PLAIN_LIMIT = 600
NOTE_LIMIT = 200
CODE_WORD = re.compile(
    r"\b(?:[a-z]{2,}[0-9]*[A-Z]\w*|[A-Za-z0-9]+_\w+|[A-Za-z_]\w*\(\))")
DRAFT = M.DIR + "/draft.json"  # written by a worker, consumed by apply; never an element
PLAN = M.DIR + "/plan.json"  # written by estimate --write-plan, read by packet, deleted by sync; never an element
CONFLICT = re.compile(rb"^(?:<{7}|>{7}|\|{7})(?: |\r?$)", re.M)


class Report:
    def __init__(self):
        self.findings = []

    def add(self, rule, severity, path, message, at=None, ident=None):
        item = {"rule": rule, "severity": severity, "path": path}
        if ident:
            item["id"] = ident
        if at and at != "$":
            item["at"] = at
        item["message"] = message
        self.findings.append(item)

    def error(self, rule, path, message, at=None, ident=None):
        self.add(rule, "error", path, message, at, ident)

    def warn(self, rule, path, message, at=None, ident=None):
        self.add(rule, "warning", path, message, at, ident)


def _walk_dicts(value, path="$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            for found in _walk_dicts(child, "%s.%s" % (path, key)):
                yield found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for found in _walk_dicts(child, "%s[%d]" % (path, index)):
                yield found


def _list(value):
    return value if isinstance(value, list) else []


def _evidence_list(value):
    return [e for e in _list(value.get("evidence")) if isinstance(e, dict)]


# --- V1 -------------------------------------------------------------------

def _v1_evidence(report, path, where, item, ident):
    kind = item.get("type")
    allowed = {"type", "note"}
    if kind in E.FILE_TYPES:
        allowed |= {"path", "symbol", "lines", "hash"}
        if "path" not in item:
            report.error("V1", path, "%s evidence needs a path" % kind, where, ident)
        elif not E.valid_path(item["path"]):
            report.error("V1", path, "evidence path must be repo-relative and POSIX", where, ident)
        if "lines" not in item:
            report.error("V1", path, "%s evidence needs lines" % kind, where, ident)
        elif not E.valid_range(item):
            report.error("V1", path, "lines must be [start, end] with 1 <= start <= end", where, ident)
    elif kind == "commit":
        allowed |= {"commit"}
        if "commit" not in item:
            report.error("V1", path, "commit evidence needs a full commit SHA", where, ident)
    elif kind == "stated":
        allowed |= {"who", "date", "source"}
        for name in ("who", "date", "source"):
            if name not in item:
                report.error("V1", path, "stated evidence needs %r" % name, where, ident)
    for name in sorted(set(item) - allowed):
        report.error("V1", path, "%r is not allowed on %s evidence" % (name, kind), where, ident)
    note = item.get("note")
    if isinstance(note, str) and len(note) > NOTE_LIMIT:
        report.error("V1", path, "note is longer than %d characters" % NOTE_LIMIT, where, ident)


def _v1(report, model):
    for path, message in model.errors:
        report.error("V1", path, message)
    for path, value in sorted(model.parsed.items()):
        name = M.schema_for_path(path)
        if path == DRAFT:
            report.warn("V1", path, "leftover draft: run apply to write it, or delete it")
            continue
        if path == PLAN:
            continue  # status reports it while it exists; sync deletes it. V9 still scans it.
        if name is None:
            report.error("V1", path, "unexpected file: not a manifest, element, decision or proposal path")
            continue
        schema = M.REGISTRY.load(name + ".schema.json")
        ident = value.get("id") if isinstance(value, dict) else None
        ident = ident if isinstance(ident, str) else None
        for where, message in check_schema(value, schema, M.REGISTRY):
            report.error("V1", path, message, where, ident)
        for where, item in E.walk(value):
            _v1_evidence(report, path, where, item, ident)
    for path in sorted(model.raw):
        if path.startswith((M.DIR + "/model/", M.DIR + "/decisions/")) and not path.endswith(".json"):
            report.error("V1", path, "unexpected file: only JSON elements belong here")
    manifest = model.manifest
    version = manifest.get("schema_version")
    if isinstance(version, str) and version.split(".")[0] != M.SCHEMA_VERSION.split(".")[0]:
        report.error("V1", M.MANIFEST, "schema_version %s needs a migration to %s" % (version, M.SCHEMA_VERSION))
    if M.MANIFEST not in model.raw:
        report.error("V1", M.MANIFEST, "the manifest is missing")


# --- V2 -------------------------------------------------------------------

def _v2(report, model, elements, paths, expected_path):
    aliases = {}
    for ident, element in sorted(elements.items()):
        path = paths[ident]
        if not M.ID_RE.match(ident):
            report.error("V2", path, "id %r does not match kind:slug" % ident, "$.id")
            continue
        if element.get("kind") != M.kind_of(ident):
            report.error("V2", path, "kind does not match the id prefix", "$.kind", ident)
        wanted = expected_path(ident)
        if path != wanted:
            report.error("V2", path, "file must be %s" % wanted, "$.id", ident)
        for alias in _list(element.get("aliases")):
            if not isinstance(alias, str):
                continue
            if alias in model.elements:
                report.error("V2", path, "alias %r is a live id" % alias, "$.aliases", ident)
            elif alias in aliases:
                report.error("V2", path, "alias %r is also used by %s" % (alias, aliases[alias]), "$.aliases", ident)
            elif M.ID_RE.match(alias) and M.kind_of(alias) != M.kind_of(ident):
                report.error("V2", path, "alias %r has a different kind" % alias, "$.aliases", ident)
            aliases.setdefault(alias, ident)


def _v2_duplicates(report, model):
    for ident, path in model.duplicates:
        report.error("V2", path, "id %r is already defined in %s" % (ident, model.paths[ident]), "$.id", ident)


def _duplicate_names(report, path, ident, element):
    groups = [("fields", "name"), ("transactions", "id"), ("steps", "id"), ("outcomes", "id")]
    for group, key in groups:
        seen = set()
        for index, item in enumerate(_list(element.get(group))):
            name = item.get(key) if isinstance(item, dict) else None
            if not isinstance(name, str):
                continue
            if name in seen:
                report.error("V2", path, "%s %r is defined twice" % (key, name), "$.%s[%d]" % (group, index), ident)
            seen.add(name)


# --- V3 and V12 -----------------------------------------------------------

def _v3(report, elements, scope, paths):
    alias_of = {a: i for i, e in elements.items() for a in _list(e.get("aliases")) if isinstance(a, str)}
    for ident in sorted(scope):
        element, path = elements[ident], paths[ident]
        kind = element.get("kind")
        for where, reference, kinds in R.iter_refs(element):
            problem = R.resolves(reference, elements)
            if problem and kind == "decision" and where.startswith("$.affects"):
                if M.base_id(reference) not in alias_of:
                    report.warn("V12", path, "historical reference: %s" % problem, where, ident)
                continue
            if problem:
                moved = alias_of.get(M.base_id(reference))
                hint = " (now %s)" % moved if moved else ""
                report.error("V3", path, problem + hint, where, ident)
            elif kinds and M.kind_of(reference) not in kinds:
                report.error("V3", path, "%r must refer to: %s" % (reference, ", ".join(kinds)), where, ident)
        if kind in ("operation",) and M.kind_of(str(element.get("parent", ""))) != "component":
            report.error("V3", path, "an operation needs a component as parent", "$.parent", ident)
        for index, rel in enumerate(_list(element.get("relations"))):
            if not isinstance(rel, dict):
                continue
            where = "$.relations[%d]" % index
            sources = R.RELATIONS.get(rel.get("type"), (None, None))[0] if isinstance(rel.get("type"), str) else None
            if sources and kind not in sources:
                report.error("V3", path, "%s cannot start at a %s" % (rel["type"], kind), where, ident)
            tx = rel.get("in_transaction")
            if tx is not None and tx not in R.sub_elements(dict(element, kind="operation")):
                report.error("V3", path, "unknown transaction %r" % tx, where + ".in_transaction", ident)
            target = elements.get(rel.get("target")) if isinstance(rel.get("target"), str) else None
            if target and target.get("kind") == "entity":
                for field in R.field_refs_in_relation(rel):
                    problem = R.resolves(field, elements)
                    if problem:
                        report.error("V3", path, problem, where + ".fields", ident)
        for index, item in enumerate(_list(element.get("constraints"))):
            for name in _list(item.get("fields")) if isinstance(item, dict) else []:
                if kind == "entity" and name not in R.sub_elements(element):
                    report.error("V3", path, "unknown field %r" % name, "$.constraints[%d].fields" % index, ident)
        for index, field in enumerate(_list(element.get("fields"))):
            states = field.get("states") if isinstance(field, dict) else None
            if isinstance(states, dict):
                values = _list(states.get("values"))
                for j, move in enumerate(_list(states.get("transitions"))):
                    for end in ("from", "to"):
                        if isinstance(move, dict) and move.get(end) not in values:
                            report.error("V3", path, "unknown state %r" % move.get(end),
                                         "$.fields[%d].states.transitions[%d].%s" % (index, j, end), ident)
        seen, current = set(), ident
        while isinstance(current, str) and current in elements:
            if current in seen:
                report.error("V3", path, "parent chain is circular", "$.parent", ident)
                break
            seen.add(current)
            current = elements[current].get("parent")


# --- V4 and V5 ------------------------------------------------------------

def _v4(report, path, ident, element):
    for where, node in _walk_dicts(element):
        provenance = node.get("provenance")
        items = _evidence_list(node)
        if element.get("kind") != "decision":
            if provenance == "observed" and not any(e.get("type") != "stated" for e in items):
                report.error("V4", path, "observed claims need evidence from the repository", where, ident)
            if provenance == "inferred" and not str(node.get("rationale") or "").strip():
                report.error("V4", path, "inferred claims need a rationale", where, ident)
            if provenance == "stated" and not any(e.get("type") == "stated" for e in items):
                report.error("V4", path, "stated claims need stated evidence", where, ident)
        if node.get("mechanism") == "none-detected" and not items and not node.get("rationale"):
            report.error("V4", path, "none-detected needs evidence or a rationale saying where you looked", where, ident)


def _v5(report, path, ident, element, overlay):
    if element.get("kind") == "decision":
        return
    proposed = element.get("lifecycle") == "proposed"
    if proposed and not overlay:
        report.error("V5", path, "proposed elements belong in a proposal overlay", "$.lifecycle", ident)
    if overlay and not proposed:
        report.error("V5", path, "overlay elements must have lifecycle proposed", "$.lifecycle", ident)


# --- V6 and V7 ------------------------------------------------------------

def _v6_v7(report, root, model, strict):
    checker = E.checker_for(root, model)
    for ident in sorted(model.elements):
        path = model.paths[ident]
        for where, item in E.walk(model.elements[ident]):
            if not E.is_file_evidence(item) or not E.valid_path(item.get("path")) or not E.valid_range(item):
                continue
            lines = checker.lines(item["path"])
            if not item.get("hash"):
                report.error("V6", path, "hash is missing: run anchor", where, ident)
            if lines is None:
                report.error("V6", path, "file not found: %s" % item["path"], where, ident)
            elif not E.valid_range(item, len(lines)):
                report.error("V6", path, "lines %s are out of range: %s has %d lines"
                             % (item["lines"], item["path"], len(lines)), where, ident)
            if not item.get("hash"):
                continue
            result = checker.classify(item)
            if result["state"] in ("stale", "missing"):
                report.add("V7", "error" if strict else "warning", path,
                           "%s: %s" % (result["state"], result["reason"]), where, ident)
            elif result["state"] == "moved":
                report.warn("V7", path, "moved to %s %s: run check-evidence --fix-moved"
                            % (result["path"], result["lines"]), where, ident)


# --- V8 -------------------------------------------------------------------

def _v8(report, path, ident, element):
    steps = [s for s in _list(element.get("steps")) if isinstance(s, dict)]
    step_ids = {s.get("id") for s in steps}
    outcome_ids = {o.get("id") for o in _list(element.get("outcomes")) if isinstance(o, dict)}
    if not steps:
        report.error("V8", path, "a workflow needs at least one step", "$.steps", ident)

    def check(where, value, known, label):
        if value is not None and value not in known:
            report.error("V8", path, "unknown %s %r" % (label, value), where, ident)

    for index, step in enumerate(_list(element.get("steps"))):
        if not isinstance(step, dict):
            continue
        where = "$.steps[%d]" % index
        if not step.get("operation") and not step.get("rules") and not _evidence_list(step):
            report.error("V8", path, "step needs an operation, a rule or evidence", where, ident)
        for i, target in enumerate(_list(step.get("next"))):
            check("%s.next[%d]" % (where, i), target, step_ids, "step")
        check(where + ".outcome", step.get("outcome"), outcome_ids, "outcome")
        for group in ("branches", "on_failure"):
            for i, branch in enumerate(_list(step.get(group))):
                if isinstance(branch, dict):
                    spot = "%s.%s[%d]" % (where, group, i)
                    check(spot + ".next", branch.get("next"), step_ids, "step")
                    check(spot + ".outcome", branch.get("outcome"), outcome_ids, "outcome")
                    if "next" not in branch and "outcome" not in branch:
                        report.error("V8", path, "branch needs a next step or an outcome", spot, ident)


# --- V9, V10, V11 ---------------------------------------------------------

def _v9(report, model):
    for path in sorted(model.raw):
        if path in model.parsed:
            # The plan lists repository file paths: an odd file name must not block validation,
            # so only the named credential patterns apply there.
            for where, finding in secrets.scan_value(model.parsed[path], entropy=path != PLAN):
                report.error("V9", path, "possible secret: %s" % finding, where)
        else:
            text = model.raw[path].decode("utf-8", "replace")
            for number, line in enumerate(text.splitlines(), 1):
                for finding in secrets.scan_text(line):
                    report.error("V9", path, "possible secret: %s" % finding, "line %d" % number)


def _v10(report, path, ident, element):
    if element.get("kind") in PLAIN_REQUIRED and not str(element.get("plain") or "").strip():
        report.warn("V10", path, "plain text is missing", "$.plain", ident)
    allowed = str(element.get("name") or element.get("title") or "")
    for where, node in _walk_dicts(element):
        for key, text in node.items():
            if not (key == "plain" or key.endswith("_plain")) or not isinstance(text, str):
                continue
            spot = "%s.%s" % (where, key)
            if len(text) > PLAIN_LIMIT:
                report.warn("V10", path, "plain text is longer than %d characters" % PLAIN_LIMIT, spot, ident)
            words = sorted({w for w in CODE_WORD.findall(text) if w not in allowed})
            if words:
                report.warn("V10", path, "plain text contains code identifiers: %s" % ", ".join(words), spot, ident)


def _v11(report, path, ident, element):
    decision = element.get("decision") if isinstance(element.get("decision"), dict) else {}
    if element.get("status") != "proposed" and decision.get("provenance") != "confirmed":
        report.error("V11", path, "status %r needs decision.provenance confirmed" % element.get("status"),
                     "$.status", ident)
    confirmed = any(node.get("provenance") == "confirmed" for _, node in _walk_dicts(element))
    if confirmed and not any(e.get("type") in ("stated", "doc") for e in _evidence_list(element)):
        report.error("V11", path, "confirmed sections need stated evidence (or the imported ADR as doc evidence)",
                     "$.evidence", ident)


# --- V13, V14, V15 --------------------------------------------------------

def unformatted(model):
    """Paths whose text is not canonical."""
    result = []
    for path, value in sorted(model.parsed.items()):
        name = M.schema_for_path(path)
        if name and not M.same_text(model.raw[path], M.format_value(value, name)):
            result.append(path)
    return result


def _v14(report, model):
    for path in sorted(model.raw):
        if CONFLICT.search(model.raw[path]):
            report.error("V14", path, "Git conflict markers")


def _v15(report, model):
    if not any(p.startswith(M.DIR + "/views/") for p in model.raw):
        report.warn("V15", M.DIR + "/views", "views have not been rendered")
        return
    problems = render.out_of_sync(model)
    for path in problems:
        report.warn("V15", path, "view is out of sync with the model: run render")


# --- entry point ----------------------------------------------------------

def run(root, model, strict=False):
    report = Report()
    _v1(report, model)
    _v2_duplicates(report, model)
    _v2(report, model, model.elements, model.paths, M.path_for_id)
    _v3(report, model.elements, model.elements, model.paths)
    for ident in sorted(model.elements):
        _element(report, model.paths[ident], ident, model.elements[ident], overlay=False)
    for ident in _list((model.manifest.get("coverage") or {}).get("pending")
                       if isinstance(model.manifest.get("coverage"), dict) else None):
        if isinstance(ident, str) and ident not in model.elements:
            report.error("V3", M.MANIFEST, "unknown element %r" % ident, "$.coverage.pending")
    system = model.manifest.get("system")
    for name in ("summary", "plain"):
        if isinstance(system, dict) and not str(system.get(name) or "").strip():
            report.warn("V10", M.MANIFEST, "system %s is missing" % name, "$.system." + name)
    _v6_v7(report, root, model, strict)
    _v9(report, model)
    for path in unformatted(model):
        report.add("V13", "error" if strict else "warning", path, "not canonically formatted: run fmt")
    _v14(report, model)
    _v15(report, model)
    for slug in sorted(model.overlays):
        _overlay(report, model, slug)
    report.findings.sort(key=lambda f: (f["severity"] != "error", int(f["rule"][1:]), f["path"],
                                        f.get("at", ""), f["message"]))
    return report.findings


def _element(report, path, ident, element, overlay):
    _duplicate_names(report, path, ident, element)
    _v4(report, path, ident, element)
    _v5(report, path, ident, element, overlay)
    _v10(report, path, ident, element)
    if element.get("kind") == "workflow":
        _v8(report, path, ident, element)
    if element.get("kind") == "decision":
        _v11(report, path, ident, element)


def _overlay(report, model, slug):
    merged = model.overlay(slug)
    prefix = "%s/proposals/%s/" % (M.DIR, slug)
    own = {}
    for path, value in model.overlays[slug].items():
        ident = value.get("id") if isinstance(value, dict) else None
        if isinstance(ident, str) and M.schema_for_path(path) not in (None, "proposal"):
            own[ident] = path
    if prefix + "proposal.json" not in model.raw:
        report.error("V1", prefix + "proposal.json", "the proposal file is missing")
    _v2(report, merged, {i: merged.elements[i] for i in own if i in merged.elements}, own,
        lambda i: prefix + M.path_for_id(i)[len(M.DIR) + 1:])
    live = [i for i in own if i in merged.elements]
    _v3(report, merged.elements, live, dict(merged.paths, **own))
    for ident in sorted(live):
        _element(report, own[ident], ident, merged.elements[ident], overlay=True)
