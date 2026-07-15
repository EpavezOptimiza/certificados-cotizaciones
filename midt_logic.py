"""Bot para consulta masiva de RUTs en Mi DT (midt.dirtrab.cl)."""

import io, time, re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

_URL = "https://midt.dirtrab.cl"


# ── Login ──────────────────────────────────────────────────────────────────────

def _tipo_campo(page, locator, valor):
    """Escribe en un campo simulando teclas reales para activar la validación JS."""
    locator.click()
    locator.press("Control+a")
    locator.press("Backspace")
    try:
        locator.press_sequentially(valor, delay=60)
    except AttributeError:
        # Playwright < 1.38 no tiene press_sequentially
        locator.type(valor, delay=60)
    locator.dispatch_event("input")
    locator.dispatch_event("change")
    locator.press("Tab")


def hacer_login(page, run, clave, log):
    log("Abriendo Mi DT...", "info")
    page.goto(_URL, wait_until="networkidle", timeout=45000)

    # Click en botón de login principal
    btn_login = page.locator(
        "a:has-text('Iniciar sesión'), button:has-text('Iniciar sesión'), "
        "a:has-text('Ingresar'), button:has-text('Ingresar'), "
        "a[href*='login'], a[href*='clave']"
    ).first
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=35000):
            btn_login.click(timeout=10000)
    except PWTimeout:
        page.wait_for_load_state("domcontentloaded", timeout=10000)

    # Esperar ClaveÚnica
    if "claveunica" not in page.url.lower():
        page.wait_for_url("*claveunica*", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=20000)

    log("Ingresando ClaveÚnica...", "info")

    # ClaveÚnica puede ser de un paso (RUN+clave juntos) o dos pasos (RUN → Continuar → clave).
    # Detectamos cuál es verificando si hay campo de contraseña visible al inicio.
    run_field = page.locator("#uname, input[name='run'], input[type='text']").first
    run_field.wait_for(state="visible", timeout=15000)

    pw_visible = False
    try:
        pw_loc = page.locator("#pword, input[type='password']").first
        pw_visible = pw_loc.is_visible()
    except Exception:
        pass

    # Escribir RUN con teclas reales (dispara keyup/input requeridos por ClaveÚnica)
    _tipo_campo(page, run_field, run)
    time.sleep(0.5)

    if not pw_visible:
        # Flujo de dos pasos: primero RUN → click "Continuar"
        try:
            continuar = page.locator("button:has-text('Continuar'), button:has-text('CONTINUAR')").first
            continuar.wait_for(state="visible", timeout=8000)
            continuar.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            time.sleep(1)
        except PWTimeout:
            pass

    # Escribir contraseña
    pw_field = page.locator("#pword, input[type='password']").first
    pw_field.wait_for(state="visible", timeout=15000)
    _tipo_campo(page, pw_field, clave)
    time.sleep(0.8)

    # Esperar que el botón INGRESA se habilite (hasta 10s)
    ingresar = page.locator("#login-submit, button:has-text('INGRESA'), button:has-text('Ingresar')").first
    try:
        page.wait_for_function(
            "() => { const b = document.querySelector('#login-submit, button[type=submit]'); "
            "return b && !b.disabled; }",
            timeout=10000
        )
    except PWTimeout:
        pass

    log("Enviando credenciales...", "info")
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=40000):
            ingresar.click(timeout=8000)
    except PWTimeout:
        ingresar.click(timeout=8000, force=True)
        page.wait_for_load_state("domcontentloaded", timeout=20000)

    # Esperar redirección de vuelta a Mi DT
    if "midt.dirtrab" not in page.url.lower():
        page.wait_for_url("*midt.dirtrab*", timeout=35000)
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
    """Vuelca al log los elementos clickeables con tag+atributos+texto,
    para poder deducir selectores exactos sin ver el HTML completo."""
    try:
        items = page.evaluate("""() => {
            const out = [];
            const els = document.querySelectorAll(
                'button, a, [role=button], [routerlink], .card, .mat-card, ' +
                'input, [class*=btn], [class*=card], [class*=item]'
            );
            for (const el of els) {
                const t = (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 45);
                const cs = getComputedStyle(el);
                const clickable = el.tagName === 'BUTTON' || el.tagName === 'A' ||
                    el.onclick || el.getAttribute('role') === 'button' ||
                    el.hasAttribute('routerlink') || cs.cursor === 'pointer';
                if (!t && !clickable) continue;
                let desc = el.tagName.toLowerCase();
                if (el.id) desc += '#' + el.id;
                if (el.className && typeof el.className === 'string')
                    desc += '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.');
                const rl = el.getAttribute('routerlink');
                if (rl) desc += `[routerlink=${rl}]`;
                if (clickable) desc += '*';
                out.push(`${desc} "${t}"`);
            }
            return [...new Set(out)].slice(0, 30);
        }""")
        log(f"[debug] {etiqueta}:", "info")
        for it in items:
            log(f"    {it}", "info")
    except Exception:
        pass


