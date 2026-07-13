# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, jsonify, session
from functools import wraps
import json

reportes_bp = Blueprint('reportes', __name__, url_prefix='/reportes')

def login_required_r(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'error': 'No autorizado'}), 401
        return f(*args, **kwargs)
    return decorated

@reportes_bp.route('/')
def index():
    if not session.get('user_id'):
        from flask import redirect, url_for
        return redirect(url_for('login'))
    return render_template('reportes/index.html')

@reportes_bp.route('/api/analizar', methods=['POST'])
@login_required_r
def analizar_excel():
    if 'excel' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400

    archivo = request.files['excel']
    if not archivo.filename:
        return jsonify({'error': 'Archivo vacío'}), 400

    try:
        from reportes_logic import leer_excel_cierre, datos_a_chartjs
        excel_bytes = archivo.read()
        hojas_cierre, todas_hojas = leer_excel_cierre(excel_bytes)

        if not hojas_cierre:
            return jsonify({'error': 'No se encontraron hojas de Cierre en el Excel. Asegúrate de que el archivo tenga hojas con nombre "Cierre ..."'}), 400

        resultado = {}
        for nombre_hoja, datos in hojas_cierre.items():
            if 'error' in datos:
                resultado[nombre_hoja] = {'error': datos['error']}
                continue
            charts = datos_a_chartjs(datos)
            resultado[nombre_hoja] = {
                'charts': charts,
                'tablas': {
                    'por_institucion': _tabla_a_lista(datos.get('por_institucion', {})),
                    'por_anio':        _tabla_a_lista(datos.get('por_anio', {})),
                    'por_estatus':     _tabla_a_lista(datos.get('por_estatus', {})),
                    'por_motivo':      _tabla_a_lista(datos.get('por_motivo', {})),
                    'resumen':         datos.get('resumen_tabla', {}),
                    'por_fee':         _tabla_a_lista(datos.get('por_fee', {})),
                },
                'header_cliente': datos.get('header_cliente', {}),
            }

        return jsonify({'ok': True, 'hojas': list(resultado.keys()), 'datos': resultado})

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'detalle': traceback.format_exc()}), 500

def _tabla_a_lista(tabla_dict):
    """Convierte {label: monto} a [{label, monto, pct}] con porcentaje sobre el total."""
    items = [(k, v) for k, v in tabla_dict.items() if k != '__total__']
    total = tabla_dict.get('__total__') or sum(v for _, v in items) or 1
    return [
        {'label': k, 'monto': v, 'pct': round(v / total * 100, 1)}
        for k, v in items
    ]
