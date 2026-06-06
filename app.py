"""
Certificados de Cotizaciones — Versión Web
Flask + SQLite nativo | Despliegue Railway
"""
import os, json, shutil, secrets
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, redirect, url_for, make_response)
from database import get_conn, init_db, hash_password

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "adjuntos"))
ADJUNTOS = DATA_DIR
os.makedirs(ADJUNTOS, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

EXCEL_URL = "https://docs.google.com/spreadsheets/d/1xNA3CS_WX4KeOc4vRizCUC5rpNoTmCGmswpOjWK9VjI/gviz/tq?tqx=out:csv"
_empresa_cache = None

def cargar_empresas_excel():
    """Lee el Google Sheets y retorna mapa RUT -> {razon_social, grupo}"""
    global _empresa_cache
    try:
        import urllib.request, csv, io
        req = urllib.request.Request(EXCEL_URL, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read().decode("utf-8")
        reader = csv.reader(io.StringIO(data))
        rows   = list(reader)
        if not rows:
            return _empresa_cache or {}
        header = [c.strip().upper() for c in rows[0]]
        def col_idx(names):
            for n in names:
                for i, h in enumerate(header):
                    if n in h: return i
            return None
        rut_col   = col_idx(["RUT"])
        grp_col   = col_idx(["GRUPO"])
        razon_col = col_idx(["RAZON","RAZÓN","SOCIAL"])
        result = {}
        for row in rows[1:]:
            if len(row) <= max(filter(lambda x: x is not None, [rut_col, grp_col, razon_col]), default=0):
                continue
            rut   = row[rut_col].strip()   if rut_col is not None and len(row)>rut_col else ""
            razon = row[razon_col].strip() if razon_col is not None and len(row)>razon_col else ""
            grupo = row[grp_col].strip()   if grp_col is not None and len(row)>grp_col else "Sin grupo"
            if rut and razon:
                result[rut] = {"razon_social": razon, "grupo": grupo}
        _empresa_cache = result
        print(f"[SHEETS] Cargadas {len(result)} empresas desde Google Sheets")
        return result
    except Exception as e:
        print(f"[SHEETS] Error: {e}")
        return _empresa_cache or {}

INSTITUCIONES = [
    ("AFC",            "Certificado de deuda", "Seguro Desempleo"),
    ("AFP Capital",    "Certificado de deuda", "AFP"),
    ("AFP Cuprum",     "Certificado de deuda", "AFP"),
    ("AFP Habitat",    "Certificado de deuda", "AFP"),
    ("AFP Modelo",     "Certificado de deuda", "AFP"),
    ("AFP Planvital",  "Certificado de deuda", "AFP"),
    ("AFP Provida",    "Certificado de deuda", "AFP"),
    ("AFP Uno",        "Certificado de deuda", "AFP"),
    ("Consalud",       "Certificado de deuda", "Salud"),
    ("Cruz Blanca",    "Certificado de deuda", "Salud"),
    ("Nueva Mas Vida", "Certificado de deuda", "Salud"),
    ("Colmena",        "Certificado de deuda", "Salud"),
    ("Esencial",       "Certificado de deuda", "Salud"),
]
ESTADOS = ["Pendiente","En proceso","Obtenido","Vencido"]
MESES   = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
           "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

init_db()

# ── Auth helpers ──────────────────────────────────────────────────────────────
def get_current_user():
    token = request.cookies.get("session_token")
    if not token: return None
    with get_conn() as conn:
        row = conn.execute("""
            SELECT u.* FROM sesiones s
            JOIN usuarios u ON u.id = s.usuario_id
            WHERE s.token = ?""", (token,)).fetchone()
        return dict(row) if row else None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or user["rol"] != "admin":
            return jsonify({"error": "Sin permisos"}), 403
        return f(*args, **kwargs)
    return decorated

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "No autenticado"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Rutas auth ────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET"])
def login():
    if get_current_user():
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def do_login():
    d = request.json
    username = d.get("username","").strip()
    password = d.get("password","")
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM usuarios WHERE username=? AND password=?",
            (username, hash_password(password))).fetchone()
        if not user:
            return jsonify({"error": "Usuario o contraseña incorrectos"}), 401
        token = secrets.token_hex(32)
        conn.execute("INSERT INTO sesiones(token,usuario_id,creada) VALUES(?,?,?)",
                     (token, user["id"], datetime.now().isoformat()))
    resp = make_response(jsonify({"ok": True, "rol": user["rol"]}))
    resp.set_cookie("session_token", token, max_age=86400*30, httponly=True)
    return resp

@app.route("/api/certi_report", methods=["POST"])
@api_login_required
def certi_report():
    user = get_current_user()
    d    = request.json
    send_email_solicitud("epavez@optimizaco.cl", "Esteban", {
        "empresa": f"Reporte de problema — {user['nombre']}",
        "rut": "",
        "institucion": "Soporte Certi",
        "solicitado_por": user["nombre"],
        "notas": d.get("mensaje",""),
        "sid": 0,
        "poder": "",
        "rol_doc": "",
    })
    return jsonify({"ok": True})

@app.route("/logout_beacon", methods=["POST"])
def logout_beacon():
    """Cierra sesión via sendBeacon al cerrar el navegador"""
    token = request.cookies.get("session_token")
    if token:
        with get_conn() as conn:
            conn.execute("DELETE FROM sesiones WHERE token=?", (token,))
    return "", 204

@app.route("/logout")
def logout():
    token = request.cookies.get("session_token")
    if token:
        with get_conn() as conn:
            conn.execute("DELETE FROM sesiones WHERE token=?", (token,))
    resp = make_response(redirect(url_for("login")))
    resp.delete_cookie("session_token")
    return resp

# ── Rutas principales ─────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("index.html",
        instituciones=INSTITUCIONES, estados=ESTADOS, meses=MESES,
        user=user)

