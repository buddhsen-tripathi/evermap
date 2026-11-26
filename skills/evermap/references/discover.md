# Discovery

Unmeasured: written before EverBench. Read only your part.

## Calling agent

1. Ask about scope only when the repository is large or holds several systems; otherwise state
   the default scope you use.
2. `init` with `--name`, `--summary`, `--plain`, and `--scope` or `--exclude` when needed.
3. `estimate --write-plan`, approval and one worker per packet, as `headless.md` says.
4. When `final_pass` says one worker, run the final pass last: its packet text is the block
   below, followed by the packet text in `headless.md` from "For stale evidence" to its end.
5. `sync --mode init`, then `render`.
6. Close with a short report from `status`, `coverage` and `search <kind> --format md`: counts
   per kind, coverage gaps, elements with `unknown` provenance, open questions from workers.

```
Final pass over .evermap/ for this repository: no packet number. Read
<absolute skill dir>/references/model.md, discover.md and functional.md; not assets/schema/.
Read the model with python3 <absolute skill dir>/scripts/evermap.py search and query, using
--format md, and only the code each cross-packet claim needs, in line ranges. Follow
"Final pass" in discover.md.
```

## Worker

The "Calling agent" part above is not for you. Changes under `.evermap/`, including
`.evermap/plan.json` and your draft, are expected and never make the tree dirty.

### Order

1. Manifests and entry points: package and build files, containers, routing tables, consumer
   and scheduler setup. Docs and ADRs show intent, not behaviour.
2. Reachability: list every handler defined in your files and compare the list with the
   registrations you found in step 1. Each handler with no route, consumer or schedule is
   `exposure: unreachable` (see "Facts, not guesses").
3. Schema: migrations, DDL, ORM models.
4. Per component, in your files only: entities; operations, entry points first; relations; on
   each operation `auth`, `transactions`, `concurrency`, `idempotency` and `failure_handling`;
   channels.
5. Then roles, rules, invariants (enforcement you see; `stated` ones come only from people),
   workflows (`functional.md`), and `origins` for fields that are derived or cross components.

### What is significant

Map: deployable units and external systems; every reachable entry point (route, consumer,
schedule, webhook, CLI); internal operations that cross components, write state, apply a rule
or open a transaction; persisted entities and messages; rules, invariants, roles; workflows a
non-technical person would name.
Leave out: helpers, formatting, logging and framework glue, unless they hold a rule or a
security check; generated and vendored code. Tests are evidence (`verified_by`, type `test`),
not elements.

### Provenance

`observed` needs a cited span that shows the claim. `inferred` needs a `rationale` (naming,
configuration, a registry). `unknown` means you looked and cannot tell. Never write `stated`:
only people state. Wrong, then right:

```json wrong operation
{"auth": {"authentication": "required", "provenance": "observed"}}
```

```json right operation
{"auth": {"authentication": "required", "provenance": "inferred", "rationale": "The router wraps every parcel route in the login check."}}
```

```json wrong operation
{"idempotency": {"mechanism": "none-detected"}}
```

```json right operation
{"idempotency": {"mechanism": "none-detected", "evidence": [{"type": "code", "path": "api/deposit.py", "symbol": "deposit_parcel", "lines": [4, 12]}]}}
```

### Evidence

Cite the smallest span that shows the claim: the registration line for a route, the
`UPDATE … WHERE` line for a conditional update, not the whole function or file. Give `symbol`
when there is one; never write `hash`. A two-line check cited as `"lines": [1, 240]` is wrong.

### Facts, not guesses

- A handler that is defined but never registered as a route, consumer or schedule is
  `exposure: unreachable`; cite the registration code you checked.
- Dynamic dispatch (registries, string lookups) makes a relation `inferred` unless you cite the
  registry line.
- Direction ("Relations" in model.md): `depends_on` from consumer to provider, `calls` from
  caller to callee, cited at the line in the caller that invokes it. A client calls the
  server's handler; a value received as an argument is not a call.
- Check-then-act, a missing transaction or a missing constraint is recorded as what you see:
  the `reads` and `writes` relations, the `transactions` that exist (none means no entry), a
  `concurrency` or invariant `enforcement` entry with `none-detected` and the span you read.
  Never write "race" or "bug": judging risk is the assessment's job.
- Entity `constraints` list only what the schema declares.

### Record it in the model

Your reply is cut, so findings go into fields: `failure_handling`, invariant `enforcement`,
`provenance: unknown` with a `rationale` naming what is missing, and for gaps that no element
holds, one line each in `coverage.limitations` of `.evermap/evermap.json`.

### One packet of a larger system

- Relations to elements outside your files use the ids that `packet <n>` lists. If the target
  does not exist yet, leave the relation out and list it in your reply as pending, for example
  `pending: operation:parcel.collect applies rule:collection.window`.
- Do not create elements for code outside your files, and do not recreate listed ids.

### Final pass

Only for the final-pass packet. From the model (`search`, `query neighbors`, `query refs`,
`query lineage`, all with `--format md`), add workflows that cross components, `origins` for
fields fed by other components, rules applied by several operations, and the relations other
workers reported as pending. Read only the code each of these needs. Write only new or changed
elements.
