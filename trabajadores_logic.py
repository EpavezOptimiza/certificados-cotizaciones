"""Actualización mensual del N° de trabajadores por empresa.

Recorre las empresas VIGENTES de BASE MADRE que tengan credenciales
PreviRed, descarga la planilla pagada del organismo de accidentes
(ISL / ACHS / Mutual CChC / IST) del período indicado, extrae el
número de trabajadores y ELIMINA el PDF de inmediato (no ocupa espacio:
solo queda el dato en la base de datos).

Reutiliza la lógica Playwright de previred_logic.py.
"""

import os
import re
import time
import unicodedata

from playwright.sync_api import sync_playwright

from previred_logic import (
    hacer_login, ir_a_empresa, ir_a_planillas_pagadas,
    obtener_nominas, MESES_NOMBRE, _select_anio, _click_texto,
)

# Palabras que identifican al organismo de accidentes en Previred
_ORGANISMOS = ("MUTUAL", "ACHS", "ASOCIACION CHILENA", "ASOCIACIÓN CHILENA",
               "ISL", "INSTITUTO DE SEGURIDAD", "IST", "SEGURIDAD DEL TRABAJO")


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.strip().lower()


def extraer_clientes_previred(columnas, filas):
    """Desde BASE MADRE: empresas vigentes → (con_credenciales, sin_credenciales)."""
    def col(*terms):
        for c in (columnas or []):
            cn = _norm(c)
            if all(t in cn for t in terms):
                return c
        return None

    c_rut, c_razon = col("rut"), col("razon", "social")
    c_est          = col("estatus", "cliente") or col("estatus")
    c_usr, c_pwd   = col("usuario", "previred"), col("clave", "previred")

    con, sin = [], []
    for f in (filas or []):
        if "vigente" not in _norm(f.get(c_est, "")):
            continue
        rut   = (f.get(c_rut) or "").strip()
        razon = (f.get(c_razon) or "").strip()
        usr   = (f.get(c_usr) or "").strip() if c_usr else ""
        pwd   = (f.get(c_pwd) or "").strip() if c_pwd else ""
        if not rut:
            continue
        if usr and pwd and _norm(usr) not in ("pendiente", "n/a", "na", "-"):
            con.append({"rut": rut, "razon": razon, "usuario": usr, "clave": pwd})
        else:
            sin.append({"rut": rut, "razon": razon})
    return con, sin


def extraer_n_trabajadores(ruta_pdf):
    """Busca el N° de trabajadores en el PDF de la planilla (varios formatos)."""
    import pdfplumber
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            texto = "\n".join((p.extract_text() or "") for p in pdf.pages[:4])
    except Exception:
        return None
    t = _norm(texto)
    patrones = [
        r'n[°º]?\s*(?:total\s*)?(?:de\s*)?trabajadores(?:\s*(?:del|en\s*el)\s*periodo)?\s*[:\.]?\s*(\d{1,6})',
        r'total\s+(?:de\s+)?trabajadores\s*[:\.]?\s*(\d{1,6})',
        r'cantidad\s+(?:de\s+)?trabajadores\s*[:\.]?\s*(\d{1,6})',
        r'trabajadores\s+informados\s*[:\.]?\s*(\d{1,6})',
        r'trabajadores\s*[:\.]\s*(\d{1,6})',
    ]
    for pat in patrones:
        m = re.search(pat, t)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    return None


def _buscar_planilla_organismo(page, mes, anio, nombre_nomina, log):
    """Selecciona la nómina y filtra por tipo de institución del organismo
    de accidentes (Mutual/ISL). Si no existe el tipo, usa 'Todas'."""
    page.wait_for_selector("#mesR0", timeout=15000)
    page.select_option("#mesR0", str(mes).zfill(2))
    time.sleep(1)
    _select_anio(page, anio)
    time.sleep(1)
    page.wait_for_selector("#combo_nominas", timeout=15000)
    opciones = page.evaluate("""() => {
        var sel = document.getElementById('combo_nominas');
        return Array.from(sel.options).map(o => o.text.trim());
    }""")
    objetivo = next((o for o in opciones if o == nombre_nomina.strip()), None) or \
               next((o for o in opciones if nombre_nomina.strip() in o), None)
    if not objetivo:
        return False
    page.select_option("#combo_nominas", label=objetivo)
    time.sleep(1)

    # Tipo de institución: buscar Mutual/ISL; si no, 'Todas'
    try:
        page.wait_for_selector("#combo_tipo_institucion", timeout=8000)
        tipos = page.evaluate("""() => {
            var sel = document.getElementById('combo_tipo_institucion');
            return Array.from(sel.options).map(o => o.text);
        }""")
        elegido = None
        for t in tipos:
            tn = _norm(t)
            if "mutual" in tn or "isl" in tn or "seguridad" in tn or "accidente" in tn:
                elegido = t
                break
        if not elegido:
            elegido = next((t for t in tipos if "todas" in _norm(t) or "todos" in _norm(t)), None)
        if elegido:
            page.select_option("#combo_tipo_institucion", label=elegido)
            log(f"  Tipo institución: {elegido.strip()}", "info")
        time.sleep(2)
        try:
            page.wait_for_function("""() => {
                var sel = document.getElementById('combo_instituciones');
                return sel && sel.options.length >= 1;
            }""", timeout=8000)
            inst = page.evaluate("""() => {
                var sel = document.getElementById('combo_instituciones');
                return Array.from(sel.options).map(o => o.text);
            }""")
            if "Todas las Instituciones" in inst:
                page.select_option("#combo_instituciones", label="Todas las Instituciones")
        except Exception:
            pass
        time.sleep(1)
    except Exception:
        time.sleep(1)

    # Cerrar dialogs flotantes y buscar
    try:
        page.evaluate("""() => {
            document.querySelectorAll('.ui-dialog').forEach(function(d){
                var btn = d.querySelector('button'); if (btn) btn.click();
            });
        }""")
        time.sleep(1)
    except Exception:
        pass
    page.evaluate("() => document.getElementById('buscar').click()")
    time.sleep(3)

    try:
        cuerpo = page.inner_text("body")
        if "no est" in cuerpo.lower() and "timbradas" in cuerpo.lower():
            return False
    except Exception:
        pass
    return True


