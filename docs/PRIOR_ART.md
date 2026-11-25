# Prior Art and Reusable Components

Status: Draft v0.1 · 2026-11-25

This document records what we learned from the Agent Skills specification and from related
open-source projects. It also lists the components Evermap should reuse instead of rebuilding.
Each project is described from its public README, documentation or paper as read on
2026-11-25. Claims about third-party projects are limited to what those sources state. We have
not benchmarked any of them.

## 1. The Agent Skills specification

Source: <https://agentskills.io/specification> and the guides linked from
<https://agentskills.io/llms.txt>.

### 1.1 Format requirements that constrain Evermap

| Requirement | Specification text (summarised) | Consequence for Evermap |
| --- | --- | --- |
| Directory | A skill is a directory with at least a `SKILL.md`. Optional `scripts/`, `references/`, `assets/` and any other files. | Ship as `skills/evermap/` with those three subdirectories. |
| `name` | Required. 1–64 characters, lowercase `a-z`, `0-9` and `-`. No leading, trailing or consecutive hyphens. **Must match the parent directory name.** | `name: evermap` in directory `evermap/`. |
| `description` | Required. 1–1024 characters. Say what the skill does *and when to use it*, with keywords. | The description decides when the skill activates. It must not make the skill trigger on every coding prompt (see SPEC §5.6). |
| `license` | Optional. A licence name or a bundled licence file. | `license: Apache-2.0` (decided; SPEC D16). |
| `compatibility` | Optional. 1–500 characters. Environment requirements. | State the `git` and `python3` requirements. |
| `metadata` | Optional. String-to-string map. | Holds `version`. |
| `allowed-tools` | Optional and **experimental**. Space-separated list of pre-approved tools. | Leave it out of the MVP. Support varies between agents. |
| Body | No format restrictions. The whole body loads when the skill activates. | Keep the body to routing and gotchas. Put the detail in references. |
| Progressive disclosure | Metadata ~100 tokens. Body under 5,000 tokens recommended. Keep `SKILL.md` under 500 lines. Resources load on demand. | Measurable acceptance criterion (ROADMAP M1). |
| File references | Relative paths from the skill root, **one level deep** from `SKILL.md`. Avoid deep reference chains. | `references/*.md` must not tell the agent to load further reference files. |
| Validation | `skills-ref validate ./my-skill` | Run in CI. |

### 1.2 Guidance from the specification's guides

**Best practices** (`skill-creation/best-practices.md`):

- Include only what the agent would get wrong without being told. Do not explain general
  knowledge.
- Give defaults, not menus.
- Prefer procedures over declarations: teach the approach rather than the answer for one case.
- Keep a **gotchas** section in `SKILL.md`. It is "the highest-value content in many skills".
- Use checklists, validation loops ("do, validate, fix, repeat") and plan-validate-execute for
  fragile or batch operations.
- Say *when* to load each reference file, for example "Read X if Y", not "see references/".

**Using scripts** (`skill-creation/using-scripts.md`):

- Scripts must not prompt interactively.
- Document each script with `--help`. Errors should say what was expected and what to try.
- Write structured output (JSON) to stdout and diagnostics to stderr.
- Make scripts idempotent, give them distinct exit codes and a `--dry-run` for stateful
  operations, and keep output to a predictable size. Harnesses may truncate output beyond about
  10–30K characters, so summarise by default and paginate or write to a file for large output.
- Self-contained scripts should declare their dependencies inline (PEP 723 for Python) or have
  none.

**Evaluating skills** (`skill-creation/evaluating-skills.md`):

- Test cases live in `evals/evals.json` (prompt, expected output, files and assertions).
- Run each case **with and without** the skill (or against the previous version), each in a clean
  context.
- Record `timing.json` (`total_tokens`, `duration_ms`), `grading.json` (assertion pass/fail
  with evidence) and an aggregated `benchmark.json` with mean and standard deviation per
  configuration and the delta.
