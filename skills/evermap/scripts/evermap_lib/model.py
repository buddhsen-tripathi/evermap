"""Loading, formatting and writing the canonical model."""

import json
import os
import re

from . import gitio
from .jsonschema import Registry, canonical_order

SCHEMA_VERSION = "0.1"
DIR = ".evermap"
MANIFEST = DIR + "/evermap.json"
KIND_DIRS = {
    "component": "components", "operation": "operations", "entity": "entities",
    "channel": "channels", "workflow": "workflows", "rule": "rules",
    "invariant": "invariants", "role": "roles",
}
KINDS = list(KIND_DIRS) + ["decision"]
SEGMENT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
ID_RE = re.compile(r"^(%s):(%s(?:\.%s)*)\Z" % ("|".join(KINDS), SEGMENT, SEGMENT))
REGISTRY = Registry()


class ModelError(Exception):
    """The model is absent or cannot be used."""


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key %r" % key)
        result[key] = value
    return result


def _no_constant(name):
    raise ValueError("%s is not valid JSON" % name)


def parse(data):
    """Strict JSON: UTF-8, no duplicate keys, no NaN or Infinity."""
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates,
                          parse_constant=_no_constant)
    except RecursionError:
        raise ValueError("nested too deeply")


def path_for_id(identifier):
    match = ID_RE.match(identifier)
    if not match:
        return None
    kind, slug = match.groups()
    if kind == "decision":
        return "%s/decisions/%s.json" % (DIR, slug)
    return "%s/model/%s/%s.json" % (DIR, KIND_DIRS[kind], slug)


def kind_of(identifier):
    return identifier.split(":", 1)[0]


def base_id(reference):
    return reference.split("#", 1)[0]


# --- canonical formatting -------------------------------------------------

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sort_key(name, item):
    if name == "relations":
        return (str(item.get("type")), str(item.get("target")), dumps(item))
    if name in ("evidence", "verified_by"):
        lines = item.get("lines")
        numbers = [n for n in lines if isinstance(n, int)] if isinstance(lines, list) else []
        return (str(item.get("path", "")), numbers, dumps(item))
    return (str(item.get("name")), dumps(item))


SORTED_OBJECTS = {"relations", "evidence", "verified_by", "fields"}
SORTED_STRINGS = {"aliases", "tags"}


