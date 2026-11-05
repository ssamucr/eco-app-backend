"""Punto de entrada que sirve la API de Eco y, aparte, el MCP:  uvicorn eco_mcp.asgi:app

  /mcp y /.well-known/*   -> servidor MCP (con su propia autenticacion OAuth)
  todo lo demas           -> la API de siempre (main.app), sin modificar

El MCP vive en su propio paquete: main.py, auth.py y el resto de la API no lo conocen. Si falta configuracion del MCP
(o algo falla al montarlo) se sirve solo la API, nunca un MCP abierto.
"""

import logging

from main import app as api

from .config import Configuracion, ConfiguracionIncompleta

registro = logging.getLogger("eco_mcp")


def _prefijo_mcp(ruta):
    return ruta == "/mcp" or ruta.startswith("/mcp/") or ruta.startswith("/.well-known/")


def _montar():
    try:
        config = Configuracion()
    except ConfiguracionIncompleta as motivo:
        registro.warning("MCP desactivado: %s", motivo)
        return api
    from .servidor import crear_servidor

    mcp = crear_servidor(api, config)
    app_mcp = mcp.http_app(
        path="/mcp", stateless_http=True, host_origin_protection=True, allowed_hosts=config.hosts,
    )

    async def despachar(scope, receive, send):
        # `lifespan` lo necesita el MCP (administrador de sesiones); la API no usa eventos de ciclo de vida.
        if scope["type"] == "lifespan":
            return await app_mcp(scope, receive, send)
        if scope["type"] in ("http", "websocket") and _prefijo_mcp(scope["path"]):
            return await app_mcp(scope, receive, send)
        return await api(scope, receive, send)

    registro.info("MCP montado en /mcp (escritura: %s, login: %s)", config.escritura, not config.sin_autenticacion)
    return despachar


app = _montar()
