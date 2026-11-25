# Update

Unmeasured: written before EverBench. Update only when the user asks. Read only your part.

## Calling agent

1. **No-op first (U5, U4).** Run `status`. If `up_to_date` is true and `changed_files` is 0,
   say "up to date": no worker, no file written. If `up_to_date` is true but files changed,
   they are unrelated (docs, styling, test-only): `sync --mode update` only, no worker.
2. **Moved evidence.** `check-evidence --fix-moved` relocates moved spans without a worker.
   Run `status` again; if it is now up to date, stop after `sync --mode update`.
3. **Dirty tree (U6).** If `working_tree_dirty` is true, ask the user to commit or stash
   first: workers need a clean tree (`headless.md`). Never update a dirty tree in this
   conversation.
4. **Baseline (U7).** If `changes` warns that the synced commit is not an ancestor of HEAD,
   tell the user the update uses the merge base and is wider.
5. `estimate --changed --write-plan`, approval and one worker per packet, as `headless.md`
   says.
6. **Deletions (U3).** Workers cannot delete files. For each `delete: <id>` a worker reports,
   check `query refs <id> --format md` is empty, then delete the file at the id's path under
   `.evermap/model/` (`model.md` gives the path rule). Never delete a decision: `validate`
   reports it as a historical reference (V12).
7. **After a merge.** `validate` lists conflict markers (V14) and dangling references.
   Resolve a conflict in `.evermap/evermap.json` by keeping either side; `sync` rewrites
   `synced`. Get the parents' merge base with `git merge-base HEAD^1 HEAD^2` and run
   `estimate --changed --since <merge base> --write-plan`: workers re-verify the conflicted and
   changed elements against the merged code, which decides, not either side's text.
8. `sync --mode update`, then `render`. Summarise from `diff HEAD --format md` (the model
   before and after): added, removed, modified and renamed elements, functional and
   risk-relevant changes, deleted ids, decisions flagged by `validate`, a dirty tree, a wider
   baseline.

**Regenerate is not available.** It needs `reconcile`, which does not exist yet. Instead: a
wide update keeps every id, `estimate --changed --since <older commit> --write-plan`; or keep
the model and fix drift element by element.

## Worker

The "Calling agent" part above is not for you. Changes under `.evermap/`, including
`.evermap/plan.json` and your draft, are expected and never make the tree dirty. Your packet's
files are the changed files; `packet <n>` lists them and the ids in the model.

1. **Re-verify.** For each element whose evidence is in your files, re-read the cited span.
   - Meaning unchanged and only the span stale: `anchor <id> --refresh`. Do not rewrite it.
   - Meaning changed: write the whole corrected element in the draft, then `anchor <id>
     --refresh`: `apply` keeps the old hash of a span whose lines did not move.
   - Unchanged: leave it alone. Element files outside the change stay byte-identical (U1).
2. **Renames (U2).** Same concept, new name or place: keep the id; change only evidence and
   `name`. Only when the concept itself is renamed, split or merged does it get a new id, with
   the old one in `aliases`.
3. **Deletions (U3).** Code gone: do not write the element. Remove references to it from the
   elements you rewrite (`query refs <id> --format md`) and report `delete: <id>` as pending.
   Never change or delete a decision.
4. **One-hop neighbours.** For each changed element run `query neighbors <id> --format md`.
   Change a neighbour only when the code shows the knock-on effect, and name it in your reply.
5. **Unmapped changed files.** Add elements only for significant code: an entry point, a state
   write, a rule, a transaction or a cross-component call; not helpers or glue.
6. Follow `model.md` for shapes, provenance and evidence; record findings in fields, not only
   in the reply.
