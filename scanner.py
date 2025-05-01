#!/usr/bin/env python3
import os
import json
import tempfile
import subprocess
import logging
import base64
import datetime
from urllib.parse import unquote

from flask import Flask, request, abort
from google.cloud import storage, pubsub_v1

# === Configuration ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
RESULTS_TOPIC = os.getenv("RESULTS_TOPIC", "file-scan-results")
PRODUCTION = os.getenv("PRODUCTION_BUCKET", "fleet-ivy-432307-m0-production")
QUARANTINE = os.getenv("QUARANTINE_BUCKET", "fleet-ivy-432307-m0-quarantine")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")

# Initialize logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger("file-scanner")

# Initialize clients
storage_client = storage.Client()
publisher = pubsub_v1.PublisherClient()

app = Flask(__name__)

def _extract_event(raw: dict) -> dict:
    """Normalize incoming Pub/Sub message into a consistent format."""
    # Handle direct Cloud Storage notification
    if 'bucket' in raw and 'name' in raw:
        return {
            "bucket": raw["bucket"],
            "name": unquote(raw["name"]),
            "contentType": raw.get("contentType", "<none>"),
            "size": int(raw.get("size", 0)),
            "resourceState": "exists",
        }

    # Handle wrapped Pub/Sub message
    msg = raw.get("message", {}) or {}
    attrs = msg.get("attributes", {}) or {}

    # Decode data payload (handles both base64 and plain JSON)
    data = {}
    if "data" in msg:
        try:
            decoded = base64.b64decode(msg["data"]).decode("utf-8")
            data = json.loads(decoded)
        except:
            try:
                data = json.loads(msg["data"])
            except:
                pass

    return {
        "bucket": attrs.get("bucketId") or data.get("bucket"),
        "name": unquote(attrs.get("objectId", "") or data.get("name", "")),
        "contentType": attrs.get("contentType") or data.get("contentType", "<none>"),
        "size": int(attrs.get("size", "0") or data.get("size", 0)),
        "resourceState": "not_exists" if attrs.get("eventType") == "OBJECT_DELETE" else "exists",
    }

def scan_file(file_path: str) -> tuple:
    """Scan a file using clamd and return (status, output)."""
    try:
        result = subprocess.run(
            ["clamdscan", "--fdpass", "--stream", "--no-summary", file_path],
            capture_output=True,
            text=True,
            timeout=240
        )

        if result.returncode == 0:
            return ("SCANNED_OK", result.stdout.strip())
        elif result.returncode == 1:
            return ("INFECTED", result.stdout.strip())
        else:
            return ("SCAN_ERROR", result.stderr.strip())

    except subprocess.TimeoutExpired:
        return ("TIMEOUT", "Scan timed out after 240 seconds")
    except Exception as e:
        return ("ERROR", str(e))

def move_file(src_bucket_name: str, obj_name: str, status: str) -> bool:
    """Move file to appropriate bucket based on scan status."""
    dst_bucket_name = PRODUCTION if status == "SCANNED_OK" else QUARANTINE

    try:
        src_bucket = storage_client.bucket(src_bucket_name)
        dst_bucket = storage_client.bucket(dst_bucket_name)
        src_blob = src_bucket.blob(obj_name)

        logger.info(
            "Moving file: gs://%s/%s → gs://%s/%s (Status: %s)",
            src_bucket_name, obj_name, dst_bucket_name, obj_name, status
        )

        # Perform the copy and delete operations
        dst_bucket.copy_blob(src_blob, dst_bucket, obj_name)
        src_blob.delete()

        logger.info("File move completed successfully")
        return True

    except Exception as e:
        logger.error("Failed to move file: %s", str(e))
        return False

def publish_result(obj_name: str, status: str) -> bool:
    """Publish scan result to Pub/Sub."""
    try:
        if not PROJECT_ID:
            raise ValueError("GOOGLE_CLOUD_PROJECT environment variable not set")

        topic_path = publisher.topic_path(PROJECT_ID, RESULTS_TOPIC)

        future = publisher.publish(
            topic_path,
            data=b"",
            fileId=obj_name.split("/")[-1],
            status=status,
            scannedAt=datetime.datetime.utcnow().isoformat(),
            bucket=PRODUCTION if status == "SCANNED_OK" else QUARANTINE
        )

        future.result()  # Wait for publish to complete
        logger.info("Published scan result for %s: %s", obj_name, status)
        return True

    except Exception as e:
        logger.error("Failed to publish scan result: %s", str(e))
        return False

def process_event(raw_evt: dict):
    """Process a file upload event."""
    evt = _extract_event(raw_evt)

    # Skip deleted objects
    if evt.get("resourceState") != "exists":
        logger.info("Skipping non-existent file: %s", evt.get("name"))
        return

    bucket_name = evt["bucket"]
    obj_name    = evt["name"]

    # Fetch authoritative metadata from GCS
    src_bucket          = storage_client.bucket(bucket_name)
    src_blob            = src_bucket.blob(obj_name)
    src_blob.reload()
    actual_size         = src_blob.size or 0
    actual_contentType  = src_blob.content_type or "<none>"

    logger.info(
        "Processing file: gs://%s/%s (%s, %d bytes)",
        bucket_name, obj_name, actual_contentType, actual_size
    )

    status = "QUARANTINED"  # Default to quarantine

    # Only scan supported file types under 50MB
    if actual_contentType in ("image/png", "image/jpeg", "application/pdf") \
            and actual_size <= 50_000_000:
        try:
            with tempfile.NamedTemporaryFile() as tmp_file:
                # Download file to temporary location
                src_blob.download_to_filename(tmp_file.name)
                logger.debug("Downloaded file to temporary location: %s", tmp_file.name)

                # Scan the file
                scan_status, scan_output = scan_file(tmp_file.name)
                logger.debug("Scan result: %s - %s", scan_status, scan_output)

                if scan_status == "SCANNED_OK":
                    status = "SCANNED_OK"
                elif scan_status == "INFECTED":
                    logger.warning("Virus detected in %s: %s", obj_name, scan_output)
                else:
                    logger.error("Scan failed for %s: %s", obj_name, scan_output)

        except Exception as e:
            logger.error("Error processing %s: %s", obj_name, str(e))
    else:
        logger.info(
            "Auto-quarantining due to file type/size: %s (%d bytes)",
            actual_contentType, actual_size
        )

    # Move file and publish result
    if move_file(bucket_name, obj_name, status):
        publish_result(obj_name, status)

@app.route("/", methods=["POST"])
def index():
    """Handle HTTP requests from Pub/Sub."""
    envelope = request.get_json(silent=True)
    if not envelope:
        logger.error("No JSON payload received")
        abort(400)

    # Process different message formats
    if "message" in envelope:  # Pub/Sub push
        process_event(envelope)
    elif isinstance(envelope, dict):  # Direct JSON
        process_event({"message": {"data": json.dumps(envelope)}})
    else:
        logger.error("Unrecognized payload format")
        abort(400)

    return "", 204

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))