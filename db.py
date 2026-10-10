import os
import threading
from pathlib import Path

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


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

MAX_CONEXIONES = int(os.environ.get("ECO_DB_MAX_CONEXIONES", "8"))
_pool = None
_candado = threading.Lock()
_cupos = threading.BoundedSemaphore(MAX_CONEXIONES)


def _obtener_pool():
    global _pool
    with _candado:
        if _pool is None:
            url = os.environ.get("DATABASE_URL")
            if not url:
                raise RuntimeError(
                    "Falta DATABASE_URL: copia .env.example como .env y completa la cadena de conexion de Postgres."
                )
            _pool = ThreadedConnectionPool(1, MAX_CONEXIONES, url, connect_timeout=15)
        return _pool


class Cursor:
    """Cursor con la misma interfaz que se usaba antes: los parametros se escriben con `?`."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, consulta, parametros=()):
        if parametros:
            # los % literales se escapan porque psycopg2 usa %s para sus parametros
            self._cursor.execute(consulta.replace("%", "%%").replace("?", "%s"), tuple(parametros))
        else:
            self._cursor.execute(consulta)
        return self

    def __getattr__(self, nombre):
        return getattr(self._cursor, nombre)


class Conexion:
    """Conexion prestada del pool: close() la devuelve, descartando lo que no se confirmo."""

    def __init__(self, conexion):
        self._conexion = conexion

    def cursor(self):
        return Cursor(self._conexion.cursor())

    def commit(self):
        self._conexion.commit()

    def rollback(self):
        self._conexion.rollback()

    def close(self):
        pool = _obtener_pool()
        try:
            if not self._conexion.closed:
                self._conexion.rollback()
        finally:
            pool.putconn(self._conexion, close=bool(self._conexion.closed))
            _cupos.release()


def _viva(conexion):
    """Las conexiones ociosas pueden haber sido cerradas por el servidor: se prueban antes de usarlas."""
    if conexion.closed:
        return False
    try:
        with conexion.cursor() as cursor:
            cursor.execute("SELECT 1")
        conexion.rollback()
        return True
    except psycopg2.Error:
        return False


def get_connection():
    _cupos.acquire()
    try:
        pool = _obtener_pool()
        for _ in range(3):
            conexion = pool.getconn()
            if _viva(conexion):
                return Conexion(conexion)
            pool.putconn(conexion, close=True)
        raise RuntimeError("No se pudo conectar a la base de datos.")
    except Exception:
        _cupos.release()
        raise
