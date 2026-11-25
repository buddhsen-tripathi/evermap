# Decisions

Unmeasured: written before EverBench. For the calling agent; no code reading is needed.
Shapes (`decision`, `Section`) are in `model.md`.

## Calling agent

### Capture

1. When the user states or confirms a decision, show them the record's text before writing it.
2. Each section is `confirmed` only for text the user confirmed in this conversation; the rest
   is `inferred` or `unknown`. Record only the options, drivers and consequences they named.
3. Add one `stated` evidence item: `who` is the role or handle the user gives (never an email),
   `date` is the date as they give it (otherwise the decision's `date` is null), `source` is
   `conversation`.
4. Write the record to `.evermap/draft.json` and run `apply --allow-decisions`. Only you pass
   `--allow-decisions`, and only after the user confirmed the text in this conversation;
   workers never pass it.

```json wrong decision
{"status": "accepted"}
```

```json right decision
{"status": "accepted", "decision": {"text": "Assign lockers with row locks that skip locked rows.", "provenance": "confirmed"}, "evidence": [{"type": "stated", "who": "tech-lead", "date": "2026-01-01", "source": "conversation"}]}
```

```json wrong decision
{"problem": {"text": "Concurrent deposits picked the same locker.", "provenance": "confirmed"}}
```

The first is accepted without a confirmed decision; the second is confirmed without anyone
stating it (V11).

### Candidates

You may draft a `proposed` record with `inferred` sections, for example "the deposit code locks
one locker row and skips locked rows; this looks like a choice to let deposits run in
parallel". `apply` accepts it without `--allow-decisions`. It is never history: views label it
"Inferred — not confirmed". Ask the user to confirm or correct it.

### Retrieval

1. For "why X?", run `search <words> --kind decision --format md`, then read that record.
2. A record exists: answer from it, name its id, and label every inferred section
   "Inferred — not confirmed".
3. No record: say "No recorded decision." You may then add an explanation labelled
   "Inferred, not confirmed:", from the model or the code.
4. Never invent a date, a person, an alternative or a reason.

Wrong: "The team chose row locks in 2024 because a mutex was too slow."
Right: "No recorded decision. Inferred, not confirmed: the deposit code locks one locker row."

### Rejected options

An option the user rejected has `outcome: rejected` and `reasons_rejected` as a `Section`.
Assessments check proposals against these (`assess.md`, step 3).

### Supersession

The new record lists the old id in `supersedes`. The old record gets `superseded_by` and
`status: superseded`, and nothing else changes in it. Both writes need `--allow-decisions`,
because the old record is an existing decision.

### Imports

An ADR file in the repository is cited as `doc` evidence. Copy its text as `confirmed` only
when the ADR itself is an accepted record; the `doc` item then counts as the statement. Any
other ADR becomes a `proposed` record with `inferred` sections.