- A PASS needs concrete evidence. Remove assertions that pass in every configuration. Use blind
  comparison for holistic quality.

EverBench (see BENCHMARK.md) adopts this vocabulary and these file shapes, so results stay
compatible with the ecosystem's tooling. It extends them with structural ground truth and
multiple arms.

### 1.3 Distribution through skills.sh

Source: <https://github.com/vercel-labs/skills>, including `docs/creating-skills.md`.

- `npx skills add <owner>/<repo>` installs a skill into a project's agent directory
  (`.claude/skills/`, `.agents/skills/` and others). `--skill <name>` selects one skill and `-g`
  installs globally.
- **Discovery:** the CLI searches the repository root (when it has a `SKILL.md`), `skills/`,
  several dot-directories, and `.claude-plugin/` manifests. It walks up to three levels deep.
  A `skills.sh.json` file can add custom paths. When nothing is found in the standard locations,
  it falls back to a recursive search.
- `skills-lock.json` records a content hash of the installed skill folder. It skips
  `metadata.json`, `.git` and `__pycache__`.
- `metadata.internal: true` hides a skill from normal discovery. This is useful for
  pre-release builds.

**Consequences:**

1. Put the skill at `skills/evermap/`.
2. Keep benchmark fixtures, tests and docs **outside** `skills/evermap/`, so installs stay small
   and evaluator material never ships to users.
3. Never put a file named `SKILL.md` inside benchmark fixtures. The recursive fallback could
   otherwise discover a fixture as a skill.

## 2. Existing architecture-documentation skills

These are the closest prior art in the same distribution channel.

| Project | What it does (per its README) | Lesson for Evermap |
| --- | --- | --- |
| `softaworks/agent-toolkit` → `c4-architecture` (listed on skills.sh) | Generates C4 Mermaid diagrams in a four-step flow: scope, analyse, generate, document. | Diagram-first and one-shot. No persistent model, no evidence links, no update procedure. |
| `lmammino/c4-codebase-architecture-skill` | Builds C4 documentation from codebase evidence and **separates observed facts from inferences**. Asks follow-up questions when the code is not enough. Outputs Markdown + Mermaid, PlantUML or Structurizr DSL. | The observed/inferred split matches Evermap's epistemic model, and asking the human for missing context is the right move for decisions. Its output is still a document, not a maintained model. |
| `othmanadi/codebase-knowledge-builder` | Produces a source-cited Markdown knowledge artifact. Its README says explicitly that it does **not** maintain a queryable graph or index. | Source citations alone do not provide stable identifiers, change detection or impact traversal. |
| Hermes Agent `code-wiki` skill (inspired by Google Code Wiki) | Wiki pages and Mermaid diagrams built with ordinary file tools only. Explicitly excludes "why" narratives. | Shows that a skill with no infrastructure can produce useful reference docs. Evermap adds the missing "why" layer as *confirmed* decision records, never invented ones. |
| `aetox-skills/senior-architect-agent` | Architecture review with a `Direct`/`Inferred` evidence taxonomy and "file reading wins when graph contradicts". | Supports treating code reading as the final authority over any derived index. |

**Gap Evermap targets:** none of these skills claims all of the following:

- stable identifiers;
- incremental, Git-diff-driven maintenance that preserves unaffected knowledge;
- confirmed decision records that survive regeneration;
- invariants;
- impact analysis whose findings are labelled verified, potential or speculative.

## 3. Repository documentation generators

