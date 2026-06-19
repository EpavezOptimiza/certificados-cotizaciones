# -*- coding: utf-8 -*-
"""
Lógica de lectura del Excel de cierre y generación de datos para gráficos.
Lee las tablas pivot de la hoja "Cierre ..." y devuelve dicts listos para Chart.js.
"""
import io, re
import openpyxl

MESES_ES = ['','Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

def _fmt_monto(v):
    try:
        return int(float(str(v).replace('$','').replace(',','')))
    except:
        return 0

def _col_a(col):
    """Devuelve el índice de columna desde letra Excel (A=0, B=1, ...)"""
    r = 0
    for c in col.upper():
        r = r * 26 + (ord(c) - ord('A') + 1)
    return r - 1

def leer_cierre(wb, nombre_hoja):
    """
    Lee una hoja de cierre y extrae todas las tablas pivot.
    Retorna dict con secciones de datos.
    """
    ws = wb[nombre_hoja]
    datos = {}

    # Leer todas las filas no vacías
    filas = []
    for row in ws.iter_rows(values_only=True):
        if any(c is not None for c in row):
            filas.append(list(row))
        else:
            filas.append(None)  # fila vacía como separador

    def buscar_tabla(header_col0, header_col1_keywords=None, max_buscar=200):
        """Busca una tabla por el valor de la primera columna del encabezado."""
        for i, fila in enumerate(filas[:max_buscar]):
            if fila is None: continue
            v0 = str(fila[0] or '').strip()
            if v0.lower() == header_col0.lower():
                # Leer filas hasta encontrar "Total general" o fila vacía
                tabla = {}
                for j in range(i+1, min(i+50, len(filas))):
                    f = filas[j]
                    if f is None: break
                    k = str(f[0] or '').strip()
                    if not k: break
                    v = _fmt_monto(f[1]) if len(f) > 1 else 0
                    if k.lower() == 'total general':
                        tabla['__total__'] = v
                    else:
                        tabla[k] = v
                return tabla
        return {}

    # 1. Por institución (A col=Institucion, B col=Monto Interes)
    datos['por_institucion'] = buscar_tabla('institucion')
    if not datos['por_institucion']:
        datos['por_institucion'] = buscar_tabla('afp')

    # 2. Por año
    datos['por_anio'] = buscar_tabla('años')
    if not datos['por_anio']:
        datos['por_anio'] = buscar_tabla('a?os')

    # 3. Por estatus
    datos['por_estatus'] = buscar_tabla('estatus')

    # 4. Por motivo de deuda
    datos['por_motivo'] = buscar_tabla('26_ motivos de deuda')
    if not datos['por_motivo']:
        datos['por_motivo'] = buscar_tabla('motivos de deuda')

    # 5. Tabla resumen superior (fila 3-8 aprox): Estatus AFP-AFC vs ISAPRE vs Total
    datos['resumen_tabla'] = _buscar_resumen(filas)

    return datos

def _buscar_resumen(filas):
    """Busca la tabla de resumen con columnas Estatus / AFP-AFC / ISAPRE / Total."""
    for i, fila in enumerate(filas[:30]):
        if fila is None: continue
        # Buscar fila con 'Estatus' en alguna columna
        for j, v in enumerate(fila):
            if str(v or '').strip().lower() == 'estatus':
                # Las 3 columnas siguientes son AFP-AFC, ISAPRE, Total
                tabla = {'headers': [], 'filas': []}
                for k in range(j+1, min(j+4, len(fila))):
                    tabla['headers'].append(str(fila[k] or '').strip())
                for m in range(i+1, min(i+15, len(filas))):
                    f = filas[m]
                    if f is None or f[j] is None: continue
                    etiqueta = str(f[j] or '').strip()
                    if not etiqueta: continue
                    vals = []
                    for k in range(j+1, min(j+4, len(f))):
                        vals.append(_fmt_monto(f[k]))
                    tabla['filas'].append({'label': etiqueta, 'valores': vals})
                if tabla['filas']:
                    return tabla
    return {}

def detectar_hojas_cierre(wb):
    """Retorna lista de hojas que parecen hojas de cierre."""
    return [n for n in wb.sheetnames if 'cierre' in n.lower()]

def leer_excel_cierre(excel_bytes):
    """
    Entrada: bytes del Excel.
    Salida: dict con {nombre_hoja: datos_cierre, ...} para todas las hojas de cierre.
    """
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=True)
    hojas_cierre = detectar_hojas_cierre(wb)
    resultado = {}
    for h in hojas_cierre:
        try:
            resultado[h] = leer_cierre(wb, h)
        except Exception as e:
            resultado[h] = {'error': str(e)}
    return resultado, wb.sheetnames

