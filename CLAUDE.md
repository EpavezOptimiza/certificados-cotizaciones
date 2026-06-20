# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Deployment

- **Platform**: Railway (auto-deploys on push to `main`)
- **Builder**: NIXPACKS (defined in `railway.toml`)
- **Start**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 600`
- **Browser automation**: Playwright installs its own Chromium during build via `nixpacks.toml`

To deploy: `git push origin main` — Railway builds and deploys automatically.

## Running locally

```bash
pip install -r requirements.txt
playwright install chromium
python app.py
```

App runs on `http://localhost:5000`. SQLite DB is created automatically at startup (`certificados.db`).

## Architecture

Single Flask app (`app.py`) with two registered Blueprints:

| Blueprint | File | URL prefix |
|-----------|------|------------|
| Cartas Previsionales | `cartas.py` | `/cartas` |
| Reportes de Cierre | `reportes.py` | `/reportes` |

**Auth**: Cookie-based (`session_token`). Three decorators: `login_required`, `admin_required`, `api_login_required`. Roles: `admin`, `consultor` (others). Blueprints implement their own auth check via `_get_user()` querying the same `sesiones` table.

**Database**: SQLite via `database.py`. `get_conn()` returns a connection with `row_factory = sqlite3.Row`. `init_db()` is called at startup. `DB_PATH` defaults to `certificados.db` in the project root, overridable via env var.

**File storage**: PDFs and uploaded files go to `DATA_DIR` (env var, defaults to `adjuntos/`). On Railway this is a mounted volume.

**Empresa data**: Loaded from a Google Sheets CSV (`EXCEL_URL` in `app.py`) into an in-memory cache `_empresa_cache`. Not persisted to SQLite.

## Module details

### cartas.py
- Parses Excel debt files (`parsear_excel`, `agrupar_por_trabajador`)
- Generates PDF letters with ReportLab (`generar_carta_pdf`)
- Runs a **Playwright headless bot** (`run_bot_previred`) in a background thread for PreviRed automation
- Bot jobs tracked in `_jobs` dict (in-memory, cleared on restart)
- Bot flow: login → select empresa → regularización manual → fill form (RUT, AFP, salud, causa, fecha cese) → períodos ausentismo if needed → download comprobante PDF

### reportes.py + reportes_logic.py
- Accepts Excel upload, detects sheets named "Cierre ..."
- `leer_cierre()` scans for pivot tables by searching column A headers: `institucion`, `años`, `estatus`, `motivos de deuda`
- Returns data formatted for Chart.js (doughnut + bar charts)

### previred_logic.py / base_deudas_logic.py / pdf_excel_logic.py
- Supporting logic used by `app.py` routes (not Blueprints)

## Key environment variables

| Var | Purpose |
|-----|---------|
| `DATA_DIR` | Where PDFs are stored |
| `DB_PATH` | SQLite database path |
| `PORT` | Injected by Railway at runtime |

## Templates

Each module has its own self-contained HTML (no base template inheritance). Templates live in `templates/` root for `app.py` routes and `templates/cartas/`, `templates/reportes/` for blueprints. All pages replicate the nav bar directly in HTML.
