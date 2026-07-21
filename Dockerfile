FROM python:3.11-slim-bookworm

# Install system dependencies for pyodbc and Microsoft ODBC Driver 17
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 \
    # Fix SSL Legacy Sigalg error for older SQL Servers \
    && sed -i 's/\[openssl_init\]/\[openssl_init\]\nssl_conf = ssl_sect\nproviders = provider_sect/g' /etc/ssl/openssl.cnf \
    && printf "\n[provider_sect]\ndefault = default_sect\nlegacy = legacy_sect\n\n[default_sect]\nactivate = 1\n\n[legacy_sect]\nactivate = 1\n\n[ssl_sect]\nsystem_default = system_default_sect\n\n[system_default_sect]\nCipherString = DEFAULT@SECLEVEL=0\nSignatureAlgorithms = RSA+SHA1:ECDSA+SHA1:RSA+MD5-SHA1\n" >> /etc/ssl/openssl.cnf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ ./app/

# Create logs directory
RUN mkdir -p /app/logs

EXPOSE 8000

# Single worker required for in-memory state consistency
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
