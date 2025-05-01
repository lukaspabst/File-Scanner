# Dockerfile

FROM python:3.11-slim

# Install ClamAV + daemon, Supervisor, then redirect ClamAV logs to stdout
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      clamav clamav-daemon supervisor && \
    freshclam && \
    rm -rf /var/lib/apt/lists/* && \
    sed -i \
      -e 's|^LogFile /var/log/clamav/clamd.log|LogFile /dev/stdout|' \
      -e 's|^#*LogFileLocking .*|LogFileLocking false|' \
    /etc/clamav/clamd.conf

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scanner.py supervisord.conf entrypoint.sh ./
RUN chmod +x entrypoint.sh

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

ENV PORT=8080
ENTRYPOINT ["./entrypoint.sh"]
