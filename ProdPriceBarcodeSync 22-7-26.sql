USE EPS


CREATE TABLE #LinkedServers
(
    SRV_NAME VARCHAR(200),
    SRV_PROVIDERNAME VARCHAR(200),
    SRV_PRODUCT VARCHAR(200),
    SRV_DATASOURCE VARCHAR(200),
    SRV_PROVIDERSTRING VARCHAR(200),
    SRV_LOCATION VARCHAR(200),
    SRV_CAT VARCHAR(200)
)

PRINT 'Inserted #LinkedServer'

INSERT INTO #LinkedServers
EXEC SP_LinkedServers

DECLARE @vFoundLinkServer INT,
        @vDepotCode VARCHAR(6),
        @vOpenQuery VARCHAR(8000)

SET @vFoundLinkServer = 0 
SET @vOpenQuery = ''

SELECT @vFoundLinkServer = COUNT(*) FROM #LinkedServers WHERE SRV_NAME='192.168.11.200'
IF @vFoundLinkServer = 0
BEGIN
    EXEC SP_ADDLinkedServer '192.168.11.200'
END

SELECT @vDepotCode = DepotCode FROM Depot WHERE ActiveDepot='Y'

-- =============================================
-- 1. PULL PRODUCT DATA FROM HO
-- =============================================
SET @vOpenQuery ='SELECT ProductCode,ProductName,BarCode,PackSize,SubCategoryID,BrandID,ManufacturerID,SubCategoryMD,CostPrice,UnitPrice,VATID,VATPerc,VAT,MRP,MinStock,MaxStock,MinOrderQty,Active,DiscountStatus,ExpiryManagement,CreatedBy,CreatedDate,ModifiedBy,ModifiedDate,PosType,''Y'' AllowNegative
FROM OPENQUERY([192.168.11.200],''SELECT ProductCode,ProductName,BarCode,PackSize,SubCategoryID,BrandID,ManufacturerID,SubCategoryMD,CostPrice,UnitPrice,VATID,VATPerc,VAT,MRP,MinStock,MaxStock,MinOrderQty,Active,DiscountStatus,ExpiryManagement,CreatedBy,CreatedDate,ModifiedBy,ModifiedDate,PosType
FROM EPSMirror.DBO.RepProduct D '')'

PRINT 'Inserted #HoProduct'
SELECT TOP 0 ProductCode,ProductName,BarCode,PackSize,SubCategoryID,BrandID,ManufacturerID,SubCategoryMD,CostPrice,UnitPrice,VATID,VATPerc,VAT,MRP,MinStock,MaxStock,MinOrderQty,Active,DiscountStatus,ExpiryManagement,CreatedBy,CreatedDate,ModifiedBy,ModifiedDate,PosType,'Y' AllowNegative INTO #HoProduct FROM Product
INSERT INTO #HoProduct EXEC (@vOpenQuery)

-- =============================================
-- 2. PULL PRICE DATA FROM HO (WITH Price FLAG)
-- =============================================
SET @vOpenQuery ='SELECT ProductCode,DepotCode,UnitPrice,VATPerc,VAT,MRP,ModifiedBy,ModifiedDate,VATDiscount,EffectedDate,VATDiscPerc,SDVATPerc,SDVATDiscPerc,VATCalcType,Price
FROM OPENQUERY([192.168.11.200],
''SELECT ProductCode,DepotCode,UnitPrice,VATPerc,VAT,MRP,ModifiedBy,ModifiedDate,VATDiscount,EffectedDate,VATDiscPerc,SDVATPerc,SDVATDiscPerc,VATCalcType,Price
FROM EPSMirror.DBO.RepProductPrice D WHERE SyncStatus=''''N'''' AND DepotCode='''''+@vDepotCode+''''' '')'

PRINT 'Inserted #HoProductPrice'
SELECT TOP 0 ProductCode,DepotCode,UnitPrice,VATPerc,VAT,MRP,ModifiedBy,ModifiedDate,VATDiscount,EffectedDate,VATDiscPerc,SDVATPerc,SDVATDiscPerc,VATCalcType,CAST('N' AS VARCHAR(1)) AS Price INTO #HoProductPrice FROM ProductPrice
INSERT INTO #HoProductPrice EXEC (@vOpenQuery)

-- =============================================
-- 3. PULL DELETE MARKERS FROM HO
-- =============================================
SET @vOpenQuery = 'SELECT ProductCode, DepotCode 
FROM OPENQUERY([192.168.11.200],
''SELECT ProductCode, DepotCode 
FROM EPSMirror.DBO.RepProductPrice D WHERE SyncStatus=''''N'''' AND SyncType=''''D'''' AND DepotCode='''''+@vDepotCode+''''' '')'

