# Product PriceSync Service — Project Brain

## Purpose

This FastAPI service synchronizes queued Product, ProductPrice, and ProductBarcode information from Head Office `Rep*` tables to multiple outlet SQL Server databases ("Depots"). APScheduler triggers periodic full-sync cycles, `SyncManager` throttles concurrency with a semaphore, and failed outlets enter a SQL-persisted retry queue with exponential backoff. After an outlet transaction commits, the service directly acknowledges the exact processed `RepProductPrice` keys at Head Office. A web dashboard provides real-time monitoring, manual controls, and schedule configuration.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                    FastAPI Service                     │
│                                                        │
│  ┌───────────────────┐    ┌──────────────────────┐    │
│  │  APScheduler       │    │  Retry Processor     │    │
│  │  (cron/interval)   │───▶│  (10s polling loop)  │    │
│  └───────────────────┘    └──────────┬───────────┘    │
│                                       │               │
│  ┌────────────────────────────────────▼────────────┐  │
│  │              SyncManager                        │  │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │  │
│  │  │ Semaphore│  │ SyncLock │  │ RetryQueue     │ │  │
│  │  │ (N=10)  │  │ (mutex)  │  │ (in-memory)    │ │  │
│  │  └──────────┘  └──────────┘  └───────────────┘ │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ _check_central_db_health()               │  │  │
│  │  │ (health check before each cycle)         │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  └────────────────────┬────────────────────────────┘  │
│                        │                               │
│  ┌─────────────────────▼──────────────────────────┐   │
│  │           sync_engine.py                        │   │
│  │  (outlet transaction + linked server setup)    │   │
│  │  + post-commit Head Office acknowledgement     │   │
│  └─────────────────────┬──────────────────────────┘   │
│                        │                               │
│  ┌─────────────────────▼──────────────────────────┐   │
│  │           db_logger.py                          │   │
│  │  ├─ ProductSyncLog (latest status, UPSERT)     │   │
│  │  └─ service state/cycle/retry persistence      │   │
│  └────────────────────────────────────────────────┘   │
│                                                        │
│  ┌─────────────┐  ┌──────────┐  ┌─────────────────┐  │
│  │ REST API    │  │ Settings │  │ Dashboard HTML  │  │
│  │ (routers/)  │  │ (state)  │  │ (static/)       │  │
│  │ + price-    │  │          │  │ + Price Change  │  │
│  │   changes   │  │          │  │   History tab   │  │
│  └─────────────┘  └──────────┘  └─────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### Data Flow

1. **APScheduler** triggers `scheduled_sync()` at configured interval/cron
2. **SyncManager** acquires `sync_lock` (one cycle at a time), runs **central DB health check** (`SELECT 1` with 5s timeout), then loads outlet list from central DB
3. Outlets are filtered through `EXCLUDED_OUTLETS` list
4. Active outlets are dispatched through an `asyncio.Semaphore` (default max 10 concurrent)
5. Each outlet runs inside a per-outlet watchdog (`OUTLET_SYNC_TIMEOUT`, default 180s)
6. The full cycle runs inside a cycle-level watchdog (`FULL_SYNC_TIMEOUT`, default 10800s)
7. Outlet SQL reads `RepProduct`, pending non-delete `RepProductPrice`, delete-marked `RepProductPrice`, and `RepProductBarcode` through the configured linked server
8. Product, ProductPrice, ProductPrice deletions, and ProductBarcode changes commit as one outlet transaction
9. SQL returns marker result sets containing the distinct processed `(ProductCode, DepotCode)` acknowledgement keys
10. Only after the outlet commit, Python connects directly with `HO_*` credentials and updates matching pending `RepProductPrice` rows to `SyncStatus='Y'`, `SentTime=GETDATE()`
11. A failed HO acknowledgement produces `Partial`; the HO rows remain pending and can be processed again idempotently
12. Results are stored in `ProductSyncLog`; legacy price-change and sync-history audit writes are disabled by default
13. Failed outlets enter the `RetryQueue` with exponential backoff; the **RetryProcessor** polls every 10s for due retries

---

## File-by-File Reference

### `app/config.py` — Runtime Configuration

Uses `pydantic-settings` BaseSettings loaded from `.env` file. All settings are documented in the class. Key groups:

| Group | Variables | Description |
|-------|-----------|-------------|
| Central DB | `SOURCE_SERVER`, `SOURCE_DATABASE`, `SOURCE_USER`, `SOURCE_PASSWORD` | Head Office SQL Server |
| HO acknowledgement | `HO_SERVER`, `HO_DATABASE`, `HO_DB_USERNAME`, `HO_DB_PASSWORD` | Direct post-commit update of `RepProductPrice.SyncStatus` and `SentTime` |
| Log DB | `LOG_SERVER`, `LOG_DATABASE`, `LOG_USER`, `LOG_PASSWORD` | ProductSyncLog and persistent service state SQL Server |
| Sync SQL | `CENTRAL_DB`, `CENTRAL_LINKED_SERVER_NAME`, `LOCAL_DB` | DB names used in sync queries |
| Outlet Auth | `OUTLET_DB_USER`, `OUTLET_DB_PASSWORD` | Shared credentials for all outlets |
| Timeouts | `CONNECT_TIMEOUT` (10s), `QUERY_TIMEOUT` (120s) | SQL Server timeouts |
| Watchdogs | `OUTLET_SYNC_TIMEOUT` (180s), `FULL_SYNC_TIMEOUT` (10800s) | asyncio per-outlet and per-cycle timeouts |
| Exclusions | `EXCLUDED_OUTLETS` (default empty) | Comma-separated, case-insensitive |
| Scheduling | `SYNC_INTERVAL_MINUTES` (30) | Default interval |
| Retry | `RETRY_MAX_ATTEMPTS` (10), `RETRY_BASE_DELAY` (30s) | Retry policy |
| Concurrency | `MAX_CONCURRENT_SYNCS` (20), `THREAD_POOL_MAX_WORKERS` (40) | Max simultaneous outlet syncs and explicit blocking-ODBC worker capacity |
| Security | `ADMIN_API_KEY`, `VIEWER_API_KEY`, `ADMIN_RATE_LIMIT_PER_MINUTE` | Operator/viewer authorization and administrative request limiting |
| Legacy audit flags | `ENABLE_PRODUCT_PRICE_CHANGE_LOG=false`, `ENABLE_PRODUCT_SYNC_LOG_HISTORY=false` | Dormant audit tables; keep disabled unless deliberately reactivating and validating the old subsystem |
| Retention/audit | `PRICE_CHANGE_RETENTION_DAYS` (90), `PRICE_CHANGE_INSERT_BATCH_SIZE` (500) | Used only when legacy price-change auditing is enabled |

**Important:** The singleton `settings = Settings()` is imported by every module.

**Current database role mapping:** `SOURCE_*` loads the active outlet list. Outlet SQL reads the Head Office `Rep*` queues through `CENTRAL_LINKED_SERVER_NAME`. After the outlet commit, `HO_*` performs the direct acknowledgement update. `LOG_*` remains independent and stores `ProductSyncLog` plus service/cycle/retry state. Do not substitute `SOURCE_*` or `LOG_*` for the `HO_*` acknowledgement connection.

