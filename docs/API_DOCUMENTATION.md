# ProductPriceSync Service - API Documentation

**Base URL:** `http://<host>:8000`
**Content Type:** `application/json`
**Version:** 1.0.0

---

## Authentication and transport

The health endpoint and operational status/outlet endpoints are public for local
monitoring. Log and price-history reads require either `VIEWER_API_KEY` or
`ADMIN_API_KEY`. Synchronization, retry processing, schedule changes, and
cleanup require `ADMIN_API_KEY`.

Send the key on every protected request:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs
```

```powershell
$headers = @{ "X-API-Key" = $env:ADMIN_API_KEY }
Invoke-RestMethod -Headers $headers -Uri http://localhost:8000/ProductSync/api/logs
```

The dashboard asks for the key and retains it only in tab-scoped session
storage. Deploy behind an HTTPS reverse proxy and do not expose port 8000
directly to untrusted networks.

---

## Table of Contents

1. [Dashboard](#1-dashboard)
2. [Health Check](#2-health-check)
3. [Service Status](#3-service-status)
4. [Sync Operations](#4-sync-operations)
   - [Start Full Sync](#41-start-full-sync)
   - [Sync Single Outlet](#42-sync-single-outlet)
5. [Outlet Results](#5-outlet-results)
   - [Get All Outlet Results](#51-get-all-outlet-results)
   - [Get Single Outlet Result](#52-get-single-outlet-result)
6. [Retry Queue](#6-retry-queue)
   - [Get Retry Queue](#61-get-retry-queue)
   - [Force Process Retries](#62-force-process-retries)
   - [Clear Retry Queue](#63-clear-retry-queue)
7. [Settings](#7-settings)
   - [Get Current Settings](#71-get-current-settings)
   - [Update Schedule](#72-update-schedule)
8. [Logs](#8-logs)
   - [List Log Files](#81-list-log-files)
   - [Get Log Content](#82-get-log-content)
   - [Get Price Change History](#83-get-price-change-history)
9. [Testing Guide](#9-testing-guide)
10. [Error Codes Reference](#10-error-codes-reference)

---

## 1. Dashboard

**Serves the web-based monitoring dashboard.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/Dashboard` |
| **Response** | HTML page |
| **Auth** | None |

### Usage

Open in a browser:
```
http://localhost:8000/ProductSync/Dashboard
```

The dashboard provides:
- Real-time sync status with progress bar
- Outlet results table (color-coded by status)
- Retry queue visibility
- Schedule configuration (visual picker + cron expression)
- **Price Change History** table with filterable view
- Log viewer
- Manual sync trigger buttons

---

## 2. Health Check

