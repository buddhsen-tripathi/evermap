"""Positive and negative tests for V1 to V16, plus the mutation suite (ROADMAP M1.4)."""

import json
import os
import unittest

from tests import support as S
from evermap_lib import model as M

DEPOSIT, COLLECT, NOTIFY = "operation:parcel.deposit", "operation:parcel.collect", "operation:recipient.notify"
PARCEL, RULE, INV = "entity:parcel", "rule:collection.window", "invariant:one-parcel-per-locker"
FLOW, DECISION, API = "workflow:deposit-parcel", "decision:skip-locked-assignment", "component:locker-api"
NOTIFIER = "component:notifier"


def setter(ident, change):
    return lambda case: case.edit(ident, change)


def put(**fields):
    return lambda value: value.update(fields)


def drop(key):
    return lambda value: value.pop(key)


def at(*keys):
    """Return a function that fetches a nested value."""
    def fetch(value):
        for key in keys:
            value = value[key]
        return value
    return fetch


def nested(_where, **fields):
    return lambda value: at(*_where)(value).update(fields)


def raw(path, text):
    return lambda case: S.put(case.root, path, text)


def write_bytes(case, path, data):
    with open(os.path.join(case.root, *path.split("/")), "wb") as handle:
        handle.write(data)


def sub(path, old, new):
    def change(case):
        text = S.read(case.root, path)
        assert old in text, (path, old)
        S.put(case.root, path, text.replace(old, new, 1))
    return change


