# Evermap Canonical Application Model — Proposed Schema

Status: Draft v0.1 (schema version `0.1`) · 2026-11-25

The canonical model is Evermap's primary artifact. Every diagram, the technical view, the
functional view, change summaries and impact reports are rendered from it or computed over it.
This document defines the model's on-disk layout, identifiers, epistemic fields, evidence
format, element kinds, relations and validation rules.

Examples use the EverBench booking domain for readability. The skill's own reference files must
use a *different* domain, so the skill is not tuned to the benchmark (BENCHMARK §8.4).

---

## 1. Design goals

1. **Evidence-first.** A claim without evidence must be labelled `inferred`, `stated` or
   `unknown`. It may never pose as observed.
2. **Two orthogonal axes** on every claim: *lifecycle* (implemented, proposed, …) and
   *provenance* (observed, inferred, stated, unknown). Together they meet the requirement to
   distinguish implemented, proposed and inferred behaviour.
3. **Git-friendly.** One small, canonically formatted JSON file per element, so diffs and merges
   stay local to the element that changed.
4. **Stable identity.** IDs name *concepts*, not code locations. Renaming or moving code does not
   change an ID.
5. **Selective, not exhaustive.** The model holds *architecturally significant* elements
   (SPEC §5.2). It is not a symbol index.
6. **One source for two audiences.** Plain-language text is stored on the same elements as the
   technical facts, so the functional view cannot say anything the model does not.
7. **Deterministically checkable.** Every structural rule in §9 can be checked by a
   standard-library script without semantic judgement.

## 2. On-disk layout (in the mapped application's repository)

```
.evermap/
├── evermap.json                 # manifest (§8)
├── model/                       # canonical model: one JSON file per element
│   ├── components/<slug>.json
│   ├── operations/<slug>.json
│   ├── entities/<slug>.json
│   ├── channels/<slug>.json
│   ├── workflows/<slug>.json
│   ├── rules/<slug>.json
│   ├── invariants/<slug>.json
│   └── roles/<slug>.json
├── decisions/<slug>.json        # decision records (kept apart: historical, never regenerated)
├── proposals/<slug>/            # optional overlays for proposed changes (§7)
├── draft.json                   # transient: elements a worker wrote for `apply` (SPEC §6.3); never an element
├── plan.json                    # transient: worker packets from `estimate --write-plan`; deleted by `sync`
└── views/                       # GENERATED from the model; never edited by hand
    ├── README.md                # index and system overview
    ├── technical/…              # per-component pages and Mermaid diagrams
    ├── functional/…             # per-workflow plain-language pages
    └── decisions/…              # MADR-compatible renders of decision records
```

Decisions live outside `model/` on purpose. A full regeneration rewrites `model/` (with ID
reconciliation) but **never** deletes or rewrites `decisions/` (SPEC §5.5).

### 2.1 Canonical JSON formatting

The `fmt` script owns formatting. Agents do not hand-format. The rules:

- UTF-8, LF line endings, 2-space indent, trailing newline.
- Object keys in **schema order**: `id`, `kind`, `name`, `summary`, `plain`, then the remaining
  fields in a fixed per-kind order. This keeps the important fields at the top of diffs.
- Arrays of objects are sorted by a stable key where order carries no meaning: relations by
  `(type, target)`, evidence by `(path, lines)`, fields by `name`. Arrays where order matters,
  such as workflow steps, keep their authored order.
- No timestamps or run identifiers anywhere, except the human-provided `date` on decisions and
  `stated` evidence. Repeated runs on unchanged code must produce byte-identical files.

## 3. Identifiers

```
id      := kind ":" slug
kind    := "component" | "operation" | "entity" | "channel" | "workflow"
         | "rule" | "invariant" | "role" | "decision"
slug    := segment ("." segment)*            # dots express scope
segment := [a-z0-9]+ ("-" [a-z0-9]+)*
```

Examples: `component:booking-service`, `operation:booking.cancel`, `entity:booking`,
`rule:cancellation.full-refund-window`, `decision:use-transactional-outbox`.