def _sort_arrays(value, name=None):
    if isinstance(value, dict):
        return {k: _sort_arrays(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [_sort_arrays(v) for v in value]
        if name in SORTED_OBJECTS and items and all(isinstance(i, dict) for i in items):
            return sorted(items, key=lambda i: _sort_key(name, i))
        if name in SORTED_STRINGS and all(isinstance(i, str) for i in items):
            return sorted(items)
        return items
    return value


def format_value(value, schema_name):
    """Canonical text for a parsed file."""
    schema = REGISTRY.load(schema_name + ".schema.json") if schema_name else {}
    ordered = canonical_order(_sort_arrays(value), schema, REGISTRY)
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


def same_text(data, text):
    """Compare file bytes with canonical text, ignoring CRLF checkouts."""
    return data.replace(b"\r\n", b"\n") == text.encode("utf-8")


# --- file classification --------------------------------------------------

def schema_for_path(path):
    """Schema name for a path under .evermap/, or None when it is not a model file."""
    parts = path.split("/")
    if path == MANIFEST:
        return "manifest"
    if len(parts) == 3 and parts[1] == "decisions" and path.endswith(".json"):
        return "decision"
    if len(parts) == 4 and parts[1] == "model" and path.endswith(".json"):
        for kind, folder in KIND_DIRS.items():
            if parts[2] == folder:
                return kind
    if parts[1:2] == ["proposals"] and len(parts) > 3:
        if len(parts) == 4 and parts[3] == "proposal.json":
            return "proposal"
        inner = schema_for_path("/".join([DIR] + parts[3:]))
        return inner if inner not in (None, "manifest") else None
    return None


# --- model ----------------------------------------------------------------

class Model:
    """Parsed model files from one source (working tree or a commit)."""

    def __init__(self, files):
        self.raw = files                # path -> bytes, everything under .evermap/
        self.parsed = {}                # path -> value
        self.errors = []                # (path, message)
        self.elements = {}              # id -> dict (decisions included)
        self.paths = {}                 # id -> path
        self.duplicates = []            # (id, path)
        self.overlays = {}              # proposal slug -> {path: value}
        for path in sorted(files):
            if not path.endswith(".json"):
                continue
            try:
                value = parse(files[path])
            except (ValueError, UnicodeDecodeError) as exc:
                self.errors.append((path, "not valid JSON: %s" % exc))
                continue
            self.parsed[path] = value
            if path.startswith(DIR + "/proposals/"):
                self.overlays.setdefault(path.split("/")[2], {})[path] = value
            elif schema_for_path(path) not in (None, "manifest") and isinstance(value, dict):
                identifier = value.get("id")
                if not isinstance(identifier, str):
                    continue
                if identifier in self.elements:
                    self.duplicates.append((identifier, path))
                else:
                    self.elements[identifier] = value
                    self.paths[identifier] = path
        manifest = self.parsed.get(MANIFEST)
        self.manifest = manifest if isinstance(manifest, dict) else {}

    def get(self, reference):
        return self.elements.get(base_id(reference))

    def of_kind(self, *kinds):
        return [self.elements[i] for i in sorted(self.elements) if kind_of(i) in kinds]

    def overlay(self, slug):
        """A copy of this model with a proposal's elements shadowing the base."""
        if slug not in self.overlays:
            raise ModelError("no proposal named %r" % slug)
        prefix = "%s/proposals/%s/" % (DIR, slug)
        files = {p: d for p, d in self.raw.items() if not p.startswith(DIR + "/proposals/")}
        for path in self.overlays[slug]:
            if schema_for_path(path) not in (None, "proposal"):
                files[DIR + "/" + path[len(prefix):]] = self.raw[path]
        return Model(files)


def load_worktree(root, require=True):
    base = os.path.join(root, DIR)
    files = {}
    if os.path.isdir(base) and not os.path.islink(base):
        for folder, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(folder, d)))
            for name in sorted(names):
                full = os.path.join(folder, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                path = os.path.relpath(full, root).replace(os.sep, "/")
                with open(full, "rb") as handle:
                    files[path] = handle.read()
    if require and MANIFEST not in files:
        raise ModelError("no Evermap model here: %s is missing (run init first)" % MANIFEST)
    return Model(files)


def load_ref(root, ref):
    return Model(gitio.files_at(root, gitio.resolve(root, ref), DIR))


# --- writing --------------------------------------------------------------

def _safe_target(root, path):
    """Absolute path for writing, refusing anything that resolves outside .evermap/."""
    parts = path.split("/")
    if parts[0] != DIR or len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
        raise ModelError("refusing to write outside %s/: %s" % (DIR, path))
    current = root
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise ModelError("refusing to write through a symbolic link: %s" % path)
    return current


def write(root, path, text):
    """Write text under .evermap/ with LF endings. Returns True when it changed."""
    target = _safe_target(root, path)
    data = text.encode("utf-8")
    if os.path.isfile(target):
        with open(target, "rb") as handle:
            if same_text(handle.read(), text):
                return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(data)
    return True


def remove(root, path):
    os.remove(_safe_target(root, path))


# --- globs ----------------------------------------------------------------

def glob_regex(pattern):
    if pattern.endswith("/"):
        pattern += "**"
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z", re.S)


def matcher(patterns):
    regexes = [glob_regex(p) for p in patterns if isinstance(p, str)]
    return lambda path: any(r.match(path) for r in regexes)


def in_scope(manifest):
    scope = manifest.get("scope") or {}
    include = matcher(scope.get("include") or ["**"])
    exclude = matcher(scope.get("exclude") or [])
    return lambda path: not gitio.is_model_path(path) and include(path) and not exclude(path)


def owners(model):
    """[(component id, match function)] for components that declare paths."""
    return [(c["id"], matcher(c["paths"])) for c in model.of_kind("component")
            if isinstance(c.get("paths"), list)]
