"""Lógica de descarga Playwright para Previred — sin credenciales hardcodeadas."""
import os, re, time, shutil

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL_LOGIN = "https://www.previred.com/wPortal/login/login.jsp"

MESES_NOMBRE = {
    1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",
    7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"
}


def rut_a_btn_id(rut: str, razon_social: str = "") -> str:
    rut = (rut or '').strip()
    num = rut.split("-")[0].replace(".", "")
    sub_id = "00"
    if razon_social:
        m = re.search(r'\(I(\d+)\)', razon_social, re.IGNORECASE)
        if m:
            sub_id = m.group(1)
    return f"empresa#{num}#{sub_id}#false"


def _dump_modal_impresion(page, log):
    """Cuando el botón #aceptar_modal no aparece, vuelca en el log los
    botones/inputs realmente visibles para saber si Previred cambió el id
    o el modal no se abrió como se esperaba."""
    try:
        log(f"  [debug] url = {page.url}", "warn")
    except Exception as e_url:
        log(f"  [debug] no se pudo leer la url: {type(e_url).__name__}", "warn")
    try:
        info = page.evaluate("""() => {
            const out = [];
            for (const e of document.querySelectorAll(
                    'button, input[type="submit"], input[type="button"], a.button, .ui-dialog button')) {
                if (e.offsetParent === null) continue;
                out.push({
                    tag: e.tagName.toLowerCase(), id: e.id || '',
                    clase: (e.className || '').toString().slice(0, 60),
                    texto: (e.innerText || e.value || '').trim().slice(0, 40)
                });
            }
            const dialogAbierto = document.querySelectorAll('.ui-dialog:not([style*="display: none"])').length;
            return {botones: out.slice(0, 15), dialogos_abiertos: dialogAbierto};
        }""")
        log(f"  [debug] diálogos abiertos: {info['dialogos_abiertos']}", "warn")
        for b in info["botones"]:
            log(f"    {b['tag']} id={b['id']!r} clase={b['clase']!r} texto={b['texto']!r}", "warn")
        if not info["botones"]:
            try:
                texto = page.evaluate(
                    "() => (document.body ? document.body.innerText : '')"
                    ".replace(/\\s+/g, ' ').trim().slice(0, 300)")
                log(f"  [debug] sin botones visibles — texto de la página: {texto!r}", "warn")
            except Exception:
                pass
    except Exception as e:
        log(f"  [debug] no se pudo volcar el modal: {type(e).__name__}: {str(e)[:150]}", "warn")


def _click_texto(page, texto: str, timeout: int = 15000) -> bool:
    """Hace click en el primer elemento visible que contenga 'texto'."""
    # Primer intento: esperar que el elemento esté visible (respeta timeout)
    for tag in ["a", "span", "li", "button"]:
        try:
            loc = page.locator(f"{tag}:has-text('{texto}')").first
            loc.wait_for(state="visible", timeout=timeout)
            loc.click()
            return True
        except Exception:
            pass
        # Los siguientes tags usan timeout corto, ya se esperó en el primero
        timeout = 2000

    # Fallback JS: pasar texto como argumento para evitar problemas con comillas
    try:
        found = page.evaluate(
            "(t) => { var nodes = document.querySelectorAll('a, span, li, button'); "
            "for (var el of nodes) { "
            "if (el.offsetParent !== null && el.textContent.toLowerCase().includes(t)) { el.click(); return true; } "
            "} return false; }",
            texto.lower()
        )
        return bool(found)
    except Exception:
        return False


def _select_anio(page, anio: int):
    """Selecciona el año en #yearR0 probando value y label."""
    anio_str = str(anio)
    try:
        page.select_option("#yearR0", value=anio_str)
    except Exception:
        page.select_option("#yearR0", label=anio_str)


def hacer_login(page, rut_usuario: str, contrasena: str, log):
    log("Iniciando sesión en Previred...", "info")
    page.goto(URL_LOGIN, wait_until="networkidle", timeout=30000)
    page.wait_for_selector('[name="web_rut2"]', timeout=20000)
    page.fill('[name="web_rut2"]', rut_usuario)
    time.sleep(0.5)
    page.fill('[name="web_password"]', contrasena)
    time.sleep(0.5)
    try:
        page.click("button:has-text('INGRESAR')", timeout=5000)
    except Exception:
        page.click("button[type='submit']")
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    time.sleep(2)
    # No se afirma "sesión iniciada" aquí: eso lo confirma verificar_acceso()
    log("Credenciales enviadas", "info")


def esta_en_login(page) -> bool:
    try:
        return page.locator('[name="web_rut2"]').count() > 0
    except Exception:
        return False


class CuentaBloqueada(Exception):
    """PreviRed indica clave expirada o usuario bloqueado."""


# Tope de seguridad: los re-login preventivos fueron los que provocaron el
# bloqueo de la cuenta maestra. Nunca más se inicia sesión "por si acaso".
_MAX_LOGINS = 15