# ── API usuario actual ────────────────────────────────────────────────────────
@app.route("/api/me")
@api_login_required
def get_me():
    return jsonify(get_current_user())

# ── API Usuarios (solo admin) ─────────────────────────────────────────────────
@app.route("/api/usuarios")
@admin_required
def get_usuarios():
    with get_conn() as conn:
        rows = conn.execute("SELECT id,username,nombre,email,rol FROM usuarios ORDER BY nombre").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/usuarios", methods=["POST"])
@admin_required
def crear_usuario():
    d = request.json
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO usuarios(username,password,nombre,email,rol) VALUES(?,?,?,?,?)",
                (d["username"], hash_password(d["password"]),
                 d["nombre"], d.get("email",""), d["rol"]))
            return jsonify({"id": cur.lastrowid}), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 400

@app.route("/api/usuarios/<int:uid>", methods=["PUT"])
@admin_required
def editar_usuario(uid):
    d = request.json
    with get_conn() as conn:
        if "password" in d and d["password"]:
            conn.execute("UPDATE usuarios SET nombre=?,email=?,rol=?,password=? WHERE id=?",
                (d["nombre"], d.get("email",""), d["rol"], hash_password(d["password"]), uid))
        else:
            conn.execute("UPDATE usuarios SET nombre=?,email=?,rol=? WHERE id=?",
                (d["nombre"], d.get("email",""), d["rol"], uid))
        return jsonify({"ok": True})

@app.route("/api/usuarios/<int:uid>", methods=["DELETE"])
@admin_required
def eliminar_usuario(uid):
    user = get_current_user()
    if user["id"] == uid:
        return jsonify({"error": "No puedes eliminarte a ti mismo"}), 400
    with get_conn() as conn:
        conn.execute("DELETE FROM usuarios WHERE id=?", (uid,))
        return jsonify({"ok": True})

# ── API empresas desde Excel ──────────────────────────────────────────────────
@app.route("/api/empresas_excel")
@api_login_required
def get_empresas_excel():
    """Retorna lista de empresas desde el Excel de Google Drive"""
    empresas = cargar_empresas_excel()
    result = []
    for rut, data in empresas.items():
        result.append({
            "rut": rut,
            "nombre": data["razon_social"],
            "grupo": data["grupo"]
        })
    result.sort(key=lambda x: x["nombre"])
    return jsonify(result)