### `app/main.py` — Application Entry Point

- **Lifespan:** Manages startup (APScheduler, retry processor, router injection) and shutdown (scheduler shutdown, SyncManager stop)
- **Singleton instances:** `sync_manager` and `scheduler`
- **Schedule building:** `_build_schedule_job_kwargs()` reads persisted state from `state.py` to restore schedule across restarts. Falls back to `.env` defaults if no dashboard override exists.
- **Visual schedule precision:** `Run Every` supports 5-minute increments through 60 minutes plus 75 and 120 minutes (`5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 75, 120`). Active From/Until persist separate hour and minute fields and support every `:00`/`:30` boundary, including windows that cross midnight. Each active window is anchored at Active From, and the next-run API reports the next eligible execution rather than an out-of-window interval check. Older state files remain compatible because missing minute fields default to `0`.
- **Single-outlet production test:** A distinct dashboard card above Schedule Configuration accepts an outlet code and calls `POST /ProductSync/api/sync/outlet/{outlet_code}` after an accessible dashboard-styled confirmation modal (no native browser confirmation). It reports progress/result state and refreshes Outlet Results. The operator should pause scheduling before using it.
- **Schedule confirmations:** Apply Schedule, Pause Schedule, and Resume Schedule use the dashboard-styled confirmation modal. Apply Schedule summarizes the chosen interval, time window, and active days before submission. Cancelling makes no API request; confirming invokes the corresponding protected endpoint and updates the visible state.
- **Manual full-sync confirmation:** The dashboard's Start Full Sync button opens a custom confirmation warning that all eligible outlet databases will receive Product, ProductPrice, and ProductBarCode writes. Only confirmation calls the protected start endpoint; scheduled cycles are unaffected.
- **Graceful-stop confirmation:** Stop Current Sync opens a custom confirmation explaining that queued outlets are cancelled while active database workers drain safely. Cancelling makes no stop request; confirming calls the protected graceful-stop endpoint.
- **Retry confirmations:** Retry Failed and Clear Retry Queue use action-specific dashboard confirmation modals. Clear no longer uses the native browser `confirm()` dialog, and cancelling either action makes no API request.
- **Dashboard clock format:** Shared `formatTime()` output is forced to a consistent 12-hour `hh:mm:ss AM/PM` display for status cards, results, retry times, price history, and next-run timestamps.
- **Persisted schedule form state:** On dashboard initialization, `schedule.js` reads `GET /ProductSync/api/settings` and restores the saved visual interval, start/end hour and minute, active days, or cron expression. The form therefore reflects the actual backend schedule after a browser refresh instead of reverting visually to its HTML defaults.
- **Schedule enable state:** `is_schedule_enabled()` restores whether the `sync_cycle` job is active or paused. A paused sync schedule stays paused after restart; the independent midnight price-change cleanup job remains active.
- **Scheduler config:** `max_instances=1`, `coalesce=True`
- **Max-instance logging:** `_log_scheduler_event()` captures APScheduler `EVENT_JOB_MAX_INSTANCES` and logs which outlets were active when a cycle was skipped
- **Injectors:** `sync.sync_manager = sync_manager`, etc. — router modules import `None` placeholders populated at startup
- **Endpoints:** `/ProductSync/Dashboard` serves the HTML dashboard, `/ProductSync/static/` serves static assets

### `app/database.py` — SQL Server Connections & Outlet Loading

- `make_connection()` — Factory for `pyodbc.connect()` with TCP-prefixed connection string. Supports optional `timeout` parameter for quick health check connections.
- `_load_outlet_data_sync()` / `load_outlet_data()` — Loads active depots via `Depot INNER JOIN DepotIP WHERE ActiveDepot='Y'`. Uses `pandas.read_sql()`. Async wrapper via `asyncio.to_thread()`. Has a timeout of `CONNECT_TIMEOUT + QUERY_TIMEOUT`.
- `build_outlet_list()` — Converts DataFrame to list of dicts with keys: `Outlet`, `Server`, `Database`, `User`, `Password`

### `app/sync_sql.py` — SQL Templates

- `get_sync_sql()` — Generates the full SQL batch for each outlet:
  1. Pull `RepProduct` data via OPENQUERY into `#HoProduct`
  2. Pull pending non-delete `RepProductPrice` rows for the Python-supplied outlet code into `#HoProductPrice`
  3. Pull pending `SyncType='D'` rows separately into `#HoProductPriceDelete`
  4. Pull `RepProductBarcode` into `#HoBarcode`
  5. INSERT new products and UPDATE the five product fields from the supplied SQL
  6. DELETE local ProductPrice rows identified by delete markers before normal upserts
  7. INSERT missing ProductPrice rows; `Price='Y'` uses the incoming UnitPrice, otherwise a missing row receives the supplied fallback of zero
  8. UPDATE matching ProductPrice rows; `Price='Y'` controls only UnitPrice while the other supplied fields retain the reference-query behavior
  9. INSERT missing ProductBarcode pairs
  10. Return `HO_ACK_SUMMARY` and `HO_ACKNOWLEDGEMENTS` marker result sets containing the union of upsert and delete keys
  11. DROP all temp tables
- `SyncType='D'` is deliberately excluded from `#HoProductPrice`. This is a behavior correction from the reference SQL, which otherwise deletes a row and can reinsert it from the normal upsert temp table.
- The outlet code comes from the Python outlet record rather than `SELECT @vDepotCode FROM Depot`; this keeps queue selection tied to the outlet being processed.
- Identifiers and the nested OPENQUERY outlet literal are escaped before interpolation.
- `LINKED_SERVER_CHECK_SQL` — `SELECT COUNT(*) FROM sys.servers WHERE name = ?`
- `LINKED_SERVER_CREATE_TEMPLATE` — `sp_addlinkedserver` with SQLNCLI provider

### `app/sync_engine.py` — Outlet Sync Execution

- `_run_on_outlet_blocking()` — Connects to one outlet, ensures the linked server exists, runs the sync SQL as a single `cursor.execute()`, validates the acknowledgement summary/details, then commits the outlet transaction.
- Outlet connections use `autocommit=False`. Linked-server administration is committed separately, then Product, ProductPrice, and ProductBarcode changes commit as one unit. Any sync exception calls `rollback()`.
- `run_on_outlet()` — Async wrapper that:
  1. Runs the blocking sync via `asyncio.to_thread()`
  2. After success, calls `_acknowledge_ho_blocking()` in a worker thread with the returned keys
  3. Marks the result `Partial` if direct HO acknowledgement fails; the outlet commit is not rolled back and HO rows remain `N`
  4. Updates `ProductSyncLog`; writes `ProductSyncLogHistory` only when its feature flag is enabled
