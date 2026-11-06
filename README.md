# Dockerized Video Web Service & Recorder

This project is a Dockerized rework of a browser automation video recorder. It runs a Flask web service behind an Apache web server (using `mod_wsgi`) and a PostgreSQL database, fully orchestrated by Docker Compose.

## Architecture

- **web** — Apache + `mod_wsgi` serving the Flask application. Listens on host port `8000`.
- **db** — PostgreSQL 16 database. Data is persisted in a named Docker volume.
- **uploads** — A named Docker volume mounted to `/app/uploads` to persist recorded videos across container restarts.

## Requirements

- Docker
- Docker Compose
- Python 3.9+ (only to run the local client/recorder script)

## How to run the server

From the root of the project, run:

```bash
docker compose up --build
```

This will:
1. Build the `web` image (installing Apache, Python dependencies, and `mod_wsgi`).
2. Start the PostgreSQL database.
3. Wait for the database to be healthy.
4. Start the Apache web server on `http://127.0.0.1:8000`.

## How to test the API

You can use your browser or `curl` to test the endpoints:

- **List all videos:**
  `http://127.0.0.1:8000/videos`
- **Get a video URL by company name:**
  `http://127.0.0.1:8000/get_video_url?company_name=Acme Corp`

## How to run the local recorder client

To generate new videos and upload them to the running Docker server:

1. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```
2. Install local dependencies (Playwright and Requests):
   ```bash
   pip install playwright requests
   python -m playwright install chromium
   ```
3. Run the recorder, pointing it to the Dockerized server:
   ```bash
   python capture_video.py --server http://127.0.0.1:8000 --view-seconds 5
   ```

## Data Persistence

This project uses named volumes (`db_data` and `uploads`). 
If you stop the containers with `docker compose down`, your videos and database records will **not** be lost. They will still be there the next time you run `docker compose up`.

## Project Structure

- `web_service.py` — Flask application, migrated from SQLite to PostgreSQL (`psycopg2`).
- `Dockerfile` — Builds the Apache + Flask image.
- `docker-compose.yml` — Defines the `web` and `db` services, volumes, and healthcheck.
- `apache-config.conf` — Apache VirtualHost configuration.
- `web_service.wsgi` — WSGI entry point for Apache.
- `capture_video.py`, `qscript.py`, `video_client.py` — Local client scripts for recording and uploading.