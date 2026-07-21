# Production rollout plan: concurrency 10 to 20

Date: 19 July 2026  
Target: `MAX_CONCURRENT_SYNCS=20`  
Worker capacity: `THREAD_POOL_MAX_WORKERS=40`  
Audit batch size: `PRICE_CHANGE_INSERT_BATCH_SIZE=500`

## Decision

Roll out concurrency 20 in two controlled stages: 10 to 15, then 15 to 20. Do not treat deployment success or total-duration improvement alone as approval. Correctness, audit integrity, database headroom, tail latency, and failure behavior must all remain acceptable.

The two-hour operating baseline at concurrency 10 implies an ideal linear duration near 60 minutes at concurrency 20. This is a planning lower bound, not a forecast. Head Office linked-server reads and the shared log database can prevent linear scaling.

## Changes included in this release

- An explicit 40-worker default executor replaces Python's implicit executor sizing.
- The concurrency setting is 20.
- `ProductSyncLog`, `ProductPriceChangeLog`, their indexes/migrations, and `ProductSyncLogHistory` are checked once per service process. A failed startup check is retried lazily by the first logging operation.
- ProductPrice audit events use `fast_executemany` in batches of 500, followed by one durable-count verification query per batch.
- Duplicate EventIDs in one batch are rejected before writing.
- The full-cycle watchdog is 10,800 seconds for the rollout so it remains a last-resort guard and does not invalidate comparison with the reported two-hour baseline.

## Owners required during rollout

- Application operator: deploy, start/stop cycles, capture application metrics, and execute rollback.
- Head Office DBA: watch source SQL Server load, waits, blocking, deadlocks, and linked-server activity.
- Log database DBA: watch audit write latency, transaction log, blocking, and storage latency.
- Network operator: watch Head Office and outlet-path throughput, retransmits, and packet loss.
- Business/data validator: reconcile a sample of Product, ProductPrice, ProductBarcode, and audit records.

## Phase 0 — Pre-production gates

Complete before changing a production worker count:

1. Build the exact production image and run the complete Python and JavaScript regression suites.
2. Verify startup logs show `Max concurrent syncs: 20`, `ODBC thread pool workers: 40`, and successful audit-schema initialization.
3. Confirm the runtime settings endpoint reports concurrency 20, thread workers 40, batch size 500, and full-cycle timeout 10,800 seconds.
4. Confirm the log database account can create/alter the audit schema during startup. After startup, verify later outlet completions do not issue repeated audit DDL.
5. Confirm required unique/indexed keys exist:
   - Head Office `ProductPrice` supports depot/date filtering.
   - Outlet `Product(ProductCode)`.
   - Outlet `ProductPrice(ProductCode, DepotCode)`.
   - Outlet `ProductBarcode(ProductCode, BarCode)`.
   - Outlet `ProductVfmg(ProductCode, Active)`.
   - Log `ProductPriceChangeLog(EventID)` unique index.
6. Pause the normal schedule for controlled test cycles. Do not run cleanup, manual outlet syncs, or manual retry-all actions during comparisons.
7. Record the deployed image/revision and export the current environment settings without credentials.

## Phase 1 — Establish the concurrency-10 baseline

Run at least two representative full cycles at 10 using the new code. This separates code-change effects from concurrency effects.

Capture:

- Total cycle duration and completed/failed/partial/excluded/cancelled counts.
- Outlet duration p50, p95, p99, and maximum.
- Connection failures, query timeouts, outlet-watchdog timeouts, retries, and deadlocks.
- Maximum simultaneous active outlets and maximum draining outlets.
- CapturedCount versus LoggedCount for every successful outlet.
- ProductPrice audit rows per outlet and audit-write p50/p95/p99 duration.
- Head Office CPU, data-file read latency, throughput, waits, blocking, and linked-server sessions.
- Log database CPU, write latency, transaction-log growth, blocking, and deadlocks.
- Application process CPU, memory, thread count, and executor queueing if available.
- Network throughput, retransmits, packet loss, and connection-reset rate.

Use the median of valid cycles as the comparison baseline. Keep outlier cycles in the record with an explanation; do not silently discard them.

