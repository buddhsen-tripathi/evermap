# Real-repository benchmark: bucket0

This is the real-repository track of EverBench ([BENCHMARK §2.3](../../docs/BENCHMARK.md)).
[bucket0](https://github.com/buddhsen-tripathi/bucket0) is a public, open-source application
(TypeScript on Cloudflare Workers, Hono and Drizzle) owned by the maintainer. It is mapped as it
is, with no planted properties, at a pinned commit. Anyone can clone it, rerun the ground truth
and a mapping run, and check the numbers below.

| File | Does |
| --- | --- |
| `gt.py` | Mechanical ground truth from a checkout: HTTP endpoints (one per exported method in `server/routes/**/route.ts`), Drizzle tables with columns, primary keys, foreign keys and line ranges (`db/schema.ts`), and queues, Durable Objects, containers and R2 buckets (`wrangler.jsonc`) |
| `gt.json` | `gt.py` output at the pinned commit, kept so it can be checked without a clone |
| `run.py` | One first-map run on a fresh clone, following the skill's own procedure (`discover.md` "Calling agent", `headless.md`): init, estimate, one headless worker per packet, a continuation for packets that leave files unowned, the final pass, sync and render |
| `score.py` | Scores a run against `gt.py` and writes `results/<run>.json` |
| `results/` | Numbers only: cost, tokens, turns, time, model health and recall |
| `raw/` | Worker outputs and logs, not committed |

## Run

From the root of this repository, with Claude Code or Codex CLI installed and signed in:

```bash
python3 bench/bucket0/run.py --agent claude --run claude-sonnet-2 --model sonnet
```

```bash
python3 bench/bucket0/run.py --agent codex --run codex-2
```

`run.py` clones `https://github.com/buddhsen-tripathi/bucket0.git` at
`26387d87e6fa958a82a9ed75674bfcc873a562e5` into `evermap-bench/<run>` under the system temp
folder (set `EVERMAP_BENCH_WORK` to change it) and uses the skill in this checkout. `--source` and
`--commit` choose another clone or commit. Runs have no cost cap; each logs its cost and tokens.
To check the ground truth, or to score an existing map:

```bash
python3 bench/bucket0/gt.py <bucket0 checkout>
```

```bash
python3 bench/bucket0/score.py --run NAME --work <checkout>
```

## Scope

Every run uses the same manifest: `server/**`, `lib/**`, `workers/**`, `media/**`, `db/**`,
`wrangler.jsonc`, `vite.config.ts`, `web/root.tsx` and `web/routes.ts`, without docs or
Markdown. All four extra diagram kinds are chosen, and Excalidraw and Eraser files are written.

## What is scored

- **Endpoints:** an operation that cites the route file and names the method in its trigger,
  name or id. A route file with one method needs only the citation. `OPTIONS` handlers (CORS
  preflight) are counted apart.
- **Tables, columns, foreign keys:** the entity whose evidence covers most of the table's
  definition in `db/schema.ts` (else the one named after it), its fields against the columns,
  and foreign-key constraints whose `references` point at the right entity.
- **Infrastructure:** each queue, Durable Object, container and R2 binding named anywhere in
  the model.
- **Model health:** validation errors and warnings, V16 warnings, evidence freshness, unowned
  files.
- **Cost:** dollars, input, cached and output tokens, turns and worker time per run.

The ground truth is mechanical, so it covers only what the code declares. Workflows, rules and
invariants are not scored here.

## Results

First maps at the pinned commit, skill at Evermap `7bcda78` plus the V16 language fix
(`c3a927d`), all diagram kinds chosen. `score.py` reruns `validate`, so V16 is scored with the
fix.

| Run | Workers | Cost | Worker time | Elements | Endpoints | Tables | Columns | Foreign keys | Errors / warnings | Unowned files |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Earlier map (older skill; map not published, so not reproducible) | — | — | — | 285 | 124/128 | 22/22 | 103/255 | 0/11 | 0 / 25 | 115 |
| Claude Code, Sonnet | 14 (1 continuation) | $9.89 | 38 min | 322 | 125/128 | 22/22 | 255/255 | 11/11 | 0 / 0 | 0 |
| Codex, gpt-6.1-sol (high) | 13 | 1.38M tokens | 94 min | 736 | 128/128 | 22/22 | 255/255 | 11/11 | 0 / 0 | 0 |

Endpoints leave out 7 `OPTIONS` preflight handlers. Claude missed `POST` on the agent keys
route and both methods of the chunked-upload cleanup route. Claude cost 9.5M cached input
tokens, 1.27M cache writes and 386K output tokens over 427 turns; the estimate's billed range
was 3.2M to 9.9M input tokens. Codex reports one total per worker. Codex's run used the skill
before the V16 fix for its first 8 packets.