**Simple health check for Docker healthcheck and load balancers.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/health` |
| **Response** | `200 OK` |

### Response Body

```json
{
  "status": "healthy",
  "timestamp": "2026-02-24T14:30:15.123456"
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"healthy"` when service is running |
| `timestamp` | string | ISO 8601 timestamp of the response |

### Testing

```bash
# curl
curl http://localhost:8000/ProductSync/api/health

# PowerShell
Invoke-RestMethod http://localhost:8000/ProductSync/api/health
```

---

## 3. Service Status

**Get overall service status including current sync cycle info, schedule, and retry queue size.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/status` |
| **Response** | `200 OK` |

### Response Body

```json
{
  "service": "ProductPriceSync",
  "uptime_seconds": 3621.5,
  "current_sync": {
    "state": "running",
    "started_at": "2026-02-24T14:00:00.000000",
    "finished_at": null,
    "total_outlets": 52,
    "completed": 38,
    "failed": 2,
    "in_progress": 5,
    "trigger": "scheduled"
  },
  "schedule": {
    "mode": "visual",
    "interval_minutes": 30,
    "description": "Every 30 min"
  },
  "next_scheduled_run": "2026-02-24T14:30:00.000000+03:00",
  "retry_queue_size": 2,
  "total_syncs_completed": 5
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `service` | string | Service name (`ProductPriceSync`) |
| `uptime_seconds` | float | Seconds since service started |
| `current_sync.state` | string | `"idle"` or `"running"` |
| `current_sync.started_at` | string/null | ISO timestamp when current/last sync started |
| `current_sync.finished_at` | string/null | ISO timestamp when last sync finished (null if running) |
| `current_sync.total_outlets` | int | Total outlets in current/last cycle |
| `current_sync.completed` | int | Successfully synced outlets |
| `current_sync.failed` | int | Failed outlets |
| `current_sync.in_progress` | int | Currently syncing outlets |
| `current_sync.trigger` | string | `"scheduled"`, `"manual"`, or `""` |
| `schedule.mode` | string | `"visual"` or `"cron"` |
| `schedule.interval_minutes` | int | Interval (visual mode only) |
| `schedule.cron_expression` | string | Cron expression (cron mode only) |
| `schedule.description` | string | Human-readable schedule description |
| `next_scheduled_run` | string/null | ISO timestamp of next scheduled sync |
| `retry_queue_size` | int | Number of outlets in retry queue |
| `total_cycles` | int | Persisted count of attempted full cycles |
| `total_syncs_completed` | int | Lifetime completed sync cycles |

### Testing

```bash
curl http://localhost:8000/ProductSync/api/status
```

---

## 4. Sync Operations

### 4.1. Start Full Sync

**Trigger a full sync cycle across all outlets. Runs in background.**

| Property | Value |
|----------|-------|
| **Method** | `POST` |
| **URL** | `/ProductSync/api/sync/start` |
| **Request Body** | None |
| **Response** | `200 OK` |

### Response Body (Success)

```json
{
  "message": "Sync cycle started",
  "trigger": "manual"
}
```

### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `409 Conflict` | Sync already running | `{"detail": "Sync cycle already in progress"}` |
| `503 Service Unavailable` | Service not initialized | `{"detail": "Service not initialized"}` |

### Testing

```bash
# Start a sync
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/sync/start

# PowerShell
Invoke-RestMethod -Headers $headers -Method POST http://localhost:8000/ProductSync/api/sync/start

# Then monitor progress
curl http://localhost:8000/ProductSync/api/status
```

### 4.2. Sync Single Outlet

**Trigger sync for a specific outlet by its code.**

| Property | Value |
|----------|-------|
| **Method** | `POST` |
| **URL** | `/ProductSync/api/sync/outlet/{outlet_code}` |
| **Request Body** | None |
| **Response** | `200 OK` |

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `outlet_code` | string | Yes | The outlet code (e.g., `B004`, `B012`) |

### Response Body (Success)

```json
{
  "outlet_code": "B004",
  "ip": "192.168.1.100",
  "status": "Success",
  "remarks": "Sync completed successfully",
  "timestamp": "2026-02-24T14:35:22.123456",
  "duration_seconds": 8.45
}
```

### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `404 Not Found` | Outlet code not found | `{"detail": "Outlet B999 not found"}` |
| `500 Internal Server Error` | Sync failed | `{"detail": "Connection timeout..."}` |
| `503 Service Unavailable` | Service not initialized | `{"detail": "Service not initialized"}` |

### Testing

```bash
# Sync a specific outlet
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/sync/outlet/B004

# PowerShell
Invoke-RestMethod -Headers $headers -Method POST http://localhost:8000/ProductSync/api/sync/outlet/B004
```

---

## 5. Outlet Results

### 5.1. Get All Outlet Results

**Get all outlet results from the last/current sync cycle.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/outlets` |
| **Response** | `200 OK` |

### Response Body

```json
[
  {
    "outlet_code": "B004",
    "ip": "192.168.1.100",
    "status": "Success",
    "remarks": "Sync completed successfully",
    "timestamp": "2026-02-24T14:35:22.123456",
    "duration_seconds": 8.45
  },
  {
    "outlet_code": "B012",
    "ip": "192.168.1.112",
    "status": "N",
    "remarks": "Connection timeout after 10 seconds",
    "timestamp": "2026-02-24T14:35:30.654321",
    "duration_seconds": 10.02
  }
]
```

### Result Fields

| Field | Type | Description |
|-------|------|-------------|
| `outlet_code` | string | Outlet identifier (e.g., `B004`) |
| `ip` | string | Outlet server IP address |
| `status` | string | `"Success"` or `"N"` (failed) |
| `remarks` | string | Success message or error description |
| `timestamp` | string | ISO timestamp of the sync attempt |
| `duration_seconds` | float | Time taken for the sync in seconds |

### Testing

```bash
curl http://localhost:8000/ProductSync/api/outlets
```

### 5.2. Get Single Outlet Result

**Get result for a specific outlet from the last/current sync cycle.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/outlets/{outlet_code}` |
| **Response** | `200 OK` |

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `outlet_code` | string | Yes | The outlet code (e.g., `B004`) |

### Response Body

```json
{
  "outlet_code": "B004",
  "ip": "192.168.1.100",
  "status": "Success",
  "remarks": "Sync completed successfully",
  "timestamp": "2026-02-24T14:35:22.123456",
  "duration_seconds": 8.45
}
```

### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `404 Not Found` | Outlet not in results | `{"detail": "No result found for outlet B999"}` |
| `503 Service Unavailable` | Service not initialized | `{"detail": "Service not initialized"}` |

### Testing

```bash
curl http://localhost:8000/ProductSync/api/outlets/B004
```

---

## 6. Retry Queue

### 6.1. Get Retry Queue

**Get current retry queue contents (pending retries + permanently failed).**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/retries` |
| **Response** | `200 OK` |

### Response Body

```json
{
  "entries": [
    {
      "outlet_code": "B012",
      "server": "192.168.1.112",
      "attempt": 2,
      "max_attempts": 3,
      "next_retry_at": "2026-02-24T14:40:35.123456",
      "last_error": "Connection timeout after 10 seconds",
      "added_at": "2026-02-24T14:35:30.654321"
    },
    {
      "outlet_code": "B045",
      "server": "192.168.1.145",
      "attempt": 4,
      "max_attempts": 3,
      "next_retry_at": "2026-02-24T14:38:00.000000",
      "last_error": "Login failed for user 'sa'",
      "added_at": "2026-02-24T14:30:10.000000",
      "permanently_failed": true
    }
  ],
  "size": 2,
  "pending": 1
}
```

### Entry Fields

| Field | Type | Description |
|-------|------|-------------|
| `outlet_code` | string | Outlet identifier |
| `server` | string | Outlet server IP |
| `attempt` | int | Current attempt number |
| `max_attempts` | int | Maximum retry attempts (from config) |
| `next_retry_at` | string | ISO timestamp for next retry (exponential backoff) |
| `last_error` | string | Error message from the last failed attempt |
| `added_at` | string | ISO timestamp when entry was added to queue |
| `permanently_failed` | bool | Present and `true` if max retries exceeded |

### Retry Queue Fields

| Field | Type | Description |
|-------|------|-------------|
| `entries` | array | List of retry entries |
| `size` | int | Total entries (pending + permanently failed) |
| `pending` | int | Entries still waiting for retry |

### Testing

```bash
curl http://localhost:8000/ProductSync/api/retries
```

### 6.2. Force Process Retries

**Force process all due retries immediately (without waiting for the 10-second background loop).**

| Property | Value |
|----------|-------|
| **Method** | `POST` |
| **URL** | `/ProductSync/api/retries/process-now` |
| **Request Body** | None |
| **Response** | `200 OK` |

### Response Body

```json
{
  "message": "Processing 3 retries",
  "processed": 3
}
```

If no retries are due:

```json
{
  "message": "No retries due",
  "processed": 0
}
```

### Testing

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/retries/process-now
```

### 6.3. Clear Retry Queue

**Clear the entire retry queue (pending + permanently failed).**

| Property | Value |
|----------|-------|
| **Method** | `DELETE` |
| **URL** | `/ProductSync/api/retries` |
| **Request Body** | None |
| **Response** | `200 OK` |

### Response Body

```json
{
  "message": "Retry queue cleared",
  "removed": 5
}
```

### Testing

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" -X DELETE http://localhost:8000/ProductSync/api/retries

# PowerShell
Invoke-RestMethod -Headers $headers -Method DELETE http://localhost:8000/ProductSync/api/retries
```

---

## 7. Settings

### 7.1. Get Current Settings

**Get current service settings including schedule info and valid intervals.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/settings` |
| **Response** | `200 OK` |

### Response Body

```json
{
  "schedule": {
    "mode": "visual",
    "interval_minutes": 30,
    "active_hours_start": 0,
    "active_minutes_start": 0,
    "active_hours_end": 23,
    "active_minutes_end": 0,
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "cron_expression": null,
    "description": "Every 30 minutes, all day, every day"
  },
  "valid_intervals": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 75, 120],
  "max_concurrent_syncs": 10,
  "retry_max_attempts": 3,
  "retry_base_delay_seconds": 30,
  "connect_timeout_seconds": 10,
  "query_timeout_seconds": 120
}
```

### Schedule Fields

| Field | Type | Description |
|-------|------|-------------|
| `schedule.mode` | string | `"visual"` or `"cron"` |
| `schedule.interval_minutes` | int | Current interval in minutes |
| `schedule.active_hours_start` | int | Active hours start (0-23) |
| `schedule.active_minutes_start` | int | Active start minute (`0` or `30`) |
| `schedule.active_hours_end` | int | Active hours end (0-23) |
| `schedule.active_minutes_end` | int | Active end minute (`0` or `30`) |
| `schedule.active_days` | array | Active days (`"mon"` through `"sun"`) |
| `schedule.cron_expression` | string/null | Raw cron expression (cron mode only) |
| `schedule.description` | string | Human-readable description |
| `valid_intervals` | array | Allowed interval values for visual mode |
| `max_concurrent_syncs` | int | Max simultaneous outlet syncs |
| `thread_pool_max_workers` | int | Explicit worker capacity for blocking ODBC and logging calls |
| `price_change_insert_batch_size` | int | ProductPrice audit rows sent per batch |
| `retry_max_attempts` | int | Max retry attempts per outlet |
| `retry_base_delay_seconds` | int | Base delay for exponential backoff |
| `connect_timeout_seconds` | int | SQL connection timeout |
| `query_timeout_seconds` | int | SQL query execution timeout |
| `outlet_sync_timeout_seconds` | int | Per-outlet asynchronous watchdog |
| `full_sync_timeout_seconds` | int | Full-cycle last-resort watchdog |

### Testing

```bash
curl http://localhost:8000/ProductSync/api/settings
```

### 7.2. Update Schedule

**Update the sync schedule. Supports two modes: Visual and Cron. Changes take effect immediately and persist across container restarts.**

| Property | Value |
|----------|-------|
| **Method** | `PUT` |
| **URL** | `/ProductSync/api/settings/schedule` |
| **Content-Type** | `application/json` |
| **Response** | `200 OK` |

### Request Body - Visual Mode

Use `mode: "visual"` with interval, active hours, and active days.

```json
{
  "mode": "visual",
  "interval_minutes": 15,
  "active_hours_start": 6,
  "active_minutes_start": 30,
  "active_hours_end": 22,
  "active_minutes_end": 30,
  "active_days": ["mon", "tue", "wed", "thu", "fri"]
}
```

### Visual Mode Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `mode` | string | No | `"visual"` | Set to `"visual"` |
| `interval_minutes` | int | Yes | - | Must be one of: `5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 75, 120` |
| `active_hours_start` | int | No | `0` | Hour to start running syncs (0-23) |
| `active_minutes_start` | int | No | `0` | Minute to start running syncs (`0` or `30`) |
| `active_hours_end` | int | No | `23` | Hour to stop running syncs (0-23) |
| `active_minutes_end` | int | No | `0` | Minute to stop running syncs (`0` or `30`) |
| `active_days` | array | No | All 7 days | Days to run syncs: `"mon"`, `"tue"`, `"wed"`, `"thu"`, `"fri"`, `"sat"`, `"sun"` |

### Visual Mode Response

```json
{
  "message": "Every 15min, 6-22h, mon,tue,wed,thu,fri",
  "mode": "visual",
  "interval_minutes": 15,
  "active_hours": "06:30 - 22:30",
  "active_days": ["mon", "tue", "wed", "thu", "fri"],
  "next_run": "2026-02-24T14:45:00.000000+03:00"
}
```

### Request Body - Day-wise Mode

Use `mode: "daywise"` when individual weekdays need different intervals or active windows. Disabled days may be submitted but are not persisted as active rules. An overnight window, such as `07:00` through `00:30`, ends on the following calendar day.

```json
{
  "mode": "daywise",
  "rules": [
    {"day": "sun", "enabled": true, "interval_minutes": 120, "active_hours_start": 7, "active_minutes_start": 0, "active_hours_end": 0, "active_minutes_end": 30},
    {"day": "mon", "enabled": true, "interval_minutes": 120, "active_hours_start": 7, "active_minutes_start": 0, "active_hours_end": 0, "active_minutes_end": 30},
    {"day": "tue", "enabled": true, "interval_minutes": 120, "active_hours_start": 7, "active_minutes_start": 0, "active_hours_end": 0, "active_minutes_end": 30},
    {"day": "wed", "enabled": true, "interval_minutes": 120, "active_hours_start": 7, "active_minutes_start": 0, "active_hours_end": 0, "active_minutes_end": 30},
    {"day": "thu", "enabled": true, "interval_minutes": 30, "active_hours_start": 9, "active_minutes_start": 0, "active_hours_end": 18, "active_minutes_end": 0},
    {"day": "fri", "enabled": false},
    {"day": "sat", "enabled": false}
  ]
}
```

Each enabled rule requires a unique `day`, a supported `interval_minutes`, hours from `0`–`23`, and minutes of `0` or `30`. At least one rule must be enabled.

### Request Body - Cron Mode

Use `mode: "cron"` with a standard 5-field cron expression.

```json
{
  "mode": "cron",
  "cron_expression": "*/30 6-22 * * mon-fri"
}
```

### Cron Mode Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `mode` | string | Yes | Must be `"cron"` |
| `cron_expression` | string | Yes | Standard 5-field cron: `minute hour day month day_of_week` |

### Cron Expression Format

```
┌───────────── minute (0-59, */N, ranges like 0,30)
│ ┌─────────── hour (0-23, ranges like 6-22)
│ │ ┌───────── day of month (1-31, *)
│ │ │ ┌─────── month (1-12, *)
│ │ │ │ ┌───── day of week (mon-sun, 0-6, ranges like mon-fri)
│ │ │ │ │
* * * * *
```

### Cron Expression Examples

| Expression | Description |
|------------|-------------|
| `*/30 * * * *` | Every 30 minutes, 24/7 |
| `*/15 6-22 * * *` | Every 15 minutes, 6 AM to 10 PM daily |
| `0 8,12,18 * * mon-fri` | At 8:00, 12:00, 18:00 on weekdays |
| `*/10 9-17 * * mon-fri` | Every 10 minutes during business hours (weekdays) |
| `0 */2 * * *` | Every 2 hours at :00 |
| `30 6 * * *` | Once daily at 6:30 AM |
| `0 0 1 * *` | Once monthly at midnight on the 1st |

### Cron Mode Response

```json
{
  "message": "Cron schedule applied: */30 6-22 * * mon-fri",
  "mode": "cron",
  "cron_expression": "*/30 6-22 * * mon-fri",
  "next_run": "2026-02-24T15:00:00.000000+03:00"
}
```

### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `400 Bad Request` | Invalid interval value | `{"detail": "interval_minutes must be one of: [5, 10, 15, ...]"}` |
| `400 Bad Request` | Invalid hours | `{"detail": "Hours must be 0-23"}` |
| `400 Bad Request` | Invalid days | `{"detail": "Invalid days. Use: ['mon', 'tue', ...]"}` |
| `400 Bad Request` | Empty or duplicate day-wise rules | A descriptive validation error |
| `400 Bad Request` | Missing cron expression | `{"detail": "cron_expression is required for cron mode"}` |
| `400 Bad Request` | Wrong cron field count | `{"detail": "Cron expression must have 5 fields: ..."}` |
| `400 Bad Request` | Invalid cron syntax | `{"detail": "Invalid cron expression: ..."}` |
| `503 Service Unavailable` | Scheduler not ready | `{"detail": "Scheduler not initialized"}` |

### Testing

```bash
# Visual mode - every 15 minutes, weekdays only, business hours
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "visual", "interval_minutes": 15, "active_hours_start": 8, "active_hours_end": 18, "active_days": ["mon", "tue", "wed", "thu", "fri"]}'

