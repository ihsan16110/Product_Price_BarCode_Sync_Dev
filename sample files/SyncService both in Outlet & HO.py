import os
import pyodbc
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Load environment variables from .env file
load_dotenv()

db_username = os.getenv("Outlet_DB_USERNAME")
db_password = os.getenv("Outlet_DB_PASSWORD")

# All values are read directly from .env file
HO_SERVER = os.getenv("HO_SERVER")
HO_DATABASE = os.getenv("HO_DATABASE")
HO_USERNAME = os.getenv("HO_DB_USERNAME")
HO_PASSWORD = os.getenv("HO_DB_PASSWORD")

# =============================================
# VALIDATE REQUIRED ENVIRONMENT VARIABLES
# =============================================
required_vars = {
    "Outlet_DB_USERNAME": db_username,
    "Outlet_DB_PASSWORD": db_password,
    "HO_DB_USERNAME": HO_USERNAME,
    "HO_DB_PASSWORD": HO_PASSWORD,
    "HO_SERVER": HO_SERVER,
    "HO_DATABASE": HO_DATABASE
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    print(f"❌ ERROR: Missing required environment variables: {', '.join(missing_vars)}")
    print("Please check your .env file")
    exit(1)

print("✅ Environment variables loaded successfully")
print(f"   Outlet DB: Using credentials for {db_username}")
print(f"   HO DB: {HO_SERVER}/{HO_DATABASE} using {HO_USERNAME}")

# =============================================
# READ SERVER LIST FROM EXCEL
# =============================================
#excel_file = 'All Active Server - 969.xlsx'
excel_file = 'Failed List.xlsx'
servers_df = pd.read_excel(excel_file)

# Initialize status log list
status_log = []

# SQL file to execute on outlets
sqlFile = 'PriceSync.sql'

# =============================================
# FUNCTION: UPDATE HEAD OFFICE DATABASE
# =============================================
def update_ho_database(depot_data, depot_code, outlet_code):
    """
    Update HO database - ONLY mark records as synced
    The SQL already handled the conditional price updates
    """
    try:
        ho_conn_str = (
            f'DRIVER={{ODBC Driver 17 for SQL Server}};'
            f'SERVER={HO_SERVER};'
            f'DATABASE={HO_DATABASE};'
            f'UID={HO_USERNAME};'
            f'PWD={HO_PASSWORD}'
        )

        print(f"   🏠 Connecting to HO Server: {HO_SERVER}/{HO_DATABASE} for {outlet_code}")

        with pyodbc.connect(
            ho_conn_str,
            timeout=60,
            autocommit=True
        ) as conn:
            cursor = conn.cursor()

            # ✅ CORRECT: ONLY Update SyncStatus and SentTime
            # ❌ DO NOT update UnitPrice, VATPerc, etc.
            
            # Get unique product codes
            product_codes = [str(row['ProductCode']) for _, row in depot_data.iterrows()]
            
            if not product_codes:
                return {
                    "status": "no_data",
                    "message": "No product codes to sync",
                    "records_updated": 0
                }

            # Build the UPDATE query with IN clause
            placeholders = ','.join(['?'] * len(product_codes))
            
            update_sql = f"""
            UPDATE RepProductPrice 
            SET 
                SyncStatus = 'Y',
                SentTime = GETDATE()
            WHERE SyncStatus = 'N' 
                AND DepotCode = ?
                AND ProductCode IN ({placeholders})
            """
            
            # Prepare parameters
            params = [depot_code] + product_codes
            
            # Execute the update
            cursor.execute(update_sql, params)
            update_count = cursor.rowcount

            return {
                "status": "success",
                "message": f"✅ Marked {update_count} records as synced in HO database",
                "records_updated": update_count
            }

    except pyodbc.InterfaceError as e:
        error_msg = f"Interface error connecting to HO: {str(e)}"
        print(f"   ❌ {error_msg}")
        return {
            "status": "failed",
            "message": error_msg,
            "records_updated": 0
        }
    
    except pyodbc.OperationalError as e:
        error_msg = f"Operational error connecting to HO: {str(e)}"
        print(f"   ❌ {error_msg}")
        return {
            "status": "failed",
            "message": error_msg,
            "records_updated": 0
        }
    
    except Exception as e:
        error_msg = f"Error updating HO database: {str(e)}"
        print(f"   ❌ {error_msg}")
        return {
            "status": "failed",
            "message": error_msg,
            "records_updated": 0
        }

# =============================================
# FUNCTION: PROCESS EACH SERVER
# =============================================
def process_server(outlet_code, server_ip):
    now = datetime.now()
    checking_time = (
        f"{now.month}/{now.day}/{now.year} "
        f"{now.hour % 12 or 12}:{now.minute:02}"
    )

    conn_str = (
        f'DRIVER={{ODBC Driver 17 for SQL Server}};'
        f'SERVER={server_ip};'
        f'UID={db_username};'
        f'PWD={db_password}'
    )

    result = {
        "OutletCode": outlet_code,
        "IP": server_ip,
        "Checking Time": checking_time,
        "Status": "",
        "Message": "",
        "Duration (seconds)": 0,
        "RecordsSynced": 0,
        "HOSyncStatus": "",
        "HOSyncMessage": ""
    }

    start_time = time.time()

    try:
        print(f"🔄 Processing: {outlet_code} at IP: {server_ip}")

        with pyodbc.connect(
            conn_str,
            timeout=60,
            autocommit=True
        ) as conn:

            cursor = conn.cursor()

            with open(sqlFile, 'r', encoding='utf-8-sig') as file:
                sql_query = file.read()

            cursor.execute(sql_query)

            row_count_total = 0
            depot_data = None
            depot_code = None

            while True:
                if cursor.description:
                    columns = [col[0] for col in cursor.description]
                    if 'ProductCode' in columns and 'DepotCode' in columns:
                        # This is the data we need for HO update
                        rows = cursor.fetchall()
                        if rows:
                            depot_data = pd.DataFrame.from_records(
                                rows, 
                                columns=columns
                            )
                            if 'DepotCodeParam' in columns:
                                depot_code = depot_data['DepotCodeParam'].iloc[0]
                            elif 'DepotCode' in columns:
                                depot_code = depot_data['DepotCode'].iloc[0]
                            
                            print(f"   📊 Retrieved {len(depot_data)} records for HO sync")
                else:
                    # Regular row count
                    rows_affected = cursor.rowcount
                    if rows_affected != -1:
                        row_count_total += rows_affected
                        log_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        log_message = (
                            f"{log_time} - "
                            f"{sqlFile} - "
                            f"{outlet_code} - "
                            f"IP: {server_ip} - "
                            f"({rows_affected} row(s) affected)"
                        )
                        print(f"   📊 {log_message}")

                        with open("query_log.txt", "a", encoding="utf-8") as log_file:
                            log_file.write(log_message + "\n")

                if not cursor.nextset():
                    break

            duration = round(time.time() - start_time, 2)
            
            # Outlet execution successful
            result["Status"] = "Success"
            result["Message"] = f"Query executed successfully. Total rows affected: {row_count_total}"
            result["Duration (seconds)"] = duration
            
            # ✅ ONLY UPDATE HO IF OUTLET EXECUTION WAS SUCCESSFUL
            if depot_data is not None and not depot_data.empty:
                print(f"   🔄 Outlet successful, updating HO database for {outlet_code}")
                ho_result = update_ho_database(depot_data, depot_code, outlet_code)
                result["HOSyncStatus"] = ho_result["status"]
                result["HOSyncMessage"] = ho_result["message"]
                result["RecordsSynced"] = ho_result.get("records_updated", 0)
                
                if ho_result["status"] == "success":
                    print(f"✅ SUCCESS: {outlet_code} completed in {duration} seconds. HO Updated: {result['RecordsSynced']} records")
                else:
                    print(f"⚠️ PARTIAL: {outlet_code} outlet succeeded but HO update failed: {ho_result['message']}")
            else:
                # No data to sync to HO
                result["HOSyncStatus"] = "no_data"
                result["HOSyncMessage"] = "No records to sync to HO"
                print(f"✅ SUCCESS: {outlet_code} completed in {duration} seconds. No data to sync to HO")

    except pyodbc.InterfaceError as e:
        error_message = f"Could not connect to {server_ip} - {e}"
        print(f"❌ OFFLINE: {outlet_code} - {error_message}")

        with open("query_log.txt", "a", encoding="utf-8") as log_file:
            log_file.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - "
                f"{outlet_code} - "
                f"IP: {server_ip} - "
                f"{error_message}\n"
            )

        result["Status"] = "Offline"
        result["Message"] = f"Could not connect to {server_ip}: {e}"
        result["Duration (seconds)"] = round(time.time() - start_time, 2)
        result["HOSyncStatus"] = "not_attempted"
        result["HOSyncMessage"] = "HO update not attempted - outlet is offline"

    except pyodbc.OperationalError as e:
        error_message = f"Operational error connecting to {server_ip} - {e}"
        print(f"❌ OFFLINE: {outlet_code} - {error_message}")

        with open("query_log.txt", "a", encoding="utf-8") as log_file:
            log_file.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - "
                f"{outlet_code} - "
                f"IP: {server_ip} - "
                f"{error_message}\n"
            )

        result["Status"] = "Offline"
        result["Message"] = f"Operational error connecting to {server_ip}: {e}"
        result["Duration (seconds)"] = round(time.time() - start_time, 2)
        result["HOSyncStatus"] = "not_attempted"
        result["HOSyncMessage"] = "HO update not attempted - outlet is offline"

    except Exception as e:
        error_message = f"Error executing query on {server_ip} - {e}"
        print(f"❌ FAILED: {outlet_code} - {error_message}")

        with open("query_log.txt", "a", encoding="utf-8") as log_file:
            log_file.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - "
                f"{outlet_code} - "
                f"IP: {server_ip} - "
                f"{error_message}\n"
            )

        result["Status"] = "Failed"
        result["Message"] = f"Error executing query on {server_ip}: {e}"
        result["Duration (seconds)"] = round(time.time() - start_time, 2)
        result["HOSyncStatus"] = "not_attempted"
        result["HOSyncMessage"] = "HO update not attempted - outlet execution failed"

    return result