- Connection cleanup in `finally` blocks prevents leaks
- `conn.timeout = settings.QUERY_TIMEOUT` is set before cursor creation (pyodbc 5.3.0: `Cursor.timeout` does NOT exist)
- `_acknowledge_ho_blocking()` deduplicates keys and executes a parameterized `UPDATE dbo.RepProductPrice SET SyncStatus='Y', SentTime=GETDATE()` restricted by pending status, ProductCode, and DepotCode.

### `app/sync_manager.py` — Central Orchestrator

The `SyncManager` class is the heart of the service:

| Property/Method | Description |
|-----------------|-------------|
| `semaphore` | `asyncio.Semaphore(MAX_CONCURRENT_SYNCS)` — throttles concurrent outlets |
| `sync_lock` | `asyncio.Lock()` — prevents overlapping full cycles |
| `retry_queue` | `RetryQueue` instance |
| `current_cycle_task` | Retained full-cycle `asyncio.Task`; makes duplicate-start rejection atomic and enables operator cancellation |
| `outlet_tasks` | Current cycle task-to-outlet mapping used to cancel outlets that have not started |
| `cancel_requested` | `asyncio.Event` coordinating graceful cycle cancellation |
| `active_outlets` | `dict[str, dict]` — tracks currently executing outlets with start timestamps |
| `outlet_operations` | Canonical outlet-code-to-task registry that prevents a second manual/retry/full-cycle operation for the same outlet |
| `draining_outlets` | Timed-out or cancelled outlet wrappers retained until their underlying ODBC worker actually exits |
| `_check_central_db_health()` | Static async method. Runs `SELECT 1` on central DB with 5s timeout. Returns `True/False`. Called at start of `run_full_sync()` before loading outlets. Uses proper `try/finally` connection cleanup. |
| `run_full_sync(trigger)` | Main cycle: runs health check, loads outlets, filters exclusions, dispatches through semaphore, applies full-cycle watchdog, records results |
| `start_full_sync(trigger)` | Atomically creates and retains a full-cycle task; returns `None` if a cycle already exists |
| `request_cancellation()` | Requests graceful cancellation, immediately cancels queued outlet tasks, and lets active blocking ODBC work drain or time out |
| `sync_single_outlet(code)` | Manual sync for diagnostics — bypasses exclusion check |
| `retry_single_outlet(entry)` | Called by retry processor — increments attempt and requeues on failure |
| `_run_outlet_with_watchdog()` | Wraps any outlet operation with semaphore + per-outlet timeout |
| `_sync_outlet_with_semaphore()` | Used by full cycles — calls watchdog wrapper and updates counters/retry queue |

**Full cycle watchdog:** `asyncio.wait()` with `timeout=FULL_SYNC_TIMEOUT` is used instead of `asyncio.gather()`. When timeout fires, pending tasks are cancelled, timeout results are generated with status `timed_out`, and the cycle lock is released so the next APScheduler tick can proceed.

**Exclusion logic:** `_excluded_outlet_codes()` parses `settings.EXCLUDED_OUTLETS` into a set. Excluded outlets are added to results with status `Excluded` and are skipped.

**CancelledError handling:** `asyncio.CancelledError` inherits from `BaseException`, not `Exception`. The `_sync_outlet_with_semaphore()` handler has `except asyncio.CancelledError: raise` BEFORE `except Exception` to ensure cancellation propagates correctly during shutdown. The `run_full_sync()` outer handler also catches `CancelledError` separately to allow clean shutdown.

**Graceful operator cancellation:** Cycle state transitions from `running` to `stopping` after `/sync/stop`. Outlet tasks waiting for the semaphore are cancelled immediately and recorded with status `Cancelled`; they are not sent to the retry queue. Active `asyncio.to_thread()`/ODBC operations cannot be killed safely by cancelling their coroutine, so they remain visible until the SQL call completes. A timed-out outlet is registered as draining, and another attempt for that outlet is rejected/deferred until the retained task exits. The final cycle status is `cancelled`.

**Cycle accounting:** Attempted, completed, partial-failure, cancelled, timed-out, and failed cycles have separate lifetime counters. Per-cycle counters, including `total_outlets`, reset before database health checks so aborted cycles cannot leak previous values.

### `app/retry_queue.py` — In-Memory Retry Queue

- `RetryEntry` — Stores outlet info, attempt count, last error, and computes `next_retry_at` with exponential backoff: `base * 2^(attempt-1) + random(0,5)`
- `RetryQueue` — Deduplicated by outlet code (one entry per outlet):
  - `add()` — Queues or updates. If `attempt >= RETRY_MAX_ATTEMPTS`, moves to `_permanently_failed` dictionary
  - `get_due()` — Returns entries whose `next_retry_at` has passed
  - `get_all()` — Returns all entries (pending + permanently failed) with `permanently_failed: true` flag
- `start_retry_processor()` — Background asyncio task that polls every 10 seconds for due retries. Removes from queue before dispatching to avoid double-processing.

### `app/db_logger.py` — Central DB Log Writer

Contains the active status/state persistence subsystem plus dormant legacy audit code:

**SyncLog** (latest per-outlet status, UPSERT):
- `_update_sync_log_blocking()` — Creates `SyncLog` table if not exists, then UPSERTs the latest result
- Remarks are normalized: `"Y"` for success, `"Network Connectivity Issue"` for network/timeout errors, `"Query Error"` for SQL errors
- For success: updates `LastSyncStatus`, `LastSuccessfulSync`, `LastAttempt`, `Remarks`
- For failure: updates `LastSyncStatus`, `LastAttempt`, `Remarks` (does NOT update `LastSuccessfulSync`)

**Legacy ProductPriceChangeLog** (disabled by default):
- `ENABLE_PRODUCT_PRICE_CHANGE_LOG=false` prevents schema creation and makes the public logging wrapper a no-op. The active sync path no longer captures `@PriceChanges` and does not call this subsystem.
- `_ensure_price_change_table()` — Creates `ProductPriceChangeLog` with event/run identifiers, change type, before/after values, and **two indexes**: `IX_ProductPriceChangeLog_ProductDepot` on `(ProductCode, DepotCode, ChangeOccurrenceTime DESC)` for lookup queries, and `IX_ProductPriceChangeLog_ChangeOccurrenceTime` on `(ChangeOccurrenceTime DESC)` for cleanup queries. Both use `IF NOT EXISTS` guards. Startup migration removes the obsolete `PriceDeltaPercent` computed column when upgrading an existing database.
- `_insert_price_changes_blocking()` — Retained for possible deliberate reactivation; it is not called by the active sync engine.
- `log_product_price_changes()` — Async wrapper via `asyncio.to_thread()`

**Legacy ProductSyncLogHistory** (disabled by default):
- `ENABLE_PRODUCT_SYNC_LOG_HISTORY=false` prevents schema creation and per-attempt history writes.
- `ProductSyncLog` latest-status UPSERT and service/cycle/retry persistence remain active and are not controlled by this flag.

