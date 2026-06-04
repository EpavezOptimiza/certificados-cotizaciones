"""
Certificados de Cotizaciones — Versión Web
Flask + SQLite nativo | Despliegue Railway
"""
import os, json, shutil
from datetime import datetime
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory)
from database import get_conn, init_db

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADJUNTOS = os.path.join(BASE_DIR, "adjuntos")
os.makedirs(ADJUNTOS, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

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

# ── Helpers ───────────────────────────────────────────────────────────────────
def row_to_dict(row):
    return dict(row) if row else None

def certs_default(conn, empresa_id):
    anio = str(datetime.now().year)
    for nombre, tipo, cat in INSTITUCIONES:
        conn.execute("""INSERT INTO certificados
            (empresa_id,institucion,tipo,categoria,estado,mes,anio,
             folio,notas,adjunto,sin_deuda,sin_afiliados)
            VALUES (?,?,?,?,'Pendiente','',?,
                    '','','',0,0)""",
            (empresa_id, nombre, tipo, cat, anio))

def inst_match(nombre):
    nl = nombre.lower().strip()
    for inst, _, _ in INSTITUCIONES:
        if inst.lower() in nl or nl in inst.lower():
            return inst
    return None

# ── Rutas ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html",
        instituciones=INSTITUCIONES, estados=ESTADOS, meses=MESES)

# ── API Grupos ────────────────────────────────────────────────────────────────
@app.route("/api/grupos")
def get_grupos():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT g.id, g.nombre,
                   COUNT(e.id) as n_empresas
            FROM grupos g
            LEFT JOIN empresas e ON e.grupo_id = g.id
            GROUP BY g.id ORDER BY g.nombre""").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/grupos", methods=["POST"])
def crear_grupo():
    d = request.json
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO grupos(nombre) VALUES(?)", (d["nombre"],))
        return jsonify({"id": cur.lastrowid, "nombre": d["nombre"]}), 201

@app.route("/api/grupos/<int:gid>", methods=["PUT"])
def editar_grupo(gid):
    d = request.json
    with get_conn() as conn:
        conn.execute("UPDATE grupos SET nombre=? WHERE id=?", (d["nombre"], gid))
        return jsonify({"id": gid, "nombre": d["nombre"]})

@app.route("/api/grupos/<int:gid>", methods=["DELETE"])
def eliminar_grupo(gid):
    with get_conn() as conn:
        conn.execute("DELETE FROM grupos WHERE id=?", (gid,))
        return jsonify({"ok": True})

@app.route("/api/grupos/<int:gid>/empresas_list")
def get_empresas_grupo(gid):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,nombre,rut FROM empresas WHERE grupo_id=? ORDER BY nombre",
            (gid,)).fetchall()
        return jsonify([dict(r) for r in rows])

# ── API Empresas ──────────────────────────────────────────────────────────────
@app.route("/api/grupos/<int:gid>/empresas", methods=["POST"])
def crear_empresa(gid):
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
def get_empresa(eid):
    with get_conn() as conn:
        emp = row_to_dict(conn.execute(
            "SELECT * FROM empresas WHERE id=?", (eid,)).fetchone())
        if not emp: return jsonify({"error":"Not found"}), 404
        certs = conn.execute(
            "SELECT * FROM certificados WHERE empresa_id=? ORDER BY id",
            (eid,)).fetchall()
        emp["certificados"] = []
        for c in certs:
            cd = dict(c)
            cd["sin_deuda"]     = bool(cd["sin_deuda"])
            cd["sin_afiliados"] = bool(cd["sin_afiliados"])
            emp["certificados"].append(cd)
        return jsonify(emp)

@app.route("/api/empresas/<int:eid>", methods=["PUT"])
def editar_empresa(eid):
    d = request.json
    with get_conn() as conn:
        conn.execute(
            "UPDATE empresas SET nombre=?,rut=?,razon_social=? WHERE id=?",
            (d.get("nombre"), d.get("rut",""), d.get("razon_social",""), eid))
        return jsonify({"ok": True})

@app.route("/api/empresas/<int:eid>", methods=["DELETE"])
def eliminar_empresa(eid):
    with get_conn() as conn:
        conn.execute("DELETE FROM empresas WHERE id=?", (eid,))
        return jsonify({"ok": True})

# ── API Certificados ──────────────────────────────────────────────────────────
@app.route("/api/empresas/<int:eid>/certificados", methods=["POST"])
def crear_cert(eid):
    d = request.json
    with get_conn() as conn:
        cur = conn.execute("""INSERT INTO certificados
            (empresa_id,institucion,tipo,categoria,estado,mes,anio,
             folio,notas,adjunto,sin_deuda,sin_afiliados)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, d.get("institucion",""), d.get("tipo","Certificado de deuda"),
             d.get("categoria",""), d.get("estado","Pendiente"),
             d.get("mes",""), d.get("anio",""), d.get("folio",""),
             d.get("notas",""), d.get("adjunto",""),
             1 if d.get("sin_deuda") else 0,
             1 if d.get("sin_afiliados") else 0))
        return jsonify({"id": cur.lastrowid}), 201

