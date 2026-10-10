"""Copia los datos de la base SQL Server original a PostgreSQL (una sola vez) y verifica que cuadren.

Origen (solo lectura): variables ECO_DB_SERVIDOR / ECO_DB_BASE_DATOS / ECO_DB_USUARIO / ECO_DB_CONTRASENA del .env.
Destino: DATABASE_URL (la base ya debe tener el esquema de database/eco.sql y estar vacia).

    python database/migrar_desde_sqlserver.py

Requiere pyodbc, que ya no esta en requirements.txt (solo se necesita para esta migracion).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402,F401  (carga el .env)
import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

try:
    import pyodbc
except ImportError:
    sys.exit("Falta pyodbc: pip install pyodbc (solo para esta migracion).")

# en orden de dependencias (claves foraneas)
TABLAS = [
    ("personas", "id_persona"),
    ("categorias", "id_categoria"),
    ("cuentas", "id_cuenta"),
    ("subcuentas", "id_subcuenta"),
    ("plan_recurrente", "id_plan"),
    ("plan_recurrente_destinos", "id_plan_recurrente_destino"),
    ("transacciones", "id_transaccion"),
    ("movimientos_subcuenta", "id_movimiento_subcuenta"),
    ("financiamientos", "id_financiamiento"),
    ("cuotas_financiamiento", "id_cuota_financiamiento"),
    ("obligaciones", "id_obligacion"),
    ("liquidaciones_obligacion", "id_liquidacion"),
    ("ciclo_config", "id_ciclo_config"),
    ("ciclo_config_dia", None),
    ("ciclo", "id_ciclo"),
]
BOOLEANAS = {"activo", "es_principal", "ajustado"}


def origen():
    cadena = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=%s;DATABASE=%s;UID=%s;PWD=%s;" % (
        os.environ.get("ECO_DB_SERVIDOR", "localhost\\SQLEXPRESS"), os.environ.get("ECO_DB_BASE_DATOS", "eco"),
        os.environ["ECO_DB_USUARIO"], os.environ["ECO_DB_CONTRASENA"],
    )
    return pyodbc.connect(cadena, readonly=True)


def main():
    destino_url = os.environ.get("DATABASE_URL")
    if not destino_url:
        sys.exit("Falta DATABASE_URL (la base de destino).")
    src = origen()
    dst = psycopg2.connect(destino_url)
    try:
        s, d = src.cursor(), dst.cursor()
        for tabla, _ in TABLAS:
            d.execute("SELECT COUNT(*) FROM " + tabla)
            if d.fetchone()[0]:
                sys.exit("La tabla %s del destino no esta vacia: la migracion solo corre sobre una base vacia." % tabla)

        for tabla, clave in TABLAS:
            s.execute("SELECT * FROM " + tabla)
            columnas = [c[0] for c in s.description]
            filas = [tuple(bool(v) if (c in BOOLEANAS and v is not None) else v for c, v in zip(columnas, fila))
                     for fila in s.fetchall()]
            if filas:
                execute_values(d, "INSERT INTO %s (%s) VALUES %%s" % (tabla, ", ".join(columnas)), filas)
            if clave:  # las identidades siguen despues del mayor id copiado
                d.execute("SELECT setval(pg_get_serial_sequence(%s, %s), COALESCE((SELECT MAX(" + clave + ") FROM " + tabla + "), 0) + 1, false)",
                          (tabla, clave))
            print("%-26s %5d filas" % (tabla, len(filas)))

        # verificacion contra el origen: conteos, saldos de cuentas, subcuentas y estado de obligaciones
        problemas = []
        for tabla, _ in TABLAS:
            s.execute("SELECT COUNT(*) FROM " + tabla)
            d.execute("SELECT COUNT(*) FROM " + tabla)
            if s.fetchone()[0] != d.fetchone()[0]:
                problemas.append("conteo de " + tabla)
        s.execute("SELECT id_cuenta, saldo_calculado FROM vw_saldo_cuentas ORDER BY id_cuenta")
        d.execute("SELECT id_cuenta, saldo_calculado FROM vw_saldo_cuentas ORDER BY id_cuenta")
        if [tuple(f) for f in s.fetchall()] != [tuple(f) for f in d.fetchall()]:
            problemas.append("saldos de cuentas (vw_saldo_cuentas)")
        s.execute("SELECT id_subcuenta, saldo FROM subcuentas ORDER BY id_subcuenta")
        d.execute("SELECT id_subcuenta, saldo FROM subcuentas ORDER BY id_subcuenta")
        if [tuple(f) for f in s.fetchall()] != [tuple(f) for f in d.fetchall()]:
            problemas.append("saldos de subcuentas")
        s.execute("SELECT id_obligacion, monto_pendiente, resuelta FROM vw_estado_obligaciones ORDER BY id_obligacion")
        d.execute("SELECT id_obligacion, monto_pendiente, resuelta FROM vw_estado_obligaciones ORDER BY id_obligacion")
        if [tuple(f) for f in s.fetchall()] != [tuple(f) for f in d.fetchall()]:
            problemas.append("estado de obligaciones")
        if problemas:
            dst.rollback()
            sys.exit("NO CUADRA, no se guardo nada: " + ", ".join(problemas))
        dst.commit()
        print("Migracion completa y verificada: conteos, saldos de cuentas, subcuentas y obligaciones coinciden.")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