**ProductPriceChangeLog Cleanup** (scheduled purge, daily at midnight + manual trigger):
- `_cleanup_price_changes_blocking()` — Purges old records in **batches** using `DELETE TOP (?)` in a `WHILE` loop. Accepts `retention_days` and `batch_size` (default 5000). Each batch commits independently (autocommit mode), keeping transaction log growth bounded. The dedicated `IX_ProductPriceChangeLog_ChangeOccurrenceTime` index makes the WHERE clause seekable instead of scanning the clustered index. Returns total rows deleted across all batches.
- `cleanup_price_changes()` — Async wrapper via `asyncio.to_thread()`, accepts both `retention_days` and `batch_size`.
- Scheduled as a daily APScheduler job at midnight with `misfire_grace_time=3600` in `main.py` lifespan.
- Also exposed as `POST /ProductSync/api/cleanup/price-changes` for on-demand manual triggering.

**ProductPriceChangeLog schema:**
```sql
LogID                   BIGINT IDENTITY(1,1) PRIMARY KEY,
EventID                 UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
RunID                   UNIQUEIDENTIFIER NULL,
ChangeType              VARCHAR(10) NOT NULL DEFAULT 'UPDATE',
ProductCode             VARCHAR(20)     NOT NULL,
DepotCode               VARCHAR(10)     NOT NULL,
PreviousUnitPrice       DECIMAL(18,4)   NULL,
PreviousModifiedDate    DATETIME        NULL,
CurrentUnitPrice        DECIMAL(18,4)   NOT NULL,
CurrentModifiedDate     DATETIME        NULL,
ChangeOccurrenceTime    DATETIME        NOT NULL DEFAULT GETDATE(),
OutletCode              VARCHAR(10)     NULL,
ChangedBy               VARCHAR(50)     NULL
```

### `app/state.py` — Persistent State

- Persists schedule configuration to `dbo.ProductSyncServiceState` in the logging database
- Keeps a process-local cache for synchronous scheduler reads; SQL Server is the source of truth
- `schedule_enabled: true|false` independently persists whether future scheduled sync cycles are enabled
- `is_schedule_enabled()` reads the cached pause state; async setters persist changes to SQL
- Default state has `schedule_mode: None` meaning "use .env defaults"
- `get_persisted_schedule()` returns `None` when no dashboard override has been set
- `set_persisted_schedule()` writes the full schedule config to SQL
- A legacy `data/service_state.json` file is read only as a one-time migration source when no SQL configuration exists

### `app/logger.py` — Logging Setup

- Day-wise rotation at midnight with 90-day retention
- File naming: `SyncLog YYYY-MM-DD.log`
- Dual output: file handler + stdout (for Docker logs)
- Suppresses noisy loggers: `apscheduler` → WARNING, `uvicorn.access` → WARNING

### `app/models.py` — Pydantic Models

| Model | Fields | Purpose |
|-------|--------|---------|
| `OutletInfo` | outlet_code, server, database, user, password | Outlet connection details |
| `SyncResult` | outlet_code, ip, status, remarks, timestamp, duration_seconds, ho_ack_status, ho_ack_count | Per-outlet sync outcome; the blocking phase temporarily carries `ho_acknowledgements`, which the async wrapper consumes before returning the API result |
| `SyncCycleStatus` | state, started_at, finished_at, total_outlets, completed, failed, in_progress, trigger | Cycle-level status |
| `RetryEntry` | outlet_code, server, attempt, max_attempts, next_retry_at, last_error, added_at | Retry queue entry |
| `ServiceStatus` | service, uptime_seconds, current_sync, schedule_interval_minutes, next_scheduled_run, retry_queue_size, total_syncs_completed | Full service status |
| `ScheduleUpdateRequest` | mode, interval_minutes, active_hours_start/end, active_minutes_start/end, active_days, cron_expression | Schedule update payload; visual time boundaries support `:00` and `:30` |

### `app/security.py` — API Authorization & Rate Limiting

- `require_operator()` accepts only `ADMIN_API_KEY` for mutating administrative operations
- `require_viewer()` accepts either `VIEWER_API_KEY` or `ADMIN_API_KEY` for logs and price history
- API keys arrive through the `X-API-Key` header and are compared with `secrets.compare_digest()`
- Missing server-side keys disable the corresponding protected surface with HTTP 503; invalid client keys return HTTP 401
- `limit_expensive_operation()` applies an in-process per-client rolling one-minute limit; production reverse proxies must also enforce rate limits and HTTPS

### `app/routers/` — REST API

All endpoints are prefixed with `/ProductSync/api/`.

Authorization policy:
- Public: health plus operational status/outlet/settings/retry-queue reads used for internal monitoring
- Viewer or operator: log listing, archived/live logs, and price-change history
- Operator only: sync start/stop/outlet, schedule changes, forced/cleared retries, and cleanup
- Expensive mutating routes also apply `limit_expensive_operation`

#### `routers/status.py`
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (`healthy` + ISO timestamp) |
| `/status` | GET | Full service status (sync cycle, schedule, retry queue, uptime) |
| `/outlets` | GET | All outlet results from last/current cycle |
| `/outlets/{code}` | GET | Single outlet result (404 if not found) |
| `/logs` | GET | List log files sorted newest-first |
| `/logs/archive/{date}` | GET | Read last 1000 lines of a specific day's log (returns plain text) |
| `/logs/stream` | GET | Authenticated server-sent log stream consumed by the dashboard through Fetch streaming |
| `/logs/price-changes` | GET | **Query ProductPriceChangeLog history** — supports `outlet_code`, `days` (default 7), `limit` (default 100) query params |

#### `routers/sync.py`
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sync/start` | POST | Start full sync cycle (runs in background — returns immediately) |
| `/sync/stop` | POST | Request graceful cancellation of the current full cycle; idempotently returns `idle` when no cycle exists |
| `/sync/outlet/{code}` | POST | Sync single outlet (blocking call, returns result) |

#### `routers/settings.py`
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/settings` | GET | Current settings + schedule info |
| `/settings/schedule` | PUT | Update schedule (visual or cron mode) |
| `/settings/schedule/pause` | POST | Pause future scheduled sync cycles without stopping the API or cleanup job |
| `/settings/schedule/resume` | POST | Resume future scheduled sync cycles and return the next run time |

#### `routers/retry.py`
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/retries` | GET | Retry queue contents |
| `/retries/process-now` | POST | Force-process all due retries |
| `/retries` | DELETE | Clear entire retry queue |

#### `routers/cleanup.py`
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cleanup/price-changes` | POST | **Manual trigger for ProductPriceChangeLog purge (batched)** |

### `app/static/` — Modular Web Dashboard