# Visual mode - every 30 minutes, all day, every day
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "visual", "interval_minutes": 30, "active_hours_start": 0, "active_hours_end": 23, "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}'

# Cron mode - every 30 minutes during business hours on weekdays
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "cron", "cron_expression": "*/30 8-18 * * mon-fri"}'

# Cron mode - three times daily at 8, 12, 18
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "cron", "cron_expression": "0 8,12,18 * * *"}'

# PowerShell - Visual mode
Invoke-RestMethod -Headers $headers -Method PUT -Uri http://localhost:8000/ProductSync/api/settings/schedule `
  -ContentType "application/json" `
  -Body '{"mode": "visual", "interval_minutes": 15}'
```

---

## 8. Logs

### 8.1. List Log Files

**List all available log files in the logs directory.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/logs` |
| **Response** | `200 OK` |

### Response Body

```json
{
  "files": [
    {
      "filename": "ProductSyncLog 2026-02-24.log",
      "size_bytes": 45230,
      "modified": "2026-02-24T14:35:00.000000"
    },
    {
      "filename": "ProductSyncLog 2026-02-23.log",
      "size_bytes": 128456,
      "modified": "2026-02-23T23:59:59.000000"
    }
  ]
}
```

### File Entry Fields

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Log file name |
| `size_bytes` | int | File size in bytes |
| `modified` | string | Last modified ISO timestamp |

### Testing

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs
```

