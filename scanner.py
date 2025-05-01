#!/usr/bin/env python3
import os
import json
import tempfile
import subprocess
import logging
import base64
from urllib.parse import unquote

from flask import Flask, request, abort
from google.cloud import storage, pubsub_v1

# === Configuration ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
RESULTS_TOPIC = os.getenv("RESULTS_TOPIC", "scan-results-topic")
PRODUCTION = os.getenv("PRODUCTION_BUCKET", "fleet-ivy-432307-m0-scanned")
QUARANTINE = os.getenv("QUARANTINE_BUCKET", "fleet-ivy-432307-m0-quarantine")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("file-scanner")

storage_client = storage.Client()
publisher = pubsub_v1.PublisherClient()

app = Flask(__name__)

def _extract_event(raw: dict) -> dict:
    """
    Normalize incoming Pub/Sub pull or push into a dict with:
      bucket, name, contentType, size, resourceState
    Handles both Cloud Storage v1 and v2 notification formats
    """
    # Handle direct Cloud Storage notification format (v2)
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

    # Try to decode data payload (base64 encoded in Pub/Sub)
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

    # Merge attributes and data with priority to attributes
    return {
        "bucket": attrs.get("bucketId") or data.get("bucket"),
        "name": unquote(attrs.get("objectId", "") or data.get("name", "")),
        "contentType": attrs.get("contentType") or data.get("contentType", "<none>"),
        "size": int(attrs.get("size", "0") or data.get("size", 0)),
        "resourceState": "not_exists" if attrs.get("eventType") == "OBJECT_DELETE" else "exists",
    }

def process_event(raw_evt: dict):
    logger.debug("Raw event data: %s", json.dumps(raw_evt, indent=2))
    evt = _extract_event(raw_evt)

    # Skip deletes or pre-delete notifications
    if evt.get("resourceState") != "exists":
        logger.info("Skipping non-existence event for %s", evt.get("name"))
        return

    bucket_name = evt["bucket"]
    obj_name = evt["name"]
    logger.info(">>> Finalized Event: gs://%s/%s", bucket_name, obj_name)

    status = "QUARANTINED"

    # Only scan small images & PDFs
    if evt["contentType"] in ("image/png", "image/jpeg", "application/pdf") and evt["size"] <= 50_000_000:
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            storage_client.bucket(bucket_name).blob(obj_name).download_to_filename(tmp)
            res = subprocess.run(
                ["clamscan", "--stdout", tmp],
                capture_output=True,
                text=True,
                timeout=240
            )
            status = "SCANNED_OK" if res.returncode == 0 else "QUARANTINED"
            logger.info("ClamAV scan returned %d: %s", res.returncode, res.stdout.strip())
        except Exception as e:
            logger.error("Error during scan of %s: %s", obj_name, e)
            status = "QUARANTINED"
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    else:
        logger.info("Auto-quarantine (%s, %d bytes)", evt["contentType"], evt["size"])

    # Move object into target bucket
    dst_bucket_name = PRODUCTION if status == "SCANNED_OK" else QUARANTINE
    src_bucket = storage_client.bucket(bucket_name)
    dst_bucket = storage_client.bucket(dst_bucket_name)
    src_blob = src_bucket.blob(obj_name)

    try:
        logger.info("Copying gs://%s/%s → gs://%s/%s", bucket_name, obj_name, dst_bucket_name, obj_name)
        dst_bucket.copy_blob(src_blob, dst_bucket, obj_name)
        src_blob.delete()
        logger.info("Move succeeded, status=%s", status)
    except Exception as e:
        logger.error("Failed to move %s from %s to %s: %s", obj_name, bucket_name, dst_bucket_name, e)

    # Publish the result
    try:
        if not PROJECT_ID:
            raise ValueError("GOOGLE_CLOUD_PROJECT environment variable not set")

        topic_path = f"projects/{PROJECT_ID}/topics/{RESULTS_TOPIC}"
        publisher.publish(
            topic_path,
            b"",
            fileId=obj_name.split("/")[-1],
            status=status,
        )
        logger.info("Published scan result for %s: %s", obj_name, status)
    except Exception as e:
        logger.error("Failed to publish scan result: %s", e)

@app.route("/", methods=["POST"])
def index():
    envelope = request.get_json(silent=True)
    if not envelope:
        logger.error("No JSON payload")
        abort(400)

    # Pub/Sub push wrapper?
    if "message" in envelope:
        process_event(envelope)
    # Direct JSON payload?
    elif isinstance(envelope, dict):
        process_event(envelope)
    else:
        logger.error("Unrecognized payload: %s", envelope)
        abort(400)

    return "", 204

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))