def _click_texto(page, candidatos, log, timeout=15000):
    """Intenta clickear el primer elemento visible que coincida, probando
    role=button → role=link → texto. `candidatos` es lista de textos a probar.
    Usa clicks nativos de Playwright (manejan Angular y esperan actionability).
    Devuelve el texto que funcionó, o None."""
    for texto in candidatos:
        for loc in [
            page.get_by_role("button", name=texto),
            page.get_by_role("link", name=texto),
            page.get_by_text(texto),
        ]:
            try:
                el = loc.first
                el.wait_for(state="visible", timeout=3500)
                el.scroll_into_view_if_needed(timeout=3000)
                el.click(timeout=timeout)
                return texto
            except Exception:
                continue
    return None


def seleccionar_empresa(page, rut_empresa, log, snap=None):
    """Selecciona perfil EMPLEADOR → Empleador Persona Jurídica → empresa por RUT."""
    time.sleep(2)  # dejar que /roles renderice
    if snap:
        snap("1a_roles_inicial")

    # ── Paso 1: click en el perfil EMPLEADOR ───────────────────────────────────
    log("Seleccionando perfil EMPLEADOR...", "info")
    emp = _click_texto(page, ["EMPLEADOR", "Empleador"], log)
    if not emp:
        _dump_pantalla(page, log, "Perfiles disponibles")
        raise Exception("No se encontró el perfil EMPLEADOR en la pantalla de roles")
    log(f"[debug] Click perfil: {emp}", "info")

    # Esperar a que aparezca la pantalla "Indica qué tipo de empleador"
    try:
        page.get_by_text("Persona Jurídica").first.wait_for(state="visible", timeout=20000)
    except PWTimeout:
        pass
    time.sleep(1)
    if snap:
        snap("1b_post_empleador")
    log(f"[debug] URL tras EMPLEADOR: {page.url}", "info")
    _dump_pantalla(page, log, "Opciones tras EMPLEADOR")

    # ── Paso 2: click en "Empleador Persona Jurídica" ──────────────────────────
    log("Seleccionando Empleador Persona Jurídica...", "info")
    pj = _click_texto(page, ["Empleador Persona Jurídica", "Persona Jurídica"], log)
    if not pj:
        raise Exception("No se encontró 'Empleador Persona Jurídica' tras elegir EMPLEADOR")
    log(f"[debug] Click tipo empleador: {pj}", "info")

    # Esperar a que aparezca la lista de empresas (que contenga el RUT)
    rut_sin = rut_empresa.replace(".", "").replace("-", "")
    rut_con = rut_empresa
    try:
        page.get_by_text(rut_sin[:6]).first.wait_for(state="visible", timeout=20000)
    except PWTimeout:
        pass
    time.sleep(1.5)
    if snap:
        snap("1c_post_persona_juridica")
    log(f"[debug] URL tras Persona Jurídica: {page.url}", "info")
    _dump_pantalla(page, log, "Empresas tras Persona Jurídica")

    # ── Paso 3: click en la empresa por RUT ────────────────────────────────────
    log(f"Seleccionando empresa {rut_empresa}...", "info")
    empresa = _click_texto(page, [rut_sin, rut_con, rut_empresa.replace("-", "")], log)
    if not empresa:
        raise Exception(f"No se encontró la empresa {rut_empresa} tras elegir Persona Jurídica")
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

_IFRAME_SRC = "front001-registro-electronico-lab.api.dirtrab.cl"

def _get_iframe_frame(page, espera=20):
    """Devuelve el Frame Python del iframe del formulario DT."""
    for _ in range(espera):
        for fr in page.frames:
            if _IFRAME_SRC in (fr.url or ""):
                return fr
        time.sleep(1)
    return None


