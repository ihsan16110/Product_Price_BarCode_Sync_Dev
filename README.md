# ProductPriceSync Service

Synchronizes queued Product, ProductPrice, and BarCode data from the Head Office SQL Server to outlet databases, with scheduling, retries, post-commit Head Office acknowledgements, and an operational dashboard.

## Production setup

1. Copy `.env.example` to `.env` and replace every placeholder credential and API key.
2. Keep port 8000 private. Publish the service through an HTTPS reverse proxy.
3. Start the immutable production image:

```bash
docker compose up -d --build
```

Production Compose persists only `logs/` and `data/`; it does not mount the source tree over the image. For local development with live reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The container health check calls `/ProductSync/api/health`.

## Direct development

Python 3.11 is the deployment target.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
npm ci
npm test -- --runInBand
```

`requirements.txt` contains exact production pins. Test-only packages live in `requirements-dev.txt`.

## Concurrency capacity

The production target is 20 concurrent outlets with 40 explicitly configured
ODBC worker threads. Outlet changes commit before the service marks the exact
`RepProductPrice` keys as sent at Head Office. Follow the staged monitoring and rollback
gates in [the concurrency-20 rollout plan](docs/CONCURRENCY_20_ROLLOUT_PLAN.md)
before enabling 20 in production.

Dashboard cycle cards, schedule configuration, and retry entries persist in the
dedicated log database. See [database state persistence](docs/DB_STATE_PERSISTENCE.md)
and the [DBA-managed schema script](docs/PRODUCT_SYNC_STATE_SCHEMA.sql).

## Dashboard and authentication

Open `http://<server>:8000/ProductSync/Dashboard`. Enter `ADMIN_API_KEY` in the dashboard to use administrative controls and protected logs. The key is kept only in the current browser tab's session storage; it is not embedded in the application. Legacy price-change history is available only when `ENABLE_PRODUCT_PRICE_CHANGE_LOG=true`.

For direct API requests, send:

```http
X-API-Key: <ADMIN_API_KEY or VIEWER_API_KEY>
```

The viewer key can read logs and price history. Mutating operations require the administrator/operator key. The health endpoint remains public.

See [API documentation](docs/API_DOCUMENTATION.md) for endpoints and examples.
