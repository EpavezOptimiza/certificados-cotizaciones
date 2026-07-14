"""Bot para consulta masiva de RUTs en Mi DT (midt.dirtrab.cl)."""

import io, time, re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

_URL = "https://midt.dirtrab.cl"


# ── Login ──────────────────────────────────────────────────────────────────────

def hacer_login(page, run, clave, log):
    log("Abriendo Mi DT...", "info")
    page.goto(_URL, wait_until="networkidle", timeout=45000)
    log(f"[debug] URL inicial: {page.url}", "info")

    # Buscar el botón de login con varios selectores posibles
    btn_login = page.locator(
        "a:has-text('Iniciar sesión'), button:has-text('Iniciar sesión'), "
        "a:has-text('Ingresar'), button:has-text('Ingresar'), "
        "a:has-text('Login'), a[href*='login'], a[href*='clave']"
    ).first
    log(f"[debug] Haciendo click en botón login...", "info")

    # expect_navigation maneja el redirect aunque sea JS-driven
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=35000):
            btn_login.click(timeout=10000)
    except PWTimeout:
        # Puede que ya navegó antes de que capturáramos
        page.wait_for_load_state("domcontentloaded", timeout=10000)

    log(f"[debug] URL tras click: {page.url}", "info")

    # Esperar ClaveÚnica — el dominio real es claveunica.gob.cl
    if "claveunica" not in page.url.lower():
        page.wait_for_url("*claveunica*", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=20000)

    log(f"[debug] URL ClaveÚnica: {page.url}", "info")
    log("Ingresando ClaveÚnica...", "info")

    # Formulario ClaveÚnica (un solo paso): #uname (RUN) + #pword (clave) + #login-submit
    run_field = page.locator("#uname, input[name='run']").first
    run_field.wait_for(state="visible", timeout=15000)
    run_field.fill(run)

    pass_field = page.locator("#pword, input[type='password']").first
    pass_field.fill(clave)

    # El botón #login-submit arranca con atributo disabled=true; ClaveÚnica lo
    # habilita solo cuando los campos disparan keyup/blur. page.fill() no dispara
    # keyup, así que forzamos los eventos para que la validación JS active el botón.
    page.evaluate("""() => {
        for (const sel of ['#uname', '#pword']) {
            const el = document.querySelector(sel);
            if (!el) continue;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
            el.dispatchEvent(new Event('blur', {bubbles:true}));
        }
    }""")

    ingresar = page.locator("#login-submit, button:has-text('INGRESA')").first
    log("[debug] Esperando que INGRESA se habilite...", "info")
    try:
        ingresar.wait_for(state="visible", timeout=8000)
        # Esperar hasta que ya no esté deshabilitado
        page.wait_for_function(
            "() => { const b = document.querySelector('#login-submit'); return b && !b.disabled; }",
            timeout=8000
        )
    except PWTimeout:
        pass

    log("[debug] Clickeando INGRESA...", "info")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=35000):
        try:
            ingresar.click(timeout=8000)
        except PWTimeout:
            # Fallback: click forzado saltando la comprobación de habilitado
            ingresar.click(timeout=8000, force=True)

    log(f"[debug] URL post-login: {page.url}", "info")

    # Esperar redirección de vuelta a Mi DT
    if "midt.dirtrab" not in page.url.lower():
        page.wait_for_url("*midt.dirtrab*", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=20000)

    log("Login exitoso", "ok")


# ── Selección empresa ──────────────────────────────────────────────────────────

# JS: hace click en el elemento cuyo texto es EXACTAMENTE `texto` (o empieza por él),
# eligiendo el nodo más pequeño y subiendo a su ancestro clickeable. Devuelve el
# texto clickeado o null.
_JS_CLICK_EXACTO = """
(texto) => {
    const objetivo = texto.trim().toUpperCase();
    let best = null;
    for (const el of document.querySelectorAll('button, a, div, span, li, [role=button], .card, .mat-card')) {
        const t = (el.innerText || '').trim().toUpperCase();
        if (t === objetivo || t.startsWith(objetivo + ' ') || t === objetivo.replace(/\\s+/g,'')) {
            if (!best || (el.innerText || '').length < (best.innerText || '').length) best = el;
        }
    }
    if (!best) return null;
    let target = best;
    for (let i = 0; i < 5 && target; i++) {
        const cs = getComputedStyle(target);
        if (target.tagName === 'BUTTON' || target.tagName === 'A' ||
            target.getAttribute('role') === 'button' || target.onclick ||
            cs.cursor === 'pointer') {
            target.click();
            return best.innerText.trim();
        }
        target = target.parentElement;
    }
    best.click();
    return best.innerText.trim();
}
"""

