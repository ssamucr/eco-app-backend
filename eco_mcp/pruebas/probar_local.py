"""Prueba de humo del MCP contra un servidor LOCAL (copia desechable de la base, nunca produccion).

    ECO_MCP_SIN_AUTENTICACION=1 ECO_AUTH_DESACTIVADA=1 ECO_MCP_URL_PUBLICA=http://localhost:8000 \
        uvicorn eco_mcp.asgi:app --port 8000
    python -m eco_mcp.pruebas.probar_local

Registra un ingreso de prueba, comprueba que no se duplica y lo borra por la API para dejar la copia como estaba.
"""

import asyncio
import json
import sys
import urllib.request

from fastmcp import Client
from fastmcp.exceptions import ToolError

BASE = "http://localhost:8000"
fallos = []


def comprobar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


def api(metodo, ruta):
    peticion = urllib.request.Request(BASE + ruta, method=metodo)
    with urllib.request.urlopen(peticion, timeout=30) as r:
        return json.load(r)


def datos(resultado):
    if resultado.data is not None:
        return resultado.data
    return json.loads(resultado.content[0].text) if resultado.content else None


def estado():
    return {
        "transacciones": sorted(m["id_transaccion"] for m in api("GET", "/movimientos-detalle?limite=200")["movimientos"]),
        "mov_sub": sorted(m["id_movimiento_subcuenta"] for m in api("GET", "/movimientos-subcuenta-detalle?limite=200")["movimientos"]),
        "obligaciones": sorted(o["id_obligacion"] for o in api("GET", "/obligaciones")),
        "liquidaciones": sorted(l["id_liquidacion"] for l in api("GET", "/liquidaciones")),
    }


def restaurar(antes):
    ahora = estado()
    for clave, ruta in (("liquidaciones", "/liquidaciones/%d"), ("obligaciones", "/obligaciones/%d"),
                        ("mov_sub", "/movimientos-subcuenta/%d"), ("transacciones", "/transacciones/%d")):
        for id_ in sorted(set(ahora[clave]) - set(antes[clave]), reverse=True):
            try:
                api("DELETE", ruta % id_)
            except Exception as e:  # puede haberse borrado en cascada
                print("  (aviso) no se pudo borrar", ruta % id_, e)


