"""Shared helpers: builds a small parcel-locker repository with a valid model."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SKILL = os.path.join(REPO, "skills", "evermap")
SCRIPT = os.path.join(SKILL, "scripts", "evermap.py")
SOURCE = os.path.join(HERE, "fixtures-mini", "parcels")
GOLDEN = os.path.join(HERE, "golden")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

from evermap_lib import evidence as E  # noqa: E402
from evermap_lib import model as M  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(root, *args):
    result = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false"] + list(args), cwd=root,
        env=dict(os.environ, **GIT_ENV), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout.decode()


def commit(root, message="change"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD").strip()


def run(root, *args, **env):
    """(exit code, stdout bytes, stderr text). Keyword arguments are extra environment variables."""
    result = subprocess.run([sys.executable, SCRIPT] + list(args), cwd=root, env=dict(os.environ, **env),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode, result.stdout, result.stderr.decode("utf-8", "replace")


def run_json(root, *args, **env):
    code, out, err = run(root, *args, **env)
    try:
        return code, json.loads(out.decode("utf-8"))
    except ValueError:
        raise AssertionError("exit %d, no JSON from %s: %s" % (code, args, err))


def read(root, path):
    with open(os.path.join(root, *path.split("/")), encoding="utf-8", newline="") as handle:
        return handle.read()


def put(root, path, text):
    full = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def tree(root, sub=""):
    """{relative path: bytes} for every file below root/sub, .git excluded."""
    files = {}
    for folder, dirs, names in os.walk(os.path.join(root, sub)):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            full = os.path.join(folder, name)
            with open(full, "rb") as handle:
                files[os.path.relpath(full, root).replace(os.sep, "/")] = handle.read()
    return files


def ev(path, lines, symbol=None, kind="code"):
    item = {"type": kind, "path": path, "lines": lines}
    if symbol:
        item["symbol"] = symbol
    return [item]


def element(ident, name, summary, **fields):
    base = {"id": ident, "kind": ident.split(":")[0], "name": name, "summary": summary,
            "lifecycle": "implemented", "provenance": "observed"}
    base.update(fields)
    return base


ELEMENTS = [
    element("component:locker-api", "Locker API", "HTTP service for deposits and collection.",
            plain="Lets couriers drop parcels off and recipients pick them up.", type="service",
            technology="python", paths=["api/**"], responsibilities=["Deposits", "Collection"],
            config=[{"name": "DATABASE_URL", "kind": "env", "purpose": "Database location"}],
            evidence=ev("api/routes.py", [6, 8], "register"),
            relations=[{"type": "depends_on", "target": "component:locker-db", "mechanism": "sql"}]),
    element("component:notifier", "Notifier", "Worker that consumes stored-parcel events.",
            plain="Tells recipients their parcel has arrived.", type="worker", paths=["worker/**"],
            evidence=ev("worker/notify.py", [4, 6], "on_parcel_stored")),
    element("component:locker-db", "Locker database", "Relational store for lockers and parcels.",
            plain="Where lockers and parcels are recorded.", type="datastore", paths=["db/**"],
            evidence=ev("db/schema.sql", [1, 4], "lockers", "schema")),
    element("entity:locker", "Locker", "One physical locker.", parent="component:locker-db", type="table",
            fields=[{"name": "id", "type": "integer"}, {"name": "free", "type": "boolean"}],
            constraints=[{"type": "primary-key", "fields": ["id"]}],
            evidence=ev("db/schema.sql", [1, 4], "lockers", "schema")),
    element("entity:parcel", "Parcel", "A parcel held in a locker.", parent="component:locker-db", type="table",
            fields=[
                {"name": "id", "type": "text", "origins": [{"type": "user-input", "via": "operation:parcel.deposit"}]},
                {"name": "locker_id", "type": "integer", "origins": [
                    {"type": "copied", "from": ["entity:locker#id"], "via": "operation:parcel.deposit"}]},
                {"name": "pickup_code", "type": "text", "sensitivity": "credential-reference"},
                {"name": "recipient", "type": "text", "sensitivity": "pii"},
                {"name": "status", "type": "text", "states": {
                    "values": ["stored", "collected"],
                    "transitions": [{"from": "stored", "to": "collected", "via": "operation:parcel.collect",
                                     "guard": "rule:collection.window"}]}}],
            evidence=ev("db/schema.sql", [6, 12], "parcels", "schema")),
    element("entity:parcel-stored", "Parcel stored event", "Message announcing a stored parcel.", type="message",
            fields=[{"name": "parcel_id", "type": "text", "origins": [
                {"type": "copied", "from": ["entity:parcel#id"], "via": "operation:parcel.deposit"}]}],
            evidence=ev("api/deposit.py", [11, 11], "parcel.stored")),
    element("channel:parcel-outbox", "Parcel outbox", "Outbox rows for parcel events.", type="outbox",
            message="entity:parcel-stored", delivery="at-least-once", ordering="unknown",
            evidence=ev("db/schema.sql", [14, 17], "outbox", "schema")),
    element("operation:parcel.deposit", "Deposit parcel", "Assigns a free locker and stores the parcel.",
            plain="A courier puts a parcel in a free locker.", parent="component:locker-api",
            trigger={"type": "http", "detail": "POST /parcels"}, exposure="public",
            contract={"input": [{"name": "id", "type": "text", "required": True, "source": "body",
                                 "maps_to": "entity:parcel#id"}],
                      "output": [{"name": "locker", "type": "integer", "maps_to": "entity:locker#id"}],
                      "errors": [{"condition": "caller is not a courier", "signal": "throws PermissionError",
                                  "plain": "Only couriers may do this."}]},
            auth={"authentication": "required", "mechanism": "request roles", "authorization": [
                {"rule": "role is courier", "roles": ["role:courier"], "ownership_check": False,
                 "provenance": "observed", "evidence": ev("api/deposit.py", [5, 6])}]},
            transactions=[{"id": "tx1", "mechanism": "db.transaction", "isolation": "default",
                           "provenance": "observed", "evidence": ev("api/deposit.py", [7, 11])}],
            concurrency=[{"resource": "entity:locker", "mechanism": "row-lock", "provenance": "observed",
                          "evidence": ev("api/deposit.py", [8, 8])}],
            idempotency={"mechanism": "none-detected", "provenance": "inferred",
                         "rationale": "No key or prior lookup in the handler."},
            evidence=ev("api/deposit.py", [4, 12], "deposit_parcel"),
            relations=[
                {"type": "writes", "target": "entity:parcel", "fields": ["id", "status"], "in_transaction": "tx1"},
                {"type": "writes", "target": "entity:locker", "fields": ["free"], "in_transaction": "tx1"},
                {"type": "publishes", "target": "channel:parcel-outbox", "message": "entity:parcel-stored",
                 "in_transaction": "tx1"}]),
    element("operation:parcel.collect", "Collect parcel", "Checks the code and releases the locker.",
            plain="A recipient enters a code and takes the parcel.", parent="component:locker-api",
            trigger={"type": "http", "detail": "POST /parcels/<id>/collect"}, exposure="public",
            auth={"authentication": "none"},
            concurrency=[{"resource": "entity:parcel#status", "mechanism": "conditional-update",
                          "provenance": "observed", "evidence": ev("api/collect.py", [16, 16])}],
            evidence=ev("api/collect.py", [10, 17], "collect_parcel"),
            relations=[
                {"type": "reads", "target": "entity:parcel", "fields": ["pickup_code", "status"]},
                {"type": "writes", "target": "entity:parcel", "fields": ["status"]},
                {"type": "writes", "target": "entity:locker", "fields": ["free"]},
                {"type": "applies", "target": "rule:collection.window"}]),
    element("operation:recipient.notify", "Notify recipient", "Consumes stored events and sends a message.",
            plain="The recipient hears that the parcel is ready.", parent="component:notifier",
            trigger={"type": "message", "channel": "channel:parcel-outbox"}, exposure="internal",
            evidence=ev("worker/notify.py", [4, 6], "on_parcel_stored"),
            relations=[{"type": "consumes", "target": "channel:parcel-outbox"},
                       {"type": "reads", "target": "entity:parcel", "fields": ["recipient"]}]),
    element("rule:collection.window", "Collection window", "Parcels expire after a fixed number of days.",
            plain="A parcel can only be collected for seven days.", type="eligibility",
            statement="days since storage must not exceed MAX_DAYS",
            parameters=[{"name": "max_days", "value": 7}],
            evidence=ev("api/collect.py", [3, 7], "is_expired")),
    element("invariant:one-parcel-per-locker", "One parcel per locker", "A locker never holds two parcels.",
            plain="Two parcels never share a locker.", statement="at most one stored parcel per locker",
            category="uniqueness", scope=["entity:locker", "entity:parcel#locker_id"],
            enforcement=[{"mechanism": "lock", "where": "operation:parcel.deposit", "provenance": "observed",
                          "evidence": ev("api/deposit.py", [8, 9])}],
            verified_by=ev("tests/test_deposit.py", [1, 2], "test_one_parcel_per_locker", "test"),
            criticality="high", evidence=ev("api/deposit.py", [7, 11])),
    element("role:courier", "Courier", "Delivery person.", plain="Someone who delivers parcels.",
            type="user-role", evidence=ev("api/deposit.py", [5, 5])),
    element("workflow:deposit-parcel", "Deposit a parcel", "From drop-off to notification.",
            plain="How a parcel gets into a locker and the recipient hears about it.", actors=["role:courier"],
            trigger_plain="A courier arrives with a parcel.", data_collected=["entity:parcel#id"],
            steps=[{"id": "s1", "plain": "Check the person is a courier.", "operation": "operation:parcel.deposit",
                    "next": ["s2"], "on_failure": [{"plain": "Refused.", "outcome": "o-denied"}]},
                   {"id": "s2", "plain": "Put the parcel in a free locker.", "operation": "operation:parcel.deposit",
                    "next": ["s3"]},
                   {"id": "s3", "plain": "Tell the recipient.", "operation": "operation:recipient.notify",
                    "outcome": "o-stored"}],
            outcomes=[{"id": "o-stored", "type": "success", "plain": "Parcel stored and recipient told."},
                      {"id": "o-denied", "type": "failure", "plain": "The drop-off is refused."}],
            evidence=ev("api/deposit.py", [4, 12], "deposit_parcel")),
    {"id": "decision:skip-locked-assignment", "kind": "decision", "title": "Assign lockers with SKIP LOCKED",
     "status": "accepted", "date": "2026-01-01",
     "problem": {"text": "Concurrent deposits picked the same locker.", "provenance": "confirmed"},
     "options": [{"name": "Skip locked rows", "outcome": "chosen"},
                 {"name": "Global mutex", "outcome": "rejected",
                  "reasons_rejected": {"text": "Serialises every deposit.", "provenance": "confirmed"}}],
     "decision": {"text": "Use row locks and skip locked rows.", "provenance": "confirmed"},
     "affects": ["operation:parcel.deposit", "entity:locker"],
     "evidence": [{"type": "stated", "who": "tech-lead", "date": "2026-01-01", "source": "conversation"}]},
]


def elements():
    return {e["id"]: copy.deepcopy(e) for e in ELEMENTS}


def write_element(root, value, path=None):
    put(root, path or M.path_for_id(value["id"]), M.format_value(value, value.get("kind")))


def load(root):
    return M.load_worktree(root)


def anchor_all(root):
    model = load(root)
    result = E.anchor(root, model)
    assert not result["problems"], result["problems"]


def build(root, with_model=True):
    """Copy the sources, commit them, add an anchored model, sync, render, commit."""
    shutil.copytree(SOURCE, root, dirs_exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    commit(root, "base")
    if not with_model:
        return root
    code, _ = run_json(root, "init", "--name", "Parcel lockers", "--summary", "Locker deposits and collection.",
                       "--plain", "Holds parcels until people pick them up.")
    assert code == 0
    for value in ELEMENTS:
        write_element(root, copy.deepcopy(value))
    anchor_all(root)
    assert run(root, "render")[0] == 0
    commit(root, "model")
    return root


_TEMPLATE = None


def _template():
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = tempfile.mkdtemp(prefix="evermap-template-")
        build(_TEMPLATE)
        import atexit
        atexit.register(shutil.rmtree, _TEMPLATE, True)
    return _TEMPLATE


class RepoCase(unittest.TestCase):
    """Gives each test its own copy of the modelled fixture in self.root."""

    maxDiff = None

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="evermap-test-"))
        self.root = os.path.join(self.base, "parcels")
        shutil.copytree(_template(), self.root, symlinks=True)
        self.addCleanup(shutil.rmtree, self.base, True)

    def edit(self, ident, change, anchor=False):
        path = M.path_for_id(ident)
        value = json.loads(read(self.root, path))
        change(value)
        write_element(self.root, value, path)
        if anchor:
            anchor_all(self.root)

    def findings(self, *args):
        code, out = run_json(self.root, "validate", "--limit", "0", "--max-chars", "0", *args)
        return code, out["findings"]
