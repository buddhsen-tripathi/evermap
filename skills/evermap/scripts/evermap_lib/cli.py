"""Command line interface. JSON on stdout, diagnostics on stderr."""

import argparse
import json
import os
import sys

from . import diff as D
from . import evidence as E
from . import gitio
from . import graph as G
from . import model as M
from . import render as V
from . import rules
from . import secrets

OK, INVALID, USAGE, NO_MODEL, STALE = 0, 1, 2, 3, 4
MAX_CHARS = 10000
EXIT_CODES = "exit codes: 0 ok, 1 validation errors, 2 usage error, 3 no Git repository or model, 4 stale (--strict)"
# Agent skills installed into the project (.agents/skills/…, .claude/skills/… and their symlinks,
# skills-lock.json) are tooling, not application code. A plain skills/ directory is not excluded.
DEFAULT_EXCLUDE = ["**/node_modules/**", "**/vendor/**", "**/dist/**", "**/build/**", "docs/**",
                   ".*/skills", ".*/skills/**", "skills-lock.json"]
GITATTRIBUTES = "* text eol=lf\nviews/** linguist-generated=true\n"


class UsageError(Exception):
    pass


# --- output ---------------------------------------------------------------

def _encode(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _lists(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            for found in _lists(child, path + "." + key if path else key):
                yield found
    elif isinstance(value, list):
        yield path, value
        for child in value:
            for found in _lists(child, path + "[]"):
                yield found


def bound(payload, limit, encode=_encode):
    """Shorten the largest lists until the encoded payload fits. Says what was cut."""
    if not limit or len(encode(payload)) <= limit:
        return payload
    payload = json.loads(json.dumps(payload))
    totals = {}
    while len(encode(payload)) > limit:
        candidates = [(len(json.dumps(v)), p, v) for p, v in _lists(payload) if p != "truncated" and v]
        if not candidates:
            break
        _, path, items = max(candidates, key=lambda c: (c[0], c[1]))
        totals.setdefault(path, 0)
        drop = max(1, len(items) // 2)
        totals[path] += drop
        del items[len(items) - drop:]
        payload["truncated"] = [{"at": p, "omitted": n} for p, n in sorted(totals.items())]
        payload["more"] = "use --limit/--offset to page, or --max-chars 0 for everything"
    return payload


def page(items, args):
    end = None if not args.limit else args.offset + args.limit
    return items[args.offset:end]


def _generic_markdown(value, depth=0):
    pad, out = "  " * depth, []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                out.append("%s- **%s**%s" % (pad, V.text(key), "" if child else ": none"))
                out += _generic_markdown(child, depth + 1)
            else:
                out.append("%s- **%s**: %s" % (pad, V.text(key), V.text(child)))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, dict):
                parts = ["%s: %s" % (V.text(k), V.text(v if isinstance(v, str) else M.dumps(v)))
                         for k, v in child.items()]
                out.append("%s- %s" % (pad, "; ".join(parts)))
            else:
                out.append("%s- %s" % (pad, V.text(child)))
    return out


def to_markdown(payload):
    return "\n".join(_generic_markdown(payload)) + "\n"


def emit(args, payload, markdown=None):
    if args.format == "md":
        def encode(value):
            text = (markdown or to_markdown)(value)
            if markdown and "truncated" in value:
                text += "\nShortened: " + M.dumps(value["truncated"]) + "\n"
            return text
        text = encode(bound(payload, args.max_chars, encode))
    else:
        text = _encode(bound(payload, args.max_chars))
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def warn(message):
    sys.stderr.write("evermap: %s\n" % message)


# --- helpers --------------------------------------------------------------

def synced_commit(model):
    synced = model.manifest.get("synced")
    commit = synced.get("commit") if isinstance(synced, dict) else None
    if not isinstance(commit, str):
        raise M.ModelError("the manifest has no synced commit")
    return commit


def baseline(root, ref):
    """(commit, warning). Falls back to the merge base when ref is not an ancestor (U7)."""
    commit, head = gitio.resolve(root, ref), gitio.head(root)
    if gitio.is_ancestor(root, commit, head):
        return commit, None
    base = gitio.merge_base(root, commit, head)
    return base, ("%s is not an ancestor of HEAD; using merge base %s, so the update is wider"
                  % (commit[:12], base[:12]))


def need_ids(model, idents, sub=True):
    for ident in idents:
        target = model.get(ident)
        if target is None:
            aliases = [i for i, e in sorted(model.elements.items()) if M.base_id(ident) in (e.get("aliases") or [])]
            raise UsageError("unknown element %r%s" % (ident, " (now %s)" % aliases[0] if aliases else ""))
        if "#" in ident:
            from .refs import resolves
            problem = resolves(ident, model.elements) if sub else "a part is not allowed here"
            if problem:
                raise UsageError(problem)
    return idents


def evidence_index(model):
    """{path: {element id}} for file evidence."""
    table = {}
    for ident, element in model.elements.items():
        for _, item in E.walk(element):
            if E.is_file_evidence(item) and isinstance(item.get("path"), str):
                table.setdefault(item["path"], set()).add(ident)
    return table


def compute_changes(root, model, since):
    base, message = baseline(root, since)
    changes = gitio.changed_files(root, base)
    renames = {c["old_path"]: c["path"] for c in changes if c["status"] == "renamed"}
    checker, index = E.Checker(root, renames), evidence_index(model)
    owners, scoped = M.owners(model), M.in_scope(model.manifest)
    affected, unmapped, outside = {}, [], []
    for change in changes:
        paths = [change["path"]] + ([change["old_path"]] if "old_path" in change else [])
        idents = sorted(set().union(*(index.get(p, set()) for p in paths)))
        # As in coverage, a component owns only in-scope files.
        change["components"] = [i for i, match in owners if any(match(p) and scoped(p) for p in paths)]
        change["elements"] = idents
        for ident in idents:
            states = affected.setdefault(ident, set())
            for _, item in E.walk(model.elements[ident]):
                if E.is_file_evidence(item) and item.get("path") in paths:
                    states.add(checker.classify(item)["state"])
        if not idents and not change["components"]:
            (unmapped if any(scoped(p) for p in paths) else outside).append(change["path"])
    return {
        "baseline": base, "head": gitio.head(root), "warning": message, "files": changes,
        "affected": [dict(G.describe(model, i), evidence=sorted(affected[i])) for i in sorted(affected)],
        "unmapped": unmapped, "out_of_scope": outside,
        "renames": [{"from": old, "to": new} for old, new in sorted(renames.items())],
    }


def check_ref(root, ref):
    try:
        gitio.resolve(root, ref)
    except gitio.GitError as exc:
        raise UsageError(str(exc))


# --- commands -------------------------------------------------------------

def cmd_init(args, root):
    if M.load_worktree(root, require=False).raw:
        raise UsageError("a model already exists in %s/: use update or regenerate" % M.DIR)
    manifest = {
        "schema_version": M.SCHEMA_VERSION,
        "system": {"name": args.name or os.path.basename(root), "summary": args.summary, "plain": args.plain},
        "synced": {"commit": gitio.head(root), "mode": "init"},
        "scope": {"include": args.scope or ["**"],
                  "exclude": DEFAULT_EXCLUDE if args.exclude is None else args.exclude},
        "coverage": {"unmapped": [], "pending": [], "limitations": []},
    }
    if gitio.is_dirty(root):
        manifest["synced"]["dirty"] = True
        warn("the working tree has uncommitted changes outside %s/" % M.DIR)
    problems = rules.check_schema(manifest, M.REGISTRY.load("manifest.schema.json"), M.REGISTRY)
    if problems:
        raise UsageError("; ".join("%s: %s" % p for p in problems))
    M.write(root, M.MANIFEST, M.format_value(manifest, "manifest"))
    M.write(root, M.DIR + "/.gitattributes", GITATTRIBUTES)
    emit(args, {"created": [M.MANIFEST, M.DIR + "/.gitattributes"], "manifest": manifest})
    return OK


def cmd_status(args, root):
    model = M.load_worktree(root)
    result = compute_changes(root, model, synced_commit(model))
    states = {}
    for item in E.check(root, model):
        states[item["state"]] = states.get(item["state"], 0) + 1
    relevant = [c for c in result["files"] if c["elements"] or c["components"] or c["path"] in result["unmapped"]]
    stale = states.get("stale", 0) + states.get("missing", 0)
    current = not relevant and not stale and not states.get("moved") and not states.get("unanchored")
    payload = {
        "up_to_date": current, "synced": model.manifest["synced"], "head": result["head"],
        "warning": result["warning"], "working_tree_dirty": gitio.is_dirty(root),
        "changed_files": len(result["files"]),
        "changed_files_relevant": page([c["path"] for c in relevant], args),
        "affected_elements": page([a["id"] for a in result["affected"]], args),
        "unmapped_files": page(result["unmapped"], args),
        "evidence": {k: states.get(k, 0) for k in ("fresh", "moved", "stale", "missing", "unanchored")},
        "views_out_of_sync": V.out_of_sync(model),
    }
    plan = model.parsed.get(PLAN)
    if PLAN in model.raw:
        packets = plan.get("packets") if isinstance(plan, dict) else None
        payload["plan"] = {"packets": len(packets) if isinstance(packets, list) else None}
    emit(args, payload)
    return STALE if args.strict and not current else OK


def cmd_changes(args, root):
    model = M.load_worktree(root)
    if args.since:
        check_ref(root, args.since)
    result = compute_changes(root, model, args.since or synced_commit(model))
    if result["warning"]:
        warn(result["warning"])
    result["total_files"] = len(result["files"])
    for key in ("files", "affected", "unmapped", "out_of_scope", "renames"):
        result[key] = page(result[key], args)
    emit(args, result)
    return OK


def cmd_anchor(args, root):
    model = M.load_worktree(root)
    if args.refresh and not args.ids:
        raise UsageError("--refresh needs explicit element ids: re-read each span before replacing its hash")
    if model.errors:
        raise UsageError("fix invalid JSON first: %s" % "; ".join("%s: %s" % e for e in model.errors[:5]))
    need_ids(model, args.ids, sub=False)
    result = E.anchor(root, model, args.ids, args.refresh, args.dry_run)
    emit(args, dict(result, anchored=page(result["anchored"], args), total=len(result["anchored"])))
    return INVALID if result["problems"] else OK


def cmd_check_evidence(args, root):
    model = M.load_worktree(root)
    results = E.check(root, model)
    counts = {k: sum(r["state"] == k for r in results) for k in ("fresh", "moved", "stale", "missing", "unanchored")}
    rows = []
    for r in results:
        if r["state"] == "fresh":
            continue
        row = {"id": r["id"], "at": r["at"], "state": r["state"], "path": r["evidence_path"], "lines": r["item"].get("lines")}
        if r["state"] == "moved":
            row["now"] = {"path": r["path"], "lines": list(r["lines"])}
        else:
            row["reason"] = r["reason"]
        rows.append(row)
    written = E.fix_moved(root, model, results) if args.fix_moved and not args.dry_run else []
    emit(args, {"counts": counts, "items": page(rows, args), "total": len(rows), "written": written})
    return OK


def cmd_fmt(args, root):
    model = M.load_worktree(root)
    paths = rules.unformatted(model)
    if not args.check:
        for path in paths:
            M.write(root, path, M.format_value(model.parsed[path], M.schema_for_path(path)))
    for path, message in model.errors:
        warn("%s: %s" % (path, message))
    emit(args, {"unformatted" if args.check else "formatted": paths, "errors": [p for p, _ in model.errors]})
    return INVALID if model.errors or (args.check and paths) else OK


def cmd_validate(args, root):
    model = M.load_worktree(root, require=False)
    if not model.raw:
        raise M.ModelError("no Evermap model here: %s is missing (run init first)" % M.MANIFEST)
    findings = rules.run(root, model, args.strict)
    errors = sum(f["severity"] == "error" for f in findings)
    emit(args, {"valid": not errors, "errors": errors, "warnings": len(findings) - errors,
                "elements": len(model.elements), "findings": page(findings, args)})
    return INVALID if errors else OK


def cmd_sync(args, root):
    model = M.load_worktree(root)
    head, dirty = gitio.head(root), gitio.is_dirty(root)
    removed = []
    try:
        if _plan_file(root):
            M.remove(root, PLAN)  # the plan served this mapping run; a later run writes a new one
            removed.append(PLAN)
    except UsageError as exc:
        warn("left %s alone: %s" % (PLAN, exc))
    synced = dict(model.manifest.get("synced") or {})
    try:
        base = gitio.resolve(root, str(synced.get("commit")))
        same = not dirty and not synced.get("dirty") and not any(
            not gitio.is_model_path(p) for p in _paths_between(root, base, head))
    except gitio.GitError:
        same = False
    if same:
        emit(args, {"written": [], "removed": removed, "synced": synced,
                    "reason": "nothing changed outside %s/" % M.DIR})
        return OK
    new = {"commit": head, "mode": args.mode}
    if dirty:
        new["dirty"] = True
        warn("the working tree has uncommitted changes outside %s/: recording dirty" % M.DIR)
    manifest = dict(model.manifest, synced=new)
    changed = M.write(root, M.MANIFEST, M.format_value(manifest, "manifest"))
    emit(args, {"written": [M.MANIFEST] if changed else [], "removed": removed, "synced": new})
    return OK


def _paths_between(root, a, b):
    out = gitio.git(root, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
                    "--end-of-options", a, b, "--")
    return gitio._z(out)


def cmd_query(args, root):
    model = M.load_worktree(root)
    ids = need_ids(model, args.ids)
    if args.what != "shared-resources" and not ids:
        raise UsageError("query %s needs at least one element id" % args.what)
    if args.what == "neighbors":
        depth = 1 if args.depth is None else args.depth
        rows = G.Graph(model).neighbors(sorted({M.base_id(i) for i in ids}), depth)
        payload = {"ids": ids, "depth": depth, "total": len(rows), "neighbors": page(rows, args)}
    elif args.what == "impact":
        depth = 2 if args.depth is None else args.depth
        payload = dict({"ids": ids, "depth": depth}, **G.impact(model, ids, depth))
        for key in ("direct", "indirect", "shared_resources", "lineage_downstream", "tests"):
            payload[key] = page(payload[key], args)
    elif args.what == "lineage":
        payload = dict({"ids": ids}, **G.lineage(model, ids, args.direction, 10 if args.depth is None else args.depth))
        for key in ("upstream", "downstream"):
            if key in payload:
                payload[key] = page(payload[key], args)
    elif args.what == "refs":
        graph = G.Graph(model)
        rows = [r for i in ids for r in graph.references_to(i)]
        payload = {"ids": ids, "total": len(rows), "references": page(rows, args)}
    else:
        rows = G.shared_resources(model, ids)
        payload = {"total": len(rows), "shared_resources": page(rows, args)}
    emit(args, payload)
    return OK


def cmd_search(args, root):
    model = M.load_worktree(root)
    rows = G.search(model, " ".join(args.text), args.kind)
    emit(args, {"query": " ".join(args.text), "total": len(rows), "results": page(rows, args)})
    return OK


def cmd_coverage(args, root):
    model = M.load_worktree(root)
    scoped, owners = M.in_scope(model.manifest), M.owners(model)
    files = [p for p in gitio.list_files(root) if scoped(p)]
    owned = {p for p in files if any(match(p) for _, match in owners)}
    coverage = model.manifest.get("coverage") or {}
    known = M.matcher([u.get("path") for u in coverage.get("unmapped") or [] if isinstance(u, dict)])
    unowned = [p for p in files if p not in owned]
    parents = {o.get("parent") for o in model.of_kind("operation")}
    empty = [c["id"] for c in model.of_kind("component")
             if c.get("type") in ("service", "worker", "frontend", "module") and c["id"] not in parents]
    idle = [i for i, match in owners if not any(match(p) for p in files)]
    emit(args, {
        "files_in_scope": len(files), "files_owned": len(owned),
        "unowned": page([p for p in unowned if not known(p)], args),
        "unowned_total": sum(not known(p) for p in unowned),
        "declared_unmapped": page([p for p in unowned if known(p)], args),
        "components_without_operations": empty, "components_without_files": idle,
        "pending": coverage.get("pending") or [], "limitations": coverage.get("limitations") or [],
        "unknown": [i for i in sorted(model.elements) if model.elements[i].get("provenance") == "unknown"],
    })
    return OK


DRAFT, PLAN = rules.DRAFT, rules.PLAN
DRAFT_LIMIT = 4 * 1024 * 1024


def _plan_file(root):
    """Absolute path of the plan when it exists as a regular file; refuses symbolic links."""
    try:
        full = M._safe_target(root, PLAN)
    except M.ModelError as exc:
        raise UsageError(str(exc))
    return full if os.path.isfile(full) else None


def _draft_path(root, path):
    """The draft's absolute path: a regular file inside .evermap/, not a model file, no links."""
    if not isinstance(path, str) or "\\" in path or os.path.isabs(path) or not path.startswith(M.DIR + "/"):
        raise UsageError("the draft must be a file inside %s/: %s" % (M.DIR, path))
    if M.schema_for_path(path) is not None or path.startswith(M.DIR + "/views/") or path == PLAN:
        raise UsageError("the draft must not be a model, manifest, plan or view file: %s" % path)
    try:
        full = M._safe_target(root, path)
    except M.ModelError as exc:
        raise UsageError(str(exc))
    if not os.path.isfile(full):
        raise UsageError("no draft at %s" % path)
    if os.path.getsize(full) > DRAFT_LIMIT:
        raise UsageError("the draft is larger than %d bytes" % DRAFT_LIMIT)
    return full


def _confirmed(value):
    return {where for where, node in rules._walk_dicts(value) if node.get("provenance") == "confirmed"}


def _keep_hashes(element, old):
    """Draft hashes are never trusted: keep the existing hash of an unchanged span, drop the rest."""
    known = {}
    for _, item in E.walk(old or {}):
        if E.is_file_evidence(item) and item.get("hash"):
            known[(item.get("type"), item.get("path"), M.dumps(item.get("lines")))] = item["hash"]
    for _, item in E.walk(element):
        if E.is_file_evidence(item):
            item.pop("hash", None)
            previous = known.get((item.get("type"), item.get("path"), M.dumps(item.get("lines"))))
            if previous:
                item["hash"] = previous


def _check_draft_element(root, model, element, args):
    """Problems as (rule, json path, message); empty when the element may be written."""
    ident = element.get("id")
    if not isinstance(ident, str) or not M.ID_RE.match(ident):
        return [("V2", "$.id", "id must be kind:slug")]
    kind = M.kind_of(ident)
    if element.get("kind") != kind:
        return [("V2", "$.kind", "kind must be %r, as the id says" % kind)]
    problems = [("V1", where, message)
                for where, message in rules.check_schema(element, M.REGISTRY.load(kind + ".schema.json"), M.REGISTRY)]
    report = rules.Report()
    for where, item in E.walk(element):
        rules._v1_evidence(report, "", where, item, ident)
    problems += [(f["rule"], f.get("at", "$"), f["message"]) for f in report.findings]
    problems += [("V9", where, "possible secret: %s" % finding) for where, finding in secrets.scan_value(element)]
    if element.get("lifecycle") == "proposed":
        problems.append(("V5", "$.lifecycle", "proposed elements belong in a proposal overlay, not in model/"))
    path = M.path_for_id(ident)
    try:
        target = M._safe_target(root, path)
    except M.ModelError as exc:
        return problems + [("V2", "$.id", str(exc))]
    old = model.parsed.get(path) if os.path.exists(target) else None
    # A file left unparseable by Git conflict markers is replaced after re-verification (SPEC 5.10).
    conflicted = path not in model.parsed and bool(rules.CONFLICT.search(model.raw.get(path, b"")))
    if os.path.exists(target) and not conflicted and not (isinstance(old, dict) and old.get("id") == ident):
        problems.append(("V2", "$.id", "%s holds something else: not overwritten" % path))
    if kind == "decision" and not args.allow_decisions:
        if conflicted:
            problems.append(("V11", "$", "would replace a conflicted decision (needs --allow-decisions)"))
        elif old is not None and M.format_value(old, kind) != M.format_value(element, kind):
            problems.append(("V11", "$", "would change an existing decision (needs --allow-decisions)"))
        raised = sorted(_confirmed(element) - _confirmed(old or {}))
        if raised:
            problems.append(("V11", raised[0], "would mark a section confirmed (needs --allow-decisions)"))
    return problems


def cmd_apply(args, root):
    model = M.load_worktree(root)
    full = _draft_path(root, args.file)
    with open(full, "rb") as handle:
        data = handle.read()
    try:
        value = M.parse(data)
    except (ValueError, UnicodeDecodeError) as exc:
        emit(args, {"draft": args.file, "elements": 0, "written": 0, "unchanged": 0, "draft_deleted": False,
                    "rejected": [{"rule": "V1", "message": "not valid JSON: %s" % exc}]})
        return INVALID
    items = value if isinstance(value, list) else [value]
    ids = [i.get("id") if isinstance(i, dict) else None for i in items]
    rejected, accepted = [], []
    for index, element in enumerate(items):
        ident = ids[index] if isinstance(ids[index], str) else None
        if not isinstance(element, dict):
            problems = [("V1", "$", "each element must be a JSON object")]
        elif ident is not None and ids.count(ident) > 1:
            problems = [("V2", "$.id", "id appears more than once in the draft")]
        else:
            problems = _check_draft_element(root, model, element, args)
        if problems:
            for rule, where, message in problems[:10]:
                row = {"index": index, "rule": rule}
                if ident is not None and M.ID_RE.match(ident):
                    row["id"] = ident
                row.update({"at": where, "message": message})
                rejected.append(row)
        else:
            accepted.append(element)
    written = unchanged = 0
    for element in accepted:
        path = M.path_for_id(element["id"])
        _keep_hashes(element, model.parsed.get(path))
        if M.write(root, path, M.format_value(element, element["kind"])):
            written += 1
        else:
            unchanged += 1
    deleted = not rejected
    if deleted:
        M.remove(root, args.file)
    # Anchor and format only what this draft wrote: other elements are never touched.
    # Validation still covers the whole model, so their problems are reported.
    mine = sorted(e["id"] for e in accepted)
    result = E.anchor(root, M.load_worktree(root), mine) if mine else {"anchored": [], "problems": []}
    model = M.load_worktree(root)
    paths = {M.path_for_id(i) for i in mine}
    formatted = [p for p in rules.unformatted(model) if p in paths]
    for path in formatted:
        M.write(root, path, M.format_value(model.parsed[path], M.schema_for_path(path)))
    model = M.load_worktree(root)
    findings = [{k: f[k] for k in ("rule", "severity", "id", "path", "at", "message")
                 if k in f and not (k == "path" and "id" in f)} for f in rules.run(root, model)]
    findings = [{"rule": "V6", "severity": "error", "id": p["id"], "at": p["at"], "message": p["message"]}
                for p in result["problems"]] + findings
    errors = sum(f["severity"] == "error" for f in findings)
    emit(args, {
        "draft": args.file, "elements": len(items), "written": written, "unchanged": unchanged,
        "rejected": page(rejected, args), "draft_deleted": deleted,
        "anchored": len(result["anchored"]), "formatted": len(formatted),
        "valid": not errors, "errors": errors, "warnings": len(findings) - errors, "findings": page(findings, args),
    })
    return INVALID if rejected or errors else OK


STARTUP_TOKENS = 9332  # one measurement: Claude Code 2.1.x headless with the lean flags (SPEC 5.12)
REFERENCES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "references")
# The guides a worker reads, by plan mode: "all" is an init packet, "changed" an update packet.
WORKER_READS = {"all": ["model.md", "discover.md", "functional.md"], "changed": ["model.md", "update.md"]}


def _read_tokens(names):
    total = 0
    for name in names:
        try:
            total += (os.path.getsize(os.path.join(REFERENCES, name)) + 3) // 4
        except OSError:
            pass
    return total


def _size(root, path):
    """Characters of a regular in-repository file, "binary", "link", or None when unreadable."""
    if os.path.islink(os.path.join(root, *path.split("/"))):
        return "link"  # never followed: the target is counted under its own path when in scope
    data = gitio.read_worktree(root, path)
    if data is None:
        return None
    if b"\0" in data[:8000]:
        return "binary"
    return len(data.replace(b"\r\n", b"\n").decode("utf-8", "replace"))  # line endings aside, as for hashes


def cmd_estimate(args, root):
    model = M.load_worktree(root)
    scoped, owners = M.in_scope(model.manifest), M.owners(model)
    if args.since and not args.changed:
        raise UsageError("--since needs --changed")
    if args.since:
        check_ref(root, args.since)
    if args.changed:
        result = compute_changes(root, model, args.since or synced_commit(model))
        outside = set(result["out_of_scope"])
        paths = sorted({c["path"] for c in result["files"] if c["status"] != "deleted" and c["path"] not in outside})
    else:
        paths = [p for p in gitio.list_files(root) if scoped(p)]
    groups, skipped, counted = {}, {"binary": 0, "link": 0, "unreadable": 0}, {}
    for path in paths:
        size = _size(root, path)
        if not isinstance(size, int):
            skipped["unreadable" if size is None else size] += 1
            continue
        counted[path] = size
        names = [i for i, match in owners if match(path) and scoped(path)]
        # Unowned files are grouped by top-level directory: the natural packets before components exist.
        for name in names or ["unowned:" + (path.split("/", 1)[0] + "/" if "/" in path else ".")]:
            group = groups.setdefault(name, {"group": name, "files": 0, "characters": 0, "paths": []})
            group["files"] += 1
            group["characters"] += size
            group["paths"].append(path)
    rows = sorted(groups.values(), key=lambda g: (g["group"].startswith("unowned:"), g["group"]))
    for row in rows:
        row["tokens"] = _tokens(row["characters"])
    packets = _pack(rows, counted, args.packet_tokens)
    for row in rows:
        del row["paths"]
    if args.write_plan:
        scope = model.manifest.get("scope") if isinstance(model.manifest.get("scope"), dict) else {}
        plan = {"mode": "changed" if args.changed else "all", "packet_tokens": args.packet_tokens,
                "scope": {"include": scope.get("include") or ["**"], "exclude": scope.get("exclude") or []},
                "packets": [dict(p, files=sorted(set(p["paths"]))) for p in packets]}
        for packet in plan["packets"]:
            del packet["paths"]
        M.write(root, PLAN, json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    for packet in packets:
        packet["files"] = len(packet.pop("paths"))
    mode = "changed" if args.changed else "all"
    reads = WORKER_READS[mode]
    code = _tokens(sum(counted.values()))
    overhead = args.startup_tokens + _read_tokens(reads)
    contexts = [overhead + p["tokens"] for p in packets]
    # An init plan of several packets ends with one final cross-packet pass (references/discover.md).
    final = 1 if mode == "all" and len(packets) > 1 else 0
    emit(args, {
        "approximate": True,
        "method": "characters / 4, plus per worker the startup overhead and the guides it reads",
        "mode": mode,
        "files": len(counted), "characters": sum(counted.values()), "code_tokens": code,
        "skipped": skipped,
        "workers": len(packets), "packet_tokens": args.packet_tokens,
        "worker_reads": reads, "overhead_per_worker": overhead,
        "final_pass": {"workers": final, "expected_billed_input_tokens": {
            "low": final * TURNS[0] * overhead, "high": final * TURNS[1] * overhead},
            "note": "add to the figures above; it reads the model and only the code it needs"},
        "estimated_input_tokens": code + len(packets) * overhead,
        "expected_billed_input_tokens": {
            "low": sum(TURNS[0] * c for c in contexts), "high": sum(TURNS[1] * c for c in contexts),
            "assumption": "workers x turns x average context, assuming %d to %d turns per worker and an "
                          "average context of the overhead plus the packet's code. An assumption until "
                          "EverBench calibrates it; most of it is cached reads, which cost less." % TURNS,
        },
        "plan": PLAN if args.write_plan else None,
        "total_packets": len(packets), "packets": page(packets, args),
        "total_groups": len(rows), "groups": page(rows, args),
        "note": "Approximate, not a guarantee: show the user the billed range, not only the low figure. "
                "A budget cap can be overshot by the last turn. A file owned by several components counts "
                "once in the totals and once per group.",
    })
    return OK


def cmd_packet(args, root):
    """One packet of the plan, for a worker: scope, file paths, and the ids already in the model."""
    model = M.load_worktree(root)
    full = _plan_file(root)
    if full is None:
        raise UsageError("no plan: run estimate --write-plan first")
    with open(full, "rb") as handle:
        try:
            plan = M.parse(handle.read())
        except (ValueError, UnicodeDecodeError) as exc:
            raise UsageError("%s is not valid JSON: %s" % (PLAN, exc))
    packets = plan.get("packets") if isinstance(plan, dict) else None
    if not isinstance(packets, list) or not 1 <= args.number <= len(packets) \
            or not isinstance(packets[args.number - 1], dict):
        raise UsageError("no packet %d in %s" % (args.number, PLAN))
    packet = packets[args.number - 1]
    files = [p for p in packet.get("files") or [] if isinstance(p, str)]
    missing = [p for p in files if not E.valid_path(p) or not os.path.lexists(os.path.join(root, *p.split("/")))]
    ids = [{"id": i, "name": e.get("name") or e.get("title")} for i, e in sorted(model.elements.items())]
    emit(args, {
        "packet": args.number, "of": len(packets), "mode": plan.get("mode"),
        "reads": WORKER_READS.get(plan.get("mode"), WORKER_READS["all"]),
        "groups": packet.get("groups") or [],
        "scope": plan.get("scope") or {},
        "total_files": len(files), "files": page(files, args), "missing": page(missing, args),
        "total_ids": len(ids), "ids": page(ids, args),
        "paging": "--limit and --offset page files, missing and ids together",
    })
    return OK


TURNS = (8, 25)
PACKET_TOKENS = 20000


def _tokens(characters):
    return (characters + 3) // 4


def _pack(rows, sizes, limit):
    """Pack groups, in order, into packets of at most limit code tokens (0: no limit). A group stays
    whole unless it alone is over the limit; then it is split by file, in path order."""
    units = []
    for row in rows:
        if not limit or row["tokens"] <= limit:
            units.append((row["group"], row["tokens"], sorted(row["paths"])))
            continue
        chunks, current = [], []
        for path in sorted(row["paths"]):
            if current and _tokens(sum(sizes[p] for p in current + [path])) > limit:
                chunks.append(current)
                current = []
            current.append(path)
        chunks.append(current)
        for number, chunk in enumerate(chunks, 1):
            part = {"group": row["group"], "part": number, "of": len(chunks), "first": chunk[0], "last": chunk[-1]}
            units.append((part, _tokens(sum(sizes[p] for p in chunk)), chunk))
    packets = []
    for name, tokens, paths in units:
        if not packets or (limit and packets[-1]["tokens"] + tokens > limit):
            packets.append({"packet": len(packets) + 1, "tokens": 0, "paths": [], "groups": []})
        packets[-1]["tokens"] += tokens
        packets[-1]["paths"] += paths
        packets[-1]["groups"].append(name)
    return packets


def cmd_diff(args, root):
    if args.overlay:
        if args.refs:
            raise UsageError("use either two refs or --overlay")
        old = M.load_worktree(root)
        if args.overlay not in old.overlays:
            raise UsageError("no proposal named %r in %s/proposals/" % (args.overlay, M.DIR))
        new = old.overlay(args.overlay)
        names = ["working tree", "proposal " + args.overlay]
    else:
        if not 1 <= len(args.refs) <= 2:
            raise UsageError("diff needs <refA> [<refB>] or --overlay <proposal>")
        for ref in args.refs:
            check_ref(root, ref)
        old = M.load_ref(root, args.refs[0])
        new = M.load_ref(root, args.refs[1]) if len(args.refs) == 2 else M.load_worktree(root)
        names = [args.refs[0], args.refs[1] if len(args.refs) == 2 else "working tree"]
    for name, model in zip(names, (old, new)):
        if model.errors:
            warn("%s: ignoring unreadable files: %s" % (name, ", ".join(p for p, _ in model.errors)))
    result = D.diff(old, new)
    for key in ("added", "removed", "modified"):
        result[key] = page(result[key], args)
    diagram = D.mermaid(old, new, result)
    emit(args, dict({"from": names[0], "to": names[1]}, **result), lambda p: D.markdown(p, diagram))
    return OK


def cmd_render(args, root):
    model = M.load_worktree(root)
    try:
        result = V.write(root, model)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        warn("cannot render an invalid model (%s): run validate" % exc)
        return INVALID
    emit(args, result)
    return OK


# --- parser ---------------------------------------------------------------

def _count(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="directory inside the repository (default: current)")
    common.add_argument("--format", choices=("json", "md"), default="json", help="output format (default: json)")
    common.add_argument("--limit", type=_count, default=50, help="items per list, 0 for all (default: 50)")
    common.add_argument("--offset", type=_count, default=0, help="items to skip in each list")
    common.add_argument("--max-chars", type=_count, default=MAX_CHARS,
                        help="shorten lists to keep output under this size, 0 for no limit (default: %d)" % MAX_CHARS)
    parser = argparse.ArgumentParser(
        prog="evermap.py", epilog=EXIT_CODES,
        description="Deterministic tools for an Evermap application model. "
                    "Writes only inside .evermap/ and never runs repository code.")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.required = True

    def add(name, function, text):
        sub = commands.add_parser(name, help=text, description=text, epilog=EXIT_CODES, parents=[common])
        sub.set_defaults(run=function)
        return sub

    sub = add("init", cmd_init, "Create the manifest for a new model.")
    sub.add_argument("--scope", action="append", metavar="GLOB", help="files to include (repeatable, default **)")
    sub.add_argument("--exclude", action="append", metavar="GLOB", help="files to exclude (repeatable)")
    sub.add_argument("--name", help="system name (default: repository directory name)")
    sub.add_argument("--summary", default="", help="one or two technical sentences")
    sub.add_argument("--plain", default="", help="plain-language description")
    sub = add("status", cmd_status, "Report how fresh the model is.")
    sub.add_argument("--strict", action="store_true", help="exit 4 when the model is not up to date")
    sub = add("changes", cmd_changes, "List changed files and the elements they affect.")
    sub.add_argument("--since", metavar="REF", help="baseline (default: the synced commit)")
    sub = add("anchor", cmd_anchor, "Fill missing evidence hashes and check line ranges.")
    sub.add_argument("ids", nargs="*", help="element ids (default: all)")
    sub.add_argument("--refresh", action="store_true", help="replace existing hashes of the named elements")
    sub.add_argument("--dry-run", action="store_true", help="report without writing")
    sub = add("check-evidence", cmd_check_evidence, "Classify evidence as fresh, moved, stale or missing.")
    sub.add_argument("--fix-moved", action="store_true", help="update path and lines of moved evidence")
    sub.add_argument("--dry-run", action="store_true", help="report without writing")
    sub = add("fmt", cmd_fmt, "Format model files canonically.")
    sub.add_argument("--check", action="store_true", help="report without writing; exit 1 when not formatted")
    sub = add("validate", cmd_validate, "Check validation rules V1 to V16.")
    sub.add_argument("--strict", action="store_true", help="treat stale evidence and formatting as errors")
    sub = add("sync", cmd_sync, "Record HEAD as the commit the model describes.")
    sub.add_argument("--mode", choices=("init", "update", "regenerate"), required=True)
    sub = add("query", cmd_query, "Query the model graph.")
    sub.add_argument("what", choices=("neighbors", "impact", "lineage", "shared-resources", "refs"))
    sub.add_argument("ids", nargs="*", help="element ids; entity:x#field works for impact, lineage and refs")
    sub.add_argument("--depth", type=_count, help="hops (default: 1 neighbors, 2 impact, 10 lineage)")
    sub.add_argument("--direction", choices=("up", "down", "both"), default="both", help="lineage direction")
    sub = add("search", cmd_search, "Search ids, names, summaries, plain text and statements.")
    sub.add_argument("text", nargs="+")
    sub.add_argument("--kind", action="append", choices=M.KINDS, help="restrict to a kind (repeatable)")
    add("coverage", cmd_coverage, "Report in-scope files and components the model does not cover.")
    sub = add("apply", cmd_apply, "Write the elements in a draft file, then anchor, format and validate.")
    sub.add_argument("file", nargs="?", default=DRAFT, help="JSON array of elements inside %s/ (default: %s)"
                     % (M.DIR, DRAFT))
    sub.add_argument("--allow-decisions", action="store_true",
                     help="allow changing a decision or marking a section confirmed")
    sub = add("estimate", cmd_estimate, "Approximate input tokens to map in-scope files, per component.")
    sub.add_argument("--changed", action="store_true", help="only the files that changes reports")
    sub.add_argument("--since", metavar="REF", help="baseline for --changed (default: the synced commit)")
    sub.add_argument("--startup-tokens", type=_count, default=STARTUP_TOKENS,
                     help="startup overhead per worker (default: %d, one measurement)" % STARTUP_TOKENS)
    sub.add_argument("--packet-tokens", type=_count, default=PACKET_TOKENS,
                     help="code tokens per worker packet, 0 for one packet (default: %d)" % PACKET_TOKENS)
    sub.add_argument("--write-plan", action="store_true", help="also write the packets with their files to %s" % PLAN)
    sub = add("packet", cmd_packet, "Print one packet of the plan: scope, files and the ids already in the model.")
    sub.add_argument("number", type=int, help="packet number, from 1")
    sub = add("diff", cmd_diff, "Compare the model at two refs, or a ref with the working tree.")
    sub.add_argument("refs", nargs="*", metavar="ref")
    sub.add_argument("--overlay", metavar="PROPOSAL", help="compare the working tree with a proposal overlay")
    add("render", cmd_render, "Regenerate the views.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        root = gitio.find_root(args.repo)
        return args.run(args, root)
    except UsageError as exc:
        warn(str(exc))
        return USAGE
    except (gitio.GitError, M.ModelError) as exc:
        warn(str(exc))
        return NO_MODEL
