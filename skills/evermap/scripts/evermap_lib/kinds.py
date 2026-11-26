"""The diagram kinds the user chooses (manifest views.kinds): sequence, er, state and access.

Each is built from model data only, deterministic and capped like the flowcharts; a section that
would hold no diagram is left out. render.py emits the values (sequence, er, state, diagram).
"""

from . import render as V

PERSISTED = ("table", "collection", "document")
MAX_MESSAGES = 40
ACCESS = ("reads", "writes", "deletes")
DATA_MODEL = V.VIEWS + "data-model.md"


def _draw(value, page):
    return page.add(value) if page is not None else V.emit_mermaid(value)


def _name(model, ident):
    element = model.get(ident or "") or {}
    return element.get("name") or element.get("title") or str(ident)


def _parent(model, ident):
    """The component that owns an operation or entity, when it exists."""
    parent = (model.get(ident or "") or {}).get("parent")
    return parent if parent and (model.get(parent) or {}).get("kind") == "component" else None


def _relations(element):
    return [r for r in element.get("relations") or [] if isinstance(r, dict) and isinstance(r.get("target"), str)]


def _publishers(model, channel):
    """Components whose operations publish to the channel, sorted."""
    return sorted({_parent(model, o["id"]) for o in model.of_kind("operation")
                   if any(r.get("type") == "publishes" and r["target"] == channel for r in _relations(o))} - {None})


# --- sequence -----------------------------------------------------------------

def _step_note(model, step):
    """(note text, rule names) of a step: its rules, then branch and failure conditions."""
    rules = ", ".join(_name(model, r) for r in step.get("rules") or [] if isinstance(r, str))
    conditions = [str(b.get("condition_plain", "")) for b in step.get("branches") or [] if isinstance(b, dict)]
    conditions += ["fails: " + str(b.get("plain", "")) for b in step.get("on_failure") or [] if isinstance(b, dict)]
    conditions = " / ".join(c for c in conditions if c.strip())
    return (rules + ": " + conditions if rules and conditions else rules or conditions), rules


def sequence_value(model, workflow):
    """(value, total messages, total participants) of a workflow's sequence diagram."""
    actors = [a for a in workflow.get("actors") or [] if isinstance(a, str)]
    first = actors[0] if actors else None
    events, components, channels = [], [], []   # ("m", message) or ("n", (participant, text, rule))

    def seen(ident):
        bucket = channels if (model.get(ident) or {}).get("kind") == "channel" else components
        if ident not in bucket and ident not in actors:
            bucket.append(ident)

    def say(source, target, text, dashed):
        seen(source)
        seen(target)
        events.append(("m", (source, target, text, dashed)))

    current = first
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        op = model.get(step.get("operation") or "")
        here = _parent(model, op["id"]) if op and op.get("kind") == "operation" else None
        if here:
            trigger = op.get("trigger") if isinstance(op.get("trigger"), dict) else {}
            kind, channel = trigger.get("type"), trigger.get("channel")
            detail = trigger.get("detail") or (_name(model, channel) if channel else "")
            text = op["name"] + (" (%s)" % detail if detail else "")
            if kind in ("http", "rpc", "webhook", "cli") and first:
                say(first, here, text, False)
            elif channel and model.get(channel):
                source = (_publishers(model, channel) or [channel])[0]
                say(source, here, text, True)
            elif kind in ("schedule", "startup") or not current:
                say(here, here, text, False)
            else:
                say(current, here, text, False)
            for rel in _relations(op):
                target = model.get(rel["target"]) or {}
                if rel.get("type") == "calls" and _parent(model, rel["target"]):
                    mechanism = str(rel.get("mechanism", ""))
                    say(here, _parent(model, rel["target"]), target.get("name", rel["target"]),
                        mechanism.startswith("async-"))
                elif rel.get("type") in ACCESS and _parent(model, rel["target"]):
                    say(here, _parent(model, rel["target"]), "%s %s" % (rel["type"], target.get("name", "")), False)
                elif rel.get("type") == "publishes" and target.get("kind") == "channel":
                    message = _name(model, rel["message"]) if rel.get("message") else target.get("name", "")
                    say(here, rel["target"], "publishes " + message, True)
            current = here
        noted = (not here and step.get("rules")) or step.get("branches") or step.get("on_failure")
        note, rules = _step_note(model, step) if noted else ("", "")
        if note and current:
            seen(current)
            events.append(("n", (current, note, "" if here else rules)))
    order = [(a, _name(model, a), True) for a in actors] + [(c, _name(model, c), False) for c in components + channels]
    shown = order[:V.MAX_NODES]
    visible = {p[0] for p in shown}
    messages, notes = [], []
    for kind, item in events:
        if kind == "m" and {item[0], item[1]} <= visible:
            if len(messages) == MAX_MESSAGES:
                break
            messages.append(item)
        elif kind == "n" and item[0] in visible:
            notes.append((len(messages),) + item)
    total = sum(kind == "m" for kind, _ in events)
    value = V.sequence("Workflow: %s - sequence" % workflow["name"], shown, messages, notes)
    return value, total, len(order)


