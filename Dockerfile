# Start from a slim Debian that supports ClamAV
FROM debian:bookworm-slim

# Install ClamAV + Python
RUN apt-get update && \
    apt-get install -y clamav clamav-daemon python3 python3-pip && \
    freshclam && \
    rm -rf /var/lib/apt/lists/*

# Copy your Python code
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY scanner.py .

# Use a non-root user for extra safety (optional)
RUN useradd --create-home scanner
USER scanner

# Entry point for Cloud Run (Pub/Sub push will POST JSON here)
CMD ["python3", "scanner.py"]
