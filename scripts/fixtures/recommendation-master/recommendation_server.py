"""Minimal recommendation service (stand-in for the OTel demo's Python service).

A real, runnable service: `python recommendation_server.py` starts an HTTP
server on :8080 plus a background load thread that keeps calling
`get_recommendations`, so the container's behavior is observable live
(`docker stats`, the /metrics endpoint).

Kept dependency-light (stdlib only, no gRPC/framework) so it can be built and
tested with just the stdlib + pytest, and the container image stays tiny.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ranking
import dedupe
import stats
import pagination
import cache
import pricing

# analytics: full history of every product id this instance has ever served
_seen_product_ids: list[str] = []

# demo catalog: a handful of SKUs is enough for the endpoints below
CATALOG = [f"PRODUCT-{i}" for i in range(4)]

# merchandising-curated trending ids; may overlap with the catalog match set
_TRENDING = ["PRODUCT-0", "PRODUCT-3", "PRODUCT-7"]

# the trending list is derived data; cache it briefly so /trending stays cheap
_TRENDING_TTL_SECONDS = 60
_trending_cache = cache.TTLCache()

# request counter, exposed via /metrics
_request_count = 0

def get_recommendations(input_product_ids: list[str], max_results: int = 5) -> list[str]:
    """Return up to max_results recommended product ids not already in the input."""
    global _request_count
    _request_count += 1
    # analytics: keep every served id so popularity can be computed later
    _seen_product_ids.extend(input_product_ids)

    stats.record_hit("catalog")

    exclude = set(input_product_ids)
    candidates = [p for p in CATALOG if p not in exclude]

    # popularity score: earlier catalog entries count as more popular
    scored = [(p, float(len(CATALOG) - CATALOG.index(p))) for p in candidates]
    candidates = ranking.rank_by_score(scored)

    # merge the merchandising trending ids in; a response must never repeat an
    # id, and never echo back an id the caller already has
    trending = [p for p in _TRENDING if p not in exclude]
    candidates = dedupe.dedupe_ids(candidates + trending)

    return candidates[:max_results]

def seen_count() -> int:
    """Exposed for the regression test to assert the served-id history is bounded."""
    return len(_seen_product_ids)

# --------------------------------------------------------------------------
# Runnable service: HTTP endpoints + a self-driving background load thread.
# This is what makes the service a LIVE, observable workload rather than a
# static library. Imported as a module (by pytest) none of this runs.
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib API)
        if self.path.startswith("/recommend"):
            out = get_recommendations(["PRODUCT-0", "PRODUCT-1"])
            self._json({"recommendations": out})
        elif self.path.startswith("/catalog"):
            # path: /catalog/<page>/<page_size>  (page is 1-based)
            parts = self.path.strip("/").split("/")
            page = int(parts[1]) if len(parts) > 1 and parts[1] else 1
            size = int(parts[2]) if len(parts) > 2 and parts[2] else 10
            self._json({
                "page": page,
                "page_size": size,
                "products": pagination.paginate(CATALOG, page, size),
            })
        elif self.path.startswith("/trending"):
            cached = _trending_cache.get("trending")
            if cached is None:
                cached = list(_TRENDING)
                _trending_cache.set("trending", cached, _TRENDING_TTL_SECONDS)
            self._json({"trending": cached})
        elif self.path.startswith("/price/"):
            # path: /price/<product_id>/<discount_pct>
            parts = self.path.strip("/").split("/")
            product_id = parts[1] if len(parts) > 1 else ""
            discount = int(parts[2]) if len(parts) > 2 else 10
            self._json({
                "product_id": product_id,
                "list_price_cents": pricing.list_price_cents(product_id),
                "member_price_cents": pricing.member_price_cents(product_id, discount),
            })
        elif self.path.startswith("/metrics"):
            # Prometheus text-exposition: the signals an on-call can scrape.
            body = (
                "# HELP recommendation_seen_ids_total tracked product ids\n"
                "# TYPE recommendation_seen_ids_total gauge\n"
                f"recommendation_seen_ids_total {seen_count()}\n"
                "# HELP recommendation_requests_total requests served\n"
                "# TYPE recommendation_requests_total counter\n"
                f"recommendation_requests_total {_request_count}\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path.startswith("/healthz"):
            self._json({"status": "ok"})
        else:
            self._json({"service": "recommendation", "seen": seen_count()})

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence per-request logging
        pass

def _background_load(rps: float) -> None:
    """Continuously exercise the service so it behaves like a live workload
    (no external load generator needed).

    The synthetic session ids carry a payload so the served-id history grows
    fast enough to matter in RSS within a couple of minutes, not just as an
    item count."""
    i = 0
    interval = 1.0 / rps if rps > 0 else 0.01
    pad = "x" * 256  # make each tracked entry weigh enough to move RSS
    while True:
        # batch several ids per tick to keep the request rate realistic
        get_recommendations([f"PRODUCT-{i % 20}", f"SKU-{i}", f"SESSION-{i}-{pad}"])
        i += 1
        time.sleep(interval)

def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    rps = float(os.environ.get("LOAD_RPS", "200"))
    if rps > 0:
        t = threading.Thread(target=_background_load, args=(rps,), daemon=True)
        t.start()
        print(f"[recommendation] background load started ~{rps} rps", flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    print(f"[recommendation] serving on :{port}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