# ── API Grupos ────────────────────────────────────────────────────────────────
@app.route("/api/grupos")
@api_login_required
def get_grupos():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT g.id, g.nombre, g.poder, COUNT(e.id) as n_empresas
            FROM grupos g LEFT JOIN empresas e ON e.grupo_id = g.id
            GROUP BY g.id ORDER BY g.nombre""").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/grupos", methods=["POST"])
@api_login_required
def crear_grupo():
    user = get_current_user()
    if user["rol"] == "terreno":
        return jsonify({"error": "Sin permisos"}), 403
    d = request.json
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO grupos(nombre) VALUES(?)", (d["nombre"],))
        return jsonify({"id": cur.lastrowid, "nombre": d["nombre"]}), 201

@app.route("/api/grupos/<int:gid>", methods=["PUT"])
@api_login_required
def editar_grupo(gid):
    user = get_current_user()
    if user["rol"] == "terreno":
        return jsonify({"error": "Sin permisos"}), 403
    d = request.json
    with get_conn() as conn:
        conn.execute("UPDATE grupos SET nombre=? WHERE id=?", (d["nombre"], gid))
        return jsonify({"ok": True})

@app.route("/api/grupos/<int:gid>", methods=["DELETE"])
@api_login_required
def eliminar_grupo(gid):
    user = get_current_user()
    if user["rol"] not in ("admin","consultor"):
        return jsonify({"error": "Sin permisos"}), 403
    with get_conn() as conn:
        conn.execute("DELETE FROM grupos WHERE id=?", (gid,))
        return jsonify({"ok": True})

@app.route("/api/grupos/<int:gid>/empresas_list")
@api_login_required
def get_empresas_grupo(gid):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,nombre,rut FROM empresas WHERE grupo_id=? ORDER BY nombre", (gid,)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/grupos/<int:gid>/poder", methods=["POST"])
@api_login_required
def upload_poder(gid):
    user = get_current_user()
    if user["rol"] != "admin":
        return jsonify({"error":"Sin permisos"}), 403
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}), 400
    fname = f"poder_grupo_{gid}_{f.filename}"
    f.save(os.path.join(ADJUNTOS, fname))
    with get_conn() as conn:
        conn.execute("UPDATE grupos SET poder=? WHERE id=?", (fname, gid))
    return jsonify({"poder": fname})

@app.route("/api/empresas/<int:eid>/rol", methods=["POST"])
@api_login_required
def upload_rol(eid):
    user = get_current_user()
    if user["rol"] != "admin":
        return jsonify({"error":"Sin permisos"}), 403
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}), 400
    fname = f"rol_empresa_{eid}_{f.filename}"
    f.save(os.path.join(ADJUNTOS, fname))
    with get_conn() as conn:
        conn.execute("UPDATE empresas SET rol_doc=? WHERE id=?", (fname, eid))
    return jsonify({"rol_doc": fname})

# ── API Empresas ──────────────────────────────────────────────────────────────
@app.route("/api/grupos/<int:gid>/empresas", methods=["POST"])
@api_login_required
def crear_empresa(gid):
    user = get_current_user()
    if user["rol"] == "terreno":
        return jsonify({"error": "Sin permisos"}), 403
    d = request.json
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO empresas(grupo_id,nombre,rut,razon_social) VALUES(?,?,?,?)",
            (gid, d["nombre"], d.get("rut",""), d.get("razon_social","")))
        eid = cur.lastrowid
        if d.get("cargar_default", True):
            certs_default(conn, eid)
        return jsonify({"id": eid, "nombre": d["nombre"], "grupo_id": gid}), 201

@app.route("/api/empresas/<int:eid>")
@api_login_required
def get_empresa(eid):
    with get_conn() as conn:
        emp = dict(conn.execute("""
            SELECT e.*, g.poder as grupo_poder
            FROM empresas e JOIN grupos g ON g.id = e.grupo_id
            WHERE e.id=?""", (eid,)).fetchone())
        certs = [dict(r) for r in conn.execute(
            "SELECT * FROM certificados WHERE empresa_id=? ORDER BY id", (eid,)).fetchall()]
        for c in certs:
            c["sin_deuda"]     = bool(c["sin_deuda"])
            c["sin_afiliados"] = bool(c["sin_afiliados"])
        emp["certificados"] = certs
        return jsonify(emp)

@app.route("/api/empresas/<int:eid>", methods=["PUT"])
@api_login_required
def editar_empresa(eid):
    user = get_current_user()
    if user["rol"] == "terreno":
        return jsonify({"error": "Sin permisos"}), 403
    d = request.json
    with get_conn() as conn:
        conn.execute("UPDATE empresas SET nombre=?,rut=?,razon_social=? WHERE id=?",
            (d.get("nombre"), d.get("rut",""), d.get("razon_social",""), eid))
        return jsonify({"ok": True})

@app.route("/api/empresas/<int:eid>", methods=["DELETE"])
@api_login_required
def eliminar_empresa(eid):
    user = get_current_user()
    if user["rol"] not in ("admin","consultor"):
        return jsonify({"error": "Sin permisos"}), 403
    with get_conn() as conn:
        conn.execute("DELETE FROM empresas WHERE id=?", (eid,))
        return jsonify({"ok": True})

# ── API Certificados ──────────────────────────────────────────────────────────
@app.route("/api/empresas/<int:eid>/certificados", methods=["POST"])
@api_login_required
def crear_cert(eid):
    d = request.json
    with get_conn() as conn:
        cur = conn.execute("""INSERT INTO certificados
            (empresa_id,institucion,tipo,categoria,estado,mes,anio,
             folio,notas,adjunto,sin_deuda,sin_afiliados,formato)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, d.get("institucion",""), d.get("tipo","Certificado de deuda"),
             d.get("categoria",""), d.get("estado","Pendiente"),
             d.get("mes",""), d.get("anio",""), d.get("folio",""),
             d.get("notas",""), d.get("adjunto",""),
             1 if d.get("sin_deuda") else 0,
             1 if d.get("sin_afiliados") else 0,
             d.get("formato","")))
        return jsonify({"id": cur.lastrowid}), 201

