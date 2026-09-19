# Benchmark: agent codebase-retrieval methods

The numbers in the README come from this benchmark. This page gives the method, the
full results, the 10× scalability pass, the N=3 replication, and the caveats. The raw
per-run data and charts sit next to this file in [`benchmark/`](benchmark/).

**Run dates:** 2026-06-13 (base run) and 2026-06-14 (10× pass and replication).
**Agent under test:** Claude Sonnet, one fresh agent per run.
**Method under test:** the `ledger` rows below are what ships as `agent-nav`. The ranker
measured here is the same simple lexical ranker in `agent_nav/ledger.py` — not BM25, not
embeddings.

The harness that ran these (agent loop, synthetic repos, grader) lives in a private
research repo and is not published. What is published is everything needed to check the
claims: the probes, every graded run, the aggregate tables, and the charts.

## 1. Problem statement

As AI agents take on real software tasks, *how a codebase exposes itself to an agent*
becomes an engineering choice. A common pattern is a hand-maintained `index.md` that
`CLAUDE.md` routes to: the agent loads the whole index into context and navigates from
it. The worry is that this doesn't **scale**. As the repo grows, the index grows, and every
task pays a context-window tax before the agent does any work.

This benchmark asks: **which navigation scaffolding produces better agent behavior?** Not
just "which retrieves the right file," but which is cheap, robust, and resistant to the
failure modes agents actually exhibit (satisficing, fabricating, chasing decoys, trusting
stale pointers).

## 2. Method

**Four methods**, each a swappable mechanism the agent is told to use:

| method | mechanism |
|---|---|
| **ledger** (= agent-nav) | run `find "<query>"` → ranked match over a `path → description` ledger, get candidate paths |
| **index** | read `index.md` (full file listing with descriptions) into context, navigate from it |
| **tag** | run `find_by_tag "<query>"` → match `@domain/@type/@keywords` header tags |
| **baseline** | bare repo + native `grep`/`glob` (the control) |

**Two conditions:** `forbid_native_search` (use only the mechanism) and
`allow_native_search` (mechanism + grep). Because tool-gating is prompt-enforced in the
agent loop, **compliance** (did the agent obey "use only the mechanism?") is itself
measured.

**Synthetic repo:** 44 files across 6 domains × 6 layers (frontend/backend/api/db/docs/
runbook) in mixed formats, with planted `*_DEPRECATED` distractors and embedded
deterministic `FACT:` values so answer keys are exact.

**10 behavior probes** ([`benchmark/probes.json`](benchmark/probes.json)), each targeting
a known agent failure mode: distractor, multi-hop, satisficing, hallucination,
description-trust, stale-pointer, paraphrase, cross-domain, lost-in-middle,
instruction-adherence. Each has ground-truth files and an answer key.

**Harness:** a fresh Sonnet agent per `(query × method × condition)` = **80 isolated runs**,
fanned out in parallel, each returning a structured trace (files read, ranked order, tool
calls, final answer, refusal flag). A grader scores every trace. Metrics: precision, recall,
F1, MRR, task-success, hallucination-rate, tool-calls, and **context-inflation** (tokens a
method injects before the agent acts, computed statically).

**Aggregation:** every table below reports the **arithmetic mean** over runs. Nothing here
is a median.

**Scoring rules fixed after a pilot:** refusal-expected queries credit `refused=true` as
success, and scaffold files (`index.md`, the ledger file) are excluded from precision/MRR.
Their cost is captured by context-inflation instead, avoiding a double-penalty.

## 3. Base run results (44-file repo)

Mean over 10 queries × 2 conditions = 20 runs per method.
Raw rows: [`benchmark/runs.csv`](benchmark/runs.csv).

