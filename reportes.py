# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, jsonify, session, current_app
from functools import wraps
import os
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

@reportes_bp.route('/api/bases')
@login_required_r
def listar_bases():
    """Lista los archivos Base de Deudas guardados en DATA_DIR/bases/."""
    import time as _time
    data_dir = current_app.config.get('DATA_DIR', os.environ.get('DATA_DIR', 'adjuntos'))
    bases_dir = os.path.join(data_dir, 'bases')
    if not os.path.exists(bases_dir):
        return jsonify([])
    archivos = []
    for f in os.listdir(bases_dir):
        if not f.endswith('.xlsx'):
            continue
        ruta = os.path.join(bases_dir, f)
        mtime = os.path.getmtime(ruta)
        archivos.append({
            'nombre': f,
            'fecha': _time.strftime('%d/%m/%Y %H:%M', _time.localtime(mtime)),
            'mtime': mtime,
        })
    archivos.sort(key=lambda x: x['mtime'], reverse=True)
    for a in archivos:
        del a['mtime']
    return jsonify(archivos)

@reportes_bp.route('/api/analizar-base', methods=['POST'])
@login_required_r
def analizar_base_guardada():
    """Analiza un archivo Base de Deudas guardado en el servidor."""
    body = request.get_json(force=True) or {}
    nombre = body.get('nombre', '')
    if not nombre.endswith('.xlsx') or '/' in nombre or '\\' in nombre or '..' in nombre:
        return jsonify({'error': 'Nombre de archivo inválido'}), 400

    data_dir = current_app.config.get('DATA_DIR', os.environ.get('DATA_DIR', 'adjuntos'))
    ruta = os.path.join(data_dir, 'bases', nombre)
    if not os.path.exists(ruta):
        return jsonify({'error': 'Archivo no encontrado en el servidor'}), 404

    with open(ruta, 'rb') as f:
        excel_bytes = f.read()

    return _procesar_bytes(excel_bytes)

@reportes_bp.route('/api/analizar', methods=['POST'])
@login_required_r
def analizar_excel():
    if 'excel' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400
    archivo = request.files['excel']
    if not archivo.filename:
        return jsonify({'error': 'Archivo vacío'}), 400
    return _procesar_bytes(archivo.read())

def _procesar_bytes(excel_bytes):
    try:
        from reportes_logic import leer_excel_cierre, datos_a_chartjs
        hojas_cierre, todas_hojas = leer_excel_cierre(excel_bytes)

        if not hojas_cierre:
            return jsonify({'error': 'No se encontraron hojas Cierre ni hoja Base AFP en el Excel.'}), 400

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