- **File path** = `.evermap/model/<kind-dir>/<slug>.json`, where `<kind-dir>` is the directory
  named in §2 (the plural of the kind, so `entities` for `entity`). Decisions are the exception:
  `.evermap/decisions/<slug>.json`. The validator enforces this. Slugs contain no `/` or `:`, so
  paths are safe on every OS.
- **Sub-element references** use `#`. `entity:booking#status` is a field. `workflow:cancel-booking#s3`
  is a workflow step. `operation:booking.cancel#tx1` is a transaction boundary.
- **Assignment:** chosen once, when the element is created, from the concept's domain name, not
  from a file or function name. Examples: `operation:booking.cancel`, not
  `operation:cancel-booking-handler-ts`.
- **Stability:** an ID never changes because code was renamed or moved. If the *concept* itself
  is renamed, split or merged, the new element lists the old IDs in `aliases` so references and
  history can be followed. Retired IDs are never reused.
- **Reconciliation** during full regeneration matches rediscovered elements to existing IDs
  (SPEC §5.4). The `reconcile` script proposes matches by evidence overlap (same path and symbol,
  overlapping line ranges) and kind. The agent confirms ambiguous matches.

## 4. Epistemic fields (on every element, relation and annotated attribute)

| Field | Values | Meaning |
| --- | --- | --- |
| `lifecycle` | `implemented` | Exists in the code at the synced commit. |
| | `proposed` | Exists only in a proposal overlay (§7). **Forbidden inside `model/`.** |
| | `deprecated` | Implemented, but marked for removal by code annotations or a confirmed decision. |
| `provenance` | `observed` | Evidence directly shows the claim. At least one evidence item is required. |
| | `inferred` | Reasoned from indirect evidence: naming, convention, dynamic dispatch, configuration, tests. Requires `rationale`, and should cite the evidence it reasoned from. |
| | `stated` | Asserted by a human: a confirmed decision, a declared invariant, or a user statement during mapping. Requires `stated`-type evidence (§5). |
| | `unknown` | Expected but not determinable from the available evidence. The element exists to record the gap, for example "authorization for this route could not be determined". |
| `rationale` | string | Why an `inferred` claim was made. Required when `provenance = inferred`. |

`decision` records do not use these two fields. They carry `status` and per-section provenance
instead (§6.9).

**Inheritance:** a relation or attribute without its own `provenance` inherits the element's.
An `observed` element may contain `inferred` attributes. For example, an endpoint can be
observed while its authorization rule is inferred from middleware ordering.

**Removed behaviour** is not kept in `model/`. When code is deleted, the element file is deleted,
and Git history and model diffs (SPEC §5.7) preserve the record. Decisions that reference removed
elements keep the reference and are flagged (§9, rule V12).

## 5. Evidence

