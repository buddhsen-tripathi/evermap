"""Validator for the JSON Schema subset the Evermap schemas use.

Supported keywords: type, required, enum, pattern, properties, items,
additionalProperties, and $ref (within a file or to a sibling schema file).
Annotations are ignored. Any other keyword raises SchemaError, so a schema can
never silently rely on something that is not checked.
"""

import json
import os
import re

SCHEMA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "schema")
)

ANNOTATIONS = {"$schema", "$id", "$defs", "$comment", "title", "description", "definitions"}
KEYWORDS = {
    "type", "required", "enum", "pattern", "properties", "items", "additionalProperties", "$ref",
}


class SchemaError(Exception):
    """The schema itself is unusable."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_ok(value, name):
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "number":
        return _is_number(value)
    if name == "integer":
        return _is_number(value) and float(value).is_integer()
    raise SchemaError("unknown type %r" % (name,))


def _equal(a, b):
    """JSON equality: 1 == 1.0, but true != 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _matches(pattern, value):
    # JSON Schema's "$" never matches before a trailing newline; Python's does.
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    try:
        return re.search(pattern, value) is not None
    except re.error as exc:
        raise SchemaError("unsupported pattern %r: %s" % (pattern, exc))


class Registry:
    """Loads schema files on demand and resolves references between them."""

    def __init__(self, directory=SCHEMA_DIR):
        self.directory = directory
        self._files = {}

    def load(self, name):
        if name not in self._files:
            if os.path.basename(name) != name:
                raise SchemaError("schema reference outside the schema directory: %r" % name)
            try:
                with open(os.path.join(self.directory, name), encoding="utf-8") as handle:
                    self._files[name] = json.load(handle)
            except (OSError, ValueError) as exc:
                raise SchemaError("cannot load schema %r: %s" % (name, exc))
        return self._files[name]

    def resolve(self, ref, root):
        filename, _, pointer = ref.partition("#")
        doc = self.load(filename) if filename else root
        node = doc
        if pointer:
            if not pointer.startswith("/"):
                raise SchemaError("unsupported reference %r" % ref)
            for part in pointer[1:].split("/"):
                part = part.replace("%25", "%").replace("%22", '"')
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(node, list) and part.isdigit() and int(part) < len(node):
                    node = node[int(part)]
                elif isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    raise SchemaError("unresolvable reference %r" % ref)
        return node, doc


def validate(value, schema, registry=None, root=None, path="$"):
    """Return a list of (json path, message) pairs. Empty means valid."""
    registry = registry or Registry()
    root = schema if root is None else root
    if schema is True:
        return []
    if schema is False:
        return [(path, "no value is allowed here")]
    if not isinstance(schema, dict):
        raise SchemaError("a schema must be an object or a boolean")
    unknown = set(schema) - KEYWORDS - ANNOTATIONS
    if unknown:
        raise SchemaError("unsupported keyword(s): %s" % ", ".join(sorted(unknown)))

    errors = []
    if "$ref" in schema:
        target, target_root = registry.resolve(schema["$ref"], root)
        errors += validate(value, target, registry, target_root, path)

    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, name) for name in names):
            return errors + [(path, "expected %s" % " or ".join(names))]

    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        errors.append((path, "must be one of: %s" % ", ".join(map(str, schema["enum"]))))

    if "pattern" in schema and isinstance(value, str) and not _matches(schema["pattern"], value):
        errors.append((path, "does not match %s" % schema["pattern"]))

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append((path, "missing required field %r" % name))
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            where = "%s.%s" % (path, name)
            if name in properties:
                errors += validate(item, properties[name], registry, root, where)
            elif extra is False:
                errors.append((where, "unknown field"))
            elif extra is not True:
                errors += validate(item, extra, registry, root, where)

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += validate(item, schema["items"], registry, root, "%s[%d]" % (path, index))
    return errors


def canonical_order(value, schema, registry, root=None):
    """Return value with object keys in schema property order, unknown keys last."""
    root = schema if root is None else root
    while isinstance(schema, dict) and "$ref" in schema:
        schema, root = registry.resolve(schema["$ref"], root)
    if not isinstance(schema, dict):
        schema = {}
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        keys = [k for k in properties if k in value]
        keys += sorted(k for k in value if k not in properties)
        return {k: canonical_order(value[k], properties.get(k, {}), registry, root) for k in keys}
    if isinstance(value, list):
        return [canonical_order(v, schema.get("items", {}), registry, root) for v in value]
    return value
