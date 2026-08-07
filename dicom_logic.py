"""Bot para el portal DICOM / Equifax (sec.equifax.cl/compraonline).

Por ahora SOLO inicia sesión y comprueba que entró. No compra ni descarga
nada: el portal cobra por cada documento y esa parte se agrega aparte,
cuando esté confirmado cómo se paga.
"""

import os
import time

from playwright.sync_api import sync_playwright

URL_LOGIN = "https://business.equifax.ca/auth/login"

# Paso 1 — identificadores verificados en el portal real (Okta / Client Central)
_SEL_USUARIO = "#idp-discovery-username, input[name='username']"
_SEL_NEXT    = "#idp-discovery-submit, input[type='submit'][value='Next']"
# Paso 2 — la contraseña aparece después de "Next"
_SEL_CLAVE   = "input[type='password']"


class LoginFallido(Exception):
    """No se pudo iniciar sesión en DICOM/Equifax."""


def _texto(page, limite=400):
    try:
        return (page.evaluate(
            "() => (document.body ? document.body.innerText : '')"
            ".replace(/\\s+/g, ' ').trim()") or "")[:limite]
    except Exception:
        return ""


def _sigue_en_login(page):
    """True si el formulario de login sigue en pantalla (no entró)."""
    try:
        return page.locator(_SEL_CLAVE).count() > 0
    except Exception:
        return False


def _dump_campos(page, log):
    """Detalla todos los campos visibles: etiqueta, placeholder, si es
    obligatorio y si tiene valor. Sirve para identificar campos ocultos
    a la vista (captcha, código, etc.) que estén frenando el envío."""
    try:
        campos = page.evaluate("""() => {
            const out = [];
            for (const e of document.querySelectorAll('input, select, textarea')) {
                if (e.offsetParent === null || e.type === 'hidden') continue;
                let etiqueta = e.getAttribute('aria-label') || '';
                if (!etiqueta && e.id) {
                    const l = document.querySelector('label[for="' + e.id + '"]');
                    if (l) etiqueta = l.innerText.trim();
                }
                if (!etiqueta) {
                    const cont = e.closest('div, li, td');
                    if (cont) {
                        const l = cont.querySelector('label');
                        if (l) etiqueta = l.innerText.trim();
                    }
                }
                out.push({
                    tipo: e.type || e.tagName.toLowerCase(),
                    name: e.name || '', id: e.id || '',
                    etiqueta: (etiqueta || '').replace(/\\s+/g, ' ').slice(0, 40),
                    placeholder: e.placeholder || '',
                    obligatorio: !!e.required || e.getAttribute('aria-required') === 'true',
                    conValor: !!(e.value || '').trim()
                });
            }
            return out.slice(0, 10);
        }""")
        log("  [debug] campos de la pantalla:", "warn")
        for c in campos:
            log(f"    {c['tipo']} name={c['name']!r} id={c['id']!r} "
                f"etiqueta={c['etiqueta']!r} ph={c['placeholder']!r} "
                f"obligatorio={c['obligatorio']} conValor={c['conValor']}", "warn")
    except Exception:
        pass


def _hay(page, selector):
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def _tipear(page, selector, texto, log=None, timeout_click=8000):
    """Escribe TECLA POR TECLA (no page.fill).

    El portal está hecho con un framework JS: si el valor se asigna por
    programa, la página lo muestra pero internamente lo considera vacío y el
    botón de acceso no hace nada. Con pulsaciones reales sí lo registra.

    El click lleva timeout corto: si el campo está bloqueado o prellenado
    (Okta lo hace con el usuario), no se pierden 45 s esperándolo.
    """
    loc = page.locator(selector).first
    try:
        loc.click(timeout=timeout_click)
    except Exception:
        # Campo no clickeable (readonly/disabled/cubierto): enfocar por JS
        try:
            loc.evaluate("e => e.focus()")
        except Exception:
            if log:
                log(f"  no se pudo enfocar {selector[:30]}", "warn")
            return False
    try:
        loc.press("Control+a")
        loc.press("Backspace")
    except Exception:
        pass
    try:
        loc.press_sequentially(texto, delay=70)
    except AttributeError:          # Playwright antiguo
        loc.type(texto, delay=70)
    except Exception:
        if log:
            log(f"  no se pudo escribir en {selector[:30]}", "warn")
        return False
    try:
        loc.dispatch_event("input")
        loc.dispatch_event("change")
    except Exception:
        pass
    return True