def rename_file(old, new):
    def change(case):
        target = os.path.join(case.root, *new.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(os.path.join(case.root, *old.split("/")), target)
    return change


def new_element(value):
    return lambda case: S.write_element(case.root, value)


def manifest(change):
    def run(case):
        value = json.loads(S.read(case.root, M.MANIFEST))
        change(value)
        S.put(case.root, M.MANIFEST, M.format_value(value, "manifest"))
    return run


def overlay(value, proposal=True):
    def change(case):
        base = ".evermap/proposals/try-it/"
        if proposal:
            S.put(case.root, base + "proposal.json",
                  M.format_value({"title": "Try it", "status": "draft"}, "proposal"))
        S.put(case.root, base + M.path_for_id(value["id"])[len(".evermap/"):], M.format_value(value, value["kind"]))
    return change


FILE = {i: M.path_for_id(i) for i in (DEPOSIT, COLLECT, PARCEL, RULE, INV, FLOW, DECISION, API)}
ROLE = {"id": "role:ghost", "kind": "role", "name": "Ghost", "summary": "Test role.", "plain": "A test role.",
        "type": "user-role", "lifecycle": "implemented", "provenance": "inferred", "rationale": "Test."}
# Built at run time so that this file itself contains no credential-shaped strings.
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_GITHUB = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
FAKE_STRIPE = "sk" + "_live_" + "a1B2c3D4e5F6g7H8"
FAKE_JWT = "eyJ" + "hbGciOiJIUzI1" + ".eyJzdWIiOiIxMjM0" + ".sig"
FAKE_RANDOM = "q7Zp2" + "Xv9LmT4" + "wR8kB1nC6sD3"
FAKE_URL = "postgres://app" + ":hunter22@db.example.invalid/x"
FAKE_PEM = "-----BEGIN " + "PRIVATE KEY-----"
FAKE_HEX = "9f86d081884c7d65" + "9a2feaa0c55ad015"

# (name, expected rule, mutation)
MUTATIONS = [
    # V1
    ("truncated JSON", "V1", raw(FILE[RULE], '{"id": "rule:collection.window",')),
    ("duplicate key", "V1", sub(FILE[RULE], '"kind": "rule",', '"kind": "rule",\n  "kind": "rule",')),
    ("NaN literal", "V1", sub(FILE[RULE], '"value": 7', '"value": NaN')),
    ("invalid UTF-8", "V1", lambda c: write_bytes(c, FILE[RULE], b'{"id": "\xff"}')),
    ("unknown field", "V1", setter(RULE, put(confidence=0.9))),
    ("missing summary", "V1", setter(RULE, drop("summary"))),
    ("blank name", "V1", setter(RULE, put(name="  "))),
    ("bad enum", "V1", setter(RULE, put(type="guess"))),
    ("wrong type", "V1", setter(RULE, put(tags="x"))),
    ("missing trigger", "V1", setter(DEPOSIT, drop("trigger"))),
    ("evidence without path", "V1", setter(RULE, lambda v: v["evidence"][0].pop("path"))),
    ("evidence without lines", "V1", setter(RULE, lambda v: v["evidence"][0].pop("lines"))),
    ("evidence path escapes the repository", "V1", setter(RULE, nested(("evidence", 0), path="a/../../x.py"))),
    ("evidence absolute path", "V1", setter(RULE, nested(("evidence", 0), path="/etc/hosts"))),
    ("evidence lines reversed", "V1", setter(RULE, nested(("evidence", 0), lines=[7, 3]))),
    ("evidence excerpt stored", "V1", setter(RULE, nested(("evidence", 0), excerpt="MAX_DAYS = 7"))),
    ("stated evidence without who", "V1", setter(DECISION, lambda v: v["evidence"][0].pop("who"))),
    ("email address in who", "V1", setter(DECISION, nested(("evidence", 0), who="someone@example.invalid"))),
    ("short commit SHA", "V1", setter(RULE, lambda v: v["evidence"].append({"type": "commit", "commit": "abc1234"}))),
    ("overlong note", "V1", setter(RULE, nested(("evidence", 0), note="x" * 201))),
    ("config value recorded", "V1", setter(API, nested(("config", 0), value="x"))),
    ("stray JSON file", "V1", raw(".evermap/model/misc/x.json", "{}\n")),
    ("stray text file", "V1", raw(".evermap/model/rules/notes.txt", "hello\n")),
    ("manifest without scope", "V1", manifest(drop("scope"))),
    ("manifest from a newer major schema", "V1", manifest(put(schema_version="1.0"))),
    ("manifest deleted", "V1", lambda c: os.remove(os.path.join(c.root, ".evermap", "evermap.json"))),
    ("stored derived relation", "V1", setter(DEPOSIT, lambda v: v["relations"].append(
        {"type": "enforces", "target": INV}))),
    ("decision with lifecycle", "V1", setter(DECISION, put(lifecycle="implemented"))),
    # V2
    ("id with uppercase", "V2", sub(FILE[RULE], '"rule:collection.window"', '"rule:Collection.window"')),
    ("id does not match path", "V2", sub(FILE[RULE], '"rule:collection.window"', '"rule:collection.other"')),
    ("kind does not match id", "V2", setter(RULE, put(kind="role"))),
    ("element in the wrong directory", "V2", rename_file(FILE[RULE], ".evermap/model/roles/collection.window.json")),
    ("duplicate id", "V2", lambda c: S.put(c.root, ".evermap/model/rules/copy.json", S.read(c.root, FILE[RULE]))),
    ("alias collides with a live id", "V2", setter(RULE, put(aliases=["rule:collection.window"]))),
    ("alias used twice", "V2", lambda c: [c.edit(DEPOSIT, put(aliases=["operation:old"])),
                                          c.edit(COLLECT, put(aliases=["operation:old"]))]),
    ("duplicate field name", "V2", setter(PARCEL, lambda v: v["fields"].append(dict(v["fields"][0])))),
    ("duplicate step id", "V2", setter(FLOW, lambda v: v["steps"].append(dict(v["steps"][0])))),
    # V3
    ("dangling relation", "V3", setter(DEPOSIT, nested(("relations", 0), target="channel:gone"))),
    ("dangling parent", "V3", setter(DEPOSIT, put(parent="component:gone"))),
    ("parent of the wrong kind", "V3", setter(DEPOSIT, put(parent=PARCEL))),
    ("dangling role", "V3", setter(DEPOSIT, nested(("auth", "authorization", 0), roles=["role:gone"]))),
    ("dangling field in maps_to", "V3", setter(DEPOSIT, nested(("contract", "input", 0), maps_to="entity:parcel#gone"))),
    ("dangling lineage source", "V3", setter(PARCEL, lambda v: [
        o.update({"from": ["entity:locker#gone"]}) for f in v["fields"] if f["name"] == "locker_id" for o in f["origins"]])),
    ("dangling via", "V3", setter(PARCEL, lambda v: [
        o.update({"via": "operation:gone"}) for f in v["fields"] for o in f.get("origins", [])])),
    ("unknown transaction", "V3", setter(DEPOSIT, nested(("relations", 0), in_transaction="tx9"))),
    ("unknown field on a write", "V3", setter(DEPOSIT, nested(("relations", 1), fields=["gone"]))),
    ("relation target of the wrong kind", "V3", setter(COLLECT, lambda v: [
        r.update(target="role:courier") for r in v["relations"] if r["type"] == "applies"])),
    ("relation from the wrong kind", "V3", setter(RULE, put(relations=[{"type": "calls", "target": DEPOSIT}]))),
    ("dangling step operation", "V3", setter(FLOW, nested(("steps", 0), operation="operation:gone"))),
    ("dangling invariant scope", "V3", setter(INV, put(scope=["entity:gone"]))),
    ("dangling trigger channel", "V3", setter(NOTIFY, nested(("trigger",), channel="channel:gone"))),
    ("unknown state in a transition", "V3", setter(PARCEL, lambda v: [
        f["states"]["transitions"][0].update(to="lost") for f in v["fields"] if f.get("states")])),
    ("unknown constraint field", "V3", setter("entity:locker", nested(("constraints", 0), fields=["gone"]))),
    ("circular parents", "V3", lambda c: [c.edit(API, put(parent="component:notifier")),
                                          c.edit("component:notifier", put(parent=API))]),
    ("dangling superseded decision", "V3", setter(DECISION, put(supersedes=["decision:gone"]))),
    ("pending unknown component", "V3", manifest(nested(("coverage",), pending=["component:gone"]))),
    ("element deleted but still referenced", "V3", lambda c: os.remove(os.path.join(c.root, *FILE[RULE].split("/")))),
    # V4
    ("observed without evidence", "V4", setter(RULE, drop("evidence"))),
    ("observed with only stated evidence", "V4", setter(RULE, put(evidence=[
        {"type": "stated", "who": "lead", "date": "2026-01-01", "source": "conversation"}]))),
    ("inferred without rationale", "V4", setter(DEPOSIT, lambda v: v["idempotency"].pop("rationale"))),
    ("stated without stated evidence", "V4", setter(RULE, put(provenance="stated"))),
    ("observed attribute without evidence", "V4", setter(DEPOSIT, lambda v: v["transactions"][0].pop("evidence"))),
    ("observed relation without evidence", "V4", setter(DEPOSIT, nested(("relations", 0), provenance="observed"))),
    ("none-detected without support", "V4", setter(COLLECT, put(idempotency={"mechanism": "none-detected"}))),
    # V5
    ("proposed element in model", "V5", setter(RULE, put(lifecycle="proposed"))),
    ("implemented element in an overlay", "V5", overlay(ROLE)),
    # V6
    ("evidence file missing", "V6", setter(RULE, nested(("evidence", 0), path="api/gone.py"))),
    ("evidence beyond the end of file", "V6", setter(RULE, nested(("evidence", 0), lines=[3, 999]))),
    ("hash missing", "V6", setter(RULE, lambda v: v["evidence"][0].pop("hash"))),
    # V8
    ("step without support", "V8", setter(FLOW, lambda v: v["steps"][1].pop("operation"))),
    ("unknown next step", "V8", setter(FLOW, nested(("steps", 0), next=["s9"]))),
    ("unknown outcome", "V8", setter(FLOW, nested(("steps", 2), outcome="o-gone"))),
    ("failure without an outcome", "V8", setter(FLOW, lambda v: v["steps"][0]["on_failure"][0].pop("outcome"))),
    ("workflow without steps", "V8", setter(FLOW, put(steps=[]))),
    # V9
    ("cloud key in summary", "V9", setter(RULE, put(summary="Uses " + FAKE_AWS))),
    ("source host token in note", "V9", setter(RULE, nested(("evidence", 0), note=FAKE_GITHUB))),
    ("payment key in plain", "V9", setter(RULE, put(plain="The key is " + FAKE_STRIPE))),
    ("web token in rationale", "V9", setter(DEPOSIT, nested(("idempotency",), rationale=FAKE_JWT))),
    ("random string in a parameter", "V9", setter(RULE, nested(("parameters", 0), value=FAKE_RANDOM))),
    ("long hex in a parameter", "V9", setter(RULE, nested(("parameters", 0), value=FAKE_HEX))),
    ("credentials in a URL", "V9", setter(API, put(technology=FAKE_URL))),
    ("private key in a view", "V9", raw(".evermap/views/extra.md", FAKE_PEM + "\n")),
    ("assigned password", "V9", setter(API, put(summary="Connects with password" + "=hunter22x"))),
    # V11
    ("accepted without confirmation", "V11", setter(DECISION, nested(("decision",), provenance="inferred"))),
    ("rejected without confirmation", "V11", setter(DECISION, lambda v: [
        v.update(status="rejected"), v["decision"].update(provenance="unknown")])),
    ("confirmed without stated evidence", "V11", setter(DECISION, drop("evidence"))),
    # V14
    ("conflict markers", "V14", raw(FILE[RULE], "<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> other\n")),
    ("conflict marker in a view", "V14", raw(".evermap/views/README.md", "<<<<<<< HEAD\na\n")),
]

# Rules whose default severity is a warning. (name, rule, mutation, error under --strict)
WARNINGS = [
    ("stale evidence", "V7", sub("api/collect.py", "MAX_DAYS = 7", "MAX_DAYS = 9"), True),
    ("moved evidence", "V7", sub("api/collect.py", "MAX_DAYS = 7", "# limit\nMAX_DAYS = 7"), False),
    ("plain missing", "V10", setter(RULE, drop("plain")), False),
    ("plain too long", "V10", setter(RULE, put(plain="word " * 130)), False),
    ("code in plain", "V10", setter(RULE, put(plain="Calls isExpired() first.")), False),
    ("code in nested plain", "V10", setter(FLOW, nested(("steps", 0), plain="Run check_courier now.")), False),
    ("historical reference", "V12", setter(DECISION, lambda v: v["affects"].append("operation:gone")), False),
    ("unformatted file", "V13", sub(FILE[RULE], '  "kind"', '     "kind"'), True),
    ("view edited by hand", "V15", sub(".evermap/views/README.md", "# Parcel", "# My parcel"), False),
    ("model changed after render", "V15", setter(RULE, put(summary="Changed.")), False),
    ("views deleted", "V15", lambda c: S.shutil.rmtree(os.path.join(c.root, ".evermap", "views")), False),
    ("call against a dependency", "V16", lambda c: [depend(c, API, NOTIFIER), call(c, NOTIFY, DEPOSIT, "sync-call")], False),
    ("call without a mechanism against a dependency", "V16",
     lambda c: [depend(c, API, NOTIFIER), call(c, NOTIFY, DEPOSIT, None)], False),
    ("in-process call across languages", "V16", lambda c: [
        c.edit(NOTIFIER, put(technology="go")), call(c, NOTIFY, DEPOSIT, "in-process")], False),
    ("mutual dependency", "V16", lambda c: [depend(c, API, NOTIFIER), depend(c, NOTIFIER, API)], False),
]


def depend(case, source, target):
    case.edit(source, lambda v: v.setdefault("relations", []).append({"type": "depends_on", "target": target}))


def call(case, source, target, mechanism):
    relation = {"type": "calls", "target": target}
    if mechanism:
        relation["mechanism"] = mechanism
    case.edit(source, lambda v: v.setdefault("relations", []).append(relation))


class RelationDirection(S.RepoCase):
    """V16: warnings only, naming both ids; the right direction stays clean."""

    def v16(self):
        code, findings = self.findings("--strict")
        return code, [f for f in findings if f["rule"] == "V16"]

    def test_right_direction_is_clean(self):
        depend(self, NOTIFIER, API)
        call(self, NOTIFY, DEPOSIT, "sync-call")
        self.assertEqual(self.v16()[1], [])

    def test_other_mechanisms_and_same_technology_are_clean(self):
        depend(self, API, NOTIFIER)
        call(self, NOTIFY, DEPOSIT, "sync-http")  # a webhook back to the caller is legitimate
        call(self, NOTIFY, COLLECT, "async-message")
        self.edit(NOTIFIER, put(technology="Python"))
        call(self, NOTIFY, DEPOSIT, "in-process")
        self.assertEqual([f["message"].split(" ")[1] for f in self.v16()[1]], ["calls"])  # only the reversed one

    def in_process(self, caller, callee):
        self.edit(NOTIFIER, put(technology=caller))
        self.edit(API, put(technology=callee))
        call(self, NOTIFY, DEPOSIT, "in-process")
        return self.v16()[1]

    def test_one_language_family_is_clean(self):
        for caller, callee in (("hono route modules", "typescript"), ("typescript, hono", "typescript"),
                               ("Swift package (macOS 15)", "swift"), ("cloudflare durable objects", "python")):
            with self.subTest(caller=caller, callee=callee):
                self.assertEqual(self.in_process(caller, callee), [])

    def test_different_languages_warn(self):
        for caller, callee in (("go", "python"), ("kotlin", "swift")):
            with self.subTest(caller=caller, callee=callee):
                hits = self.in_process(caller, callee)
                self.assertTrue(hits)  # one per in-process call added so far
                for hit in hits:
                    self.assertIn("languages differ", hit["message"])

    def test_languages_reads_every_word(self):
        from evermap_lib import rules
        cases = {"C++ (17)": {"cpp"}, "C#": {"dotnet"}, "F#,": {"dotnet"}, "Python 3.9": {"python"},
                 "typescript, hono on bun": {"js"}, "Kotlin and Swift": {"jvm", "swift"},
                 "C (99)": set(), "postgres": set(), "": set(), None: set()}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(rules._languages(value), expected)

    def test_calls_inside_one_component_are_clean(self):
        self.edit(API, put(technology="python"))
        call(self, COLLECT, DEPOSIT, "in-process")
        self.assertEqual(self.v16()[1], [])

    def test_messages_name_both_ids_and_never_fail(self):
        depend(self, API, NOTIFIER)
        depend(self, NOTIFIER, API)
        self.edit(NOTIFIER, put(technology="go"))
        call(self, NOTIFY, DEPOSIT, None)
        code, hits = self.v16()
        self.assertEqual(len(hits), 3, hits)
        self.assertEqual({f["severity"] for f in hits}, {"warning"})
        mutual = next(f for f in hits if "each other" in f["message"])
        self.assertIn(API, mutual["message"])
        self.assertIn(NOTIFIER, mutual["message"])
        for hit in (f for f in hits if f is not mutual):
            self.assertEqual(hit["id"], NOTIFY)
            self.assertRegex(hit["at"], r"^\$\.relations\[\d\]$")
            self.assertIn(DEPOSIT, hit["message"])
            self.assertIn("check", hit["message"])
        self.assertFalse([f for f in self.findings("--strict")[1] if f["severity"] == "error" and f["rule"] != "V15"])


class Positive(S.RepoCase):
    def test_fixture_is_clean_in_strict_mode(self):
        code, findings = self.findings("--strict")
        self.assertEqual((code, findings), (0, []))

    def test_valid_variants_stay_clean(self):
        self.edit(RULE, put(provenance="inferred", rationale="Named like a rule."))
        self.edit(COLLECT, put(idempotency={"mechanism": "none-detected", "rationale": "Read the whole handler."}))
        self.edit(DECISION, put(status="proposed", decision={"text": "Maybe.", "provenance": "inferred"}))
        self.edit(INV, lambda v: v["enforcement"].append(
            {"mechanism": "none-detected", "provenance": "inferred", "rationale": "Nothing else found."}))
        S.run(self.root, "render")
        self.assertEqual(self.findings("--strict"), (0, []))

    def test_unknown_provenance_needs_no_evidence(self):
        self.edit(RULE, lambda v: [v.update(provenance="unknown"), v.pop("evidence")])
        S.run(self.root, "render")
        self.assertEqual(self.findings()[1], [])

    def test_overlay_may_shadow_and_add(self):
        overlay(dict(ROLE, lifecycle="proposed"))(self)
        shadow = S.elements()[RULE]
        shadow.update(lifecycle="proposed", provenance="inferred", rationale="Proposal.", statement="ten days")
        shadow.pop("evidence")
        overlay(shadow)(self)
        self.assertEqual(self.findings("--strict"), (0, []))

    def test_imported_adr_counts_as_confirmation(self):
        S.put(self.root, "adr/0001.md", "# Accepted\nUse row locks.\n")
        self.edit(DECISION, put(evidence=[{"type": "doc", "path": "adr/0001.md", "lines": [1, 2]}]), anchor=True)
        self.assertFalse([f for f in self.findings()[1] if f["severity"] == "error"])

    def test_decision_reference_to_an_aliased_id_is_not_historical(self):
        self.edit(RULE, put(aliases=["rule:old-window"]))
        self.edit(DECISION, lambda v: v["affects"].append("rule:old-window"))
        self.assertFalse([f for f in self.findings()[1] if f["rule"] == "V12"])

    def test_ordinary_text_is_not_a_secret(self):
        for text in ("The password must have 12 characters", "Retries createRefundAndNotifyCustomer2 times",
                     "See https://example.invalid/a:b/c", "token: opaque string", "uuid v4 in the Idempotency-Key header",
                     "commit history and sha256:0123456789abcdef anchors"):
            self.edit(RULE, put(summary=text))
            self.assertFalse([f for f in self.findings()[1] if f["rule"] == "V9"], text)


class Mutations(S.RepoCase):
    def test_suite_is_large_enough_and_covers_every_rule(self):
        self.assertGreaterEqual(len(MUTATIONS), 50)
        covered = {m[1] for m in MUTATIONS} | {w[1] for w in WARNINGS}
        self.assertEqual(covered, {"V%d" % n for n in range(1, 17)})

    def test_findings_never_print_the_secret(self):
        self.edit(RULE, put(summary="Uses " + FAKE_AWS))
        _, out, err = S.run(self.root, "validate", "--max-chars", "0")
        self.assertNotIn(FAKE_AWS, out.decode() + err)


def _error_test(rule, mutate):
    def test(self):
        mutate(self)
        code, findings = self.findings()
        hits = [f for f in findings if f["rule"] == rule and f["severity"] == "error"]
        self.assertTrue(hits, findings)
        self.assertEqual(code, 1)
    return test


def _warning_test(rule, mutate, strict_error):
    def test(self):
        mutate(self)
        _, findings = self.findings()
        hits = [f for f in findings if f["rule"] == rule]
        self.assertTrue(hits, findings)
        self.assertEqual({f["severity"] for f in hits}, {"warning"})
        code, findings = self.findings("--strict")
        hits = {f["severity"] for f in findings if f["rule"] == rule}
        self.assertEqual(hits, {"error" if strict_error else "warning"})
        if strict_error:
            self.assertEqual(code, 1)
    return test


for _name, _rule, _mutate in MUTATIONS:
    setattr(Mutations, "test_%s_%s" % (_rule, _name.replace(" ", "_")), _error_test(_rule, _mutate))
for _name, _rule, _mutate, _strict in WARNINGS:
    setattr(Mutations, "test_%s_%s" % (_rule, _name.replace(" ", "_")), _warning_test(_rule, _mutate, _strict))


if __name__ == "__main__":
    unittest.main()
