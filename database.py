"""Base de datos SQLite usando sqlite3 nativo — sin dependencias externas."""
import sqlite3, os, hashlib, secrets

DB_PATH = os.environ.get("DB_PATH",
          os.path.join(os.path.dirname(os.path.abspath(__file__)), "certificados.db"))

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL UNIQUE,
            password   TEXT NOT NULL,
            nombre     TEXT NOT NULL,
            email      TEXT DEFAULT '',
            rol        TEXT NOT NULL DEFAULT 'consultor'
        );
        CREATE TABLE IF NOT EXISTS sesiones (
            token      TEXT PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            creada     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS grupos (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS empresas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            grupo_id     INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
            nombre       TEXT NOT NULL,
            rut          TEXT DEFAULT '',
            razon_social TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS certificados (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id     INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            institucion    TEXT DEFAULT '',
            tipo           TEXT DEFAULT 'Certificado de deuda',
            categoria      TEXT DEFAULT '',
            estado         TEXT DEFAULT 'Pendiente',
            mes            TEXT DEFAULT '',
            anio           TEXT DEFAULT '',
            folio          TEXT DEFAULT '',
            notas          TEXT DEFAULT '',
            adjunto        TEXT DEFAULT '',
            sin_deuda      INTEGER DEFAULT 0,
            sin_afiliados  INTEGER DEFAULT 0,
            formato        TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS solicitudes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id     INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            institucion    TEXT NOT NULL,
            solicitado_por INTEGER NOT NULL REFERENCES usuarios(id),
            estado         TEXT DEFAULT 'Pendiente',
            notas          TEXT DEFAULT '',
            creada         TEXT NOT NULL,
            atendida       TEXT DEFAULT ''
        );
        """)
        # Crear usuario admin por defecto si no existe
        admin = conn.execute("SELECT id FROM usuarios WHERE username='admin'").fetchone()
        if not admin:
            conn.execute("""INSERT INTO usuarios(username,password,nombre,email,rol)
                VALUES('admin',?,?,'','admin')""",
                (hash_password('admin123'), 'Administrador'))