@app.route("/api/certificados/<int:cid>", methods=["PUT"])
@api_login_required
def editar_cert(cid):
    user = get_current_user()
    d    = request.json
    # Terreno solo puede cambiar estado, adjunto, sin_deuda, sin_afiliados, formato
    if user["rol"] == "terreno":
        allowed = {"estado","adjunto","sin_deuda","sin_afiliados","formato","mes","anio"}
        d = {k:v for k,v in d.items() if k in allowed}
    with get_conn() as conn:
        fields, vals = [], []
        for k in ["institucion","tipo","categoria","estado","mes","anio","folio","notas","adjunto","formato"]:
            if k in d: fields.append(f"{k}=?"); vals.append(d[k])
        if "sin_deuda"    in d: fields.append("sin_deuda=?");    vals.append(1 if d["sin_deuda"] else 0)
        if "sin_afiliados" in d: fields.append("sin_afiliados=?"); vals.append(1 if d["sin_afiliados"] else 0)
        if fields:
            vals.append(cid)
            conn.execute(f"UPDATE certificados SET {','.join(fields)} WHERE id=?", vals)
        return jsonify({"ok": True})

@app.route("/api/certificados/<int:cid>", methods=["DELETE"])
@api_login_required
def eliminar_cert(cid):
    user = get_current_user()
    if user["rol"] == "terreno":
        return jsonify({"error": "Sin permisos"}), 403
    with get_conn() as conn:
        conn.execute("DELETE FROM certificados WHERE id=?", (cid,))
        return jsonify({"ok": True})

@app.route("/api/certificados/<int:cid>/adjunto", methods=["POST"])
@api_login_required
def upload_adjunto(cid):
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}), 400
    fname = f"{cid}_{f.filename}"
    f.save(os.path.join(ADJUNTOS, fname))
    with get_conn() as conn:
        conn.execute("UPDATE certificados SET adjunto=? WHERE id=?", (fname, cid))
    return jsonify({"adjunto": fname})

@app.route("/adjuntos/<path:fname>")
@login_required
def ver_adjunto(fname):
    return send_from_directory(ADJUNTOS, fname)

