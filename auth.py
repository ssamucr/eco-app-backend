"""Autenticacion: la API solo atiende peticiones con un token de Supabase Auth de un usuario permitido.

Configuracion (variables de entorno):
  SUPABASE_URL              https://<proyecto>.supabase.co  (de ahi sale la clave publica que verifica la firma)
  ECO_USUARIOS_PERMITIDOS   correos (o ids de usuario) separados por coma que pueden entrar
  SUPABASE_JWT_SECRET       solo si el proyecto aun firma con HS256 (secreto heredado); lo normal es no definirlo
  ECO_AUTH_DESACTIVADA=1    SOLO para desarrollo local: no pide token

Si falta la configuracion y la autenticacion no esta desactivada, se rechaza todo (falla cerrado).
"""
import os

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

RUTAS_PUBLICAS = {"/health"}  # sin datos: solo dice si la API responde


def _lista(valor):
    return {x.strip().lower() for x in (valor or "").split(",") if x.strip()}


def _config():
    return {
        "desactivada": os.environ.get("ECO_AUTH_DESACTIVADA") == "1",
        "url": (os.environ.get("SUPABASE_URL") or "").rstrip("/"),
        "permitidos": _lista(os.environ.get("ECO_USUARIOS_PERMITIDOS")),
        "secreto": os.environ.get("SUPABASE_JWT_SECRET"),
    }


_clientes = {}


def _cliente_jwks(url):
    if url not in _clientes:
        _clientes[url] = PyJWKClient(url + "/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _clientes[url]


def _no_autorizado(mensaje):
    return HTTPException(status_code=401, detail=mensaje, headers={"WWW-Authenticate": "Bearer"})


def _verificar(token, cfg):
    emisor = cfg["url"] + "/auth/v1"
    try:
        algoritmo = jwt.get_unverified_header(token).get("alg")
        if algoritmo == "HS256":
            if not cfg["secreto"]:
                raise _no_autorizado("Token no valido.")
            clave, algoritmos = cfg["secreto"], ["HS256"]
        elif algoritmo in ("ES256", "RS256"):
            clave, algoritmos = _cliente_jwks(cfg["url"]).get_signing_key_from_jwt(token).key, [algoritmo]
        else:
            raise _no_autorizado("Token no valido.")
        return jwt.decode(token, clave, algorithms=algoritmos, audience="authenticated", issuer=emisor,
                          options={"require": ["exp", "sub"]})
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="No se pudo verificar la sesión: intenta de nuevo.")
    except jwt.ExpiredSignatureError:
        raise _no_autorizado("La sesión expiró: vuelve a iniciar sesión.")
    except (jwt.PyJWTError, ValueError):
        raise _no_autorizado("Token no valido.")


def exigir_usuario(request: Request):
    """Dependencia global: toda ruta (salvo /health y las peticiones OPTIONS de CORS) exige un usuario permitido."""
    if request.method == "OPTIONS" or request.url.path in RUTAS_PUBLICAS:
        return None
    cfg = _config()
    if cfg["desactivada"]:
        return None
    if not cfg["url"] or not cfg["permitidos"]:
        raise HTTPException(status_code=503, detail="La autenticación no está configurada en el servidor.")
    autorizacion = request.headers.get("Authorization", "")
    esquema, _, token = autorizacion.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise _no_autorizado("Inicia sesión para continuar.")
    datos = _verificar(token.strip(), cfg)
    identidades = {str(datos.get("email") or "").lower(), str(datos.get("sub") or "").lower()}
    if not (identidades & cfg["permitidos"]):
        raise HTTPException(status_code=403, detail="Este usuario no tiene acceso.")
    return datos