## Phase 2 — Concurrency 15 canary

1. Set `MAX_CONCURRENT_SYNCS=15`; keep the executor at 40.
2. Restart the service so the semaphore and schema cache are recreated from configuration.
3. Verify effective settings before enabling a cycle.
4. Run one controlled full cycle with continuous monitoring, then a second cycle if the first passes.
5. Compare all metrics with the concurrency-10 baseline.

Promote to 20 only when:

- No Product/ProductPrice/ProductBarcode reconciliation issue is found.
- Every successful outlet has `CapturedCount = LoggedCount`; there are no unexplained duplicate EventIDs.
- No concurrency-attributable deadlock occurs.
- Failure, Partial, timeout, and retry rates are no worse than baseline by more than 2 percentage points and are not more than twice their baseline rates.
- Head Office and log database CPU remain below the DBA-approved ceiling. Until a local ceiling is approved, use sustained 80% CPU for five minutes as a conservative stop threshold.
- Database storage latency and network error indicators do not show sustained degradation from baseline.
- Draining operations do not accumulate across waves.
- Total duration improves by at least 15% without a material p95/p99 outlet-duration regression.

## Phase 3 — Concurrency 20 target

Repeat the Phase 2 procedure at 20. Run at least two controlled cycles and one normal scheduled cycle before declaring the target stable.

Expected outcome:

- Mathematical lower bound: approximately 60 minutes from the stated two-hour/10-worker baseline.
- Production acceptance: measured improvement with all correctness and reliability gates passing. A duration above 60 minutes is acceptable when the system remains healthy and the improvement is operationally useful.

Do not promote to 30 as part of this release. Concurrency 30 requires a separate decision using the measured 10/15/20 scaling curve and shared-resource headroom.

## Immediate rollback triggers

Rollback to 10 immediately on any of the following:

- Product, price, barcode, or audit data mismatch.
- Any unexplained `CapturedCount != LoggedCount` or material increase in `Partial/AuditFailed` results.
- Repeated or correlated deadlocks, query timeouts, connection resets, or outlet watchdogs.
- Growing draining-operation count or evidence that effective ODBC work is exceeding safe capacity.
- Sustained Head Office or log database resource saturation, blocking that affects other workloads, or DBA stop request.
- Material transaction-log growth or storage-latency degradation on the log database.
- Less than 15% cycle improvement combined with worse p95/p99 outlet latency or higher failure rate.
- Any customer-facing or outlet-facing degradation temporally associated with the rollout.

## Rollback procedure

1. Request graceful cancellation from the service and wait for active/draining outlet operations to finish or reach their SQL timeout.
2. Set `MAX_CONCURRENT_SYNCS=10`. Keep `THREAD_POOL_MAX_WORKERS=40`; excess idle workers do not create database load and retaining it avoids another variable during rollback.
3. Restart the service and verify the effective settings endpoint.
4. Run targeted reconciliation for outlets active when rollback began.
5. Validate ProductPrice audit counts and investigate every `Partial/AuditFailed` attempt before another price synchronization can overwrite before-values.
6. Run one controlled concurrency-10 recovery cycle and compare it with the recorded baseline.
7. Record the incident window, trigger, database/network evidence, affected outlets, and remediation owner.

## First-week monitoring

- Days 1–2: operator and DBA coverage for every full cycle; review immediately after completion.
- Days 3–4: automated alert review plus one daily engineering review.
- Days 5–7: daily scorecard covering duration, success/partial/failure rates, retries, timeouts, deadlocks, audit integrity, p95/p99 outlet duration, Head Office load, and log write latency.
- End of week: approve continued concurrency 20, tune down to 15, or roll back to 10. Do not consider 30 until this review is complete.

## Required evidence for closure

- Configuration snapshot and deployed revision.
- At least two valid cycles at 10, two at 15, and three at 20 including one scheduled cycle.
- Per-stage metric comparison and scaling efficiency.
- DBA and network approval.
- Data reconciliation sign-off.
- Documented disposition of every timeout, deadlock, Partial result, audit mismatch, and rollback event.
