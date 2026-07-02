"""Lógica de descarga Selenium para Previred — sin credenciales hardcodeadas."""
import os, re, time, shutil
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

URL_LOGIN = "https://www.previred.com/wPortal/login/login.jsp"

MESES_NOMBRE = {
    1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",
    7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"
}


def rut_a_btn_id(rut: str, razon_social: str = "") -> str:
    """
    Construye el ID del botón de empresa en Previred.
    '76.098.152-4'               → 'empresa#76098152#00#false'
    '76.098.152-4', 'Emp (I700)' → 'empresa#76098152#700#false'
    """
    rut = (rut or '').strip()
    num = rut.split("-")[0].replace(".", "")
    sub_id = "00"
    if razon_social:
        m = re.search(r'\(I(\d+)\)', razon_social, re.IGNORECASE)
        if m:
            sub_id = m.group(1)
    return f"empresa#{num}#{sub_id}#false"


def _encontrar_chrome():
    """Busca el binario de Chrome/Chromium instalado en el sistema."""
    candidatos = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]
    for nombre in candidatos:
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    raise RuntimeError("No se encontró Chrome/Chromium instalado en el servidor")

def _encontrar_chromedriver():
    """Busca chromedriver instalado en el sistema."""
    ruta = shutil.which("chromedriver")
    if ruta:
        return ruta
    raise RuntimeError("No se encontró chromedriver instalado en el servidor")


def iniciar_driver(carpeta_temp: str) -> webdriver.Chrome:
    chrome_bin = _encontrar_chrome()
    driver_bin = _encontrar_chromedriver()

    opciones = Options()
    opciones.binary_location = chrome_bin
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1080")
    opciones.add_argument("--disable-notifications")
    prefs = {
        "download.default_directory": carpeta_temp,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    }
    opciones.add_experimental_option("prefs", prefs)
    servicio = Service(driver_bin)
    driver = webdriver.Chrome(service=servicio, options=opciones)
    # Habilitar descargas en modo headless
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": carpeta_temp,
    })
    return driver


def hacer_login(driver, rut_usuario: str, contrasena: str, log):
    log("Iniciando sesión en Previred...", "info")
    driver.get(URL_LOGIN)
    wait = WebDriverWait(driver, 20)
    campo_rut = wait.until(EC.presence_of_element_located((By.NAME, "web_rut2")))
    campo_rut.clear()
    campo_rut.send_keys(rut_usuario)
    time.sleep(0.5)
    campo_pass = wait.until(EC.presence_of_element_located((By.NAME, "web_password")))
    campo_pass.clear()
    campo_pass.send_keys(contrasena)
    time.sleep(0.5)
    try:
        boton = driver.find_element(By.XPATH, "//button[contains(text(),'INGRESAR')] | //input[@value='INGRESAR']")
    except Exception:
        boton = driver.find_element(By.XPATH, "//button[@type='submit']")
    boton.click()
    time.sleep(4)
    log("Sesión iniciada", "ok")


def esta_en_login(driver) -> bool:
    try:
        driver.find_element(By.NAME, "web_rut2")
        return True
    except Exception:
        return False


