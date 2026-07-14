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

def _ir_a_formulario(page, log, snap=None):
    """Va a Registro Electrónico Laboral → 'Registrar'. Devuelve la página del
    formulario (puede ser una pestaña nueva)."""
    page.goto(f"{_URL}/empleador/registro-electronico-laboral",
              wait_until="networkidle", timeout=45000)
    time.sleep(2)
    log(f"[debug] URL registro: {page.url}", "info")
    if snap:
        snap("2a_registro")

    ctx = page.context
    pags_antes = len(ctx.pages)
    page_form = page

    def _en_tarjetas():
        """True si seguimos en la pantalla de tarjetas (no entró al formulario)."""
        try:
            return page_form.get_by_text("Registro de Contrato de Trabajo Individual").first.is_visible(timeout=2000)
        except Exception:
            return False

    # Estrategias de click sobre "Registrar", verificando que SALGA de las tarjetas.
    # El botón real es: <button class="ui blue basic button btn-register">Registrar</button>
    estrategias = [
        ("btn-register", lambda: page.locator("button.btn-register").first),
        ("role=button",  lambda: page.get_by_role("button", name="Registrar").first),
        ("text-exact",   lambda: page.get_by_text("Registrar", exact=True).first),
        ("css-button",   lambda: page.locator("button:has-text('Registrar')").first),
    ]

    clicado = False
    for nombre, getter in estrategias:
        try:
            el = getter()
            el.wait_for(state="visible", timeout=5000)
            el.scroll_into_view_if_needed(timeout=3000)
            el.click(timeout=8000)
            log(f"[debug] Click 'Registrar' con estrategia: {nombre}", "info")
            # Verificar que cambió (URL o salió de tarjetas) — hasta 8s
            cambio = False
            for _ in range(8):
                if "registroContratoTrabajo" in page_form.url or not _en_tarjetas():
                    cambio = True
                    break
                time.sleep(1)
            if cambio:
                clicado = True
                log(f"[debug] '{nombre}' funcionó → {page_form.url}", "info")
                break
            else:
                log(f"[debug] '{nombre}' no cambió la pantalla, probando otra...", "warn")
        except Exception as e:
            log(f"[debug] estrategia {nombre} falló: {str(e)[:60]}", "warn")
            continue

    # Último recurso: click por JS sobre el botón .btn-register (o texto 'Registrar')
    if not clicado:
        log("[debug] Probando click por JS sobre '.btn-register'...", "warn")
        try:
            page.evaluate("""() => {
                const b = document.querySelector('button.btn-register');
                if (b) { b.click(); return true; }
                for (const el of document.querySelectorAll('button, a')) {
                    if ((el.innerText||'').trim() === 'Registrar') { el.click(); return true; }
                }
                return false;
            }""")
            time.sleep(3)
        except Exception:
            pass

    log("[debug] Click en botón: Registrar", "info")

    # Esperar a que la URL cambie al formulario del contrato
    try:
        page_form.wait_for_url("**registroContratoTrabajo**", timeout=20000)
    except PWTimeout:
        pass
    try:
        page_form.wait_for_load_state("networkidle", timeout=30000)
    except PWTimeout:
        pass

    def _contar_campos():
        t = 0
        for fr in page_form.frames:
            try:
                t += fr.evaluate("() => document.querySelectorAll('input,select,textarea').length")
            except Exception:
                pass
        return t

    def _esperar_campos(segundos):
        for _ in range(segundos):
            if _contar_campos() > 0:
                return True
            time.sleep(1)
        return False

    # Esperar activamente a que aparezcan campos (hasta 25s)
    ok = _esperar_campos(25)

    # Fallback 1: si no renderizó, navegar directo a la URL del formulario
    if not ok:
        url_form = page_form.url
        if "registroContratoTrabajo" not in url_form:
            url_form = f"{_URL}/empleador/registro-electronico-laboral/registroContratoTrabajo"
        log("[debug] Formulario sin campos; navegando directo a la URL...", "warn")
        try:
            page_form.goto(url_form, wait_until="networkidle", timeout=40000)
        except Exception:
            pass
        ok = _esperar_campos(20)

    # Fallback 2: recargar la página del formulario
    if not ok:
        log("[debug] Aún sin campos; recargando el formulario...", "warn")
        try:
            page_form.reload(wait_until="networkidle", timeout=40000)
        except Exception:
            pass
        ok = _esperar_campos(20)

    total = _contar_campos()
    time.sleep(2)
    log(f"[debug] Pestañas abiertas: {len(ctx.pages)} (antes {pags_antes})", "info")
    log(f"[debug] URL formulario: {page_form.url}", "info")
    # Panorama de frames SIEMPRE (para diagnóstico)
    for fr in page_form.frames:
        try:
            n = fr.evaluate("() => document.querySelectorAll('input,select,textarea').length")
        except Exception:
            n = -1
        marca = fr.url[:65] if fr.url else "(sin url)"
        log(f"[debug] frame {marca} → {n} campos", "info")
    log(f"Formulario cargado ({total} campos detectados)", "info")
    return page_form


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

def _consultar_rut(page, rut, log):
    """Rellena el RUT del trabajador y extrae los datos auto-rellenados."""
    # Detectar el frame que realmente contiene el formulario (main o iframe)
    frame = _frame_form(page)

    # Campo RUT Persona Trabajadora — probar varias estrategias de localización
    rut_input = None
    for intento in [
        lambda: frame.get_by_label("RUT Persona Trabajadora", exact=False),
        lambda: frame.get_by_placeholder("RUT", exact=False),
        lambda: frame.locator("input[formcontrolname*='rut' i]"),
        lambda: frame.locator("input[name*='rut' i]"),
        lambda: frame.locator("input[id*='rut' i]"),
        lambda: frame.locator("input[type='text']").first,
    ]:
        try:
            cand = intento().first
            cand.wait_for(state="visible", timeout=4000)
            rut_input = cand
            break
        except Exception:
            continue

    if rut_input is None:
        raise Exception("No se encontró el campo 'RUT Persona Trabajadora' en el formulario")

    rut_input.scroll_into_view_if_needed(timeout=5000)
    rut_input.click(timeout=10000)
    rut_input.fill("")
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