```json
{
  "type": "code",
  "path": "services/booking/src/cancel.ts",
  "symbol": "cancelBooking",
  "lines": [42, 88],
  "hash": "sha256:3f1c9a0b7d2e4f61",
  "note": "status update and refund request share one DB transaction"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `type` | yes | `code`, `config`, `schema` (DDL/migrations/ORM models), `test`, `doc` (existing docs or ADRs in the repo), `commit`, `stated`. |
| `path` | for file types | Repo-relative POSIX path. |
| `symbol` | recommended | Function, class, route, table or other named anchor. Used to relocate the span after edits. |
| `lines` | for file types | `[start, end]`, 1-based and inclusive. Must be valid at the synced commit. |
| `hash` | for file types | **Filled in by the `anchor` script, never by the agent.** It is the first 16 hex characters of SHA-256 over the span after normalisation (LF line endings, trailing whitespace stripped). |
| `note` | optional | ≤ 200 characters. Says what the evidence shows. |
| `commit` | for `commit` type | Full SHA. `note` summarises the relevant part of the message. |
| `who`, `date`, `source` | for `stated` type | `who` is a role or handle, **never an email address**. `date` is ISO 8601 and provided by the human. `source` is `conversation`, a repo path, or an issue or PR URL. |

**No code excerpts or literal values are stored.** The model holds locations and hashes, not
copied source. This keeps secrets and bulk out of the model, and the views link to the code
instead.

**Freshness states** are computed by `check-evidence` and never stored:

- `fresh`: the hash at the recorded lines matches.
- `moved`: the identical span was found elsewhere in the file, or in the file's rename target.
  The script rewrites `lines` and `path` automatically. This is a mechanical change.
- `stale`: no identical span exists, or several do (see below). The claim needs re-verification
  by the agent.
- `missing`: the file is gone, or `symbol` no longer appears anywhere in the file.

**Ambiguous moves:** when the identical span occurs more than once, the item is `stale` with the
reason "the span occurs more than once", even when `symbol` is recorded. Identical copies contain
the same symbol, so it cannot tell them apart, and guessing from nearby lines could re-anchor the
claim to the wrong code without anyone noticing. `stale` is the safe over-approximation.

**Anchoring never hides staleness:** `anchor` only fills hashes that are absent. Replacing an
existing hash needs `--refresh` with the element named explicitly, after the agent has re-read the
span.

## 6. Element kinds

All kinds share this envelope:

| Field | Required | Description |
| --- | --- | --- |
| `id`, `kind` | yes | See §3. `kind` must match the ID prefix. |
| `name` | yes | Short human name. |
| `summary` | yes | One or two technical sentences. |
| `plain` | yes for components, operations, workflows, rules, invariants and roles | Plain-English statement for non-technical readers. It may only restate what the technical fields and evidence support. |
| `parent` | per kind | Containment reference, for example the component that owns an operation. |
| `lifecycle`, `provenance`, `rationale` | yes, yes, conditional | §4. Not used on `decision` (§6.9). |
| `evidence` | conditional | §5. Required when `provenance = observed`. |
| `relations` | optional | Outgoing typed edges (§6.10). |
| `aliases` | optional | Previous IDs of this concept. |
| `tags` | optional | Free-form lowercase tags. |

### 6.1 `component`

A deployable or logical unit of code, or an external system.

| Field | Description |
| --- | --- |
| `type` | `service`, `worker`, `frontend`, `module`, `library`, `datastore`, `external-system` |
| `technology` | e.g. `typescript/node`, `postgres`, `stripe-api`. This is the technology name, never a connection string. |
| `paths` | Globs of the code this component owns. They map changed files to components, which is how unmapped changes are detected. |
| `responsibilities` | Short bullet strings. |
| `config` | Names only: `[{"name": "DATABASE_URL", "kind": "env", "purpose": "…"}]`. **Values are never recorded.** |

```json
{
  "id": "component:booking-service",
  "kind": "component",
  "name": "Booking service",
  "summary": "HTTP service that owns booking holds, confirmation, modification and cancellation.",
  "plain": "Handles everything about reserving a room: holding it, confirming it after payment, changing and cancelling it.",
  "type": "service",
  "technology": "typescript/node",
  "paths": ["services/booking/**"],
  "responsibilities": ["Availability checks", "Booking state transitions", "Publishing booking events"],
  "config": [{"name": "HOLD_TTL_MINUTES", "kind": "env", "purpose": "How long an unpaid hold lasts"}],
  "lifecycle": "implemented",
  "provenance": "observed",
  "evidence": [{"type": "code", "path": "services/booking/src/server.ts", "symbol": "createServer", "lines": [1, 40], "hash": "sha256:…"}],
  "relations": [
    {"type": "depends_on", "target": "component:main-db", "mechanism": "sql"},
    {"type": "depends_on", "target": "component:payments-service", "mechanism": "http"}
  ]
}
```

### 6.2 `operation`

A unit of behaviour with a contract: HTTP or RPC handler, message consumer, scheduled job,
webhook handler, CLI command, or an exported function that other components call. `parent` is
the owning component.

| Field | Description |
| --- | --- |
| `trigger` | `{"type": "http"\|"rpc"\|"message"\|"schedule"\|"webhook"\|"cli"\|"call"\|"startup", "detail": "POST /bookings/:id/cancel" \| "*/5 * * * *" \| …, "channel": "channel:…"?}` |
| `exposure` | `public` (reachable from outside the system), `internal`, `unreachable` (defined but not wired: dead code or a disabled flag) |
| `contract` | `{"input": [Param], "output": [Param], "errors": [Error], "spec": Evidence?}`. Param: `{name, type, required, source: path\|query\|body\|header\|message\|arg, validation?, maps_to?: "entity:x#field"}`. Error: `{condition, signal: "HTTP 409"\|"throws X"\|…, plain}`. `spec` points to an OpenAPI or AsyncAPI file when one exists. |
| `auth` | `{"authentication": "required"\|"optional"\|"none"\|"unknown", "mechanism": "…", "authorization": [{"rule": "…", "roles": ["role:…"], "ownership_check": true\|false\|null, "provenance", "evidence"}]}` |
| `transactions` | `[{"id": "tx1", "mechanism": "db.transaction(...)", "isolation": "default"\|"read-committed"\|"repeatable-read"\|"serializable"\|"unknown", "provenance", "evidence"}]`. Relations point to these with `in_transaction`. |
| `concurrency` | `[{"resource": "entity:x"\|"entity:x#field", "mechanism": "row-lock"\|"optimistic-version"\|"unique-constraint"\|"atomic-update"\|"conditional-update"\|"advisory-lock"\|"single-consumer"\|"none-detected", "provenance", "evidence", "note"}]` |
| `idempotency` | `{"mechanism": "idempotency-key"\|"natural-key"\|"dedupe-record"\|"conditional-update"\|"none-detected"\|"not-applicable", "key": "…", "provenance", "evidence"}` |
| `failure_handling` | `[{"on": "…", "behaviour": "retry"\|"compensate"\|"propagate"\|"swallow"\|"dead-letter"\|"mark-failed", "plain", "provenance", "evidence"}]` |

`none-detected` is deliberately different from absent. It means the agent looked and found no
mechanism, which is a claim that needs evidence of where it looked. Absent means not assessed.

```json
{
  "id": "operation:booking.cancel",
  "kind": "operation",
  "name": "Cancel booking",
  "summary": "Moves a held or confirmed booking to cancelled and requests a refund when eligible.",
  "plain": "A customer can cancel their own booking, and staff can cancel any booking. If the cancellation is early enough, the customer is refunded in full.",
  "parent": "component:booking-service",
  "trigger": {"type": "http", "detail": "POST /bookings/:id/cancel"},
  "exposure": "public",
  "contract": {
    "input": [{"name": "id", "type": "uuid", "required": true, "source": "path", "maps_to": "entity:booking#id"}],
    "output": [{"name": "status", "type": "string", "maps_to": "entity:booking#status"}],
    "errors": [{"condition": "booking not in held|confirmed", "signal": "HTTP 409", "plain": "Already cancelled or completed bookings cannot be cancelled."}]
  },
  "auth": {
    "authentication": "required",
    "mechanism": "bearer token middleware",
    "authorization": [{"rule": "owner OR role in [staff, admin]", "roles": ["role:staff", "role:admin"], "ownership_check": true, "provenance": "observed", "evidence": [{"type": "code", "path": "services/booking/src/cancel.ts", "symbol": "assertCanCancel", "lines": [12, 25], "hash": "sha256:…"}]}]
  },
  "transactions": [{"id": "tx1", "mechanism": "db.transaction", "isolation": "default", "provenance": "observed", "evidence": [{"type": "code", "path": "services/booking/src/cancel.ts", "symbol": "cancelBooking", "lines": [42, 88], "hash": "sha256:…"}]}],
  "concurrency": [{"resource": "entity:booking#status", "mechanism": "conditional-update", "provenance": "observed", "note": "UPDATE … WHERE status IN ('held','confirmed')", "evidence": [{"type": "code", "path": "services/booking/src/repo.ts", "symbol": "markCancelled", "lines": [70, 82], "hash": "sha256:…"}]}],
  "idempotency": {"mechanism": "conditional-update", "provenance": "inferred", "rationale": "A second call finds status=cancelled and returns 409. There is no explicit key."},
  "lifecycle": "implemented",
  "provenance": "observed",
  "evidence": [{"type": "code", "path": "services/booking/src/routes.ts", "symbol": "registerRoutes", "lines": [30, 31], "hash": "sha256:…"}],
  "relations": [
    {"type": "reads", "target": "entity:booking", "fields": ["status", "check_in", "guest_id"], "in_transaction": "tx1"},
    {"type": "writes", "target": "entity:booking", "fields": ["status", "cancelled_at"], "in_transaction": "tx1"},
    {"type": "writes", "target": "entity:outbox-event", "in_transaction": "tx1"},
    {"type": "applies", "target": "rule:cancellation.full-refund-window"},
    {"type": "calls", "target": "operation:payments.refund", "mechanism": "async-via-outbox"}
  ]
}
```

### 6.3 `entity`

A data structure: a persisted table, collection or document type, or a message, DTO or file
schema. `parent` is the owning datastore component for persisted entities and is omitted for
messages and DTOs.

| Field | Description |
| --- | --- |
| `type` | `table`, `collection`, `document`, `cache-key`, `file`, `message`, `dto` |
| `fields` | `[Field]` (§6.4) |
| `constraints` | `[{"type": "primary-key"\|"unique"\|"foreign-key"\|"check"\|"exclusion"\|"not-null", "fields": [...], "detail": "…", "references": "entity:x#field"\|"entity:x"?, "provenance", "evidence"}]`. `references` (proposed, SPEC D20) names a foreign key's target; V3 checks it resolves to an entity or one of its fields. |

### 6.4 Fields and data lineage

```json
{
  "name": "total_amount",
  "type": "integer (minor units)",
  "nullable": false,
  "plain": "Total price the guest pays, in cents.",
  "sensitivity": "none",
  "origins": [{
    "type": "derived",
    "from": ["entity:room-rate#nightly_amount", "entity:booking#check_in", "entity:booking#check_out"],
    "via": "operation:booking.hold",
    "transform": "nights × nightly_amount, then the seasonal multiplier from rule:pricing.seasonal",
    "provenance": "observed",
    "evidence": [{"type": "code", "path": "services/catalog/src/pricing.ts", "symbol": "quote", "lines": [10, 44], "hash": "sha256:…"}]
  }],
  "states": null
}
```

- `origins[].type`: `user-input`, `derived`, `system-generated` (IDs, timestamps), `external`
  (third-party response), `copied` (denormalised copy), `constant`.
- `sensitivity`: `none`, `pii`, `financial`, `credential-reference`. Only the *classification*
  is recorded, never a value.
- `states` (optional, for status-like fields): a state machine,
  `{"values": [...], "transitions": [{"from", "to", "via": "operation:…", "guard": "rule:…"?}]}`.
- Field references (`entity:booking#total_amount`) make **field-level lineage** a graph. The
  edges run from `origins[].from` through `via` to this field, so the `query lineage` script can
  answer "where does this value come from" and "what does changing this field affect".

