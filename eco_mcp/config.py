"""Configuracion del servidor MCP (todo por variables de entorno; nada de esto es secreto).

SUPABASE_URL                 proyecto de Supabase que emite los tokens (el mismo de la API).
ECO_USUARIOS_PERMITIDOS      correos (o ids) con acceso, separados por coma (el mismo de la API).
ECO_MCP_URL_PUBLICA          URL publica de la API, p. ej. https://api.eco.samu0x.dev (sin /mcp al final).
ECO_MCP_HOSTS_PERMITIDOS     hosts que pueden llamar a /mcp, separados por coma (por defecto, el de la URL publica).
ECO_MCP_ESCRITURA            "0" deja solo las herramientas de lectura.
ECO_MCP_ZONA_UTC             desplazamiento horario de "hoy" para fechas omitidas (por defecto -5, Panama).
ECO_MCP_SIN_AUTENTICACION    "1" apaga el login; solo se admite con una URL publica local (pruebas).
"""

import os
from urllib.parse import urlparse


class ConfiguracionIncompleta(Exception):
    """Falta algo para montar el MCP: el MCP no se monta, pero la API sigue funcionando."""


def _lista(nombre):
    return [x.strip() for x in os.environ.get(nombre, "").split(",") if x.strip()]


def _es_local(url):
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


class Configuracion:
    def __init__(self):
        self.supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        self.usuarios = [u.lower() for u in _lista("ECO_USUARIOS_PERMITIDOS")]
        self.url_publica = os.environ.get("ECO_MCP_URL_PUBLICA", "").strip().rstrip("/")
        self.escritura = os.environ.get("ECO_MCP_ESCRITURA", "1") != "0"
        self.zona_utc = int(os.environ.get("ECO_MCP_ZONA_UTC", "-5"))
        self.sin_autenticacion = os.environ.get("ECO_MCP_SIN_AUTENTICACION") == "1"

        if not self.url_publica:
            raise ConfiguracionIncompleta("Falta ECO_MCP_URL_PUBLICA.")
        if self.sin_autenticacion:
            if not _es_local(self.url_publica):
                raise ConfiguracionIncompleta("ECO_MCP_SIN_AUTENTICACION solo se admite con una URL publica local.")
        else:
            if not self.supabase_url:
                raise ConfiguracionIncompleta("Falta SUPABASE_URL.")
            if not self.usuarios:
                raise ConfiguracionIncompleta("Falta ECO_USUARIOS_PERMITIDOS.")

        host = urlparse(self.url_publica).netloc
        self.hosts = _lista("ECO_MCP_HOSTS_PERMITIDOS") or [host]