def ir_a_empresa(driver, rut_empresa: str, log, razon_social: str = ""):
    rut_num = rut_empresa.replace(".", "").split("-")[0]
    patron  = f"empresa#{rut_num}#"
    log(f"Navegando a empresa {rut_empresa}...", "info")
    wait = WebDriverWait(driver, 20)

    btn_menu = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[@id='empresa']")))
    btn_menu.click()
    time.sleep(3)

    # Obtener IDs de botones para este RUT (sin iterar el DOM desde Python)
    ids_encontrados = driver.execute_script("""
        var patron = arguments[0];
        return Array.from(document.querySelectorAll('[id^="' + patron + '"]'))
                    .map(function(el){ return el.id; });
    """, patron)
    log(f"Botones empresa encontrados: {ids_encontrados}", "info")

    # Elegir el botón correcto
    btn_id_elegido = None
    if not ids_encontrados:
        btn_id_elegido = f"{patron}00#false"
        log("Sin botones para el RUT, usando #00# por defecto", "warn")
    elif len(ids_encontrados) == 1:
        btn_id_elegido = ids_encontrados[0]
    else:
        # Extraer el sufijo distintivo entre paréntesis: "Empresa (I700)" → "I700"
        # Si no hay paréntesis, usar el botón #00# (empresa principal)
        m_suf = re.search(r'\(([^)]+)\)', razon_social or "")
        if m_suf:
            sufijo = m_suf.group(1).lower().strip()
            log(f"Buscando empresa con sufijo '{sufijo}'...", "info")
            # Buscar el ancestro ÚNICO de cada botón (primer ancestro que contenga
            # solo 1 botón del RUT). Ese es el "row" exclusivo de esa empresa.
            resultado = driver.execute_script("""
                var patron = arguments[0];
                var sufijo = arguments[1];
                var btns = document.querySelectorAll('[id^="' + patron + '"]');
                var diagnostico = [];
                var encontrado = null;
                for (var i = 0; i < btns.length; i++) {
                    var el = btns[i].parentElement;
                    var depth = 0;
                    while (el && depth < 15) {
                        var cnt = el.querySelectorAll('[id^="' + patron + '"]').length;
                        if (cnt === 1) {
                            // El nombre de empresa está en el padre del ancestro único (la fila completa)
                            var parent = el.parentElement;
                            var textoRow = parent ? parent.textContent.toLowerCase().trim().replace(/\\s+/g, ' ') : '';
                            var textoEl  = el.textContent.toLowerCase().trim().replace(/\\s+/g, ' ');
                            var textoCheck = textoEl + ' ' + textoRow;
                            if (i < 8) {
                                diagnostico.push(btns[i].id + ' | ' + textoRow.substring(0, 150));
                            }
                            if (!encontrado && textoCheck.indexOf(sufijo) !== -1) {
                                encontrado = btns[i].id;
                            }
                            break;
                        }
                        el = el.parentElement;
                        depth++;
                    }
                }
                return {encontrado: encontrado, diagnostico: diagnostico};
            """, patron, sufijo)
            for linea in (resultado.get("diagnostico") or []):
                log(f"  ROW: {linea}", "info")
            btn_id_elegido = resultado.get("encontrado")
            if btn_id_elegido:
                log(f"Empresa identificada por sufijo '{sufijo}': {btn_id_elegido}", "info")
            else:
                btn_id_elegido = ids_encontrados[0]
                log(f"Sufijo '{sufijo}' no encontrado, usando primer botón", "warn")
        else:
            # Sin sufijo → empresa principal (#00#)
            btn_id_elegido = f"{patron}00#false"
            log(f"Sin sufijo en razón social, usando empresa principal: {btn_id_elegido}", "info")

    wait.until(EC.element_to_be_clickable((By.ID, btn_id_elegido))).click()
    time.sleep(4)
    log("Empresa seleccionada", "ok")


def ir_a_planillas_pagadas(driver, log):
    wait = WebDriverWait(driver, 20)
    try:
        btn_rem = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(text(),'Remuneraciones')] | //span[contains(text(),'Remuneraciones')]")
        ))
        btn_rem.click()
        time.sleep(2)
    except Exception:
        pass
    btn_imprimir = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(text(),'Imprimir Documentos')] | //span[contains(text(),'Imprimir Documentos')]")
    ))
    btn_imprimir.click()
    time.sleep(2)
    btn_planillas = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(text(),'Planillas Pagadas')] | //span[contains(text(),'Planillas Pagadas')]")
    ))
    btn_planillas.click()
    time.sleep(6)
    log("En sección Planillas Pagadas", "ok")


def verificar_y_relogin(driver, rut_usuario, contrasena, rut_empresa, razon_social, log):
    if esta_en_login(driver):
        log("Sesión expirada — re-login automático...", "warn")
        hacer_login(driver, rut_usuario, contrasena, log)
        ir_a_empresa(driver, rut_empresa, log, razon_social)
        ir_a_planillas_pagadas(driver, log)
        return True
    return False


def obtener_nominas(driver, mes: int, anio: int) -> list:
    wait = WebDriverWait(driver, 15)
    Select(wait.until(EC.presence_of_element_located((By.ID, "mesR0")))).select_by_value(str(mes).zfill(2))
    time.sleep(1)
    Select(wait.until(EC.presence_of_element_located((By.ID, "yearR0")))).select_by_visible_text(str(anio))
    time.sleep(2)
    select_nomina = Select(wait.until(EC.presence_of_element_located((By.ID, "combo_nominas"))))
    nominas = []
    for o in select_nomina.options:
        texto = o.text.strip()
        valor = o.get_attribute("value") or ""
        if texto and valor and "seleccione" not in texto.lower():
            nominas.append(texto)
    return nominas