def datos_a_chartjs(datos):
    """Convierte los datos del cierre a formato Chart.js."""
    charts = {}

    COLORES_INST = [
        '#6366f1','#8b5cf6','#a78bfa','#c4b5fd',
        '#4f46e5','#7c3aed','#2563eb','#0ea5e9',
        '#10b981','#f59e0b','#ef4444','#ec4899',
    ]
    COLORES_ESTATUS = {
        'Corresponde Pago':      '#10b981',
        'Corresponde pago':      '#10b981',
        'En gestion':            '#f59e0b',
        'En Gestion':            '#f59e0b',
        'En gestión':            '#f59e0b',
        'Regularizado':          '#6366f1',
        'Regularizado Jun-26':   '#6366f1',
        'Regularizado May-26':   '#6366f1',
        'Regularizado - Informado': '#a78bfa',
        'Regularizado-Informado':   '#a78bfa',
        'Pagado en aclaracion':  '#0ea5e9',
        'Sin deuda':             '#94a3b8',
    }

    # Gráfico 1: Por institución (barra horizontal)
    inst = {k: v for k, v in datos.get('por_institucion', {}).items() if k != '__total__'}
    if inst:
        charts['por_institucion'] = {
            'type': 'bar',
            'labels': list(inst.keys()),
            'datasets': [{
                'label': 'Monto ($)',
                'data': list(inst.values()),
                'backgroundColor': COLORES_INST[:len(inst)],
                'borderRadius': 6,
            }],
            'options': {'indexAxis': 'y', 'title': 'Deuda por institución'},
        }

    # Gráfico 2: Por estatus (doughnut)
    est = {k: v for k, v in datos.get('por_estatus', {}).items() if k != '__total__' and v > 0}
    if est:
        colores_est = [COLORES_ESTATUS.get(k, '#94a3b8') for k in est.keys()]
        charts['por_estatus'] = {
            'type': 'doughnut',
            'labels': list(est.keys()),
            'datasets': [{
                'data': list(est.values()),
                'backgroundColor': colores_est,
                'hoverOffset': 6,
            }],
            'options': {'title': 'Estado de la deuda'},
        }

    # Gráfico 3: Por año (barra vertical)
    anios = {k: v for k, v in datos.get('por_anio', {}).items() if k != '__total__'}
    if anios:
        charts['por_anio'] = {
            'type': 'bar',
            'labels': [str(k) for k in anios.keys()],
            'datasets': [{
                'label': 'Monto ($)',
                'data': list(anios.values()),
                'backgroundColor': '#6366f1',
                'borderRadius': 4,
            }],
            'options': {'title': 'Deuda por año'},
        }

    # Gráfico 4: Por motivo (doughnut)
    mot = {k: v for k, v in datos.get('por_motivo', {}).items() if k != '__total__' and v > 0}
    if mot:
        charts['por_motivo'] = {
            'type': 'doughnut',
            'labels': list(mot.keys()),
            'datasets': [{
                'data': list(mot.values()),
                'backgroundColor': COLORES_INST[:len(mot)],
                'hoverOffset': 6,
            }],
            'options': {'title': 'Motivos de deuda'},
        }

    return charts