The dashboard served at `/ProductSync/Dashboard` is split into a semantic HTML shell, external stylesheet, ES-module orchestrator, API client, utilities, and focused components:
- `dashboard.html` contains markup only and loads `css/dashboard.css` plus `js/app.js`
- `js/api.js` centralizes API calls, URL encoding, tab-scoped API-key headers, and authenticated Fetch log streaming
- `js/components/` contains status, outlet, retry, schedule, log, price-change, and cleanup behavior
- All event handling uses `addEventListener()`; database/error values render through `textContent` and DOM node creation rather than dynamic `innerHTML`
- The page contains no embedded stylesheet, inline JavaScript, inline event handler, or native `EventSource`
- Auto-refreshes every 5 seconds
- Sync cycle progress bar with outlet counts
- Color-coded outlet results table (Success=green, Failed=red, Excluded=orange)
- Retry queue panel with attempt countdown and force-process button
- Schedule editor with Visual and Advanced (Cron) tabs
- Operator controls for **Pause Schedule**, **Resume Schedule**, and **Stop Current Sync**
- Displays cycle state as `RUNNING`, `STOPPING`, or `IDLE`; the Stop button is disabled once draining begins
- **Price Change History** section with outlet code filter, days-back dropdown, and audit columns including change type, run ID, product/depot, before/after price, actor, timestamps, and outlet
- Log viewer with date picker
- Manual sync trigger buttons
- API-key connection control. Keys are stored only in the current tab's `sessionStorage`, never embedded in static assets

---

## Key Concepts & Patterns

### Concurrency Model
```
SyncManager.sync_lock (asyncio.Lock)
    │
    ├─▶ _check_central_db_health() — quick SELECT 1 before cycle
    │
    ├─▶ Full cycle dispatch
    │       │
    │       ├─▶ Outlet 1 ──▶ SyncManager.semaphore ──▶ asyncio.wait_for(180s)
    │       ├─▶ Outlet 2 ──▶ SyncManager.semaphore ──▶ asyncio.wait_for(180s)
    │       └─▶ ...         (max 10 concurrent)
    │
    ├─▶ Single outlet sync (bypasses lock, uses semaphore + watchdog)
    │
    └─▶ Retry dispatch (bypasses lock, uses semaphore + watchdog)
```

### Timeout Hierarchy (from innermost to outermost)

1. **`CONNECT_TIMEOUT`** (10s) — `pyodbc.connect()` login timeout
2. **`QUERY_TIMEOUT`** (120s) — `pyodbc.Connection.timeout` set before cursor creation → ODBC statement timeout
3. **`OUTLET_SYNC_TIMEOUT`** (180s) — `asyncio.wait_for()` per-outlet watchdog
4. **`FULL_SYNC_TIMEOUT`** (10800s) — `asyncio.wait()` full-cycle watchdog

**Rule:** Keep `OUTLET_SYNC_TIMEOUT > QUERY_TIMEOUT` so ODBC aborts the SQL statement before the asyncio watchdog fires. This ensures clean query cancellation rather than forced task cancellation.

### Active ProductPrice Queue and Acknowledgement Flow

```
OUTLET sync SQL executes in one transaction
  │
  ├─▶ Read pending non-delete RepProductPrice rows for the outlet
  │
  ├─▶ Read SyncType='D' rows into a separate delete temp table
  │
  ├─▶ DELETE markers, then INSERT/UPDATE normal ProductPrice rows
  │
  ├─▶ Return distinct upsert + delete (ProductCode, DepotCode) keys
  │     as HO_ACK_SUMMARY and HO_ACKNOWLEDGEMENTS marker result sets
  │
  ├─▶ COMMIT outlet transaction
  │
  └─▶ Direct HO connection updates only matching pending keys:
        SyncStatus='Y', SentTime=GETDATE()
        Failure here => Partial; HO rows remain pending for a later cycle
```

### pyodbc 5.3.0 Details
- `pyodbc.Connection.timeout` — **DOES EXIST**. Set before creating cursors. Controls ODBC query/statement timeout.
- `pyodbc.Cursor.timeout` — **DOES NOT EXIST** in pyodbc 5.3.0. Cursors inherit timeout from the connection at creation time.
- The `timeout` parameter in `pyodbc.connect()` controls **login timeout only**.
- Always close connections in `finally` blocks to prevent leaks.

### Retry Queue Behavior
- Exponential backoff: `RETRY_BASE_DELAY * 2^(attempt-1) + random(0, 5)`
- Max attempts: `RETRY_MAX_ATTEMPTS` (default 10)
- Permanently failed when: `attempt >= RETRY_MAX_ATTEMPTS` (off-by-one correction: previously was `>`)
- Deduplication: Only one entry per outlet code at a time
- Persisted in `dbo.ProductSyncRetryQueue` and restored at service startup. Database passwords are reconstructed from settings and are never stored in this table.
- Background processor polls every 10 seconds via `start_retry_processor()`

### Schedule Modes

**Visual mode:** User picks interval (`5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 75, 120` minutes), Active From/Until in 30-minute steps, and active days (mon-sun). `VisualScheduleTrigger` anchors the interval to Active From for every eligible window and supports overnight ranges. `scheduled_sync()` retains a defensive persisted day/time guard.

**Day-wise mode:** User assigns an independent interval and active window to each enabled weekday. `DayWiseScheduleTrigger` returns the earliest candidate across those rules while the service retains one APScheduler job and one-cycle-at-a-time protection. Rules persist as JSON in `ProductSyncServiceState.ScheduleRulesJson`; overnight windows belong to their starting weekday and may end the following day.

**Cron mode:** Raw 5-field cron expression: `minute hour day month day_of_week`.

**Persistence:** Schedule survives container restarts via `dbo.ProductSyncServiceState`. Completed-cycle dashboard statistics and retry entries are stored in `dbo.ProductSyncCycle` and `dbo.ProductSyncRetryQueue`.

**Pause/resume:** `schedule_enabled` is separate from the interval/cron definition. Pausing calls `scheduler.pause_job("sync_cycle")`, prevents future scheduled cycles, and persists across restart. It does not cancel a cycle already in progress. Resuming calls `scheduler.resume_job("sync_cycle")` and recalculates `next_run_time`.

**Stopping an active cycle:** This is a separate operator action from pausing the schedule. `/sync/stop` prevents queued outlets from starting and moves the cycle to `stopping`; active database calls drain or time out. To ensure no new scheduled run occurs after cancellation, pause the schedule first or use both dashboard controls.

---

## Legacy Context

### Historical Script

The root file `CustomerInfoUpdate v9 1-14-26.py` was the original monolithic Python script that the first version of this service replaced. It performed the same sync logic but without:
- A web API or dashboard
- Concurrent outlet processing (sequential only)
- Timeout protections (could hang indefinitely)
- Retry queue with backoff
- Schedule configurability (no APScheduler)
- Structured logging

The script is kept in the repository for reference but is gitignored and not deployed. All functionality has been superseded by the FastAPI service.

### Production Incident (13 July 2026)

Outlet `F786` (`172.22.186.41`) repeatedly hung during ODBC SQL execution. The old implementation waited indefinitely for every task, so the running cycle retained the sync lock. APScheduler skipped later executions with `maximum number of running instances reached (1)`.

### Changes Made in Response

1. **`QUERY_TIMEOUT` applied as statement timeout** — Previously protected only login. Now set on `Connection.timeout` before cursor creation in all three pyodbc usage sites (outlet sync, central outlet loading, central log writes).

