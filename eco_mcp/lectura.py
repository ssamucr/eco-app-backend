"""Herramientas de solo lectura: consultan la API tal cual; los calculos los hace el servidor de Eco."""

from datetime import date
from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from pydantic import Field

from .nombres import elegir

SOLO_LECTURA = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

Fecha = Annotated[Optional[str], Field(description="Fecha en formato AAAA-MM-DD.")]


def fecha_valida(valor, etiqueta):
    if valor is None:
        return None
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        raise ToolError("%s debe tener el formato AAAA-MM-DD (por ejemplo 2026-11-05)." % etiqueta)


def registrar(mcp, api):
    @mcp.tool(annotations=SOLO_LECTURA)
    async def resumen():
        """Panorama general de las finanzas: ciclo actual, ingresos y gastos del periodo (con variación frente al
        anterior), ahorro, deuda de tarjetas, patrimonio neto, saldos por cuenta, gastos por categoría, actividad
        reciente y lo que te deben o debes."""
        return await api.leer("/resumen")

    @mcp.tool(annotations=SOLO_LECTURA)
    async def cuentas():
        """Cuentas de ahorro/corriente/efectivo con sus subcuentas (saldo, meta y lo que queda sin asignar) y tarjetas
        de crédito (deuda, límite, disponible y % de uso). Trae los ids de cuentas y subcuentas."""
        return await api.leer("/cuentas-detalle")

    @mcp.tool(annotations=SOLO_LECTURA)
    async def movimientos(
        desde: Fecha = None,
        hasta: Fecha = None,
        categoria: Annotated[Optional[str], Field(description="Nombre de la categoría para filtrar.")] = None,
        limite: Annotated[int, Field(ge=1, le=200, description="Cuántos movimientos traer (máx. 200).")] = 30,
        pagina: Annotated[int, Field(ge=0, description="Página, empezando en 0.")] = 0,
    ):
        """Movimientos (ingresos, gastos, transferencias y pagos de tarjeta) del más reciente al más antiguo, con
        cuentas y categoría ya resueltas. Se pueden filtrar por fechas y por categoría. Devuelve también el total."""
        id_categoria = None
        if categoria:
            lista = await api.leer("/categorias")
            id_categoria = elegir(categoria, [(c["id_categoria"], c["nombre"]) for c in lista], "las categorías")
        return await api.leer(
            "/movimientos-detalle",
            desde=fecha_valida(desde, "desde"), hasta=fecha_valida(hasta, "hasta"),
            id_categoria=id_categoria, limite=limite, pagina=pagina,
        )

    @mcp.tool(annotations=SOLO_LECTURA)
    async def analitica(
        id_ciclo: Annotated[Optional[int], Field(description="Id de un ciclo (de la herramienta `ciclos`). Si se omite, el actual.")] = None,
    ):
        """Análisis de un ciclo: historial de ciclos, promedios, tasa de ahorro, cobertura de días con tu ahorro, gastos
        por categoría y cuenta, gasto hormiga, metas con fecha estimada y hallazgos explicados. Todo calculado en el
        servidor."""
        return await api.leer("/analitica", id_ciclo=id_ciclo)

    @mcp.tool(annotations=SOLO_LECTURA)
    async def ciclos(limite: Annotated[int, Field(ge=1, le=60)] = 12):
        """Ciclos de pago (fecha de inicio y fin) de la configuración principal, del más reciente al más antiguo, con
        el ciclo actual marcado. Sirve para conocer los ids que acepta `analitica`."""
        return await api.leer("/ciclos", limite=limite)

    @mcp.tool(annotations=SOLO_LECTURA)
    async def obligaciones():
        """Obligaciones con personas: lo que te deben, lo que debes, reposiciones pendientes y las ya liquidadas, con
        monto, pendiente y concepto. Trae los ids que usan `liquidar_obligacion`."""
        return await api.leer("/obligaciones-detalle")

    @mcp.tool(annotations=SOLO_LECTURA)
    async def planes_recurrentes():
        """Planes recurrentes (por ejemplo el reparto del sueldo): nombre, si están activos y cuántos destinos tienen."""
        return await api.leer("/planes-detalle")

    @mcp.tool(annotations=SOLO_LECTURA)
    async def financiamientos():
        """Compras a cuotas: monto, cuotas, pagadas y próximas fechas de vencimiento."""
        return await api.leer("/financiamientos-detalle")

    @mcp.tool(annotations=SOLO_LECTURA)
    async def catalogos():
        """Listas para identificar nombres e ids: cuentas (con subcuentas), categorías y personas."""
        datos = await api.leer("/movimientos-opciones")
        datos.pop("transacciones_recientes", None)
        return datos