### 6.5 `channel`

Asynchronous or scheduled communication: queue, topic, outbox table, webhook endpoint, schedule
or stream.

| Field | Description |
| --- | --- |
| `type` | `queue`, `topic`, `outbox`, `webhook`, `schedule`, `stream`, `email` |
| `message` | `entity:…` of type `message`, when the payload is modelled |
| `delivery` | `at-least-once`, `at-most-once`, `exactly-once-claimed`, `unknown`. `exactly-once-claimed` records what the code or docs claim. It is not a verified guarantee. |
| `ordering` | `none`, `per-key`, `total`, `unknown` |

Producers and consumers are not stored on the channel. They are derived from `publishes` and
`consumes` relations on operations, which keeps a single source of truth.

### 6.6 `workflow`

An end-to-end business process told as steps. It is the backbone of the functional view and is
shared with the technical view.

```json
{
  "id": "workflow:cancel-booking",
  "kind": "workflow",
  "name": "Cancel a booking",
  "summary": "Cancellation from request through optional refund and notification.",
  "plain": "How a booking gets cancelled and when the guest gets money back.",
  "actors": ["role:customer", "role:staff"],
  "trigger_plain": "A guest or staff member asks to cancel a booking.",
  "data_collected": ["entity:booking#id"],
  "steps": [
    {"id": "s1", "plain": "Check the person is allowed to cancel this booking.", "operation": "operation:booking.cancel", "next": ["s2"],
     "on_failure": [{"plain": "Someone else's booking: request refused.", "outcome": "o-denied"}]},
    {"id": "s2", "plain": "Check the booking can still be cancelled.", "operation": "operation:booking.cancel", "next": ["s3"],
     "on_failure": [{"plain": "Already cancelled or finished: nothing changes.", "outcome": "o-rejected"}]},
    {"id": "s3", "plain": "Decide whether a full refund applies.", "rules": ["rule:cancellation.full-refund-window"],
     "branches": [{"condition_plain": "More than 24 hours before check-in", "next": "s4"},
                  {"condition_plain": "Within 24 hours of check-in", "outcome": "o-cancelled-no-refund"}]},
    {"id": "s4", "plain": "Ask the payment service to refund the guest.", "operation": "operation:payments.refund", "outcome": "o-cancelled-refunded"}
  ],
  "outcomes": [
    {"id": "o-cancelled-refunded", "type": "success", "plain": "Booking cancelled and money returned."},
    {"id": "o-cancelled-no-refund", "type": "alternative", "plain": "Booking cancelled without a refund."},
    {"id": "o-rejected", "type": "failure", "plain": "Cancellation refused because of the booking's state."},
    {"id": "o-denied", "type": "failure", "plain": "Cancellation refused because the person lacks permission."}
  ],
  "lifecycle": "implemented",
  "provenance": "observed",
  "evidence": [{"type": "code", "path": "services/booking/src/cancel.ts", "symbol": "cancelBooking", "lines": [42, 88], "hash": "sha256:…"}]
}
```