# JS: hace click en el elemento más pequeño cuyo texto CONTIENE `sub` (subcadena).
_JS_CLICK_CONTIENE = """
(sub) => {
    const objetivo = sub.trim().toUpperCase();
    let best = null;
    for (const el of document.querySelectorAll('button, a, div, span, li, [role=button], .card, .mat-card')) {
        const t = (el.innerText || '').trim().toUpperCase();
        if (t.includes(objetivo)) {
            if (!best || (el.innerText || '').length < (best.innerText || '').length) best = el;
        }
    }
    if (!best) return null;
    let target = best;
    for (let i = 0; i < 5 && target; i++) {
        const cs = getComputedStyle(target);
        if (target.tagName === 'BUTTON' || target.tagName === 'A' ||
            target.getAttribute('role') === 'button' || target.onclick ||
            cs.cursor === 'pointer') {
            target.click();
            return best.innerText.trim();
        }
        target = target.parentElement;
    }
    best.click();
    return best.innerText.trim();
}
"""

# JS: hace click en el elemento que CONTIENE el RUT dado (empresa en la lista).
_JS_CLICK_RUT = """
(rut) => {
    const rutClean = rut.replace(/[\\.\\-]/g, '').toUpperCase();
    let best = null;
    for (const el of document.querySelectorAll('button, a, div, li, span, .card, .mat-card, [role=button]')) {
        const txt = (el.innerText || '').replace(/[\\.\\-]/g, '').toUpperCase();
        if (txt.includes(rutClean)) {
            if (!best || (el.innerText || '').length < (best.innerText || '').length) best = el;
        }
    }
    if (!best) return null;
    let target = best;
    for (let i = 0; i < 5 && target; i++) {
        const cs = getComputedStyle(target);
        if (target.tagName === 'BUTTON' || target.tagName === 'A' ||
            target.getAttribute('role') === 'button' || target.onclick ||
            cs.cursor === 'pointer') {
            target.click();
            return best.innerText.trim();
        }
        target = target.parentElement;
    }
    best.click();
    return best.innerText.trim();
}
"""