def _ir_a_formulario(page, log, snap=None):
    """Navega al formulario de registro de contrato dentro del iframe DT."""
    url_destino = f"{_URL}/empleador/registro-electronico-laboral/registroContratoTrabajo"
    page.goto(url_destino, wait_until="networkidle", timeout=45000)
    time.sleep(2)

    log("Esperando iframe del formulario...", "info")

    # El formulario está en un iframe cross-origin. Usar frame_locator para interactuar.
    iframe_loc = page.frame_locator(f"iframe[src*='{_IFRAME_SRC}'], iframe").first

    # Esperar y clickear "Registrar" dentro del iframe
    log("Buscando botón Registrar...", "info")
    try:
        btn = iframe_loc.get_by_role("button", name="Registrar")
        btn.wait_for(state="visible", timeout=20000)
        btn.click()
        log("Click en Registrar", "info")
    except Exception as e:
        # Fallback: buscar por texto
        try:
            btn = iframe_loc.get_by_text("Registrar", exact=True)
            btn.wait_for(state="visible", timeout=10000)
            btn.click()
            log("Click en Registrar (fallback texto)", "info")
        except Exception as e2:
            raise Exception(f"No se pudo clickear 'Registrar' en el iframe: {e} / {e2}")

    # Esperar que los campos del formulario estén HABILITADOS (no solo en DOM)
    # Angular puede tener 32 campos en DOM pero todos disabled durante inicialización.
    log("Esperando formulario de RUT...", "info")
    fr = _get_iframe_frame(page, espera=5)
    for _ in range(30):
        if fr:
            try:
                enabled_n = fr.evaluate("""() => {
                    let n = 0;
                    for (const el of document.querySelectorAll('input, select, textarea')) {
                        if (!el.disabled && !el.readOnly) n++;
                    }
                    return n;
                }""")
                if enabled_n > 5:
                    log(f"Formulario listo ({enabled_n} campos habilitados)", "ok")
                    # Clickear radio "Cédula de identidad" para activar sección trabajador
                    try:
                        iframe_loc = page.frame_locator(f"iframe[src*='{_IFRAME_SRC}'], iframe").first
                        radios = iframe_loc.locator("input[type='radio']")
                        if radios.count() > 0:
                            radios.first.click(force=True)
                            time.sleep(0.5)
                    except Exception:
                        pass
                    return page
            except Exception:
                pass
        time.sleep(1)
        fr = _get_iframe_frame(page, espera=1)

    raise Exception("El formulario del contrato no cargó campos habilitados tras 30 segundos")


def _frame_form(page, espera=20, log=None):
    """Devuelve el Frame (main o iframe) que contiene el formulario, detectado
    por ser el que tiene más inputs. Espera hasta `espera` seg a que carguen."""
    for intento in range(espera):
        best, best_n = None, 0
        for fr in page.frames:
            try:
                n = fr.evaluate(
                    "() => document.querySelectorAll('input, select, textarea').length"
                )
            except Exception:
                n = 0
            if n and n > best_n:
                best_n, best = n, fr
        if best and best_n > 0:
            if log and intento == 0:
                log(f"[debug] Frame del formulario: {best.url[:70]} ({best_n} campos)", "info")
            return best
        time.sleep(1)
    if log:
        # Volcar panorama de frames para diagnóstico
        for fr in page.frames:
            try:
                n = fr.evaluate("() => document.querySelectorAll('input,select,textarea').length")
            except Exception:
                n = -1
            log(f"[debug] frame {fr.url[:60]} → {n} campos", "info")
    return page.main_frame


