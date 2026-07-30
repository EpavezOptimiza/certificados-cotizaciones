"""Bot para el portal DICOM / Equifax (sec.equifax.cl/compraonline).

Por ahora SOLO inicia sesión y comprueba que entró. No compra ni descarga
nada: el portal cobra por cada documento y esa parte se agrega aparte,
cuando esté confirmado cómo se paga.
"""

import os
import time

from playwright.sync_api import sync_playwright

URL_LOGIN = "https://sec.equifax.cl/compraonline/login"

# Campos del formulario (verificados en el portal real)
_SEL_EMAIL = "input[type='email']"
_SEL_CLAVE = "input[type='password']"


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


def hacer_login(page, correo, clave, log):
    """Inicia sesión en el portal. Lanza LoginFallido si no entra."""
    log("Abriendo el portal DICOM...", "info")
    page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=45000)

    try:
        page.wait_for_selector(_SEL_EMAIL, state="visible", timeout=20000)
    except Exception:
        raise LoginFallido(f"No apareció el formulario de acceso. "
                           f"La página muestra: {_texto(page, 200)}")

    log("Ingresando credenciales...", "info")
    page.fill(_SEL_EMAIL, correo)
    time.sleep(0.4)
    page.fill(_SEL_CLAVE, clave)
    time.sleep(0.4)

    # Botón "Iniciar sesión"
    try:
        page.click("button[type='submit']", timeout=8000)
    except Exception:
        page.evaluate("""() => {
            for (const b of document.querySelectorAll('button')) {
                if ((b.innerText || '').toLowerCase().includes('iniciar sesion') ||
                    (b.innerText || '').toLowerCase().includes('iniciar sesión')) {
                    b.click(); return;
                }
            }
        }""")

    log("Enviando...", "info")
    # Esperar a que el formulario desaparezca (entró) o aparezca un error
    fin = time.time() + 30
    error_texto = ""
    while time.time() < fin:
        if not _sigue_en_login(page):
            break
        try:
            error_texto = page.evaluate("""() => {
                const t = (document.body ? document.body.innerText : '').toLowerCase();
                for (const frase of ['credenciales', 'incorrect', 'inválid', 'invalid',
                                     'no coinciden', 'usuario o contraseña', 'bloquead',
                                     'intentos']) {
                    if (t.includes(frase)) {
                        const i = t.indexOf(frase);
                        return (document.body.innerText || '')
                            .substring(Math.max(0, i - 90), i + 90).replace(/\\s+/g, ' ');
                    }
                }
                return '';
            }""") or ""
        except Exception:
            pass
        if error_texto:
            break
        time.sleep(0.5)

    if _sigue_en_login(page):
        detalle = error_texto or _texto(page, 200)
        raise LoginFallido(f"No se pudo entrar. El portal responde: {detalle}")

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
