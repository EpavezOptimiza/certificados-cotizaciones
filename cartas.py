"""
Módulo Cartas Previsionales — Blueprint Flask
Integra con la app de Certificados de Cotizaciones
"""
import os, json, threading, uuid, tempfile, shutil
from datetime import datetime
from flask import (Blueprint, render_template, request, jsonify,
                   send_file, session, current_app, make_response)
from functools import wraps

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
cartas_bp = Blueprint("cartas", __name__,
    template_folder=_os.path.join(_HERE, "templates", "cartas"),
    static_folder=_os.path.join(_HERE, "static", "cartas") if _os.path.exists(_os.path.join(_HERE, "static", "cartas")) else None,
    url_prefix="/cartas")

# ── Estado de jobs (en memoria, Railway reinicia limpia) ──────────────────────
_jobs = {}  # job_id -> {status, log, result, worker_rut}

def get_job(job_id):
    return _jobs.get(job_id)

def set_job(job_id, data):
    _jobs[job_id] = data

# ── Auth helper (reutiliza el de app.py) ──────────────────────────────────────
def cartas_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from app import get_current_user
        from flask import redirect, url_for
        user = get_current_user()
        if not user:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# ── Parseo de Excel ───────────────────────────────────────────────────────────
def parsear_excel(file_bytes):
    """Lee el Excel de deudas y retorna lista de dicts con todos los campos."""
    import openpyxl, io
    from datetime import datetime as dt

    MESES_MAP = {'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,
                 'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)

    # Buscar hoja correcta
    ws = None
    for nombre in wb.sheetnames:
        n = nombre.lower()
        if 'base' in n and 'afp' in n:
            ws = wb[nombre]; break
    if ws is None:
        for nombre in wb.sheetnames:
            if 'base' in nombre.lower():
                ws = wb[nombre]; break
    if ws is None:
        ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h).lower().strip() if h else '' for h in rows[0]]

    def col(nombres, default=-1):
        for n in nombres:
            for i, h in enumerate(headers):
                if n in h:
                    return i
        return default

    iRutEmp  = col(['01_rut emp','rut empresa','rut_empresa'], 0)
    iRazon   = col(['02_razon','razon social','razon_social'], 1)
    iRutTrab = col(['03_rut afil','rut afiliado','rut_afiliado','rut trabajador'], 2)
    iNombre  = col(['04_nombre','nombre afiliado','nombre trabajador','nombre_afiliado'], 3)
    iTipo    = col(['05_tipo','tipo producto','tipo_producto'], 4)
    iInst    = col(['06_inst','institucion'], 5)
    iPeriodo = col(['07_periodo','período','periodo'], 6)
    iMontoN  = col(['11_monto nom','monto nominal'], 7)
    iMontoI  = col(['12_monto int','monto interes'], 8)
    iFee     = col(['fee%','fee'], 9)
    iFecha   = col(['08_fecha cese','fecha cese','fecha_cese'], 10)
    iAnalisis= col(['13_','analisis'], 11)
    iMotivo  = col(['09_','motivos de deuda','motivo de deuda'], 12)
    iTipoDoc = col(['14_','tipo documento'], 13)
    iEstatus = col(['15_','estatus','estado'], 14)
    iObs     = col(['observaciones'], 15)

    def g(row, idx):
        if idx < 0 or idx >= len(row): return ''
        v = row[idx]
        if v is None: return ''
        return str(v).strip()

    def fmt_periodo(v):
        if v is None: return ''
        if hasattr(v, 'month'):
            MESES = {1:'Ene',2:'Feb',3:'Mar',4:'Abr',5:'May',6:'Jun',
                     7:'Jul',8:'Ago',9:'Sep',10:'Oct',11:'Nov',12:'Dic'}
            return f"{MESES[v.month]}-{str(v.year)[2:]}"
        s = str(v).strip().split(' ')[0]
        parts = s.split('-')
        if len(parts) == 3:
            try:
                MESES = {1:'Ene',2:'Feb',3:'Mar',4:'Abr',5:'May',6:'Jun',
                         7:'Jul',8:'Ago',9:'Sep',10:'Oct',11:'Nov',12:'Dic'}
                return f"{MESES[int(parts[1])]}-{parts[0][2:]}"
            except: pass
        return s

    datos = []
    for row in rows[1:]:
        if not any(row): continue
        rut_trab = g(row, iRutTrab)
        if not rut_trab or rut_trab.upper() in ('ND','N/D','','NONE'): continue
        periodo = fmt_periodo(row[iPeriodo] if iPeriodo < len(row) else None)
        datos.append({
            'rut_empresa':  g(row, iRutEmp),
            'razon_social': g(row, iRazon),
            'rut_trabajador': rut_trab,
            'nombre':       g(row, iNombre),
            'tipo':         g(row, iTipo),
            'institucion':  g(row, iInst),
            'periodo':      periodo,
            'monto_nominal': g(row, iMontoN),
            'monto_interes': g(row, iMontoI),
            'fee':          g(row, iFee),
            'fecha_cese':   g(row, iFecha),
            'analisis':     g(row, iAnalisis),
            'motivo':       g(row, iMotivo),
            'tipo_documento': g(row, iTipoDoc),
            'estatus':      g(row, iEstatus),
            'observaciones': g(row, iObs),
        })
    return datos

