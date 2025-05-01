#!/bin/bash
# entrypoint.sh
set -e

echo "Starting ClamAV daemon in background..."
# Launch clamd as a daemon (background) so this script can continue
clamd --config-file=/etc/clamav/clamd.conf &

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

echo "Starting supervisord (launches only the Flask app)..."
exec supervisord -c ./supervisord.conf
