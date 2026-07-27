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
    hacer_login, esta_en_login, MESES_NOMBRE, _select_anio, URL_LOGIN,
)


# ── Navegación rápida (sin esperas fijas) ──────────────────────────────────────

def _poll(page, js, max_seg=8.0, paso=0.25):
    """Espera hasta que una condición JS sea verdadera. Devuelve True/False."""
    fin = time.time() + max_seg
    while time.time() < fin:
        try:
            if page.evaluate(js):
                return True
        except Exception:
            pass
        time.sleep(paso)
    return False


# Click en un ítem de menú buscando en TODOS los tags a la vez (el _click_texto
# de previred_logic prueba tag por tag con timeouts de 15s: ~20s perdidos por paso)
_JS_CLICK_MENU = """(t) => {
    const objetivo = t.toLowerCase();
    const els = document.querySelectorAll('a, span, li, button, td, div');
    let mejor = null;
    for (const el of els) {
        if (el.offsetParent === null) continue;
        const txt = (el.innerText || '').trim().toLowerCase();
        if (!txt) continue;
        if (txt === objetivo) { el.click(); return true; }
        if (txt.includes(objetivo) && txt.length <= objetivo.length + 20) {
            if (!mejor || txt.length < (mejor.innerText || '').trim().length) mejor = el;
        }
    }
    if (mejor) { mejor.click(); return true; }
    return false;
}"""

_JS_HAY_MES = "() => !!document.querySelector('#mesR0')"


def _ir_a_planillas_rapido(page, log):
    """Menú Remuneraciones → Imprimir Documentos → Planillas Pagadas.
    Usa clicks JS y polling: ~5s en vez de ~25s."""
    if page.evaluate(_JS_HAY_MES):
        return True
    for intento in range(2):
        for etiqueta, siguiente in (
            ("Remuneraciones",     "imprimir documentos"),
            ("Imprimir Documentos", "planillas pagadas"),
            ("Planillas Pagadas",   None),
        ):
            try:
                page.evaluate(_JS_CLICK_MENU, etiqueta)
            except Exception:
                pass
            if siguiente:
                # Esperar a que aparezca el siguiente nivel del menú
                _poll(page, "(() => { const t = '%s';"
                            " return Array.from(document.querySelectorAll('a,span,li,button,td,div'))"
                            " .some(e => e.offsetParent !== null &&"
                            " (e.innerText||'').trim().toLowerCase().includes(t)); })" % siguiente,
                      max_seg=4)
            else:
                if _poll(page, _JS_HAY_MES, max_seg=12):
                    return True
        if intento == 0:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:
                pass
    return page.evaluate(_JS_HAY_MES)


def _hay_nominas_cargadas(page):
    return _poll(page, """() => {
        const sel = document.getElementById('combo_nominas');
        return !!sel && Array.from(sel.options).some(o => o.value &&
            !o.text.toLowerCase().includes('seleccione'));
    }""", max_seg=6)


def _reset_busqueda(page, log):
    """Vuelve al formulario de búsqueda dejando el combo de nóminas utilizable.
    Sin esto, la 2ª nómina en adelante no se encontraba y se saltaba en silencio."""
    try:
        page.evaluate(_JS_CLICK_MENU, "Nueva búsqueda")
    except Exception:
        pass
    if _poll(page, "() => !!document.querySelector('#combo_nominas')", max_seg=5):
        return True
    # Respaldo: rehacer el camino del menú hasta Planillas Pagadas
    try:
        return _ir_a_planillas_rapido(page, log)
    except Exception:
        return False


def _nominas_rapido(page, mes, anio):
    """Nóminas del período, con polling en vez de sleeps fijos."""
    page.wait_for_selector("#mesR0", timeout=12000)
    page.select_option("#mesR0", str(mes).zfill(2))
    _select_anio(page, anio)
    # El combo se repuebla por AJAX: esperar a que tenga opciones reales
    _poll(page, """() => {
        const sel = document.getElementById('combo_nominas');
        if (!sel) return false;
        return Array.from(sel.options).some(o => o.value &&
            !o.text.toLowerCase().includes('seleccione'));
    }""", max_seg=5)
    try:
        return page.evaluate("""() => {
            const sel = document.getElementById('combo_nominas');
            if (!sel) return [];
            return Array.from(sel.options)
                .filter(o => o.value && !o.text.toLowerCase().includes('seleccione'))
                .map(o => o.text.trim());
        }""") or []
    except Exception:
        return []


