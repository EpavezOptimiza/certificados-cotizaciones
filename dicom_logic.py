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


def _hay(page, selector):
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


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
    page.fill(_SEL_USUARIO, correo)
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
        log("La pantalla pide el usuario otra vez — completando...", "info")
        try:
            page.evaluate("""(correo) => {
                const cs = Array.from(document.querySelectorAll(
                    "input[type='text'], input[type='email'], input:not([type])"))
                    .filter(e => e.offsetParent !== null);
                if (cs.length) {
                    const u = cs[0];
                    u.focus(); u.value = correo;
                    u.dispatchEvent(new Event('input',  {bubbles: true}));
                    u.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""", correo)
            time.sleep(0.3)
        except Exception:
            pass

    log("Escribiendo la contraseña...", "info")
    page.fill(_SEL_CLAVE, clave)
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

    # Enviar: PRIMERO el botón cuyo texto sea "Iniciar Sesión" (el correcto),
    # después los submit genéricos, y por último Enter.
    enviado = False
    try:
        enviado = bool(page.evaluate("""() => {
            const cands = Array.from(document.querySelectorAll(
                "button, input[type=submit], input[type=button]"))
                .filter(e => e.offsetParent !== null);
            for (const b of cands) {
                const t = ((b.innerText || b.value || '')).trim().toLowerCase();
                if (t.includes('iniciar sesion') || t.includes('iniciar sesión') ||
                    t.includes('sign in') || t === 'ingresar') {
                    b.click(); return true;
                }
            }
            return false;
        }"""))
    except Exception:
        pass
    if not enviado:
        for sel in ("input[type='submit']", "button[type='submit']"):
            try:
                page.click(sel, timeout=4000)
                enviado = True
                break
            except Exception:
                continue
    if not enviado:
        page.press(_SEL_CLAVE, "Enter")
    log(f"  formulario enviado ({'botón' if enviado else 'Enter'})", "info")

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
    """Entra al portal, confirma el acceso y guarda una captura de pantalla."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
            locale="es-CL",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = ctx.new_page()
        page.set_default_timeout(45000)
        try:
            hacer_login(page, correo, clave, log)
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
            if ruta_captura:
                try:
                    os.makedirs(os.path.dirname(ruta_captura), exist_ok=True)
                    page.screenshot(path=ruta_captura, full_page=False)
                    log("Captura de pantalla guardada", "ok")
                except Exception as e:
                    log(f"No se pudo guardar la captura: {type(e).__name__}", "warn")
            return info
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            browser.close()
