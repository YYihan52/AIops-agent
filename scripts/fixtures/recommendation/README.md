# recommendation service (demo fixture)

> **Historical note (2026-09, Phase B)**: this single-bug fixture is no longer the
> seeding source. All planted bugs (this leak + ranking/dedupe/stats + the newer
> pagination/cache/pricing) now live together on **master** of the upstream repo,
> seeded from `scripts/fixtures/recommendation-master/` via
> `scripts/seed_github_history.py`. Kept as the original s4-only reference.

Stand-in Python recommendation service used by the AIOps agent's **s4 capstone**
(code-fix → PR path).

## Build / test
```bash
pip install -r requirements.txt
python -m py_compile $(git ls-files '*.py')
pytest -q
```

`test_memory_is_bounded` fails on the planted-leak revision and passes once the
unbounded module-level accumulation is bounded.
