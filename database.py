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
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT NOT NULL UNIQUE,
            password        TEXT NOT NULL,
            password_admin  TEXT NOT NULL DEFAULT '',
            nombre          TEXT NOT NULL,
            email           TEXT DEFAULT '',
            rol             TEXT NOT NULL DEFAULT 'consultor',
            clave_cambiada  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sesiones (
            token      TEXT PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            creada     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS grupos (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            poder  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS empresas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            grupo_id     INTEGER NOT NULL REFERENCES grupos(id) ON DELETE CASCADE,
            nombre       TEXT NOT NULL,
            rut          TEXT DEFAULT '',
            razon_social TEXT DEFAULT '',
            rol_doc      TEXT DEFAULT ''
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
            formato        TEXT DEFAULT '',
            generacion     TEXT DEFAULT 'Inicial'
        );
        CREATE TABLE IF NOT EXISTS solicitudes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id     INTEGER REFERENCES empresas(id) ON DELETE CASCADE,
            institucion    TEXT NOT NULL,
            solicitado_por INTEGER NOT NULL REFERENCES usuarios(id),
            estado         TEXT DEFAULT 'Pendiente',
            notas          TEXT DEFAULT '',
            creada         TEXT NOT NULL,
            atendida       TEXT DEFAULT '',
            generacion     TEXT DEFAULT 'Inicial',
            empresa_excel  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            accion     TEXT NOT NULL,
            detalle    TEXT DEFAULT '',
            fecha      TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS preferencias (
            usuario_id   INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
            tema         TEXT DEFAULT 'claro',
            mostrar_stats INTEGER DEFAULT 1,
            mostrar_opti  INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS device_preferences (
            device_id    TEXT PRIMARY KEY,
            login_style  TEXT DEFAULT 'orbos',
            color_bg     TEXT DEFAULT '#0d1b2e',
            color_orb1   TEXT DEFAULT '#2563eb',
            color_orb2   TEXT DEFAULT '#6366f1',
            color_btn    TEXT DEFAULT '#2563eb',
            color_icon   TEXT DEFAULT '#1d4ed8',
            actualizado  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS permisos_modulos (
            usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            modulo      TEXT NOT NULL,
            habilitado  INTEGER DEFAULT 1,
            PRIMARY KEY (usuario_id, modulo)
        );
        """)
        # Migrar columnas nuevas si no existen
        try:
            conn.execute("ALTER TABLE grupos ADD COLUMN poder TEXT DEFAULT ''")
        except: pass
        try:
            conn.execute("ALTER TABLE empresas ADD COLUMN rol_doc TEXT DEFAULT ''")
        except: pass
        try:
            conn.execute("ALTER TABLE certificados ADD COLUMN generacion TEXT DEFAULT 'Inicial'")
        except: pass
        try:
            conn.execute("ALTER TABLE solicitudes ADD COLUMN generacion TEXT DEFAULT 'Inicial'")
        except: pass
        try:
            conn.execute("ALTER TABLE solicitudes ADD COLUMN empresa_excel TEXT DEFAULT ''")
        except: pass
        # Quitar NOT NULL de empresa_id en solicitudes (SQLite requiere recrear la tabla)
        try:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(solicitudes)").fetchall()]
            if 'empresa_excel' in cols:
                # Verificar si empresa_id tiene NOT NULL revisando si falla con NULL
                conn.execute("BEGIN")
                try:
                    conn.execute("""CREATE TABLE IF NOT EXISTS solicitudes_new (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        empresa_id     INTEGER REFERENCES empresas(id) ON DELETE CASCADE,
                        institucion    TEXT NOT NULL,
                        solicitado_por INTEGER NOT NULL REFERENCES usuarios(id),
                        estado         TEXT DEFAULT 'Pendiente',
                        notas          TEXT DEFAULT '',
                        creada         TEXT NOT NULL,
                        atendida       TEXT DEFAULT '',
                        generacion     TEXT DEFAULT 'Inicial',
                        empresa_excel  TEXT DEFAULT ''
                    )""")
                    conn.execute("""INSERT INTO solicitudes_new
                        SELECT id,empresa_id,institucion,solicitado_por,estado,
                               notas,creada,atendida,generacion,empresa_excel
                        FROM solicitudes""")
                    conn.execute("DROP TABLE solicitudes")
                    conn.execute("ALTER TABLE solicitudes_new RENAME TO solicitudes")
                    conn.execute("COMMIT")
                except:
                    conn.execute("ROLLBACK")
        except: pass
        try:
            # Para usuarios existentes, password_admin = password actual (ya hasheado)
            conn.execute("ALTER TABLE usuarios ADD COLUMN password_admin TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE usuarios SET password_admin = password WHERE password_admin = ''")
        except: pass
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN clave_cambiada INTEGER DEFAULT 0")
            # Admin ya tiene clave propia por defecto
            conn.execute("UPDATE usuarios SET clave_cambiada=1 WHERE rol='admin'")
        except: pass
        try:
            conn.execute("UPDATE usuarios SET rol='ahorro' WHERE rol='consultor'")
        except: pass
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN lema TEXT DEFAULT ''")
        except: pass
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN cualidades TEXT DEFAULT ''")
        except: pass
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN fecha_ingreso TEXT DEFAULT ''")
        except: pass
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS previred_empresas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                rut          TEXT NOT NULL,
                grupo        TEXT DEFAULT '',
                razon_social TEXT DEFAULT '',
                activa       INTEGER DEFAULT 1
            )""")
        except: pass
        # Crear permisos por defecto para usuarios existentes
        try:
            usuarios = conn.execute("SELECT id, rol FROM usuarios").fetchall()
            for u in usuarios:
                for modulo in ['certificados', 'cartas']:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO permisos_modulos(usuario_id, modulo, habilitado) VALUES(?,?,?)",
                            (u['id'], modulo, 1))
                    except: pass
        except: pass
        # Migrar tabla logs si no existe (para instancias ya existentes)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
                accion     TEXT NOT NULL,
                detalle    TEXT DEFAULT '',
                fecha      TEXT NOT NULL
            )""")
        except: pass
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS preferencias (
                usuario_id    INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
                tema          TEXT DEFAULT 'claro',
                mostrar_stats INTEGER DEFAULT 1,
                mostrar_opti  INTEGER DEFAULT 1
            )""")
        except: pass

        # Crear usuario admin por defecto si no existe
        admin = conn.execute("SELECT id FROM usuarios WHERE username='admin'").fetchone()
        if not admin:
            conn.execute("""INSERT INTO usuarios(username,password,password_admin,nombre,email,rol)
                VALUES('admin',?,?,?,'','admin')""",
                (hash_password('admin123'), hash_password('admin123'), 'Administrador'))
