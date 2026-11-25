import json
import os
import unittest

from tests import support  # noqa: F401
from evermap_lib import jsonschema as J
from evermap_lib import model as M

SUITE = os.environ.get("JSON_SCHEMA_TEST_SUITE")


class Subset(unittest.TestCase):
    def ok(self, value, schema):
        self.assertEqual(J.validate(value, schema), [])

    def bad(self, value, schema):
        self.assertNotEqual(J.validate(value, schema), [])

    def test_types(self):
        self.ok(1.0, {"type": "integer"})
        self.bad(1.5, {"type": "integer"})
        self.bad(True, {"type": "integer"})
        self.bad(True, {"type": "number"})
        self.ok(None, {"type": ["string", "null"]})

    def test_enum_distinguishes_booleans_from_numbers(self):
        self.bad(True, {"enum": [1]})
        self.ok(1.0, {"enum": [1]})

    def test_pattern_end_anchor_rejects_trailing_newline(self):
        self.ok("ab", {"pattern": "^ab$"})
        self.bad("ab\n", {"pattern": "^ab$"})

    def test_required_properties_additional(self):
        schema = {"properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
        self.ok({"a": "x"}, schema)
        self.bad({}, schema)
        self.bad({"a": "x", "b": 1}, schema)

    def test_items_and_refs(self):
        schema = {"$defs": {"n": {"type": "integer"}}, "items": {"$ref": "#/$defs/n"}}
        self.ok([1, 2], schema)
        self.bad([1, "x"], schema)

    def test_unsupported_keyword_is_an_error(self):
        with self.assertRaises(J.SchemaError):
            J.validate(1, {"minimum": 0})

    def test_reference_cannot_leave_schema_directory(self):
        with self.assertRaises(J.SchemaError):
            J.validate(1, {"$ref": "../../SKILL.md#"})


class ShippedSchemas(unittest.TestCase):
    def test_every_schema_uses_only_the_subset(self):
        def visit(schema, root, name):
            unknown = set(schema) - J.KEYWORDS - J.ANNOTATIONS
            self.assertFalse(unknown, "%s: %s" % (name, unknown))
            if "$ref" in schema:
                M.REGISTRY.resolve(schema["$ref"], root)
            for group in ("properties", "$defs"):
                for child in schema.get(group, {}).values():
                    visit(child, root, name)
            if isinstance(schema.get("items"), dict):
                visit(schema["items"], root, name)

        names = sorted(os.listdir(J.SCHEMA_DIR))
        self.assertEqual(len(names), len(M.KINDS) + 3)
        for name in names:
            visit(M.REGISTRY.load(name), M.REGISTRY.load(name), name)


@unittest.skipUnless(SUITE, "set JSON_SCHEMA_TEST_SUITE to a checkout of the official test suite")
class OfficialSuite(unittest.TestCase):
    """Official cases for the supported keywords, for schemas inside the subset."""

    def test_official_cases(self):
        ran = 0
        for keyword in ("type", "required", "enum", "pattern", "properties", "items", "additionalProperties", "ref"):
            path = os.path.join(SUITE, "validation", "tests", keyword + ".json")
            with open(path, encoding="utf-8") as handle:
                groups = json.load(handle)["tests"]
            for group in groups:
                if str(group.get("compatibility") or " ").lstrip()[:1] in ("=", "<"):
                    continue  # older drafts only
                for case in group["tests"]:
                    try:
                        valid = not J.validate(case["data"], group["schema"])
                    except J.SchemaError:
                        continue  # outside the subset
                    with self.subTest(keyword=keyword, group=group["description"], case=case["description"]):
                        self.assertEqual(valid, case["valid"])
                        ran += 1
        self.assertGreater(ran, 200)


if __name__ == "__main__":
    unittest.main()