### 8.2. Get Log Content

**Get log content for a specific date. Returns the last 1000 lines.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/logs/archive/{date}` |
| **Response** | `200 OK` (text/plain) |

### Path Parameters

| Parameter | Type | Required | Format | Description |
|-----------|------|----------|--------|-------------|
| `date` | string | Yes | `YYYY-MM-DD` | Date of the log file (e.g., `2026-02-24`) |

### Response

Returns plain text (not JSON) containing the last 1000 lines of the log file.

```
[2026-02-24 14:00:00] [INFO] [app.sync_manager] === Starting full sync cycle (trigger: scheduled) ===
[2026-02-24 14:00:00] [INFO] [app.sync_manager] Processing 52 outlets with max 10 concurrent
[2026-02-24 14:00:05] [INFO] [app.sync_engine] [B004] Sync started (192.168.1.100)
[2026-02-24 14:00:13] [INFO] [app.sync_engine] [B004] Sync completed successfully in 8.45s
...
```

### Error Responses

| Status | Condition | Body |
|--------|-----------|------|
| `404 Not Found` | Log file not found | `{"detail": "Log file not found for date 2026-01-01"}` |
| `500 Internal Server Error` | Read error | `{"detail": "Error reading log file: ..."}` |

### Testing

```bash
# Get today's logs
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs/archive/2026-02-24