def revisar_cuenta_bloqueada(page):
    """Lanza CuentaBloqueada si el portal avisa clave expirada / usuario bloqueado."""
    try:
        malo = page.evaluate("""() => {
            const t = (document.body ? document.body.innerText : '').toLowerCase();
            return t.includes('clave ha expirado') || t.includes('encuentra bloqueado') ||
                   t.includes('usuario bloqueado');
        }""")
    except Exception:
        return
    if malo:
        raise CuentaBloqueada(
            "PreviRed indica: 'Su clave ha expirado, o su usuario se encuentra "
            "bloqueado'. Renueva la clave en previred.com y actualízala en la "
            "configuración de Previred de la plataforma.")


def sesion_expirada(page) -> bool:
    """True si el portal muestra 'sesión expirada' o volvió al login.
    OJO: 'Su CLAVE ha expirado' NO es esto (es cuenta bloqueada)."""
    try:
        if esta_en_login(page):
            return True
        return bool(page.evaluate("""() => {
            const t = (document.body ? document.body.innerText : '')
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
            if (t.includes('clave ha expirado') || t.includes('encuentra bloqueado'))
                return false;
            return t.includes('sesion expirada') || t.includes('sesion ha expirado') ||
                   t.includes('sesion ha caducado') || t.includes('sesion caducada') ||
                   t.includes('sesion ha finalizado') || t.includes('sesion finalizada') ||
                   t.includes('sesion ha sido cerrada') || t.includes('vuelva a iniciar sesion') ||
                   t.includes('debe iniciar sesion') || t.includes('sesion no valida') ||
                   t.includes('su sesion ha');
        }"""))
    except Exception:
        return False


class CredencialesInvalidas(Exception):
    """El usuario/clave configurados no sirven para entrar a PreviRed."""


def verificar_acceso(page, log):
    """Comprueba que el login realmente entró (hacer_login no lo verifica).

    Si quedó en la pantalla de acceso, informa el motivo textual del portal:
    normalmente la clave guardada quedó desactualizada."""
    revisar_cuenta_bloqueada(page)
    if not esta_en_login(page):
        log("Sesión iniciada", "ok")
        return True

    detalle = ""
    try:
        detalle = page.evaluate("""() => {
            const t = (document.body ? document.body.innerText : '');
            const low = t.toLowerCase();
            for (const f of ['incorrect', 'inválid', 'invalid', 'no coincide',
                             'erróne', 'errone', 'intentos', 'bloquead', 'no válid']) {
                const i = low.indexOf(f);
                if (i >= 0) return t.substring(Math.max(0, i - 80), i + 100)
                    .replace(/\\s+/g, ' ').trim();
            }
            return '';
        }""") or ""
    except Exception:
        pass

    raise CredencialesInvalidas(
        "No se pudo iniciar sesión en PreviRed: el portal sigue mostrando la "
        "pantalla de acceso. Revisa el RUT y la clave guardados en la "
        "configuración de Previred (⚙) — si renovaste la clave en previred.com, "
        "hay que actualizarla también aquí."
        + (f" El portal dice: {detalle}" if detalle else ""))


def login_previred(page, rut_usuario, contrasena, log, estado, motivo=""):
    """Inicia sesión llevando la cuenta. Se usa UNA VEZ al comenzar y,
    después, solo cuando se detecta que la sesión expiró."""
    estado["logins"] = estado.get("logins", 0) + 1
    if estado["logins"] > _MAX_LOGINS:
        raise CuentaBloqueada(
            f"La sesión se cerró {_MAX_LOGINS} veces en esta descarga. Se detiene "
            "para no arriesgar el bloqueo de la cuenta en PreviRed.")
    if motivo:
        log(f"{motivo} (reconexión {estado['logins']})", "warn")
    hacer_login(page, rut_usuario, contrasena, log)
    verificar_acceso(page, log)


def reconectar_si_expiro(page, rut_usuario, contrasena, log, estado):
    """Reconecta SOLO si la sesión expiró. Devuelve True si reconectó."""
    revisar_cuenta_bloqueada(page)
    if not sesion_expirada(page):
        return False
    login_previred(page, rut_usuario, contrasena, log, estado,
                   motivo="Sesión expirada en PreviRed — reconectando")
    return True