# =============================================
# MAIN EXECUTION
# =============================================
print("🚀 Starting parallel execution...")
print(f"📁 Processing {len(servers_df)} servers from {excel_file}")
print(f"📄 SQL Script: {sqlFile}")
print(f"🏠 HO Server: {HO_SERVER}/{HO_DATABASE}")
print(f"🔐 Using HO Credentials: {HO_USERNAME}")
print("=" * 80)

with ThreadPoolExecutor(max_workers=20) as executor:
    futures = []

    for index, row in servers_df.iterrows():
        outlet_code = row['OutletCode']
        server_ip = row['IP']

        futures.append(
            executor.submit(
                process_server,
                outlet_code,
                server_ip
            )
        )

    for future in as_completed(futures):
        result = future.result()
        status_log.append(result)

# =============================================
# SAVE EXECUTION SUMMARY
# =============================================
finished_time = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

output_filename = (
    f"{sqlFile.replace('.sql', '')}_on_{excel_file.replace('.xlsx', '')}_"
    f"Execution_Status_{finished_time}.csv"
)

# Create DataFrame with all columns including HO status
status_df = pd.DataFrame(status_log)

# Ensure all expected columns exist
expected_columns = [
    "OutletCode",
    "IP",
    "Checking Time",
    "Status",
    "Message",
    "Duration (seconds)",
    "RecordsSynced",
    "HOSyncStatus",
    "HOSyncMessage"
]