# Get a previous day's logs
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs/archive/2026-02-23
```

### 8.3. Get Price Change History

**Query the ProductPriceChangeLog table for a detailed audit trail of price updates, showing old vs new UnitPrice and ModifiedDate per product per outlet.**

| Property | Value |
|----------|-------|
| **Method** | `GET` |
| **URL** | `/ProductSync/api/logs/price-changes` |
| **Query Parameters** | `outlet_code` (optional), `days` (default 7, max 30), `limit` (default 100) |
| **Response** | `200 OK` |

### 8.4. Manual Price Change Cleanup

**Manually trigger cleanup of old price change records. Uses batched DELETE to keep transaction log growth minimal.**

| Property | Value |
|----------|-------|
| **Method** | `POST` |
| **URL** | `/ProductSync/api/cleanup/price-changes` |
| **Request Body** | JSON (all fields optional) |
| **Response** | `200 OK` |

### Request Body

```json
{
  "retention_days": 90,
  "batch_size": 5000
}
```

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `retention_days` | int | No | `90` (from service config) | Delete records older than this many days |
| `batch_size` | int | No | `5000` | Rows to delete per batch. Smaller = shorter locks but more round-trips |

### Response Body

```json
{
  "status": "ok",
  "deleted": 15420,
  "retention_days": 90,
  "batch_size": 5000
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` on success, `"error"` on failure |
| `deleted` | int | Total rows removed across all batches |
| `retention_days` | int/null | The age threshold that was applied |
| `batch_size` | int/null | Batch size that was used |

### Implementation Notes

- Uses **batched** `DELETE TOP (?)` in a `WHILE` loop — each batch commits independently
- A dedicated index `IX_ProductPriceChangeLog_ChangeOccurrenceTime` on `(ChangeOccurrenceTime DESC)` makes the WHERE clause seekable
- Also runs automatically every night at midnight via APScheduler

### Testing

```bash
# Default cleanup (90 days, 5000/batch)
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/cleanup/price-changes

# Custom: keep last 30 days, delete 1000 per batch
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/cleanup/price-changes \
  -H "Content-Type: application/json" \
  -d '{"retention_days": 30, "batch_size": 1000}'

# PowerShell
$body = @{retention_days = 60} | ConvertTo-Json
Invoke-RestMethod -Headers $headers -Method POST -Uri http://localhost:8000/ProductSync/api/cleanup/price-changes `
  -ContentType "application/json" `
  -Body $body
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `outlet_code` | string | No | - | Filter by specific outlet code (e.g., `B004`) |
| `days` | int | No | `7` | Number of days back to search (1-30) |
| `limit` | int | No | `100` | Maximum number of change records to return |

### Response Body

```json
{
  "changes": [
    {
      "log_id": 1,
      "product_code": "PRD001",
      "depot_code": "B004",
      "previous_unit_price": 15.50,
      "previous_modified_date": "2026-02-23T10:00:00",
      "current_unit_price": 18.75,
      "current_modified_date": "2026-02-24T14:35:00",
      "change_occurrence_time": "2026-02-24T14:35:22",
      "outlet_code": "B004",
      "changed_by": "admin"
    }
  ],
  "count": 1,
  "filters": {
    "outlet_code": null,
    "days": 7,
    "limit": 100
  }
}
```

### Change Fields

| Field | Type | Description |
|-------|------|-------------|
| `log_id` | int | Auto-incrementing log ID |
| `product_code` | string | Product identifier |
| `depot_code` | string | Depot/outlet code from ProductPrice |
| `previous_unit_price` | float/null | UnitPrice BEFORE the update |
| `previous_modified_date` | string/null | ModifiedDate BEFORE the update |
| `current_unit_price` | float | UnitPrice AFTER the update |
| `current_modified_date` | string | ModifiedDate AFTER the update |
| `change_occurrence_time` | string | When the change was logged |
| `outlet_code` | string/null | Which outlet server recorded this |
| `changed_by` | string/null | User who made the change (from source ModifiedBy) |

### Usage Notes

- Records are only written when `UnitPrice` or `ModifiedDate` actually changed (conditional UPDATE)
- Historical data accumulates — older records can be purged via a cleanup job if needed
- This table is append-only (INSERT), never updated or deleted by the service

### Testing

```bash
# Last 7 days of price changes (default)
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs/price-changes

# Last 24 hours for a specific outlet
curl -H "X-API-Key: $ADMIN_API_KEY" "http://localhost:8000/ProductSync/api/logs/price-changes?outlet_code=B004&days=1&limit=50"

# Last 30 days for all outlets
curl -H "X-API-Key: $ADMIN_API_KEY" "http://localhost:8000/ProductSync/api/logs/price-changes?days=30"
```

---

## 9. Testing Guide

### Quick Start Testing Sequence

After starting the service with `docker compose up -d`, run these commands in order:

```bash
# 1. Verify service is running
curl http://localhost:8000/ProductSync/api/health

# 2. Check initial status
curl http://localhost:8000/ProductSync/api/status

# 3. Get current settings
curl http://localhost:8000/ProductSync/api/settings

# 4. Trigger a full sync
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/sync/start

# 5. Monitor sync progress (poll every few seconds)
curl http://localhost:8000/ProductSync/api/status

# 6. View results after sync completes
curl http://localhost:8000/ProductSync/api/outlets

# 7. Check specific outlet
curl http://localhost:8000/ProductSync/api/outlets/B004

# 8. Check retry queue for failed outlets
curl http://localhost:8000/ProductSync/api/retries

# 9. Force process retries
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/retries/process-now

# 10. Change schedule to every 15 minutes
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "visual", "interval_minutes": 15}'