**Rule:** each step must reference an `operation` or a `rule`, or carry its own `evidence`.
A step with none of these is invalid. This is how the functional view is prevented from
inventing behaviour.

### 6.7 `rule`

A business rule, validation, eligibility check, calculation, policy or state-transition guard.

| Field | Description |
| --- | --- |
| `type` | `validation`, `eligibility`, `calculation`, `policy`, `state-transition` |
| `statement` | Precise technical statement. |
| `plain` | Plain-English statement. |
| `parameters` | Business constants, e.g. `[{"name": "full_refund_window", "value": "24h", "source": Evidence}]`. **Only non-secret business constants.** |

Operations link to rules with `applies` relations.

### 6.8 `invariant`

A property that must always hold. It may be observed in enforcement code or stated by a human.

| Field | Description |
| --- | --- |
| `statement`, `plain` | As for rules. |
| `category` | `uniqueness`, `referential`, `consistency`, `authorization`, `idempotency`, `ordering`, `temporal`, `conservation` (e.g. refunds ≤ payments) |
| `scope` | Entities, fields and operations involved. |
| `enforcement` | `[{"mechanism": "db-constraint"\|"transaction"\|"lock"\|"conditional-update"\|"application-check"\|"idempotency-key"\|"test-only"\|"none-detected", "where": "operation:…"\|"entity:…", "provenance", "evidence"}]` |
| `verified_by` | Evidence items of type `test`. |
| `criticality` | `high`, `medium`, `low` (human-set, or `inferred` with rationale). |

