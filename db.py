import pyodbc

# Edita estos valores con los datos reales de tu instancia.
SERVIDOR = "localhost\\SQLEXPRESS"
BASE_DATOS = "eco"
USUARIO = "eco_app"
CONTRASENA = "nqy8rpcpBdQt"


def get_connection():
    cadena_conexion = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={SERVIDOR};DATABASE={BASE_DATOS};UID={USUARIO};PWD={CONTRASENA};"
    )
    return pyodbc.connect(cadena_conexion)
