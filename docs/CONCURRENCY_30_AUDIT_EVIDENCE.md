# Concurrency 30 audit evidence

Audit date: 19 July 2026  
Repository revision: `ec623d6`

## Decision question

Can the service safely raise `MAX_CONCURRENT_SYNCS` from 10 to 30 for approximately 1,000 outlets without violating current application dependencies, and what evidence is required before production rollout?

## User-provided operating baseline

- Approximately 1,000 outlets.
- Current concurrency is 10 outlets.
- A Product, ProductPrice, and ProductBarcode full synchronization takes approximately 2 hours.

This baseline was supplied by the operator and was not independently reconstructed from production logs or SQL Server telemetry.

## Code and configuration reviewed

- `app/config.py`
- `app/database.py`
- `app/sync_manager.py`
- `app/sync_engine.py`
- `app/sync_sql.py`
- `app/db_logger.py`
- `app/retry_queue.py`
- `app/main.py`
- `.env` (only concurrency and timeout values)
- `.env.example`
- `tests/test_sync_manager.py`
- `tests/test_service_controls.py`
- `tests/test_sync_engine_audit.py`
- `tests/test_db_logger.py`
- `docs/PRODUCT_PRICE_AUDIT.md`

## Verified implementation facts

1. `MAX_CONCURRENT_SYNCS` is validated from 1 to 100 and initializes one process-wide `asyncio.Semaphore`; 30 is configuration-valid.
2. A full cycle creates one task per outlet and the semaphore limits entry into the outlet operation.
3. A per-outlet lock and active-operation registry prevent two application operations for the same outlet from running simultaneously.
4. Only one full cycle can run at a time. APScheduler also uses `max_instances=1` and `coalesce=True`.
5. Manual outlet syncs and retry tasks use the same semaphore, so they compete with a full cycle for capacity.
6. Each outlet uses a separate ODBC connection to its own outlet database, but each outlet's linked-server SQL reads the same Head Office source.
7. Every outlet downloads all Product rows and all ProductBarcode rows. ProductPrice is restricted to the outlet and a recent date condition.
8. Product, ProductPrice, and ProductBarcode changes commit in one outlet-local transaction.
9. After the outlet commit, audit and status records are written through separate connections to one shared log database.
10. Blocking ODBC work uses `asyncio.to_thread()` and the application does not explicitly configure the default thread executor.
11. A timed-out coroutine releases the semaphore while its shielded ODBC thread may continue draining. Actual database work can therefore temporarily exceed the configured semaphore limit.
12. Configuration contains a 120-second query timeout, 180-second outlet watchdog, and 3,600-second full-cycle watchdog. The reported 2-hour full cycle is inconsistent with the repository's default 1-hour full-cycle watchdog unless the deployed value differs, the observation includes retries/multiple cycles, or the running build differs.
13. Price audit inserts are row-by-row and each inserted event is followed by a verification query. Thirty outlets finishing together can create a burst on the shared log database.
14. SQL creation and schema checks are repeatedly executed during normal logging. They are safe in intent but add avoidable shared-database work under higher concurrency.
15. The repository does not contain production evidence for Head Office CPU, disk latency, linked-server session limits, network throughput, log-database write latency, deadlocks, query timeout rate, p95 outlet duration, or outlet table/index health.

## Capacity arithmetic

Using the operator's 120-minute baseline and assuming near-full utilization:

- At 10 workers, 1,000 outlets require about 100 waves, implying about 72 seconds per wave on average.
- 15 workers require about 67 waves: 80.4 minutes in an ideal linear model.
- 20 workers require 50 waves: 60 minutes in an ideal linear model.
- 25 workers require 40 waves: 48 minutes in an ideal linear model.
- 30 workers require about 34 waves: 40.8 minutes in an ideal linear model.

These are mathematical lower-bound planning estimates, not measured forecasts. Shared Head Office and log-database contention can make actual duration materially longer and can increase timeouts or failures.

## Chart map

- Section: idealized duration scenarios.
- Analytical question: how does cycle duration change under perfect linear scaling from the stated 10-worker baseline?
- Family/type: ordered category comparison, vertical bar.
- Fields: concurrency on the category axis; ideal duration in minutes on the quantitative axis; wave count and interpretation retained in the dataset for context.
- Supported claim: 30 workers have a mathematical lower bound of approximately 40.8 minutes from the stated baseline.
- Palette policy: single-root preferred, no redundant category legend.
- Caveat: modeled planning bounds only; no production performance observations beyond the 10-worker baseline.

## Verification limitation

Static test coverage was inspected, but the Python regression suite could not be executed in this workspace because no functional Python runtime or running Docker daemon was available. No production database or monitoring connection was available, so this is a static architecture and capacity-risk audit rather than a production load certification.