for col in expected_columns:
    if col not in status_df.columns:
        status_df[col] = ""

# Reorder columns
status_df = status_df[expected_columns]

# Add summary statistics
summary = {
    "Total": len(status_df),
    "Success": len(status_df[status_df['Status'] == 'Success']),
    "Offline": len(status_df[status_df['Status'] == 'Offline']),
    "Failed": len(status_df[status_df['Status'] == 'Failed']),
    "HO_Success": len(status_df[status_df['HOSyncStatus'] == 'success']),
    "HO_Failed": len(status_df[status_df['HOSyncStatus'] == 'failed']),
    "HO_Not_Attempted": len(status_df[status_df['HOSyncStatus'] == 'not_attempted']),
    "HO_No_Data": len(status_df[status_df['HOSyncStatus'] == 'no_data'])
}

status_df.to_csv(output_filename, index=False)

print("=" * 80)
print("📊 EXECUTION SUMMARY")
print(f"   Total Servers: {summary['Total']}")
print(f"   ✅ Outlet Success: {summary['Success']}")
print(f"   ❌ Offline: {summary['Offline']}")
print(f"   ❌ Failed: {summary['Failed']}")
print(f"   🏠 HO Update Success: {summary['HO_Success']}")
print(f"   🏠 HO Update Failed: {summary['HO_Failed']}")
print(f"   ⏭️  HO Not Attempted (Outlet Failed): {summary['HO_Not_Attempted']}")
print(f"   📭 HO No Data to Sync: {summary['HO_No_Data']}")
print(f"\n📁 Detailed status written to: {output_filename}")
print("✅ Execution completed!")