async def probar_resto(cliente):
    cuentas = datos(await cliente.call_tool("cuentas", {}))
    ahorro = cuentas["ahorro"]["cuentas"]
    tarjeta = cuentas["tarjetas"]["cuentas"][0]["nombre"]
    con_sub = next(c for c in ahorro if any(x["saldo"] > 0 for x in c["subcuentas"]))
    sub = next(x for x in con_sub["subcuentas"] if x["saldo"] > 0)
    otra = next(c for c in ahorro if c["nombre"] != con_sub["nombre"] and (c["sin_asignar"] or 0) > 1)
    persona = datos(await cliente.call_tool("catalogos", {}))["personas"][0]["nombre"]
    fecha = "2026-01-02"

    r = datos(await cliente.call_tool("registrar_gasto", {
        "cuenta": con_sub["nombre"], "monto": 0.01, "descripcion": "prueba gasto", "fecha": fecha,
        "subcuenta": sub["nombre"], "confirmar": True}))
    comprobar(r["estado"] == "registrado", "gasto desde subcuenta registrado")
    comprobar(len(estado()["mov_sub"]) > 0, "creo su movimiento de subcuenta")

    r = datos(await cliente.call_tool("registrar_gasto", {
        "cuenta": tarjeta, "monto": 0.02, "descripcion": "prueba gasto tarjeta", "fecha": fecha, "confirmar": True}))
    comprobar(r["estado"] == "registrado", "gasto con tarjeta registrado")

    antes_ob = set(estado()["obligaciones"])
    v = datos(await cliente.call_tool("registrar_gasto", {
        "cuenta": tarjeta, "monto": 0.03, "descripcion": "prueba reposicion", "fecha": fecha, "generar_reposicion": True}))
    comprobar(v["estado"] == "vista_previa" and "reposición" in v["se_registrara"], "gasto con reposicion: vista previa")
    comprobar(set(estado()["obligaciones"]) == antes_ob, "la vista previa no crea la obligacion")
    r = datos(await cliente.call_tool("registrar_gasto", {
        "cuenta": tarjeta, "monto": 0.03, "descripcion": "prueba reposicion", "fecha": fecha,
        "generar_reposicion": True, "confirmar": True}))
    comprobar(r["estado"] == "registrado", "gasto con reposicion registrado")
    nuevas = [o for o in api("GET", "/obligaciones-detalle")["reposiciones"] if o["id_obligacion"] not in antes_ob]
    comprobar(
        len(nuevas) == 1 and nuevas[0]["tipo"] == "REPOSICION" and abs(nuevas[0]["monto"] - 0.03) < 0.001
        and nuevas[0]["cuenta_destino"] == tarjeta and nuevas[0]["id_transaccion_origen"] == r["id_transaccion"]
        and nuevas[0]["concepto"] == "prueba reposicion",
        "creo la obligacion de reposicion hacia la tarjeta, ligada al gasto",
    )
    try:
        await cliente.call_tool("registrar_gasto", {
            "cuenta": otra["nombre"], "monto": 0.03, "fecha": fecha, "generar_reposicion": True, "confirmar": True})
        comprobar(False, "reposicion en cuenta que no es tarjeta debe rechazarse")
    except ToolError:
        comprobar(True, "reposicion en cuenta que no es tarjeta rechazada")

    r = datos(await cliente.call_tool("registrar_transferencia", {
        "cuenta_origen": con_sub["nombre"], "cuenta_destino": otra["nombre"], "monto": 0.01,
        "subcuenta_origen": sub["nombre"], "descripcion": "prueba transf", "fecha": fecha, "confirmar": True}))
    comprobar(r["estado"] == "registrado", "transferencia registrada")
    try:
        await cliente.call_tool("registrar_transferencia", {
            "cuenta_origen": otra["nombre"], "cuenta_destino": otra["nombre"], "monto": 1})
        comprobar(False, "origen = destino debe rechazarse")
    except ToolError:
        comprobar(True, "transferencia a la misma cuenta rechazada")
    try:
        await cliente.call_tool("registrar_transferencia", {
            "cuenta_origen": tarjeta, "cuenta_destino": otra["nombre"], "monto": 1})
        comprobar(False, "origen tarjeta debe rechazarse")
    except ToolError:
        comprobar(True, "transferencia desde tarjeta rechazada (usa gasto)")

    r = datos(await cliente.call_tool("pagar_tarjeta", {
        "tarjeta": tarjeta, "cuenta_origen": otra["nombre"], "monto": 0.01, "fecha": fecha,
        "descripcion": "prueba pago", "confirmar": True}))
    comprobar(r["estado"] == "registrado", "pago de tarjeta registrado")

    v = datos(await cliente.call_tool("crear_obligacion", {
        "tipo": "POR_COBRAR", "persona": persona, "monto": 0.05, "concepto": "prueba obligacion",
        "cuenta_de_liquidacion": otra["nombre"], "fecha": fecha}))
    comprobar(v["estado"] == "vista_previa", "obligacion: vista previa")
    r = datos(await cliente.call_tool("crear_obligacion", {
        "tipo": "POR_COBRAR", "persona": persona, "monto": 0.05, "concepto": "prueba obligacion",
        "cuenta_de_liquidacion": otra["nombre"], "fecha": fecha, "confirmar": True}))
    comprobar(r["estado"] == "registrado" and r["id_obligacion"], "obligacion registrada")
    ido = r["id_obligacion"]

    try:
        await cliente.call_tool("liquidar_obligacion", {"id_obligacion": ido, "monto": 9, "cuenta": otra["nombre"]})
        comprobar(False, "liquidar de mas debe rechazarse")
    except ToolError as e:
        comprobar("pendientes" in str(e), "liquidar mas de lo pendiente -> rechazado")
    v = datos(await cliente.call_tool("liquidar_obligacion", {
        "id_obligacion": ido, "monto": 0.02, "cuenta": otra["nombre"], "fecha": fecha}))
    comprobar(v["estado"] == "vista_previa" and "0.03" in v["se_registrara"], "liquidacion: vista previa con el pendiente")
    r = datos(await cliente.call_tool("liquidar_obligacion", {
        "id_obligacion": ido, "monto": 0.02, "cuenta": otra["nombre"], "fecha": fecha, "confirmar": True}))
    comprobar(r["estado"] == "registrado", "liquidacion registrada")
    try:
        await cliente.call_tool("liquidar_obligacion", {"id_obligacion": 99999999, "monto": 1, "cuenta": otra["nombre"]})
        comprobar(False, "obligacion inexistente debe rechazarse")
    except ToolError:
        comprobar(True, "obligacion inexistente rechazada")


