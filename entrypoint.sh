#!/bin/bash
set -e

# Start ClamAV daemon
echo "Starting ClamAV daemon..."
clamd --config-file=/etc/clamav/clamd.conf &

# Wait for ClamAV to be ready on TCP port
echo "Waiting for ClamAV to be ready on port 3310..."
timeout=30
while ! nc -z localhost 3310; do
    sleep 1
    ((timeout--))
    if [ $timeout -le 0 ]; then
        echo "Timeout waiting for ClamAV"
        exit 1
    fi
done
echo "ClamAV is ready"

# Start supervisord
echo "Starting supervisord..."
exec supervisord -c ./supervisord.conf