# 11. Verify schedule changed
curl http://localhost:8000/ProductSync/api/settings

# 12. View today's logs
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs/archive/2026-02-24

# 13. List all log files
curl -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/ProductSync/api/logs

# 14. View recent price changes
curl -H "X-API-Key: $ADMIN_API_KEY" "http://localhost:8000/ProductSync/api/logs/price-changes?days=1"

# 15. Manually trigger price change log cleanup
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/cleanup/price-changes

# 16. Custom cleanup (keep 60 days, 2000 per batch)
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://localhost:8000/ProductSync/api/cleanup/price-changes \
  -H "Content-Type: application/json" \
  -d '{"retention_days": 60, "batch_size": 2000}'
```

### Interactive Testing with the Dashboard

1. Open `http://localhost:8000/ProductSync/Dashboard` in a browser
2. The dashboard auto-refreshes every 5 seconds
3. Use the **"Start Sync"** button to trigger a manual sync
4. Watch the progress bar and outlet table update in real-time
5. Use the **Schedule** panel to change the sync interval
6. Switch between **Visual** and **Advanced (Cron)** tabs for scheduling
7. View the **Retry Queue** section for failed outlets
8. View the **Price Change History** section to see per-product price changes
9. Use the **Logs** tab to view service logs

### Testing Schedule Persistence

