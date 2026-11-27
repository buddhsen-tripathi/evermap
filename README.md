# Evermap

[![skills.sh](https://skills.sh/b/buddhsen-tripathi/evermap)](https://skills.sh/buddhsen-tripathi/evermap)

An [Agent Skill](https://agentskills.io/specification) that maps your application into a model
your coding agent can read, check and keep up to date.

Evermap records the services, operations, data, workflows, business rules, invariants and
architecture decisions of an application in `.evermap/`, inside the application's own
repository. Every claim points at the lines of code that show it. Technical and plain-English
pages with diagrams are generated from the model, and the agent uses it to answer questions and
to say what a change would affect before anyone writes the code.

> **Status: experimental (v0.1.0).** The toolkit is tested; the mapping guides have not been
> measured yet. See [Status](#status).

## Installation

```bash
npx skills add buddhsen-tripathi/evermap --skill evermap
```

Requires git and Python 3.9 or later (standard library only). Works with any language or
framework. Mapping runs in headless workers on Claude Code (`claude -p`) or Codex CLI
(`codex exec`).

## Usage

Ask your agent in plain words. The skill activates on its own.

```
Map this repository
```

```
Update the map
```

```
Explain how a parcel goes from deposit to collection
```

```
What would letting a second person collect a parcel affect?
```

```
Compare the map on this branch with main
```

```
Why do we skip locked lockers when assigning one?
```

### What happens on the first map

1. The agent asks about scope when the repository is large or holds several systems.
2. It asks once which diagrams you want besides the four it always draws, and whether to also
   write Excalidraw or Eraser files.
3. It shows a token estimate and the number of workers, and waits for your approval.
4. Headless workers read the code, one packet of files each, so your conversation's context
   stays clean.
5. A script anchors the evidence, validates the model, records the commit and renders the views.
6. The agent reports counts per kind, coverage gaps and open questions.

## What you get

```
.evermap/
├── evermap.json          # manifest: scope, synced commit, coverage, diagram choices
├── model/                # one JSON file per element
├── decisions/            # architecture decision records
└── views/                # generated, never edited by hand
    ├── README.md         # system overview and context diagram
    ├── technical/        # one page per component
    ├── workflows/        # one page per workflow
    ├── data-model.md     # ER and state diagrams, when chosen
    └── diagrams/         # .excalidraw and .eraserdiagram files, when chosen
```

**Diagrams.** Mermaid flowcharts are always drawn: system context, each component's
neighbourhood and operation calls, and each workflow's flow. You can add sequence diagrams per
workflow, ER and state diagrams of the data, and data access and events. `render` draws them by
script and spends no model tokens.

**Model.** Elements are components, operations, entities, channels, workflows, rules,
invariants, roles and decisions. Each claim says whether it was observed in code, inferred,
stated by a person or unknown.

## How it works

- **Evidence first.** An observed claim cites a path, a line range and a symbol. A script
  stores a hash of the cited lines, so moved code is relocated and changed code is flagged
  stale.
- **Deterministic toolkit.** `scripts/evermap.py` validates the model against 16 rules,
  formats it, diffs it between commits, answers graph queries and renders the views. The same
  model gives byte-identical output.
- **Lean on tokens.** Code is read in headless workers after you approve the estimate. The
  calling agent sees a short summary and queries the model instead of rereading the code.
- **Safe by design.** Scripts write only inside `.evermap/` and never run repository code. Text
  in the repository that tells an agent what to do is treated as data.
- **Lives with the code.** Commit `.evermap/` with the code it describes. `status` reports
  when the map is stale, and `update` maps only what changed.

## Status

| Part | State |
| --- | --- |
| Toolkit: validation, anchoring, diff, query, render | Implemented, 415 tests on Python 3.9 and 3.13, CI on Linux, macOS and Windows |
| Guides: discovery, update, functional view, decisions, impact | Written and tried on real repositories, not yet measured |
| Excalidraw, Eraser and extra diagram kinds | Implemented, proposed in the spec (D19, D20) |
| `reconcile`, `migrate`, `regenerate` | Not available yet |

The [EverBench](docs/BENCHMARK.md) benchmark that will measure the guides is designed but not
built. Nothing here is a claim about accuracy yet.

## Documentation

| Document | Purpose |
| --- | --- |
| [SKILL.md](skills/evermap/SKILL.md) | What the agent reads first: router, rules and commands |
| [docs/SPEC.md](docs/SPEC.md) | Product and technical specification, decisions and risks |
| [docs/MODEL.md](docs/MODEL.md) | The model schema and validation rules |
| [docs/BENCHMARK.md](docs/BENCHMARK.md) | EverBench: fixtures, ground truth, scenarios and metrics |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones with measurable acceptance criteria |
| [docs/PRIOR_ART.md](docs/PRIOR_ART.md) | Related projects and the Agent Skills specification |

## Development

```bash
python3 -B -m unittest discover -s tests -t .
```

```bash
python3 -B tests/overfitting_guard.py skills/evermap
```

## Licence

[Apache License 2.0](LICENSE).