def ir_a_empresa(page, rut_empresa: str, log, razon_social: str = ""):
    rut_num = rut_empresa.replace(".", "").split("-")[0]
    patron = f"empresa#{rut_num}#"
    log(f"Navegando a empresa {rut_empresa}...", "info")

    try:
        page.wait_for_selector("li#empresa", timeout=20000)
    except Exception:
        # ¿Por qué no está el menú? Puede ser sesión no iniciada u otra pantalla
        verificar_acceso(page, log)          # lanza si son credenciales/bloqueo
        try:
            info = page.evaluate("""() => ({
                url: location.href,
                texto: (document.body ? document.body.innerText : '')
                    .replace(/\\s+/g, ' ').trim().slice(0, 250)
            })""")
            log(f"[debug] sin menú de empresas — url: {info['url'][:80]}", "warn")
            log(f"[debug] pantalla: {info['texto']}", "warn")
        except Exception:
            pass
        raise
    page.click("li#empresa")
    # Esperar navegación completa antes de evaluar
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_selector(f'[id^="{patron}"]', timeout=15000)
    except Exception:
        time.sleep(3)

    # Retry si el contexto se destruye por redirección secundaria
    ids_encontrados = None
    for intento in range(3):
        try:
            time.sleep(1)
            ids_encontrados = page.evaluate(
                "(patron) => Array.from(document.querySelectorAll('[id^=\"' + patron + '\"]')).map(el => el.id)",
                patron
            )
            break
        except Exception as e:
            if "context was destroyed" in str(e).lower() and intento < 2:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            else:
                raise
    if ids_encontrados is None:
        ids_encontrados = []
    log(f"Botones empresa encontrados: {ids_encontrados}", "info")

    btn_id_elegido = None
    if not ids_encontrados:
        btn_id_elegido = f"{patron}00#false"
        log("Sin botones para el RUT, usando #00# por defecto", "warn")
    elif len(ids_encontrados) == 1:
        btn_id_elegido = ids_encontrados[0]
    else:
        m_suf = re.search(r'\(([^)]+)\)', razon_social or "")
        if m_suf:
            sufijo = m_suf.group(1).lower().strip()
            log(f"Buscando empresa con sufijo '{sufijo}'...", "info")
            resultado = page.evaluate("""([patron, sufijo]) => {
                var btns = document.querySelectorAll('[id^="' + patron + '"]');
                var diagnostico = [];
                var encontrado = null;
                for (var i = 0; i < btns.length; i++) {
                    var el = btns[i].parentElement;
                    var depth = 0;
                    while (el && depth < 15) {
                        var cnt = el.querySelectorAll('[id^="' + patron + '"]').length;
                        if (cnt === 1) {
                            var parent = el.parentElement;
                            var textoRow = parent ? parent.textContent.toLowerCase().trim().replace(/\\s+/g, ' ') : '';
                            var textoEl  = el.textContent.toLowerCase().trim().replace(/\\s+/g, ' ');
                            var textoCheck = textoEl + ' ' + textoRow;
                            if (i < 8) diagnostico.push(btns[i].id + ' | ' + textoRow.substring(0, 150));
                            if (!encontrado && textoCheck.indexOf(sufijo) !== -1) encontrado = btns[i].id;
                            break;
                        }
                        el = el.parentElement;
                        depth++;
                    }
                }
                return {encontrado, diagnostico};
            }""", [patron, sufijo])
            for linea in (resultado.get("diagnostico") or []):
                log(f"  ROW: {linea}", "info")
            btn_id_elegido = resultado.get("encontrado")
            if btn_id_elegido:
                log(f"Empresa identificada por sufijo '{sufijo}': {btn_id_elegido}", "info")
            else:
                btn_id_elegido = ids_encontrados[0]
                log(f"Sufijo '{sufijo}' no encontrado, usando primer botón", "warn")
        else:
            btn_id_elegido = f"{patron}00#false"
            log(f"Sin sufijo en razón social, usando empresa principal: {btn_id_elegido}", "info")

    if not page.locator(f'[id="{btn_id_elegido}"]').count():
        raise RuntimeError(f"Botón empresa {btn_id_elegido} no encontrado en DOM")
    page.click(f'[id="{btn_id_elegido}"]')
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    time.sleep(3)
    log("Empresa seleccionada", "ok")


def ir_a_planillas_pagadas(page, log):
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    time.sleep(2)

    # Remuneraciones — expande el submenú
    if _click_texto(page, "Remuneraciones", timeout=10000):
        log("Remuneraciones clickeado", "info")
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        time.sleep(2)
    else:
        log("Remuneraciones no visible, continuando...", "warn")

    # Imprimir Documentos
    if not _click_texto(page, "Imprimir Documentos", timeout=15000):
        if not _click_texto(page, "Imprimir Documentos", timeout=5000):
            raise RuntimeError("No se encontró 'Imprimir Documentos' en el menú")
    time.sleep(2)

    # Planillas Pagadas
    if not _click_texto(page, "Planillas Pagadas", timeout=15000):
        raise RuntimeError("No se encontró 'Planillas Pagadas' en el menú")
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    time.sleep(4)
    log("En sección Planillas Pagadas", "ok")


