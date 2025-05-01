#!/bin/bash
set -e

# Create required directories on startup (Cloud Run has ephemeral filesystem)
mkdir -p /var/run/clamav
chown clamav:clamav /var/run/clamav
chmod 750 /var/run/clamav

# Start ClamAV in background with logging
echo "Starting ClamAV daemon..."
clamd --config-file=/etc/clamav/clamd.conf &
clamd_pid=$!

# Function to check if port is listening
is_port_listening() {
    netstat -tuln | grep -q ":3310.*LISTEN"
}

# Wait for ClamAV to be ready on TCP port
echo "Waiting for ClamAV to be ready on port 3310..."
timeout=60
while ! is_port_listening; do
    sleep 1
    ((timeout--))
    if [ $timeout -le 0 ]; then
        echo "Timeout waiting for ClamAV to start"
        exit 1
    fi
    # Check if clamd process is still running
    if ! kill -0 $clamd_pid 2>/dev/null; then
        echo "ClamAV process died unexpectedly"
        exit 1
    fi
done
echo "ClamAV is ready"

# Start supervisord in foreground
echo "Starting supervisord..."
exec supervisord -c ./supervisord.conf