@app.route("/api/certificados/<int:cid>", methods=["PUT"])
def editar_cert(cid):
    d = request.json
    with get_conn() as conn:
        fields = []
        vals   = []
        for k in ["institucion","tipo","categoria","estado","mes","anio","folio","notas","adjunto"]:
            if k in d:
                fields.append(f"{k}=?")
                vals.append(d[k])
        if "sin_deuda" in d:
            fields.append("sin_deuda=?")
            vals.append(1 if d["sin_deuda"] else 0)
        if "sin_afiliados" in d:
            fields.append("sin_afiliados=?")
            vals.append(1 if d["sin_afiliados"] else 0)
        if fields:
            vals.append(cid)
            conn.execute(f"UPDATE certificados SET {','.join(fields)} WHERE id=?", vals)
        return jsonify({"ok": True})

@app.route("/api/certificados/<int:cid>", methods=["DELETE"])
def eliminar_cert(cid):
    with get_conn() as conn:
        conn.execute("DELETE FROM certificados WHERE id=?", (cid,))
        return jsonify({"ok": True})

@app.route("/api/certificados/<int:cid>/adjunto", methods=["POST"])
def upload_adjunto(cid):
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file"}), 400
    fname = f"{cid}_{f.filename}"
    f.save(os.path.join(ADJUNTOS, fname))
    with get_conn() as conn:
        conn.execute("UPDATE certificados SET adjunto=? WHERE id=?", (fname, cid))
    return jsonify({"adjunto": fname})

@app.route("/adjuntos/<path:fname>")
def ver_adjunto(fname):
    return send_from_directory(ADJUNTOS, fname)

# ── Importar Excel + PDFs ─────────────────────────────────────────────────────
@app.route("/api/importar", methods=["POST"])
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
            # Buscar/crear grupo
            grp_row = conn.execute(
                "SELECT id FROM grupos WHERE UPPER(nombre)=UPPER(?)",
                (grp_nombre,)).fetchone()
            if grp_row:
                gid = grp_row["id"]
            else:
                cur = conn.execute("INSERT INTO grupos(nombre) VALUES(?)", (grp_nombre,))
                gid = cur.lastrowid
            # Buscar/crear empresa
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
            # Guardar PDF
            dest = f"{eid}_{pdf.filename}"
            pdf.save(os.path.join(ADJUNTOS, dest))
            # Marcar certificado
            cert_row = conn.execute(
                "SELECT id FROM certificados WHERE empresa_id=? AND LOWER(institucion) LIKE ?",
                (eid, f"%{inst_n.lower()}%")).fetchone()
            if cert_row:
                conn.execute(
                    "UPDATE certificados SET estado='Obtenido',adjunto=? WHERE id=?",
                    (dest, cert_row["id"]))
                actualizadas += 1
            else:
                no_proc.append(f"{fname} → certificado '{inst_n}' no encontrado")
    return jsonify({"creadas":creadas,"actualizadas":actualizadas,"no_procesados":no_proc})

