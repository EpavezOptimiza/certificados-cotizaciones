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
    log("Sesión iniciada", "ok")


def esta_en_login(page) -> bool:
    try:
        return page.locator('[name="web_rut2"]').count() > 0
    except Exception:
        return False


def ir_a_empresa(page, rut_empresa: str, log, razon_social: str = ""):
    rut_num = rut_empresa.replace(".", "").split("-")[0]
    patron = f"empresa#{rut_num}#"
    log(f"Navegando a empresa {rut_empresa}...", "info")

    page.wait_for_selector("li#empresa", timeout=20000)
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


def verificar_y_relogin(page, rut_usuario, contrasena, rut_empresa, razon_social, log):
    if esta_en_login(page):
        log("Sesión expirada — re-login automático...", "warn")
        hacer_login(page, rut_usuario, contrasena, log)
        ir_a_empresa(page, rut_empresa, log, razon_social)
        ir_a_planillas_pagadas(page, log)
        return True
    return False


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

    ids_info = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('img[src*="planillas.gif"]')).map(function(img) {
            return img.id || '';
        });
    }""")
    total_iconos = len(ids_info)
    log(f"Instituciones a descargar: {total_iconos}", "info")
    if total_iconos == 0:
        log("No se encontraron iconos planillas.gif", "warn")
        return 0

    for i in range(total_iconos):
        inst_num = i + 1
        img_id = ids_info[i] if i < len(ids_info) else ''
        partes_id = img_id.split('#')
        nombre_inst = partes_id[-1] if len(partes_id) > 1 else f"inst{inst_num:02d}"
        nombre_dest = f"{prefijo}-{nombre_inst}.pdf"
        ruta_dest = os.path.join(carpeta_dest, nombre_dest)

        # 1. Click en ícono planilla → modal aparece en página principal
        try:
            page.evaluate(f"document.querySelectorAll('img[src*=\"planillas.gif\"]')[{i}].click()")
        except Exception as ec:
            log(f"inst{inst_num} ({nombre_inst}): click falló {ec.__class__.__name__}", "warn")
            continue

        # 2. Esperar modal en página principal
        try:
            page.wait_for_selector("#aceptar_modal", state="visible", timeout=8000)
        except Exception:
            log(f"inst{inst_num} ({nombre_inst}): modal no apareció", "warn")
            time.sleep(1)
            continue

        # 3. Seleccionar Total Empresa si no está marcado
        try:
            radio = page.locator("input[type='radio'][value*='total']").first
            if radio.count() > 0 and not radio.is_checked():
                radio.click()
            page.wait_for_timeout(500)
        except Exception:
            pass

        # 4. Click Imprimir — igual que el flujo normal: el evento download
        # se captura en la página principal independiente de que abra pestaña nueva.
        guardado = False
        try:
            with page.expect_download(timeout=20000) as dl_info:
                page.click("#aceptar_modal")
                log(f"inst{inst_num} ({nombre_inst}): Imprimir clickeado", "info")
            dl = dl_info.value
            dl.save_as(ruta_dest)
            guardado = True
        except Exception as e_dl:
            log(f"inst{inst_num} ({nombre_inst}): descarga falló {e_dl.__class__.__name__}", "warn")

        # 6. Cerrar modal si quedó abierto
        try:
            cerrar = page.locator("button:has-text('Cerrar')").first
            if cerrar.is_visible():
                cerrar.click()
            page.wait_for_timeout(500)
        except Exception:
            pass

        if guardado:
            log(f"Guardado: {nombre_dest}", "ok")
            descargados += 1

        time.sleep(1)

    return descargados


def descargar_planilla(page, mes: int, anio: int, nombre_nomina: str,
                       carpeta_temp: str, carpeta_dest: str, log) -> bool:
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

    # Abrir modal de impresión
    page.click("button[id^='planillas_masivas']")
    log("Modal de impresión abierto", "info")
    time.sleep(2)

    # Seleccionar "Total Empresa"
    try:
        radio = page.locator("input[type='radio'][value*='total']").first
        if radio.count() > 0 and not radio.is_checked():
            radio.click()
        time.sleep(1)
    except Exception:
        pass

    # Esperar que el botón Imprimir sea visible
    try:
        page.wait_for_selector("#aceptar_modal", state="visible", timeout=10000)
    except Exception as e_imp:
        log(f"Botón Imprimir no apareció: {e_imp.__class__.__name__}", "err")
        return False

    descargado = False
    try:
        with page.expect_download(timeout=30000) as dl_info:
            page.click("#aceptar_modal")
            log("Imprimir clickeado", "info")
            time.sleep(3)
            if _hay_dialogo_email(page):
                raise RuntimeError("email_dialog")

        dl = dl_info.value
        dl.save_as(ruta_dest)
        descargado = True

    except RuntimeError as e:
        if "email_dialog" in str(e):
            log("Planilla muy grande — Previred pide envío por email. Descargando PDFs individuales por institución...", "warn")
            try:
                _click_texto(page, "Nueva Búsqueda", timeout=5000)
                time.sleep(2)
            except Exception:
                pass
            try:
                buscar_planilla(page, mes, anio, nombre_nomina)
                n = _descargar_pdfs_individuales(page, mes, anio, nombre_nomina, carpeta_temp, carpeta_dest, log)
                if n > 0:
                    log(f"Descargados {n} PDF(s) individuales para '{nombre_nomina}'", "ok")
                    return True
                log(f"No se pudo descargar ningún PDF individual para '{nombre_nomina}'", "err")
                return False
            except Exception as e2:
                log(f"Error en descarga individual: {e2}", "err")
                return False
        log(f"Captura directa falló ({e.__class__.__name__}), buscando PDF en carpeta...", "warn")

    except Exception as e:
        log(f"Captura directa falló ({e.__class__.__name__}), buscando PDF en carpeta...", "warn")

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


def volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log):
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
        if esta_en_login(page):
            hacer_login(page, rut_usuario, contrasena, log)
            ir_a_empresa(page, rut_empresa, log, razon_social)
            ir_a_planillas_pagadas(page, log)


def descargar(rut_usuario: str, contrasena: str, rut_empresa: str,
              periodos: list, carpeta_dest: str, carpeta_temp: str, log,
              razon_social: str = ""):
    os.makedirs(carpeta_dest, exist_ok=True)
    os.makedirs(carpeta_temp, exist_ok=True)

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
        try:
            hacer_login(page, rut_usuario, contrasena, log)
            try:
                ir_a_empresa(page, rut_empresa, log, razon_social)
            except Exception as e_emp:
                log(f"ir_a_empresa falló ({e_emp.__class__.__name__}), reintentando con re-login...", "warn")
                hacer_login(page, rut_usuario, contrasena, log)
                ir_a_empresa(page, rut_empresa, log, razon_social)
            ir_a_planillas_pagadas(page, log)

            log(f"Períodos a procesar: {len(periodos)}", "info")

            periodos_con_nomina = 0
            for (mes, anio) in periodos:
                mes_nombre = MESES_NOMBRE.get(mes, str(mes))
                log(f"── Período: {mes_nombre} {anio}", "info")

                if periodos_con_nomina > 0 and periodos_con_nomina % 3 == 0:
                    log("Re-login preventivo...", "info")
                    # hacer_login ya navega a URL_LOGIN, no duplicar goto
                    hacer_login(page, rut_usuario, contrasena, log)
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

                periodos_con_nomina += 1
                log(f"Nóminas ({len(nominas)}): {', '.join(nominas)}", "info")

                for nombre_nomina in nominas:
                    log(f"Procesando: {nombre_nomina}", "info")
                    try:
                        hay = buscar_planilla(page, mes, anio, nombre_nomina)
                        if not hay:
                            log(f"Sin planillas timbradas: {nombre_nomina}", "warn")
                            volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log)
                            continue
                        descargar_planilla(page, mes, anio, nombre_nomina,
                                           carpeta_temp, carpeta_dest, log)
                        volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log)
                    except Exception as e:
                        log(f"Error '{nombre_nomina}' ({type(e).__name__}): {str(e)[:200]}", "err")
                        try:
                            volver_a_busqueda(page, rut_usuario, contrasena, rut_empresa, razon_social, log)
                        except Exception:
                            pass

            log("Descarga completada", "ok")
        except Exception as e:
            log(f"Error inesperado: {e}", "err")
            raise
        finally:
            context.close()
            browser.close()