def _dump_iframe(page, log):
    """Vuelca los campos del formulario (main o iframe)."""
    try:
        fr = _frame_form(page, log=log)
        campos = fr.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('input, select, textarea')) {
                let label = '';
                if (el.id) {
                    const l = document.querySelector(`label[for="${el.id}"]`);
                    if (l) label = l.innerText.trim();
                }
                if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
                if (!label) {
                    const p = el.closest('mat-form-field, .form-group, div');
                    if (p) { const l = p.querySelector('label, mat-label'); if (l) label = l.innerText.trim(); }
                }
                const desc = el.tagName.toLowerCase() +
                    (el.type ? `[type=${el.type}]` : '') +
                    (el.id ? `#${el.id}` : '') +
                    (el.name ? `[name=${el.name}]` : '') +
                    (el.getAttribute('formcontrolname') ? `[fcn=${el.getAttribute('formcontrolname')}]` : '') +
                    (el.placeholder ? `[ph="${el.placeholder}"]` : '');
                out.push(`${desc} label="${label}"`);
            }
            return out.slice(0, 40);
        }""")
        log("[debug] Campos del iframe:", "info")
        for c in campos:
            log(f"    {c}", "info")
    except Exception as e:
        log(f"[debug] No se pudo volcar iframe: {e}", "warn")


# ── Consulta de un RUT ─────────────────────────────────────────────────────────

_JS_EXTRAER = """() => {
    const out = {};
    for (const el of document.querySelectorAll('input, select, textarea')) {
        const id = el.id || '';
        let label = (el.getAttribute('aria-label') || '').trim();
        if (!label && id) {
            const l = document.querySelector('label[for="' + id + '"]');
            if (l) label = l.textContent.trim();
        }
        if (!label) {
            const ff = el.closest('mat-form-field, .form-group, .field');
            if (ff) {
                const ml = ff.querySelector('mat-label, label, .label');
                if (ml) label = ml.textContent.trim();
            }
        }
        if (!label) label = (el.getAttribute('formcontrolname') || el.placeholder || '').trim();
        const val = el.tagName === 'SELECT'
            ? (el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : '')
            : (el.value || '').trim();
        // Guardar por ID (clave exacta) y por label (fallback)
        if (id) out[id] = val;
        if (label) out[label] = val;
    }
    return out;
}"""


def _extraer_datos_frame(frame):
    """Extrae todos los valores del formulario con sus etiquetas."""
    try:
        return frame.evaluate(_JS_EXTRAER) or {}
    except Exception:
        return {}


def _esperar_datos(frame, max_seg=8):
    """Espera hasta que campos del TRABAJADOR tengan valor (por ID exacto)."""
    # IDs conocidos del dump del formulario DT
    _IDS_TRAB = ("nombresTrabajador", "apellidosTrabajador", "emailTrabajador",
                 "calleTrabajador", "fechaNacimientoTrabajador")
    for _ in range(int(max_seg / 0.4)):
        datos = _extraer_datos_frame(frame)
        if any(datos.get(k) for k in _IDS_TRAB):
            return datos
        time.sleep(0.4)
    return _extraer_datos_frame(frame)


def _buscar_campo_rut(frame):
    """Localiza #rutTrabajador (ya habilitado porque _ir_a_formulario esperó campos enabled)."""
    # ID exacto conocido del dump del formulario DT
    for sel in ["#rutTrabajador", "input[id*='rutTrab' i]"]:
        try:
            loc = frame.locator(sel).first
            loc.wait_for(state="enabled", timeout=8000)
            return loc
        except Exception:
            continue

    # Fallback JS: primer input de texto habilitado con 'rut' en el id (excluyendo rutEmpleador)
    try:
        field_id = frame.evaluate("""() => {
            for (const el of document.querySelectorAll('input[type="text"]')) {
                if (el.disabled || el.readOnly) continue;
                const id = (el.id || '').toLowerCase();
                if (id === 'rutempleador' || id === 'rutrepresentanteempleador') continue;
                if (id.includes('rut')) return el.id;
            }
            return null;
        }""")
        if field_id:
            return frame.locator(f"#{field_id}").first
    except Exception:
        pass

    return None


def _consultar_rut(page, rut, log):
    """Rellena el RUT del trabajador y extrae los datos auto-rellenados."""
    frame = _get_iframe_frame(page, espera=10)
    if frame is None:
        frame = _frame_form(page)

    rut_input = _buscar_campo_rut(frame)
    if rut_input is None:
        raise Exception("No se encontró campo RUT en el formulario")

    rut_input.scroll_into_view_if_needed(timeout=5000)
    _tipo_campo(None, rut_input, rut)

    # Botón lupa / buscar
    search_btn = None
    for sel in ["button[aria-label*='buscar' i]", "button[aria-label*='search' i]",
                "button.btn-search", "button[type='button']:has(mat-icon)",
                "button[type='submit']"]:
        try:
            cand = frame.locator(sel).first
            cand.wait_for(state="visible", timeout=2000)
            search_btn = cand
            break
        except Exception:
            continue

    if search_btn:
        try:
            search_btn.click(timeout=4000)
        except Exception:
            rut_input.press("Enter")
    else:
        rut_input.press("Enter")

    # Espera inteligente: hasta 8s, sale en cuanto hay datos
    datos = _esperar_datos(frame, max_seg=8)

    # Mapeo flexible: busca la clave que contenga cada término
    def _buscar(terminos):
        for t in terminos:
            t_l = t.lower()
            for k, v in datos.items():
                if t_l in k.lower() and v:
                    return v
        return ""

    # Extracción por ID exacto del formulario DT (conocidos del debug dump)
    nombres   = datos.get("nombresTrabajador", "") or _buscar(["nombres"])
    apellidos = datos.get("apellidosTrabajador", "") or _buscar(["apellido"])
    fecha_nac = datos.get("fechaNacimientoTrabajador", "") or _buscar(["nacimiento"])
    correo    = datos.get("emailTrabajador", "") or _buscar(["correo electrónico"])
    calle     = datos.get("calleTrabajador", "") or _buscar(["calle trabajador"])
    numero    = datos.get("numeroTrabajador", "") or _buscar(["número trabajador"])
    comuna    = datos.get("comunaTrabajador", "") or _buscar(["comunaTrab"])

    # Log diagnóstico si no hay datos del trabajador
    if not nombres and not correo:
        trab_keys = [k for k in datos if "trabaj" in k.lower() or "trab" in k.lower()]
        log(f"  sin datos trabajador. IDs trab: {trab_keys}", "warn")

    return {
        "RUT Trabajador": rut,
        "Nombres":        nombres,
        "Apellidos":      apellidos,
        "Fecha Nac.":     fecha_nac,
        "Correo":         correo,
        "Calle":          f"{calle} {numero}".strip(),
        "Comuna":         comuna,
        "Error":          "" if (nombres or correo or calle) else "sin datos",
    }