def verificar_y_relogin(page, rut_usuario, contrasena, rut_empresa, razon_social, log,
                        estado=None):
    """Reconecta solo si la sesión expiró (nunca de forma preventiva)."""
    if estado is None:
        estado = {}
    if not reconectar_si_expiro(page, rut_usuario, contrasena, log, estado):
        return False
    ir_a_empresa(page, rut_empresa, log, razon_social)
    ir_a_planillas_pagadas(page, log)
    return True


def _cerrar_tabs_extra(page):
    """Cierra pestañas extra que Previred pueda abrir (PDF en nueva pestaña)."""
    for p in page.context.pages[1:]:
        try:
            p.close()
        except Exception:
            pass


def obtener_nominas(page, mes: int, anio: int) -> list:
    page.wait_for_selector("#mesR0", timeout=15000)
    page.select_option("#mesR0", str(mes).zfill(2))
    time.sleep(1)
    _select_anio(page, anio)
    time.sleep(2)
    page.wait_for_selector("#combo_nominas", timeout=15000)
    opciones = page.evaluate("""() => {
        var sel = document.getElementById('combo_nominas');
        return Array.from(sel.options).map(o => ({text: o.text.trim(), value: o.value}));
    }""")
    return [o["text"] for o in opciones if o["text"] and o["value"] and "seleccione" not in o["text"].lower()]