| Project | Relevant characteristics | Lesson |
| --- | --- | --- |
| **RepoAgent** (OpenBMB; EMNLP 2024 demo, arXiv 2402.16667) | LLM-generated documentation for Python repositories with a `repoagent diff` command that shows which docs a code change will update. | Incremental updates driven by Git changes are feasible and valued. Its unit is the code object, not architectural concepts or workflows. |
| **DeepWiki** (Cognition) / **deepwiki-open** / **OpenDeepWiki** | Hosted or self-hosted wiki with chat. deepwiki-open regenerates on demand. OpenDeepWiki has scheduled incremental updates and MCP. | Prose-first and service-hosted. Evermap avoids hosted services and vector stores in the MVP. |
| **CodeWiki** (arXiv 2510.24428) and **CodeWikiBench** | Open-source repo-level documentation framework, plus a benchmark that scores docs with hierarchical rubrics and agentic assessment. | Rubric-based, agent-graded documentation scoring is a usable pattern for EverBench's *semantic* metrics. It does not measure structural accuracy or update correctness. |
| **Google Code Wiki** | Hosted wiki for public repositories that is kept fresh automatically. | Shows the demand for freshness. It is hosted, not in-repo. |
| **OpenWiki** (LangChain) | An agent maintains Markdown docs in the repo, reading commits since the last run and using git diffs to update the wiki. | Closest in spirit to Evermap's update model. Evermap adds the structured canonical model, the evidence checks, stable IDs and impact analysis. |

## 4. Code knowledge graphs and MCP servers

Examples: CodeGraph, code-review-graph, Graphify, CodeGraphContext, GitNexus, Serena (LSP-based),
and Aider's repo map (tree-sitter plus a PageRank-ranked symbol map). A third-party comparison
(repowise.dev, 2026) reports that GitNexus requires a paid licence for work use. We have not
verified licences beyond the projects' own pages.

Common characteristics:

- **Strengths:** deterministic extraction with tree-sitter or LSP; exact callers and callees;
  "blast radius" over reverse call edges; incremental re-indexing through file watchers. These
  are excellent for *symbol-level* structure.
- **Limitations relative to Evermap's goals:** they don't capture semantics such as transaction
  boundaries, business rules, authorization intent, idempotency or workflows. Most need a running
  server, a database (SQLite, RocksDB, graph DBs) or embeddings, and most are exposed over MCP,
  all of which the MVP excludes. Their indexes are usually caches, not reviewed and versioned
  knowledge.

**Lesson:** Evermap should not compete on symbol-level call graphs. A later milestone could
accept their output as *optional evidence accelerators* (see SPEC §11), while the reviewed
canonical model stays the source of truth.

## 5. Architecture-as-code, catalogues and contracts

| Standard or tool | What Evermap borrows |
| --- | --- |
| **C4 model** / **Structurizr DSL** | Levels of abstraction: system, container (deployable), component. Evermap's `component.type` mirrors the container and component distinction. Structurizr DSL export is a possible later renderer. |
| **Backstage Software Catalog** | Typed, human-readable entity references (`kind:namespace/name`) and typed relations (`dependsOn`, `providesApi` and so on). Evermap's ID format `kind:slug` comes from this. |
| **arc42** | Sections for constraints, cross-cutting concepts, decisions and risks. These informed the decision and invariant kinds. |
| **OpenAPI / AsyncAPI** | Evermap **references** existing contract files as evidence instead of duplicating them. Contracts are summarised to the field level only where lineage or validation needs it. |

## 6. Architecture Decision Records

- **MADR** (Markdown Architectural Decision Records, MIT or CC0-1.0) gives the decision template:
  status (`proposed | rejected | accepted | deprecated | superseded by …`), date, deciders,
  context, decision drivers, considered options with pros and cons, decision outcome,
  consequences and confirmation.
- **Nygard-style ADRs** and tooling such as adr-tools and log4brains: one immutable, numbered
  record per decision. Later records supersede earlier ones instead of editing them.
- **Y-statements:** "In the context of … facing … we decided … and neglected … to achieve …
  accepting …"

**Evermap adopts** MADR's status vocabulary and its option and outcome structure, so records can
be rendered as MADR-compatible Markdown. **Evermap adds** per-section provenance (`confirmed`,
`inferred`, `unknown`) so that reasoning inferred from code is never presented as historical
fact.

## 7. Data lineage

- **OpenLineage** models jobs, datasets and runs, with column-level lineage expressed as facets
  (input fields → output field plus transformation description and type).

