"""Pruebas de seguridad del MCP (sin tocar datos).

1. Verificador de tokens: firma, emisor, audiencia, vencimiento, client_id y lista de usuarios (claves generadas aqui).
2. Servidor con login activado (opcional): si se define PROBAR_URL, comprueba que /mcp exige token y que la API
   sigue exigiendolo.

    python -m eco_mcp.pruebas.probar_seguridad
    PROBAR_URL=http://localhost:8002 python -m eco_mcp.pruebas.probar_seguridad
"""

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from eco_mcp.seguridad import VerificadorEco

fallos = []
EMISOR = "https://proyecto.supabase.co/auth/v1"


def comprobar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


def claves():
    privada = ec.generate_private_key(ec.SECP256R1())
    publica = privada.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return privada, publica


async def probar_verificador():
    print("Verificador de tokens")
    privada, publica = claves()
    otra_privada, _ = claves()
    verificador = VerificadorEco(
        ["dueno@example.com"], public_key=publica, issuer=EMISOR, algorithm="ES256", audience="authenticated"
    )
    base = {"iss": EMISOR, "aud": "authenticated", "sub": "u-1", "email": "dueno@example.com",
            "client_id": "cliente-claude", "exp": int(time.time()) + 600}

    def firmar(**cambios):
        claims = {**base, **cambios}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, privada, algorithm="ES256", headers={"kid": "k1"})

    comprobar(await verificador.verify_token(firmar()) is not None, "token OAuth valido del dueno: aceptado")
    comprobar(await verificador.verify_token(firmar(email="DUENO@example.com")) is not None, "correo en mayusculas: aceptado")
    comprobar(await verificador.verify_token(firmar(email="otro@example.com", sub="u-9")) is None, "otro usuario: rechazado")
    comprobar(await verificador.verify_token(firmar(email=None, sub="dueno@example.com")) is not None, "identificado por sub: aceptado")
    comprobar(await verificador.verify_token(firmar(client_id=None)) is None, "sin client_id (token de sesion web): rechazado")
    comprobar(await verificador.verify_token(firmar(exp=int(time.time()) - 5)) is None, "vencido: rechazado")
    comprobar(await verificador.verify_token(firmar(iss="https://otro.supabase.co/auth/v1")) is None, "otro emisor: rechazado")
    comprobar(await verificador.verify_token(firmar(aud="otra")) is None, "otra audiencia: rechazado")
    falso = jwt.encode(base, otra_privada, algorithm="ES256", headers={"kid": "k1"})
    comprobar(await verificador.verify_token(falso) is None, "firma de otra clave: rechazado")
    sin_firma = jwt.encode(base, key=None, algorithm="none")
    comprobar(await verificador.verify_token(sin_firma) is None, "alg=none: rechazado")
    hs = jwt.encode(base, "secreto-de-prueba-secreto-de-prueba", algorithm="HS256")
    comprobar(await verificador.verify_token(hs) is None, "HS256 (confusion de algoritmo): rechazado")
    comprobar(await verificador.verify_token("basura") is None, "texto que no es un token: rechazado")


def pedir(url, metodo="GET", cuerpo=None, token=None):
    cabeceras = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = "Bearer " + token
    peticion = urllib.request.Request(url, method=metodo, headers=cabeceras,
                                      data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def probar_servidor(base):
    print("Servidor con login activado (%s)" % base)
    inicializar = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "prueba", "version": "0"}}}
    estado, cabeceras, _ = pedir(base + "/mcp", "POST", inicializar)
    comprobar(estado == 401, "/mcp sin token: 401")
    comprobar("resource_metadata" in cabeceras.get("WWW-Authenticate", cabeceras.get("www-authenticate", "")),
              "el 401 indica donde descubrir la autenticacion (resource_metadata)")
    estado, _, _ = pedir(base + "/mcp", "POST", inicializar, token="basura")
    comprobar(estado == 401, "/mcp con token falso: 401")

    estado, _, cuerpo = pedir(base + "/.well-known/oauth-protected-resource/mcp")
    meta = json.loads(cuerpo) if estado == 200 else {}
    comprobar(estado == 200 and meta.get("resource", "").endswith("/mcp"), "metadata del recurso protegido publicada")
    comprobar(any(a.rstrip("/").endswith("/auth/v1") for a in meta.get("authorization_servers", [])),
              "apunta a Supabase como servidor de autorizacion")

    estado, _, _ = pedir(base + "/cuentas")
    comprobar(estado == 401, "la API de siempre sigue exigiendo token")
    estado, _, _ = pedir(base + "/cuentas", token="basura")
    comprobar(estado == 401, "la API rechaza un token falso")
    estado, _, cuerpo = pedir(base + "/health")
    comprobar(estado == 200, "/health sigue publico")
    estado, _, _ = pedir(base + "/docs")
    comprobar(estado == 404, "/docs sigue apagado")


if __name__ == "__main__":
    asyncio.run(probar_verificador())
    if os.environ.get("PROBAR_URL"):
        probar_servidor(os.environ["PROBAR_URL"].rstrip("/"))
    print("\n%s" % ("TODO BIEN" if not fallos else "FALLOS: %d" % len(fallos)))
    sys.exit(1 if fallos else 0)