def buscar_planilla(page, mes: int, anio: int, nombre_nomina: str) -> bool:
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
    objetivo = next((o for o in opciones if o == nombre_nomina.strip()), None)
    if not objetivo:
        objetivo = next((o for o in opciones if nombre_nomina.strip() in o), None)
    if not objetivo:
        return False
    page.select_option("#combo_nominas", label=objetivo)
    time.sleep(1)

    try:
        page.wait_for_selector("#combo_tipo_institucion", timeout=8000)
        opciones_tipo = page.evaluate("""() => {
            var sel = document.getElementById('combo_tipo_institucion');
            return Array.from(sel.options).map(o => o.text);
        }""")
        afp_opt = next((o for o in opciones_tipo if "AFP" in o.upper()), None)
        if afp_opt:
            page.select_option("#combo_tipo_institucion", label=afp_opt)
        time.sleep(3)
        page.wait_for_function("""() => {
            var sel = document.getElementById('combo_instituciones');
            return sel && sel.options.length >= 1;
        }""", timeout=8000)
        textos_inst = page.evaluate("""() => {
            var sel = document.getElementById('combo_instituciones');
            return Array.from(sel.options).map(o => o.text);
        }""")
        if "Todas las Instituciones" in textos_inst:
            page.select_option("#combo_instituciones", label="Todas las Instituciones")
        time.sleep(1)
    except Exception:
        time.sleep(1)

    # Cerrar dialogs flotantes antes de buscar
    try:
        page.evaluate("""() => {
            document.querySelectorAll('.ui-dialog').forEach(function(d){
                var btn=d.querySelector('button'); if(btn) btn.click();
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


def _hay_dialogo_email(page) -> bool:
    """Detecta si Previred mostró el formulario de envío por email (planilla muy grande)."""
    try:
        cuerpo = page.inner_text("body")
        return "enviará por email" in cuerpo.lower() or "enviara por email" in cuerpo.lower()
    except Exception:
        return False


def _descargar_pdfs_individuales(page, mes: int, anio: int, nombre_nomina: str,
                                  carpeta_temp: str, carpeta_dest: str, log) -> int:
    nombre_limpio = re.sub(r'[/\\:]', '-', nombre_nomina)
    prefijo = f"{anio}-{str(mes).zfill(2)}-{nombre_limpio}"
    descargados = 0

    def _iconos_planilla_actuales():
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img[src*="planillas.gif"]')).map(function(img) {
                return img.id || '';
            });
        }""")

    # Cada institución (Seguro Social, AFP, Isapre, Mutual, CCAF, IPS/Fonasa,
    # APV) aparece colapsada detrás de un ícono "+" (mas_nomina.png,
    # <a id="oculta_nomina|N">) — recién al abrirlo aparecen los íconos
    # planillas.gif descargables de esa categoría. Antes solo se procesaban
    # los que YA estuvieran visibles sin abrir nada (en la práctica, solo
    # AFP aparecía expandida de entrada), perdiendo el resto de categorías.
    ids_info = list(dict.fromkeys(_iconos_planilla_actuales()))

    categorias = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[id^="oculta_nomina"]')).map(function(a) {
            return a.id || '';
        });
    }""")
    log(f"Categorías de institución encontradas: {len(categorias)}", "info")
    if len(categorias) <= 1:
        # Diagnóstico: si solo aparece 1 categoría (o ninguna), puede que
        # esta página (a la que se llega tras "Nueva Búsqueda" en el
        # flujo de "planilla muy grande") ya venga directo mostrando una
        # sola institución en vez de las 7 colapsadas. Volcar el texto de
        # las filas de institución visibles para confirmar qué hay.
        try:
            filas = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('td, th'))
                    .map(e => (e.innerText || '').trim())
                    .filter(t => t.length > 0 && t.length < 40)
                    .slice(0, 20);
            }""")
            log(f"  [debug] texto de celdas visibles: {filas}", "warn")
        except Exception:
            pass

    def _click_categoria(cat_id):
        page.evaluate(
            "(id) => { const el = document.querySelector('[id=\"' + id + '\"]'); if (el) el.click(); }",
            cat_id)
        page.wait_for_timeout(1200)

    for cat_id in categorias:
        antes = set(_iconos_planilla_actuales())
        try:
            _click_categoria(cat_id)
        except Exception as e_cat:
            log(f"No se pudo abrir categoría {cat_id}: {type(e_cat).__name__}", "warn")
            continue
        despues = set(_iconos_planilla_actuales())
        if len(despues) < len(antes):
            # El click cerró una categoría que ya estaba abierta (ej. AFP,
            # que suele venir expandida de entrada): reabrirla para no
            # perder sus íconos ya capturados en ids_info.
            try:
                _click_categoria(cat_id)
                despues = set(_iconos_planilla_actuales())
            except Exception:
                pass
        for nuevo_id in despues:
            if nuevo_id and nuevo_id not in ids_info:
                ids_info.append(nuevo_id)

    total_iconos = len(ids_info)
    log(f"Instituciones a descargar: {total_iconos}", "info")
    if total_iconos == 0:
        log("No se encontraron iconos planillas.gif", "warn")
        return 0

    # Previred genera estos PDFs en una cola en su servidor: el primero
    # puede llegar al instante, pero cada uno siguiente tarda cada vez más
    # (visto en producción: instantáneo, 7s, 90s+...). Esperar institución
    # por institución (clickear → esperar su PDF → recién pasar a la
    # siguiente) hacía que los que demoraban más se perdieran: el archivo
    # SÍ llegaba (quedaba capturado en memoria), pero ya se había pasado a
    # la institución siguiente y nadie volvía a revisarlo, así que nunca se
    # guardaba a disco.
    #
    # Ahora se clickean TODAS las instituciones seguidas, sin esperar a que
    # cada una termine, y recién al final se espera UNA vez por todas las
    # que sigan pendientes -- ningún archivo se descarta por timing.
    capturas = {}      # img_id -> {'pdf': [bytes], 'ruta': str, 'nombre_inst': str}
    listeners_page = []  # [(evento, callback), ...] registrados en `page`, para limpiar al final

    for i, img_id in enumerate(ids_info):
        inst_num = i + 1
        partes_id = img_id.split('#')
        nombre_inst = partes_id[-1] if len(partes_id) > 1 else f"inst{inst_num:02d}"
        nombre_dest = f"{prefijo}-{nombre_inst}.pdf"
        ruta_dest = os.path.join(carpeta_dest, nombre_dest)

        pdf_capturado = []
        capturas[img_id] = {"pdf": pdf_capturado, "ruta": ruta_dest, "nombre_inst": nombre_inst}

        def _capturar_respuesta(response, _pdf=pdf_capturado):
            try:
                ct = (response.headers or {}).get("content-type", "")
                if "pdf" not in ct.lower():
                    return
                body = response.body()
                if body and len(body) > 500:
                    _pdf.append(body)
            except Exception:
                pass

        def _capturar_download(dl, _pdf=pdf_capturado):
            try:
                import tempfile as _tf
                tmp = _tf.mktemp(suffix=".pdf")
                dl.save_as(tmp)
                with open(tmp, "rb") as fh:
                    b = fh.read()
                os.remove(tmp)
                if b and len(b) > 500:
                    _pdf.append(b)
            except Exception:
                pass

        page.on("download", _capturar_download)
        page.on("response", _capturar_respuesta)
        listeners_page.append(("download", _capturar_download))
        listeners_page.append(("response", _capturar_respuesta))

        try:
            with page.expect_popup(timeout=4000) as popup_info:
                page.evaluate(
                    "(id) => { const el = document.querySelector('[id=\"' + id + '\"]'); if (el) el.click(); }",
                    img_id)
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            popup.on("download", _capturar_download)
            popup.on("response", _capturar_respuesta)
        except PWTimeout:
            pass
        except Exception as ec:
            log(f"inst{inst_num} ({nombre_inst}): click falló {ec.__class__.__name__}", "warn")
            continue

        try:
            page.wait_for_selector("#aceptar_modal", state="visible", timeout=8000)
            if i == 0:
                # Diagnóstico solo para la primera institución
                _dump_modal_impresion(page, log)
                try:
                    nombre_antes = "debug_antes_click_individual.png"
                    page.screenshot(path=os.path.join(carpeta_temp, nombre_antes))
                    tid = os.path.basename(os.path.normpath(carpeta_temp))
                    log(f"Captura ANTES del click: "
                        f"<a href='/api/previred/captura/{tid}/{nombre_antes}' target='_blank'>ver imagen</a>",
                        "warn")
                except Exception:
                    pass
            page.click("#aceptar_modal")
            log(f"inst{inst_num} ({nombre_inst}): Imprimir clickeado", "info")
        except Exception:
            log(f"inst{inst_num} ({nombre_inst}): modal no apareció, "
                f"esperando captura por red...", "warn")
            if i == 0:
                _dump_modal_impresion(page, log)

        # Respiro corto antes de pasar a la siguiente institución -- NO se
        # espera aquí a que este PDF termine de generarse, eso pasa después
        # para todas juntas.
        page.wait_for_timeout(1500)

    # Esperar UNA vez por todos los PDFs que sigan pendientes
    faltantes = [k for k, c in capturas.items() if not c["pdf"]]
    if faltantes:
        log(f"Esperando que Previred termine de generar {len(faltantes)} PDF(s) pendiente(s)...", "info")
        for i_espera in range(180):
            faltantes = [k for k, c in capturas.items() if not c["pdf"]]
            if not faltantes:
                break
            if i_espera > 0 and i_espera % 20 == 0:
                log(f"Todavía faltan {len(faltantes)} de {total_iconos}... ({i_espera}s)", "info")
            time.sleep(1)

    for cb_evento, cb_fn in listeners_page:
        try:
            page.remove_listener(cb_evento, cb_fn)
        except Exception:
            pass

    for img_id, c in capturas.items():
        if c["pdf"]:
            with open(c["ruta"], "wb") as f:
                f.write(c["pdf"][0])
            log(f"Guardado: {os.path.basename(c['ruta'])}", "ok")
            descargados += 1
        else:
            log(f"{c['nombre_inst']}: no llegó ningún PDF", "err")

    return descargados