VERSION = "v6 (corrige el falso error por la palabra 'credenciales')"


def _pide_verificacion(page):
    """True si el portal muestra la pantalla 'Verify your Account'."""
    try:
        return bool(page.evaluate("""() => {
            const t = (document.body ? document.body.innerText : '').toLowerCase();
            return t.includes('verify your account') || t.includes('send me the code') ||
                   t.includes('verification code') || t.includes('security code') ||
                   t.includes('codigo de verificacion') ||
                   t.includes('código de verificación') ||
                   t.includes('verifique su cuenta') || t.includes('enviarme el codigo') ||
                   t.includes('enviarme el código');
        }"""))
    except Exception:
        return False


def _resolver_verificacion(page, log, obtener_codigo):
    """Pide el envío del código, espera a que la persona lo escriba en la
    plataforma y lo ingresa en el portal (flujo semiautomático)."""
    log("El portal pide un código de verificación", "warn")

    # 1. Botón "SEND ME THE CODE" (si aún no se ha enviado)
    try:
        enviado = page.evaluate("""() => {
            for (const b of document.querySelectorAll('button, input[type=submit], a')) {
                const t = ((b.innerText || b.value || '')).trim().toLowerCase();
                if (t.includes('send me the code') || t.includes('enviarme el') ||
                    t.includes('send code') || t.includes('enviar código') ||
                    t.includes('enviar codigo')) { b.click(); return true; }
            }
            return false;
        }""")
        if enviado:
            log("Código solicitado — revisa tu correo", "info")
            time.sleep(3)
    except Exception:
        pass

    # 2. Esperar a que la persona escriba el código en la plataforma
    codigo = obtener_codigo()
    if not codigo:
        raise LoginFallido(
            "No se recibió el código de verificación a tiempo. Vuelve a intentarlo "
            "y escribe el código apenas llegue a tu correo.")
    log("Código recibido — ingresándolo...", "info")

    # 3. Escribirlo en el campo correspondiente
    campo = None
    for sel in ("input[name='passCode']", "input[autocomplete='one-time-code']",
                "input[type='tel']", "input[name*='code' i]", "input[id*='code' i]",
                "input[type='text']"):
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=4000)
            campo = sel
            break
        except Exception:
            continue
    if not campo:
        raise LoginFallido("No se encontró dónde escribir el código de verificación")

    _tipear(page, campo, str(codigo).strip(), log)
    time.sleep(0.5)

    # 4. Enviar
    for descripcion, loc in (
        ("botón Verify", page.get_by_role("button", name="Verify")),
        ("botón Verificar", page.get_by_role("button", name="Verificar")),
        ("submit", page.locator("input[type='submit'], button[type='submit']")),
    ):
        try:
            el = loc.first
            el.wait_for(state="visible", timeout=3000)
            el.click(timeout=4000)
            log(f"Código enviado ({descripcion})", "info")
            break
        except Exception:
            continue
    else:
        try:
            page.locator(campo).first.press("Enter")
        except Exception:
            pass

    # 5. Esperar a que pase la verificación
    fin = time.time() + 60
    while time.time() < fin:
        if not _pide_verificacion(page):
            return True
        time.sleep(1)
    raise LoginFallido("El portal no aceptó el código de verificación")


