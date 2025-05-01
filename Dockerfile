# Dockerfile

FROM python:3.11-slim

# 1) Install system deps: ClamAV, Supervisor, netcat-openbsd, curl; add appuser; patch clamd.conf
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      clamav clamav-daemon supervisor netcat-openbsd curl && \
    freshclam && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --shell /bin/bash appuser && \
    sed -i \
      -e 's|^LogFile /var/log/clamav/clamd.log|LogFile /dev/stdout|' \
      -e 's|^#*LogFileLocking .*|LogFileLocking false|' \
    /etc/clamav/clamd.conf

WORKDIR /app

# 2) Install Python deps + Gunicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# 3) Copy application code & configs, set permissions
COPY scanner.py supervisord.conf entrypoint.sh ./
RUN chmod +x entrypoint.sh && \
    chown -R appuser:appuser /app

# 4) Healthcheck via curl
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# 5) Runtime
ENV PORT=8080
ENTRYPOINT ["./entrypoint.sh"]