def _menu_empresas_visible(page, espera=6):
    """True si el menú de empresas (li#empresa) está disponible en pantalla."""
    return _poll(page, "() => !!document.querySelector('li#empresa')",
                 max_seg=float(espera), paso=0.25)


def _volver_al_inicio(page, estado, usuario, clave, log):
    """Vuelve a donde está el menú de empresas (li#empresa).

    El portal es por sesión: navegar por URL desloguea. Por eso se intenta,
    en orden: (1) link Inicio/logo dentro de la página, (2) volver atrás en
    el historial, (3) URL guardada, (4) re-login como último recurso.
    """
    if _menu_empresas_visible(page, espera=0.5):
        return

    # 1. Link de inicio dentro del portal
    try:
        clickeado = page.evaluate("""() => {
            const cands = Array.from(document.querySelectorAll('a, li, span, img'));
            for (const el of cands) {
                if (el.offsetParent === null) continue;
                const t = ((el.innerText || '') + ' ' + (el.getAttribute('title') || '') + ' ' +
                           (el.getAttribute('alt') || '') + ' ' + (el.id || '')).toLowerCase();
                if (/\\binicio\\b|\\bhome\\b|portada|mis empresas|cambiar empresa/.test(t)) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        if clickeado and _menu_empresas_visible(page, espera=5):
            return
    except Exception:
        pass

    # 2. Volver atrás en el historial hasta encontrar el menú
    for _ in range(3):
        try:
            page.go_back(wait_until="domcontentloaded", timeout=8000)
        except Exception:
            break
        if esta_en_login(page):
            break
        if _menu_empresas_visible(page, espera=2):
            return

    # 3. URL guardada del portal
    home = estado.get("home")
    if home:
        try:
            page.goto(home, wait_until="domcontentloaded", timeout=20000)
            if not esta_en_login(page) and _menu_empresas_visible(page, espera=4):
                return
        except Exception:
            pass

    # 4. Último recurso: re-login
    log("  Reabriendo sesión...", "warn")
    hacer_login(page, usuario, clave, log)
    estado["home"] = page.url


# Lee el N° de trabajadores directamente de la tabla de resultados (sin PDF).
# Devuelve {n, fila, cabeceras} para poder diagnosticar si no lo encuentra.
_JS_TABLA_TRABAJADORES = """(orgKeys) => {
    const norm = s => (s || '').toString().normalize('NFD')
        .replace(/[\\u0300-\\u036f]/g, '').trim().toLowerCase();
    const out = {n: null, fila: '', cabeceras: []};
    for (const tabla of document.querySelectorAll('table')) {
        const filas = Array.from(tabla.querySelectorAll('tr'));
        if (filas.length < 2) continue;
        const heads = Array.from(filas[0].querySelectorAll('th, td')).map(c => norm(c.innerText));
        let idx = heads.findIndex(h => h.includes('trabajador') || h.includes('afiliado') ||
                                       h.includes('cotizante'));
        if (idx < 0) continue;
        if (!out.cabeceras.length) out.cabeceras = heads.filter(Boolean).slice(0, 12);
        for (const fila of filas.slice(1)) {
            const celdas = Array.from(fila.querySelectorAll('td'));
            if (celdas.length <= idx) continue;
            const texto = norm(fila.innerText);
            const esOrg = orgKeys.some(k => texto.includes(norm(k)));
            if (!esOrg) continue;
            const val = (celdas[idx].innerText || '').replace(/[^0-9]/g, '');
            if (val) {
                out.n = parseInt(val, 10);
                out.fila = fila.innerText.replace(/\\s+/g, ' ').trim().slice(0, 120);
                return out;
            }
        }
    }
    return out;
}"""


def _ids_empresa(page, rut, log):
    """Abre el menú de empresas y devuelve los ids de botón para ese RUT.
    Si no aparece, intenta filtrar por RUT y entrega diagnóstico real."""
    rut_num = rut.replace(".", "").split("-")[0]
    patron = f"empresa#{rut_num}#"

    page.wait_for_selector("li#empresa", timeout=20000)
    page.click("li#empresa")

    # Esperar a que cargue CUALQUIER empresa (la lista es asíncrona)
    _poll(page, "() => document.querySelectorAll('[id^=\"empresa#\"]').length > 0", max_seg=12)
    try:
        total = page.evaluate("() => document.querySelectorAll('[id^=\"empresa#\"]').length")
    except Exception:
        total = 0

    def _ids():
        try:
            return page.evaluate(
                "(p) => Array.from(document.querySelectorAll('[id^=\"' + p + '\"]')).map(e => e.id)",
                patron)
        except Exception:
            return []

    ids = _ids()
    if ids:
        return ids

    # No está a la vista: probar el buscador del listado (si existe)
    try:
        escrito = page.evaluate("""(rutNum) => {
            var inputs = Array.from(document.querySelectorAll('input[type=text], input:not([type])'))
                .filter(function(el){ return el.offsetParent !== null; });
            if (!inputs.length) return false;
            var inp = inputs[0];
            inp.focus(); inp.value = rutNum;
            inp.dispatchEvent(new Event('input',  {bubbles:true}));
            inp.dispatchEvent(new Event('keyup',  {bubbles:true}));
            inp.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
        }""", rut_num)
        if escrito:
            time.sleep(2)
            ids = _ids()
            if ids:
                log("  (empresa encontrada usando el buscador del listado)", "info")
                return ids
    except Exception:
        pass

    # Diagnóstico: qué hay realmente en pantalla
    try:
        muestra = page.evaluate(
            "() => Array.from(document.querySelectorAll('[id^=\"empresa#\"]')).slice(0,5).map(e => e.id)")
    except Exception:
        muestra = []
    log(f"  RUT no está en la cuenta PreviRed (empresas visibles: {total}; ej: {muestra})", "warn")
    return []

# Palabras que identifican al organismo de accidentes en Previred
_ORGANISMOS = ("MUTUAL", "ACHS", "ASOCIACION CHILENA", "ASOCIACIÓN CHILENA",
               "ISL", "INSTITUTO DE SEGURIDAD", "IST", "SEGURIDAD DEL TRABAJO")


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.strip().lower()


def extraer_vigentes(columnas, filas):
    """Desde BASE MADRE: todas las empresas VIGENTES → [{rut, razon}].
    Se entra a todas con la misma cuenta maestra de PreviRed."""
    def col(*terms):
        for c in (columnas or []):
            cn = _norm(c)
            if all(t in cn for t in terms):
                return c
        return None

    c_rut, c_razon = col("rut"), col("razon", "social")
    c_est          = col("estatus", "cliente") or col("estatus")

    out, vistos = [], set()
    for f in (filas or []):
        if "vigente" not in _norm(f.get(c_est, "")):
            continue
        rut = (f.get(c_rut) or "").strip()
        if not rut or rut in vistos:
            continue
        vistos.add(rut)
        out.append({"rut": rut, "razon": (f.get(c_razon) or "").strip()})
    return out


# Etiquetas del dato en la planilla. En las planillas de mutual/ISL el
# encabezado es "N° de Afiliados Informados" y el valor va en la CELDA DE ABAJO.
_ETIQUETAS_N = (
    "afiliados informados", "trabajadores informados",
    "n de afiliados", "numero de afiliados", "total de afiliados", "total afiliados",
    "n de trabajadores", "numero de trabajadores", "total de trabajadores",
    "total trabajadores", "cantidad de trabajadores", "cantidad de afiliados",
)


def _es_etiqueta(txt):
    t = _norm(txt).replace("°", "").replace("º", "").replace(".", "")
    return any(e in t for e in _ETIQUETAS_N)


def _num(txt):
    """Convierte '48', '1.234' → int; None si no es un número limpio."""
    s = re.sub(r"[^\d]", "", (txt or ""))
    if not s or len(s) > 6:
        return None
    try:
        return int(s)
    except Exception:
        return None


def extraer_n_trabajadores(ruta_pdf):
    """N° de trabajadores de la planilla: busca 'N° de Afiliados Informados'
    y toma el valor de la celda de abajo (o de la derecha, o de la línea
    siguiente según cómo se extraiga el PDF)."""
    import pdfplumber
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            paginas = pdf.pages[:3]

            # 1) Por tablas: la etiqueta y el valor están en celdas distintas
            for pag in paginas:
                try:
                    tablas = pag.extract_tables() or []
                except Exception:
                    tablas = []
                for tabla in tablas:
                    for i, fila in enumerate(tabla or []):
                        for j, celda in enumerate(fila or []):
                            if not celda or not _es_etiqueta(celda):
                                continue
                            # a) celda de abajo (caso de la planilla mutual)
                            if i + 1 < len(tabla):
                                abajo = (tabla[i + 1] or [])
                                if j < len(abajo):
                                    n = _num(abajo[j])
                                    if n is not None:
                                        return n
                            # b) celda a la derecha
                            if j + 1 < len(fila):
                                n = _num(fila[j + 1])
                                if n is not None:
                                    return n
                            # c) número dentro de la misma celda
                            n = _num(re.sub(r"(?i).*informados", "", celda))
                            if n is not None:
                                return n

            # 2) Por texto: etiqueta en una línea, valor en la línea siguiente
            texto = "\n".join((p.extract_text() or "") for p in paginas)
        lineas = [l.strip() for l in texto.split("\n")]
        for idx, linea in enumerate(lineas):
            if not _es_etiqueta(linea):
                continue
            # número en la misma línea, después de la etiqueta
            m = re.search(r"informados\D{0,15}(\d{1,6})", _norm(linea))
            if m:
                return int(m.group(1))
            # número al inicio de alguna de las líneas siguientes
            for sig in lineas[idx + 1: idx + 4]:
                m2 = re.match(r"^(\d{1,6})\b", sig.replace(".", ""))
                if m2:
                    return int(m2.group(1))
    except Exception:
        return None
    return None


def _buscar_planilla_organismo(page, mes, anio, nombre_nomina, log):
    """Selecciona la nómina y filtra por tipo de institución del organismo
    de accidentes (Mutual/ISL). Si no existe el tipo, usa 'Todas'."""
    page.wait_for_selector("#mesR0", timeout=15000)
    page.select_option("#mesR0", str(mes).zfill(2))
    _select_anio(page, anio)
    page.wait_for_selector("#combo_nominas", timeout=15000)
    _hay_nominas_cargadas(page)
    opciones = page.evaluate("""() => {
        var sel = document.getElementById('combo_nominas');
        return Array.from(sel.options).map(o => o.text.trim());
    }""")
    objetivo = next((o for o in opciones if o == nombre_nomina.strip()), None) or \
               next((o for o in opciones if nombre_nomina.strip() in o), None)
    if not objetivo:
        # Segundo intento: el combo pudo no haber terminado de recargarse
        _hay_nominas_cargadas(page)
        opciones = page.evaluate("""() => {
            var sel = document.getElementById('combo_nominas');
            return Array.from(sel.options).map(o => o.text.trim());
        }""")
        objetivo = next((o for o in opciones if o == nombre_nomina.strip()), None) or \
                   next((o for o in opciones if nombre_nomina.strip() in o), None)
    if not objetivo:
        log(f"    (nómina no está en el combo: {opciones[:6]})", "warn")
        return False
    page.select_option("#combo_nominas", label=objetivo)

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
        try:
            _poll(page, """() => {
                const sel = document.getElementById('combo_instituciones');
                return sel && sel.options.length >= 1;
            }""", max_seg=6)
            inst = page.evaluate("""() => {
                var sel = document.getElementById('combo_instituciones');
                return Array.from(sel.options).map(o => o.text);
            }""")
            if "Todas las Instituciones" in inst:
                page.select_option("#combo_instituciones", label="Todas las Instituciones")
        except Exception:
            pass
    except Exception:
        pass

    # Cerrar dialogs flotantes y buscar
    try:
        page.evaluate("""() => {
            document.querySelectorAll('.ui-dialog').forEach(function(d){
                var btn = d.querySelector('button'); if (btn) btn.click();
            });
        }""")
    except Exception:
        pass
    page.evaluate("() => document.getElementById('buscar').click()")

    # Esperar el resultado real. OJO: la página SIEMPRE tiene el enlace
    # "Fecha Planillas Timbradas", así que no sirve como señal de "sin datos".
    # Señal de resultados cargados: aparecen los botones Nueva Búsqueda /
    # Descargar Planillas Masivas, o los íconos de institución.
    estado_res = ""
    fin = time.time() + 12
    while time.time() < fin:
        try:
            estado_res = page.evaluate("""() => {
                const t = (document.body.innerText || '').toLowerCase();
                if (document.querySelectorAll('img[src*="planillas.gif"]').length) return 'ok';
                if (t.includes('nueva busqueda') || t.includes('nueva búsqueda') ||
                    t.includes('descargar planillas masivas')) return 'ok';
                if (t.includes('no existen planillas') || t.includes('no se encontraron') ||
                    t.includes('no hay planillas') ||
                    t.includes('no se registran') || t.includes('sin resultados')) return 'vacio';
                return '';
            }""")
        except Exception:
            estado_res = ""
        if estado_res:
            break
        time.sleep(0.3)

    if estado_res == "vacio":
        return False
    if not estado_res:
        return False
    return True


def _iconos_organismo(page, log):
    """Todos los íconos de planilla que corresponden al organismo de accidentes.
    Devuelve [(indice, nombre)] — puede haber más de uno por búsqueda."""
    try:
        ids_info = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img[src*="planillas.gif"]'))
                .map(img => img.id || '');
        }""")
    except Exception:
        return []
    if not ids_info:
        return []
    out = []
    for i, img_id in enumerate(ids_info):
        nombre_inst = img_id.split('#')[-1] if '#' in img_id else img_id
        n = _norm(nombre_inst).upper()
        if any(_norm(k).upper() in n for k in _ORGANISMOS):
            out.append((i, nombre_inst.strip()))
    if not out:
        insts = [i.split('#')[-1] for i in ids_info if i]
        log(f"    sin organismo de accidentes entre: {insts}", "warn")
    return out