**Evermap borrows** the column-level idea: each entity field can declare its origins (input,
derived, generated, external, copied) together with the operation and transformation that
produce it. Evermap records *static* lineage from code only. It never records runtime run data.

## 8. Static analysis and architecture rules

These could serve as reference tools for the benchmark's ground truth, or as later optional
accelerators:

- **TypeScript compiler API / ts-morph:** call and import graphs for TS fixtures.
- **dependency-cruiser / madge:** module dependency graphs and rule checking for JS/TS.
- **tree-sitter:** language-agnostic parsing.
- **SCIP** (Sourcegraph): language-agnostic symbol monikers that stay stable across indexing.
  These are useful prior art for anchoring evidence to symbols rather than to lines.
- **CodeQL, Semgrep:** data flow and pattern queries.
- **ArchUnit / ts-arch:** architecture rules written as executable tests. Evermap invariants can
  point to such tests as `verified_by` evidence.

The engineering principles keep these out of the MVP skill's runtime: no infrastructure, and
language-agnostic agent reasoning. **EverBench's evaluator** may use them (§9) to cross-check
human-written ground truth for omissions. The agent under evaluation never sees their output.

## 9. Benchmarks and evaluation methodology

| Work | Relevance |
| --- | --- |
| **SWE-bench** | Downstream task success judged by **hidden tests** run after the agent finishes. EverBench uses this protocol for its "implement a change safely" tasks. |
| **CodeWikiBench** | Rubric- and agent-graded documentation quality. Pattern for semantic accuracy scoring. |
| **SWR-Bench** (arXiv 2509.01494) | Real PRs with ground-truth "change points". It reports that automated reviewers suffer from many **false positives**. This is why EverBench includes *decoys* and measures risk-detection precision, not just recall. |
| **BIG-bench canary strings** | A unique GUID embedded in benchmark files so that training-data or context contamination can be detected. EverBench embeds a canary in every evaluator-only file. |
| **skill-creator / Agent Skills eval guide** | With-skill and without-skill runs, `timing.json` and `benchmark.json` conventions (§1.2). |

## 10. Reuse decisions

| Component | Decision | Where |
| --- | --- | --- |
| Agent Skills spec + `skills-ref validate` | **Adopt.** Run in CI on `skills/evermap/`. | ROADMAP M1 |
| `skills` CLI (`npx skills add . --skill evermap`) | **Adopt** for install smoke tests. | ROADMAP M1, M8 |
| Claude Code (`claude -p --output-format json`) and Codex CLI (`codex exec --json`, `--output-schema`) headless modes | **Adopt** as the two agents under test *and* as the cross-vendor ground-truth and grading judges. Codex discovers repo skills in `.agents/skills/`; Claude Code in `.claude/skills/`. | BENCHMARK §3.2, §5.1, §8.3 |
| Eval file conventions (`evals.json`, `timing.json`, `grading.json`, `benchmark.json`) | **Adopt and extend.** | BENCHMARK §7 |
| MADR status and option vocabulary | **Adopt** in the `decision` kind and its renderer. | MODEL §6.9 |
| Backstage-style `kind:slug` IDs and typed relations | **Adapt.** | MODEL §3 |
| OpenLineage column-level lineage concepts | **Adapt** as field `origins`. | MODEL §6.4 |
| Mermaid | **Adopt** for all rendered diagrams. GitHub renders it natively, so no binary is needed. | SPEC §7 |
| C4 levels | **Adapt** as `component.type`. Diagrams use Mermaid `flowchart`, not Mermaid's C4 syntax, which Mermaid labels experimental. | SPEC §7 |
| tree-sitter, SCIP, ts-morph, dependency-cruiser | **Defer** for the skill (possible later accelerators). **Use** in the benchmark evaluator only. | BENCHMARK §4.3 |
| Code-graph MCP servers, vector DBs, hosted wikis | **Exclude** from the MVP (principle: no infrastructure). | SPEC §2 |
