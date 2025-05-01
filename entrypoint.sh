# entrypoint.sh
#!/bin/bash
set -e

echo "Starting ClamAV daemon..."
clamd --config-file=/etc/clamav/clamd.conf || {
    echo "Failed to start ClamAV daemon"
    exit 1
}

echo "Waiting for ClamAV to be ready..."
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

echo "Starting supervisord..."
exec supervisord -c ./supervisord.conf
