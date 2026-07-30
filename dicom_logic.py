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


def _tipear(page, selector, texto, log=None):
    """Escribe TECLA POR TECLA (no page.fill).

    El portal está hecho con un framework JS: si el valor se asigna por
    programa, la página lo muestra pero internamente lo considera vacío y el
    botón de acceso no hace nada. Con pulsaciones reales sí lo registra.
    """
    loc = page.locator(selector).first
    loc.click()
    try:
        loc.press("Control+a")
        loc.press("Backspace")
    except Exception:
        pass
    try:
        loc.press_sequentially(texto, delay=70)
    except AttributeError:          # Playwright antiguo
        loc.type(texto, delay=70)
    try:
        loc.dispatch_event("input")
        loc.dispatch_event("change")
    except Exception:
        pass


def hacer_login(page, correo, clave, log):
    """Login en dos pasos: usuario → Next → contraseña → entrar."""
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

    # ── Paso 2: contraseña ──────────────────────────────────────────────────
    try:
        page.wait_for_selector(_SEL_CLAVE, state="visible", timeout=25000)
    except Exception:
        detalle = _texto(page, 250)
        raise LoginFallido(
            "Tras Next no apareció el campo de contraseña. "
            f"El portal muestra: {detalle}")

    # Tras "Next" el portal redirige a su pantalla de acceso (en español), que
    # pide NOMBRE DE USUARIO **y** CONTRASEÑA de nuevo. Si el campo de usuario
    # está presente y vacío, hay que volver a escribirlo.
    try:
        usuario_vacio = page.evaluate("""() => {
            const cs = Array.from(document.querySelectorAll(
                "input[type='text'], input[type='email'], input:not([type])"))
                .filter(e => e.offsetParent !== null);
            if (!cs.length) return null;
            const u = cs[0];
            return {selectorIdx: 0, vacio: !(u.value || '').trim(),
                    id: u.id || '', name: u.name || ''};
        }""")
    except Exception:
        usuario_vacio = None

    if usuario_vacio and usuario_vacio.get("vacio"):
        log("La pantalla pide el usuario otra vez — escribiéndolo...", "info")
        # Tecla por tecla: asignarlo por programa no lo registra el sitio
        try:
            _tipear(page, "input[type='text']:visible, input[type='email']:visible",
                    correo, log)
        except Exception:
            try:
                _tipear(page, "input[type='text']", correo, log)
            except Exception:
                pass
        time.sleep(0.3)

    log("Escribiendo la contraseña...", "info")
    _tipear(page, _SEL_CLAVE, clave, log)
    time.sleep(0.4)

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
    # Esperar: entró, error de credenciales, o pide código de verificación
    fin = time.time() + 35
    error_texto = ""
    while time.time() < fin:
        if not _sigue_en_login(page):
            break
        try:
            error_texto = page.evaluate("""() => {
                const t = (document.body ? document.body.innerText : '').toLowerCase();
                for (const frase of ['unable to sign in', 'incorrect', 'invalid',
                                     'credenciales', 'inválid', 'no coinciden',
                                     'locked', 'bloquead', 'too many', 'intentos']) {
                    if (t.includes(frase)) {
                        const i = t.indexOf(frase);
                        return (document.body.innerText || '')
                            .substring(Math.max(0, i - 90), i + 100).replace(/\\s+/g, ' ');
                    }
                }
                return '';
            }""") or ""
        except Exception:
            pass
        if error_texto:
            break
        time.sleep(0.5)

    # ¿Pide código de verificación? Eso no lo puede resolver el bot solo.
    try:
        pide_codigo = page.evaluate("""() => {
            const t = (document.body ? document.body.innerText : '').toLowerCase();
            return t.includes('verification code') || t.includes('security code') ||
                   t.includes('enter code') || t.includes('codigo de verificacion') ||
                   t.includes('código de verificación') || t.includes('authenticator') ||
                   t.includes('multifactor') || t.includes('verify your identity');
        }""")
    except Exception:
        pide_codigo = False
    if pide_codigo:
        raise LoginFallido(
            "El portal pide un CÓDIGO DE VERIFICACIÓN (autenticación en dos pasos). "
            "Eso llega a tu correo o teléfono, así que el robot no puede completarlo "
            "solo. Habría que pedirle a Equifax una cuenta de servicio sin ese paso, "
            "o ingresar el código a mano cada vez.")

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

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    log(f"Sesión iniciada — {page.url}", "ok")
    return True


def probar_login(correo, clave, log, ruta_captura=None):
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
                hacer_login(page, correo, clave, log)
            except LoginFallido:
                # Guardar la captura del FALLO para poder ver qué muestra el portal
                _captura(" (pantalla del error)")
                _dump_campos(page, log)
                raise
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
