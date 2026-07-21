/*
  Persistent dashboard, schedule, cycle-summary, and retry state.
  The service applies the same additive DDL automatically during startup.
  Run this script manually only when schema changes are DBA-controlled.
*/

IF OBJECT_ID('dbo.ProductSyncServiceState', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ProductSyncServiceState (
        StateID             TINYINT NOT NULL PRIMARY KEY,
        ScheduleEnabled     BIT NOT NULL DEFAULT 1,
        ScheduleMode        VARCHAR(10) NULL,
        IntervalMinutes     INT NULL,
        ActiveHoursStart    TINYINT NOT NULL DEFAULT 0,
        ActiveMinutesStart  TINYINT NOT NULL DEFAULT 0,
        ActiveHoursEnd      TINYINT NOT NULL DEFAULT 23,
        ActiveMinutesEnd    TINYINT NOT NULL DEFAULT 0,
        ActiveDays          VARCHAR(50) NOT NULL DEFAULT 'mon,tue,wed,thu,fri,sat,sun',
        CronExpression      VARCHAR(100) NULL,
        ScheduleRulesJson   NVARCHAR(MAX) NULL,
        IsConfigured        BIT NOT NULL DEFAULT 0,
        UpdatedAt           DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT CK_ProductSyncServiceState_Singleton CHECK (StateID = 1)
    );
END;

IF NOT EXISTS (SELECT 1 FROM dbo.ProductSyncServiceState WHERE StateID = 1)
    INSERT INTO dbo.ProductSyncServiceState (StateID) VALUES (1);

IF COL_LENGTH('dbo.ProductSyncServiceState', 'ScheduleRulesJson') IS NULL
    ALTER TABLE dbo.ProductSyncServiceState ADD ScheduleRulesJson NVARCHAR(MAX) NULL;

IF OBJECT_ID('dbo.ProductSyncCycle', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ProductSyncCycle (
        RunID           UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
        TriggerType     VARCHAR(30) NULL,
        StartedAt       DATETIME2 NOT NULL,
        FinishedAt      DATETIME2 NOT NULL,
        Outcome         VARCHAR(30) NOT NULL,
        TotalOutlets    INT NOT NULL DEFAULT 0,
        Successful      INT NOT NULL DEFAULT 0,
        Failed          INT NOT NULL DEFAULT 0,
        Excluded        INT NOT NULL DEFAULT 0,
        Cancelled       INT NOT NULL DEFAULT 0,
        AuditFailed     INT NOT NULL DEFAULT 0,
        RetryQueueSize  INT NOT NULL DEFAULT 0,
        DurationSeconds DECIMAL(18,2) NULL
    );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_ProductSyncCycle_FinishedAt'
      AND object_id = OBJECT_ID('dbo.ProductSyncCycle')
)
    CREATE INDEX IX_ProductSyncCycle_FinishedAt
        ON dbo.ProductSyncCycle (FinishedAt DESC);

IF OBJECT_ID('dbo.ProductSyncRetryQueue', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ProductSyncRetryQueue (
        OutletCode        VARCHAR(10) NOT NULL PRIMARY KEY,
        ServerAddress     VARCHAR(255) NOT NULL,
        Attempt           INT NOT NULL,
        MaxAttempts       INT NOT NULL,
        LastError         NVARCHAR(2000) NULL,
        AddedAt           DATETIME2 NOT NULL,
        NextRetryAt       DATETIME2 NOT NULL,
        PermanentlyFailed BIT NOT NULL DEFAULT 0,
        UpdatedAt         DATETIME2 NOT NULL DEFAULT SYSDATETIME()
    );
END;