def sequence_section(model, workflow, page=None):
    value, total, people = sequence_value(model, workflow)
    if not value["messages"] and not value["notes"]:
        return []
    out = ["## Sequence", ""] + _draw(value, page) + [""]
    out += V._left_out(len(value["participants"]), people, "participants")
    return out + V._left_out(len(value["messages"]), total, "messages")


# --- access and events --------------------------------------------------------

def _fields(rel):
    names = [str(f) for f in rel.get("fields") or [] if isinstance(f, str)]
    return ": " + ", ".join(names[:3]) + (", ..." if len(names) > 3 else "") if names else ""


def access_section(model, component, operations, page=None):
    """The component's operations, the entities they read, write or delete and the channels they use."""
    own = [o["id"] for o in operations]
    targets, edges = [], []
    for op in operations:
        for rel in _relations(op):
            target = model.get(rel["target"]) or {}
            if rel.get("type") in ACCESS and target.get("kind") == "entity":
                edges.append((op["id"], rel["target"], rel["type"] + _fields(rel)))
            elif rel.get("type") == "publishes" and target.get("kind") == "channel":
                edges.append((op["id"], rel["target"], "publishes"))
            elif rel.get("type") == "consumes" and target.get("kind") == "channel":
                edges.append((rel["target"], op["id"], "consumes"))
            else:
                continue
            if rel["target"] not in targets and rel["target"] not in own:
                targets.append(rel["target"])
    if not edges:
        return []
    nodes = [(o["id"], o["name"]) for o in operations] + [(t, _name(model, t)) for t in sorted(targets)]
    value = V.diagram("Component: %s - data access" % component["name"], nodes[:V.MAX_NODES], edges)
    out = ["## Data access", ""] + _draw(value, page) + [""]
    return out + V._left_out(min(len(nodes), V.MAX_NODES), len(nodes), "operations, entities and channels")


def events_section(model, page=None):
    """Publisher component -> channel -> consumer component, for every channel with either."""
    edges = set()
    for op in model.of_kind("operation"):
        owner = _parent(model, op["id"])
        for rel in _relations(op):
            if owner and (model.get(rel["target"]) or {}).get("kind") == "channel":
                if rel.get("type") == "publishes":
                    edges.add((owner, rel["target"], "publishes"))
                elif rel.get("type") == "consumes":
                    edges.add((rel["target"], owner, "consumes"))
    if not edges:
        return []
    used = {n for a, b, _ in edges for n in (a, b)}
    nodes = [(c["id"], c["name"]) for c in model.of_kind("component") if c["id"] in used]
    nodes += [(c["id"], c["name"]) for c in model.of_kind("channel") if c["id"] in used]
    system = (model.manifest.get("system") or {}).get("name", "System")
    value = V.diagram("System: %s - events" % system, nodes[:V.MAX_NODES], sorted(edges))
    out = ["## Events", ""] + _draw(value, page) + [""]
    return out + V._left_out(min(len(nodes), V.MAX_NODES), len(nodes), "components and channels")


# --- er and state -------------------------------------------------------------

def persisted(model):
    return [e for e in model.of_kind("entity") if e.get("type") in PERSISTED]


def _constraints(entity, kind):
    return [c for c in entity.get("constraints") or [] if isinstance(c, dict) and c.get("type") == kind]


def _attributes(entity):
    keys = {}
    for kind, mark in (("primary-key", "PK"), ("foreign-key", "FK"), ("unique", "UK")):
        for item in _constraints(entity, kind):
            for name in item.get("fields") or []:
                keys.setdefault(name, [])
                if mark not in keys[name]:
                    keys[name].append(mark)
    return [(f["name"], f.get("type") or "unknown", tuple(keys.get(f["name"], ())),
             "pii" if f.get("sensitivity") == "pii" else "")
            for f in entity.get("fields") or [] if isinstance(f, dict) and isinstance(f.get("name"), str)]


