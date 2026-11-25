# Plain text and workflows

Unmeasured: written before EverBench. For workers writing `plain`, `trigger_plain`,
`condition_plain` and workflows. Shapes are in `model.md`.

## Plain text

The reader does not read code. Write what happens and why it matters, in everyday words.

- Every sentence restates a field of the same element: a rule's `statement` and `parameters`,
  an operation's `auth`, `contract` errors and `failure_handling`, an invariant's `statement`.
  Add nothing the fields and their evidence do not hold.
- No code identifiers: no names of functions, classes, tables, columns, variables or
  constants, nothing in `snake_case`, `camelCase` or with `()`. The element's own `name` is
  allowed. `validate` warns about them (V10).
- Numbers come from `parameters` or the cited code, with their unit. At most 600 characters.
- What is unknown is said to be unknown ("Who may collect for someone else is not determined
  from the code"), and the field carries `provenance: unknown`.

```json wrong rule
{"plain": "is_expired() is true when MAX_DAYS is exceeded."}
```

```json right rule
{"plain": "A parcel can be collected for seven days after it was stored."}
```

```json wrong operation
{"plain": "Calls collect_parcel and updates the parcels table."}
```

```json right operation
{"plain": "A recipient enters their code and takes the parcel, unless it has expired."}
```

## Workflows

A workflow is a process someone outside the team would name, such as "Deposit a parcel", told
from `trigger_plain` to its `outcomes`.

- `actors` are roles; `data_collected` lists the fields the person provides.
- Every step names an `operation`, or `rules`, or carries its own `evidence`; a step with none
  of them is invalid (V8). Steps tell the reader's story, not the call graph: one step per
  thing the person would notice.
- Cover the branches (`branches` with a `condition_plain`), the failures (`on_failure`) and
  every outcome: the success, the alternatives, and each failure the code produces in
  `contract` errors or `failure_handling`. Every branch and failure ends in `next` or
  `outcome`.
- Ids: steps `s1`, `s2` …; outcomes `o-` plus a word (`o-stored`).
- A step you could not determine says so in its `plain` and has `provenance: unknown`.

```json wrong workflow
{"steps": [{"id": "s1", "plain": "The courier is checked.", "outcome": "o-done"}]}
```

```json right workflow
{"steps": [{"id": "s1", "plain": "Check that the person is a courier.", "operation": "operation:parcel.deposit", "on_failure": [{"plain": "Anyone else is turned away.", "outcome": "o-denied"}], "outcome": "o-done"}]}
```

```json wrong workflow
{"steps": [{"id": "s1", "plain": "Check how long the parcel waited.", "rules": ["rule:collection.window"], "branches": [{"condition_plain": "It waited more than seven days"}], "outcome": "o-done"}]}
```

```json right workflow
{"steps": [{"id": "s1", "plain": "Check how long the parcel waited.", "rules": ["rule:collection.window"], "branches": [{"condition_plain": "It waited more than seven days", "outcome": "o-denied"}], "outcome": "o-done"}]}
```

The first wrong step names nothing the model holds; the second leaves a branch without an end.