An invariant with `provenance: stated` and enforcement `none-detected` is a valuable finding:
the team believes something holds that the code does not enforce.

### 6.9 `decision` (stored in `.evermap/decisions/`)

MADR-compatible, with per-section provenance.

| Field | Description |
| --- | --- |
| `title` | Short imperative title. |
| `status` | `proposed`, `accepted`, `rejected`, `deprecated`, `superseded` (MADR vocabulary) |
| `date` | Date the decision was made, **as stated by a human**. `null` if unknown. |
| `problem` | `{"text", "provenance"}` |
| `drivers` | `[{"text", "provenance"}]`: constraints and forces. |
| `options` | `[{"name", "outcome": "chosen"\|"rejected"\|"not-chosen", "pros": [...], "cons": [...], "reasons_rejected": {"text", "provenance"}?}]` |
| `decision` | `{"text", "provenance"}` |
| `consequences` | `[{"text", "type": "positive"\|"negative"\|"neutral", "provenance"}]`: trade-offs. |
| `affects` | Element IDs. |
| `supersedes`, `superseded_by` | Decision IDs. |
| `evidence` | Must include ≥ 1 `stated` item if any section is `confirmed`. For a decision imported from an accepted ADR in the repository, the ADR's `doc` evidence item counts instead (SPEC §5.5). |

