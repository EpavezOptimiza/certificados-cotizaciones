"""Conexión a BASE MADRE.xlsx (SharePoint) — lectura automática de la hoja CLIENTES.

El enlace compartido del Excel se configura en la variable de entorno
BASE_MADRE_URL (NO va en el código: el repositorio es público).
Acepta el share-link tal cual lo entrega SharePoint y lo convierte
internamente al formato de descarga directa.
"""

import io
import os
import time
import threading
import http.cookiejar
import urllib.request

import openpyxl

# Cache en memoria: se refresca solo si pasaron REFRESCO_SEG segundos
REFRESCO_SEG = 600  # 10 minutos
_CACHE = {"filas": None, "columnas": None, "ts": 0, "error": None}
_LOCK = threading.Lock()


def url_guardada():
    """Enlace del Excel: variable de entorno o, si no existe, la base de datos
    (tabla app_config — se pega desde la página /base_madre)."""
    url = os.environ.get("BASE_MADRE_URL", "").strip()
    if url:
        return url
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT valor FROM app_config WHERE clave='base_madre_url'").fetchone()
            return (row["valor"] or "").strip() if row else ""
    except Exception:
        return ""


def guardar_url(url):
    """Guarda el enlace en la base de datos y limpia el cache."""
    from database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_config(clave, valor) VALUES('base_madre_url', ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (url.strip(),))
    with _LOCK:
        _CACHE.update({"filas": None, "columnas": None, "ts": 0, "error": None})


def _url_descarga():
    """Convierte el share-link de SharePoint al endpoint de descarga directa.

    https://<tenant>-my.sharepoint.com/:x:/g/personal/<usuario>/<TOKEN>?e=...
      → https://<tenant>-my.sharepoint.com/personal/<usuario>/_layouts/15/download.aspx?share=<TOKEN>
    """
    url = url_guardada()
    if not url:
        return None
    if "download.aspx" in url:
        return url
    try:
        if "/:x:/g/personal/" in url:
            dominio = url.split("/:x:/")[0]
            resto = url.split("/:x:/g/personal/")[1]
            usuario, token = resto.split("/", 1)
            token = token.split("?")[0]
            return f"{dominio}/personal/{usuario}/_layouts/15/download.aspx?share={token}"
    except Exception:
        pass
    return url


def _descargar():
    url = _url_descarga()
    if not url:
        raise Exception("Falta pegar el enlace del Excel (usa el recuadro de configuración)")
    # SharePoint exige conservar cookies entre las redirecciones del enlace anónimo
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]
    with op.open(url, timeout=90) as r:
        data = r.read()
    if data[:2] != b"PK":
        raise Exception("SharePoint no devolvió el Excel — revisa que el enlace siga "
                        "vigente y compartido como 'Cualquier persona puede ver'")
    return data


def _parsear(data):
    """Lee la hoja CLIENTES (o la primera) → (columnas, filas como dicts)."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = None
        for nombre in wb.sheetnames:
            if "cliente" in nombre.lower():
                ws = wb[nombre]
                break
        if ws is None:
            ws = wb.active
        try:
            ws.reset_dimensions()
        except Exception:
            pass

        import datetime as _dt

        def _fmt(v):
            if v is None:
                return ""
            if isinstance(v, (_dt.datetime, _dt.date)):
                return v.strftime("%d/%m/%Y")
            if isinstance(v, float) and v.is_integer():
                return str(int(v))
            return str(v).strip()

        gen = ws.iter_rows(values_only=True)
        headers = []
        for row in gen:
            headers = [str(h).strip() if h else "" for h in row]
            break
        cols = [i for i, h in enumerate(headers) if h]

        filas = []
        for row in gen:
            if not any(v is not None and str(v).strip() for v in row):
                continue
            d = {headers[i]: (_fmt(row[i]) if i < len(row) else "") for i in cols}
            if any(d.values()):
                filas.append(d)
        return [headers[i] for i in cols], filas
    finally:
        wb.close()


def obtener_clientes(forzar=False):
    """Devuelve (columnas, filas, ts_ultima_lectura, error).

    Sirve desde cache si la última lectura tiene menos de REFRESCO_SEG.
    Si la descarga falla pero hay datos previos en cache, sigue sirviendo
    los datos antiguos y reporta el error.
    """
    with _LOCK:
        fresco = _CACHE["filas"] is not None and (time.time() - _CACHE["ts"]) < REFRESCO_SEG
        if fresco and not forzar:
            return _CACHE["columnas"], _CACHE["filas"], _CACHE["ts"], _CACHE["error"]
        try:
            data = _descargar()
            columnas, filas = _parsear(data)
            _CACHE.update({"filas": filas, "columnas": columnas,
                           "ts": time.time(), "error": None})
        except Exception as e:
            _CACHE["error"] = str(e)
        return _CACHE["columnas"], _CACHE["filas"], _CACHE["ts"], _CACHE["error"]
