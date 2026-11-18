# Dependency-free: the reference servers use only the Python standard library.
FROM python:3.12-slim
WORKDIR /app
COPY cloud ./cloud
COPY scripts ./scripts
COPY brain ./brain
RUN useradd --create-home --uid 10001 brain && mkdir -p /data && chown -R brain:brain /data /app
USER brain
EXPOSE 8790 8791
CMD ["python", "cloud/sync_server/app.py", "--host", "0.0.0.0", "--port", "8790", "--data", "/data/sync"]