```bash
# 1. Set a schedule
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "visual", "interval_minutes": 15}'

# 2. Restart the container
docker compose restart

# 3. Verify schedule was restored
curl http://localhost:8000/ProductSync/api/settings
# Should show interval_minutes: 15
```

### Testing Cron Schedule

```bash
# Set a cron schedule: every 10 minutes during business hours, weekdays only
curl -H "X-API-Key: $ADMIN_API_KEY" -X PUT http://localhost:8000/ProductSync/api/settings/schedule \
  -H "Content-Type: application/json" \
  -d '{"mode": "cron", "cron_expression": "*/10 9-17 * * mon-fri"}'

# Verify next run time
curl http://localhost:8000/ProductSync/api/status | python -m json.tool
```

### PowerShell Testing Examples

```powershell
# Health check
Invoke-RestMethod http://localhost:8000/ProductSync/api/health

# Status
Invoke-RestMethod http://localhost:8000/ProductSync/api/status | ConvertTo-Json -Depth 5

# Start sync
Invoke-RestMethod -Headers $headers -Method POST http://localhost:8000/ProductSync/api/sync/start

# Update schedule
$body = @{
    mode = "visual"
    interval_minutes = 15
    active_hours_start = 8
    active_hours_end = 18
    active_days = @("mon", "tue", "wed", "thu", "fri")
} | ConvertTo-Json

Invoke-RestMethod -Method PUT `
  -Uri http://localhost:8000/ProductSync/api/settings/schedule `
  -ContentType "application/json" `
  -Body $body

# Cron schedule
$body = @{
    mode = "cron"
    cron_expression = "*/30 6-22 * * *"
} | ConvertTo-Json

Invoke-RestMethod -Method PUT `
  -Uri http://localhost:8000/ProductSync/api/settings/schedule `
  -ContentType "application/json" `
  -Body $body

# View price changes
Invoke-RestMethod -Headers $headers "http://localhost:8000/ProductSync/api/logs/price-changes?days=7"
```