def descargar_planilla(page, mes: int, anio: int, nombre_nomina: str,
                       carpeta_temp: str, carpeta_dest: str, log) -> bool:
    """Descarga la planilla. Usa el mismo patrón que ya funciona en
    cartas.py (movimiento de personal): capturar el PDF por RED (respuesta
    HTTP con content-type PDF) y por evento de descarga del navegador, en
    vez de depender de encontrar y clickear un botón específico dentro del
    popup. Así, si Previred cambia el id/markup del botón "Imprimir", el
    PDF igual se captura mientras la ventana emergente lo sirva."""
    nombre_limpio = re.sub(r'[/\\:]', '-', nombre_nomina)
    nombre_dest = f"{anio}-{str(mes).zfill(2)}-{nombre_limpio}.pdf"
    ruta_dest = os.path.join(carpeta_dest, nombre_dest)

    page.wait_for_selector("button[id^='planillas_masivas']", timeout=20000)

    # Limpiar PDFs previos en carpeta_temp para no confundir descargas
    for f in os.listdir(carpeta_temp):
        if f.lower().endswith(".pdf"):
            try:
                os.remove(os.path.join(carpeta_temp, f))
            except Exception:
                pass

    pdf_capturado = []

    def _capturar_respuesta(response):
        try:
            ct = (response.headers or {}).get("content-type", "")
            if "pdf" not in ct.lower():
                return
            body = response.body()
            if body and len(body) > 500:
                pdf_capturado.append(body)
                log(f"PDF capturado por red ({len(body)} bytes)", "ok")
        except Exception:
            pass

    def _capturar_download(dl):
        try:
            import tempfile as _tf
            tmp = _tf.mktemp(suffix=".pdf")
            dl.save_as(tmp)
            with open(tmp, "rb") as fh:
                b = fh.read()
            os.remove(tmp)
            if b and len(b) > 500:
                pdf_capturado.append(b)
                log(f"PDF capturado por descarga ({len(b)} bytes)", "ok")
        except Exception:
            pass

    page.on("download", _capturar_download)

    # Abrir modal de impresión. Previred lo abre en una VENTANA EMERGENTE
    # nueva (el propio sitio explica cómo destrabar el bloqueador de popups
    # con "Ctrl+Alt+Click" cuando no se abre). expect_popup() es el mismo
    # mecanismo ya probado en cartas.py para "movimiento de personal".
    modal = page
    try:
        with page.expect_popup(timeout=8000) as popup_info:
            page.click("button[id^='planillas_masivas']")
        modal = popup_info.value
        modal.wait_for_load_state("domcontentloaded")
        modal.on("download", _capturar_download)
        modal.on("response", _capturar_respuesta)
        log("Modal de impresión abierto (ventana emergente)", "info")
    except PWTimeout:
        # El click funcionó pero no se abrió ventana nueva: asumir que el
        # modal quedó en la misma página (fallback)
        page.on("response", _capturar_respuesta)
        if not pdf_capturado:
            log("Modal de impresión abierto", "info")

    try:
        email_dialog = False

        # Algunos casos (ej. una sola institución) descargan el PDF directo
        # al hacer click, sin abrir ventana emergente ni modal — el listener
        # de 'download' ya lo capturó arriba. Si es así, no hay nada más
        # que esperar ni clickear.
        if pdf_capturado:
            log("PDF descargado directo (sin modal intermedio)", "info")
        else:
            time.sleep(2)

            # Seleccionar "Total Empresa"
            try:
                radio = modal.locator("input[type='radio'][value*='total']").first
                if radio.count() > 0 and not radio.is_checked():
                    radio.click()
                time.sleep(1)
            except Exception:
                pass

            # Click en Imprimir si el botón aparece — best-effort: si no
            # aparece, no se corta el flujo, porque el PDF puede llegar solo
            # por la captura de red/descarga ya armada arriba.
            if not pdf_capturado:
                try:
                    modal.wait_for_selector("#aceptar_modal", state="visible", timeout=15000)
                    modal.click("#aceptar_modal")
                    log("Imprimir clickeado", "info")
                except Exception as e_imp:
                    if not pdf_capturado:
                        log(f"Botón Imprimir no apareció ({e_imp.__class__.__name__}) — "
                            f"esperando captura por red...", "warn")
                        _dump_modal_impresion(modal, log)

            # Esperar a que llegue el PDF (por red o descarga) o el aviso de
            # "planilla muy grande, se enviará por email"
            for _ in range(15):
                if pdf_capturado:
                    break
                if _hay_dialogo_email(modal):
                    email_dialog = True
                    break
                time.sleep(1)

        if email_dialog:
            log("Planilla muy grande — Previred pide envío por email. "
                "Descargando PDFs individuales por institución...", "warn")
            if modal is not page:
                try:
                    modal.close()
                except Exception:
                    pass
            try:
                _click_texto(page, "Nueva Búsqueda", timeout=5000)
                time.sleep(2)
            except Exception:
                pass
            try:
                buscar_planilla(page, mes, anio, nombre_nomina)
                n = _descargar_pdfs_individuales(page, mes, anio, nombre_nomina,
                                                  carpeta_temp, carpeta_dest, log)
                if n > 0:
                    log(f"Descargados {n} PDF(s) individuales para '{nombre_nomina}'", "ok")
                    return True
                log(f"No se pudo descargar ningún PDF individual para '{nombre_nomina}'", "err")
                return False
            except Exception as e2:
                log(f"Error en descarga individual: {e2}", "err")
                return False

        descargado = False
        if pdf_capturado:
            with open(ruta_dest, "wb") as f:
                f.write(pdf_capturado[0])
            descargado = True

        if not descargado:
            try:
                nombre_captura = "debug_modal_impresion.png"
                modal.screenshot(path=os.path.join(carpeta_temp, nombre_captura))
                tid = os.path.basename(os.path.normpath(carpeta_temp))
                url_captura = f"/api/previred/captura/{tid}/{nombre_captura}"
                log(f"Captura de pantalla: <a href='{url_captura}' target='_blank'>ver imagen</a>", "warn")
            except Exception as e_shot:
                log(f"  [debug] no se pudo guardar la captura: "
                    f"{type(e_shot).__name__}: {str(e_shot)[:150]}", "warn")
    finally:
        try:
            page.remove_listener("download", _capturar_download)
        except Exception:
            pass
        try:
            page.remove_listener("response", _capturar_respuesta)
        except Exception:
            pass
        if modal is not page:
            try:
                modal.close()
            except Exception:
                pass

    _cerrar_tabs_extra(page)

    if not descargado:
        for _ in range(20):
            pdfs = [f for f in os.listdir(carpeta_temp) if f.lower().endswith(".pdf")]
            if pdfs:
                break
            time.sleep(1)
        pdfs = [f for f in os.listdir(carpeta_temp) if f.lower().endswith(".pdf")]
        if pdfs:
            ultimo = max([os.path.join(carpeta_temp, f) for f in pdfs], key=os.path.getmtime)
            shutil.move(ultimo, ruta_dest)
            descargado = True

    if descargado:
        log(f"Guardado: {nombre_dest}", "ok")
        return True

    log(f"Sin PDF para '{nombre_nomina}'", "err")
    return False


def volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log,
                      estado=None):
    _cerrar_tabs_extra(page)
    # Intentar con el texto exacto (timeout corto)
    if _click_texto(page, "Nueva búsqueda", timeout=3000):
        time.sleep(2)
        return
    # JS agresivo: buscar en cualquier elemento visible
    try:
        found = page.evaluate("""() => {
            var all = document.querySelectorAll('*');
            for (var el of all) {
                if (el.offsetParent !== null && el.childElementCount === 0) {
                    var t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (t === 'nueva búsqueda' || t === 'nueva busqueda') {
                        el.click(); return true;
                    }
                }
            }
            return false;
        }""")
        if found:
            time.sleep(2)
            return
    except Exception:
        pass
    # Último recurso: navegar directo a planillas
    try:
        ir_a_planillas_pagadas(page, log)
    except Exception:
        # Reconectar solo si la sesión expiró de verdad
        if reconectar_si_expiro(page, rut_usuario, contrasena, log,
                                estado if estado is not None else {}):
            ir_a_empresa(page, rut_empresa, log, razon_social)
            ir_a_planillas_pagadas(page, log)


class DetenidoPorUsuario(Exception):
    """Se lanza cuando el usuario pide detener la tarea desde la interfaz."""


def descargar(rut_usuario: str, contrasena: str, rut_empresa: str,
              periodos: list, carpeta_dest: str, carpeta_temp: str, log,
              razon_social: str = "", debe_cancelar=None):
    os.makedirs(carpeta_dest, exist_ok=True)
    os.makedirs(carpeta_temp, exist_ok=True)

    def _chequear_cancelacion():
        if debe_cancelar is not None and debe_cancelar():
            raise DetenidoPorUsuario()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            downloads_path=carpeta_temp,
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        # Timeout global: ninguna operación Playwright puede colgar más de 45s
        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(45000)
        estado = {"logins": 0}
        try:
            # ÚNICO login de la descarga. Después solo se reconecta si PreviRed
            # cierra la sesión (los re-login preventivos bloquearon la cuenta).
            login_previred(page, rut_usuario, contrasena, log, estado)
            try:
                ir_a_empresa(page, rut_empresa, log, razon_social)
            except Exception as e_emp:
                log(f"ir_a_empresa falló ({e_emp.__class__.__name__}), reintentando...", "warn")
                reconectar_si_expiro(page, rut_usuario, contrasena, log, estado)
                ir_a_empresa(page, rut_empresa, log, razon_social)
            ir_a_planillas_pagadas(page, log)

            log(f"Períodos a procesar: {len(periodos)}", "info")
            log(f"__PROGRESO_TOTAL__:{len(periodos)}", "info")

            archivos_ok = 0
            archivos_error = 0

            for idx, (mes, anio) in enumerate(periodos, 1):
                _chequear_cancelacion()
                mes_nombre = MESES_NOMBRE.get(mes, str(mes))
                periodo_label = f"{mes_nombre} {anio}"
                log(f"── Período: {periodo_label}", "info")
                log(f"__PROGRESO_AVANCE__:{idx}", "info")

                # Sin re-login preventivo: solo si la sesión expiró de verdad
                if reconectar_si_expiro(page, rut_usuario, contrasena, log, estado):
                    ir_a_empresa(page, rut_empresa, log, razon_social)
                    ir_a_planillas_pagadas(page, log)

                try:
                    nominas = obtener_nominas(page, mes, anio)
                except Exception as e:
                    log(f"Error obteniendo nóminas: {e}", "err")
                    continue

                if not nominas:
                    log("Sin nóminas para este período", "warn")
                    continue

                log(f"Nóminas ({len(nominas)}): {', '.join(nominas)}", "info")

                for nombre_nomina in nominas:
                    _chequear_cancelacion()
                    log(f"Procesando: {nombre_nomina}", "info")
                    try:
                        hay = buscar_planilla(page, mes, anio, nombre_nomina)
                        if not hay:
                            log(f"Sin planillas timbradas: {nombre_nomina}", "warn")
                            log(f"__ARCHIVO_ERROR__:{periodo_label}:{nombre_nomina}:Sin planillas timbradas", "warn")
                            archivos_error += 1
                            volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log, estado)
                            continue
                        ok = descargar_planilla(page, mes, anio, nombre_nomina,
                                                carpeta_temp, carpeta_dest, log)
                        if ok:
                            log(f"__ARCHIVO_OK__:{periodo_label}:{nombre_nomina}", "ok")
                            archivos_ok += 1
                        else:
                            log(f"__ARCHIVO_ERROR__:{periodo_label}:{nombre_nomina}:No se pudo descargar", "err")
                            archivos_error += 1
                        volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log, estado)
                    except Exception as e:
                        log(f"Error '{nombre_nomina}' ({type(e).__name__}): {str(e)[:200]}", "err")
                        log(f"__ARCHIVO_ERROR__:{periodo_label}:{nombre_nomina}:{type(e).__name__}", "err")
                        archivos_error += 1
                        try:
                            volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log, estado)
                        except Exception:
                            pass

            log(f"__RESUMEN__:{archivos_ok}:{archivos_error}", "ok")
            log("Descarga completada", "ok")
        except DetenidoPorUsuario:
            log("Detenido por el usuario", "warn")
            raise
        except Exception as e:
            log(f"Error inesperado: {e}", "err")
            raise
        finally:
            context.close()
            browser.close()
