# Data for ER and state diagrams

Unmeasured. For workers only, and only when your packet lists `data.md` under `reads`: the map
draws ER or state diagrams, so record the data structure as well. Facts only, as discover.md
says: what the schema or code declares, never what a name suggests.

## Persisted entities

For each `table`, `collection` or `document` in your files, from the schema, migration or ORM
model that defines it:

- `fields`: every column or property with `name`, `type` as declared, `nullable` when the
  schema says (true for a column without NOT NULL in SQL), and `sensitivity` (`pii`,
  `financial`, `credential-reference`, or `none`). Never record values.
- `constraints`: `primary-key`, `unique` and `foreign-key` with their `fields`; a foreign key
  also gets `references`, the target as `entity:x#field` (or `entity:x` when the target column
  is not named). Only constraints the schema declares; an index is not a constraint.
- Evidence on each constraint: the schema or migration line that declares it, for example
  `"evidence": [{"type": "schema", "path": "db/schema.sql", "lines": [8, 8]}]` for
  `locker_id integer REFERENCES lockers (id)`.

A foreign key to a table no element models yet: leave `references` out and list it as pending.

## Status fields

For a field whose value moves through named states (`stored`, `collected`, `expired`):

- `states.values`: the values the schema or code allows (a CHECK list, an enum, the literals
  written to it).
- `states.transitions`: one `{"from", "to", "via"}` per update you see, `via` naming the
  operation that writes it, plus `guard` when a rule decides it (`rule:collection.window`).
  Give each transition `evidence` at the line that writes the new value, for example the
  `UPDATE parcels SET status = 'collected' WHERE status = 'stored'` line.
- Never add an initial or final state, and never a transition you did not see in the code.