async def main():
    assert BASE.startswith("http://localhost"), "solo contra un servidor local"
    async with Client(BASE + "/mcp") as cliente:
        herramientas = {t.name: t for t in await cliente.list_tools()}
        print("Herramientas:", ", ".join(sorted(herramientas)))
        lectura = ["resumen", "cuentas", "movimientos", "analitica", "ciclos", "obligaciones",
                   "planes_recurrentes", "financiamientos", "catalogos"]
        escritura = ["registrar_gasto", "registrar_ingreso", "registrar_transferencia", "pagar_tarjeta",
                     "crear_obligacion", "liquidar_obligacion"]
        comprobar(set(lectura + escritura) == set(herramientas), "estan exactamente las 15 herramientas previstas")
        comprobar(all(herramientas[n].annotations.read_only_hint for n in lectura), "las de lectura son readOnly")
        comprobar(all(not herramientas[n].annotations.read_only_hint and not herramientas[n].annotations.destructive_hint
                      for n in escritura), "las de escritura no son readOnly ni destructivas")

        print("Lectura")
        for nombre in lectura:
            r = await cliente.call_tool(nombre, {})
            vacio_valido = nombre in ("financiamientos",)  # sin cuotas registradas la lista es []
            comprobar(not r.is_error and (vacio_valido or bool(datos(r))), "%s responde bien" % nombre)
        r = await cliente.call_tool("movimientos", {"limite": 3, "desde": "2020-01-01"})
        comprobar(len(datos(r)["movimientos"]) == 3, "movimientos respeta el limite")
        try:
            await cliente.call_tool("movimientos", {"desde": "ayer"})
            comprobar(False, "fecha mala debe rechazarse")
        except ToolError as e:
            comprobar("AAAA-MM-DD" in str(e), "fecha mala -> error claro")

        print("Escritura")
        antes = api("GET", "/movimientos-detalle?limite=1")["total"]
        cuentas = datos(await cliente.call_tool("cuentas", {}))["ahorro"]["cuentas"]
        cuenta = cuentas[0]["nombre"]
        args = {"cuenta": cuenta, "monto": 0.01, "descripcion": "prueba mcp", "fecha": "2026-01-02"}

        vista = datos(await cliente.call_tool("registrar_ingreso", args))
        comprobar(vista["estado"] == "vista_previa" and "se_registrara" in vista, "sin confirmar: vista previa")
        comprobar(api("GET", "/movimientos-detalle?limite=1")["total"] == antes, "la vista previa no guarda nada")

        try:
            await cliente.call_tool("registrar_ingreso", {**args, "cuenta": "no existe xyz"})
            comprobar(False, "cuenta inexistente debe rechazarse")
        except ToolError as e:
            comprobar("Opciones" in str(e), "cuenta inexistente -> lista las opciones")
        try:
            await cliente.call_tool("registrar_ingreso", {**args, "monto": -5})
            comprobar(False, "monto negativo debe rechazarse")
        except ToolError:
            comprobar(True, "monto negativo rechazado")

        id_creado = None
        try:
            hecho = datos(await cliente.call_tool("registrar_ingreso", {**args, "confirmar": True}))
            id_creado = hecho.get("id_transaccion")
            comprobar(hecho["estado"] == "registrado" and id_creado, "con confirmar: registrado (id %s)" % id_creado)
            comprobar(api("GET", "/movimientos-detalle?limite=1")["total"] == antes + 1, "ahora hay un movimiento mas")
            try:
                await cliente.call_tool("registrar_ingreso", {**args, "confirmar": True})
                comprobar(False, "el duplicado debe rechazarse")
            except ToolError as e:
                comprobar("idéntico" in str(e), "reintento identico -> rechazado (sin duplicar)")
            vista2 = datos(await cliente.call_tool("registrar_ingreso", args))
            comprobar("advertencia" in vista2, "la vista previa avisa del duplicado")
        finally:
            if id_creado:
                api("DELETE", "/transacciones/%d" % id_creado)
        comprobar(api("GET", "/movimientos-detalle?limite=1")["total"] == antes, "copia local restaurada")

        print("Resto de escrituras (se limpian al final)")
        instantanea = estado()
        try:
            await probar_resto(cliente)
        finally:
            restaurar(instantanea)
        comprobar(estado() == instantanea, "copia local restaurada tras todas las pruebas")

    print("\n%s" % ("TODO BIEN" if not fallos else "FALLOS: %d" % len(fallos)))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
