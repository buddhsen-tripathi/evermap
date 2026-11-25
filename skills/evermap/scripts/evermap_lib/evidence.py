"""Evidence spans: hashing, anchoring and freshness."""

import hashlib

from . import gitio, model as M

FILE_TYPES = {"code", "config", "schema", "test", "doc"}


def is_file_evidence(item):
    return isinstance(item, dict) and item.get("type") in FILE_TYPES


def walk(value, path="$"):
    """Yield (json path, evidence dict) for every evidence item inside an element."""
    if isinstance(value, dict):
        for key, child in value.items():
            where = "%s.%s" % (path, key)
            if key in ("evidence", "verified_by") and isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, dict):
                        yield "%s[%d]" % (where, index), item
            elif key in ("spec", "source") and isinstance(child, dict) and "type" in child:
                yield where, child
            else:
                for found in walk(child, where):
                    yield found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for found in walk(child, "%s[%d]" % (path, index)):
                yield found


def split_lines(data):
    """Normalised lines: LF endings, trailing whitespace stripped."""
    lines = data.replace(b"\r\n", b"\n").split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return [line.rstrip(b" \t\r\f\v") for line in lines]


def hash_lines(lines):
    return "sha256:" + hashlib.sha256(b"\n".join(lines)).hexdigest()[:16]


def valid_range(item, count=None):
    span = item.get("lines")
    if not (isinstance(span, list) and len(span) == 2):
        return False
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in span):
        return False
    return 1 <= span[0] <= span[1] and (count is None or span[1] <= count)


def valid_path(path):
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        return False
    if "\0" in path or (len(path) > 1 and path[1] == ":"):
        return False
    parts = path.split("/")
    return parts[0] != ".git" and all(part not in ("", ".", "..") for part in parts)


class Checker:
    """Classifies evidence against the working tree, caching file reads."""

    def __init__(self, root, renames=None):
        self.root = root
        self.renames = renames or {}
        self._lines = {}

    def lines(self, path):
        if path not in self._lines:
            data = gitio.read_worktree(self.root, path) if valid_path(path) else None
            self._lines[path] = None if data is None else split_lines(data)
        return self._lines[path]

    def _find(self, path, item):
        """Start lines (1-based) where the recorded span occurs in path."""
        lines = self.lines(path) or []
        length = item["lines"][1] - item["lines"][0] + 1
        return [i + 1 for i in range(len(lines) - length + 1)
                if hash_lines(lines[i:i + length]) == item["hash"]]

    def _has_symbol(self, path, symbol):
        return any(symbol.encode("utf-8") in line for line in self.lines(path) or [])

    def classify(self, item):
        """Return {state, path?, lines?, reason?}; state is fresh, moved, stale,
        missing or unanchored (no hash recorded yet)."""
        if not valid_path(item.get("path")) or not valid_range(item):
            return {"state": "stale", "reason": "invalid path or line range"}
        path, symbol = item["path"], item.get("symbol")
        if not item.get("hash"):
            if self.lines(path) is None:
                return {"state": "missing", "reason": "file not found"}
            return {"state": "unanchored", "reason": "no hash recorded"}
        lines = self.lines(path)
        target = path
        if lines is None:
            target = self.renames.get(path)
            if target is None or self.lines(target) is None:
                return {"state": "missing", "reason": "file not found"}
        start, end = item["lines"]
        if lines is not None and end <= len(lines) and hash_lines(lines[start - 1:end]) == item["hash"]:
            return {"state": "fresh"}
        length = end - start + 1
        found = self._find(target, item)
        if len(found) == 1:
            return {"state": "moved", "path": target, "lines": [found[0], found[0] + length - 1]}
        if symbol and not self._has_symbol(target, symbol):
            return {"state": "missing", "reason": "symbol %r not found" % symbol}
        # Identical copies contain the same symbol, so nothing in the file can tell them apart.
        if found:
            return {"state": "stale", "reason": "the span occurs more than once"}
        return {"state": "stale", "reason": "the span changed"}


def rename_map(root, base):
    if not base:
        return {}
    try:
        changes = gitio.changed_files(root, base)
    except gitio.GitError:
        return {}
    return {c["old_path"]: c["path"] for c in changes if c["status"] == "renamed"}


def checker_for(root, model):
    synced = model.manifest.get("synced")
    commit = synced.get("commit") if isinstance(synced, dict) else None
    return Checker(root, rename_map(root, commit if isinstance(commit, str) else None))


def check(root, model):
    """Classify every file evidence item. Sorted by element id."""
    checker, results = checker_for(root, model), []
    for identifier in sorted(model.elements):
        for where, item in walk(model.elements[identifier]):
            if is_file_evidence(item):
                result = checker.classify(item)
                results.append(dict(result, id=identifier, at=where,
                                    evidence_path=item.get("path"), item=item))
    return results


def save(root, model, identifiers):
    """Write the given elements in canonical form. Returns the changed paths."""
    written = []
    for identifier in sorted(identifiers):
        path = model.paths[identifier]
        text = M.format_value(model.elements[identifier], M.schema_for_path(path))
        if M.write(root, path, text):
            written.append(path)
    return written


def anchor(root, model, identifiers=None, refresh=False, dry_run=False):
    """Fill absent hashes. With refresh, also replace hashes of the named elements."""
    checker = Checker(root)
    anchored, problems, touched = [], [], set()
    for identifier in sorted(identifiers or model.elements):
        for where, item in walk(model.elements[identifier]):
            if not is_file_evidence(item) or (item.get("hash") and not refresh):
                continue
            lines = checker.lines(item.get("path"))
            if lines is None:
                problems.append({"id": identifier, "at": where, "message":
                                 "file not found: %s" % item.get("path")})
                continue
            if not valid_range(item, len(lines)):
                problems.append({"id": identifier, "at": where, "message":
                                 "lines %s are not valid in %s (%d lines)"
                                 % (item.get("lines"), item["path"], len(lines))})
                continue
            start, end = item["lines"]
            value = hash_lines(lines[start - 1:end])
            if not any(lines[start - 1:end]):
                problems.append({"id": identifier, "at": where, "message":
                                 "the span in %s is blank" % item["path"]})
                continue
            if item.get("hash") != value:
                item["hash"] = value
                anchored.append({"id": identifier, "at": where})
                touched.add(identifier)
    written = [] if dry_run else save(root, model, touched)
    return {"anchored": anchored, "problems": problems, "written": written}


def fix_moved(root, model, results):
    touched = set()
    for result in results:
        if result["state"] == "moved":
            result["item"]["path"] = result["path"]
            result["item"]["lines"] = result["lines"]
            touched.add(result["id"])
    return save(root, model, touched)
