"""Herramientas que registran datos. Todas trabajan en dos pasos:

1. Sin `confirmar`: solo devuelven una vista previa de lo que se guardaria (nada se escribe).
2. Con `confirmar=true`: llaman a la API, que valida las reglas de negocio y guarda.

No hay herramientas para borrar ni editar: lo ya registrado solo se cambia desde la aplicacion.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from pydantic import Field

from .lectura import fecha_valida
from .nombres import elegir, normalizar

auditoria = logging.getLogger("eco_mcp.auditoria")

ESCRITURA = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

Monto = Annotated[float, Field(gt=0, description="Monto en dólares, mayor que 0.")]
Descripcion = Annotated[Optional[str], Field(description="Descripción corta (hasta 100 caracteres).", max_length=100)]
Confirmar = Annotated[
    bool,
    Field(
        description="false (por defecto): solo muestra la vista previa. true: registra de verdad. Pásalo en true "
        "únicamente después de que el usuario haya visto la vista previa y la haya aprobado."
    ),
]
Duplicado = Annotated[
    bool, Field(description="true solo si el usuario confirma que quiere repetir un movimiento idéntico a uno ya registrado.")
]


def _dolares(valor):
    return "${:,.2f}".format(valor)


def registrar(mcp, api, config):
    def hoy():
        return datetime.now(timezone(timedelta(hours=config.zona_utc))).date().isoformat()

    async def catalogos():
        datos = await api.leer("/movimientos-opciones")
        return datos["cuentas"], datos["categorias"], datos["personas"]

    def cuenta_por(valor, cuentas, que="las cuentas", tipos=None, excluir_tipos=None):
        candidatas = [
            (c["id_cuenta"], c["nombre"]) for c in cuentas
            if (tipos is None or c["tipo"] in tipos) and (excluir_tipos is None or c["tipo"] not in excluir_tipos)
        ]
        id_ = elegir(valor, candidatas, que)
        return next(c for c in cuentas if c["id_cuenta"] == id_)

    def categoria_por(valor, categorias):
        if not valor:
            return None
        return elegir(valor, [(c["id_categoria"], c["nombre"]) for c in categorias], "las categorías")

    def tomar_de_subcuenta(cuerpo, origen, subcuenta, monto, descripcion):
        """Agrega al cuerpo el movimiento de subcuenta que respalda un gasto que sale de una subcuenta; devuelve su nombre."""
        if origen["tipo"] == "TARJETA_CREDITO":
            raise ToolError("Una tarjeta de crédito no tiene subcuentas.")
        id_sub = elegir(subcuenta, [(x["id_subcuenta"], x["nombre"]) for x in origen["subcuentas"]],
                        "las subcuentas de «%s»" % origen["nombre"])
        cuerpo["movimientos_subcuenta"] = [
            {"tipo": "GASTO", "monto": monto, "id_subcuenta_origen": id_sub, "descripcion": descripcion}
        ]
        return next(x["nombre"] for x in origen["subcuentas"] if x["id_subcuenta"] == id_sub)

    async def repetido(cuerpo, origen, destino):
        """Movimiento ya registrado con los mismos datos el mismo dia (para no duplicar por un reintento)."""
        dia = cuerpo["fecha"]
        existentes = await api.leer("/movimientos-detalle", desde=dia, hasta=dia, limite=200)
        for m in existentes["movimientos"]:
            if (
                m["tipo"] == cuerpo["tipo"] and abs(float(m["monto"]) - float(cuerpo["monto"])) < 0.005
                and (m["cuenta_origen"] or None) == (origen["nombre"] if origen else None)
                and (m["cuenta_destino"] or None) == (destino["nombre"] if destino else None)
                and normalizar(m["descripcion"] or "") == normalizar(cuerpo.get("descripcion") or "")
            ):
                return m
        return None

    def anotar(accion, detalle):
        token = get_access_token()
        claims = (token.claims if token else None) or {}
        auditoria.info("%s | usuario=%s cliente=%s | %s", accion, claims.get("email") or claims.get("sub"),
                       claims.get("client_id"), detalle)

    async def transaccion(cuerpo, origen, destino, texto, confirmar, permitir_duplicado, extra=None):
        igual = None if permitir_duplicado else await repetido(cuerpo, origen, destino)
        if not confirmar:
            vista = {
                "estado": "vista_previa",
                "se_registrara": texto,
                "saldos_actuales": {c["nombre"]: c["saldo"] for c in (origen, destino) if c},
                "siguiente_paso": "Muestra este resumen al usuario. Si lo aprueba, repite la llamada con confirmar=true.",
            }
            if igual:
                vista["advertencia"] = (
                    "Ya hay un movimiento idéntico ese día (id %d). Pregunta al usuario si es otro distinto; "
                    "si lo es, usa permitir_duplicado=true." % igual["id_transaccion"]
                )
            if extra:
                vista.update(extra)
            return vista
        if igual:
            raise ToolError(
                "Ya existe un movimiento idéntico ese día (id %d). No lo registré para evitar duplicados; si de verdad "
                "es otro, repite con permitir_duplicado=true." % igual["id_transaccion"]
            )
        creada = await api.crear("/transacciones", cuerpo)
        anotar("transaccion", "id=%s %s" % (creada.get("id_transaccion"), texto))
        return {"estado": "registrado", "id_transaccion": creada.get("id_transaccion"), "registrado": texto}

    @mcp.tool(annotations=ESCRITURA)
    async def registrar_gasto(
        cuenta: Annotated[str, Field(description="Cuenta o tarjeta de la que sale el gasto (nombre o id).")],
        monto: Monto,
        descripcion: Descripcion = None,
        categoria: Annotated[Optional[str], Field(description="Categoría (nombre).")] = None,
        fecha: Annotated[Optional[str], Field(description="AAAA-MM-DD. Si se omite, hoy.")] = None,
        subcuenta: Annotated[
            Optional[str],
            Field(description="Subcuenta de esa cuenta de la que se toma el dinero. Si se omite, sale de lo sin asignar."),
        ] = None,
        confirmar: Confirmar = False,
        permitir_duplicado: Duplicado = False,
    ):
        """Registra un gasto. Primero devuelve una vista previa; solo guarda con confirmar=true."""
        cuentas, categorias, _ = await catalogos()
        origen = cuenta_por(cuenta, cuentas)
        cuerpo = {
            "fecha": fecha_valida(fecha, "fecha") or hoy(), "tipo": "GASTO", "monto": monto,
            "id_cuenta_origen": origen["id_cuenta"], "id_categoria": categoria_por(categoria, categorias),
            "descripcion": descripcion,
        }
        texto = "Gasto de %s desde «%s» el %s" % (_dolares(monto), origen["nombre"], cuerpo["fecha"])
        if subcuenta:
            texto += ", tomado de la subcuenta «%s»" % tomar_de_subcuenta(cuerpo, origen, subcuenta, monto, descripcion)
        if categoria:
            texto += ", categoría «%s»" % next(c["nombre"] for c in categorias if c["id_categoria"] == cuerpo["id_categoria"])
        if descripcion:
            texto += " — «%s»" % descripcion
        return await transaccion(cuerpo, origen, None, texto, confirmar, permitir_duplicado)

    @mcp.tool(annotations=ESCRITURA)
    async def registrar_ingreso(
        cuenta: Annotated[str, Field(description="Cuenta que recibe el dinero (nombre o id).")],
        monto: Monto,
        descripcion: Descripcion = None,
        categoria: Annotated[Optional[str], Field(description="Categoría (nombre).")] = None,
        fecha: Annotated[Optional[str], Field(description="AAAA-MM-DD. Si se omite, hoy.")] = None,
        confirmar: Confirmar = False,
        permitir_duplicado: Duplicado = False,
    ):
        """Registra un ingreso (sueldo, pago recibido…). Primero devuelve una vista previa; solo guarda con confirmar=true."""
        cuentas, categorias, _ = await catalogos()
        destino = cuenta_por(cuenta, cuentas)
        cuerpo = {
            "fecha": fecha_valida(fecha, "fecha") or hoy(), "tipo": "INGRESO", "monto": monto,
            "id_cuenta_destino": destino["id_cuenta"], "id_categoria": categoria_por(categoria, categorias),
            "descripcion": descripcion,
        }
        texto = "Ingreso de %s a «%s» el %s" % (_dolares(monto), destino["nombre"], cuerpo["fecha"])
        if descripcion:
            texto += " — «%s»" % descripcion
        return await transaccion(cuerpo, None, destino, texto, confirmar, permitir_duplicado)

    @mcp.tool(annotations=ESCRITURA)
    async def registrar_transferencia(
        cuenta_origen: Annotated[str, Field(description="Cuenta de la que sale el dinero (nombre o id).")],
        cuenta_destino: Annotated[str, Field(description="Cuenta que lo recibe (nombre o id).")],
        monto: Monto,
        descripcion: Descripcion = None,
        fecha: Annotated[Optional[str], Field(description="AAAA-MM-DD. Si se omite, hoy.")] = None,
        subcuenta_origen: Annotated[
            Optional[str],
            Field(description="Subcuenta de la cuenta de origen de la que se toma el dinero (si no hay saldo sin asignar)."),
        ] = None,
        confirmar: Confirmar = False,
        permitir_duplicado: Duplicado = False,
    ):
        """Mueve dinero entre dos de tus cuentas. Para abonar a una tarjeta usa `pagar_tarjeta`. Primero devuelve una
        vista previa; solo guarda con confirmar=true."""
        cuentas, _, _ = await catalogos()
        origen = cuenta_por(cuenta_origen, cuentas, "las cuentas de origen", excluir_tipos=("TARJETA_CREDITO",))
        destino = cuenta_por(cuenta_destino, cuentas, "las cuentas de destino")
        if origen["id_cuenta"] == destino["id_cuenta"]:
            raise ToolError("La cuenta de origen y la de destino deben ser distintas.")
        cuerpo = {
            "fecha": fecha_valida(fecha, "fecha") or hoy(), "tipo": "TRANSFERENCIA", "monto": monto,
            "id_cuenta_origen": origen["id_cuenta"], "id_cuenta_destino": destino["id_cuenta"],
            "descripcion": descripcion,
        }
        texto = "Transferencia de %s de «%s» a «%s» el %s" % (_dolares(monto), origen["nombre"], destino["nombre"], cuerpo["fecha"])
        if subcuenta_origen:
            texto += ", tomado de la subcuenta «%s»" % tomar_de_subcuenta(cuerpo, origen, subcuenta_origen, monto, descripcion)
        if descripcion:
            texto += " — «%s»" % descripcion
        return await transaccion(cuerpo, origen, destino, texto, confirmar, permitir_duplicado)

    @mcp.tool(annotations=ESCRITURA)
    async def pagar_tarjeta(
        tarjeta: Annotated[str, Field(description="Tarjeta de crédito que se abona (nombre o id).")],
        monto: Monto,
        cuenta_origen: Annotated[
            Optional[str], Field(description="Cuenta de la que sale el pago (nombre o id). Puede omitirse.")
        ] = None,
        descripcion: Descripcion = None,
        fecha: Annotated[Optional[str], Field(description="AAAA-MM-DD. Si se omite, hoy.")] = None,
        subcuenta_origen: Annotated[
            Optional[str],
            Field(description="Subcuenta de la cuenta de origen de la que se toma el dinero (si no hay saldo sin asignar)."),
        ] = None,
        confirmar: Confirmar = False,
        permitir_duplicado: Duplicado = False,
    ):
        """Registra un pago a una tarjeta de crédito. Primero devuelve una vista previa; solo guarda con confirmar=true."""
        cuentas, _, _ = await catalogos()
        destino = cuenta_por(tarjeta, cuentas, "las tarjetas", tipos=("TARJETA_CREDITO",))
        origen = cuenta_por(cuenta_origen, cuentas, "las cuentas de origen", excluir_tipos=("TARJETA_CREDITO",)) if cuenta_origen else None
        cuerpo = {
            "fecha": fecha_valida(fecha, "fecha") or hoy(), "tipo": "PAGO_TARJETA", "monto": monto,
            "id_cuenta_origen": origen["id_cuenta"] if origen else None, "id_cuenta_destino": destino["id_cuenta"],
            "descripcion": descripcion,
        }
        texto = "Pago de %s a la tarjeta «%s»%s el %s" % (
            _dolares(monto), destino["nombre"], " desde «%s»" % origen["nombre"] if origen else "", cuerpo["fecha"]
        )
        if subcuenta_origen:
            if origen is None:
                raise ToolError("Para tomar el dinero de una subcuenta indica también la cuenta de origen.")
            texto += ", tomado de la subcuenta «%s»" % tomar_de_subcuenta(cuerpo, origen, subcuenta_origen, monto, descripcion)
        if descripcion:
            texto += " — «%s»" % descripcion
        return await transaccion(cuerpo, origen, destino, texto, confirmar, permitir_duplicado)

    @mcp.tool(annotations=ESCRITURA)
    async def crear_obligacion(
        tipo: Annotated[
            Literal["POR_COBRAR", "POR_PAGAR"],
            Field(description="POR_COBRAR: alguien te debe a ti. POR_PAGAR: tú le debes a alguien."),
        ],
        persona: Annotated[str, Field(description="Persona (nombre o id).")],
        monto: Monto,
        concepto: Annotated[str, Field(description="De qué es la deuda (hasta 100 caracteres).", max_length=100)],
        cuenta_de_liquidacion: Annotated[
            str, Field(description="Cuenta por donde se saldará la deuda (nombre o id).")
        ],
        subcuenta_de_liquidacion: Annotated[Optional[str], Field(description="Subcuenta de esa cuenta, si aplica.")] = None,
        fecha: Annotated[Optional[str], Field(description="AAAA-MM-DD. Si se omite, hoy.")] = None,
        confirmar: Confirmar = False,
    ):
        """Registra una obligación con una persona (lo que te deben o debes). Primero devuelve una vista previa; solo
        guarda con confirmar=true."""
        cuentas, _, personas = await catalogos()
        id_persona = elegir(persona, [(p["id_persona"], p["nombre"]) for p in personas], "las personas")
        nombre_persona = next(p["nombre"] for p in personas if p["id_persona"] == id_persona)
        cuenta = cuenta_por(cuenta_de_liquidacion, cuentas)
        id_sub = None
        if subcuenta_de_liquidacion:
            id_sub = elegir(subcuenta_de_liquidacion, [(s["id_subcuenta"], s["nombre"]) for s in cuenta["subcuentas"]],
                            "las subcuentas de «%s»" % cuenta["nombre"])
        cuerpo = {
            "fecha_creacion": fecha_valida(fecha, "fecha") or hoy(), "tipo": tipo, "monto": monto,
            "descripcion": concepto, "id_persona": id_persona,
            "id_cuenta_destino_resolucion": cuenta["id_cuenta"], "id_subcuenta_destino_resolucion": id_sub,
        }
        texto = "Obligación %s con %s por %s — «%s», a saldarse por «%s»" % (
            "por cobrar" if tipo == "POR_COBRAR" else "por pagar", nombre_persona, _dolares(monto), concepto, cuenta["nombre"]
        )
        if not confirmar:
            return {"estado": "vista_previa", "se_registrara": texto,
                    "siguiente_paso": "Muestra este resumen al usuario. Si lo aprueba, repite la llamada con confirmar=true."}
        creada = await api.crear("/obligaciones", cuerpo)
        anotar("obligacion", "id=%s %s" % (creada.get("id_obligacion"), texto))
        return {"estado": "registrado", "id_obligacion": creada.get("id_obligacion"), "registrado": texto}

    @mcp.tool(annotations=ESCRITURA)
    async def liquidar_obligacion(
        id_obligacion: Annotated[int, Field(description="Id de la obligación (de la herramienta `obligaciones`).")],
        monto: Monto,
        cuenta: Annotated[
            str,
            Field(description="Cuenta de la que sale el dinero (o, si te deben, la que lo recibe). Nombre o id."),
        ],
        subcuenta: Annotated[Optional[str], Field(description="Subcuenta de esa cuenta, si aplica.")] = None,
        descripcion: Descripcion = None,
        fecha: Annotated[Optional[str], Field(description="AAAA-MM-DD. Si se omite, hoy.")] = None,
        confirmar: Confirmar = False,
    ):
        """Registra un pago (total o parcial) de una obligación y genera los movimientos de dinero que lo respaldan.
        Primero devuelve una vista previa; solo guarda con confirmar=true."""
        cuentas, _, _ = await catalogos()
        elegida = cuenta_por(cuenta, cuentas)
        id_sub = None
        if subcuenta:
            id_sub = elegir(subcuenta, [(s["id_subcuenta"], s["nombre"]) for s in elegida["subcuentas"]],
                            "las subcuentas de «%s»" % elegida["nombre"])
        obligacion = _buscar_obligacion(await api.leer("/obligaciones-detalle"), id_obligacion)
        if obligacion is None:
            raise ToolError("No existe la obligación %d." % id_obligacion)
        if obligacion.get("resuelta"):
            raise ToolError("La obligación %d ya está liquidada por completo." % id_obligacion)
        pendiente = float(obligacion.get("monto_pendiente") or 0)
        if monto > pendiente + 0.005:
            raise ToolError("A la obligación %d solo le quedan %s pendientes." % (id_obligacion, _dolares(pendiente)))
        cuerpo = {
            "id_obligacion": id_obligacion, "fecha": fecha_valida(fecha, "fecha") or hoy(), "monto": monto,
            "id_cuenta_pago": elegida["id_cuenta"], "id_subcuenta_pago": id_sub, "descripcion": descripcion,
        }
        concepto = obligacion.get("concepto") or obligacion.get("descripcion") or "sin concepto"
        texto = "Liquidación de %s de la obligación %d («%s», %s) usando «%s»; quedarían %s pendientes" % (
            _dolares(monto), id_obligacion, concepto, obligacion.get("persona") or "sin persona", elegida["nombre"],
            _dolares(max(pendiente - monto, 0)),
        )
        if not confirmar:
            return {"estado": "vista_previa", "se_registrara": texto,
                    "siguiente_paso": "Muestra este resumen al usuario. Si lo aprueba, repite la llamada con confirmar=true."}
        creada = await api.crear("/liquidaciones", cuerpo)
        anotar("liquidacion", "id=%s %s" % (creada.get("id_liquidacion"), texto))
        return {"estado": "registrado", "id_liquidacion": creada.get("id_liquidacion"), "registrado": texto}


def _buscar_obligacion(datos, id_obligacion):
    """Busca en cualquier parte de la respuesta de /obligaciones-detalle la obligacion con ese id."""
    if isinstance(datos, dict):
        if datos.get("id_obligacion") == id_obligacion and "monto_pendiente" in datos:
            return datos
        datos = list(datos.values())
    if isinstance(datos, list):
        for elemento in datos:
            hallada = _buscar_obligacion(elemento, id_obligacion)
            if hallada is not None:
                return hallada
    return None
