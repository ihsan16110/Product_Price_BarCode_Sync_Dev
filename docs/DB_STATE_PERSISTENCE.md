# Database-backed dashboard and schedule state

The dedicated log database is the durable source of truth for service scheduling, completed full-cycle summaries, and the actionable retry queue.

## Tables

- `ProductSyncServiceState`: one singleton row (`StateID=1`) containing pause/resume state, visual/day-wise/cron mode, interval, active time window, active days, cron expression, and JSON day-wise rules.
- `ProductSyncCycle`: one immutable/upserted row per full-cycle RunID containing the dashboard counts, timestamps, outcome, duration, and retry-queue size at completion.
- `ProductSyncRetryQueue`: the current pending and permanently failed outlet entries. Database credentials are never stored; they are reconstructed from service configuration after restart.

`Next run` is intentionally not stored. APScheduler calculates it from the persisted schedule after startup, preventing a stale timestamp from becoming authoritative.

## Startup behavior

1. The service applies additive schema checks.
2. SQL schedule state is loaded into a process cache.
3. If SQL has never been configured and `data/service_state.json` exists, the JSON schedule is migrated once into SQL.
4. The latest `ProductSyncCycle` row restores the dashboard's last-run outlet/success/failure values.
5. Aggregate cycle outcomes restore lifetime cycle counters.
6. `ProductSyncRetryQueue` restores actionable retry entries.
7. The service starts in `IDLE`; a historical `RUNNING` state is never restored.

Schedule updates are written to SQL before the live APScheduler job changes. Retry-queue clearing is also database-first. If the log database is unavailable at startup, the service uses default schedule values and reports restoration errors in logs; completed data synchronization remains governed by its existing log-database partial-failure behavior.

## Deployment

The application creates these tables automatically using the configured log-database account. For environments where DBAs control DDL, apply [PRODUCT_SYNC_STATE_SCHEMA.sql](PRODUCT_SYNC_STATE_SCHEMA.sql) before deployment and grant the service account `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on the three tables.