def _descargar_pdf_organismo(page, carpeta_temp, log):
    """En los resultados, ubica el ícono de planilla del organismo de accidentes,
    descarga SOLO ese PDF y devuelve (ruta_pdf, nombre_organismo) o (None, None)."""
    ids_info = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('img[src*="planillas.gif"]'))
            .map(img => img.id || '');
    }""")
    if not ids_info:
        return None, None

    idx_org, nombre_org = None, None
    for i, img_id in enumerate(ids_info):
        nombre_inst = img_id.split('#')[-1] if '#' in img_id else img_id
        n = _norm(nombre_inst).upper()
        if any(_norm(k).upper() in n for k in _ORGANISMOS):
            idx_org, nombre_org = i, nombre_inst.strip()
            break
    if idx_org is None:
        # Diagnóstico: qué instituciones había
        insts = [i.split('#')[-1] for i in ids_info if i]
        log(f"  Sin organismo de accidentes entre: {insts}", "warn")
        return None, None

    log(f"  Organismo detectado: {nombre_org}", "info")
    try:
        page.evaluate(f"document.querySelectorAll('img[src*=\"planillas.gif\"]')[{idx_org}].click()")
        page.wait_for_selector("#aceptar_modal", state="visible", timeout=8000)
    except Exception:
        log("  Modal de impresión no apareció", "warn")
        return None, nombre_org

    try:
        radio = page.locator("input[type='radio'][value*='total']").first
        if radio.count() > 0 and not radio.is_checked():
            radio.click()
        page.wait_for_timeout(500)
    except Exception:
        pass

    ruta = os.path.join(carpeta_temp, "planilla_organismo.pdf")
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except Exception:
        pass

    try:
        with page.expect_download(timeout=25000) as dl_info:
            page.click("#aceptar_modal")
        dl_info.value.save_as(ruta)
    except Exception as e:
        log(f"  Descarga falló: {e.__class__.__name__}", "warn")
        return None, nombre_org
    finally:
        try:
            cerrar = page.locator("button:has-text('Cerrar')").first
            if cerrar.is_visible():
                cerrar.click()
        except Exception:
            pass

    return ruta, nombre_org


def actualizar_trabajadores(clientes, mes, anio, carpeta_temp, log, guardar):
    """Proceso principal. `clientes`: [{rut, razon, usuario, clave}].
    `guardar(rut, razon, organismo, n, estado)` persiste cada resultado.
    El PDF se elimina apenas se extrae el dato (no se acumula espacio)."""
    os.makedirs(carpeta_temp, exist_ok=True)
    periodo_txt = f"{MESES_NOMBRE.get(mes, mes)} {anio}"
    log(f"Período a consultar: {periodo_txt} — {len(clientes)} empresa(s)", "info")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            downloads_path=carpeta_temp,
        )
        try:
            for i, cli in enumerate(clientes, 1):
                rut, razon = cli["rut"], cli.get("razon", "")
                log(f"[{i}/{len(clientes)}] {razon or rut}...", "info")
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(45000)
                page.set_default_navigation_timeout(45000)
                try:
                    hacer_login(page, cli["usuario"], cli["clave"], log)
                    ir_a_empresa(page, rut, log, razon)
                    ir_a_planillas_pagadas(page, log)

                    nominas = obtener_nominas(page, mes, anio)
                    if not nominas:
                        log("  Sin nóminas en el período", "warn")
                        guardar(rut, razon, "", None, "sin_planilla")
                        continue

                    n_total, organismo_usado, con_planilla = None, "", False
                    for nombre_nomina in nominas:
                        if not _buscar_planilla_organismo(page, mes, anio, nombre_nomina, log):
                            continue
                        ruta_pdf, organismo = _descargar_pdf_organismo(page, carpeta_temp, log)
                        if not ruta_pdf:
                            continue
                        con_planilla = True
                        n = extraer_n_trabajadores(ruta_pdf)
                        # Eliminar el PDF de inmediato — solo nos quedamos con el dato
                        try:
                            os.remove(ruta_pdf)
                        except Exception:
                            pass
                        if n is not None:
                            n_total = (n_total or 0) + n
                            organismo_usado = organismo or organismo_usado
                            log(f"  ✓ {organismo}: {n} trabajadores", "ok")
                        else:
                            organismo_usado = organismo or organismo_usado
                            log("  PDF leído pero no se encontró el N° de trabajadores", "warn")
                        # Volver a búsqueda para la siguiente nómina
                        try:
                            _click_texto(page, "Nueva búsqueda", timeout=4000)
                            time.sleep(2)
                        except Exception:
                            pass

                    if n_total is not None:
                        guardar(rut, razon, organismo_usado, n_total, "ok")
                        log(f"  TOTAL {rut}: {n_total} trabajadores ({organismo_usado})", "ok")
                    elif con_planilla:
                        guardar(rut, razon, organismo_usado, None, "sin_dato_en_pdf")
                    else:
                        guardar(rut, razon, "", None, "sin_planilla_organismo")

                except Exception as e:
                    msg = f"{type(e).__name__}: {str(e)[:120]}"
                    log(f"  Error: {msg}", "err")
                    guardar(rut, razon, "", None, f"error: {msg}")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        finally:
            browser.close()

    log("Proceso terminado", "ok")
