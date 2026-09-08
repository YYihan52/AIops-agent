# recommendation service (demo)

Minimal stand-in recommendation service used by the AIOps example's code-fix
exercises. Stdlib-only Python; `python recommendation_server.py` starts an
HTTP server on :8080 plus a background load thread.

## Endpoints

- `GET /recommend` — top-5 recommendations: catalog match + merchandising
  trending ids, deduped, ranked by popularity
- `GET /catalog/<page>/<page_size>` — paginated catalog browsing (1-based page)
- `GET /trending` — merchandising-curated trending ids (cached, 60s TTL)
- `GET /price/<product_id>/<discount_pct>` — list price & member price, in cents
- `GET /metrics` — Prometheus text exposition
- `GET /healthz` — liveness

## Build / test
```bash
pip install -r requirements.txt
python -m py_compile $(git ls-files '*.py')
pytest -q
```

Known open issues are tracked in [BUGS.md](BUGS.md) — please fix **one issue
per PR** and don't bundle unrelated changes.