| method | precision | recall | F1 | MRR | task-success | hallucination | tool-calls | **context-inflation** | compliance |
|---|---|---|---|---|---|---|---|---|---|
| **ledger** | 0.82 | 0.87 | 0.81 | **0.97** | **1.00** | 0.00 | 4.0 | **17 tok** | 1.00 |
| **tag** | 0.82 | 0.87 | 0.81 | 0.88 | **1.00** | 0.00 | 3.5 | **15 tok** | 1.00 |
| **index** | 0.82 | 0.87 | 0.81 | 0.93 | 0.95 | 0.00 | 3.2 | **990 tok** | 1.00 |
| **baseline** | 0.61 | 0.72 | 0.62 | 0.70 | **1.00** | 0.00 | 3.7 | **0 tok** | 1.00 |

Per-behavior task-success was 1.00 everywhere **except one cell**: the only failure across
all 80 runs was **index × forbid_native_search × stale_pointer** (0/1).

Notable points:
- **Scaffolding beats raw grep on realistic queries.** The baseline lagged on precision
  (0.61 vs 0.82), recall (0.72 vs 0.87) and F1 (0.62 vs 0.81). On multi-file, paraphrased,
  and cross-domain queries, plain grep opened more wrong files and missed some relevant
  ones. The three scaffolds all lifted retrieval quality to the same level.
- **`index` pays a ~60× context tax for identical retrieval quality.** Same precision/
  recall/F1 as ledger/tag, but **990 tokens of upfront context** vs ~15. With a 44-file
  repo that's already large; it grows linearly with the repo.
- **`ledger` has the best ranking** (MRR 0.97). Its description-scored output surfaces the
  right file first most reliably.
- **`index` is the most brittle to staleness.** When `index.md` pointed at a moved file
  and native search was forbidden, the agent followed the dead link and failed. `ledger`
  and `tag` recovered even under forbid, because their search step re-scans *actual* file
  content rather than a cached listing; `index` only recovered once grep was allowed.
- **Agents were fully compliant (1.00) and never hallucinated (0.00)**, including on the
  hallucination and stale-pointer probes. Sonnet reliably used the prescribed mechanism
  even with grep available, and correctly refused the non-existent-policy query in every
  method instead of fabricating.

Charts: [F1 by method](benchmark/charts/f1_by_method.png) ·
[context-inflation by method](benchmark/charts/context_inflation_by_method.png) ·
[hallucination by method](benchmark/charts/hallucination_by_method.png).

### Interpretation of the base run

The three scaffolds retrieve equally well and all beat raw grep, so the tie-breakers are
the agentic properties:

1. **Context economy.** `index.md` buys its retrieval quality with a large, ever-growing
   context cost that the query-based methods avoid entirely (~15 tokens, on-demand). The
   cost is paid on *every* task, crowding out the agent's actual working context.