def buscar_planilla(driver, mes: int, anio: int, nombre_nomina: str) -> bool:
    wait = WebDriverWait(driver, 15)
    Select(wait.until(EC.presence_of_element_located((By.ID, "mesR0")))).select_by_value(str(mes).zfill(2))
    time.sleep(1)
    Select(wait.until(EC.presence_of_element_located((By.ID, "yearR0")))).select_by_visible_text(str(anio))
    time.sleep(1)
    select_nomina = Select(wait.until(EC.presence_of_element_located((By.ID, "combo_nominas"))))
    opciones = [o for o in select_nomina.options if o.text.strip() == nombre_nomina.strip()]
    if not opciones:
        opciones = [o for o in select_nomina.options if nombre_nomina.strip() in o.text.strip()]
    if opciones:
        select_nomina.select_by_visible_text(opciones[0].text)
    else:
        return False
    time.sleep(1)
    try:
        select_tipo = Select(wait.until(EC.presence_of_element_located((By.ID, "combo_tipo_institucion"))))
        afp_opt = next((o for o in select_tipo.options if "AFP" in o.text.upper()), None)
        if afp_opt:
            select_tipo.select_by_visible_text(afp_opt.text)
        time.sleep(3)
        wait_inst = WebDriverWait(driver, 8)
        wait_inst.until(lambda d: len(Select(d.find_element(By.ID, "combo_instituciones")).options) >= 1)
        sel_inst = Select(driver.find_element(By.ID, "combo_instituciones"))
        textos_inst = [o.text for o in sel_inst.options]
        if "Todas las Instituciones" in textos_inst:
            sel_inst.select_by_visible_text("Todas las Instituciones")
        time.sleep(1)
    except Exception:
        time.sleep(1)
    try:
        driver.execute_script("""
            document.querySelectorAll('.ui-dialog').forEach(function(d){
                var btn=d.querySelector('button'); if(btn) btn.click();
            });
        """)
        time.sleep(1)
    except Exception:
        pass
    boton = wait.until(EC.presence_of_element_located((By.ID, "buscar")))
    driver.execute_script("arguments[0].click();", boton)
    time.sleep(3)
    try:
        cuerpo = driver.find_element(By.TAG_NAME, "body").text
        if "no est" in cuerpo.lower() and "timbradas" in cuerpo.lower():
            return False
    except Exception:
        pass
    return True


def descargar_planilla(driver, mes: int, anio: int, nombre_nomina: str,
                       carpeta_temp: str, carpeta_dest: str, log) -> bool:
    wait = WebDriverWait(driver, 20)
    log("  [D1] buscando btn planillas_masivas...", "info")
    btn_masivas = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[starts-with(@id,'planillas_masivas')]")
    ))
    log("  [D2] click planillas_masivas...", "info")
    btn_masivas.click()
    time.sleep(2)
    # Diagnostico: qué dialogs/modales aparecieron
    try:
        info_dom = driver.execute_script("""
            var btns = Array.from(document.querySelectorAll('button,input[type=button],input[type=submit]'))
                .filter(function(b){ return b.id || b.type === 'submit'; })
                .map(function(b){ return b.id + '|' + b.textContent.trim().substring(0,30); });
            var dialogs = Array.from(document.querySelectorAll('[role="dialog"],.ui-dialog,.modal'))
                .map(function(d){ return d.id + '|' + d.className.substring(0,40); });
            return {btns: btns.slice(0,15), dialogs: dialogs.slice(0,8)};
        """)
        log(f"  [D2b] DOM btns: {info_dom.get('btns')}", "info")
        log(f"  [D2b] DOM dialogs: {info_dom.get('dialogs')}", "info")
    except Exception as ex:
        log(f"  [D2b] err: {ex}", "info")
    log("  [D3] buscando radio...", "info")
    try:
        radio = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//input[@type='radio' and contains(@value,'total')] | //input[@type='radio'][1]")
        ))
        if not radio.is_selected():
            radio.click()
        time.sleep(1)
    except Exception:
        pass
    log("  [D4] buscando aceptar_modal (opcional)...", "info")
    try:
        wait_modal = WebDriverWait(driver, 5)
        btn_imp = wait_modal.until(EC.element_to_be_clickable((By.ID, "aceptar_modal")))
        log("  [D5] click aceptar_modal...", "info")
        btn_imp.click()
    except Exception:
        log("  [D5] sin modal — descarga directa al click planillas_masivas", "info")
    time.sleep(8)
    log("  [D6] verificando ventanas...", "info")
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
    time.sleep(2)
    nombre_limpio = re.sub(r'[/\\:]', '-', nombre_nomina)
    nombre_dest = f"{anio}-{str(mes).zfill(2)}-{nombre_limpio}.pdf"
    ruta_dest = os.path.join(carpeta_dest, nombre_dest)
    for _ in range(20):
        pdfs = [f for f in os.listdir(carpeta_temp) if f.endswith(".pdf")]
        if pdfs:
            break
        time.sleep(1)
    pdfs = [f for f in os.listdir(carpeta_temp) if f.endswith(".pdf")]
    if pdfs:
        ultimo = max([os.path.join(carpeta_temp, f) for f in pdfs], key=os.path.getctime)
        shutil.move(ultimo, ruta_dest)
        log(f"Guardado: {nombre_dest}", "ok")
        return True
    log(f"Sin PDF para '{nombre_nomina}'", "err")
    return False


