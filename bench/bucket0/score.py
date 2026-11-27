"""Score one benchmark run against the mechanical ground truth (gt.py).

Usage: python3 score.py --run NAME
Reads the run's clone (EVERMAP_BENCH_WORK/NAME) and raw/NAME/log.json, writes results/NAME.json:
numbers only, no code, replies or element text.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # this Evermap checkout
SKILL = os.environ.get("EVERMAP_SKILL", os.path.join(ROOT, "skills", "evermap"))
SCRIPT = os.path.join(SKILL, "scripts", "evermap.py")
WORKDIR = os.environ.get("EVERMAP_BENCH_WORK", os.path.join(tempfile.gettempdir(), "evermap-bench"))
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def norm(s):
    s = re.sub(r"[^0-9a-z]", "", str(s).lower())
    return s[:-1] if s.endswith("s") and len(s) > 3 else s


def evidence_paths(value, found=None):
    """Every evidence path anywhere inside an element."""
    found = set() if found is None else found
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and ("lines" in value or value.get("type") == "code"):
            found.add(value["path"])
        for v in value.values():
            evidence_paths(v, found)
    elif isinstance(value, list):
        for v in value:
            evidence_paths(v, found)
    return found


def evermap(work, *args):
    out = subprocess.run([sys.executable, "-B", SCRIPT] + list(args) + ["--format", "json", "--max-chars", "0", "--limit", "0"],
                         cwd=work, capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {}


def ratio(a, b):
    return round(a / b, 3) if b else None


def score(work, gt, log):
    elements = {}
    for path in glob.glob(os.path.join(work, ".evermap", "model", "**", "*.json"), recursive=True):
        e = json.load(open(path, encoding="utf-8"))
        elements[e["id"]] = e
    for path in glob.glob(os.path.join(work, ".evermap", "decisions", "*.json")):
        e = json.load(open(path, encoding="utf-8"))
        elements[e["id"]] = e
    kinds = {}
    for e in elements.values():
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    ops = [e for e in elements.values() if e.get("kind") == "operation"]

    # Endpoints: an operation citing the route file whose trigger, name or id names the method.
    # OPTIONS handlers are CORS preflight: counted apart, not as operations.
    preflight = [ep for ep in gt["endpoints"] if ep["method"] == "OPTIONS"]
    scored = [ep for ep in gt["endpoints"] if ep["method"] != "OPTIONS"]
    by_file = {}
    for ep in scored:
        by_file.setdefault(ep["file"], []).append(ep["method"])
    matched, wrong_method = 0, 0
    cited_files = set()
    for op in ops:
        cited_files |= {p for p in evidence_paths(op) if p in by_file}
    for f, methods in by_file.items():
        here = [op for op in ops if f in evidence_paths(op)]
        for m in methods:
            hit = False
            for op in here:
                text = " ".join(str(x) for x in (op.get("id"), op.get("name"),
                                                  (op.get("trigger") or {}).get("detail"))).upper()
                named = [x for x in METHODS if re.search(r"\b%s\b" % x, text)]
                if m in named or (not named and len(methods) == 1):
                    hit = True
                    break
            matched += hit
    for op in ops:
        trig = op.get("trigger") or {}
        if trig.get("type") not in ("http", "webhook"):
            continue
        files = [p for p in evidence_paths(op) if p in by_file]
        detail = str(trig.get("detail") or "").upper()
        named = [x for x in METHODS if re.search(r"\b%s\b" % x, detail)]
        if files and named and not any(m in by_file[f] for f in files for m in named):
            wrong_method += 1

    # Tables, columns and foreign keys.
    entities = [e for e in elements.values() if e.get("kind") == "entity"]
    lookup = {}
    for e in entities:
        for key in (e.get("name"), e["id"].split(":", 1)[1]):
            lookup.setdefault(norm(key), e)
    table_hits, col_total, col_hits, fk_total, fk_hits = 0, 0, 0, 0, 0
    def overlap(e, lines):
        """Lines of the table's definition that the entity's own evidence in db/schema.ts covers."""
        best = 0
        for ev in e.get("evidence") or []:
            if isinstance(ev, dict) and ev.get("path") == "db/schema.ts" and isinstance(ev.get("lines"), list):
                a, b = ev["lines"][0], ev["lines"][-1]
                best = max(best, min(b, lines[1]) - max(a, lines[0]) + 1)
        return best

    # A table matches the entity whose evidence covers most of its definition, else its name.
    entity_of = {}
    for t in gt["tables"]:
        scored_entities = sorted(((overlap(e, t["lines"]), e["id"], e) for e in entities), key=lambda x: (-x[0], x[1]))
        if scored_entities and scored_entities[0][0] > 0:
            entity_of[t["table"]] = scored_entities[0][2]
        else:
            e = lookup.get(norm(t["table"])) or lookup.get(norm(t["var"]))
            if e:
                entity_of[t["table"]] = e
    for t in gt["tables"]:
        e = entity_of.get(t["table"])
        col_total += len(t["columns"])
        fk_total += len(t["references"])
        if not e:
            continue
        table_hits += 1
        fields = {norm(f.get("name")) for f in e.get("fields") or [] if isinstance(f, dict)}
        col_hits += sum(1 for c in t["columns"] if norm(c["name"]) in fields or norm(c["column"]) in fields)
        for r in t["references"]:
            target = entity_of.get(r["table"])
            for c in e.get("constraints") or []:
                ref = str(c.get("references") or "")
                if c.get("type") == "foreign-key" and target and ref.split("#")[0] == target["id"] \
                        and any(norm(x) == norm(r["field"]) for x in c.get("fields") or []):
                    fk_hits += 1
                    break

    # Infrastructure named in wrangler.jsonc: found when an element mentions it.
    blob = json.dumps(list(elements.values())).lower()
    b = gt["bindings"]
    names = b["queues"] + b["durable_objects"] + b["containers"] + b["r2_buckets"]
    flat = re.sub(r"[^0-9a-z]", "", blob)
    infra = sum(1 for n in names if re.sub(r"[^0-9a-z]", "", n.lower()) in flat)

    v = evermap(work, "validate")
    findings = v.get("findings") or []
    cov = evermap(work, "coverage")
    st = evermap(work, "status")
    workers = log.get("workers", [])
    total = lambda k: sum(w.get(k) or 0 for w in workers)
    return {
        "run": log.get("run"), "agent": log.get("agent"), "model": log.get("model"),
        "repo_commit": log.get("repo_commit"), "skill_commit": log.get("skill_commit"),
        "started": log.get("started"), "finished": log.get("finished"),
        "process": {
            "workers": len(workers),
            "continuations": sum(1 for w in workers if w["label"].endswith("-cont")),
            "cost_usd": round(total("cost_usd"), 2),
            "input_tokens": total("input_tokens"), "cache_read_tokens": total("cache_read_tokens"),
            "cache_write_tokens": total("cache_write_tokens"), "output_tokens": total("output_tokens"),
            "codex_tokens_used": total("tokens_used"),
            "turns": total("turns"), "worker_seconds": round(total("seconds")),
            "failed_workers": sum(1 for w in workers if w.get("exit") or w.get("is_error")),
            "drafts_left": sum(1 for w in workers if w.get("draft_left")),
            "tree_changed": sum(1 for w in workers if w.get("dirty") or w.get("head_unchanged") is False),
            "pending_lines": log.get("pending_lines"),
            "estimate": log.get("estimate"),
        },
        "model": {
            "elements": len(elements), "by_kind": dict(sorted(kinds.items())),
            "validate_errors": v.get("errors"), "validate_warnings": v.get("warnings"),
            "v16_warnings": sum(1 for f in findings if f.get("rule") == "V16"),
            "evidence": st.get("evidence"),
            "unowned_files": cov.get("unowned_total", len(cov.get("unowned") or [])),
            "synced": log.get("sync"), "up_to_date": st.get("up_to_date"),
            "thin": (log.get("render") or {}).get("thin"),
        },
        "accuracy": {
            "endpoints": {"total": len(scored), "matched": matched,
                          "recall": ratio(matched, len(scored)), "options_preflight_excluded": len(preflight),
                          "route_files_cited": len(cited_files), "route_files": len(by_file),
                          "wrong_method_ops": wrong_method,
                          "http_operations": sum(1 for o in ops if (o.get("trigger") or {}).get("type") in ("http", "webhook"))},
            "tables": {"total": len(gt["tables"]), "matched": table_hits, "recall": ratio(table_hits, len(gt["tables"])),
                       "table_entities": sum(1 for e in entities if e.get("type") == "table")},
            "columns": {"total": col_total, "matched": col_hits, "recall": ratio(col_hits, col_total)},
            "foreign_keys": {"total": fk_total, "matched": fk_hits, "recall": ratio(fk_hits, fk_total)},
            "infrastructure": {"total": len(names), "mentioned": infra},
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--work", help="score this checkout instead of the run's clone (no log needed)")
    args = ap.parse_args()
    work = args.work or os.path.join(WORKDIR, args.run)
    logfile = os.path.join(HERE, "raw", args.run, "log.json")
    log = json.load(open(logfile)) if os.path.exists(logfile) else {"run": args.run}
    gt = json.loads(subprocess.run([sys.executable, os.path.join(HERE, "gt.py"), work],
                                   capture_output=True, text=True, check=True).stdout)
    result = score(work, gt, log)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", args.run + ".json"), "w") as f:
        json.dump(result, f, indent=1)
        f.write("\n")
    print(json.dumps({"process": result["process"], "model": result["model"], "accuracy": result["accuracy"]}, indent=1))


if __name__ == "__main__":
    main()