2. **Three-level watchdog system** — Per-outlet `asyncio.wait_for()` at `OUTLET_SYNC_TIMEOUT` (180s), full-cycle `asyncio.wait()` at `FULL_SYNC_TIMEOUT` (10800s), plus the existing `QUERY_TIMEOUT`.

3. **F786 exclusion** — Added to `EXCLUDED_OUTLETS`. Full cycles skip it. Manual single-outlet sync remains available for diagnostics. Remove only after verifying network/SQL stability.

4. **Visibility improvements** — `active_outlets` exposed in status API. Max-instance events and lock conflicts log active outlet codes.

5. **Retry off-by-one fix** — Permanently failed when `attempt >= RETRY_MAX_ATTEMPTS` rather than `>`. Defensive guard prevents attempt `4/3`.

6. **Connection cleanup** — All pyodbc connections closed in `finally` blocks.

---

## Core Queue Workflow Change (22 July 2026)

The active synchronization contract changed from date-based reads of live Product tables plus price-change auditing to queue-based replication and post-commit acknowledgement:

- Outlet reads now use `RepProduct`, `RepProductPrice`, and `RepProductBarcode` through the linked server.
- Pending ProductPrice rows are selected by `SyncStatus='N'` and the Python-supplied outlet code.
- `SyncType='D'` rows are isolated in `#HoProductPriceDelete`, deleted locally, and excluded from normal price upserts. This intentionally corrects the supplied reference query's delete-then-reinsert hazard.
- The supplied `Price` flag controls only UnitPrice: `Y` applies the incoming value; otherwise an existing price is preserved, while a missing row keeps the reference fallback of zero.
- SQL returns distinct upsert and delete keys through `HO_ACK_SUMMARY` and `HO_ACKNOWLEDGEMENTS` result-set markers.
- After the outlet transaction commits, Python connects directly using `HO_*` and updates only `RepProductPrice.SyncStatus` and `SentTime` for the exact pending keys.
- HO acknowledgement failure is a `Partial` result. It never rolls back an already committed outlet transaction, and it deliberately leaves HO rows pending for a later idempotent cycle.
- `ProductPriceChangeLog` and `ProductSyncLogHistory` are disabled by default through explicit feature flags. The latest `ProductSyncLog` and persistent service state remain active.
- The depot-selection source changed from a local `Depot WHERE ActiveDepot='Y'` lookup in the reference SQL to the outlet code already selected by the Python orchestrator.
- Linked-server discovery/creation occurs in Python before the outlet data transaction instead of through the reference query's `#LinkedServers` temp table.

These are core behavioral rules. Future changes must preserve commit-before-ack ordering, parameterized direct HO updates, and delete/upsert separation unless a database-owner-approved migration explicitly replaces them.

## Subsequent Enhancements (17 July 2026)

### Conditional ProductPrice UPDATE with Change Tracking

**Historical/dormant:** this implementation was superseded on 22 July 2026 by the `RepProductPrice` queue workflow. Its tables and writers remain in the codebase only behind disabled feature flags.

The former ProductPrice UPDATE was changed from a blind overwrite to a conditional update:
- Added `#ChangedPrices` temp table that identifies only rows where `UnitPrice` or `ModifiedDate` actually changed (with NULL-safe comparisons)
- The UPDATE now uses `INNER JOIN #ChangedPrices` to write only to changed rows
- Added `OUTPUT INTO @PriceChanges` to capture before/after values (old vs new UnitPrice and ModifiedDate)
- Added marker `SELECT 'PRICE_CHANGES' AS Marker, ...` result set read by Python
- This reduces transaction log I/O by ~99% (typical cycles only change 0.1-1% of product prices)
- The `ProductPriceChangeLog` audit table captures each change as a permanent record

### Central DB Health Check

Added `_check_central_db_health()` to `SyncManager`:
- Runs `SELECT 1` with 5s timeout before each full cycle
- Aborts the cycle immediately with `status: "db_unreachable"` if central DB is down
- Prevents the 130s timeout waste when the central database is unreachable
- Proper `try/finally` connection cleanup

### CancelledError Handling

Fixed `asyncio.CancelledError` propagation:
- `asyncio.CancelledError` inherits from `BaseException`, not `Exception`
- Added explicit `except asyncio.CancelledError: raise` before `except Exception` in `_sync_outlet_with_semaphore()`
- Added `except asyncio.CancelledError` in `run_full_sync()` outer handler to allow clean service shutdown
- This prevents a `CancelledError` from being silently swallowed by the generic `except Exception` handler

### Parallelized Central DB Logging

**Historical/dormant:** the price-change logging call described here is no longer part of the active sync path.

Previously, `run_on_outlet()` ran both logging operations concurrently:
- Before: sequential `await update_product_sync_log()` then `await log_product_price_changes()`
- After: `await asyncio.gather(update_product_sync_log(), log_product_price_changes())`
- Reduces per-outlet overhead by ~100-500ms

### Price Change Log Cleanup (Scheduled Purge)

Added automated cleanup of historical price change records:
- New setting `PRICE_CHANGE_RETENTION_DAYS` (default 90) in `config.py`
- `_cleanup_price_changes_blocking()` in `db_logger.py` — parameterized DELETE, checks table existence first
- Daily APScheduler job (`cron`, hour=0, minute=0) wired in `main.py` lifespan
- First run is at the next midnight after service start (APScheduler adds jobs after `start()`)
- Prevents unbounded growth of the append-only `ProductPriceChangeLog` table

### Price Change History API & Dashboard

Added `GET /ProductSync/api/logs/price-changes` endpoint:
- Queries `ProductPriceChangeLog` with `outlet_code`, `days`, `limit` filters
- Uses parameterized SQL (no injection risk)
- Follows same `asyncio.to_thread()` + `make_connection()` pattern
- Returns change type, run ID, old/new prices, ModifiedDate, actor, and occurrence time

Added Price Change History section to the web dashboard:
- Outlet code text input filter
- Days-back dropdown (24h, 3d, 7d, 14d, 30d)
- Audit table showing change type, run ID, before/after values, actor, timestamps, and outlet
- Auto-refreshes every 5 seconds
- Manual refresh button

### Persistent Schedule Pause & Graceful Cycle Stop

Added independent operational controls for future schedules and current work:
- `POST /ProductSync/api/settings/schedule/pause` pauses only the `sync_cycle` APScheduler job and persists `schedule_enabled=false` in `dbo.ProductSyncServiceState`.
- `POST /ProductSync/api/settings/schedule/resume` resumes the job, persists `schedule_enabled=true`, and returns the next run time.
- Startup restores the persisted pause state after the scheduler starts. The daily `price_change_cleanup` job is not paused.
- `POST /ProductSync/api/sync/stop` requests graceful cancellation of the current full cycle.
- `SyncManager.start_full_sync()` retains the cycle task and rejects a second start without the earlier `is_running` race window.
- `SyncManager.request_cancellation()` cancels outlet tasks that have not entered execution, while active blocking ODBC operations drain or reach their configured timeout.
- Cycle status now includes `stopping` state and a `cancelled` outlet count. Intentionally cancelled outlets are not queued for retry.
- Dashboard controls: **Pause Schedule**, **Resume Schedule**, and **Stop Current Sync**.
- Regression coverage is in `tests/test_service_controls.py` and `tests/js/api.test.js`.

