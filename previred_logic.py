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


def hacer_login(page, rut_usuario: str, contrasena: str, log):
    log("Iniciando sesión en Previred...", "info")
    page.goto(URL_LOGIN)
    page.wait_for_selector('[name="web_rut2"]', timeout=20000)
    page.fill('[name="web_rut2"]', rut_usuario)
    time.sleep(0.5)
    page.fill('[name="web_password"]', contrasena)
    time.sleep(0.5)
    try:
        page.click("button:has-text('INGRESAR')")
    except Exception:
        page.click("button[type='submit']")
    time.sleep(4)
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
    time.sleep(3)

    ids_encontrados = page.evaluate("""(patron) => {
        return Array.from(document.querySelectorAll('[id^="' + patron + '"]'))
                    .map(el => el.id);
    }""", patron)
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

    page.click(f"#{btn_id_elegido}")
    time.sleep(4)
    log("Empresa seleccionada", "ok")


def ir_a_planillas_pagadas(page, log):
    try:
        page.click("text=Remuneraciones", timeout=20000)
        time.sleep(2)
    except Exception:
        pass
    page.click("text=Imprimir Documentos", timeout=20000)
    time.sleep(2)
    page.click("text=Planillas Pagadas", timeout=20000)
    time.sleep(6)
    log("En sección Planillas Pagadas", "ok")


def verificar_y_relogin(page, rut_usuario, contrasena, rut_empresa, razon_social, log):
    if esta_en_login(page):
        log("Sesión expirada — re-login automático...", "warn")
        hacer_login(page, rut_usuario, contrasena, log)
        ir_a_empresa(page, rut_empresa, log, razon_social)
        ir_a_planillas_pagadas(page, log)
        return True
    return False


def obtener_nominas(page, mes: int, anio: int) -> list:
    page.wait_for_selector("#mesR0", timeout=15000)
    page.select_option("#mesR0", str(mes).zfill(2))
    time.sleep(1)
    page.select_option("#yearR0", str(anio))
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
    page.select_option("#yearR0", str(anio))
    time.sleep(1)
    page.wait_for_selector("#combo_nominas", timeout=15000)
    opciones = page.evaluate("""() => {
        var sel = document.getElementById('combo_nominas');
        return Array.from(sel.options).map(o => o.text.trim());
    }""")
    objetivo = next((o for o in opciones if o == nombre_nomina.strip()), None)
    if not objetivo:
        objetivo = next((o for o in opciones if nombre_nomina.strip() in o), None)
    if objetivo:
        page.select_option("#combo_nominas", label=objetivo)
    else:
        return False
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


def descargar_planilla(page, mes: int, anio: int, nombre_nomina: str,
                       carpeta_temp: str, carpeta_dest: str, log) -> bool:
    page.wait_for_selector("button[id^='planillas_masivas']", timeout=20000)
    with page.expect_download(timeout=60000) as dl_info:
        page.click("button[id^='planillas_masivas']")
        time.sleep(2)
        try:
            radio = page.locator("input[type='radio'][value*='total'], input[type='radio']").first
            if not radio.is_checked():
                radio.click()
            time.sleep(1)
        except Exception:
            pass
        try:
            page.click("#aceptar_modal", timeout=5000)
        except Exception:
            pass
    download = dl_info.value
    nombre_limpio = re.sub(r'[/\\:]', '-', nombre_nomina)
    nombre_dest = f"{anio}-{str(mes).zfill(2)}-{nombre_limpio}.pdf"
    ruta_dest = os.path.join(carpeta_dest, nombre_dest)
    download.save_as(ruta_dest)
    log(f"Guardado: {nombre_dest}", "ok")
    return True


def volver_a_busqueda(page, rut_usuario, contrasena, log):
    try:
        page.click("button:has-text('Nueva')", timeout=6000)
        time.sleep(2)
    except Exception:
        try:
            ir_a_planillas_pagadas(page, log)
        except Exception:
            if esta_en_login(page):
                hacer_login(page, rut_usuario, contrasena, log)
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
        page = browser.new_page(accept_downloads=True)
        try:
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
                    page.goto(URL_LOGIN)
                    time.sleep(2)
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
                            volver_a_busqueda(page, rut_usuario, contrasena, log)
                            continue
                        descargar_planilla(page, mes, anio, nombre_nomina,
                                           carpeta_temp, carpeta_dest, log)
                        volver_a_busqueda(page, rut_usuario, contrasena, log)
                    except Exception as e:
                        log(f"Error '{nombre_nomina}' ({type(e).__name__}): {str(e)[:200]}", "err")
                        try:
                            volver_a_busqueda(page, rut_usuario, contrasena, log)
                        except Exception:
                            pass

            log("Descarga completada", "ok")
        except Exception as e:
            log(f"Error inesperado: {e}", "err")
            raise
        finally:
            browser.close()
