FROM python:3.11-slim

# 1) Install dependencies with cleanup
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      clamav clamav-daemon supervisor netcat-openbsd curl && \
    rm -rf /var/lib/apt/lists/* && \
    # Create required directories with proper permissions
    mkdir -p /var/run/clamav && \
    chown clamav:clamav /var/run/clamav && \
    chmod 750 /var/run/clamav && \
    mkdir -p /var/lib/clamav && \
    chown clamav:clamav /var/lib/clamav && \
    # Create app user
    useradd --create-home --shell /bin/bash appuser

# 2) Configure ClamAV (use TCP only)
COPY clamd.conf /etc/clamav/clamd.conf
RUN chown clamav:clamav /etc/clamav/clamd.conf

WORKDIR /app

# 3) Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) Copy application files
COPY scanner.py supervisord.conf entrypoint.sh ./
RUN chmod +x entrypoint.sh && \
    chown -R appuser:appuser /app

# 5) Healthcheck configuration (with longer timeout)
HEALTHCHECK --interval=30s --timeout=20s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# 6) Runtime configuration
ENV PORT=8080
EXPOSE 8080
USER appuser
ENTRYPOINT ["./entrypoint.sh"]