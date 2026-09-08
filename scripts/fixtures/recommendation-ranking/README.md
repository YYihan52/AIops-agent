# recommendation-ranking fixture

> **Historical note (2026-09, Phase B)**: the feature-branch seeding below is
> retired. The `feature/*` branches were deleted from the upstream repo; this
> bug (same code, same regression test) now lives directly on **master**, seeded
> from `scripts/fixtures/recommendation-master/`. This directory remains as a
> local docker-fixture source (it is still referenced by out-of-repo tooling).

Seeds the `feature/ranking` branch on `github.com/HuaiNan54321/recommendation`,
branched from the clean pre-leak baseline commit (**not** from `master`,
which has the s4 planted memory leak — see "Why separate branches" below).

**Bug**: `ranking.py::rank_by_score()` sorts ascending instead of descending,
so the least-popular candidates surface first. Regression test:
`test_ranking.py::test_rank_by_score_orders_most_popular_first`.

Used by scenario `s10_ranking` (see `eval/expected.json`, `alerts/s10_ranking.json`).

## Why separate branches instead of one shared feature branch

An earlier version of this fixture put all three new bugs (ranking, dedupe,
a stats race) on one shared `feature/personalization` branch. That broke the
code-fix Agent's "run the full `pytest` suite, all green before opening a PR"
gate for every scenario except whichever ran first — the other two bugs'
failing tests were still sitting there, unrelated to what that scenario's
alert was about. Each bug now gets its own branch (based on the same clean
baseline, so `test_memory_is_bounded` also passes cleanly), so a scenario's
test suite only ever has ONE bug present.

## How the code-fix Agent gets onto the right branch

Deterministic, not LLM-dependent: the alert JSON has a `fixture_branch`
field. `agent/run.py` reads `alert["fixture_branch"]` and passes it straight
through to `code_fix(rc, branch=...)`, which passes it to
`clone_service_repo(..., branch=...)` — a plain `git clone --branch <branch>`.
The Agent never has to infer or relay which branch to use; it's simply
already checked out when the Agent starts working. See
`agent/agents/prompts.py:fix_prompt()` for how the Agent is told the branch
it's on (so it can set the right PR `base`).

(An earlier attempt relied on the diagnose Agent noticing "this bug lives on
branch X" from the alert text and relaying that into `remediation_detail`
for the code-fix Agent to read — that failed in practice: the diagnose
Agent, primed by `search_past_incidents` hits on the well-known s4 memory
leak, defaulted to investigating/fixing that instead of the branch's actual
bug. Don't repeat that mistake — keep this deterministic.)
