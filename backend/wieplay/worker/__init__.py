"""The WiePlay worker: pulls jobs from the API and runs them (PLAN §8).

The media worker on the Lenovo server runs ingest, transcode, thumbnails, purge and the NAS
inbox scan. It holds no storage credentials: the API signs every object URL it needs.

    python -m wieplay.worker          (settings: WIEPLAY_WORKER_* environment variables)
"""
