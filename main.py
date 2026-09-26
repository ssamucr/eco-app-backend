from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import get_connection
from schemas import (
    Categoria,
    Cuenta,
    CuotaFinanciamiento,
    Financiamiento,
    LiquidacionObligacion,
    MovimientoSubcuenta,
    Obligacion,
    Persona,
    PlanRecurrente,
    PlanRecurrenteDestino,
    Subcuenta,
    Transaccion,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- helpers de acceso a datos ----------

def fetch_all(query, params=()):
    conexion = get_connection()
    cursor = conexion.cursor()
    cursor.execute(query, params)
    columnas = [c[0] for c in cursor.description]
    filas = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
    conexion.close()
    return filas


def fetch_one(query, params=()):
    filas = fetch_all(query, params)
    return filas[0] if filas else None


def run_returning(query, params=()):
    conexion = get_connection()
    cursor = conexion.cursor()
    cursor.execute(query, params)
    columnas = [c[0] for c in cursor.description]
    fila = cursor.fetchone()
    conexion.commit()
    conexion.close()
    return dict(zip(columnas, fila)) if fila else None


def execute(query, params=()):
    conexion = get_connection()
    cursor = conexion.cursor()
    cursor.execute(query, params)
    conexion.commit()
    conexion.close()


# ---------- personas ----------

@app.get("/personas")
def listar_personas():
    return fetch_all("SELECT * FROM personas")


@app.get("/personas/{id_persona}")
def obtener_persona(id_persona: int):
    return fetch_one("SELECT * FROM personas WHERE id_persona = ?", (id_persona,))


@app.post("/personas")
def crear_persona(persona: Persona):
    return run_returning(
        "INSERT INTO personas (nombre) OUTPUT INSERTED.* VALUES (?)",
        (persona.nombre,),
    )


@app.put("/personas/{id_persona}")
def actualizar_persona(id_persona: int, persona: Persona):
    return run_returning(
        "UPDATE personas SET nombre = ? OUTPUT INSERTED.* WHERE id_persona = ?",
        (persona.nombre, id_persona),
    )


@app.delete("/personas/{id_persona}")
def eliminar_persona(id_persona: int):
    execute("DELETE FROM personas WHERE id_persona = ?", (id_persona,))
    return {"eliminado": True}


# ---------- categorias ----------

@app.get("/categorias")
def listar_categorias():
    return fetch_all("SELECT * FROM categorias")


@app.get("/categorias/{id_categoria}")
def obtener_categoria(id_categoria: int):
    return fetch_one("SELECT * FROM categorias WHERE id_categoria = ?", (id_categoria,))


@app.post("/categorias")
def crear_categoria(categoria: Categoria):
    return run_returning(
        "INSERT INTO categorias (nombre, descripcion) OUTPUT INSERTED.* VALUES (?, ?)",
        (categoria.nombre, categoria.descripcion),
    )


@app.put("/categorias/{id_categoria}")
def actualizar_categoria(id_categoria: int, categoria: Categoria):
    return run_returning(
        "UPDATE categorias SET nombre = ?, descripcion = ? OUTPUT INSERTED.* WHERE id_categoria = ?",
        (categoria.nombre, categoria.descripcion, id_categoria),
    )


@app.delete("/categorias/{id_categoria}")
def eliminar_categoria(id_categoria: int):
    execute("DELETE FROM categorias WHERE id_categoria = ?", (id_categoria,))
    return {"eliminado": True}


# ---------- cuentas ----------

@app.get("/cuentas")
def listar_cuentas():
    return fetch_all("SELECT * FROM cuentas")


@app.get("/cuentas/{id_cuenta}")
def obtener_cuenta(id_cuenta: int):
    return fetch_one("SELECT * FROM cuentas WHERE id_cuenta = ?", (id_cuenta,))


@app.post("/cuentas")
def crear_cuenta(cuenta: Cuenta):
    return run_returning(
        "INSERT INTO cuentas (nombre, entidad, tipo, limite_credito) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?)",
        (cuenta.nombre, cuenta.entidad, cuenta.tipo, cuenta.limite_credito),
    )


@app.put("/cuentas/{id_cuenta}")
def actualizar_cuenta(id_cuenta: int, cuenta: Cuenta):
    return run_returning(
        "UPDATE cuentas SET nombre = ?, entidad = ?, tipo = ?, limite_credito = ? "
        "OUTPUT INSERTED.* WHERE id_cuenta = ?",
        (cuenta.nombre, cuenta.entidad, cuenta.tipo, cuenta.limite_credito, id_cuenta),
    )


@app.delete("/cuentas/{id_cuenta}")
def eliminar_cuenta(id_cuenta: int):
    execute("DELETE FROM cuentas WHERE id_cuenta = ?", (id_cuenta,))
    return {"eliminado": True}


# ---------- subcuentas ----------

@app.get("/subcuentas")
def listar_subcuentas():
    return fetch_all("SELECT * FROM subcuentas")


@app.get("/subcuentas/{id_subcuenta}")
def obtener_subcuenta(id_subcuenta: int):
    return fetch_one("SELECT * FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))


@app.post("/subcuentas")
def crear_subcuenta(subcuenta: Subcuenta):
    return run_returning(
        "INSERT INTO subcuentas (nombre, saldo, saldo_meta, descripcion, id_cuenta) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?)",
        (subcuenta.nombre, subcuenta.saldo, subcuenta.saldo_meta, subcuenta.descripcion, subcuenta.id_cuenta),
    )


@app.put("/subcuentas/{id_subcuenta}")
def actualizar_subcuenta(id_subcuenta: int, subcuenta: Subcuenta):
    return run_returning(
        "UPDATE subcuentas SET nombre = ?, saldo = ?, saldo_meta = ?, descripcion = ?, id_cuenta = ? "
        "OUTPUT INSERTED.* WHERE id_subcuenta = ?",
        (subcuenta.nombre, subcuenta.saldo, subcuenta.saldo_meta, subcuenta.descripcion,
         subcuenta.id_cuenta, id_subcuenta),
    )


@app.delete("/subcuentas/{id_subcuenta}")
def eliminar_subcuenta(id_subcuenta: int):
    execute("DELETE FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))
    return {"eliminado": True}


# ---------- plan_recurrente ----------

@app.get("/plan-recurrente")
def listar_planes():
    return fetch_all("SELECT * FROM plan_recurrente")


@app.get("/plan-recurrente/{id_plan}")
def obtener_plan(id_plan: int):
    return fetch_one("SELECT * FROM plan_recurrente WHERE id_plan = ?", (id_plan,))


@app.post("/plan-recurrente")
def crear_plan(plan: PlanRecurrente):
    return run_returning(
        "INSERT INTO plan_recurrente (nombre, descripcion, activo) OUTPUT INSERTED.* VALUES (?, ?, ?)",
        (plan.nombre, plan.descripcion, plan.activo),
    )


@app.put("/plan-recurrente/{id_plan}")
def actualizar_plan(id_plan: int, plan: PlanRecurrente):
    return run_returning(
        "UPDATE plan_recurrente SET nombre = ?, descripcion = ?, activo = ? "
        "OUTPUT INSERTED.* WHERE id_plan = ?",
        (plan.nombre, plan.descripcion, plan.activo, id_plan),
    )


@app.delete("/plan-recurrente/{id_plan}")
def eliminar_plan(id_plan: int):
    execute("DELETE FROM plan_recurrente WHERE id_plan = ?", (id_plan,))
    return {"eliminado": True}


# ---------- plan_recurrente_destinos ----------

@app.get("/plan-recurrente-destinos")
def listar_destinos():
    return fetch_all("SELECT * FROM plan_recurrente_destinos")


@app.get("/plan-recurrente-destinos/{id_destino}")
def obtener_destino(id_destino: int):
    return fetch_one(
        "SELECT * FROM plan_recurrente_destinos WHERE id_plan_recurrente_destino = ?",
        (id_destino,),
    )


@app.post("/plan-recurrente-destinos")
def crear_destino(destino: PlanRecurrenteDestino):
    return run_returning(
        "INSERT INTO plan_recurrente_destinos "
        "(id_plan, id_cuenta_destino, id_subcuenta_destino, monto, porcentaje, activo) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?)",
        (destino.id_plan, destino.id_cuenta_destino, destino.id_subcuenta_destino,
         destino.monto, destino.porcentaje, destino.activo),
    )


@app.put("/plan-recurrente-destinos/{id_destino}")
def actualizar_destino(id_destino: int, destino: PlanRecurrenteDestino):
    return run_returning(
        "UPDATE plan_recurrente_destinos SET id_plan = ?, id_cuenta_destino = ?, "
        "id_subcuenta_destino = ?, monto = ?, porcentaje = ?, activo = ? "
        "OUTPUT INSERTED.* WHERE id_plan_recurrente_destino = ?",
        (destino.id_plan, destino.id_cuenta_destino, destino.id_subcuenta_destino,
         destino.monto, destino.porcentaje, destino.activo, id_destino),
    )


@app.delete("/plan-recurrente-destinos/{id_destino}")
def eliminar_destino(id_destino: int):
    execute(
        "DELETE FROM plan_recurrente_destinos WHERE id_plan_recurrente_destino = ?",
        (id_destino,),
    )
    return {"eliminado": True}


# ---------- transacciones ----------

@app.get("/transacciones")
def listar_transacciones():
    return fetch_all("SELECT * FROM transacciones ORDER BY fecha DESC")


@app.get("/transacciones/{id_transaccion}")
def obtener_transaccion(id_transaccion: int):
    return fetch_one("SELECT * FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))


@app.post("/transacciones")
def crear_transaccion(transaccion: Transaccion):
    return run_returning(
        "INSERT INTO transacciones "
        "(fecha, tipo, monto, id_cuenta_origen, id_cuenta_destino, id_categoria, descripcion, referencia) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (transaccion.fecha, transaccion.tipo, transaccion.monto, transaccion.id_cuenta_origen,
         transaccion.id_cuenta_destino, transaccion.id_categoria, transaccion.descripcion,
         transaccion.referencia),
    )


@app.put("/transacciones/{id_transaccion}")
def actualizar_transaccion(id_transaccion: int, transaccion: Transaccion):
    return run_returning(
        "UPDATE transacciones SET fecha = ?, tipo = ?, monto = ?, id_cuenta_origen = ?, "
        "id_cuenta_destino = ?, id_categoria = ?, descripcion = ?, referencia = ? "
        "OUTPUT INSERTED.* WHERE id_transaccion = ?",
        (transaccion.fecha, transaccion.tipo, transaccion.monto, transaccion.id_cuenta_origen,
         transaccion.id_cuenta_destino, transaccion.id_categoria, transaccion.descripcion,
         transaccion.referencia, id_transaccion),
    )


@app.delete("/transacciones/{id_transaccion}")
def eliminar_transaccion(id_transaccion: int):
    execute("DELETE FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))
    return {"eliminado": True}


# ---------- movimientos_subcuenta ----------

@app.get("/movimientos-subcuenta")
def listar_movimientos_subcuenta():
    return fetch_all("SELECT * FROM movimientos_subcuenta ORDER BY fecha DESC")


@app.get("/movimientos-subcuenta/{id_movimiento}")
def obtener_movimiento_subcuenta(id_movimiento: int):
    return fetch_one(
        "SELECT * FROM movimientos_subcuenta WHERE id_movimiento_subcuenta = ?",
        (id_movimiento,),
    )


@app.post("/movimientos-subcuenta")
def crear_movimiento_subcuenta(movimiento: MovimientoSubcuenta):
    return run_returning(
        "INSERT INTO movimientos_subcuenta "
        "(fecha, tipo, monto, id_subcuenta_origen, id_subcuenta_destino, id_categoria, descripcion, "
        "id_transaccion_relacionada) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (movimiento.fecha, movimiento.tipo, movimiento.monto, movimiento.id_subcuenta_origen,
         movimiento.id_subcuenta_destino, movimiento.id_categoria, movimiento.descripcion,
         movimiento.id_transaccion_relacionada),
    )


@app.put("/movimientos-subcuenta/{id_movimiento}")
def actualizar_movimiento_subcuenta(id_movimiento: int, movimiento: MovimientoSubcuenta):
    return run_returning(
        "UPDATE movimientos_subcuenta SET fecha = ?, tipo = ?, monto = ?, id_subcuenta_origen = ?, "
        "id_subcuenta_destino = ?, id_categoria = ?, descripcion = ?, id_transaccion_relacionada = ? "
        "OUTPUT INSERTED.* WHERE id_movimiento_subcuenta = ?",
        (movimiento.fecha, movimiento.tipo, movimiento.monto, movimiento.id_subcuenta_origen,
         movimiento.id_subcuenta_destino, movimiento.id_categoria, movimiento.descripcion,
         movimiento.id_transaccion_relacionada, id_movimiento),
    )


@app.delete("/movimientos-subcuenta/{id_movimiento}")
def eliminar_movimiento_subcuenta(id_movimiento: int):
    execute(
        "DELETE FROM movimientos_subcuenta WHERE id_movimiento_subcuenta = ?",
        (id_movimiento,),
    )
    return {"eliminado": True}


# ---------- financiamientos ----------

@app.get("/financiamientos")
def listar_financiamientos():
    return fetch_all("SELECT * FROM financiamientos ORDER BY fecha_inicio DESC")


@app.get("/financiamientos/{id_financiamiento}")
def obtener_financiamiento(id_financiamiento: int):
    return fetch_one(
        "SELECT * FROM financiamientos WHERE id_financiamiento = ?",
        (id_financiamiento,),
    )


@app.post("/financiamientos")
def crear_financiamiento(financiamiento: Financiamiento):
    return run_returning(
        "INSERT INTO financiamientos "
        "(descripcion, fecha_inicio, monto_total, numero_cuotas, tasa_interes, id_transaccion_origen) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?)",
        (financiamiento.descripcion, financiamiento.fecha_inicio, financiamiento.monto_total,
         financiamiento.numero_cuotas, financiamiento.tasa_interes, financiamiento.id_transaccion_origen),
    )


@app.put("/financiamientos/{id_financiamiento}")
def actualizar_financiamiento(id_financiamiento: int, financiamiento: Financiamiento):
    return run_returning(
        "UPDATE financiamientos SET descripcion = ?, fecha_inicio = ?, monto_total = ?, "
        "numero_cuotas = ?, tasa_interes = ?, id_transaccion_origen = ? "
        "OUTPUT INSERTED.* WHERE id_financiamiento = ?",
        (financiamiento.descripcion, financiamiento.fecha_inicio, financiamiento.monto_total,
         financiamiento.numero_cuotas, financiamiento.tasa_interes, financiamiento.id_transaccion_origen,
         id_financiamiento),
    )


@app.delete("/financiamientos/{id_financiamiento}")
def eliminar_financiamiento(id_financiamiento: int):
    execute("DELETE FROM financiamientos WHERE id_financiamiento = ?", (id_financiamiento,))
    return {"eliminado": True}


# ---------- cuotas_financiamiento ----------

@app.get("/cuotas-financiamiento")
def listar_cuotas_financiamiento():
    return fetch_all("SELECT * FROM cuotas_financiamiento ORDER BY fecha_vencimiento")


@app.get("/cuotas-financiamiento/{id_cuota}")
def obtener_cuota_financiamiento(id_cuota: int):
    return fetch_one(
        "SELECT * FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?",
        (id_cuota,),
    )


@app.post("/cuotas-financiamiento")
def crear_cuota_financiamiento(cuota: CuotaFinanciamiento):
    return run_returning(
        "INSERT INTO cuotas_financiamiento "
        "(id_financiamiento, numero_cuota, fecha_vencimiento, monto, id_transaccion_pago, fecha_pago) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?)",
        (cuota.id_financiamiento, cuota.numero_cuota, cuota.fecha_vencimiento, cuota.monto,
         cuota.id_transaccion_pago, cuota.fecha_pago),
    )


@app.put("/cuotas-financiamiento/{id_cuota}")
def actualizar_cuota_financiamiento(id_cuota: int, cuota: CuotaFinanciamiento):
    return run_returning(
        "UPDATE cuotas_financiamiento SET id_financiamiento = ?, numero_cuota = ?, "
        "fecha_vencimiento = ?, monto = ?, id_transaccion_pago = ?, fecha_pago = ? "
        "OUTPUT INSERTED.* WHERE id_cuota_financiamiento = ?",
        (cuota.id_financiamiento, cuota.numero_cuota, cuota.fecha_vencimiento, cuota.monto,
         cuota.id_transaccion_pago, cuota.fecha_pago, id_cuota),
    )


@app.delete("/cuotas-financiamiento/{id_cuota}")
def eliminar_cuota_financiamiento(id_cuota: int):
    execute(
        "DELETE FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?",
        (id_cuota,),
    )
    return {"eliminado": True}


# ---------- obligaciones ----------

@app.get("/obligaciones")
def listar_obligaciones():
    return fetch_all("SELECT * FROM obligaciones ORDER BY fecha_creacion DESC")


@app.get("/obligaciones/{id_obligacion}")
def obtener_obligacion(id_obligacion: int):
    return fetch_one("SELECT * FROM obligaciones WHERE id_obligacion = ?", (id_obligacion,))


@app.post("/obligaciones")
def crear_obligacion(obligacion: Obligacion):
    return run_returning(
        "INSERT INTO obligaciones "
        "(fecha_creacion, tipo, monto, descripcion, id_persona, id_transaccion_origen, "
        "id_movimiento_subcuenta_origen, id_cuenta_destino_resolucion, id_subcuenta_destino_resolucion) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (obligacion.fecha_creacion, obligacion.tipo, obligacion.monto, obligacion.descripcion,
         obligacion.id_persona, obligacion.id_transaccion_origen, obligacion.id_movimiento_subcuenta_origen,
         obligacion.id_cuenta_destino_resolucion, obligacion.id_subcuenta_destino_resolucion),
    )


@app.put("/obligaciones/{id_obligacion}")
def actualizar_obligacion(id_obligacion: int, obligacion: Obligacion):
    return run_returning(
        "UPDATE obligaciones SET fecha_creacion = ?, tipo = ?, monto = ?, descripcion = ?, "
        "id_persona = ?, id_transaccion_origen = ?, id_movimiento_subcuenta_origen = ?, "
        "id_cuenta_destino_resolucion = ?, id_subcuenta_destino_resolucion = ? "
        "OUTPUT INSERTED.* WHERE id_obligacion = ?",
        (obligacion.fecha_creacion, obligacion.tipo, obligacion.monto, obligacion.descripcion,
         obligacion.id_persona, obligacion.id_transaccion_origen, obligacion.id_movimiento_subcuenta_origen,
         obligacion.id_cuenta_destino_resolucion, obligacion.id_subcuenta_destino_resolucion, id_obligacion),
    )


@app.delete("/obligaciones/{id_obligacion}")
def eliminar_obligacion(id_obligacion: int):
    execute("DELETE FROM obligaciones WHERE id_obligacion = ?", (id_obligacion,))
    return {"eliminado": True}


# ---------- liquidaciones_obligacion ----------

@app.get("/liquidaciones")
def listar_liquidaciones():
    return fetch_all("SELECT * FROM liquidaciones_obligacion ORDER BY fecha DESC")


@app.get("/liquidaciones/{id_liquidacion}")
def obtener_liquidacion(id_liquidacion: int):
    return fetch_one(
        "SELECT * FROM liquidaciones_obligacion WHERE id_liquidacion = ?",
        (id_liquidacion,),
    )


@app.post("/liquidaciones")
def crear_liquidacion(liquidacion: LiquidacionObligacion):
    return run_returning(
        "INSERT INTO liquidaciones_obligacion "
        "(id_obligacion, fecha, monto, id_transaccion, id_movimiento_subcuenta, descripcion) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?)",
        (liquidacion.id_obligacion, liquidacion.fecha, liquidacion.monto,
         liquidacion.id_transaccion, liquidacion.id_movimiento_subcuenta, liquidacion.descripcion),
    )


@app.put("/liquidaciones/{id_liquidacion}")
def actualizar_liquidacion(id_liquidacion: int, liquidacion: LiquidacionObligacion):
    return run_returning(
        "UPDATE liquidaciones_obligacion SET id_obligacion = ?, fecha = ?, monto = ?, "
        "id_transaccion = ?, id_movimiento_subcuenta = ?, descripcion = ? "
        "OUTPUT INSERTED.* WHERE id_liquidacion = ?",
        (liquidacion.id_obligacion, liquidacion.fecha, liquidacion.monto,
         liquidacion.id_transaccion, liquidacion.id_movimiento_subcuenta, liquidacion.descripcion,
         id_liquidacion),
    )


@app.delete("/liquidaciones/{id_liquidacion}")
def eliminar_liquidacion(id_liquidacion: int):
    execute("DELETE FROM liquidaciones_obligacion WHERE id_liquidacion = ?", (id_liquidacion,))
    return {"eliminado": True}


# ---------- vistas (solo lectura) ----------

@app.get("/saldos-cuentas")
def listar_saldos_cuentas():
    return fetch_all("SELECT * FROM vw_saldo_cuentas")


@app.get("/estado-obligaciones")
def listar_estado_obligaciones():
    return fetch_all("SELECT * FROM vw_estado_obligaciones")