# ── Función principal ──────────────────────────────────────────────────────────

def consultar_ruts(run, clave, rut_empresa, lista_ruts, log, debug_dir=None):
    """Consulta todos los RUTs y devuelve lista de dicts."""
    resultados = []

    # Pantalla virtual (xvfb): permite correr el navegador CON pantalla en el
    # servidor. El portal DT no renderiza el formulario en modo headless, así
    # que arrancamos un display virtual y lanzamos Chromium headed. Si no está
    # disponible (p. ej. en local Windows), caemos a headless.
    _display = None
    _headless = True
    try:
        from pyvirtualdisplay import Display
        _display = Display(visible=0, size=(1440, 1000))
        _display.start()
        _headless = False
        log("[debug] Pantalla virtual (xvfb) iniciada — navegador con pantalla", "info")
    except Exception as e:
        log(f"[debug] Sin pantalla virtual ({str(e)[:50]}); usando headless", "warn")

    try:
      with sync_playwright() as pw:
        # Medidas anti-detección: el portal DT no renderiza el formulario si
        # detecta un navegador automatizado (headless / navigator.webdriver).
        browser = pw.chromium.launch(
            headless=_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
            locale="es-CL",
            timezone_id="America/Santiago",
        )
        # Ocultar señales de automatización antes de cargar cualquier página
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['es-CL','es']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = window.chrome || {runtime: {}};
        """)
        page    = ctx.new_page()
        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(45000)

        # La página "activa" puede cambiar si el formulario abre pestaña nueva
        estado = {"page": page}

        def snap(nombre):
            if not debug_dir:
                return
            import os as _os
            pg = estado["page"]
            try:
                ruta = _os.path.join(debug_dir, f"midt_{nombre}.png")
                pg.screenshot(path=ruta, full_page=True)
                log(f"[shot] captura guardada: midt_{nombre}.png", "info")
            except Exception:
                pass
            # Guardar también el HTML de la pantalla (para inspeccionar el DOM real)
            try:
                ruta_html = _os.path.join(debug_dir, f"midt_{nombre}.html")
                with open(ruta_html, "w", encoding="utf-8") as fh:
                    fh.write(pg.content())
            except Exception:
                pass

        try:
            hacer_login(page, run, clave, log)
            snap("1_roles")
            seleccionar_empresa(page, rut_empresa, log, snap)
            snap("2_post_empresa")
            page_form = _ir_a_formulario(page, log, snap)
            estado["page"] = page_form
            snap("3_formulario")
            _dump_iframe(page_form, log)   # volcar campos del formulario una vez

            for i, rut in enumerate(lista_ruts, 1):
                rut = rut.strip()
                if not rut:
                    continue
                log(f"[{i}/{len(lista_ruts)}] {rut}...", "info")
                try:
                    datos = _consultar_rut(page_form, rut, log)
                    resultados.append(datos)
                    ok = datos.get("Nombres") or datos.get("Calle")
                    log(f"[{i}] {'✓' if ok else '⚠'} {rut} → "
                        f"{datos.get('Calle','')} {datos.get('Comuna','')}", "ok" if ok else "warn")
                except Exception as e:
                    log(f"[{i}] Error {rut}: {e}", "warn")
                    resultados.append({"RUT Trabajador": rut, "Error": str(e)})
                    # Reintentar reabriendo el formulario
                    try:
                        page_form = _ir_a_formulario(page, log)
                        estado["page"] = page_form
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
    finally:
        if _display:
            try:
                _display.stop()
            except Exception:
                pass

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