Section provenance values: `confirmed` (a human confirmed this text), `inferred` (the agent
derived it from code or history; **shown as "Inferred — not confirmed"** in every render), and
`unknown`.

Hard rules:

- Any `status` other than `proposed` requires `decision.provenance = confirmed`.
- The agent may create `proposed` records holding inferred text as *candidates*. It may never
  upgrade provenance to `confirmed` without an explicit human confirmation captured as `stated`
  evidence.

### 6.10 Relations

Relations are stored on the **source** element only. The scripts compute reverse edges.

| Type | Source → Target | Extra attributes |
| --- | --- | --- |
| `depends_on` | component → component | `mechanism`: `http`, `grpc`, `sql`, `sdk`, `in-process`, `queue`, … |
| `calls` | operation → operation | `mechanism`: `sync-http`, `sync-call`, `async-via-outbox`, `async-message`, …; `condition` |
| `reads`, `writes`, `deletes` | operation → entity | `fields`, `condition`, `in_transaction` |
| `publishes`, `consumes` | operation → channel | `message`, `condition` |
| `applies` | operation → rule | |
| `uses_role` | role → role | Role hierarchy (e.g. admin includes staff). |

`enforces` (operation → invariant) and `affects` (decision → any) are **derived** edges. They are
computed from `invariant.enforcement[].where` and `decision.affects` and are never stored, so there
is one source for each fact.

Every relation may carry `provenance`, `rationale` and `evidence`. Observed relations whose
target's evidence does not already prove the link must carry their own evidence, for example the
line where the call happens.

### 6.11 `role`

| Field | Description |
| --- | --- |
| `type` | `user-role`, `service-identity`, `external-actor` |
| `plain` | Who this is, in plain words. |

## 7. Proposal overlays (proposed behaviour)

`.evermap/proposals/<slug>/` holds an optional overlay for a change under discussion:

```
proposals/allow-partial-refunds/
├── proposal.json      # title, description, status: draft|assessed|accepted|abandoned, and source (who/when)
├── model/…            # added or modified element files, all with lifecycle: proposed
└── assessment.md      # impact report (SPEC §5.8)
```

- Overlay elements may add new IDs or **shadow** existing ones, giving a modified copy with the
  same ID.
- `diff --overlay` shows exactly what the proposal changes in the model.
- Overlays are never merged into `model/` by Evermap. After the code is implemented, a normal
  `update` maps the real implementation, and the overlay can then be compared or deleted. This
  keeps *proposed* and *implemented* strictly apart.
- Overlays are optional. Most impact assessments only produce a report.

## 8. Manifest — `.evermap/evermap.json`

```json
{
  "schema_version": "0.1",
  "system": {
    "name": "Example booking platform",
    "summary": "Room booking with holds, payments, notifications and reporting.",
    "plain": "Lets guests find, book, pay for and cancel room stays."
  },
  "synced": {"commit": "9c1e2f0a…", "mode": "update"},
  "scope": {
    "include": ["services/**", "packages/**", "migrations/**"],
    "exclude": ["**/node_modules/**", "**/*.test.ts", "docs/**"]
  },
  "coverage": {
    "unmapped": [{"path": "services/legacy-import/**", "reason": "Out of scope by user request"}],
    "limitations": ["Handlers resolved through the plugin registry are inferred, not observed."]
  }
}
```

- `synced.commit` is the commit whose code the model describes. `mode` is `init`, `update` or
  `regenerate`.
