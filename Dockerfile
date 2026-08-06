FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system --gid 10001 ddnswatch \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/ddnswatch ddnswatch

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R ddnswatch:ddnswatch /app

# The Compose bind mount may be created by Docker as root. The application
# needs to create SQLite files there on first startup, so Compose runs this
# image as root by default. The image still provides the ddnswatch user for
# deployments that use a pre-created volume with matching ownership.
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