# Tras expandir el ⊕, la fila muestra una subtabla con la columna "Ver Planillas"
# y un ícono de PDF: ESE es el que descarga. Devuelve los candidatos a clickear.
_JS_ICONOS_DESCARGA = """() => {
    const out = [];
    const els = document.querySelectorAll('img, a');
    for (let i = 0; i < els.length; i++) {
        const el = els[i];
        if (el.offsetParent === null) continue;
        const src = (el.getAttribute('src') || '').toLowerCase();
        const txt = ((el.getAttribute('alt') || '') + ' ' + (el.getAttribute('title') || '') + ' ' +
                     (el.getAttribute('onclick') || '') + ' ' + (el.id || '') + ' ' +
                     (el.className || '')).toLowerCase();
        if (src.includes('planillas.gif')) continue;          // ese es el expansor ⊕
        if (src.includes('pdf') || src.includes('acrobat') || src.includes('imprimir') ||
            txt.includes('pdf') || txt.includes('ver planilla') || txt.includes('imprimir') ||
            txt.includes('descarga')) {
            out.push({idx: i, desc: (el.tagName + ' ' + (src || txt)).slice(0, 60)});
        }
    }
    return out;
}"""

# Diagnóstico: qué apareció tras expandir la fila
_JS_DIAG_EXPANDIDO = """() => {
    return Array.from(document.querySelectorAll('img, a'))
        .filter(e => e.offsetParent !== null)
        .slice(0, 40)
        .map(e => (e.tagName + '|' + (e.getAttribute('src') || '') + '|' +
                   (e.id || '') + '|' + (e.innerText || '').trim()).slice(0, 70));
}"""