### Security, Frontend, Reliability, and Deployment Hardening (17 July 2026)

The service received a coordinated production-hardening update:

- **API authorization:** `app/security.py` implements constant-time `X-API-Key` checks. Health remains public; log/history reads accept viewer or administrator keys; synchronization, retry processing, schedule mutation, and cleanup require the administrator/operator key.
- **Administrative rate limiting:** Expensive mutating operations use an in-process per-client limiter. The reverse proxy remains responsible for the outer production rate limit.
- **Browser authentication:** The dashboard accepts an operator/viewer key and stores it only in tab-scoped `sessionStorage`. `api.js` attaches it to protected Fetch requests.
- **Authenticated live logs:** Native `EventSource` was replaced with a Fetch/ReadableStream SSE parser because `EventSource` cannot send the required custom API-key header.
- **Modular frontend:** Embedded CSS and the legacy inline script were removed. `dashboard.html` now loads `static/css/dashboard.css` and `static/js/app.js`; component modules own each dashboard section.
- **DOM/XSS safety:** Inline `onclick`/`oninput`, dynamic `innerHTML`, and JavaScript-string interpolation were removed from runtime modules. Untrusted outlet codes, database values, errors, and log content render as text nodes.
- **Unambiguous log archive route:** The archive endpoint and client now use `/logs/archive/{date}`, eliminating collisions with `/logs/stream` and `/logs/price-changes`.
- **Transactional outlet writes:** Product, ProductPrice, and ProductBarcode changes use one explicit transaction with rollback on failure. Linked-server creation/check work is committed before the data transaction.
- **Cleanup safeguards:** Manual cleanup validates retention, batch size, and maximum batches, returns resolved defaults, raises database failures, and stops at a configured safety limit.
- **Long visual intervals:** Visual schedules use a window-aware trigger anchored to Active From, so 75- and 120-minute intervals remain valid across hour boundaries without drifting after an apply or restart.
- **Draining-operation registry:** `SyncManager` retains timed-out outlet tasks and rejects/defer retries until the original worker exits, preventing duplicate ODBC work and tracking-map overwrites.
- **Cycle outcomes:** Lifetime counters distinguish attempted, completed, partial failure, timeout, cancellation, and other failure outcomes.
- **Pinned dependency sets:** Production packages are exact-pinned in `requirements.txt`; test-only Python packages are in `requirements-dev.txt`; direct JavaScript dev dependencies are exact-pinned and locked by `package-lock.json`.
- **Production Compose:** The source bind mount was removed from `docker-compose.yml`, a health check was added, and live-reload mounting moved to `docker-compose.dev.yml`.
- **Documentation:** README and API examples document the API-key header, HTTPS/reverse-proxy expectations, production/development Compose commands, and the corrected archive route.
- **Regression validation:** CPython 3.11.15 clean-environment verification passed with 101 Python tests. Jest passed 43 JavaScript tests, including dashboard boot and hostile-value rendering. `pip-audit` and `npm audit` reported no known vulnerabilities.

---

## Dependencies

Production dependencies are exact-pinned in `requirements.txt`:

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | 0.139.0 | Web framework |
| `uvicorn[standard]` | 0.51.0 | ASGI server (single worker required for in-memory state) |
| `pyodbc` | 5.3.0 | SQL Server connectivity |
| `pandas` | 3.0.3 | SQL query results → DataFrame |
| `python-dotenv` | 1.2.2 | `.env` file loading |
| `pydantic-settings` | 2.14.2 | Settings model with env file support |
| `APScheduler` | 3.11.1 | Job scheduling (cron/interval) |
| `openpyxl` | 3.1.5 | Excel support |

`requirements-dev.txt` includes the production set plus exact pins for `pytest`, `pytest-asyncio`, and `httpx2` (required by the current FastAPI/Starlette TestClient). JavaScript testing uses exact direct pins in `package.json` and a committed lockfile.

---

## Docker & Deployment

### Dockerfile
- Base: `python:3.11-slim-bookworm`
- Installs Microsoft ODBC Driver 17 for SQL Server
- SSL config fix for older SQL Server compatibility (legacy signature algorithms)
- Single uvicorn worker (`--workers 1`) — required because retry queue and sync state are in-memory

### docker-compose.yml
- Single service: `product-sync`
- Configurable host port: `${HOST_PORT:-8000}:8000` (container port stays 8000)
- Timezone: `Asia/Dhaka`
- Volumes: `./logs:/app/logs`, `./data:/app/data` (for persisted state)
- No production source bind mount; the application code built into the image remains authoritative
- Container health check calls `/ProductSync/api/health`
- Uses Docker's built-in `bridge` network through `network_mode: bridge`; this single-service stack allocates no additional subnet and works even when custom address pools are exhausted

### docker-compose.dev.yml
- Adds the project source bind mount and uvicorn `--reload`
- Must be combined with the production base file for local development only

### Startup Sequence
1. Start service → lifecycle handler executes
2. Ensure the service-state SQL schema exists, then restore schedule configuration, dashboard totals, the last cycle, and retry entries from the logging database
3. If SQL has no configured schedule, migrate legacy `data/service_state.json` once or use `.env` defaults
4. Log all timeout and configuration values clearly
5. Start APScheduler with the restored schedule; immediately pause `sync_cycle` when persisted state is disabled
6. Log the next scheduled run time or the persisted paused state
7. Start retry processor background task
8. Service is ready for API requests and scheduled cycles

### Bare-Metal Deployment Checklist (without Docker)

1. Back up the production application directory and `.env`
2. Copy `.env.example` to `.env` and set production credentials
3. Create virtual environment: `python -m venv venv && source venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt` for runtime or `pip install -r requirements-dev.txt` for tests
5. Create `logs/` and `data/` directories
6. Start with: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`
7. Confirm startup logs show all timeout/watchdog values and exclusion list
8. Trigger a test cycle via API or wait for the first scheduled run
9. Verify the outlet changes, `ProductSyncLog` result, and matching HO `RepProductPrice.SyncStatus='Y'`/`SentTime`; legacy audit tables are not expected when their flags are false
10. Monitor logs for any outlets entering the retry queue

---

## API Quick Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/ProductSync/Dashboard` | Web monitoring dashboard |
| GET | `/ProductSync/api/health` | Health check |
| GET | `/ProductSync/api/status` | Full service status |
| GET | `/ProductSync/api/outlets` | All outlet results |
| GET | `/ProductSync/api/outlets/{code}` | Single outlet result |
| POST | `/ProductSync/api/sync/start` | Start full sync (background) |
| POST | `/ProductSync/api/sync/stop` | Gracefully stop current full sync |
| POST | `/ProductSync/api/sync/outlet/{code}` | Sync single outlet |
| GET | `/ProductSync/api/retries` | Retry queue |
| POST | `/ProductSync/api/retries/process-now` | Force retries |
| DELETE | `/ProductSync/api/retries` | Clear retry queue |
| GET | `/ProductSync/api/settings` | Settings & schedule |
| PUT | `/ProductSync/api/settings/schedule` | Update schedule |
| POST | `/ProductSync/api/settings/schedule/pause` | Pause future scheduled sync cycles |
| POST | `/ProductSync/api/settings/schedule/resume` | Resume future scheduled sync cycles |
| GET | `/ProductSync/api/logs` | List log files |
| GET | `/ProductSync/api/logs/archive/{date}` | Archived log content |
| GET | `/ProductSync/api/logs/stream` | Authenticated live log stream |
| GET | `/ProductSync/api/logs/price-changes` | Price change audit history |
| POST | `/ProductSync/api/cleanup/price-changes` | **Manual price change cleanup (batched)** |

