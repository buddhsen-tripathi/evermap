# Evermap

Evermap is an open-source [Agent Skill](https://agentskills.io/specification) that keeps a
living, evidence-backed, version-controlled model of a software application inside the
application's own repository.

The model records services, operations, data, workflows, business rules, invariants and
architecture decisions. Every significant claim points to the source code or another named
evidence source. Technical and plain-English (functional) views are rendered from the same
model. Coding agents and people use the model to understand the system and to assess a change
before anyone implements it.

> **Status: experimental.** The deterministic toolkit (`skills/evermap/scripts/`, milestone M1)
> and draft procedure guides (`skills/evermap/references/`) exist and have unit tests. The guides
> are unmeasured: the EverBench benchmark that is meant to measure them is designed but not
> built. Not available yet: full regeneration with id reconciliation (`reconcile`,
> `regenerate`) and schema migration (`migrate`). Nothing here is a claim about accuracy.

## Design documents

| Document | Purpose |
| --- | --- |
| [docs/SPEC.md](docs/SPEC.md) | Product and technical specification, architecture, trade-offs, open questions and risks |
| [docs/MODEL.md](docs/MODEL.md) | Proposed canonical application model schema |
| [docs/PRIOR_ART.md](docs/PRIOR_ART.md) | Research on the Agent Skills specification, related projects and reusable components |
| [docs/BENCHMARK.md](docs/BENCHMARK.md) | EverBench: benchmark applications, ground truth, scenarios, metrics and controls |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones with measurable acceptance criteria |

## Licence

[Apache License 2.0](LICENSE).

## Installation (experimental)

```bash
npx skills add buddhsen-tripathi/evermap --skill evermap
```

Mapping runs in headless workers after the user approves a token estimate; see
`skills/evermap/references/headless.md`.

Views draw their diagrams in Mermaid. `render --diagrams excalidraw,eraser` also writes each one
as an Excalidraw and an Eraser file (proposed); see `skills/evermap/references/diagrams.md`.
