#!/bin/sh
set -e

# Generate seed JSON if missing. Idempotent — the build step already does
# this, but re-running here catches: bind-mounted empty data dirs, fresh
# anonymous volumes mounted over /app/mock_saas/seed/data, etc.
if [ ! -f /app/mock_saas/seed/data/m000_shopify_orders.json ]; then
    echo "[mock_saas] seed missing; generating…"
    python -m mock_saas.seed.generate --merchants=1 --orders-per-merchant=2000
fi

exec "$@"