def _dump_pantalla(page, log, etiqueta):
    try:
        textos = page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('button, a, [role=button], li, .card, .mat-card')) {
                const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                if (t && t.length < 80) out.push(t);
            }
            return [...new Set(out)].slice(0, 40);
        }""")
        log(f"[debug] {etiqueta}: {' | '.join(textos)}", "info")
    except Exception:
        pass


def seleccionar_empresa(page, rut_empresa, log, snap=None):
    """Selecciona perfil EMPLEADOR → empresa por RUT."""
    time.sleep(2)  # dejar que /roles renderice
    if snap:
        snap("1a_roles_inicial")

    # ── Paso 1: click en el perfil EMPLEADOR ───────────────────────────────────
    log("Seleccionando perfil EMPLEADOR...", "info")
    emp = page.evaluate(_JS_CLICK_EXACTO, "EMPLEADOR")
    if not emp:
        _dump_pantalla(page, log, "Perfiles disponibles")
        raise Exception("No se encontró el perfil EMPLEADOR en la pantalla de roles")
    log(f"[debug] Click perfil: {emp}", "info")

    # Esperar a que aparezca la pantalla "Indica qué tipo de empleador"
    time.sleep(3)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeout:
        pass
    if snap:
        snap("1b_post_empleador")
    log(f"[debug] URL tras EMPLEADOR: {page.url}", "info")
    _dump_pantalla(page, log, "Opciones tras EMPLEADOR")

    # ── Paso 2: click en "Empleador Persona Jurídica" ──────────────────────────
    log("Seleccionando Empleador Persona Jurídica...", "info")
    pj = page.evaluate(_JS_CLICK_CONTIENE, "PERSONA JUR")
    if not pj:
        raise Exception("No se encontró 'Empleador Persona Jurídica' tras elegir EMPLEADOR")
    log(f"[debug] Click tipo empleador: {pj}", "info")

    time.sleep(3)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeout:
        pass
    if snap:
        snap("1c_post_persona_juridica")
    log(f"[debug] URL tras Persona Jurídica: {page.url}", "info")
    _dump_pantalla(page, log, "Empresas tras Persona Jurídica")

    # ── Paso 3: click en la empresa por RUT ────────────────────────────────────
    log(f"Seleccionando empresa {rut_empresa}...", "info")
    empresa = page.evaluate(_JS_CLICK_RUT, rut_empresa)
    if not empresa:
        raise Exception(f"No se encontró la empresa {rut_empresa} tras elegir EMPLEADOR")
    log(f"[debug] Click empresa: {empresa}", "info")

    time.sleep(3)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeout:
        pass
    if snap:
        snap("1d_post_empresa")
    log(f"[debug] URL tras seleccionar empresa: {page.url}", "info")
    log("Empresa seleccionada", "ok")


# ── Navegación al formulario ───────────────────────────────────────────────────

def _ir_a_formulario(page, log, snap=None):
    """Va a Registro Electrónico Laboral → Contrato Individual → Registrar."""
    page.goto(f"{_URL}/empleador/registro-electronico-laboral",
              wait_until="networkidle", timeout=45000)
    time.sleep(2)
    log(f"[debug] URL registro: {page.url}", "info")
    if snap:
        snap("2a_registro")

    # Volcado: mostrar todos los botones/enlaces disponibles en esta página
    try:
        botones = page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('button, a, [role=button]')) {
                const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                if (t && t.length < 60) out.push(t);
            }
            return [...new Set(out)].slice(0, 40);
        }""")
        log(f"[debug] Botones registro: {' | '.join(botones)}", "info")
    except Exception:
        pass

    # Buscar botón "Registrar" (o variantes) — click por JS si está oculto
    clicked = page.evaluate("""() => {
        const rx = /registrar|nuevo|crear|ingresar contrato|contrato individual/i;
        for (const el of document.querySelectorAll('button, a, [role=button]')) {
            const t = (el.innerText || '').trim();
            if (rx.test(t)) { el.click(); return t; }
        }
        return null;
    }""")

    if not clicked:
        raise Exception("No se encontró el botón 'Registrar' en la página de registro. "
                        "Revisa la línea [debug] Botones registro para ver qué hay.")

    log(f"[debug] Click en botón: {clicked}", "info")
    page.wait_for_load_state("networkidle", timeout=30000)

    # Esperar iframe
    page.wait_for_selector("iframe", timeout=20000)
    time.sleep(2)
    log("Formulario cargado", "info")


# ── Consulta de un RUT ─────────────────────────────────────────────────────────

def _consultar_rut(page, rut, log):
    """Rellena el RUT del trabajador y extrae los datos auto-rellenados."""
    frame = page.frame_locator("iframe")

    # Scroll al iframe para asegurar visibilidad
    page.locator("iframe").scroll_into_view_if_needed()
    time.sleep(0.5)

    # Campo RUT Persona Trabajadora
    rut_input = frame.get_by_label("RUT Persona Trabajadora", exact=False)
    rut_input.click(timeout=10000)
    rut_input.triple_click()
    rut_input.fill(rut)

    # Botón buscar (el botón azul junto al campo RUT)
    search_btn = frame.locator(
        "button[type='button'] svg, button[aria-label*='buscar'], "
        "button[aria-label*='search'], button.btn-search"
    ).locator("..").first
    try:
        search_btn.click(timeout=5000)
    except PWTimeout:
        # Fallback: presionar Enter en el campo
        rut_input.press("Enter")

    # Esperar que se rellenen los datos (Nombres o Fecha)
    time.sleep(3)

    def _val(label):
        try:
            el = frame.get_by_label(label, exact=False)
            v = el.input_value(timeout=4000).strip()
            if not v:
                # Puede ser un <select> con texto visible
                v = el.locator("option:checked").text_content(timeout=2000) or ""
            return v
        except Exception:
            return ""

    def _select_val(label):
        try:
            el = frame.get_by_label(label, exact=False)
            # Para selects, el valor visible es el option seleccionado
            v = el.evaluate("e => e.options[e.selectedIndex]?.text || e.value").strip()
            return v
        except Exception:
            return _val(label)

    nombres   = _val("Nombres")
    apellidos = _val("Apellidos")
    fecha_nac = _val("Fecha de nacimiento")
    correo    = _val("Correo electrónico")
    calle     = _val("Calle")
    numero    = _val("Número")
    comuna    = _select_val("Comuna")

    return {
        "RUT Trabajador": rut,
        "Nombres":        nombres,
        "Apellidos":      apellidos,
        "Fecha Nac.":     fecha_nac,
        "Correo":         correo,
        "Calle":          f"{calle} {numero}".strip(),
        "Comuna":         comuna,
        "Error":          "" if nombres else "sin datos",
    }