def _descargar_pdf_organismo(page, idx_org, nombre_org, carpeta_temp, log, estado=None):
    """Expande la fila del organismo (⊕) y descarga el PDF de 'Ver Planillas'.
    Devuelve (ruta_pdf, nombre_organismo)."""

    ruta = os.path.join(carpeta_temp, "planilla_organismo.pdf")
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except Exception:
        pass

    descargas, popups = [], []
    _on_dl = lambda d: descargas.append(d)
    _on_pg = lambda p: popups.append(p)
    page.on("download", _on_dl)
    page.context.on("page", _on_pg)

    def _modal_visible():
        try:
            return page.locator("#aceptar_modal").is_visible()
        except Exception:
            return False

    try:
        # 1. Expandir la fila del organismo (el ⊕ no descarga: despliega)
        try:
            icono = page.locator('img[src*="planillas.gif"]').nth(idx_org)
            icono.scroll_into_view_if_needed(timeout=3000)
            icono.click(timeout=6000)
        except Exception:
            try:
                page.evaluate(
                    f"document.querySelectorAll('img[src*=\"planillas.gif\"]')[{idx_org}].click()")
            except Exception:
                log("    no se pudo expandir la fila de la institución", "warn")
                return None, nombre_org

        # 2. Esperar a que aparezca el ícono de descarga de la subtabla
        cands = []
        fin = time.time() + 8
        while time.time() < fin and not descargas and not popups and not _modal_visible():
            try:
                cands = page.evaluate(_JS_ICONOS_DESCARGA)
            except Exception:
                cands = []
            if cands:
                break
            time.sleep(0.3)

        # 3. Clickear el ícono PDF de "Ver Planillas"
        if cands and not descargas and not popups:
            for c in cands[:3]:
                try:
                    page.evaluate(
                        "(i) => { const e = document.querySelectorAll('img, a')[i]; if (e) e.click(); }",
                        c["idx"])
                except Exception:
                    continue
                esp = time.time() + 6
                while time.time() < esp and not descargas and not popups and not _modal_visible():
                    time.sleep(0.3)
                if descargas or popups or _modal_visible():
                    break

        # 4. Si aún nada, volcar diagnóstico una sola vez
        if not descargas and not popups and not _modal_visible():
            if estado is not None and not estado.get("diag_expandido"):
                estado["diag_expandido"] = True
                try:
                    log("    [diag] elementos tras expandir:", "warn")
                    for d in page.evaluate(_JS_DIAG_EXPANDIDO):
                        log(f"      {d}", "warn")
                except Exception:
                    pass

        fin = time.time() + 4
        while time.time() < fin and not descargas and not popups and not _modal_visible():
            time.sleep(0.3)

        # (b) Modal → marcar "total empresa" y aceptar
        if not descargas and _modal_visible():
            try:
                radio = page.locator("input[type='radio'][value*='total']").first
                if radio.count() > 0 and not radio.is_checked():
                    radio.click()
            except Exception:
                pass
            try:
                page.click("#aceptar_modal", timeout=5000)
            except Exception:
                pass
            fin = time.time() + 20
            while time.time() < fin and not descargas and not popups:
                time.sleep(0.3)

        # (a) Descarga capturada
        if descargas:
            try:
                descargas[0].save_as(ruta)
                return ruta, nombre_org
            except Exception as e:
                log(f"  No se pudo guardar la descarga: {e.__class__.__name__}", "warn")

        # (c) Pestaña nueva con el PDF: bajarlo con la sesión actual
        if popups:
            pop = popups[0]
            try:
                pop.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            url_pdf = ""
            try:
                url_pdf = pop.url or ""
            except Exception:
                pass
            try:
                pop.close()
            except Exception:
                pass
            if url_pdf and "about:blank" not in url_pdf:
                try:
                    resp = page.context.request.get(url_pdf, timeout=25000)
                    data = resp.body()
                    if data[:4] == b"%PDF":
                        with open(ruta, "wb") as fh:
                            fh.write(data)
                        return ruta, nombre_org
                except Exception:
                    pass

        log("  La planilla no se pudo descargar (sin modal, sin archivo)", "warn")
        return None, nombre_org
    finally:
        for obj, evento, fn in ((page, "download", _on_dl), (page.context, "page", _on_pg)):
            try:
                obj.remove_listener(evento, fn)
            except Exception:
                pass
        try:
            cerrar = page.locator("button:has-text('Cerrar')").first
            if cerrar.count() and cerrar.is_visible():
                cerrar.click()
        except Exception:
            pass


