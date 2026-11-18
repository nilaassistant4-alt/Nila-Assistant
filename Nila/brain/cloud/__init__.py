from .protocol import (  # noqa: F401
    BlobStore, HttpBlobStore, LocalBlobStore, SyncEngine, SyncError, SyncReport, build_store,
)


def build_sync_engine(config, memory, vault, store=None) -> SyncEngine:
    return SyncEngine(config, memory, vault, store=store)