def _links(model, entity):
    """(target entity id, fields, near card, far card, target field) for each foreign key with references."""
    found = []
    nullable = {f.get("name") for f in entity.get("fields") or [] if isinstance(f, dict) and f.get("nullable")}
    unique = [set(c.get("fields") or []) for c in _constraints(entity, "unique")]
    for item in _constraints(entity, "foreign-key"):
        ref = item.get("references")
        if not isinstance(ref, str) or (model.get(ref.split("#", 1)[0]) or {}).get("kind") != "entity":
            continue
        fields = [f for f in item.get("fields") or [] if isinstance(f, str)]
        near = "zero-or-one" if fields and set(fields) in unique else "many"
        far = "zero-or-one" if nullable & set(fields) else "one"
        found.append((ref.split("#", 1)[0], fields, near, far, ref.split("#", 1)[1] if "#" in ref else None))
    return found


def er_values(model):
    """[(heading, value, cross-diagram lines, total entities)] per datastore, then the parentless ones."""
    groups = {}
    for entity in persisted(model):
        groups.setdefault(_parent(model, entity["id"]), []).append(entity)
    names = {c["id"]: c["name"] for c in model.of_kind("component")}
    keys = [c for c in names if c in groups] + ([None] if None in groups else [])
    out = []
    for owner in keys:
        members = groups[owner]
        shown = members[:V.MAX_NODES]
        inside = {e["id"] for e in shown}
        heading = names[owner] if owner else "No datastore recorded"
        rels, outside = [], []
        for entity in shown:
            for target, fields, near, far, field in _links(model, entity):
                if target in inside:
                    rels.append((entity["id"], target, near, far, fields, field))
                else:
                    where = _parent(model, target)
                    outside.append("- %s (%s) references %s in %s." % (
                        V.text(entity["name"]), ", ".join(V.code(f) for f in fields) or "no fields",
                        V.text(_name(model, target)), V.text(names[where]) if where else "no datastore"))
        entities = [(e["id"], e["name"], _attributes(e)) for e in shown]
        if not rels and not any(attrs for _, _, attrs in entities) and not outside:
            continue
        value = V.er("Data model: %s" % heading, entities, rels)
        out.append((heading, value, outside, len(members)))
    return out


def state_values(model):
    """[(entity, field name, value, total states)] for each field with states."""
    out = []
    for entity in model.of_kind("entity"):
        for field in entity.get("fields") or []:
            states = field.get("states") if isinstance(field, dict) else None
            values = [v for v in (states or {}).get("values") or [] if isinstance(v, str)] if isinstance(
                states, dict) else []
            if not values:
                continue
            edges = []
            for move in states.get("transitions") or []:
                if not isinstance(move, dict):
                    continue
                text = _name(model, move["via"]) if move.get("via") else ""
                if move.get("guard"):
                    text = (text + " " if text else "") + "[%s]" % _name(model, move["guard"])
                edges.append((move.get("from"), move.get("to"), text))
            title = "States: %s %s" % (entity["name"], field["name"])
            out.append((entity, field["name"], V.state(title, values[:V.MAX_NODES], edges), len(values)))
    return out


def data_model_page(model, chosen, page=None):
    """The text of views/data-model.md, or None when no chosen kind draws anything there."""
    out = [V.MARKER, "", "# Data model", "",
           "Persisted entities by datastore and the states of status fields. Back to the [index](README.md).", ""]
    drawn = False
    if "er" in chosen:
        for heading, value, outside, total in er_values(model):
            drawn = True
            out += ["## " + V.text(heading), ""] + _draw(value, page) + [""]
            out += V._left_out(len(value["entities"]), total, "entities")
            if outside:
                out += ["References to entities in other diagrams:", ""] + outside + [""]
    if "state" in chosen:
        values = state_values(model)
        if values:
            drawn = True
            out += ["## States", ""]
            for entity, field, value, total in values:
                out += ["### %s %s" % (V.text(entity["name"]), V.code(field)), ""] + _draw(value, page) + [""]
                out += V._left_out(len(value["nodes"]), total, "states")
    return "\n".join(out).rstrip("\n") + "\n" if drawn else None


# --- what the chosen kinds are missing ------------------------------------------

def thin(model, chosen):
    """A short reason for each chosen kind that the model has little or no data for."""
    out = []
    if "sequence" in chosen and not any(sequence_value(model, w)[0]["messages"] for w in model.of_kind("workflow")):
        out.append("sequence: no workflow step names an operation of a component")
    if "er" in chosen:
        entities = persisted(model)
        with_fields = sum(1 for e in entities if e.get("fields"))
        if not entities:
            out.append("er: no persisted entities recorded")
        elif with_fields < len(entities):
            out.append("er: %d of %d persisted entities have fields" % (with_fields, len(entities)))
    if "state" in chosen and not state_values(model):
        out.append("state: no field records states")
    if "access" in chosen and not any(r.get("type") in ACCESS + ("publishes", "consumes")
                                      for o in model.of_kind("operation") for r in _relations(o)):
        out.append("access: no operation reads, writes, deletes, publishes or consumes anything")
    return out
