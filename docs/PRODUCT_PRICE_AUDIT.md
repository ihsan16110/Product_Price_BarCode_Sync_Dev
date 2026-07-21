# ProductPrice Audit Trail

## Scope

The audit trail records ProductPrice rows affected by the existing synchronization rules:

- `INSERT`: a Head Office ProductPrice row did not exist at the outlet and was inserted.
- `UPDATE`: an existing outlet ProductPrice matched the existing changed-row logic and was updated.

Product and ProductBarcode synchronization logic is unchanged and is not recorded in this table.

## Trace identifiers and outcomes

Every full cycle shares one `RunID`; single-outlet and retry operations receive their own RunID. `ProductPriceChangeLog` stores EventID, RunID, ChangeType and before/after values. `ProductSyncLogHistory` stores one immutable outlet attempt with CapturedCount, LoggedCount and AuditStatus.

AuditStatus values:

- `NoChanges`: SQL explicitly returned CapturedCount=0.
- `Logged`: CapturedCount equals LoggedCount.
- `AuditFailed`: outlet data committed, but permanent audit logging failed; overall outlet status is `Partial`.
- `NotApplicable`: outlet synchronization failed before an auditable ProductPrice commit.

## Production verification

```sql
SELECT TOP (100)
    RunID, ChangeType, ProductCode, DepotCode,
    PreviousUnitPrice, CurrentUnitPrice,
    PreviousModifiedDate, CurrentModifiedDate,
    OutletCode, ChangedBy, ChangeOccurrenceTime
FROM dbo.ProductPriceChangeLog
ORDER BY LogID DESC;
```

```sql
SELECT TOP (100)
    RunID, TriggerType, DepotCode, SyncStatus, AuditStatus,
    CapturedCount, LoggedCount, AttemptTime, Remarks
FROM dbo.ProductSyncLogHistory
ORDER BY HistoryID DESC;
```

For every completed outlet attempt, require either `AuditStatus='NoChanges'` or `AuditStatus='Logged' AND CapturedCount=LoggedCount`. Investigate every `Partial/AuditFailed` result before another ProductPrice synchronization, because a later run cannot reconstruct the original before-values.
