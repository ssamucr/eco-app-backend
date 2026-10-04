import os
from pathlib import Path

import pyodbc


def _cargar_env():
    """Lee el archivo .env (junto a este modulo) sin pisar las variables ya definidas en el entorno."""
    ruta = Path(__file__).with_name(".env")
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env()

SERVIDOR = os.environ.get("ECO_DB_SERVIDOR", "localhost\SQLEXPRESS")
BASE_DATOS = os.environ.get("ECO_DB_BASE_DATOS", "eco")
USUARIO = os.environ.get("ECO_DB_USUARIO")
CONTRASENA = os.environ.get("ECO_DB_CONTRASENA")


def get_connection():
    if not USUARIO or not CONTRASENA:
        raise RuntimeError(
            "Faltan las credenciales de la base de datos: copia .env.example como .env y completa "
            "ECO_DB_USUARIO y ECO_DB_CONTRASENA."
        )
    cadena_conexion = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={SERVIDOR};DATABASE={BASE_DATOS};UID={USUARIO};PWD={CONTRASENA};"
    )
    return pyodbc.connect(cadena_conexion)
