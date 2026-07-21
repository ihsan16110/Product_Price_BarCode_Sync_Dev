"""
Pytest configuration: sets mock environment variables so the Settings singleton
can be imported without an actual .env file.
"""

import os

# Set required env vars before any module imports the Settings singleton
# These values are only used for unit tests — they are never used against real DBs
_required_vars = {
    "SOURCE_SERVER": "test-central-server",
    "SOURCE_DATABASE": "TestCentralDB",
    "SOURCE_USER": "test_user",
    "SOURCE_PASSWORD": "test_pass",
    "LOG_SERVER": "test-log-server",
    "LOG_DATABASE": "ProdPriceSync",
    "LOG_USER": "sa",
    "LOG_PASSWORD": "flexiload",
    "CENTRAL_DB": "CentralDB",
    "CENTRAL_LINKED_SERVER_NAME": "CUSTOMER_INFO_CENTRAL",
    "LOCAL_DB": "LocalDB",
    "OUTLET_DB_USER": "sa",
    "OUTLET_DB_PASSWORD": "outlet_pass",
}

for _key, _value in _required_vars.items():
    os.environ.setdefault(_key, _value)