- `synced.dirty` is `true` when the model describes a working tree with uncommitted changes outside
  `.evermap/` (SPEC §5.3 U6). It is omitted otherwise.
- `coverage.pending` lists component IDs that are declared but not yet mapped (a mapping run
  stopped before its last packet resumes from `.evermap/plan.json` instead, SPEC §5.12), so discovery can
  continue in a later session (SPEC §5.1).
- `coverage` makes gaps explicit. Impact reports must cite it when an affected area is unmapped,
  so that "not found" is never read as "does not exist".
- `views.diagrams` (optional, proposed with SPEC D19) lists the extra diagram formats `render`
  writes, unique values from `excalidraw` and `eraser`; absent means Mermaid only. Set it with
  `render --diagrams`.
- `views.kinds` (optional, proposed with SPEC D20) lists the diagram kinds drawn besides the
  four flowcharts, unique values from `sequence`, `er`, `state` and `access`. Absent means the
  user was never asked; `[]` means asked, none. Set it with `init --kinds` or `render --kinds`.
- There is no timestamp, so an update with no changes is byte-identical (EverBench S14).

## 9. Validation rules (enforced by `scripts/evermap.py validate`)

| # | Rule | Severity |
| --- | --- | --- |
| V1 | Every file parses as JSON and matches the kind's schema. | error |
| V2 | `id` matches the grammar (§3), the `kind` prefix and the file path. IDs are unique, and aliases do not collide with live IDs. | error |
| V3 | Every reference (`parent`, `relations[].target`, `maps_to`, `origins[].from`, `rules`, `operation`, `roles`, …) resolves to a live ID or sub-element. | error |
| V4 | `provenance = observed` ⇒ ≥ 1 evidence item that is not `stated`. `inferred` ⇒ non-empty `rationale`. `stated` ⇒ ≥ 1 `stated` evidence item. A `none-detected` mechanism carries its own evidence or rationale. | error |
| V5 | No element in `model/` has `lifecycle = proposed`. Every element in a proposal overlay does. | error |
| V6 | File evidence paths exist at the working tree. Lines are in range. Hashes are present. | error |
| V7 | Evidence freshness: `stale` or `missing` items are listed. | error in `--strict` (CI), warning otherwise |
| V8 | Workflow steps reference an operation or rule, or carry evidence. `next`, branch and outcome IDs resolve inside the workflow. | error |
| V9 | Secret scan over `.evermap/`: known credential patterns (cloud keys, private-key headers, JWTs, URLs with embedded credentials) plus high-entropy strings in value positions. | error |
| V10 | `plain` is present where required, is ≤ 600 characters, and contains no code identifiers beyond those in the element's `name`. | warning |
| V11 | Decision hard rules (§6.9). | error |
| V12 | Decisions referencing removed IDs are reported as "historical reference". They are kept, not deleted. | warning |
| V13 | Files are canonically formatted (`fmt --check`). | error in `--strict` |
| V14 | No Git conflict markers anywhere under `.evermap/`. | error |
| V15 | `views/` is in sync with the model: the render hash recorded in `views/README.md` matches. | warning |
| V16 | Relation direction (proposed, SPEC D18): a cross-component `calls` with mechanism `in-process`, `sync-call` or none goes to a component that itself `depends_on` the caller's component; such an in-process call joins components whose `technology` names different languages (frameworks and runtimes of one language, such as `hono` and `typescript`, count as one; unknown words count for nothing); or two components `depends_on` each other. The message names both ids and what to check (the call site in the caller, the mechanism, data received rather than called). | warning |

The validator prints a JSON report to stdout and exits non-zero on any error.

## 10. Schema evolution

- `schema_version` uses `MAJOR.MINOR`. A minor bump only adds optional fields. A major bump needs
  `scripts/evermap.py migrate`, which rewrites files deterministically.
- JSON Schemas ship in `skills/evermap/assets/schema/` (one per kind, a manifest schema, and a
  `common` schema for shared definitions) and are the normative definition. The property order in
  each schema is the canonical key order (§2.1). This document explains them.