# ── API Solicitudes ───────────────────────────────────────────────────────────
@app.route("/api/solicitudes", methods=["GET"])
@api_login_required
def get_solicitudes():
    user = get_current_user()
    with get_conn() as conn:
        if user["rol"] == "consultor":
            rows = conn.execute("""
                SELECT s.*, e.nombre as empresa_nombre, e.rut,
                       g.nombre as grupo_nombre, u.nombre as solicitado_nombre
                FROM solicitudes s
                JOIN empresas e ON e.id = s.empresa_id
                JOIN grupos g ON g.id = e.grupo_id
                JOIN usuarios u ON u.id = s.solicitado_por
                WHERE s.solicitado_por = ?
                ORDER BY s.creada DESC""", (user["id"],)).fetchall()
        else:
            rows = conn.execute("""
                SELECT s.*, e.nombre as empresa_nombre, e.rut,
                       g.nombre as grupo_nombre, u.nombre as solicitado_nombre
                FROM solicitudes s
                JOIN empresas e ON e.id = s.empresa_id
                JOIN grupos g ON g.id = e.grupo_id
                JOIN usuarios u ON u.id = s.solicitado_por
                ORDER BY s.creada DESC""").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/solicitudes", methods=["POST"])
@api_login_required
def crear_solicitud():
    user = get_current_user()
    if user["rol"] not in ("admin","consultor"):
        return jsonify({"error": "Sin permisos"}), 403
    d = request.json

    # Si viene empresa_excel, buscar o crear la empresa en DB
    empresa_id = d.get("empresa_id")
    if not empresa_id and d.get("empresa_excel"):
        ex = d["empresa_excel"]
        with get_conn() as conn:
            # Buscar por RUT
            emp_row = conn.execute(
                "SELECT e.id FROM empresas e WHERE REPLACE(e.rut,'-','')=?",
                (ex["rut"].replace("-",""),)).fetchone()
            if emp_row:
                empresa_id = emp_row["id"]
            else:
                # Buscar/crear grupo
                grp = conn.execute("SELECT id FROM grupos WHERE UPPER(nombre)=UPPER(?)",
                                    (ex["grupo"],)).fetchone()
                gid = grp["id"] if grp else conn.execute(
                    "INSERT INTO grupos(nombre) VALUES(?)", (ex["grupo"],)).lastrowid
                cur = conn.execute(
                    "INSERT INTO empresas(grupo_id,nombre,rut,razon_social) VALUES(?,?,?,?)",
                    (gid, ex["nombre"], ex["rut"], ex["nombre"]))
                empresa_id = cur.lastrowid
                certs_default(conn, empresa_id)

    with get_conn() as conn:
        cur = conn.execute("""INSERT INTO solicitudes
            (empresa_id,institucion,solicitado_por,estado,notas,creada)
            VALUES(?,?,?,?,?,?)""",
            (empresa_id, d["institucion"], user["id"],
             "Pendiente", d.get("notas",""),
             datetime.now().strftime("%d/%m/%Y %H:%M")))
        sid = cur.lastrowid

        # Enviar email a usuarios de terreno
        terrenos = conn.execute(
            "SELECT email,nombre FROM usuarios WHERE rol='terreno' AND email != ''").fetchall()
        emp = conn.execute("""
            SELECT e.*, g.poder as grupo_poder
            FROM empresas e JOIN grupos g ON g.id = e.grupo_id
            WHERE e.id=?""", (empresa_id,)).fetchone()

    # Enviar emails
    for t in terrenos:
        send_email_solicitud(t["email"], t["nombre"], {
            "empresa": emp["nombre"] if emp else "",
            "rut": emp["rut"] if emp else "",
            "institucion": d["institucion"],
            "solicitado_por": user["nombre"],
            "notas": d.get("notas",""),
            "sid": sid,
            "poder": emp["grupo_poder"] if emp else "",
            "rol_doc": emp["rol_doc"] if emp else "",
        })

    return jsonify({"id": sid}), 201

@app.route("/api/solicitudes/<int:sid>", methods=["PUT"])
@api_login_required
def actualizar_solicitud(sid):
    d = request.json
    with get_conn() as conn:
        conn.execute("UPDATE solicitudes SET estado=?,atendida=? WHERE id=?",
            (d["estado"], datetime.now().strftime("%d/%m/%Y %H:%M"), sid))
        return jsonify({"ok": True})

