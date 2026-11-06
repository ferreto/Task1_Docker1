import os
import time
import psycopg2
import psycopg2.extras
from pathlib import Path
from flask import Flask, request, jsonify, g, send_from_directory

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"

# Создаем папку uploads при старте, если ее нет
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Забираем настройки БД из переменных окружения (Docker передаст их)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "videos")
DB_USER = os.environ.get("DB_USER", "video_user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "video_pass")


def get_db():
    """Открывает соединение с PostgreSQL для текущего контекста запроса."""
    db = getattr(g, '_database', None)
    if db is None:
        # psycopg2 не является потокобезопасным, поэтому открываем
        # новое соединение на каждый запрос (через flask.g)
        db = g._database = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        # Используем DictCursor, чтобы обращаться к колонкам по имени (row['name'])
        db.cursor_factory = psycopg2.extras.DictCursor
    return db


@app.teardown_appcontext
def close_db(exception):
    """Закрывает соединение с БД после завершения запроса."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    """Создает таблицу videos. Ждет, пока база данных полностью загрузится."""
    retries = 10
    for attempt in range(retries):
        try:
            # Открываем соединение напрямую, без контекста Flask (так как это запуск)
            with psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD
            ) as conn:
                with conn.cursor() as cur:
                    # Синтаксис PostgreSQL: SERIAL вместо AUTOINCREMENT, NOW() вместо datetime('now')
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS videos (
                            id SERIAL PRIMARY KEY,
                            company_name TEXT NOT NULL UNIQUE,
                            file_path TEXT NOT NULL,
                            video_url TEXT NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                        )
                    """)
                conn.commit()
            print("[web_service] Database initialized successfully.")
            return
        except psycopg2.OperationalError as e:
            print(f"[web_service] Database not ready (attempt {attempt + 1}/{retries}). Waiting 2 seconds...")
            time.sleep(2)
    
    print("[web_service] ERROR: Could not connect to the database.")
    raise RuntimeError("Database initialization failed")


def _sanitize_filename(name):
    """Очищает имя файла от недопустимых символов."""
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "video"


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    company_name = request.form.get('company_name', '').strip()
    
    if not file or file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if not company_name:
        return jsonify({"error": "No company_name provided"}), 400

    safe_name = _sanitize_filename(company_name)
    ext = os.path.splitext(file.filename)[1] or ".webm"
    filename = f"{safe_name}{ext}"
    filepath = UPLOAD_DIR / filename
    
    file.save(filepath)
    
    video_url = f"{request.host_url.rstrip('/')}/media/{filename}"
    
    db = get_db()
    try:
        with db.cursor() as cur:
            # Синтаксис PostgreSQL: плейсхолдеры %s и EXCLUDED для Upsert
            cur.execute("""
                INSERT INTO videos (company_name, file_path, video_url)
                VALUES (%s, %s, %s)
                ON CONFLICT (company_name) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    video_url = EXCLUDED.video_url,
                    updated_at = NOW()
            """, (company_name, str(filepath), video_url))
        db.commit()
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "company_name": company_name,
        "video_url": video_url
    }), 200


@app.route('/get_video_url', methods=['GET'])
def get_video_url():
    company_name = request.args.get('company_name', '').strip()
    if not company_name:
        return jsonify({"error": "company_name is required"}), 400

    db = get_db()
    with db.cursor() as cur:
        # Синтаксис PostgreSQL: %s вместо ?
        cur.execute("SELECT video_url FROM videos WHERE company_name = %s", (company_name,))
        row = cur.fetchone()
    
    if row:
        return jsonify({
            "company_name": company_name,
            "video_url": row["video_url"]
        }), 200
    else:
        return jsonify({"error": "Not found"}), 404


@app.route('/media/<path:filename>', methods=['GET'])
def serve_media(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/videos', methods=['GET'])
def list_videos():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM videos ORDER BY updated_at DESC")
        rows = cur.fetchall()
    return jsonify([dict(r) for r in rows]), 200


# Вызываем инициализацию при старте WSGI-приложения (Apache загрузит этот файл)
init_db()

# app.run() отсюда удален — Apache + mod_wsgi сами запустят сервер