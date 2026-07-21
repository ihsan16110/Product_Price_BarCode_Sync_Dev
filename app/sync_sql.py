def get_sync_sql(local_db: str, central_linked_server: str, central_db: str, outlet_code: str) -> str:
    """
    Returns the sync SQL batch that runs on each outlet server.
    Syncs Product, ProductPrice, and ProductBarcode from central Head Office to outlet.
    The final result set contains price change data (marker 'PRICE_CHANGES') for audit logging.
    """
    return f"""
SET NOCOUNT ON;
USE [{local_db}];

-- ============================================================
-- PRODUCT: Load product data from central via linked server
-- ============================================================
SELECT ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID, ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc, VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus, ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate, PosType, 'Y' AS AllowNegative
INTO #HoProduct
FROM OPENQUERY([{central_linked_server}], 'SELECT ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID, ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc, VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus, ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate, PosType FROM {central_db}.DBO.Product D');

-- ============================================================
-- PRODUCT PRICE: Load price data from central (filtered by date and depot)
-- ============================================================
SELECT ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy, ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc, SDVATDiscPerc, VATCalcType
INTO #HoProductPrice
FROM OPENQUERY([{central_linked_server}], 'SELECT ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy, ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc, SDVATDiscPerc, VATCalcType FROM {central_db}.DBO.ProductPrice D WHERE (ModifiedDate >= LEFT(GETDATE()-1, 11) OR EffectedDate >= LEFT(GETDATE()-1, 11)) AND DepotCode = ''{outlet_code}''');

-- ============================================================
-- PRODUCT BARCODE: Load barcode data from central
-- ============================================================
SELECT ProductCode, BarCode, CreatedBy, CreatedDate, Active
INTO #HoBarcode
FROM OPENQUERY([{central_linked_server}], 'SELECT ProductCode, BarCode, CreatedBy, CreatedDate, Active FROM {central_db}.DBO.ProductBarcode D');

-- ============================================================
-- INSERT new products into outlet Product table
-- ============================================================
INSERT INTO Product (ProductCode, ProductName, BarCode, PackSize, SubCategoryID, BrandID, ManufacturerID, SubCategoryMD, CostPrice, UnitPrice, VATID, VATPerc, VAT, MRP, MinStock, MaxStock, MinOrderQty, Active, DiscountStatus, ExpiryManagement, CreatedBy, CreatedDate, ModifiedBy, ModifiedDate, PosType, AllowNegative)
SELECT D.* FROM #HoProduct D
LEFT JOIN Product P ON D.ProductCode = P.ProductCode
WHERE P.ProductCode IS NULL;

-- ============================================================
-- UPDATE existing products in outlet Product table
-- ============================================================
UPDATE P
SET P.ProductName = D.ProductName,
    P.UnitPrice   = D.UnitPrice,
    P.Active      = D.Active,
    P.VATPerc     = D.VATPerc,
    P.VAT         = D.VAT
FROM Product P
INNER JOIN #HoProduct D ON D.ProductCode = P.ProductCode;

-- ============================================================
-- PRICE CHANGE TRACKING: audit instrumentation only.
-- This declaration does not alter which ProductPrice rows are selected.
-- ============================================================
DECLARE @PriceChanges TABLE (
    ChangeType        VARCHAR(10),
    ProductCode       VARCHAR(20),
    DepotCode         VARCHAR(10),
    OldUnitPrice      DECIMAL(18,4),
    NewUnitPrice      DECIMAL(18,4),
    OldModifiedDate   DATETIME,
    NewModifiedDate   DATETIME,
    ModifiedBy        VARCHAR(50)
);

-- ============================================================
-- INSERT new product prices into outlet ProductPrice table
-- ============================================================
INSERT INTO ProductPrice (ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy, ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc, SDVATDiscPerc, VATCalcType)
OUTPUT 'INSERT',
       INSERTED.ProductCode,
       INSERTED.DepotCode,
       NULL,
       INSERTED.UnitPrice,
       NULL,
       INSERTED.ModifiedDate,
       INSERTED.ModifiedBy
INTO @PriceChanges
SELECT D.* FROM #HoProductPrice D
LEFT JOIN ProductPrice P ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode
WHERE P.ProductCode IS NULL OR P.DepotCode IS NULL;

-- ============================================================
-- Identify only ProductPrice rows where UnitPrice or ModifiedDate changed
-- ============================================================
SELECT D.ProductCode, D.DepotCode
INTO #ChangedPrices
FROM ProductPrice P
INNER JOIN #HoProductPrice D ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode
LEFT JOIN (SELECT * FROM ProductVfmg WHERE Active = 'Y') SQ ON P.ProductCode = SQ.ProductCode
WHERE SQ.ProductCode IS NULL
  AND (
      P.UnitPrice <> D.UnitPrice
      OR (P.UnitPrice IS NULL AND D.UnitPrice IS NOT NULL)
      OR (P.UnitPrice IS NOT NULL AND D.UnitPrice IS NULL)
      OR P.ModifiedDate <> D.ModifiedDate
      OR (P.ModifiedDate IS NULL AND D.ModifiedDate IS NOT NULL)
      OR (P.ModifiedDate IS NOT NULL AND D.ModifiedDate IS NULL)
  );

-- ============================================================
-- UPDATE only changed product prices, capturing before/after values
-- ============================================================
UPDATE P
SET P.UnitPrice    = D.UnitPrice,
    P.VATPerc      = D.VATPerc,
    P.VAT          = D.VAT,
    P.ModifiedBy   = D.ModifiedBy,
    P.ModifiedDate = D.ModifiedDate,
    P.EffectedDate = D.EffectedDate,
    P.VATDiscPerc  = D.VATDiscPerc,
    P.VATCalcType  = D.VATCalcType
OUTPUT 'UPDATE',
       INSERTED.ProductCode,
       INSERTED.DepotCode,
       DELETED.UnitPrice,
       INSERTED.UnitPrice,
       DELETED.ModifiedDate,
       INSERTED.ModifiedDate,
       INSERTED.ModifiedBy
INTO @PriceChanges
FROM ProductPrice P
INNER JOIN #HoProductPrice D ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode
INNER JOIN #ChangedPrices C ON D.ProductCode = C.ProductCode AND D.DepotCode = C.DepotCode
LEFT JOIN (SELECT * FROM ProductVfmg WHERE Active = 'Y') SQ ON P.ProductCode = SQ.ProductCode
WHERE SQ.ProductCode IS NULL;

-- ============================================================
-- INSERT new barcodes into outlet ProductBarcode table
-- ============================================================
INSERT INTO ProductBarcode (ProductCode, BarCode, CreatedBy, CreatedDate, Active)
SELECT D.* FROM #HoBarcode D
LEFT JOIN ProductBarcode P ON D.ProductCode = P.ProductCode AND D.BarCode = P.BarCode
WHERE P.ProductCode IS NULL OR P.BarCode IS NULL;

-- ============================================================
-- RETURN price change data for audit logging (read by Python via pyodbc)
-- ============================================================
SELECT 'PRICE_CHANGE_SUMMARY' AS Marker,
       COUNT(*) AS CapturedCount
FROM @PriceChanges;

SELECT 'PRICE_CHANGES' AS Marker,
       ChangeType,
       ProductCode,
       DepotCode,
       OldUnitPrice,
       NewUnitPrice,
       OldModifiedDate,
       NewModifiedDate,
       ModifiedBy
FROM @PriceChanges;

-- ============================================================
-- CLEANUP: Drop temp tables
-- ============================================================
DROP TABLE #HoProduct;
DROP TABLE #HoProductPrice;
DROP TABLE #HoBarcode;
DROP TABLE #ChangedPrices;
"""


LINKED_SERVER_CHECK_SQL = "SELECT COUNT(*) FROM sys.servers WHERE name = ?"

LINKED_SERVER_CREATE_TEMPLATE = """
    EXEC master.dbo.sp_addlinkedserver
        @server    = N'{linked_server_name}',
        @srvproduct= N'',
        @provider  = N'SQLNCLI',
        @datasrc   = N'{linked_server_name}';
"""