PRINT 'Inserted #HoProductPriceDelete'
SELECT TOP 0 ProductCode, DepotCode INTO #HoProductPriceDelete FROM ProductPrice
INSERT INTO #HoProductPriceDelete EXEC (@vOpenQuery)

-- =============================================
-- 4. PULL BARCODE DATA FROM HO
-- =============================================
SET @vOpenQuery = 'SELECT ProductCode,BarCode,CreatedBy,CreatedDate,Active FROM OPENQUERY([192.168.11.200],
''SELECT ProductCode,BarCode,CreatedBy,CreatedDate,Active FROM EPSMirror.DBO.RepProductBarcode D '')'
PRINT 'Inserted #HoBarcode'
SELECT TOP 0 * INTO #HoBarcode FROM ProductBarcode
INSERT INTO #HoBarcode EXEC (@vOpenQuery)

-- =============================================
-- 5. SYNC PRODUCT TABLE
-- =============================================
PRINT 'Inserting In Outlet Product Table'
INSERT INTO Product(ProductCode,ProductName,BarCode,PackSize,SubCategoryID,BrandID,ManufacturerID,SubCategoryMD,CostPrice,UnitPrice,VATID,VATPerc,VAT,MRP,MinStock,MaxStock,MinOrderQty,Active,DiscountStatus,ExpiryManagement,CreatedBy,CreatedDate,ModifiedBy,ModifiedDate,PosType,AllowNegative)
SELECT D.* FROM #HoProduct D LEFT JOIN Product P ON D.ProductCode= P.ProductCode WHERE P.ProductCode IS NULL

PRINT 'Updated In Outlet Product Table'
UPDATE Product 
SET ProductName=D.ProductName, 
	UnitPrice= D.UnitPrice, 
	Active= D.Active, 
	VATPerc=D.VATPerc, 
	VAT=D.VAT 
FROM Product P 
INNER JOIN #HoProduct D ON D.ProductCode= P.ProductCode 

-- =============================================
-- 6. SYNC PRODUCT PRICE TABLE (WITH Price FLAG LOGIC)
-- =============================================
PRINT 'Deleting ProductPrice records marked with SyncType = ''D'' locally'
DELETE P
FROM ProductPrice P
INNER JOIN #HoProductPriceDelete D ON P.ProductCode = D.ProductCode AND P.DepotCode = D.DepotCode

PRINT 'Inserted In Outlet ProductPrice Table'
INSERT INTO ProductPrice (ProductCode, DepotCode, UnitPrice, VATPerc, VAT, MRP, ModifiedBy, ModifiedDate, VATDiscount, EffectedDate, VATDiscPerc, SDVATPerc, SDVATDiscPerc, VATCalcType)
SELECT 
    D.ProductCode, 
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
LEFT JOIN ProductPrice P ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode 
WHERE P.ProductCode IS NULL OR P.DepotCode IS NULL

PRINT 'Updated In Outlet ProductPrice Table'
UPDATE ProductPrice 
SET 
    UnitPrice = CASE WHEN D.Price = 'Y' THEN D.UnitPrice ELSE P.UnitPrice END, 
    VATPerc = D.VATPerc, 
	VAT = D.VAT,
    ModifiedBy = D.ModifiedBy, 
    ModifiedDate = D.ModifiedDate, 
    EffectedDate = D.EffectedDate, 
    VATDiscPerc = D.VATDiscPerc, 
    VATCalcType = D.VATCalcType 
FROM ProductPrice P 
INNER JOIN #HoProductPrice D ON D.ProductCode = P.ProductCode AND D.DepotCode = P.DepotCode

-- =============================================
-- 7. SYNC BARCODE TABLE
-- =============================================
PRINT 'Inserted into Outlet ProductBarcode Table'
INSERT INTO ProductBarcode 
SELECT D.* FROM #HoBarcode D 
LEFT JOIN ProductBarcode P ON D.ProductCode = P.ProductCode AND D.BarCode = P.BarCode 
WHERE P.ProductCode IS NULL OR P.BarCode IS NULL

-- =============================================
-- 8. RETURN DATA FOR HO UPDATE (via Python)
-- =============================================
-- This returns the data that was synced from HO
-- Python will use this to update SyncStatus on HO
SELECT 
    ProductCode,
    DepotCode,
    UnitPrice,
    VATPerc,
    VAT,
    MRP,
    ModifiedBy,
    ModifiedDate,
    VATDiscount,
    EffectedDate,
    VATDiscPerc,
    SDVATPerc,
    SDVATDiscPerc,
    VATCalcType,
    @vDepotCode AS DepotCodeParam
FROM #HoProductPrice

-- =============================================
-- 9. CLEANUP TEMP TABLES
-- =============================================
PRINT 'dropping temp tables'

DROP TABLE #LinkedServers
DROP TABLE #HoProductPriceDelete
DROP TABLE #HoProduct
DROP TABLE #HoProductPrice
DROP TABLE #HoBarcode