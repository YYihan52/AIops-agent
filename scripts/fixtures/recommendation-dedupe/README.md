# recommendation-dedupe fixture

Seeds the `feature/dedupe` branch on `github.com/HuaiNan54321/recommendation`
(branched from the clean pre-leak baseline, same rationale as
`recommendation-ranking/README.md` — see that file for "why separate
branches" and "how the code-fix Agent gets onto the right branch").

**Bug**: `dedupe.py::dedupe_ids()` computes the membership key with
`.lower()` but stores the un-lowercased key in the `seen` set, so the
lookup key never matches what's stored — every duplicate slips through.
Regression test: `test_dedupe.py::test_dedupe_removes_exact_duplicates`.

Used by scenario `s11_dedupe` (see `eval/expected.json`, `alerts/s11_dedupe.json`).