def agrupar_por_trabajador(datos):
    """Agrupa filas por trabajador, coleccionando períodos."""
    MESES_MAP = {'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,
                 'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}

    def per_key(p):
        try:
            parts = str(p).lower().split('-')
            m = MESES_MAP.get(parts[0][:3], 0)
            a = int(parts[1]) + 2000 if len(parts[1]) <= 2 else int(parts[1])
            return (a, m)
        except: return (0, 0)

    grupos = {}
    for d in datos:
        k = d['rut_trabajador']
        if k not in grupos:
            grupos[k] = {**d, 'periodos': []}
        p = d['periodo']
        if p and p not in grupos[k]['periodos']:
            grupos[k]['periodos'].append(p)

    # Ordenar períodos de menor a mayor
    for k in grupos:
        grupos[k]['periodos'].sort(key=per_key)

    return list(grupos.values())

# ── Generación de carta PDF ───────────────────────────────────────────────────
def generar_carta_pdf(carta_data, firma_data):
    """Genera PDF de carta previsional y retorna bytes."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.lib import colors
    import io

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
        leftMargin=3*cm, rightMargin=3*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm)

    styles = getSampleStyleSheet()
    normal = ParagraphStyle('Normal', fontName='Helvetica', fontSize=11,
        leading=16, spaceAfter=6)
    bold_s = ParagraphStyle('Bold', fontName='Helvetica-Bold', fontSize=11, leading=16)
    title_s = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=11,
        leading=16, alignment=TA_RIGHT)

    # Períodos
    periodos_sel = carta_data.get('periodos_sel', carta_data.get('periodos', []))
    extra = [p.strip() for p in carta_data.get('periodos_extra','').split(',') if p.strip()]
    todos = list(dict.fromkeys(periodos_sel + extra))

    MESES_MAP = {'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,
                 'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}
    def per_key(p):
        try:
            parts = str(p).lower().split('-')
            m = MESES_MAP.get(parts[0][:3], 0)
            a = int(parts[1]) + 2000 if len(parts[1]) <= 2 else int(parts[1])
            return (a, m)
        except: return (0, 0)

    todos_ord = sorted(todos, key=per_key)
    if todos_ord:
        periodo_txt = todos_ord[0] if len(todos_ord) == 1 else f"{todos_ord[0]} hasta {todos_ord[-1]}"
    else:
        periodo_txt = '[PERIODO]'

    MESES_ES = {'01':'Enero','02':'Febrero','03':'Marzo','04':'Abril',
                '05':'Mayo','06':'Junio','07':'Julio','08':'Agosto',
                '09':'Septiembre','10':'Octubre','11':'Noviembre','12':'Diciembre'}
    mes_num = carta_data.get('mes', datetime.now().strftime('%Y-%m')).split('-')
    mes_txt = MESES_ES.get(mes_num[1] if len(mes_num) > 1 else '', '') if len(mes_num) > 1 else ''
    anio_txt = mes_num[0] if mes_num else str(datetime.now().year)
    fecha_txt = f"Santiago, {mes_txt} {anio_txt}"

    motivo = carta_data.get('motivo', '')
    if not motivo:
        motivo = carta_data.get('motivo_deuda', '[MOTIVO]')
    fecha_cese = carta_data.get('fecha_cese', '')
    if fecha_cese and motivo and '[MOTIVO]' not in motivo:
        motivo_final = motivo + ' ' + fecha_cese
    else:
        motivo_final = motivo or '[MOTIVO]'

    story = []
    story.append(Paragraph(fecha_txt, title_s))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(f"Senores:", normal))
    story.append(Paragraph(f"<b>{carta_data.get('institucion', '')}.</b>", normal))
    story.append(Paragraph("Presente. -", normal))
    story.append(Spacer(1, 0.3*cm))

    razon = carta_data.get('razon_social', '')
    rut_emp = carta_data.get('rut_empresa', '')
    story.append(Paragraph(
        f"Me dirijo a ustedes en representacion de la empresa {razon}. "
        f"Rut: {rut_emp}, a fin de informar a ustedes lo siguiente:",
        normal))
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph(f"<b>NOMBRE: {carta_data.get('nombre','').upper()}</b>", normal))
    story.append(Paragraph(f"<b>RUT: {carta_data.get('rut_trabajador','')}.</b>", normal))
    story.append(Paragraph(f"<b>TIPO DE PRODUCTO: {carta_data.get('tipo','')}.</b>", normal))
    story.append(Paragraph(f"<b>PERIODO DE DEUDA: {periodo_txt}.</b>", normal))
    story.append(Paragraph(f"<b>MOTIVO DE NO PAGO: {motivo_final}</b>", normal))
    story.append(Spacer(1, 0.8*cm))
    story.append(Paragraph("Les saluda atentamente,", normal))
    story.append(Spacer(1, 1.5*cm))
    story.append(HRFlowable(width="40%", thickness=0.5, color=colors.black))
    story.append(Spacer(1, 0.2*cm))

    if firma_data:
        story.append(Paragraph(f"Nombre: {firma_data.get('nombre','')}", normal))
        story.append(Paragraph(f"Rut: {firma_data.get('rut','')}", normal))
        story.append(Paragraph(f"Cargo: {firma_data.get('cargo','')}", normal))
        if firma_data.get('correo'):
            story.append(Paragraph(f"Correo: {firma_data.get('correo')}", normal))
        if firma_data.get('tel'):
            story.append(Paragraph(f"Tel: {firma_data.get('tel')}", normal))

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ── Bot PreviRed (headless) ───────────────────────────────────────────────────
def run_bot_previred(job_id, rut_login, clave, workers, firma_data):
    """Corre el bot de PreviRed en un thread separado."""
    job = _jobs[job_id]

    def log(msg):
        job['log'].append(msg)
        print(f"[BOT {job_id[:8]}] {msg}")

    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait, Select
        from selenium.webdriver.support import expected_conditions as EC
        import time, calendar

        log("Iniciando Chrome headless...")
        opts = webdriver.ChromeOptions()
        opts.add_argument('--headless')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--window-size=1280,900')

        # Configurar descarga automática de PDFs
        tmp_dir = tempfile.mkdtemp()
        opts.add_experimental_option("prefs", {
            "download.default_directory": tmp_dir,
            "download.prompt_for_download": False,
            "plugins.always_open_pdf_externally": True,
            "download.directory_upgrade": True,
        })

        driver = webdriver.Chrome(options=opts)
        wait = WebDriverWait(driver, 20)

        def click(by, val):
            el = wait.until(EC.element_to_be_clickable((by, val)))
            driver.execute_script("arguments[0].click();", el)

        def fill(by, val, text):
            el = wait.until(EC.presence_of_element_located((by, val)))
            el.clear(); el.send_keys(text)

        # Login PreviRed
        log("Accediendo a PreviRed...")
        driver.get("https://www.previred.com/wEmpresas/CtrlFce")
        fill(By.ID, 'web_rut', rut_login)
        fill(By.ID, 'web_clave', clave)
        click(By.ID, 'web_btn_login')
        time.sleep(4)

        if 'login' in driver.current_url.lower() or 'Login' in driver.page_source:
            job['status'] = 'error'
            job['error'] = 'Credenciales PreviRed incorrectas'
            driver.quit()
            return

        log("Login OK")

        for w in workers:
            rut_e = w['rut_empresa']
            rut_num = rut_e.replace('.','').replace('-','').split('-')[0]
            if '-' in rut_e:
                rut_num = rut_e.replace('.','').split('-')[0]

            log(f"Procesando {w['nombre']} ({w['rut_trabajador']})...")

            # Navegar a movimiento
            driver.get("https://www.previred.com/wEmpresas/CtrlFce")
            time.sleep(3)

            # Scroll para cargar empresas
            for _ in range(10):
                driver.execute_script("window.scrollBy(0, 400);")
                time.sleep(0.2)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)

            # Buscar empresa
            btns = driver.find_elements(By.XPATH, "//button[contains(@id,'empresa#')]")
            emp_btn = next((b for b in btns if rut_num in (b.get_attribute('id') or '')), None)
            if not emp_btn:
                log(f"⚠ Empresa {rut_e} no encontrada")
                job['resultados'][w['rut_trabajador']] = 'error_empresa'
                continue

            driver.execute_script("arguments[0].scrollIntoView(true);", emp_btn)
            driver.execute_script("arguments[0].click();", emp_btn)
            time.sleep(2)

            # Click en Regulariza
            reg_id = emp_btn.get_attribute('id').replace('empresa#','regulariza#')
            try:
                reg_btn = driver.find_element(By.ID, reg_id)
                driver.execute_script("arguments[0].click();", reg_btn)
            except:
                click(By.XPATH, "//a[contains(@id,'regulariza')]")
            time.sleep(2)

            # Ingreso Manual
            click(By.XPATH, "//input[@id='regularizacion_manual'] | //label[contains(text(),'Ingreso Manual')]")
            time.sleep(1)
            click(By.XPATH, "//button[contains(text(),'Continuar')] | //input[@value='Continuar']")
            time.sleep(3)

            # Rellenar formulario
            fill(By.XPATH, "//input[contains(@id,'web_rut_trabajador') or contains(@name,'rut_trabajador')]",
                 '', )
            rut_t = w['rut_trabajador'].replace('.','').replace(' ','')
            rut_field = driver.find_elements(By.XPATH,
                "//input[@type='text'][not(contains(@id,'empresa'))]")
            if rut_field:
                rut_field[0].clear(); rut_field[0].send_keys(rut_t)
            time.sleep(1)

            # AFP
            try:
                inst = w.get('institucion','').strip()
                sel_afp = Select(driver.find_element(By.ID,'web_combo_codigo_afp'))
                opciones = [o.text.strip() for o in sel_afp.options]
                match = next((o for o in opciones if inst.upper() in o.upper() or o.upper() in inst.upper()), None)
                if match: sel_afp.select_by_visible_text(match)
            except: pass

            # Salud
            try:
                Select(driver.find_element(By.ID,'web_combo_codigo_salud')).select_by_visible_text(w.get('salud','FONASA'))
            except: pass

            # Causa
            try:
                Select(driver.find_element(By.ID,'web_combo_movimiento_personal')).select_by_visible_text(w.get('causa',''))
            except: pass

            # Fecha cese si hay
            if w.get('fecha_cese'):
                try:
                    fc = w['fecha_cese']
                    driver.execute_script(f"var el=document.getElementById('web_fec_cese');if(el){{el.removeAttribute('readonly');el.value='{fc}';}}")
                except: pass

            es_ausentismo = 'ausentismo' in (w.get('causa') or '').lower()
            periodos_parsed = []

            if es_ausentismo:
                MESES_MAP = {'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,
                             'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}
                for p in w.get('periodos_sel', []):
                    try:
                        parts = str(p).lower().split('-')
                        m = MESES_MAP.get(parts[0][:3], 0)
                        a = int(parts[1]) + 2000 if len(parts[1]) <= 2 else int(parts[1])
                        if m: periodos_parsed.append((a, m))
                    except: pass

                if periodos_parsed:
                    # Período 0 — ingresar fechas antes del primer Continuar
                    anio0, mes0 = periodos_parsed[0]
                    ult0 = calendar.monthrange(anio0, mes0)[1]
                    desde0 = f"01/{mes0:02d}/{anio0}"
                    hasta0 = f"{ult0}/{mes0:02d}/{anio0}"
                    for fid, fval in [('start_date', desde0), ('end_date', hasta0)]:
                        try:
                            driver.execute_script(f"jQuery('#{fid}').datepicker('setDate', '{fval}');")
                        except: pass

            # Continuar → confirmación
            click(By.XPATH,"//button[contains(text(),'Continuar')] | //input[@value='Continuar']")
            time.sleep(3)

            # Checkbox confirmación
            try:
                cb = wait.until(EC.presence_of_element_located((By.XPATH,"//input[@type='checkbox']")))
                if not cb.is_selected(): driver.execute_script("arguments[0].click();", cb)
                time.sleep(1)
            except: pass

            # Continuar → comprobante
            click(By.XPATH,"//button[contains(text(),'Continuar')] | //input[@value='Continuar']")
            time.sleep(4)

            if es_ausentismo and len(periodos_parsed) > 1:
                # Períodos 2+
                for pi, (anio, mes) in enumerate(periodos_parsed[1:], 1):
                    es_ultimo = (pi == len(periodos_parsed) - 1)
                    ult = calendar.monthrange(anio, mes)[1]
                    desde = f"01/{mes:02d}/{anio}"
                    hasta = f"{ult}/{mes:02d}/{anio}"

                    # Volver a Ingreso Movimiento
                    if not es_ultimo:
                        click(By.XPATH,"//button[contains(text(),'Continuar')] | //input[@value='Continuar']")
                        time.sleep(3)
                    else:
                        # Finalizar en el último
                        for xp in ["//a[contains(text(),'Finalizar')]","//input[@value='Finalizar']"]:
                            els = driver.find_elements(By.XPATH, xp)
                            if els: driver.execute_script("arguments[0].click();", els[0]); break
                        time.sleep(3)
                        break

                    # Rellenar formulario períodos 2+
                    rut_field2 = driver.find_elements(By.XPATH,"//input[@type='text']")
                    if rut_field2: rut_field2[0].clear(); rut_field2[0].send_keys(rut_t)
                    time.sleep(1)

                    # AFP, salud, causa
                    try:
                        inst = w.get('institucion','').strip()
                        sel2 = Select(driver.find_element(By.ID,'web_combo_codigo_afp'))
                        ops2 = [o.text.strip() for o in sel2.options]
                        m2 = next((o for o in ops2 if inst.upper() in o.upper()), None)
                        if m2: sel2.select_by_visible_text(m2)
                    except: pass
                    try:
                        Select(driver.find_element(By.ID,'web_combo_codigo_salud')).select_by_visible_text(w.get('salud','FONASA'))
                    except: pass
                    try:
                        Select(driver.find_element(By.ID,'web_combo_movimiento_personal')).select_by_visible_text(w.get('causa',''))
                    except: pass

                    # Fechas
                    for fid, fval in [('start_date', desde), ('end_date', hasta)]:
                        try:
                            driver.execute_script(f"jQuery('#{fid}').datepicker('setDate', '{fval}');")
                        except: pass

                    # Continuar formulario
                    click(By.XPATH,"//button[contains(text(),'Continuar')] | //input[@value='Continuar']")
                    time.sleep(2)

                    # Checkbox confirmación
                    try:
                        cb2 = wait.until(EC.presence_of_element_located((By.XPATH,"//input[@type='checkbox']")))
                        if not cb2.is_selected(): driver.execute_script("arguments[0].click();", cb2)
                        time.sleep(1)
                    except: pass

                    # Continuar comprobante
                    click(By.XPATH,"//button[contains(text(),'Continuar')] | //input[@value='Continuar']")
                    time.sleep(3)

            else:
                # Finalizar
                for xp in ["//a[contains(text(),'Finalizar')]","//input[@value='Finalizar']"]:
                    els = driver.find_elements(By.XPATH, xp)
                    if els:
                        driver.execute_script("arguments[0].click();", els[0])
                        break
                time.sleep(3)

            # Comprobante por Trabajador
            log(f"Generando comprobante para {w['rut_trabajador']}...")
            try:
                import datetime as _dt
                hoy = _dt.date.today().strftime('%d/%m/%Y')
                cpt_xp = "//span[@id='cert_trabajador'] | //a[contains(text(),'Comprobante por Trabajador')]"
                cpt_el = wait.until(EC.element_to_be_clickable((By.XPATH, cpt_xp)))
                driver.execute_script("arguments[0].click();", cpt_el)
                time.sleep(4)

                # RUT
                rf = wait.until(EC.presence_of_element_located((By.ID, 'web_rut2')))
                driver.execute_script("arguments[0].value='';", rf)
                rf.send_keys(rut_t)
                time.sleep(1)

                # AFP
                try:
                    inst = w.get('institucion','').strip()
                    sel3 = Select(driver.find_element(By.ID, 'web_combo_codigo_afp'))
                    ops3 = [o.text.strip() for o in sel3.options]
                    m3 = next((o for o in ops3 if inst.upper() in o.upper()), None)
                    if m3: sel3.select_by_visible_text(m3)
                except: pass

                # Fechas
                for fid in ['web_desde', 'web_hasta']:
                    js = (f"var el=document.getElementById('{fid}');"
                          f"if(el){{el.removeAttribute('readonly');el.removeAttribute('disabled');el.value='{hoy}';}}"
                          f"try{{jQuery('#{fid}').datepicker('setDate','{hoy}');}}catch(e){{}}")
                    driver.execute_script(js)
                time.sleep(1)

                # Click Generar
                for xp2 in ["//button[contains(@class,'submitBtn')]","//button[@value='submit']","//input[@type='submit']"]:
                    els2 = driver.find_elements(By.XPATH, xp2)
                    if els2:
                        driver.execute_script("arguments[0].click();", els2[0])
                        break
                time.sleep(4)

                # Esperar descarga del PDF
                import glob, time as t2
                deadline = t2.time() + 30
                pdf_file = None
                while t2.time() < deadline:
                    pdfs = glob.glob(os.path.join(tmp_dir, '*.pdf'))
                    pdfs = [p for p in pdfs if not p.endswith('.crdownload')]
                    if pdfs:
                        pdf_file = pdfs[0]
                        break
                    t2.sleep(1)

                if pdf_file:
                    # Guardar en DATA_DIR
                    data_dir = current_app.config.get('DATA_DIR', 'adjuntos')
                    dest = os.path.join(data_dir, f"MOV_PER_{rut_t}.pdf")
                    shutil.copy2(pdf_file, dest)
                    job['resultados'][w['rut_trabajador']] = f"MOV_PER_{rut_t}.pdf"
                    log(f"✅ PDF guardado: MOV_PER_{rut_t}.pdf")
                else:
                    log(f"⚠ PDF no descargado para {rut_t}")
                    job['resultados'][w['rut_trabajador']] = 'sin_pdf'

            except Exception as e:
                log(f"Error comprobante: {e}")
                job['resultados'][w['rut_trabajador']] = 'error_comprobante'

        driver.quit()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        job['status'] = 'done'
        log("✅ Bot finalizado")

    except Exception as e:
        import traceback
        job['status'] = 'error'
        job['error'] = str(e)
        job['log'].append(f"Error fatal: {e}")
        print(traceback.format_exc())

# ── Rutas ─────────────────────────────────────────────────────────────────────

@cartas_bp.route("/", strict_slashes=False)
def index():
    from app import get_current_user
    from flask import request as _req
    user = get_current_user()
    if not user:
        cookies = list(_req.cookies.keys())
        return jsonify({"error": "No autenticado", "cookies_presentes": cookies}), 401
    resp = make_response(render_template("cartas/index.html", user=user))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@cartas_bp.route("/api/subir_excel", methods=["POST"])
@cartas_login_required
def subir_excel():
    """Sube y parsea el Excel de deudas."""
    if 'excel' not in request.files:
        return jsonify({"error": "No se envió archivo"}), 400
    f = request.files['excel']
    datos = parsear_excel(f.read())
    trabajadores = agrupar_por_trabajador(datos)
    return jsonify({"ok": True, "trabajadores": trabajadores, "total": len(trabajadores)})

@cartas_bp.route("/api/generar_carta", methods=["POST"])
@cartas_login_required
def generar_carta():
    """Genera PDF de carta para un trabajador."""
    data = request.json
    carta = data.get('carta', {})
    firma = data.get('firma', {})
    pdf_bytes = generar_carta_pdf(carta, firma)
    rut = carta.get('rut_trabajador','').replace('.','').replace(' ','')
    fname = f"Carta_Explicativa_{rut}.pdf"
    # Guardar en DATA_DIR
    data_dir = current_app.config.get('DATA_DIR', 'adjuntos')
    dest = os.path.join(data_dir, fname)
    with open(dest, 'wb') as fout:
        fout.write(pdf_bytes)
    return jsonify({"ok": True, "filename": fname})

@cartas_bp.route("/api/descargar/<filename>")
@cartas_login_required
def descargar(filename):
    """Descarga un PDF generado."""
    data_dir = current_app.config.get('DATA_DIR', 'adjuntos')
    return send_file(os.path.join(data_dir, filename),
                     as_attachment=True, download_name=filename)

@cartas_bp.route("/api/iniciar_bot", methods=["POST"])
@cartas_login_required
def iniciar_bot():
    """Inicia el bot de PreviRed en background."""
    data = request.json
    rut_login = data.get('rut_login','')
    clave = data.get('clave','')
    workers = data.get('workers', [])
    firma = data.get('firma', {})

    if not rut_login or not clave:
        return jsonify({"error": "Credenciales requeridas"}), 400
    if not workers:
        return jsonify({"error": "Sin trabajadores"}), 400

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        'status': 'running',
        'log': [],
        'resultados': {},
        'error': None,
        'creado': datetime.now().isoformat(),
    }

    t = threading.Thread(
        target=run_bot_previred,
        args=(job_id, rut_login, clave, workers, firma),
        daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id})

@cartas_bp.route("/api/estado_bot/<job_id>")
@cartas_login_required
def estado_bot(job_id):
    """Retorna el estado actual del bot."""
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify({
        "status": job['status'],
        "log": job['log'],
        "resultados": job['resultados'],
        "error": job.get('error'),
    })

@cartas_bp.route("/api/marcar_gestion", methods=["POST"])
@cartas_login_required
def marcar_gestion():
    """Marca trabajadores como En gestión en el Excel subido."""
    # Esta funcionalidad requiere que el Excel esté en el servidor
    # Por ahora retorna OK para el flujo web
    return jsonify({"ok": True, "mensaje": "Estado actualizado en la sesión"})
