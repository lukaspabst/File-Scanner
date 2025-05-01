# Dockerfile

FROM python:3.11-slim

# Install ClamAV + daemon, Supervisor
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      clamav clamav-daemon supervisor && \
    freshclam && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scanner.py supervisord.conf entrypoint.sh ./
RUN chmod +x entrypoint.sh
# Add health check for Gunicorn
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Set PORT environment variable
ENV PORT=8080

# Run both clamd and your app under supervisor
ENTRYPOINT ["./entrypoint.sh"]