# ── Función principal ──────────────────────────────────────────────────────────

def consultar_ruts(run, clave, rut_empresa, lista_ruts, log, debug_dir=None):
    """Consulta todos los RUTs y devuelve lista de dicts."""
    resultados = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context(viewport={"width": 1400, "height": 900})
        page    = ctx.new_page()
        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(45000)

        def snap(nombre):
            if not debug_dir:
                return
            try:
                import os as _os
                ruta = _os.path.join(debug_dir, f"midt_{nombre}.png")
                page.screenshot(path=ruta, full_page=True)
                log(f"[shot] captura guardada: midt_{nombre}.png", "info")
            except Exception:
                pass

        try:
            hacer_login(page, run, clave, log)
            snap("1_roles")
            seleccionar_empresa(page, rut_empresa, log, snap)
            snap("2_post_empresa")
            _ir_a_formulario(page, log, snap)
            snap("3_formulario")

            for i, rut in enumerate(lista_ruts, 1):
                rut = rut.strip()
                if not rut:
                    continue
                log(f"[{i}/{len(lista_ruts)}] {rut}...", "info")
                try:
                    datos = _consultar_rut(page, rut, log)
                    resultados.append(datos)
                    ok = datos.get("Nombres") or datos.get("Calle")
                    log(f"[{i}] {'✓' if ok else '⚠'} {rut} → "
                        f"{datos.get('Calle','')} {datos.get('Comuna','')}", "ok" if ok else "warn")
                except Exception as e:
                    log(f"[{i}] Error {rut}: {e}", "warn")
                    resultados.append({"RUT Trabajador": rut, "Error": str(e)})
                    # Reintentar con nuevo formulario
                    try:
                        _ir_a_formulario(page, log)
                    except Exception:
                        pass

        except Exception as e:
            log(f"Error fatal: {e}", "err")
            try:
                snap("error")
            except Exception:
                pass
            raise
        finally:
            browser.close()

    return resultados


# ── Excel ──────────────────────────────────────────────────────────────────────

COLS = ["RUT Trabajador", "Nombres", "Apellidos", "Fecha Nac.", "Correo", "Calle", "Comuna", "Error"]
ANCHOS = [16, 22, 22, 14, 30, 30, 18, 20]


def generar_excel(resultados: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Consulta RUTs DT"

    fill_h = PatternFill("solid", start_color="1E3A5F")
    font_h = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    borde  = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )
    for col, nombre in enumerate(COLS, 1):
        c = ws.cell(row=1, column=col, value=nombre)
        c.font = font_h; c.fill = fill_h; c.border = borde
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    font_d   = Font(name="Arial", size=9)
    fill_par = PatternFill("solid", start_color="EFF6FF")

    for fi, fila in enumerate(resultados, 2):
        fill = fill_par if fi % 2 == 0 else PatternFill("solid", start_color="FFFFFF")
        for col, key in enumerate(COLS, 1):
            c = ws.cell(row=fi, column=col, value=fila.get(key, ""))
            c.font = font_d; c.border = borde; c.fill = fill
            c.alignment = Alignment(horizontal="left", vertical="center")

    for col, ancho in enumerate(ANCHOS, 1):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(col)].width = ancho
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