def hacer_login(page, correo, clave, log, obtener_codigo=None):
    """Login en dos pasos: usuario → Next → contraseña → (código) → entrar."""
    log(f"DICOM {VERSION}", "info")
    log("Abriendo el portal de Equifax...", "info")
    page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=45000)

    # ── Paso 1: usuario (el correo) y botón Next ────────────────────────────
    try:
        page.wait_for_selector(_SEL_USUARIO, state="visible", timeout=25000)
    except Exception:
        raise LoginFallido(f"No apareció el campo Username. "
                           f"La página muestra: {_texto(page, 200)}")

    log("Escribiendo el usuario...", "info")
    _tipear(page, _SEL_USUARIO, correo, log)
    time.sleep(0.4)

    log("Presionando Next...", "info")
    try:
        page.click(_SEL_NEXT, timeout=8000)
    except Exception:
        try:
            page.press(_SEL_USUARIO, "Enter")
        except Exception:
            raise LoginFallido("No se pudo presionar Next")

    # ── Paso 2: pantalla de Okta (usuario + contraseña) ─────────────────────
    try:
        page.wait_for_selector(_SEL_CLAVE, state="visible", timeout=25000)
        # Dar tiempo a que el widget de Okta termine de montarse: si se escribe
        # antes, el texto queda en pantalla pero el widget no lo registra.
        time.sleep(2.5)
    except Exception:
        detalle = _texto(page, 250)
        raise LoginFallido(
            "Tras Next no apareció el campo de contraseña. "
            f"El portal muestra: {detalle}")

    # Tras "Next" el portal redirige a su pantalla de acceso (Okta, en español),
    # que pide NOMBRE DE USUARIO y CONTRASEÑA de nuevo.
    # Identificadores reales del widget (confirmados en el log)
    _OKTA_USER = "#okta-signin-username, input[name='username']"
    _OKTA_PASS = "#okta-signin-password, input[name='password']"

    # Okta suele traer el usuario ya puesto (y el campo bloqueado): solo se
    # escribe si viene vacío o distinto, para no perder tiempo ni romperlo.
    try:
        actual = page.evaluate(
            """(sel) => { const e = document.querySelector(sel.split(',')[0].trim())
                 || document.querySelector("input[name='username']");
                 return e ? (e.value || '') : null; }""", _OKTA_USER)
    except Exception:
        actual = None

    if actual and actual.strip().lower() == correo.strip().lower():
        log("El usuario ya viene puesto por el portal", "info")
    else:
        log("Escribiendo el usuario...", "info")
        try:
            _tipear(page, _OKTA_USER, correo, log)
        except Exception:
            pass
        time.sleep(0.6)

    log("Escribiendo la contraseña...", "info")
    _tipear(page, _OKTA_PASS, clave, log)
    time.sleep(0.8)

    # ¿El botón quedó activo? Si Okta no registró lo escrito, sigue inhabilitado
    # y el click no hace nada (sin mostrar error). En ese caso se reescribe.
    def _estado_boton():
        try:
            return page.evaluate("""() => {
                for (const b of document.querySelectorAll(
                        "input[type=submit], button, .button-primary")) {
                    const t = ((b.innerText || b.value || '')).trim().toLowerCase();
                    if (t.includes('iniciar') || t.includes('sign in')) {
                        const cs = getComputedStyle(b);
                        return {texto: (b.innerText || b.value || '').trim(),
                                deshabilitado: !!b.disabled ||
                                    b.getAttribute('aria-disabled') === 'true' ||
                                    (b.className || '').includes('disabled'),
                                opacidad: cs.opacity};
                    }
                }
                return null;
            }""")
        except Exception:
            return None

    est = _estado_boton()
    log(f"  estado del botón: {est}", "info")

    if est and est.get("deshabilitado"):
        log("  el botón está inhabilitado — reescribiendo los campos...", "warn")
        # Reescribir despacio, campo por campo, dando tiempo a que el widget valide
        try:
            _tipear(page, "#okta-signin-username, input[name='username']", correo, log)
            time.sleep(0.8)
            _tipear(page, "#okta-signin-password, input[name='password']", clave, log)
            time.sleep(1.2)
            est = _estado_boton()
            log(f"  estado del botón tras reescribir: {est}", "info")
        except Exception as e:
            log(f"  no se pudo reescribir: {type(e).__name__}", "warn")

    # Qué botones hay realmente en esta pantalla (queda en el log para diagnóstico)
    try:
        botones = page.evaluate("""() => Array.from(
            document.querySelectorAll("button, input[type=submit], input[type=button], a"))
            .filter(e => e.offsetParent !== null)
            .map(e => (e.tagName + '[' + (e.type || '') + ']' +
                       (e.id ? '#' + e.id : '') + ' "' +
                       (e.innerText || e.value || '').trim().slice(0, 25) + '"'))
            .slice(0, 8)""")
        log(f"  botones en pantalla: {botones}", "info")
    except Exception:
        pass

    url_antes = page.url

    # Enviar con un click REAL (los clicks por programa tampoco los registra
    # el framework). Se prueba por texto del botón y luego por submit.
    enviado = ""
    for descripcion, localizador in (
        ("botón Iniciar Sesión", page.get_by_role("button", name="Iniciar Sesión")),
        ("botón Sign in",        page.get_by_role("button", name="Sign in")),
        ("texto Iniciar Sesión", page.get_by_text("Iniciar Sesión", exact=False)),
        ("submit",               page.locator("input[type='submit'], button[type='submit']")),
    ):
        try:
            el = localizador.first
            el.wait_for(state="visible", timeout=4000)
            el.click(timeout=5000)
            enviado = descripcion
            break
        except Exception:
            continue
    if not enviado:
        try:
            page.locator(_SEL_CLAVE).first.press("Enter")
            enviado = "Enter"
        except Exception:
            enviado = "no se pudo enviar"
    log(f"  enviado con: {enviado}", "info")

    log("Enviando...", "info")
    # Esperar hasta 90 s: el portal pasa por /auth/implicit/callback y ahí se
    # queda "cargando" un buen rato antes de entrar. Se informa el avance.
    fin = time.time() + 90
    error_texto = ""
    ultimo_aviso = time.time()
    url_previa = page.url
    estable = 0          # veces seguidas SIN formulario (evita el falso positivo)
    while time.time() < fin:
        # ¿Cambió de dirección? Señal de que el acceso avanzó
        try:
            if page.url != url_previa:
                log(f"  → {page.url[:90]}", "info")
                url_previa = page.url
                if "callback" in page.url.lower() or "login" not in page.url.lower():
                    log("  El portal está procesando el acceso...", "info")
        except Exception:
            pass

        # El formulario de Okta desaparece un instante al enviarse: hay que
        # confirmar que se fue DE VERDAD (varias comprobaciones seguidas).
        if not _sigue_en_login(page):
            estable += 1
            if estable >= 4:
                break
        else:
            estable = 0

        if time.time() - ultimo_aviso > 15:
            ultimo_aviso = time.time()
            restante = int(fin - time.time())
            log(f"  esperando respuesta del portal... ({restante}s restantes)", "info")
        # Errores REALES: solo desde los recuadros de error de Okta, nunca del
        # texto general de la página. (Antes se buscaba la palabra "credenciales"
        # en todo el cuerpo y coincidía con "Ingrese sus credenciales para
        # iniciar sesión", el texto de bienvenida: fallo instantáneo y falso.)
        try:
            error_texto = page.evaluate("""() => {
                const cajas = document.querySelectorAll(
                    '.okta-form-infobox-error, [data-se="o-form-error-container"], ' +
                    '.o-form-error-container, .infobox-error, [role="alert"]');
                for (const c of cajas) {
                    if (c.offsetParent === null) continue;
                    const t = (c.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (t) return t.slice(0, 200);
                }
                return '';
            }""") or ""
        except Exception:
            pass
        if error_texto:
            break
        time.sleep(0.5)

    # ── Verificación en dos pasos ───────────────────────────────────────────
    if _pide_verificacion(page):
        if obtener_codigo is None:
            raise LoginFallido(
                "El portal pide un CÓDIGO DE VERIFICACIÓN y esta prueba no está "
                "preparada para pedírtelo.")
        _resolver_verificacion(page, log, obtener_codigo)

    if _sigue_en_login(page):
        # Diagnóstico completo para saber por qué rebotó
        estado_campos, alertas = "", ""
        try:
            estado_campos = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('input'))
                    .filter(e => e.offsetParent !== null && e.type !== 'hidden')
                    .map(e => (e.type + ':' + (e.name || e.id || '?') + ':' +
                               ((e.value || '').trim() ? 'con dato' : 'VACIO')))
                    .join(' | ');
            }""") or ""
            alertas = page.evaluate("""() => {
                const sels = ['[role=alert]', '.error', '.alert', '.o-form-error-container',
                              '.infobox-error', '[class*=error]', '[class*=Error]'];
                const out = [];
                for (const s of sels) {
                    for (const e of document.querySelectorAll(s)) {
                        const t = (e.innerText || '').trim().replace(/\\s+/g, ' ');
                        if (t && t.length < 200 && !out.includes(t)) out.push(t);
                    }
                }
                return out.slice(0, 3).join(' / ');
            }""") or ""
        except Exception:
            pass
        cambio = "sí" if page.url != url_antes else "no"
        detalle = error_texto or alertas or _texto(page, 180)
        raise LoginFallido(
            f"No se pudo entrar. Portal: {detalle}"
            f"{(' [avisos: ' + alertas + ']') if alertas and alertas != detalle else ''}"
            f" [campos: {estado_campos}] [cambió de página: {cambio}]")

    # Salió del login. Ahora puede quedar en /auth/implicit/callback "cargando":
    # se espera a que termine de resolver antes de dar por buena la entrada.
    if "callback" in (page.url or "").lower():
        log("Procesando el acceso (pantalla de carga)...", "info")
        fin_cb = time.time() + 60
        while time.time() < fin_cb and "callback" in (page.url or "").lower():
            time.sleep(1)
        log(f"  → {page.url[:90]}", "info")

    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass

    log(f"Sesión iniciada — {page.url}", "ok")
    return True


def probar_login(correo, clave, log, ruta_captura=None, obtener_codigo=None, ruts=None):
    """Entra al portal, confirma el acceso y guarda una captura de pantalla.

    Equifax detecta navegadores automatizados: en modo invisible (headless)
    el acceso rebota sin dar error. Por eso se levanta una pantalla virtual
    (xvfb) y el navegador corre CON pantalla, igual que en el módulo Mi DT.
    """
    display = None
    headless = True
    try:
        from pyvirtualdisplay import Display
        display = Display(visible=0, size=(1440, 1000))
        display.start()
        headless = False
        log("Pantalla virtual iniciada — navegador con pantalla", "info")
    except Exception as e:
        log(f"Sin pantalla virtual ({str(e)[:40]}); se usará modo invisible", "warn")

    try:
      with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-features=IsolateOrigins,site-per-process"],
        )
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
            locale="es-CL",
            timezone_id="America/Santiago",
        )
        # Ocultar las señales típicas de automatización
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['es-CL','es','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = window.chrome || {runtime: {}};
        """)
        page = ctx.new_page()
        page.set_default_timeout(45000)

        def _captura(etiqueta=""):
            if not ruta_captura:
                return
            try:
                os.makedirs(os.path.dirname(ruta_captura), exist_ok=True)
                page.screenshot(path=ruta_captura, full_page=False)
                log(f"Captura de pantalla guardada{etiqueta}", "ok")
            except Exception as e:
                log(f"No se pudo guardar la captura: {type(e).__name__}", "warn")

        try:
            try:
                hacer_login(page, correo, clave, log, obtener_codigo=obtener_codigo)
            except LoginFallido:
                # Guardar la captura del FALLO para poder ver qué muestra el portal
                _captura(" (pantalla del error)")
                _dump_campos(page, log)
                raise

            # Click en botón "Ingresar" para acceder a Reportes Interactivos
            try:
                btn = page.locator('[data-test-id="appTypeButton"], button:has-text("Ingresar")').first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_load_state('domcontentloaded', timeout=15000)
                    log("✓ Click en botón 'Ingresar'", "ok")
                else:
                    log("⚠ No se encontró botón 'Ingresar'", "warn")
            except Exception as e:
                log(f"⚠ Error al hacer click en 'Ingresar': {e}", "warn")

            # Dejar constancia de dónde quedó y qué se ve
            info = {"url": page.url, "titulo": ""}
            try:
                info["titulo"] = page.title()
            except Exception:
                pass
            log(f"Página tras el acceso: {info['titulo']}", "info")
            resumen = _texto(page, 300)
            if resumen:
                log(f"Contenido: {resumen}", "info")
            _captura()
            return info
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            browser.close()
    finally:
        if display:
            try:
                display.stop()
            except Exception:
                pass