### FastAPI Auto-Generated Docs

FastAPI provides built-in interactive API documentation:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

These provide an interactive interface to test all endpoints directly from the browser.

---

## 10. Error Codes Reference

| HTTP Status | Meaning | When It Occurs |
|-------------|---------|----------------|
| `200 OK` | Success | Request completed successfully |
| `400 Bad Request` | Invalid input | Invalid interval, cron expression, hours, or days |
| `404 Not Found` | Resource not found | Outlet code or log file not found |
| `409 Conflict` | Resource conflict | Sync cycle already running |
| `500 Internal Server Error` | Server error | Unexpected errors, sync failures |
| `503 Service Unavailable` | Not ready | Service still initializing |

### Standard Error Response Format

All error responses follow this structure:

```json
{
  "detail": "Human-readable error message"
}
```

---

## Endpoint Summary Table

| # | Method | Endpoint | Description |
|---|--------|----------|-------------|
| 1 | `GET` | `/ProductSync/Dashboard` | Dashboard HTML page |
| 2 | `GET` | `/ProductSync/api/health` | Health check |
| 3 | `GET` | `/ProductSync/api/status` | Service status with sync cycle info |
| 4 | `POST` | `/ProductSync/api/sync/start` | Trigger full sync cycle |
| 4a | `POST` | `/ProductSync/api/sync/stop` | Gracefully stop the current full sync cycle |
| 5 | `POST` | `/ProductSync/api/sync/outlet/{outlet_code}` | Sync single outlet |
| 6 | `GET` | `/ProductSync/api/outlets` | All outlet results |
| 7 | `GET` | `/ProductSync/api/outlets/{outlet_code}` | Single outlet result |
| 8 | `GET` | `/ProductSync/api/retries` | Retry queue contents |
| 9 | `POST` | `/ProductSync/api/retries/process-now` | Force process retries |
| 10 | `DELETE` | `/ProductSync/api/retries` | Clear retry queue |
| 11 | `GET` | `/ProductSync/api/settings` | Current settings |
| 12 | `PUT` | `/ProductSync/api/settings/schedule` | Update schedule (visual/cron) |
| 12a | `POST` | `/ProductSync/api/settings/schedule/pause` | Pause future scheduled sync cycles |
| 12b | `POST` | `/ProductSync/api/settings/schedule/resume` | Resume future scheduled sync cycles |
| 13 | `GET` | `/ProductSync/api/logs` | List log files |
| 14 | `GET` | `/ProductSync/api/logs/archive/{date}` | Log content by date |
| 15 | `GET` | `/ProductSync/api/logs/price-changes` | Price change history with audit trail |
| 16 | `POST` | `/ProductSync/api/cleanup/price-changes` | **Manual price change cleanup trigger (batched)** |
