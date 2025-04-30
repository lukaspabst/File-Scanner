import os
import tempfile
import json
from google.cloud import storage, pubsub_v1
import magic
import subprocess

# Environment variables (set these in Cloud Run)
UNSCANNED = os.environ["UNSCANNED_BUCKET"]
PRODUCTION = os.environ["PRODUCTION_BUCKET"]
QUARANTINE = os.environ["QUARANTINE_BUCKET"]
RESULTS_TOPIC = os.environ["SCAN_RESULTS_TOPIC"]

storage_client = storage.Client()
publisher      = pubsub_v1.PublisherClient()

def validate_and_promote(event, context):
    """
    entry point for Pub/Sub push: event is the JSON payload from GCS notif,
    context is the metadata. We expect event to include 'bucket', 'name',
    'contentType', 'size' etc.
    """
    # GCS notification payload may arrive base64-encoded or raw JSON
    if isinstance(event, bytes):
        event = json.loads(event.decode("utf-8"))

    bucket_name = event["bucket"]
    object_name = event["name"]
    content_type = event.get("contentType", "")
    size = int(event.get("size", 0))

    # 1) Quick metadata checks
    allowed_types = {"image/png", "image/jpeg", "application/pdf"}
    if content_type not in allowed_types or size > 50_000_000:
        status = "QUARANTINED"
    else:
        # 2) Download to a temp file (no in-memory risk)
        fd, tmp_path = tempfile.mkstemp()
        os.close(fd)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        blob.download_to_filename(tmp_path)

        # 3) Run ClamAV scan
        # freshclam should have run at container start
        result = subprocess.run(["clamscan", "--stdout", tmp_path], capture_output=True)
        status = "SCANNED_OK" if result.returncode == 0 else "QUARANTINED"

    # 4) Move blob to PRODUCTION or QUARANTINE
    src_bucket = storage_client.bucket(bucket_name)
    dst_name   = object_name
    if status == "SCANNED_OK":
        dst_bucket = storage_client.bucket(PRODUCTION)
    else:
        dst_bucket = storage_client.bucket(QUARANTINE)
    src_bucket.copy_blob(src_bucket.blob(object_name), dst_bucket, dst_name)
    src_bucket.blob(object_name).delete()

    # 5) Publish result (attributes only) for your Spring app to pick up
    attrs = {"fileId": object_name.split('/')[-1], "status": status}
    publisher.publish(RESULTS_TOPIC, b"", **attrs)