def volver_a_busqueda(driver, rut_usuario, contrasena, log):
    try:
        wait = WebDriverWait(driver, 6)
        btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(.,'Nueva') and contains(.,'squeda')]")
        ))
        btn.click()
        time.sleep(2)
    except Exception:
        try:
            ir_a_planillas_pagadas(driver, log)
        except Exception:
            if esta_en_login(driver):
                hacer_login(driver, rut_usuario, contrasena, log)
                ir_a_planillas_pagadas(driver, log)


def descargar(rut_usuario: str, contrasena: str, rut_empresa: str,
              periodos: list, carpeta_dest: str, carpeta_temp: str, log,
              razon_social: str = ""):
    """
    Descarga planillas PDF de Previred para los períodos indicados.
    periodos: lista de (mes:int, anio:int)
    razon_social: si la empresa tiene variantes (I700, I358...) se usa para
                  construir el botón correcto en Previred.
    log: callable(msg, tipo) donde tipo ∈ 'info'|'ok'|'warn'|'err'
    """
    os.makedirs(carpeta_dest, exist_ok=True)
    os.makedirs(carpeta_temp, exist_ok=True)

    driver = iniciar_driver(carpeta_temp)
    try:
        hacer_login(driver, rut_usuario, contrasena, log)
        ir_a_empresa(driver, rut_empresa, log, razon_social)
        ir_a_planillas_pagadas(driver, log)

        log(f"Períodos a procesar: {len(periodos)}", "info")

        periodos_con_nomina = 0
        for (mes, anio) in periodos:
            mes_nombre = MESES_NOMBRE.get(mes, str(mes))
            log(f"── Período: {mes_nombre} {anio}", "info")

            if periodos_con_nomina > 0 and periodos_con_nomina % 3 == 0:
                log("Re-login preventivo...", "info")
                driver.get(URL_LOGIN)
                time.sleep(2)
                hacer_login(driver, rut_usuario, contrasena, log)
                ir_a_empresa(driver, rut_empresa, log, razon_social)
                ir_a_planillas_pagadas(driver, log)

            try:
                nominas = obtener_nominas(driver, mes, anio)
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
                    hay = buscar_planilla(driver, mes, anio, nombre_nomina)
                    if not hay:
                        log(f"Sin planillas timbradas: {nombre_nomina}", "warn")
                        volver_a_busqueda(driver, rut_usuario, contrasena, log)
                        continue
                    descargar_planilla(driver, mes, anio, nombre_nomina,
                                       carpeta_temp, carpeta_dest, log)
                    volver_a_busqueda(driver, rut_usuario, contrasena, log)
                except Exception as e:
                    log(f"Error '{nombre_nomina}' ({type(e).__name__}): {str(e)[:200]}", "err")
                    try:
                        volver_a_busqueda(driver, rut_usuario, contrasena, log)
                    except Exception:
                        pass

        log("Descarga completada", "ok")
    except Exception as e:
        log(f"Error inesperado: {e}", "err")
        raise
    finally:
        driver.quit()