# ── Migrar JSON ───────────────────────────────────────────────────────────────
@app.route("/api/migrar", methods=["POST"])
def migrar_json():
    f = request.files.get("json")
    if not f: return jsonify({"error":"Falta JSON"}), 400
    data = json.load(f)
    emp_count = 0; cert_count = 0
    with get_conn() as conn:
        for g_d in data.get("grupos", []):
            grp = conn.execute("SELECT id FROM grupos WHERE UPPER(nombre)=UPPER(?)",
                               (g_d["nombre"],)).fetchone()
            if grp:
                gid = grp["id"]
            else:
                cur = conn.execute("INSERT INTO grupos(nombre) VALUES(?)", (g_d["nombre"],))
                gid = cur.lastrowid
            for e_d in g_d.get("empresas", []):
                emp = conn.execute(
                    "SELECT id FROM empresas WHERE grupo_id=? AND rut=?",
                    (gid, e_d.get("rut",""))).fetchone()
                if emp:
                    eid = emp["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO empresas(grupo_id,nombre,rut,razon_social) VALUES(?,?,?,?)",
                        (gid, e_d["nombre"], e_d.get("rut",""), e_d.get("razon_social","")))
                    eid = cur.lastrowid
                    emp_count += 1
                for c_d in e_d.get("certificados", []):
                    ex = conn.execute(
                        "SELECT id FROM certificados WHERE empresa_id=? AND institucion=?",
                        (eid, c_d["institucion"])).fetchone()
                    if ex:
                        conn.execute("""UPDATE certificados SET
                            estado=?,mes=?,anio=?,notas=?,
                            sin_deuda=?,sin_afiliados=? WHERE id=?""",
                            (c_d.get("estado","Pendiente"),
                             c_d.get("mes",""), c_d.get("anio",""),
                             c_d.get("notas",""),
                             1 if c_d.get("sin_deuda") else 0,
                             1 if c_d.get("sin_afiliados") else 0,
                             ex["id"]))
                    else:
                        conn.execute("""INSERT INTO certificados
                            (empresa_id,institucion,tipo,categoria,estado,mes,anio,
                             folio,notas,adjunto,sin_deuda,sin_afiliados)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (eid, c_d.get("institucion",""),
                             c_d.get("tipo","Certificado de deuda"),
                             c_d.get("categoria",""),
                             c_d.get("estado","Pendiente"),
                             c_d.get("mes",""), c_d.get("anio",""),
                             c_d.get("folio",""), c_d.get("notas",""),
                             c_d.get("adjunto",""),
                             1 if c_d.get("sin_deuda") else 0,
                             1 if c_d.get("sin_afiliados") else 0))
                    cert_count += 1
    return jsonify({"empresas": emp_count, "certificados": cert_count})

# ── Reportes ──────────────────────────────────────────────────────────────────
@app.route("/reporte/empresa/<int:eid>")
def reporte_empresa(eid):
    with get_conn() as conn:
        emp   = row_to_dict(conn.execute("SELECT * FROM empresas WHERE id=?", (eid,)).fetchone())
        grupo = row_to_dict(conn.execute("SELECT * FROM grupos WHERE id=?", (emp["grupo_id"],)).fetchone())
        certs = [dict(r) for r in conn.execute(
            "SELECT * FROM certificados WHERE empresa_id=? ORDER BY id", (eid,)).fetchall()]
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
def reporte_grupo(gid):
    with get_conn() as conn:
        grupo    = row_to_dict(conn.execute("SELECT * FROM grupos WHERE id=?", (gid,)).fetchone())
        emp_rows = conn.execute("SELECT * FROM empresas WHERE grupo_id=? ORDER BY nombre", (gid,)).fetchall()
        empresas = []
        for e in emp_rows:
            ed = dict(e)
            certs = [dict(r) for r in conn.execute(
                "SELECT * FROM certificados WHERE empresa_id=? ORDER BY id", (e["id"],)).fetchall()]
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
