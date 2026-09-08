# recommendation-race-stats fixture

> **Historical note (2026-09, Phase B)**: the feature-branch seeding below is
> retired. The `feature/*` branches were deleted from the upstream repo; this
> bug (same code, same regression test) now lives directly on **master**, seeded
> from `scripts/fixtures/recommendation-master/`. This directory remains as a
> local docker-fixture source (it is still referenced by out-of-repo tooling).

Seeds the `feature/race-stats` branch on `github.com/HuaiNan54321/recommendation`
(branched from the clean pre-leak baseline, same rationale as
`recommendation-ranking/README.md` — see that file for "why separate
branches" and "how the code-fix Agent gets onto the right branch").

**Bug**: `stats.py::record_hit()` does an unsynchronized read-modify-write on
a shared dict; under `ThreadingHTTPServer`'s concurrent request threads this
loses increments. Regression test: `test_stats.py::test_record_hit_is_thread_safe`
(forces frequent GIL switches with `sys.setswitchinterval` so the race
reproduces deterministically instead of relying on scheduling luck).

Used by scenario `s12_race` (see `eval/expected.json`, `alerts/s12_race.json`).
