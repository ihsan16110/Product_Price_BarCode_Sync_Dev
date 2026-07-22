def _sql_identifier(value: str) -> str:
    """Escape a SQL Server identifier that will be wrapped in square brackets."""
    return str(value).replace("]", "]]")


def _sql_literal(value: str) -> str:
    """Escape a value embedded inside the quoted OPENQUERY statement."""
    # The value is nested twice: once as a remote SQL literal and once inside
    # OPENQUERY's local SQL string. Each input quote therefore becomes four.
    return str(value).replace("'", "''''")


def get_sync_sql(local_db: str, central_linked_server: str, central_db: str, outlet_code: str) -> str:
    """
    Build the outlet-side synchronization batch.

    Pending rows are read from the Head Office Rep* tables through the outlet's
    linked server. The final marker result sets identify the exact ProductCode /
    DepotCode pairs that Python may acknowledge on Head Office only after the
    outlet transaction commits.
    """
    local_db_id = _sql_identifier(local_db)
    linked_server_id = _sql_identifier(central_linked_server)
    central_db_id = _sql_identifier(central_db)
    outlet_code_literal = _sql_literal(outlet_code)

    return f"""
SET NOCOUNT ON;
USE [{local_db_id}];

-- Load product master data from Head Office.
SELECT TOP 0
    ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID,
    ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc,
    VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus,
    ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate,
    PosType, 'Y' AS AllowNegative
INTO #HoProduct
FROM Product;

INSERT INTO #HoProduct
SELECT ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID,
       ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc,
       VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus,
       ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate,
       PosType, 'Y' AS AllowNegative
FROM OPENQUERY([{linked_server_id}],
    'SELECT ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID,
            ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc,
            VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus,
            ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate,
            PosType
     FROM [{central_db_id}].dbo.RepProduct');

-- Load pending non-delete price rows. Delete markers are deliberately excluded
-- so a deleted outlet row cannot be inserted again later in this batch.
SELECT TOP 0
    ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy,
    ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc,
    SDVATDiscPerc, VATCalcType, CAST('N' AS VARCHAR(1)) AS Price
INTO #HoProductPrice
FROM ProductPrice;

INSERT INTO #HoProductPrice
SELECT ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy,
       ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc,
       SDVATDiscPerc, VATCalcType, Price
FROM OPENQUERY([{linked_server_id}],
    'SELECT ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy,
            ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc,
            SDVATDiscPerc, VATCalcType, Price
     FROM [{central_db_id}].dbo.RepProductPrice
     WHERE SyncStatus = ''N''
       AND ISNULL(SyncType, '''') <> ''D''
       AND DepotCode = ''{outlet_code_literal}''');

-- Load pending delete markers independently from the normal upsert rows.
SELECT TOP 0 ProductCode, DepotCode
INTO #HoProductPriceDelete
FROM ProductPrice;

INSERT INTO #HoProductPriceDelete
SELECT ProductCode, DepotCode
FROM OPENQUERY([{linked_server_id}],
    'SELECT ProductCode, DepotCode
     FROM [{central_db_id}].dbo.RepProductPrice
     WHERE SyncStatus = ''N''
       AND SyncType = ''D''
       AND DepotCode = ''{outlet_code_literal}''');

-- Load barcode data from Head Office.
SELECT TOP 0 ProductCode, BarCode, CreatedBy, CreatedDate, Active
INTO #HoBarcode
FROM ProductBarcode;

INSERT INTO #HoBarcode
SELECT ProductCode, BarCode, CreatedBy, CreatedDate, Active
FROM OPENQUERY([{linked_server_id}],
    'SELECT ProductCode, BarCode, CreatedBy, CreatedDate, Active
     FROM [{central_db_id}].dbo.RepProductBarcode');

-- Synchronize Product.
INSERT INTO Product (
    ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID,
    ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc,
    VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus,
    ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate,
    PosType, AllowNegative
)
SELECT D.*
FROM #HoProduct D
LEFT JOIN Product P ON D.ProductCode = P.ProductCode
WHERE P.ProductCode IS NULL;

UPDATE P
SET P.ProductName = D.ProductName,
    P.UnitPrice = D.UnitPrice,
    P.Active = D.Active,
    P.VATPerc = D.VATPerc,
    P.VAT = D.VAT
FROM Product P
INNER JOIN #HoProduct D ON D.ProductCode = P.ProductCode;

-- Apply delete markers before normal price upserts.
DELETE P
FROM ProductPrice P
INNER JOIN #HoProductPriceDelete D
    ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode;

-- Insert missing ProductPrice rows. If Price is not Y, preserve the sample
-- operation's fallback of zero because there is no existing outlet row.
INSERT INTO ProductPrice (
    ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy,
    ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc,
    SDVATDiscPerc, VATCalcType
)
SELECT D.ProductCode,
       D.DepotCode,
       CASE WHEN D.Price = 'Y' THEN D.UnitPrice ELSE ISNULL(P.UnitPrice, 0) END,
       D.VATPerc,
       D.VAT,
       D.MRP,
       D.ModifiedBy,
       D.ModifiedDate,
       D.VATDiscount,
       D.EffectedDate,
       D.VATDiscPerc,
       D.SDVATPerc,
       D.SDVATDiscPerc,
       D.VATCalcType
FROM #HoProductPrice D
LEFT JOIN ProductPrice P
    ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode
WHERE P.ProductCode IS NULL OR P.DepotCode IS NULL;

-- For existing rows, Price controls only UnitPrice. Other supplied fields keep
-- the behavior of the approved sample operation.
UPDATE P
SET P.UnitPrice = CASE WHEN D.Price = 'Y' THEN D.UnitPrice ELSE P.UnitPrice END,
    P.VATPerc = D.VATPerc,
    P.VAT = D.VAT,
    P.ModifiedBy = D.ModifiedBy,
    P.ModifiedDate = D.ModifiedDate,
    P.EffectedDate = D.EffectedDate,
    P.VATDiscPerc = D.VATDiscPerc,
    P.VATCalcType = D.VATCalcType
FROM ProductPrice P
INNER JOIN #HoProductPrice D
    ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode;

-- Synchronize ProductBarcode.
INSERT INTO ProductBarcode (ProductCode, BarCode, CreatedBy, CreatedDate, Active)
SELECT D.ProductCode, D.BarCode, D.CreatedBy, D.CreatedDate, D.Active
FROM #HoBarcode D
LEFT JOIN ProductBarcode P
    ON D.ProductCode = P.ProductCode AND D.BarCode = P.BarCode
WHERE P.ProductCode IS NULL OR P.BarCode IS NULL;

-- Return both upserted and deleted keys for the separate post-commit HO update.
SELECT A.ProductCode, A.DepotCode
INTO #HoAcknowledgements
FROM (
    SELECT ProductCode, DepotCode FROM #HoProductPrice
    UNION
    SELECT ProductCode, DepotCode FROM #HoProductPriceDelete
) A;

SELECT 'HO_ACK_SUMMARY' AS Marker, COUNT(*) AS AcknowledgementCount
FROM #HoAcknowledgements;

SELECT 'HO_ACKNOWLEDGEMENTS' AS Marker, ProductCode, DepotCode
FROM #HoAcknowledgements;

DROP TABLE #HoAcknowledgements;
DROP TABLE #HoProductPriceDelete;
DROP TABLE #HoProduct;
DROP TABLE #HoProductPrice;
DROP TABLE #HoBarcode;
"""


LINKED_SERVER_CHECK_SQL = "SELECT COUNT(*) FROM sys.servers WHERE name = ?"

LINKED_SERVER_CREATE_TEMPLATE = """
    EXEC master.dbo.sp_addlinkedserver
        @server    = N'{linked_server_name}',
        @srvproduct= N'',
        @provider  = N'SQLNCLI',
        @datasrc   = N'{linked_server_name}';
"""
