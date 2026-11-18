# Reference cloud back-ends

Two deliberately minimal services. **Neither can read your data.**

| service | what it stores | what it can see | what it cannot see |
|---|---|---|---|
| `sync_server` | opaque E2EE blobs + per-key version counters | blob sizes, HMAC-derived opaque ids, access times, your bearer token | plaintext, your record ids, which records relate to each other, which devices are yours, your master key |
| `update_server` | signed manifests + release artifacts | which versions you request | nothing about your data (it is not involved in sync) |

## Quick start (loopback, no Docker)

```bash
cd personal-brain
export BRAIN_SYNC_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
python3 cloud/sync_server/app.py --port 8790 --data /tmp/brain-sync &
python3 cloud/update_server/app.py --serve /tmp/release --port 8791 &

# point the client at them
export BRAIN_SYNC_TOKEN   # same value the server uses
brain config set sync.backend http
brain config set sync.url http://127.0.0.1:8790
brain config set sync.token_env BRAIN_SYNC_TOKEN
brain sync
```

## Production notes

* Put TLS in front (Caddy/Traefik/nginx). Both servers bind loopback by default
  and the compose file publishes to `127.0.0.1` only.
* `BRAIN_SYNC_TOKEN` is a *transport* credential. It does not protect your data
  (the blobs are already ciphertext) but it does prevent strangers from filling
  your storage. Rotate it freely; the client reads it from the environment.
* The sync server is stateless apart from the blob directory, so it can sit on
  S3/r2-backed storage, a NAS, or a $5 VPS.
* For real multi-tenant hosting, add: per-user blob namespaces keyed by token
  id, size quotas, and object locking. None of that changes the client's
  security properties.