---

## Operational Rules

1. **Timeout relationship:** Keep `OUTLET_SYNC_TIMEOUT` > `QUERY_TIMEOUT` so ODBC normally aborts the active statement before the asyncio watchdog fires.
2. **Cycle watchdog:** Keep `FULL_SYNC_TIMEOUT` comfortably above the normal full-cycle duration. It is a last-resort release, not normal cycle timing.
3. **Exclusion discipline:** Do not remove an outlet from `EXCLUDED_OUTLETS` merely because a restart temporarily succeeds.
4. **Diagnostic protocol:** Before re-enabling a previously excluded outlet, inspect SQL Server blocking sessions, linked-server waits, and network stability.
5. **Active outlet monitoring:** Review `active_outlets` in status API and service logs whenever a schedule is skipped or a cycle runs unusually long.
6. **Retry persistence:** Retry entries are cached in memory during operation and persisted in the log database for restoration after restart.
7. **Single worker constraint:** Single uvicorn worker is required — in-memory state (retry queue, sync manager) would not synchronize across workers.
8. **Credential security:** `.env` contains production credentials — never commit it. Use `.env.example` as a template.
9. **Path prefix:** All endpoints are under `/ProductSync/` path prefix for reverse proxy compatibility.
10. **Legacy audit state:** `ProductPriceChangeLog` and `ProductSyncLogHistory` are disabled by default. Do not assume they are created or written unless their explicit feature flags are enabled and the old capture path has been deliberately restored.
11. **Acknowledgement ordering:** Never acknowledge HO before the outlet transaction commits. HO acknowledgement failure must leave the queue rows pending and return `Partial`.
12. **Delete separation:** Keep `SyncType='D'` rows out of the normal upsert temp table so deletion markers cannot recreate the rows they just deleted.
13. **Pause versus stop:** Pausing the schedule prevents future scheduled starts but does not cancel a running cycle. Stopping a cycle does not automatically pause its future schedule. Use both controls when maintenance requires no further sync work.
14. **Graceful-stop semantics:** `asyncio.to_thread()` cannot terminate an active ODBC call. During `stopping`, wait for `active_outlets` to drain; queued outlets are cancelled immediately, but active statements finish or time out.
15. **Cancellation and retries:** Operator-cancelled outlets must remain `Cancelled` and must not enter the automatic retry queue.
16. **Draining outlet exclusivity:** Never remove or bypass the `outlet_operations` guard. A timed-out ODBC worker must finish before that outlet can be retried.
17. **API authorization:** Mutating endpoints require the administrator key; log/history endpoints require viewer or administrator access. Never put either key in source code or committed configuration.
18. **Browser key handling:** The dashboard stores its supplied key only in tab-scoped `sessionStorage`. Static assets must never contain a configured key.
19. **Network boundary:** Keep port 8000 private and terminate HTTPS/authentication controls at a reverse proxy for production access.
20. **Production image integrity:** Do not add `.:/app` back to production Compose. Use `docker-compose.dev.yml` when source mounting is required for development.

---

## Extension Points for New Developers

### Adding a new sync table

1. **Define SQL templates** in `sync_sql.py`:
   - Add a new function like `get_extension_sync_sql()` following the pattern of `get_sync_sql()`
   - Use OPENQUERY to pull from central, INSERT/UPDATE on the outlet
   - If the source is queue-backed, keep delete markers separate and return explicit post-commit acknowledgement keys
2. **Execute in the outlet flow** in `sync_engine.py:_run_on_outlet_blocking()`:
   - Execute as part of the existing batch and read additional marker result sets
   - Or add a new unambiguous summary/detail marker pair
3. **Handle results**: The result dict returned by `_run_on_outlet_blocking()` can carry additional fields
4. **Post-commit effects**: Perform acknowledgements only after the outlet commit, with parameterized SQL and explicit partial-failure behavior

### Adding new API endpoints

1. Create a new file in `app/routers/` (or add to an existing router)
2. Define: `router = APIRouter(prefix="/ProductSync/api/...", tags=[...])`
3. Inject the dependency in `main.py` lifespan (e.g., `my_router.sync_manager = sync_manager`)
4. Include the router: `app.include_router(my_router.router)`
5. Apply `require_operator`, `require_viewer`, and/or `limit_expensive_operation` dependencies according to the endpoint's sensitivity
6. Document in `docs/API_DOCUMENTATION.md`

### Adding server-side settings

1. Add the field to `config.py:Settings` with a sensible default
2. Add it to `.env.example`
3. Expose it in `routers/settings.py:get_settings()`
4. Log it at startup in `main.py` lifespan
5. Document in `docs/API_DOCUMENTATION.md`

### Extending persistent operational state

Operational state is stored in the logging database. When adding a persistent field:
1. Update the idempotent schema creation in `app/db_logger.py`
2. Update `docs/PRODUCT_SYNC_STATE_SCHEMA.sql` for DBA-managed deployments
3. Add the corresponding load/save mapping and startup restoration
4. Add restart-oriented regression coverage

### Adding a new schedule mode (e.g., daily digest)

1. Extend `ScheduleUpdateRequest` in `models.py` with new fields
2. Add schedule-building logic in `_build_schedule_job_kwargs()` in `main.py`
3. Handle the new mode in `routers/settings.py` schedule update
4. Update the dashboard HTML shell and the relevant `app/static/js/components/` module to provide the UI picker and event handling

### Adding webhook notifications on failure

1. Add a post-sync hook in `sync_manager.py` after each outlet result
2. Or hook into `retry_queue.py` when an outlet is permanently failed
3. Fire an HTTP POST to a configured webhook URL (add to `config.py`)
4. Poll-async using `httpx` or `aiohttp`

---

## Git Conventions

- `.gitignore` excludes `__pycache__/`, `.env`, `*.log`, `logs/`, `data/`, OS files
- Production `.env` is never committed
- `data/service_state.json` is gitignored via `data/` and is supported only as a legacy one-time schedule migration source
- Logs directory is gitignored
- The legacy script `CustomerInfoUpdate v9 1-14-26.py` is gitignored per `.gitignore`
