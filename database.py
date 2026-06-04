"""Base de datos SQLite usando sqlite3 nativo — sin dependencias externas."""
import sqlite3, os

DB_PATH = os.environ.get("DB_PATH",
          os.path.join(os.path.dirname(os.path.abspath(__file__)), "certificados.db"))

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
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
            sin_afiliados  INTEGER DEFAULT 0
        );
        """)