# ── Email ─────────────────────────────────────────────────────────────────────
def send_email_solicitud(to_email, to_nombre, data):
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_user = os.environ.get("SMTP_USER","")
        smtp_pass = os.environ.get("SMTP_PASS","")
        if not smtp_user or not smtp_pass:
            print(f"[EMAIL] Sin configuración SMTP — solicitud para {to_email}")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🔔 Nueva solicitud — {data['institucion']} | {data['empresa']}"
        msg["From"]    = smtp_user
        msg["To"]      = to_email

        html = f"""
        <div style="font-family:sans-serif;max-width:500px;margin:auto;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
          <div style="background:#0f172a;padding:20px">
            <h2 style="color:#fff;margin:0">🔔 Nueva solicitud de certificado</h2>
          </div>
          <div style="padding:20px">
            <p>Hola <strong>{to_nombre}</strong>, tienes una nueva solicitud:</p>
            <table style="width:100%;border-collapse:collapse">
              <tr><td style="padding:8px;color:#64748b;width:140px">Empresa</td><td style="padding:8px;font-weight:600">{data['empresa']}</td></tr>
              <tr style="background:#f8fafc"><td style="padding:8px;color:#64748b">RUT</td><td style="padding:8px">{data['rut']}</td></tr>
              <tr><td style="padding:8px;color:#64748b">Institución</td><td style="padding:8px;font-weight:600">{data['institucion']}</td></tr>
              <tr style="background:#f8fafc"><td style="padding:8px;color:#64748b">Solicitado por</td><td style="padding:8px">{data['solicitado_por']}</td></tr>
              {'<tr><td style="padding:8px;color:#64748b">Notas</td><td style="padding:8px">'+data['notas']+'</td></tr>' if data['notas'] else ''}
            </table>
            <div style="margin-top:20px;text-align:center">
              <a href="{os.environ.get('APP_URL','https://web-production-286542.up.railway.app')}"
                 style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600">
                Ver en la app →
              </a>
            </div>
          </div>
        </div>"""

        msg.attach(MIMEText(html, "html"))

        # Adjuntar poder y ROL si existen
        from email.mime.base import MIMEBase
        from email import encoders
        for key, label in [("poder","Poder_Notarial"), ("rol_doc","ROL")]:
            fname = data.get(key,"")
            if fname:
                fpath = os.path.join(ADJUNTOS, fname)
                if os.path.exists(fpath):
                    with open(fpath, "rb") as f:
                        part = MIMEBase("application","octet-stream")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    ext = os.path.splitext(fname)[1]
                    part.add_header("Content-Disposition", f"attachment; filename={label}{ext}")
                    msg.attach(part)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_email, msg.as_string())
        print(f"[EMAIL] Enviado a {to_email}")
    except Exception as e:
        print(f"[EMAIL] Error: {e}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def certs_default(conn, empresa_id):
    anio = str(datetime.now().year)
    for nombre, tipo, cat in INSTITUCIONES:
        conn.execute("""INSERT INTO certificados
            (empresa_id,institucion,tipo,categoria,estado,mes,anio,
             folio,notas,adjunto,sin_deuda,sin_afiliados,formato)
            VALUES(?,?,?,?,'Pendiente','',?,'','','',0,0,'')""",
            (empresa_id, nombre, tipo, cat, anio))

def inst_match(nombre):
    nl = nombre.lower().strip()
    for inst, _, _ in INSTITUCIONES:
        if inst.lower() in nl or nl in inst.lower():
            return inst
    return None

def row_to_dict(row):
    return dict(row) if row else None

# ── Importar Excel + PDFs ─────────────────────────────────────────────────────
@app.route("/api/importar", methods=["POST"])
@api_login_required
def importar():
    try:
        import openpyxl
    except ImportError:
        return jsonify({"error":"openpyxl no instalado"}), 500
    excel = request.files.get("excel")
    pdfs  = request.files.getlist("pdfs")
    if not excel: return jsonify({"error":"Falta Excel"}), 400
    wb   = openpyxl.load_workbook(excel, read_only=True, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = [str(c).strip().upper() if c else "" for c in rows[0]]
    def col_idx(names):
        for n in names:
            for i, h in enumerate(header):
                if n in h: return i
        return None
    rut_col   = col_idx(["RUT"])
    grp_col   = col_idx(["GRUPO"])
    razon_col = col_idx(["RAZON","RAZÓN","SOCIAL"])
    empresa_map = {}
    for row in rows[1:]:
        rut   = str(row[rut_col]).strip()  if row[rut_col]   else ""
        razon = str(row[razon_col]).strip() if row[razon_col] else ""
        grupo = str(row[grp_col]).strip()   if (grp_col is not None and row[grp_col]) else "Sin grupo"
        if rut and razon:
            empresa_map[rut] = {"razon_social": razon, "grupo": grupo}
    creadas = 0; actualizadas = 0; no_proc = []
    with get_conn() as conn:
        for pdf in pdfs:
            fname  = os.path.splitext(pdf.filename)[0]
            parts  = fname.split("_", 1)
            if len(parts) < 2:
                no_proc.append(f"{fname} → formato inválido"); continue
            rut_a  = parts[0].strip()
            inst_a = parts[1].strip()
            inst_n = inst_match(inst_a)
            if not inst_n:
                no_proc.append(f"{fname} → institución no reconocida: '{inst_a}'"); continue
            emp_data = empresa_map.get(rut_a)
            if not emp_data:
                for k,v in empresa_map.items():
                    if k.replace("-","") == rut_a.replace("-",""):
                        emp_data=v; rut_a=k; break
            if not emp_data:
                no_proc.append(f"{fname} → RUT {rut_a} no en Excel"); continue
            grp_nombre = emp_data["grupo"]
            razon_soc  = emp_data["razon_social"]
            grp_row = conn.execute("SELECT id FROM grupos WHERE UPPER(nombre)=UPPER(?)", (grp_nombre,)).fetchone()
            if grp_row:
                gid = grp_row["id"]
            else:
                cur = conn.execute("INSERT INTO grupos(nombre) VALUES(?)", (grp_nombre,))
                gid = cur.lastrowid
            emp_row = conn.execute(
                "SELECT id FROM empresas WHERE grupo_id=? AND REPLACE(rut,'-','')=?",
                (gid, rut_a.replace("-",""))).fetchone()
            if emp_row:
                eid = emp_row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO empresas(grupo_id,nombre,rut,razon_social) VALUES(?,?,?,?)",
                    (gid, razon_soc, rut_a, razon_soc))
                eid = cur.lastrowid
                certs_default(conn, eid)
                creadas += 1
            dest = f"{eid}_{pdf.filename}"
            pdf.save(os.path.join(ADJUNTOS, dest))
            cert_row = conn.execute(
                "SELECT id FROM certificados WHERE empresa_id=? AND LOWER(institucion) LIKE ?",
                (eid, f"%{inst_n.lower()}%")).fetchone()
            if cert_row:
                conn.execute("UPDATE certificados SET estado='Obtenido',adjunto=? WHERE id=?",
                    (dest, cert_row["id"]))
                actualizadas += 1
            else:
                no_proc.append(f"{fname} → certificado '{inst_n}' no encontrado")
    return jsonify({"creadas":creadas,"actualizadas":actualizadas,"no_procesados":no_proc})

# ── Migrar JSON ───────────────────────────────────────────────────────────────
@app.route("/api/migrar", methods=["POST"])
@api_login_required
def migrar_json():
    user = get_current_user()
    if user["rol"] != "admin":
        return jsonify({"error":"Solo el admin puede migrar datos"}), 403
    f = request.files.get("json")
    if not f: return jsonify({"error":"Falta JSON"}), 400
    data = json.load(f)
    emp_count = 0; cert_count = 0
    with get_conn() as conn:
        for g_d in data.get("grupos", []):
            grp = conn.execute("SELECT id FROM grupos WHERE UPPER(nombre)=UPPER(?)", (g_d["nombre"],)).fetchone()
            gid = grp["id"] if grp else conn.execute("INSERT INTO grupos(nombre) VALUES(?)", (g_d["nombre"],)).lastrowid
            for e_d in g_d.get("empresas", []):
                emp = conn.execute("SELECT id FROM empresas WHERE grupo_id=? AND rut=?", (gid, e_d.get("rut",""))).fetchone()
                if emp:
                    eid = emp["id"]
                else:
                    eid = conn.execute("INSERT INTO empresas(grupo_id,nombre,rut,razon_social) VALUES(?,?,?,?)",
                        (gid, e_d["nombre"], e_d.get("rut",""), e_d.get("razon_social",""))).lastrowid
                    emp_count += 1
                for c_d in e_d.get("certificados", []):
                    ex = conn.execute("SELECT id FROM certificados WHERE empresa_id=? AND institucion=?",
                        (eid, c_d["institucion"])).fetchone()
                    if ex:
                        conn.execute("""UPDATE certificados SET estado=?,mes=?,anio=?,notas=?,
                            sin_deuda=?,sin_afiliados=?,formato=? WHERE id=?""",
                            (c_d.get("estado","Pendiente"), c_d.get("mes",""), c_d.get("anio",""),
                             c_d.get("notas",""), 1 if c_d.get("sin_deuda") else 0,
                             1 if c_d.get("sin_afiliados") else 0, c_d.get("formato",""), ex["id"]))
                    else:
                        conn.execute("""INSERT INTO certificados
                            (empresa_id,institucion,tipo,categoria,estado,mes,anio,
                             folio,notas,adjunto,sin_deuda,sin_afiliados,formato)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (eid, c_d.get("institucion",""), c_d.get("tipo","Certificado de deuda"),
                             c_d.get("categoria",""), c_d.get("estado","Pendiente"),
                             c_d.get("mes",""), c_d.get("anio",""), c_d.get("folio",""),
                             c_d.get("notas",""), c_d.get("adjunto",""),
                             1 if c_d.get("sin_deuda") else 0,
                             1 if c_d.get("sin_afiliados") else 0,
                             c_d.get("formato","")))
                    cert_count += 1
    return jsonify({"empresas": emp_count, "certificados": cert_count})

# ── Reportes ──────────────────────────────────────────────────────────────────
@app.route("/reporte/empresa/<int:eid>")
@login_required
def reporte_empresa(eid):
    with get_conn() as conn:
        emp   = row_to_dict(conn.execute("SELECT * FROM empresas WHERE id=?", (eid,)).fetchone())
        grupo = row_to_dict(conn.execute("SELECT * FROM grupos WHERE id=?", (emp["grupo_id"],)).fetchone())
        certs = [dict(r) for r in conn.execute("SELECT * FROM certificados WHERE empresa_id=? ORDER BY id", (eid,)).fetchall()]
        for c in certs:
            c["sin_deuda"]     = bool(c["sin_deuda"])
            c["sin_afiliados"] = bool(c["sin_afiliados"])
    cats = {}
    for c in certs:
        cats.setdefault(c["categoria"],[]).append(c)
    ICON = {"Obtenido":"✓","Pendiente":"●","En proceso":"◑","Vencido":"✕"}
    return render_template("reporte_empresa.html",
        emp=emp, grupo=grupo, certs=certs, cats=cats,
        orden_cat=["AFP","Salud","Seguro Desempleo"], icon=ICON,
        now=datetime.now().strftime("%d/%m/%Y %H:%M"))

@app.route("/reporte/grupo/<int:gid>")
@login_required
def reporte_grupo(gid):
    with get_conn() as conn:
        grupo    = row_to_dict(conn.execute("SELECT * FROM grupos WHERE id=?", (gid,)).fetchone())
        emp_rows = conn.execute("SELECT * FROM empresas WHERE grupo_id=? ORDER BY nombre", (gid,)).fetchall()
        empresas = []
        for e in emp_rows:
            ed = dict(e)
            certs = [dict(r) for r in conn.execute("SELECT * FROM certificados WHERE empresa_id=? ORDER BY id", (e["id"],)).fetchall()]
            for c in certs:
                c["sin_deuda"]     = bool(c["sin_deuda"])
                c["sin_afiliados"] = bool(c["sin_afiliados"])
            ed["certificados"] = certs
            empresas.append(ed)
    ICON = {"Obtenido":"✓","Pendiente":"●","En proceso":"◑","Vencido":"✕"}
    return render_template("reporte_grupo.html",
        grupo=grupo, empresas=empresas,
        instituciones=[i[0] for i in INSTITUCIONES],
        icon=ICON, now=datetime.now().strftime("%d/%m/%Y %H:%M"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