2. **Robustness to drift.** A static index is a single point of failure: when it goes
   stale, an agent that trusts it (and can't fall back) fails silently. Query-based
   methods degrade gracefully because they re-derive answers from live content.
3. **Ranking matters for satisficing agents.** Agents tend to stop at the first plausible
   hit. A method with high MRR (ledger, 0.97) puts the right file first, so even a
   "lazy" agent lands correctly.

**Caveats on the base run:** the queries are answerable and the repo is small, so
retrieval quality saturated near the top for all scaffolds. The separation came from
*cost* and *robustness*, not raw accuracy. The recall column is dragged down ~0.1 by the
stale-pointer probe (its ground-truth file was physically moved, so recall against the
original path is 0 by construction even when the agent answered correctly). At N=10 the
retrieval-quality ties should be read as "comparable," not "provably equal."

## 4. Scalability pass (10× repo)

The base run left one open question: does `index`'s context tax and brittleness widen as
the repo grows? This pass re-runs the same four methods against a **10× bloated** copy of
the synthetic repo: 368 files vs 44, with 9 numbered clone domains
(`payments2…payments10` etc.) plus distractors around each canonical domain.

**Sampled, not exhaustive:** **8 behavior probes × 4 methods × 2 conditions = 64 runs**.
Probes run: distractor, multi-hop, hallucination, description-trust, stale-pointer,
paraphrase, cross-domain, lost-in-middle. Not run: satisficing, instruction-adherence
(judged redundant for the scaling question).

For the stale-pointer probe the scenario was recreated at scale (one runbook moved to
`_moved/`, scaffolds left pointing at the dead path) for the duration of the run.

**Comparison is matched:** base → 10× deltas compare against the **same 8 queries** of the
base run. Raw rows: [`benchmark/runs_10x.csv`](benchmark/runs_10x.csv). Full delta
table: [`benchmark/deltas_10x.md`](benchmark/deltas_10x.md).

### Results (replicate 1)

Mean over 8 queries × 2 conditions = 16 runs per method:

| method | F1 | MRR | task-success | hallucination | tool-calls | **context-inflation** |
|---|---|---|---|---|---|---|
| **ledger** | 0.74 | **0.97** | **1.00** | 0.00 | 4.5 | **17 tok** |
| **index** | 0.77 | 0.94 | 0.94 | 0.00 | 2.9 | **9,442 tok** |
| **baseline** | 0.75 | 0.80 | **1.00** | 0.00 | 4.1 | **0 tok** |
| **tag** | **0.44** | **0.31** | **0.75** | 0.00 | 6.4 | 15 tok |

Base → 10× deltas (matched, 8 queries):

| method | F1 | MRR | task-success | context-inflation |
|---|---|---|---|---|
| **ledger** | 0.76 → 0.74 (−0.03) | 0.97 → 0.97 (0.00) | 1.00 → 1.00 | 17 → 17 |
| **index** | 0.76 → 0.77 (+0.01) | 0.91 → 0.94 (+0.03) | 0.94 → 0.94 | **990 → 9,442 (+8,452, ~9.5×)** |
| **baseline** | 0.65 → 0.75 (+0.10) | 0.75 → 0.80 (+0.05) | 1.00 → 1.00 | 0 → 0 |
| **tag** | 0.76 → **0.44** (−0.32) | 0.84 → **0.31** (−0.53) | 1.00 → **0.75** (−0.25) | 15 → 15 |

> The F1 column understates `ledger`/`index`/`baseline`: the stale-pointer probe moves
> the ground-truth file, so recall against its original path is **0 by construction for
> every method** even when the agent recovers the answer. For that probe, read
> **task-success**, not F1. Hallucination stayed 0.00 and compliance 1.00 across all 64 runs.

Charts: [F1](benchmark/charts_10x/f1_by_method.png) ·
[context-inflation](benchmark/charts_10x/context_inflation_by_method.png) ·
[hallucination](benchmark/charts_10x/hallucination_by_method.png).

### What scale revealed

**1. `index`: retrieval quality holds, context tax scales ~9.5×.** F1/MRR/task-success are
flat; upfront context jumped **990 → 9,442 tokens**, tracking file count. `index`'s *only*
failure in 16 runs was stale-pointer under `forbid`: it followed the dead index entry and,
barred from grep, couldn't recover. The index doesn't get answers *wrong* at scale, it gets
*expensive*, and it's brittle to drift.

**2. `tag` collapses, across every behavior type, not just decoys.** F1 −0.32, MRR −0.53,
and **task-success −0.25** (the only method that gets answers outright wrong at scale).
Under `forbid`, tag **refused or failed on 5 of 8 probes**. Root cause, verified directly:
the locator returns a capped top-8, and the 9 numbered clone domains share byte-identical
`@domain/@type/@keywords` headers with the canonical file. They tie, swamp the output, and
**the canonical file never appears**. This is a *worse* failure than `index`'s because it's
invisible: no token-cost signal, just wrong or refused answers.

**3. `ledger` wins, and its robustness held up under the probes built to break it.**
Task-success 1.00, MRR 0.97 (best, unchanged), flat 17-token cost. Two targeted stress
tests:
- **description-trust:** the ledger's description ranks the canonical file first amid 9
  identical-tag clones. Succeeds where `tag` refuses.
- **stale-pointer:** with the ledger entry pointing at the moved file, the agent still
  recovered the correct value under both conditions, where `index`+`forbid` failed.
  (Caveat in §6.)

We went looking for a ledger failure mode and did not find one.

**4. Correction to a base-run inference: paraphrase did *not* defeat grep here.** The base
write-up inferred that free-text descriptions absorb paraphrase better than raw grep. On
the paraphrase probe, **baseline grep succeeded with F1 1.00**: the query term still matched
a path by substring, so exact search was never actually challenged. At this probe's
difficulty the paraphrase advantage is unconfirmed. Baseline's overall F1 even *rose*
base → 10× (+0.10), because this 8-query set leans on grep-friendly, literal-token queries.

## 5. Replication (N=3 at 10×)

Every 10× cell was re-run to **3 independent replicates** (192 runs total; the §4 table is
replicate 1). [`benchmark/variance_10x.md`](benchmark/variance_10x.md) reports per-method
mean ± std over the three replicate-level means:

| method | F1 | MRR | task-success | tool-calls |
|---|---|---|---|---|
| **ledger** | 0.72 ± 0.01 | **0.98 ± 0.01** | **1.00 ± 0.00** | 4.5 ± 0.1 |
| **index** | 0.79 ± 0.01 | 0.93 ± 0.00 | 0.96 ± 0.03 | 2.8 ± 0.1 |
| **baseline** | 0.72 ± 0.02 | 0.78 ± 0.03 | 0.94 ± 0.05 | 4.4 ± 0.2 |
| **tag** | **0.40 ± 0.03** | **0.30 ± 0.00** | **0.69 ± 0.05** | 5.9 ± 0.4 |

**The headline ordering is stable, not noise.** Std is ≤0.03 on F1/MRR for every method,
so `tag`'s collapse and `ledger`'s win both reproduce. `ledger` got **every probe right in
all 3 replicates** (task-success 1.00 ± 0.00 across 48 runs), including the stale pointer.

**Where the stochasticity lives:** only **7 of 64 cells flipped** their task-success outcome
across replicates, and they cluster in the hard spots, almost all under `forbid`:
- **stale-pointer recovery is flaky for everyone except ledger.** `index/forbid` went
  [0,1,0] and `baseline` flipped under both conditions. Recovering a *moved* file is a
  coin-flip when the agent can't fall back to search; `ledger` was the only method that
  recovered it every time.
- **`tag` under `forbid` flickers** between grabbing a wrong clone and refusing. Its failure
  is consistent in *severity* even though the specific outcome wobbles.

The robust statement is: **only `ledger` recovered the stale pointer deterministically.**

## 6. Caveats

- **N=10 probes.** Directional, not a large-scale eval.
- **Synthetic repo.** Scores come from a constructed benchmark repo, not a survey of real
  projects. Clone domains share *identical* tags by construction; a real 10× repo would
  have noisier tags, so true `tag` degradation likely sits between the base and 10× numbers.
- **Sampled 10× pass.** 8 of 10 probes. With N=3 the conclusions are stable, but absolute
  F1 values are anchored to this probe mix.
- **`forbid` is prompt-enforced, not sandboxed.** `used_native_search` and tool-calls are
  self-reported. The ledger's stale-pointer recovery under `forbid` may lean on directory
  traversal the condition meant to exclude. The *answer* was right; the mechanism's purity
  is not guaranteed.
- **Paraphrase-beats-grep is unconfirmed.** The paraphrase probe didn't stress it.
- **Total token cost was not metered.** Context-inflation is a static count of what each
  method injects before the agent acts, not end-to-end spend.
- **Simple lexical ranker.** The shipped ranker is what was measured. No embeddings, no
  BM25.

## 7. Bottom line

Prefer a queryable mechanism over a loaded index, and specifically **a ranked,
description-based ledger**. At scale the two cheap query methods are *not*
interchangeable: `tag` degrades as badly as `index`, for the opposite reason (retrieval
quality rather than context cost). Description-based ranking is what distinguishes the
canonical file from its near-duplicates, and its cost stays flat.