# Diagnóstico de un solo disparo: qué hay realmente en la pantalla de resultados
_JS_DIAG_RESULTADOS = """() => {
    const out = {tablas: [], iconos: 0, modal: false, botones: []};
    out.iconos = document.querySelectorAll('img[src*="planillas.gif"]').length;
    out.modal = !!document.querySelector('#aceptar_modal');
    for (const t of Array.from(document.querySelectorAll('table')).slice(0, 6)) {
        const filas = Array.from(t.querySelectorAll('tr'));
        if (filas.length < 2) continue;
        const cab = Array.from(filas[0].querySelectorAll('th, td'))
            .map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
        const ej = Array.from(filas[1].querySelectorAll('td'))
            .map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim());
        if (cab.length) out.tablas.push({cabeceras: cab.slice(0, 12), ejemplo: ej.slice(0, 12)});
    }
    out.botones = Array.from(document.querySelectorAll('button, input[type=button], input[type=submit]'))
        .filter(b => b.offsetParent !== null)
        .map(b => (b.id || '') + ':' + ((b.innerText || b.value || '').trim().slice(0, 25)))
        .slice(0, 10);
    return out;
}"""


def actualizar_trabajadores(usuario, clave, clientes, mes, anio, carpeta_temp, log, guardar):
    """Proceso principal con la CUENTA MAESTRA de PreviRed: un solo login y
    se recorren todas las empresas vigentes. `clientes`: [{rut, razon}].
    `guardar(rut, razon, organismo, n, estado)` persiste cada resultado.
    El PDF se elimina apenas se extrae el dato (no se acumula espacio)."""
    os.makedirs(carpeta_temp, exist_ok=True)
    periodo_txt = f"{MESES_NOMBRE.get(mes, mes)} {anio}"
    log(f"Período a consultar: {periodo_txt} — {len(clientes)} empresa(s) vigentes", "info")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            downloads_path=carpeta_temp,
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(45000)
        try:
            hacer_login(page, usuario, clave, log)
            estado = {"home": page.url}   # URL real del portal tras el login

            t0_global = time.time()
            for i, cli in enumerate(clientes, 1):
                rut, razon = cli["rut"], cli.get("razon", "")
                t0 = time.time()
                if i > 1:
                    prom = (time.time() - t0_global) / (i - 1)
                    resta = int(prom * (len(clientes) - i + 1) / 60)
                    log(f"[{i}/{len(clientes)}] {razon or rut}... (~{resta} min restantes)", "info")
                else:
                    log(f"[{i}/{len(clientes)}] {razon or rut}...", "info")
                try:
                    # Entre empresas hay que volver al inicio del portal:
                    # dentro de Planillas Pagadas no existe el menú li#empresa
                    if i > 1:
                        _volver_al_inicio(page, estado, usuario, clave, log)

                    ids = _ids_empresa(page, rut, log)
                    if not ids:
                        guardar(rut, razon, "", None, "no_esta_en_previred")
                        continue

                    # Una empresa puede tener varias sucursales (#00, #11, #12...).
                    # Se revisan todas y se suman los trabajadores de cada una.
                    if len(ids) > 1:
                        log(f"  {len(ids)} sucursales en PreviRed — se revisan todas", "info")

                    n_total, organismo_usado, con_planilla, con_nomina = None, "", False, False

                    for k, btn_id in enumerate(ids):
                        if k > 0:
                            _volver_al_inicio(page, estado, usuario, clave, log)
                            if not _ids_empresa(page, rut, log):
                                break
                        etiqueta = btn_id.split("#")[2] if btn_id.count("#") >= 2 else str(k)
                        try:
                            page.click(f'[id="{btn_id}"]')
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception:
                                pass
                            if not _ir_a_planillas_rapido(page, log):
                                log(f"  Sucursal {etiqueta}: no se llegó a Planillas Pagadas", "warn")
                                continue
                            nominas = _nominas_rapido(page, mes, anio)
                        except Exception as e_suc:
                            log(f"  Sucursal {etiqueta}: {type(e_suc).__name__}", "warn")
                            continue

                        if not nominas:
                            if len(ids) > 1:
                                log(f"  Sucursal {etiqueta}: sin nóminas", "info")
                            continue
                        con_nomina = True

                        log(f"  Sucursal {etiqueta}: {len(nominas)} nómina(s) — se procesan todas",
                            "info")

                        for idx_nom, nombre_nomina in enumerate(nominas, 1):
                            marca = f"  [{idx_nom}/{len(nominas)}] {nombre_nomina}"
                            try:
                                hay = _buscar_planilla_organismo(page, mes, anio, nombre_nomina, log)
                            except Exception as e_bus:
                                log(f"{marca}: error al buscar ({type(e_bus).__name__})", "warn")
                                _reset_busqueda(page, log)
                                continue
                            if not hay:
                                log(f"{marca}: sin planillas timbradas", "warn")
                                _reset_busqueda(page, log)
                                continue

                            # Puede haber MÁS DE UN ícono de organismo por búsqueda:
                            # se descargan y suman todos.
                            iconos = _iconos_organismo(page, log)
                            if not iconos:
                                log(f"{marca}: sin planilla de mutual/ISL", "warn")
                                _reset_busqueda(page, log)
                                continue

                            for idx_ic, nombre_org in iconos:
                                ruta_pdf, organismo = _descargar_pdf_organismo(
                                    page, idx_ic, nombre_org, carpeta_temp, log, estado)
                                if not ruta_pdf:
                                    log(f"{marca}: no se pudo descargar {nombre_org}", "warn")
                                    continue
                                con_planilla = True
                                n = extraer_n_trabajadores(ruta_pdf)
                                # Eliminar el PDF de inmediato — solo queda el dato
                                try:
                                    os.remove(ruta_pdf)
                                except Exception:
                                    pass
                                if n is not None:
                                    n_total = (n_total or 0) + n
                                    organismo_usado = organismo or organismo_usado
                                    log(f"{marca} · {organismo}: {n} afiliados "
                                        f"(acumulado {n_total})", "ok")
                                else:
                                    organismo_usado = organismo or organismo_usado
                                    log(f"{marca}: PDF sin 'N° de Afiliados Informados'", "warn")

                            _reset_busqueda(page, log)

                    if n_total is not None:
                        guardar(rut, razon, organismo_usado, n_total, "ok")
                        log(f"  TOTAL {rut}: {n_total} trabajadores ({organismo_usado})", "ok")
                    elif con_planilla:
                        guardar(rut, razon, organismo_usado, None, "sin_dato_en_pdf")
                    elif con_nomina:
                        guardar(rut, razon, "", None, "sin_planilla_organismo")
                    else:
                        log("  Sin nóminas en el período", "warn")
                        guardar(rut, razon, "", None, "sin_planilla")

                except Exception as e:
                    msg = f"{type(e).__name__}: {str(e)[:120]}"
                    log(f"  Error: {msg}", "err")
                    guardar(rut, razon, "", None, f"error: {msg}")
                    # Recuperar la navegación para la siguiente empresa
                    try:
                        _volver_al_inicio(page, estado, usuario, clave, log)
                    except Exception:
                        pass
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    log("Proceso terminado", "ok")
