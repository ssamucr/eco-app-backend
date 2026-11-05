"""Verificacion del token que Claude presenta al MCP.

Supabase Auth (servidor OAuth 2.1) emite el token cuando el usuario autoriza el conector. Aqui solo se comprueba:
firma (JWKS), emisor, audiencia y vencimiento (lo hace JWTVerifier) y, ademas:
  - que sea un token del flujo OAuth (lleva `client_id`): el token de la sesion normal de la web no sirve en el MCP;
  - que el usuario este en ECO_USUARIOS_PERMITIDOS (por correo o por id).
"""

import logging

from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.supabase import SupabaseProvider

registro = logging.getLogger("eco_mcp.seguridad")


class VerificadorEco(JWTVerifier):
    def __init__(self, usuarios_permitidos, **opciones):
        super().__init__(**opciones)
        self._usuarios = {u.lower() for u in usuarios_permitidos}

    async def load_access_token(self, token):
        acceso = await super().load_access_token(token)
        if acceso is None:
            return None
        return acceso if self._autorizado(acceso.claims or {}) else None

    def _autorizado(self, claims):
        if not claims.get("client_id"):
            registro.warning("Token rechazado: no es un token del flujo OAuth (sin client_id).")
            return False
        identidades = {str(claims.get("email") or "").lower(), str(claims.get("sub") or "").lower()} - {""}
        if not identidades & self._usuarios:
            registro.warning("Token rechazado: usuario fuera de la lista permitida.")
            return False
        return True


def crear_proveedor(config):
    """Proveedor de autenticacion del MCP (None solo en pruebas locales sin login)."""
    if config.sin_autenticacion:
        return None
    emisor = config.supabase_url + "/auth/v1"
    verificador = VerificadorEco(
        config.usuarios,
        jwks_uri=emisor + "/.well-known/jwks.json",
        issuer=emisor,
        algorithm="ES256",
        audience="authenticated",
    )
    return SupabaseProvider(project_url=config.supabase_url, base_url=config.url_publica, token_verifier=verificador)
