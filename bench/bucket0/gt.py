"""Mechanical ground truth for the Evermap benchmark on this repository.

Reads a checkout (default: the current directory) and writes JSON with what the code declares
without interpretation:
  endpoints  - one per exported HTTP method in server/routes/**/route.ts
  tables     - Drizzle pgTable definitions in db/schema.ts: columns, primary keys, references
  bindings   - queues, Durable Objects, containers and R2 buckets in wrangler.jsonc

Usage: python3 gt.py [checkout] > gt.json
"""

import json
import os
import re
import sys

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let)\s+(%s)\b" % "|".join(METHODS), re.M)
EXPORT_AS = re.compile(r"export\s*\{([^}]*)\}")
TABLE = re.compile(r'export\s+const\s+(\w+)\s*=\s*pgTable\(\s*"([^"]+)"\s*,\s*\{')
COLUMN = re.compile(r'^\s*(\w+)\s*:\s*\w+\(\s*"([^"]+)"', re.M)
REFERENCE = re.compile(r'^\s*(\w+)\s*:.*?\.references\(\s*\(\)\s*=>\s*(\w+)\.(\w+)', re.M)


def route_path(rel):
    parts = rel.split("/")[2:-1]  # drop server/routes and route.ts
    out = []
    for p in parts:
        if p.startswith("[[...") or p.startswith("[..."):
            out.append("*")
        elif p.startswith("["):
            out.append(":" + p.strip("[]"))
        else:
            out.append(p)
    return "/" + "/".join(out)


def endpoints(root):
    found = []
    base = os.path.join(root, "server", "routes")
    for d, _, files in os.walk(base):
        for f in files:
            if f not in ("route.ts", "route.tsx"):
                continue
            path = os.path.join(d, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            text = open(path, encoding="utf-8").read()
            methods = set(EXPORT.findall(text))
            for group in EXPORT_AS.findall(text):
                for item in group.split(","):
                    name = item.strip().split(" as ")[-1].strip()
                    if name in METHODS:
                        methods.add(name)
            for m in sorted(methods):
                found.append({"method": m, "path": route_path(rel), "file": rel})
    return sorted(found, key=lambda e: (e["path"], e["method"]))


def _body(text, start):
    """Text of the object literal opening at text[start] == '{'."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def tables(root):
    text = open(os.path.join(root, "db", "schema.ts"), encoding="utf-8").read()
    names = {m.group(1): m.group(2) for m in TABLE.finditer(text)}
    out = []
    for m in TABLE.finditer(text):
        body = _body(text, m.end() - 1)
        columns = [{"name": c.group(1), "column": c.group(2)} for c in COLUMN.finditer(body)]
        keys = [c.group(1) for c in COLUMN.finditer(body)
                if ".primaryKey()" in body[c.start():body.find("\n", c.start())]]
        refs = [{"field": r.group(1), "table": names.get(r.group(2), r.group(2)), "column": r.group(3)}
                for r in REFERENCE.finditer(body)]
        first = text.count("\n", 0, m.start()) + 1
        last = first + body.count("\n")
        out.append({"var": m.group(1), "table": m.group(2), "columns": columns,
                    "primary_key": keys, "references": refs, "lines": [first, last]})
    return out


def bindings(root):
    raw = open(os.path.join(root, "wrangler.jsonc"), encoding="utf-8").read()
    raw = re.sub(r"(?m)^\s*//.*$", "", raw)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    conf = json.loads(raw)
    queues = conf.get("queues") or {}
    return {
        "queues": sorted({q["queue"] for q in queues.get("producers", [])} |
                         {q["queue"] for q in queues.get("consumers", [])}),
        "durable_objects": sorted(b["class_name"] for b in (conf.get("durable_objects") or {}).get("bindings", [])),
        "containers": sorted(c["class_name"] for c in conf.get("containers") or []),
        "r2_buckets": sorted(b["binding"] for b in conf.get("r2_buckets") or []),
    }


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    gt = {"endpoints": endpoints(root), "tables": tables(root), "bindings": bindings(root)}
    gt["counts"] = {"endpoints": len(gt["endpoints"]), "route_files": len({e["file"] for e in gt["endpoints"]}),
                    "tables": len(gt["tables"]),
                    "columns": sum(len(t["columns"]) for t in gt["tables"]),
                    "references": sum(len(t["references"]) for t in gt["tables"])}
    json.dump(gt, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
