"""Respaldo completo de la base (todas las tablas) en un archivo comprimido, en tu equipo.

    python database/respaldar.py            # crea respaldos/eco-AAAAMMDD-HHMM.json.gz
    python database/respaldar.py --guardar 12   # conserva solo los 12 respaldos mas recientes (por defecto 12)

Usa DATABASE_URL del .env. Los respaldos contienen tus datos financieros: la carpeta respaldos/ no se sube a git.
Para programarlo en Windows (por ejemplo, cada lunes) usa el Programador de tareas y apunta a este comando.
"""
import argparse
import gzip
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import db  # noqa: E402

TABLAS = [
    "personas", "categorias", "cuentas", "subcuentas", "plan_recurrente", "plan_recurrente_destinos",
    "transacciones", "movimientos_subcuenta", "financiamientos", "cuotas_financiamiento", "obligaciones",
    "liquidaciones_obligacion", "ciclo_config", "ciclo_config_dia", "ciclo",
]


def _json(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    raise TypeError(type(valor))


def respaldar(carpeta, conservar):
    conexion = db.get_connection()
    try:
        cursor = conexion.cursor()
        datos = {"creado": datetime.now().isoformat(timespec="seconds"), "tablas": {}}
        for tabla in TABLAS:
            cursor.execute("SELECT * FROM " + tabla)
            columnas = [c[0] for c in cursor.description]
            datos["tablas"][tabla] = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
    finally:
        conexion.close()
    carpeta.mkdir(exist_ok=True)
    destino = carpeta / ("eco-%s.json.gz" % datetime.now().strftime("%Y%m%d-%H%M"))
    with gzip.open(destino, "wt", encoding="utf-8") as f:
        json.dump(datos, f, default=_json, ensure_ascii=False)
    # se conservan solo los mas recientes
    for viejo in sorted(carpeta.glob("eco-*.json.gz"))[:-conservar]:
        viejo.unlink()
    total = sum(len(f) for f in datos["tablas"].values())
    print("Respaldo creado: %s (%d filas en %d tablas)" % (destino, total, len(TABLAS)))
    return destino


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--guardar", type=int, default=12, help="cuantos respaldos conservar")
    respaldar(RAIZ / "respaldos", max(ap.parse_args().guardar, 1))
