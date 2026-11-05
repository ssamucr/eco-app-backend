"""Cliente de la API de Eco para las herramientas del MCP.

Llama a la aplicacion en el mismo proceso (sin red) con el mismo token del usuario, de modo que la API sigue siendo
la unica que decide que se puede leer o escribir: el MCP no toca la base de datos ni repite sus reglas.
"""

import httpx2
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token


class ClienteApi:
    def __init__(self, app):
        self._http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://eco-api.interno", timeout=60.0
        )

    async def pedir(self, metodo, ruta, *, params=None, cuerpo=None):
        encabezados = {}
        token = get_access_token()
        if token is not None:
            encabezados["Authorization"] = "Bearer " + token.token
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            respuesta = await self._http.request(metodo, ruta, params=params, json=cuerpo, headers=encabezados)
        except httpx2.HTTPError as error:
            raise ToolError("No se pudo consultar Eco: %s" % error) from error
        if respuesta.status_code >= 400:
            raise ToolError(_mensaje_de_error(respuesta))
        return respuesta.json()

    async def leer(self, ruta, **params):
        return await self.pedir("GET", ruta, params=params)

    async def crear(self, ruta, cuerpo):
        return await self.pedir("POST", ruta, cuerpo=cuerpo)


def _mensaje_de_error(respuesta):
    try:
        detalle = respuesta.json().get("detail")
    except ValueError:
        detalle = None
    if isinstance(detalle, list):  # error de validacion de FastAPI
        detalle = "; ".join(
            "%s: %s" % (".".join(str(p) for p in e.get("loc", [])[1:]) or "dato", e.get("msg", "no valido"))
            for e in detalle
        )
    if respuesta.status_code in (401, 403):
        return "Eco rechazó la sesión (%d). Vuelve a conectar el conector." % respuesta.status_code
    return "Eco no pudo completar la operación (%d): %s" % (respuesta.status_code, detalle or "sin detalle")
