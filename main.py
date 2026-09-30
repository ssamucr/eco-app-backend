import calendar
import re
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from db import get_connection
from schemas import (
    CalendarioFinanciamiento,
    Categoria,
    Cuenta,
    CuentaNueva,
    CuotaFinanciamiento,
    EjecucionPlan,
    Financiamiento,
    FinanciamientoNuevo,
    LiquidacionObligacion,
    MovimientoSubcuenta,
    Obligacion,
    PagoCuota,
    Persona,
    PlanConDestinos,
    PlanRecurrenteDestino,
    Subcuenta,
    TransaccionEditar,
    TransaccionNueva,
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


@contextmanager
def transaccion():
    """Varias sentencias en una sola transaccion: si algo falla, no se guarda nada."""
    conexion = get_connection()
    cursor = conexion.cursor()
    try:
        yield cursor
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def fila_actual(cursor):
    columnas = [c[0] for c in cursor.description]
    fila = cursor.fetchone()
    return dict(zip(columnas, fila)) if fila else None


# ---------- helpers de cuentas y subcuentas ----------

TARJETA = "TARJETA_CREDITO"
TIPOS_CUENTA = ("AHORRO", "CORRIENTE", TARJETA, "EFECTIVO")


def _dinero(valor):
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"))


def _rechazar(codigo, mensaje):
    return HTTPException(status_code=codigo, detail=mensaje)


def _texto(valor):
    valor = (valor or "").strip()
    return valor or None


def _validar_cuenta(cuenta):
    nombre = _texto(cuenta.nombre)
    if not nombre:
        raise _rechazar(422, "El nombre de la cuenta es obligatorio.")
    if cuenta.tipo not in TIPOS_CUENTA:
        raise _rechazar(422, "Tipo de cuenta no válido.")
    limite = None
    if cuenta.tipo == TARJETA:
        if not cuenta.limite_credito or cuenta.limite_credito <= 0:
            raise _rechazar(422, "El límite de crédito es obligatorio para una tarjeta.")
        limite = _dinero(cuenta.limite_credito)
    return nombre, _texto(cuenta.entidad), limite


def _sin_asignar(cursor, id_cuenta):
    """Saldo de la cuenta que todavia no esta repartido en subcuentas."""
    cursor.execute("SELECT saldo_calculado FROM vw_saldo_cuentas WHERE id_cuenta = ?", (id_cuenta,))
    fila = cursor.fetchone()
    if not fila:
        raise _rechazar(404, "La cuenta no existe.")
    cursor.execute("SELECT ISNULL(SUM(saldo), 0) FROM subcuentas WHERE id_cuenta = ?", (id_cuenta,))
    return fila[0] - cursor.fetchone()[0]


def _exigir_disponible(monto, disponible):
    if monto > max(disponible, Decimal(0)):
        raise _rechazar(
            422, "Solo hay $%s sin asignar en esta cuenta." % format(max(disponible, Decimal(0)), ",.2f")
        )


def _movimiento_subcuenta(cursor, monto, origen, destino, descripcion, id_transaccion=None):
    cursor.execute(
        "INSERT INTO movimientos_subcuenta "
        "(fecha, tipo, monto, id_subcuenta_origen, id_subcuenta_destino, descripcion, "
        "id_transaccion_relacionada) VALUES (?, 'ASIGNACION', ?, ?, ?, ?, ?)",
        (date.today(), monto, origen, destino, descripcion, id_transaccion),
    )


def _insertar_subcuenta(cursor, id_cuenta, nombre, monto, saldo_meta, descripcion, id_transaccion=None):
    cursor.execute(
        "INSERT INTO subcuentas (nombre, saldo, saldo_meta, descripcion, id_cuenta) "
        "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?)",
        (nombre, monto, _dinero(saldo_meta) if saldo_meta is not None else None, descripcion, id_cuenta),
    )
    subcuenta = fila_actual(cursor)
    if monto > 0:
        _movimiento_subcuenta(
            cursor, monto, None, subcuenta["id_subcuenta"], "Asignación de saldo", id_transaccion
        )
    return subcuenta


def _contar_referencias(consultas, parametros):
    """consultas: [(singular, plural, "SELECT COUNT(*) ... WHERE ...")]; devuelve las que tienen filas."""
    seleccion = ", ".join("(%s) AS n%d" % (sql, i) for i, (_, _, sql) in enumerate(consultas))
    fila = fetch_one("SELECT " + seleccion, parametros)
    return {
        (singular if fila["n%d" % i] == 1 else plural): fila["n%d" % i]
        for i, (singular, plural, _) in enumerate(consultas)
        if fila["n%d" % i]
    }


def _mensaje_bloqueo(que, referencias):
    detalle = ", ".join("%d %s" % (total, etiqueta) for etiqueta, total in referencias.items())
    return "No se puede eliminar %s porque tiene %s." % (que, detalle)


# ---------- personas ----------

LIMITE_NOMBRE_PERSONA = 50
LIMITE_NOMBRE_CATEGORIA = 30
LIMITE_DESCRIPCION_CATEGORIA = 50
TIPOS_OBLIGACION_PERSONA = ("POR_COBRAR", "POR_PAGAR")


def _nombre_requerido(valor, maximo, etiqueta):
    nombre = _texto_acotado(valor, maximo, etiqueta)
    if not nombre:
        raise _rechazar(422, "%s es obligatorio." % etiqueta)
    return nombre


def _exigir_nombre_libre(tabla, columna_id, nombre, id_actual, mensaje):
    """No permite dos filas con el mismo nombre (sin distinguir mayusculas)."""
    repetido = fetch_one(
        "SELECT 1 AS repetido FROM %s WHERE LOWER(nombre) = LOWER(?) AND %s <> ?" % (tabla, columna_id),
        (nombre, id_actual if id_actual is not None else 0),
    )
    if repetido:
        raise _rechazar(409, mensaje)


@app.get("/personas")
def listar_personas():
    return fetch_all("SELECT * FROM personas")


@app.get("/personas/{id_persona}")
def obtener_persona(id_persona: int):
    return fetch_one("SELECT * FROM personas WHERE id_persona = ?", (id_persona,))


@app.post("/personas")
def crear_persona(persona: Persona):
    nombre = _nombre_requerido(persona.nombre, LIMITE_NOMBRE_PERSONA, "El nombre")
    _exigir_nombre_libre("personas", "id_persona", nombre, None, "Ya existe una persona con ese nombre.")
    return run_returning("INSERT INTO personas (nombre) OUTPUT INSERTED.* VALUES (?)", (nombre,))


@app.put("/personas/{id_persona}")
def actualizar_persona(id_persona: int, persona: Persona):
    nombre = _nombre_requerido(persona.nombre, LIMITE_NOMBRE_PERSONA, "El nombre")
    _exigir_nombre_libre("personas", "id_persona", nombre, id_persona, "Ya existe una persona con ese nombre.")
    actualizada = run_returning(
        "UPDATE personas SET nombre = ? OUTPUT INSERTED.* WHERE id_persona = ?",
        (nombre, id_persona),
    )
    if not actualizada:
        raise _rechazar(404, "La persona no existe.")
    return actualizada


@app.delete("/personas/{id_persona}")
def eliminar_persona(id_persona: int):
    persona = fetch_one("SELECT nombre FROM personas WHERE id_persona = ?", (id_persona,))
    if not persona:
        raise _rechazar(404, "La persona no existe.")
    pendientes = fetch_one(
        "SELECT COUNT(*) AS total FROM obligaciones o "
        "JOIN vw_estado_obligaciones e ON e.id_obligacion = o.id_obligacion "
        "WHERE o.id_persona = ? AND e.resuelta = 0",
        (id_persona,),
    )["total"]
    if pendientes:
        raise _rechazar(
            409,
            "No se puede eliminar a «%s» porque tiene %d %s. Liquídalas primero."
            % (persona["nombre"], pendientes, "obligación pendiente" if pendientes == 1 else "obligaciones pendientes"),
        )
    # Sus obligaciones ya liquidadas se conservan, solo dejan de tener persona.
    with transaccion() as cursor:
        cursor.execute("UPDATE obligaciones SET id_persona = NULL WHERE id_persona = ?", (id_persona,))
        desvinculadas = cursor.rowcount
        cursor.execute("DELETE FROM personas WHERE id_persona = ?", (id_persona,))
    return {"eliminado": True, "obligaciones_desvinculadas": desvinculadas}


def _personas_detalle(id_persona=None):
    """Personas con su balance: lo que te deben menos lo que debes, sobre obligaciones pendientes."""
    filtro = "WHERE p.id_persona = ? " if id_persona is not None else ""
    parametros = (id_persona,) if id_persona is not None else ()
    personas = fetch_all(
        "SELECT p.id_persona, p.nombre, "
        "ISNULL(SUM(CASE WHEN e.resuelta = 0 AND o.tipo = 'POR_COBRAR' THEN e.monto_pendiente END), 0) AS por_cobrar, "
        "ISNULL(SUM(CASE WHEN e.resuelta = 0 AND o.tipo = 'POR_PAGAR' THEN e.monto_pendiente END), 0) AS por_pagar, "
        "SUM(CASE WHEN e.resuelta = 0 THEN 1 ELSE 0 END) AS pendientes, "
        "SUM(CASE WHEN e.resuelta = 1 THEN 1 ELSE 0 END) AS liquidadas "
        "FROM personas p "
        "LEFT JOIN obligaciones o ON o.id_persona = p.id_persona "
        "LEFT JOIN vw_estado_obligaciones e ON e.id_obligacion = o.id_obligacion " + filtro +
        "GROUP BY p.id_persona, p.nombre",
        parametros,
    )
    # Descripcion de lo pendiente, para resumir "1 obligacion · concepto" en la lista.
    pendientes_por_persona = {}
    for fila in fetch_all(
        "SELECT o.id_persona, o.descripcion FROM obligaciones o "
        "JOIN vw_estado_obligaciones e ON e.id_obligacion = o.id_obligacion "
        "WHERE e.resuelta = 0 AND o.id_persona IS NOT NULL " + ("AND o.id_persona = ? " if id_persona is not None else ""),
        parametros,
    ):
        pendientes_por_persona.setdefault(fila["id_persona"], []).append(fila["descripcion"])

    resultado = []
    for p in personas:
        pendientes = pendientes_por_persona.get(p["id_persona"], [])
        resultado.append(
            {
                "id_persona": p["id_persona"],
                "nombre": p["nombre"],
                "balance": _num(p["por_cobrar"] - p["por_pagar"]),
                "por_cobrar": _num(p["por_cobrar"]),
                "por_pagar": _num(p["por_pagar"]),
                "pendientes": len(pendientes),
                "liquidadas": p["liquidadas"] or 0,
                "concepto": _limpiar_descripcion(pendientes[0]) if len(pendientes) == 1 else None,
            }
        )
    return resultado


@app.get("/personas-detalle")
def listar_personas_detalle():
    """Pantalla Personas: quienes tienen saldo pendiente primero (de mayor a menor), luego el resto."""
    personas = _personas_detalle()
    personas.sort(key=lambda p: (p["balance"] == 0 and p["pendientes"] == 0, -abs(p["balance"]), p["nombre"].lower()))
    return {"balance_neto": round(sum(p["balance"] for p in personas), 2), "personas": personas}


@app.get("/personas-detalle/{id_persona}")
def obtener_persona_detalle(id_persona: int):
    personas = _personas_detalle(id_persona)
    if not personas:
        raise _rechazar(404, "La persona no existe.")
    obligaciones = fetch_all(
        "SELECT o.id_obligacion, o.tipo, o.descripcion, o.fecha_creacion, o.monto, e.monto_pendiente, e.resuelta, "
        "e.fecha_ultima_liquidacion FROM obligaciones o "
        "JOIN vw_estado_obligaciones e ON e.id_obligacion = o.id_obligacion WHERE o.id_persona = ? "
        "ORDER BY e.resuelta, o.fecha_creacion DESC, o.id_obligacion DESC",
        (id_persona,),
    )
    for o in obligaciones:
        o["descripcion"] = _limpiar_descripcion(o["descripcion"])
        o["monto"] = _num(o["monto"])
        o["monto_pendiente"] = _num(o["monto_pendiente"])
        o["resuelta"] = bool(o["resuelta"])
    return {**personas[0], "obligaciones": obligaciones}


# ---------- categorias ----------

def _validar_categoria(categoria):
    nombre = _nombre_requerido(categoria.nombre, LIMITE_NOMBRE_CATEGORIA, "El nombre")
    descripcion = _texto_acotado(categoria.descripcion, LIMITE_DESCRIPCION_CATEGORIA, "La descripción")
    return nombre, descripcion


@app.get("/categorias")
def listar_categorias():
    return fetch_all("SELECT * FROM categorias")


@app.get("/categorias/{id_categoria}")
def obtener_categoria(id_categoria: int):
    return fetch_one("SELECT * FROM categorias WHERE id_categoria = ?", (id_categoria,))


@app.post("/categorias")
def crear_categoria(categoria: Categoria):
    nombre, descripcion = _validar_categoria(categoria)
    _exigir_nombre_libre("categorias", "id_categoria", nombre, None, "Ya existe una categoría con ese nombre.")
    return run_returning(
        "INSERT INTO categorias (nombre, descripcion) OUTPUT INSERTED.* VALUES (?, ?)",
        (nombre, descripcion),
    )


@app.put("/categorias/{id_categoria}")
def actualizar_categoria(id_categoria: int, categoria: Categoria):
    nombre, descripcion = _validar_categoria(categoria)
    _exigir_nombre_libre("categorias", "id_categoria", nombre, id_categoria, "Ya existe una categoría con ese nombre.")
    actualizada = run_returning(
        "UPDATE categorias SET nombre = ?, descripcion = ? OUTPUT INSERTED.* WHERE id_categoria = ?",
        (nombre, descripcion, id_categoria),
    )
    if not actualizada:
        raise _rechazar(404, "La categoría no existe.")
    return actualizada


@app.delete("/categorias/{id_categoria}")
def eliminar_categoria(id_categoria: int):
    if not fetch_one("SELECT 1 AS existe FROM categorias WHERE id_categoria = ?", (id_categoria,)):
        raise _rechazar(404, "La categoría no existe.")
    # Los movimientos ya registrados se conservan, solo quedan sin categoria.
    with transaccion() as cursor:
        cursor.execute("UPDATE transacciones SET id_categoria = NULL WHERE id_categoria = ?", (id_categoria,))
        transacciones = cursor.rowcount
        cursor.execute("UPDATE movimientos_subcuenta SET id_categoria = NULL WHERE id_categoria = ?", (id_categoria,))
        movimientos = cursor.rowcount
        cursor.execute("DELETE FROM categorias WHERE id_categoria = ?", (id_categoria,))
    return {"eliminado": True, "movimientos_sin_categoria": transacciones + movimientos}


def _categorias_detalle(id_categoria=None):
    filtro = "WHERE c.id_categoria = ? " if id_categoria is not None else ""
    return fetch_all(
        "SELECT c.id_categoria, c.nombre, c.descripcion, "
        "(SELECT COUNT(*) FROM transacciones t WHERE t.id_categoria = c.id_categoria) + "
        "(SELECT COUNT(*) FROM movimientos_subcuenta m WHERE m.id_categoria = c.id_categoria) AS movimientos "
        "FROM categorias c " + filtro + "ORDER BY c.nombre",
        (id_categoria,) if id_categoria is not None else (),
    )


@app.get("/categorias-detalle")
def listar_categorias_detalle():
    return _categorias_detalle()


@app.get("/categorias-detalle/{id_categoria}")
def obtener_categoria_detalle(id_categoria: int):
    categorias = _categorias_detalle(id_categoria)
    if not categorias:
        raise _rechazar(404, "La categoría no existe.")
    return categorias[0]


# ---------- cuentas ----------

@app.get("/cuentas")
def listar_cuentas():
    return fetch_all("SELECT * FROM cuentas")


@app.get("/cuentas/{id_cuenta}")
def obtener_cuenta(id_cuenta: int):
    return fetch_one("SELECT * FROM cuentas WHERE id_cuenta = ?", (id_cuenta,))


def _transaccion_saldo_inicial(tipo_cuenta, id_cuenta, saldo):
    """(tipo, origen, destino, monto) de la transaccion que deja la cuenta con `saldo`, o None si es 0.

    En una tarjeta el saldo es deuda (salidas - entradas), en el resto es dinero (entradas - salidas).
    """
    if saldo == 0:
        return None
    if tipo_cuenta == TARJETA:
        if saldo > 0:
            return ("GASTO", id_cuenta, None, saldo)
        return ("PAGO_TARJETA", None, id_cuenta, -saldo)
    if saldo > 0:
        return ("INGRESO", None, id_cuenta, saldo)
    return ("GASTO", id_cuenta, None, -saldo)


@app.post("/cuentas")
def crear_cuenta(cuenta: CuentaNueva):
    nombre, entidad, limite = _validar_cuenta(cuenta)
    saldo = _dinero(cuenta.saldo_inicial)
    subcuentas = []
    for s in cuenta.subcuentas:
        subcuentas.append((_texto(s.nombre), _dinero(s.monto), s.saldo_meta, _texto(s.descripcion)))
    if subcuentas:
        if cuenta.tipo == TARJETA:
            raise _rechazar(422, "Una tarjeta de crédito no admite subcuentas.")
        if any(not nombre_sub for nombre_sub, _, _, _ in subcuentas):
            raise _rechazar(422, "Cada subcuenta necesita un nombre.")
        if any(monto < 0 for _, monto, _, _ in subcuentas):
            raise _rechazar(422, "El monto de una subcuenta no puede ser negativo.")
        _exigir_disponible(sum((m for _, m, _, _ in subcuentas), Decimal(0)), saldo)

    with transaccion() as cursor:
        cursor.execute(
            "INSERT INTO cuentas (nombre, entidad, tipo, limite_credito) "
            "OUTPUT INSERTED.* VALUES (?, ?, ?, ?)",
            (nombre, entidad, cuenta.tipo, limite),
        )
        creada = fila_actual(cursor)

        id_transaccion = None
        inicial = _transaccion_saldo_inicial(cuenta.tipo, creada["id_cuenta"], saldo)
        if inicial:
            cursor.execute(
                "INSERT INTO transacciones (fecha, tipo, monto, id_cuenta_origen, id_cuenta_destino, descripcion) "
                "OUTPUT INSERTED.id_transaccion VALUES (?, ?, ?, ?, ?, 'Saldo inicial')",
                (date.today(), inicial[0], inicial[3], inicial[1], inicial[2]),
            )
            id_transaccion = cursor.fetchone()[0]

        for nombre_sub, monto, saldo_meta, descripcion in subcuentas:
            _insertar_subcuenta(
                cursor, creada["id_cuenta"], nombre_sub, monto, saldo_meta, descripcion, id_transaccion
            )
    return creada


@app.put("/cuentas/{id_cuenta}")
def actualizar_cuenta(id_cuenta: int, cuenta: Cuenta):
    nombre, entidad, limite = _validar_cuenta(cuenta)
    if cuenta.tipo == TARJETA:
        con_subcuentas = fetch_one("SELECT COUNT(*) AS total FROM subcuentas WHERE id_cuenta = ?", (id_cuenta,))
        if con_subcuentas["total"]:
            raise _rechazar(409, "Elimina primero las subcuentas para convertir la cuenta en tarjeta de crédito.")
    actualizada = run_returning(
        "UPDATE cuentas SET nombre = ?, entidad = ?, tipo = ?, limite_credito = ? "
        "OUTPUT INSERTED.* WHERE id_cuenta = ?",
        (nombre, entidad, cuenta.tipo, limite, id_cuenta),
    )
    if not actualizada:
        raise _rechazar(404, "La cuenta no existe.")
    return actualizada


@app.delete("/cuentas/{id_cuenta}")
def eliminar_cuenta(id_cuenta: int):
    cuenta = fetch_one("SELECT nombre FROM cuentas WHERE id_cuenta = ?", (id_cuenta,))
    if not cuenta:
        raise _rechazar(404, "La cuenta no existe.")
    referencias = _contar_referencias(
        [
            ("transacción", "transacciones",
             "SELECT COUNT(*) FROM transacciones WHERE id_cuenta_origen = ? OR id_cuenta_destino = ?"),
            ("subcuenta", "subcuentas", "SELECT COUNT(*) FROM subcuentas WHERE id_cuenta = ?"),
            ("obligación", "obligaciones",
             "SELECT COUNT(*) FROM obligaciones WHERE id_cuenta_destino_resolucion = ?"),
            ("plan recurrente", "planes recurrentes",
             "SELECT COUNT(*) FROM plan_recurrente_destinos WHERE id_cuenta_destino = ?"),
        ],
        (id_cuenta, id_cuenta, id_cuenta, id_cuenta, id_cuenta),
    )
    if referencias:
        raise _rechazar(409, _mensaje_bloqueo("«%s»" % cuenta["nombre"], referencias))
    execute("DELETE FROM cuentas WHERE id_cuenta = ?", (id_cuenta,))
    return {"eliminado": True}


def _cuentas_detalle(id_cuenta=None):
    """Cuentas con saldo, subcuentas y 'sin asignar' ya calculados (lectura para la pantalla Cuentas)."""
    solo_cuenta = "WHERE c.id_cuenta = ? " if id_cuenta is not None else ""
    solo_sub = "AND s.id_cuenta = ? " if id_cuenta is not None else ""
    parametros = (id_cuenta,) if id_cuenta is not None else ()

    cuentas = fetch_all(
        "SELECT c.id_cuenta, c.nombre, c.entidad, c.tipo, c.limite_credito, s.saldo_calculado AS saldo "
        "FROM cuentas c JOIN vw_saldo_cuentas s ON s.id_cuenta = c.id_cuenta " + solo_cuenta +
        "ORDER BY CASE WHEN c.tipo = 'TARJETA_CREDITO' THEN 1 ELSE 0 END, c.id_cuenta",
        parametros,
    )
    subcuentas_por_cuenta = {}
    for s in fetch_all(
        "SELECT s.id_subcuenta, s.id_cuenta, s.nombre, ISNULL(s.saldo, 0) AS saldo, s.saldo_meta, "
        "s.descripcion, (SELECT COUNT(*) FROM movimientos_subcuenta m "
        "WHERE m.id_subcuenta_origen = s.id_subcuenta OR m.id_subcuenta_destino = s.id_subcuenta) AS movimientos "
        "FROM subcuentas s WHERE s.id_cuenta IS NOT NULL " + solo_sub +
        "ORDER BY ISNULL(s.saldo, 0) DESC, s.id_subcuenta",
        parametros,
    ):
        subcuentas_por_cuenta.setdefault(s["id_cuenta"], []).append(s)

    resultado = []
    for c in cuentas:
        cuenta = {
            "id_cuenta": c["id_cuenta"],
            "nombre": c["nombre"],
            "entidad": c["entidad"],
            "tipo": c["tipo"],
            "saldo": _num(c["saldo"]),
            "subcuentas": [],
            "sin_asignar": None,
        }
        if c["tipo"] == TARJETA:
            limite = c["limite_credito"]
            cuenta["limite_credito"] = _num(limite) if limite is not None else None
            cuenta["disponible"] = _num(limite - c["saldo"]) if limite is not None else None
            cuenta["uso_pct"] = _porcentaje(c["saldo"], limite)
        else:
            subs = subcuentas_por_cuenta.get(c["id_cuenta"], [])
            sin_asignar = c["saldo"] - sum((s["saldo"] for s in subs), Decimal(0))
            base = sum((s["saldo"] for s in subs if s["saldo"] > 0), Decimal(0)) + max(sin_asignar, Decimal(0))
            cuenta["sin_asignar"] = _num(sin_asignar)
            cuenta["porcentaje_sin_asignar"] = _porcentaje(max(sin_asignar, Decimal(0)), base) or 0
            cuenta["subcuentas"] = [
                {
                    "id_subcuenta": s["id_subcuenta"],
                    "nombre": s["nombre"] or "Sin nombre",
                    "saldo": _num(s["saldo"]),
                    "saldo_meta": _num(s["saldo_meta"]) if s["saldo_meta"] is not None else None,
                    "descripcion": s["descripcion"],
                    "movimientos": s["movimientos"],
                    "porcentaje": _porcentaje(max(s["saldo"], Decimal(0)), base) or 0,
                }
                for s in subs
            ]
        resultado.append(cuenta)
    return resultado


@app.get("/cuentas-detalle")
def listar_cuentas_detalle():
    """Pantalla Cuentas: cuentas separadas en ahorro y tarjetas, con totales."""
    cuentas = _cuentas_detalle()
    ahorro = [c for c in cuentas if c["tipo"] != TARJETA]
    tarjetas = [c for c in cuentas if c["tipo"] == TARJETA]
    return {
        "ahorro": {"total": round(sum(c["saldo"] for c in ahorro), 2), "cuentas": ahorro},
        "tarjetas": {"total": round(sum(c["saldo"] for c in tarjetas), 2), "cuentas": tarjetas},
    }


@app.get("/cuentas-detalle/{id_cuenta}")
def obtener_cuenta_detalle(id_cuenta: int):
    cuentas = _cuentas_detalle(id_cuenta)
    if not cuentas:
        raise _rechazar(404, "La cuenta no existe.")
    return cuentas[0]


# ---------- subcuentas ----------

@app.get("/subcuentas")
def listar_subcuentas():
    return fetch_all("SELECT * FROM subcuentas")


@app.get("/subcuentas/{id_subcuenta}")
def obtener_subcuenta(id_subcuenta: int):
    return fetch_one("SELECT * FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))


def _validar_subcuenta(subcuenta):
    nombre = _texto(subcuenta.nombre)
    if not nombre:
        raise _rechazar(422, "El nombre de la subcuenta es obligatorio.")
    if subcuenta.saldo is not None and subcuenta.saldo < 0:
        raise _rechazar(422, "El monto asignado no puede ser negativo.")
    if subcuenta.saldo_meta is not None and subcuenta.saldo_meta < 0:
        raise _rechazar(422, "El saldo meta no puede ser negativo.")
    return nombre, _texto(subcuenta.descripcion)


@app.post("/subcuentas")
def crear_subcuenta(subcuenta: Subcuenta):
    nombre, descripcion = _validar_subcuenta(subcuenta)
    if subcuenta.id_cuenta is None:
        raise _rechazar(422, "La subcuenta debe pertenecer a una cuenta.")
    monto = _dinero(subcuenta.saldo)
    with transaccion() as cursor:
        cursor.execute("SELECT tipo FROM cuentas WHERE id_cuenta = ?", (subcuenta.id_cuenta,))
        cuenta = cursor.fetchone()
        if not cuenta:
            raise _rechazar(404, "La cuenta no existe.")
        if cuenta[0] == TARJETA:
            raise _rechazar(422, "Una tarjeta de crédito no admite subcuentas.")
        _exigir_disponible(monto, _sin_asignar(cursor, subcuenta.id_cuenta))
        return _insertar_subcuenta(
            cursor, subcuenta.id_cuenta, nombre, monto, subcuenta.saldo_meta, descripcion
        )


@app.put("/subcuentas/{id_subcuenta}")
def actualizar_subcuenta(id_subcuenta: int, subcuenta: Subcuenta):
    nombre, descripcion = _validar_subcuenta(subcuenta)
    with transaccion() as cursor:
        cursor.execute("SELECT id_cuenta, ISNULL(saldo, 0) FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))
        actual = cursor.fetchone()
        if not actual:
            raise _rechazar(404, "La subcuenta no existe.")
        id_cuenta, saldo_actual = actual
        if subcuenta.id_cuenta is not None and subcuenta.id_cuenta != id_cuenta:
            raise _rechazar(422, "No se puede mover una subcuenta a otra cuenta.")

        nuevo = _dinero(subcuenta.saldo) if subcuenta.saldo is not None else saldo_actual
        diferencia = nuevo - saldo_actual
        if diferencia > 0:
            _exigir_disponible(diferencia, _sin_asignar(cursor, id_cuenta))

        cursor.execute(
            "UPDATE subcuentas SET nombre = ?, saldo = ?, saldo_meta = ?, descripcion = ? "
            "OUTPUT INSERTED.* WHERE id_subcuenta = ?",
            (nombre, nuevo, _dinero(subcuenta.saldo_meta) if subcuenta.saldo_meta is not None else None,
             descripcion, id_subcuenta),
        )
        actualizada = fila_actual(cursor)
        if diferencia > 0:
            _movimiento_subcuenta(cursor, diferencia, None, id_subcuenta, "Ajuste de asignación")
        elif diferencia < 0:
            _movimiento_subcuenta(cursor, -diferencia, id_subcuenta, None, "Ajuste de asignación")
        return actualizada


@app.delete("/subcuentas/{id_subcuenta}")
def eliminar_subcuenta(id_subcuenta: int):
    subcuenta = fetch_one("SELECT nombre FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))
    if not subcuenta:
        raise _rechazar(404, "La subcuenta no existe.")
    referencias = _contar_referencias(
        [
            ("obligación", "obligaciones",
             "SELECT COUNT(*) FROM obligaciones WHERE id_subcuenta_destino_resolucion = ?"),
            ("plan recurrente", "planes recurrentes",
             "SELECT COUNT(*) FROM plan_recurrente_destinos WHERE id_subcuenta_destino = ?"),
        ],
        (id_subcuenta, id_subcuenta),
    )
    if referencias:
        raise _rechazar(409, _mensaje_bloqueo("«%s»" % (subcuenta["nombre"] or "la subcuenta"), referencias))
    # Su historial (asignaciones y gastos de la subcuenta) se borra junto con ella; el saldo vuelve a "sin asignar".
    with transaccion() as cursor:
        cursor.execute(
            "DELETE FROM movimientos_subcuenta WHERE id_subcuenta_origen = ? OR id_subcuenta_destino = ?",
            (id_subcuenta, id_subcuenta),
        )
        movimientos = cursor.rowcount
        cursor.execute("DELETE FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))
    return {"eliminado": True, "movimientos_eliminados": movimientos}


# ---------- plan_recurrente ----------

LIMITE_NOMBRE_PLAN = 50
LIMITE_DESCRIPCION_PLAN = 200


class _Simulacion(Exception):
    """Sirve para deshacer una ejecucion de prueba: lleva el resultado, no es un error."""

    def __init__(self, resultado):
        self.resultado = resultado


@app.get("/plan-recurrente")
def listar_planes():
    return fetch_all("SELECT * FROM plan_recurrente")


@app.get("/plan-recurrente/{id_plan}")
def obtener_plan(id_plan: int):
    return fetch_one("SELECT * FROM plan_recurrente WHERE id_plan = ?", (id_plan,))


def _validar_plan(datos):
    nombre = _nombre_requerido(datos.nombre, LIMITE_NOMBRE_PLAN, "El nombre")
    descripcion = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION_PLAN, "La descripción")
    return nombre, descripcion


def _validar_destino(cursor, destino):
    """Reglas de un destino nuevo; devuelve (monto, porcentaje) normalizados."""
    cursor.execute("SELECT tipo FROM cuentas WHERE id_cuenta = ?", (destino.id_cuenta_destino,))
    cuenta = cursor.fetchone()
    if not cuenta:
        raise _rechazar(422, "La cuenta de un destino no existe.")
    monto = _dinero(destino.monto) if destino.monto is not None else None
    porcentaje = _dinero(destino.porcentaje) if destino.porcentaje is not None else None
    if (monto is None) == (porcentaje is None):
        raise _rechazar(422, "Cada destino necesita un monto fijo o un porcentaje (solo uno de los dos).")
    if monto is not None and monto <= 0:
        raise _rechazar(422, "El monto fijo debe ser mayor que 0.")
    if porcentaje is not None and not (0 < porcentaje <= 100):
        raise _rechazar(422, "El porcentaje debe estar entre 0 y 100.")
    if destino.id_subcuenta_destino is not None:
        if cuenta[0] == TARJETA:
            raise _rechazar(422, "Una tarjeta de crédito no tiene subcuentas.")
        cursor.execute("SELECT id_cuenta FROM subcuentas WHERE id_subcuenta = ?", (destino.id_subcuenta_destino,))
        subcuenta = cursor.fetchone()
        if not subcuenta:
            raise _rechazar(422, "La subcuenta de un destino no existe.")
        if subcuenta[0] != destino.id_cuenta_destino:
            raise _rechazar(422, "La subcuenta no pertenece a la cuenta elegida.")
    return monto, porcentaje


def _sincronizar_destinos(cursor, id_plan, destinos):
    """Deja el plan con exactamente los destinos enviados: los ausentes se borran, los que traen id solo
    cambian de estado y los que no traen id se crean."""
    cursor.execute("SELECT id_plan_recurrente_destino FROM plan_recurrente_destinos WHERE id_plan = ?", (id_plan,))
    existentes = {fila[0] for fila in cursor.fetchall()}
    enviados = {d.id_plan_recurrente_destino for d in destinos if d.id_plan_recurrente_destino is not None}
    if enviados - existentes:
        raise _rechazar(422, "Uno de los destinos no pertenece a este plan.")
    for id_destino in existentes - enviados:
        cursor.execute("DELETE FROM plan_recurrente_destinos WHERE id_plan_recurrente_destino = ?", (id_destino,))
    for destino in destinos:
        if destino.id_plan_recurrente_destino is not None:
            cursor.execute(
                "UPDATE plan_recurrente_destinos SET activo = ? WHERE id_plan_recurrente_destino = ?",
                (1 if destino.activo else 0, destino.id_plan_recurrente_destino),
            )
        else:
            monto, porcentaje = _validar_destino(cursor, destino)
            cursor.execute(
                "INSERT INTO plan_recurrente_destinos "
                "(id_plan, id_cuenta_destino, id_subcuenta_destino, monto, porcentaje, activo) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (id_plan, destino.id_cuenta_destino, destino.id_subcuenta_destino, monto, porcentaje,
                 1 if destino.activo else 0),
            )
    cursor.execute(
        "SELECT ISNULL(SUM(porcentaje), 0) FROM plan_recurrente_destinos WHERE id_plan = ? AND activo = 1",
        (id_plan,),
    )
    if cursor.fetchone()[0] > 100:
        raise _rechazar(422, "Los porcentajes de los destinos activos suman más de 100 %.")


@app.post("/plan-recurrente")
def crear_plan(datos: PlanConDestinos):
    nombre, descripcion = _validar_plan(datos)
    _exigir_nombre_libre("plan_recurrente", "id_plan", nombre, None, "Ya existe un plan con ese nombre.")
    with transaccion() as cursor:
        cursor.execute(
            "INSERT INTO plan_recurrente (nombre, descripcion, activo) OUTPUT INSERTED.* VALUES (?, ?, ?)",
            (nombre, descripcion, 1 if datos.activo else 0),
        )
        creado = fila_actual(cursor)
        _sincronizar_destinos(cursor, creado["id_plan"], datos.destinos)
        return creado


@app.put("/plan-recurrente/{id_plan}")
def actualizar_plan(id_plan: int, datos: PlanConDestinos):
    nombre, descripcion = _validar_plan(datos)
    _exigir_nombre_libre("plan_recurrente", "id_plan", nombre, id_plan, "Ya existe un plan con ese nombre.")
    with transaccion() as cursor:
        cursor.execute(
            "UPDATE plan_recurrente SET nombre = ?, descripcion = ?, activo = ? OUTPUT INSERTED.* WHERE id_plan = ?",
            (nombre, descripcion, 1 if datos.activo else 0, id_plan),
        )
        actualizado = fila_actual(cursor)
        if not actualizado:
            raise _rechazar(404, "El plan no existe.")
        _sincronizar_destinos(cursor, id_plan, datos.destinos)
        return actualizado


@app.delete("/plan-recurrente/{id_plan}")
def eliminar_plan(id_plan: int):
    if not fetch_one("SELECT 1 AS existe FROM plan_recurrente WHERE id_plan = ?", (id_plan,)):
        raise _rechazar(404, "El plan no existe.")
    with transaccion() as cursor:
        cursor.execute("DELETE FROM plan_recurrente_destinos WHERE id_plan = ?", (id_plan,))
        destinos = cursor.rowcount
        cursor.execute("DELETE FROM plan_recurrente WHERE id_plan = ?", (id_plan,))
    return {"eliminado": True, "destinos_eliminados": destinos}


def _destinos_del_plan(id_plan=None):
    filtro = "WHERE d.id_plan = ? " if id_plan is not None else ""
    filas = fetch_all(
        "SELECT d.id_plan_recurrente_destino, d.id_plan, d.id_cuenta_destino, c.nombre AS cuenta, c.tipo AS tipo_cuenta, "
        "d.id_subcuenta_destino, s.nombre AS subcuenta, d.monto, d.porcentaje, d.activo "
        "FROM plan_recurrente_destinos d JOIN cuentas c ON c.id_cuenta = d.id_cuenta_destino "
        "LEFT JOIN subcuentas s ON s.id_subcuenta = d.id_subcuenta_destino " + filtro +
        "ORDER BY d.activo DESC, d.id_plan_recurrente_destino",
        (id_plan,) if id_plan is not None else (),
    )
    for f in filas:
        f["activo"] = bool(f["activo"])
        f["monto"] = _num(f["monto"]) if f["monto"] is not None else None
        f["porcentaje"] = _num(f["porcentaje"]) if f["porcentaje"] is not None else None
    return filas


def _planes_detalle(id_plan=None):
    filtro = "WHERE id_plan = ? " if id_plan is not None else ""
    planes = fetch_all(
        "SELECT id_plan, nombre, descripcion, activo FROM plan_recurrente " + filtro + "ORDER BY nombre",
        (id_plan,) if id_plan is not None else (),
    )
    destinos = _destinos_del_plan(id_plan)
    resultado = []
    for p in planes:
        propios = [d for d in destinos if d["id_plan"] == p["id_plan"]]
        resultado.append(
            {
                "id_plan": p["id_plan"],
                "nombre": p["nombre"],
                "descripcion": p["descripcion"],
                "activo": bool(p["activo"]),
                "destinos_activos": sum(1 for d in propios if d["activo"]),
                "destinos_total": len(propios),
                "usa_porcentajes": any(d["activo"] and d["porcentaje"] is not None for d in propios),
                "destinos": propios,
            }
        )
    return resultado


@app.get("/planes-detalle")
def listar_planes_detalle():
    planes = _planes_detalle()
    for p in planes:
        del p["destinos"]
    return planes


@app.get("/planes-detalle/{id_plan}")
def obtener_plan_detalle(id_plan: int):
    planes = _planes_detalle(id_plan)
    if not planes:
        raise _rechazar(404, "El plan no existe.")
    return planes[0]


@app.post("/plan-recurrente/{id_plan}/ejecutar")
def ejecutar_plan(id_plan: int, datos: EjecucionPlan):
    """Crea, por cada destino activo, la transferencia (o pago a tarjeta) desde la cuenta de origen y, si el
    destino tiene subcuenta, su asignacion vinculada. Con `simular` hace todo y lo deshace al final."""
    try:
        with transaccion() as cursor:
            resultado = _ejecutar_plan(cursor, id_plan, datos)
            if datos.simular:
                raise _Simulacion(resultado)
            return resultado
    except _Simulacion as simulacion:
        return simulacion.resultado


def _ejecutar_plan(cursor, id_plan, datos):
    cursor.execute("SELECT nombre, activo FROM plan_recurrente WHERE id_plan = ?", (id_plan,))
    plan = cursor.fetchone()
    if not plan:
        raise _rechazar(404, "El plan no existe.")
    if not plan[1]:
        raise _rechazar(409, "El plan está inactivo: actívalo para ejecutarlo.")

    cursor.execute(
        "SELECT c.nombre, c.tipo, s.saldo_calculado FROM cuentas c JOIN vw_saldo_cuentas s ON s.id_cuenta = c.id_cuenta "
        "WHERE c.id_cuenta = ?",
        (datos.id_cuenta_origen,),
    )
    origen = cursor.fetchone()
    if not origen:
        raise _rechazar(422, "La cuenta de origen no existe.")
    if origen[1] == TARJETA:
        raise _rechazar(422, "La cuenta de origen no puede ser una tarjeta de crédito.")

    cursor.execute(
        "SELECT d.id_plan_recurrente_destino, d.id_cuenta_destino, c.nombre, c.tipo, d.id_subcuenta_destino, s.nombre, "
        "d.monto, d.porcentaje FROM plan_recurrente_destinos d JOIN cuentas c ON c.id_cuenta = d.id_cuenta_destino "
        "LEFT JOIN subcuentas s ON s.id_subcuenta = d.id_subcuenta_destino "
        "WHERE d.id_plan = ? AND d.activo = 1 ORDER BY d.id_plan_recurrente_destino",
        (id_plan,),
    )
    # Las asignaciones dentro de la propia cuenta de origen van al final, cuando ya salio el dinero de las demas.
    destinos = sorted(cursor.fetchall(), key=lambda d: d[1] == datos.id_cuenta_origen)
    if not destinos:
        raise _rechazar(422, "El plan no tiene destinos activos.")

    base = _dinero(datos.monto_base) if datos.monto_base is not None else None
    if any(d[7] is not None for d in destinos) and (base is None or base <= 0):
        raise _rechazar(422, "Indica el monto a distribuir: el plan tiene destinos con porcentaje.")

    lineas = []
    total_transferido = Decimal(0)
    transacciones = 0
    for id_destino, id_cuenta, cuenta, tipo_cuenta, id_sub, subcuenta, monto_fijo, porcentaje in destinos:
        monto = monto_fijo if monto_fijo is not None else (base * porcentaje / 100).quantize(Decimal("0.01"))
        nombre_destino = subcuenta or cuenta
        if monto <= 0:
            raise _rechazar(422, "«%s» resulta en $0.00 con el monto indicado." % nombre_destino)
        misma_cuenta = id_cuenta == datos.id_cuenta_origen
        if misma_cuenta and id_sub is None:
            raise _rechazar(422, "«%s» es la cuenta de origen y no tiene subcuenta: no hay nada que mover." % cuenta)

        descripcion = ("%s: %s" % (plan[0], nombre_destino))[:LIMITE_DESCRIPCION]
        id_transaccion = None
        movimiento = "Asignación"
        if not misma_cuenta:
            tipo_trx = "PAGO_TARJETA" if tipo_cuenta == TARJETA else "TRANSFERENCIA"
            movimiento = "Pago a tarjeta" if tipo_cuenta == TARJETA else "Transferencia"
            cursor.execute(
                "INSERT INTO transacciones (fecha, tipo, monto, id_cuenta_origen, id_cuenta_destino, descripcion) "
                "OUTPUT INSERTED.id_transaccion VALUES (?, ?, ?, ?, ?, ?)",
                (datos.fecha, tipo_trx, monto, datos.id_cuenta_origen, id_cuenta, descripcion),
            )
            id_transaccion = cursor.fetchone()[0]
            total_transferido += monto
            transacciones += 1
        if id_sub is not None:
            _crear_movimiento_subcuenta(cursor, datos.fecha, "ASIGNACION", monto, None, id_sub, None, descripcion, id_transaccion)
            if not misma_cuenta:
                movimiento += " + asignación"
        lineas.append({"id_destino": id_destino, "cuenta": cuenta, "subcuenta": subcuenta, "monto": _num(monto),
                       "movimiento": movimiento})

    saldo_origen = origen[2]
    return {
        "simulado": datos.simular,
        "cuenta_origen": origen[0],
        "lineas": lineas,
        "total": _num(sum((Decimal(str(l["monto"])) for l in lineas), Decimal(0))),
        "total_transferido": _num(total_transferido),
        "saldo_origen": _num(saldo_origen),
        "excede_saldo": total_transferido > saldo_origen,
        "transacciones": transacciones,
        "movimientos_subcuenta": sum(1 for l in lineas if l["subcuenta"]),
    }


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

TIPOS_TRANSACCION = ("INGRESO", "GASTO", "TRANSFERENCIA", "PAGO_TARJETA")
TIPOS_MOVIMIENTO_SUBCUENTA = ("ASIGNACION", "GASTO", "TRANSFERENCIA", "REPOSICION")
LIMITE_DESCRIPCION = 100
LIMITE_REFERENCIA = 30


def _texto_acotado(valor, maximo, etiqueta):
    valor = _texto(valor)
    if valor and len(valor) > maximo:
        raise _rechazar(422, "%s admite hasta %d caracteres." % (etiqueta, maximo))
    return valor


def _existe(cursor, sql, parametro, mensaje):
    cursor.execute(sql, (parametro,))
    if not cursor.fetchone():
        raise _rechazar(422, mensaje)


def _validar_transaccion(cursor, datos):
    """Valida una transaccion segun su tipo; devuelve los valores ya normalizados."""
    if datos.tipo not in TIPOS_TRANSACCION:
        raise _rechazar(422, "Tipo de movimiento no válido.")
    monto = _dinero(datos.monto)
    if monto <= 0:
        raise _rechazar(422, "El monto debe ser mayor que 0.")

    origen, destino = datos.id_cuenta_origen, datos.id_cuenta_destino
    if datos.tipo == "INGRESO":
        if destino is None:
            raise _rechazar(422, "Elige la cuenta que recibe el ingreso.")
        origen = None
    elif datos.tipo == "GASTO":
        if origen is None:
            raise _rechazar(422, "Elige la cuenta de la que sale el gasto.")
        destino = None
    else:
        if destino is None:
            raise _rechazar(422, "Elige la cuenta destino.")
        if datos.tipo == "TRANSFERENCIA" and origen is None:
            raise _rechazar(422, "Elige la cuenta origen.")
        if origen == destino:
            raise _rechazar(422, "La cuenta origen y la destino deben ser distintas.")

    tipos = {}
    for id_cuenta in (origen, destino):
        if id_cuenta is None:
            continue
        cursor.execute("SELECT tipo FROM cuentas WHERE id_cuenta = ?", (id_cuenta,))
        fila = cursor.fetchone()
        if not fila:
            raise _rechazar(422, "La cuenta elegida no existe.")
        tipos[id_cuenta] = fila[0]
    if datos.tipo == "PAGO_TARJETA" and tipos[destino] != TARJETA:
        raise _rechazar(422, "El destino de un pago a tarjeta debe ser una tarjeta de crédito.")

    if datos.id_categoria is not None:
        _existe(cursor, "SELECT 1 FROM categorias WHERE id_categoria = ?", datos.id_categoria,
                "La categoría elegida no existe.")
    descripcion = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION, "La descripción")
    referencia = _texto_acotado(datos.referencia, LIMITE_REFERENCIA, "La referencia")
    return datos.fecha, datos.tipo, monto, origen, destino, datos.id_categoria, descripcion, referencia


def _validar_movimiento_subcuenta(cursor, tipo, monto, origen, destino, categoria):
    """Reglas de cada tipo de movimiento de subcuenta; devuelve el monto normalizado."""
    if tipo not in TIPOS_MOVIMIENTO_SUBCUENTA:
        raise _rechazar(422, "Tipo de movimiento de subcuenta no válido.")
    monto = _dinero(monto)
    if monto <= 0:
        raise _rechazar(422, "El monto debe ser mayor que 0.")

    if tipo in ("ASIGNACION", "REPOSICION"):
        if destino is None:
            raise _rechazar(422, "Elige la subcuenta que recibe el saldo.")
        if origen is not None:
            raise _rechazar(422, "Este movimiento sale del saldo sin asignar, no de otra subcuenta.")
    elif tipo == "GASTO":
        if origen is None:
            raise _rechazar(422, "Elige la subcuenta de la que sale el gasto.")
        if destino is not None:
            raise _rechazar(422, "Un gasto no tiene subcuenta destino.")
    else:
        if origen is None or destino is None:
            raise _rechazar(422, "Elige la subcuenta origen y la destino.")
        if origen == destino:
            raise _rechazar(422, "La subcuenta origen y la destino deben ser distintas.")

    cuentas = set()
    for id_subcuenta in (origen, destino):
        if id_subcuenta is None:
            continue
        cursor.execute("SELECT id_cuenta FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))
        fila = cursor.fetchone()
        if not fila:
            raise _rechazar(422, "La subcuenta elegida no existe.")
        cuentas.add(fila[0])
    if len(cuentas) > 1:
        raise _rechazar(422, "Las dos subcuentas deben ser de la misma cuenta.")
    if categoria is not None:
        _existe(cursor, "SELECT 1 FROM categorias WHERE id_categoria = ?", categoria,
                "La categoría elegida no existe.")
    return monto


def _subcuenta_saldo(cursor, id_subcuenta):
    cursor.execute("SELECT nombre, ISNULL(saldo, 0), id_cuenta FROM subcuentas WHERE id_subcuenta = ?", (id_subcuenta,))
    return cursor.fetchone()


def _mover_saldo(cursor, id_subcuenta, delta):
    cursor.execute("UPDATE subcuentas SET saldo = ISNULL(saldo, 0) + ? WHERE id_subcuenta = ?", (delta, id_subcuenta))


def _aplicar_movimiento_subcuenta(cursor, monto, origen, destino):
    """Refleja el movimiento en subcuentas.saldo (que se mantiene desde la aplicacion)."""
    if origen is None:
        id_cuenta = _subcuenta_saldo(cursor, destino)[2]
        _exigir_disponible(monto, _sin_asignar(cursor, id_cuenta))
    else:
        nombre, saldo, _ = _subcuenta_saldo(cursor, origen)
        if saldo < monto:
            raise _rechazar(422, "«%s» solo tiene $%s." % (nombre, format(saldo, ",.2f")))
        _mover_saldo(cursor, origen, -monto)
    if destino is not None:
        _mover_saldo(cursor, destino, monto)


def _revertir_movimiento_subcuenta(cursor, monto, origen, destino):
    if destino is not None:
        nombre, saldo, _ = _subcuenta_saldo(cursor, destino)
        if saldo < monto:
            raise _rechazar(
                409, "No se puede deshacer el movimiento: «%s» ya no tiene $%s disponibles." % (nombre, format(monto, ",.2f"))
            )
        _mover_saldo(cursor, destino, -monto)
    if origen is not None:
        _mover_saldo(cursor, origen, monto)


def _crear_movimiento_subcuenta(cursor, fecha, tipo, monto, origen, destino, categoria, descripcion, id_transaccion):
    _aplicar_movimiento_subcuenta(cursor, monto, origen, destino)
    cursor.execute(
        "INSERT INTO movimientos_subcuenta "
        "(fecha, tipo, monto, id_subcuenta_origen, id_subcuenta_destino, id_categoria, descripcion, "
        "id_transaccion_relacionada) OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fecha, tipo, monto, origen, destino, categoria, descripcion, id_transaccion),
    )
    return fila_actual(cursor)


def _crear_movimiento_vinculado(cursor, datos, fecha, id_transaccion):
    monto = _validar_movimiento_subcuenta(
        cursor, datos.tipo, datos.monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino, datos.id_categoria
    )
    descripcion = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION, "La descripción")
    return _crear_movimiento_subcuenta(
        cursor, fecha, datos.tipo, monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino,
        datos.id_categoria, descripcion, id_transaccion,
    )


REFERENCIAS_MOVIMIENTO_SUBCUENTA = [
    ("obligación", "obligaciones", "SELECT COUNT(*) FROM obligaciones WHERE id_movimiento_subcuenta_origen = ?"),
    ("liquidación de obligación", "liquidaciones de obligaciones",
     "SELECT COUNT(*) FROM liquidaciones_obligacion WHERE id_movimiento_subcuenta = ?"),
]


@app.get("/transacciones")
def listar_transacciones():
    return fetch_all("SELECT * FROM transacciones ORDER BY fecha DESC")


@app.get("/transacciones/{id_transaccion}")
def obtener_transaccion(id_transaccion: int):
    return fetch_one("SELECT * FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))


@app.post("/transacciones")
def crear_transaccion(datos: TransaccionNueva):
    with transaccion() as cursor:
        fecha, tipo, monto, origen, destino, categoria, descripcion, referencia = _validar_transaccion(cursor, datos)
        cursor.execute(
            "INSERT INTO transacciones "
            "(fecha, tipo, monto, id_cuenta_origen, id_cuenta_destino, id_categoria, descripcion, referencia) "
            "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fecha, tipo, monto, origen, destino, categoria, descripcion, referencia),
        )
        creada = fila_actual(cursor)
        for movimiento in datos.movimientos_subcuenta:
            _crear_movimiento_vinculado(cursor, movimiento, fecha, creada["id_transaccion"])
        return creada


@app.put("/transacciones/{id_transaccion}")
def actualizar_transaccion(id_transaccion: int, datos: TransaccionEditar):
    quitar = sorted(set(datos.quitar_movimientos_subcuenta))
    for id_movimiento in quitar:
        bloqueo = _contar_referencias(REFERENCIAS_MOVIMIENTO_SUBCUENTA, (id_movimiento, id_movimiento))
        if bloqueo:
            raise _rechazar(409, _mensaje_bloqueo("uno de los movimientos de subcuenta", bloqueo))

    with transaccion() as cursor:
        cursor.execute("SELECT 1 FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))
        if not cursor.fetchone():
            raise _rechazar(404, "El movimiento no existe.")
        fecha, tipo, monto, origen, destino, categoria, descripcion, referencia = _validar_transaccion(cursor, datos)

        for id_movimiento in quitar:
            cursor.execute(
                "SELECT monto, id_subcuenta_origen, id_subcuenta_destino FROM movimientos_subcuenta "
                "WHERE id_movimiento_subcuenta = ? AND id_transaccion_relacionada = ?",
                (id_movimiento, id_transaccion),
            )
            fila = cursor.fetchone()
            if not fila:
                raise _rechazar(422, "Ese movimiento de subcuenta no pertenece a esta transacción.")
            _revertir_movimiento_subcuenta(cursor, fila[0], fila[1], fila[2])
            cursor.execute("DELETE FROM movimientos_subcuenta WHERE id_movimiento_subcuenta = ?", (id_movimiento,))

        cursor.execute(
            "UPDATE transacciones SET fecha = ?, tipo = ?, monto = ?, id_cuenta_origen = ?, "
            "id_cuenta_destino = ?, id_categoria = ?, descripcion = ?, referencia = ? "
            "OUTPUT INSERTED.* WHERE id_transaccion = ?",
            (fecha, tipo, monto, origen, destino, categoria, descripcion, referencia, id_transaccion),
        )
        actualizada = fila_actual(cursor)
        for movimiento in datos.agregar_movimientos_subcuenta:
            _crear_movimiento_vinculado(cursor, movimiento, fecha, id_transaccion)
        return actualizada


@app.delete("/transacciones/{id_transaccion}")
def eliminar_transaccion(id_transaccion: int):
    existente = fetch_one("SELECT descripcion FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))
    if not existente:
        raise _rechazar(404, "El movimiento no existe.")
    referencias = _contar_referencias(
        [
            ("obligación", "obligaciones", "SELECT COUNT(*) FROM obligaciones WHERE id_transaccion_origen = ?"),
            ("liquidación de obligación", "liquidaciones de obligaciones",
             "SELECT COUNT(*) FROM liquidaciones_obligacion WHERE id_transaccion = ?"),
            ("financiamiento", "financiamientos",
             "SELECT COUNT(*) FROM financiamientos WHERE id_transaccion_origen = ?"),
            ("cuota de financiamiento", "cuotas de financiamiento",
             "SELECT COUNT(*) FROM cuotas_financiamiento WHERE id_transaccion_pago = ?"),
        ],
        (id_transaccion,) * 4,
    )
    if referencias:
        nombre = "«%s»" % existente["descripcion"] if existente["descripcion"] else "el movimiento"
        raise _rechazar(409, _mensaje_bloqueo(nombre, referencias))
    # Los movimientos de subcuenta vinculados se conservan (solo pierden el vinculo): sus saldos son independientes.
    with transaccion() as cursor:
        cursor.execute(
            "UPDATE movimientos_subcuenta SET id_transaccion_relacionada = NULL WHERE id_transaccion_relacionada = ?",
            (id_transaccion,),
        )
        desvinculados = cursor.rowcount
        cursor.execute("DELETE FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))
    return {"eliminado": True, "movimientos_desvinculados": desvinculados}


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


def _validar_transaccion_relacionada(cursor, id_transaccion):
    if id_transaccion is not None:
        _existe(cursor, "SELECT 1 FROM transacciones WHERE id_transaccion = ?", id_transaccion,
                "La transferencia relacionada no existe.")


@app.post("/movimientos-subcuenta")
def crear_movimiento_subcuenta(datos: MovimientoSubcuenta):
    with transaccion() as cursor:
        monto = _validar_movimiento_subcuenta(
            cursor, datos.tipo, datos.monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino,
            datos.id_categoria,
        )
        _validar_transaccion_relacionada(cursor, datos.id_transaccion_relacionada)
        descripcion = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION, "La descripción")
        return _crear_movimiento_subcuenta(
            cursor, datos.fecha, datos.tipo, monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino,
            datos.id_categoria, descripcion, datos.id_transaccion_relacionada,
        )


@app.put("/movimientos-subcuenta/{id_movimiento}")
def actualizar_movimiento_subcuenta(id_movimiento: int, datos: MovimientoSubcuenta):
    with transaccion() as cursor:
        cursor.execute(
            "SELECT monto, id_subcuenta_origen, id_subcuenta_destino FROM movimientos_subcuenta "
            "WHERE id_movimiento_subcuenta = ?",
            (id_movimiento,),
        )
        anterior = cursor.fetchone()
        if not anterior:
            raise _rechazar(404, "El movimiento de subcuenta no existe.")
        _revertir_movimiento_subcuenta(cursor, anterior[0], anterior[1], anterior[2])
        monto = _validar_movimiento_subcuenta(
            cursor, datos.tipo, datos.monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino,
            datos.id_categoria,
        )
        _validar_transaccion_relacionada(cursor, datos.id_transaccion_relacionada)
        descripcion = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION, "La descripción")
        _aplicar_movimiento_subcuenta(cursor, monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino)
        cursor.execute(
            "UPDATE movimientos_subcuenta SET fecha = ?, tipo = ?, monto = ?, id_subcuenta_origen = ?, "
            "id_subcuenta_destino = ?, id_categoria = ?, descripcion = ?, id_transaccion_relacionada = ? "
            "OUTPUT INSERTED.* WHERE id_movimiento_subcuenta = ?",
            (datos.fecha, datos.tipo, monto, datos.id_subcuenta_origen, datos.id_subcuenta_destino,
             datos.id_categoria, descripcion, datos.id_transaccion_relacionada, id_movimiento),
        )
        return fila_actual(cursor)


@app.delete("/movimientos-subcuenta/{id_movimiento}")
def eliminar_movimiento_subcuenta(id_movimiento: int):
    bloqueo = _contar_referencias(REFERENCIAS_MOVIMIENTO_SUBCUENTA, (id_movimiento, id_movimiento))
    if bloqueo:
        raise _rechazar(409, _mensaje_bloqueo("este movimiento de subcuenta", bloqueo))
    with transaccion() as cursor:
        cursor.execute(
            "SELECT monto, id_subcuenta_origen, id_subcuenta_destino FROM movimientos_subcuenta "
            "WHERE id_movimiento_subcuenta = ?",
            (id_movimiento,),
        )
        fila = cursor.fetchone()
        if not fila:
            raise _rechazar(404, "El movimiento de subcuenta no existe.")
        _revertir_movimiento_subcuenta(cursor, fila[0], fila[1], fila[2])
        cursor.execute("DELETE FROM movimientos_subcuenta WHERE id_movimiento_subcuenta = ?", (id_movimiento,))
    return {"eliminado": True}


# ---------- movimientos (lectura para la pantalla Movimientos) ----------

def _pagina(limite, pagina):
    limite = min(max(limite, 1), 200)
    return limite, max(pagina, 0) * limite


@app.get("/movimientos-detalle")
def listar_movimientos_detalle(limite: int = 50, pagina: int = 0):
    """Transacciones de la mas reciente a la mas antigua, con nombres ya resueltos."""
    limite, desplazamiento = _pagina(limite, pagina)
    movimientos = fetch_all(
        "SELECT t.id_transaccion, t.fecha, t.tipo, t.monto, t.descripcion, t.referencia, "
        "categorias.nombre AS categoria, origen.nombre AS cuenta_origen, destino.nombre AS cuenta_destino, "
        "(SELECT COUNT(*) FROM movimientos_subcuenta m WHERE m.id_transaccion_relacionada = t.id_transaccion) "
        "AS movimientos_subcuenta "
        "FROM transacciones t "
        "LEFT JOIN categorias ON categorias.id_categoria = t.id_categoria "
        "LEFT JOIN cuentas origen ON origen.id_cuenta = t.id_cuenta_origen "
        "LEFT JOIN cuentas destino ON destino.id_cuenta = t.id_cuenta_destino "
        "ORDER BY t.fecha DESC, t.id_transaccion DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
        (desplazamiento, limite),
    )
    for m in movimientos:
        m["monto"] = _num(m["monto"])
    total = fetch_one("SELECT COUNT(*) AS total FROM transacciones")["total"]
    return {"total": total, "movimientos": movimientos}


@app.get("/movimientos-detalle/{id_transaccion}")
def obtener_movimiento_detalle(id_transaccion: int):
    """Una transaccion con los movimientos de subcuenta vinculados (para el formulario de edicion)."""
    transaccion_fila = fetch_one("SELECT * FROM transacciones WHERE id_transaccion = ?", (id_transaccion,))
    if not transaccion_fila:
        raise _rechazar(404, "El movimiento no existe.")
    transaccion_fila["monto"] = _num(transaccion_fila["monto"])
    transaccion_fila["movimientos_subcuenta"] = _movimientos_subcuenta_detalle(
        "WHERE m.id_transaccion_relacionada = ? ", (id_transaccion,)
    )
    return transaccion_fila


def _movimientos_subcuenta_detalle(filtro, parametros, orden="ORDER BY m.fecha DESC, m.id_movimiento_subcuenta DESC"):
    filas = fetch_all(
        "SELECT m.id_movimiento_subcuenta, m.fecha, m.tipo, m.monto, m.descripcion, m.id_transaccion_relacionada, "
        "m.id_subcuenta_origen, m.id_subcuenta_destino, m.id_categoria, categorias.nombre AS categoria, "
        "origen.nombre AS subcuenta_origen, destino.nombre AS subcuenta_destino, cuentas.nombre AS cuenta, "
        "t.descripcion AS transaccion "
        "FROM movimientos_subcuenta m "
        "LEFT JOIN categorias ON categorias.id_categoria = m.id_categoria "
        "LEFT JOIN subcuentas origen ON origen.id_subcuenta = m.id_subcuenta_origen "
        "LEFT JOIN subcuentas destino ON destino.id_subcuenta = m.id_subcuenta_destino "
        "LEFT JOIN cuentas ON cuentas.id_cuenta = COALESCE(destino.id_cuenta, origen.id_cuenta) "
        "LEFT JOIN transacciones t ON t.id_transaccion = m.id_transaccion_relacionada " + filtro + orden,
        parametros,
    )
    for f in filas:
        f["monto"] = _num(f["monto"])
    return filas


@app.get("/movimientos-subcuenta-detalle")
def listar_movimientos_subcuenta_detalle(limite: int = 50, pagina: int = 0):
    limite, desplazamiento = _pagina(limite, pagina)
    filas = _movimientos_subcuenta_detalle(
        "", (desplazamiento, limite),
        "ORDER BY m.fecha DESC, m.id_movimiento_subcuenta DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
    )
    total = fetch_one("SELECT COUNT(*) AS total FROM movimientos_subcuenta")["total"]
    return {"total": total, "movimientos": filas}


@app.get("/movimientos-opciones")
def obtener_opciones_movimiento():
    """Datos para los selectores de los formularios: cuentas (con subcuentas y saldos), categorias y recientes."""
    recientes = fetch_all(
        "SELECT TOP 60 id_transaccion, fecha, tipo, monto, descripcion FROM transacciones "
        "ORDER BY fecha DESC, id_transaccion DESC"
    )
    for r in recientes:
        r["monto"] = _num(r["monto"])
    return {
        "cuentas": _cuentas_detalle(),
        "categorias": fetch_all("SELECT id_categoria, nombre FROM categorias ORDER BY nombre"),
        "transacciones_recientes": recientes,
    }


# ---------- obligaciones ----------

TIPOS_OBLIGACION = ("POR_COBRAR", "POR_PAGAR", "REPOSICION")


def _liquidado(cursor, id_obligacion):
    cursor.execute("SELECT ISNULL(SUM(monto), 0) FROM liquidaciones_obligacion WHERE id_obligacion = ?", (id_obligacion,))
    return cursor.fetchone()[0]


def _validar_obligacion(cursor, datos, liquidado=Decimal(0)):
    if datos.tipo not in TIPOS_OBLIGACION:
        raise _rechazar(422, "Tipo de obligación no válido.")
    monto = _dinero(datos.monto)
    if monto <= 0:
        raise _rechazar(422, "El monto debe ser mayor que 0.")
    if monto < liquidado:
        raise _rechazar(422, "El monto no puede ser menor que lo ya liquidado ($%s)." % format(liquidado, ",.2f"))
    concepto = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION, "El concepto")
    if not concepto:
        raise _rechazar(422, "El concepto es obligatorio.")

    if datos.id_persona is not None:
        _existe(cursor, "SELECT 1 FROM personas WHERE id_persona = ?", datos.id_persona, "La persona elegida no existe.")
    elif datos.tipo != "REPOSICION":
        raise _rechazar(422, "Elige la persona.")

    cursor.execute("SELECT tipo FROM cuentas WHERE id_cuenta = ?", (datos.id_cuenta_destino_resolucion,))
    cuenta = cursor.fetchone()
    if not cuenta:
        raise _rechazar(422, "La cuenta destino no existe.")
    if datos.id_subcuenta_destino_resolucion is not None:
        if cuenta[0] == TARJETA:
            raise _rechazar(422, "Una tarjeta de crédito no tiene subcuentas.")
        cursor.execute(
            "SELECT id_cuenta FROM subcuentas WHERE id_subcuenta = ?", (datos.id_subcuenta_destino_resolucion,)
        )
        subcuenta = cursor.fetchone()
        if not subcuenta:
            raise _rechazar(422, "La subcuenta destino no existe.")
        if subcuenta[0] != datos.id_cuenta_destino_resolucion:
            raise _rechazar(422, "La subcuenta no pertenece a la cuenta destino.")
    if datos.id_transaccion_origen is not None:
        _existe(cursor, "SELECT 1 FROM transacciones WHERE id_transaccion = ?", datos.id_transaccion_origen,
                "La transacción de origen no existe.")
    if datos.id_movimiento_subcuenta_origen is not None:
        _existe(cursor, "SELECT 1 FROM movimientos_subcuenta WHERE id_movimiento_subcuenta = ?",
                datos.id_movimiento_subcuenta_origen, "El movimiento de subcuenta de origen no existe.")
    return monto, concepto


@app.get("/obligaciones")
def listar_obligaciones():
    return fetch_all("SELECT * FROM obligaciones ORDER BY fecha_creacion DESC")


@app.get("/obligaciones/{id_obligacion}")
def obtener_obligacion(id_obligacion: int):
    return fetch_one("SELECT * FROM obligaciones WHERE id_obligacion = ?", (id_obligacion,))


@app.post("/obligaciones")
def crear_obligacion(datos: Obligacion):
    with transaccion() as cursor:
        monto, concepto = _validar_obligacion(cursor, datos)
        cursor.execute(
            "INSERT INTO obligaciones (fecha_creacion, tipo, monto, descripcion, id_persona, id_transaccion_origen, "
            "id_movimiento_subcuenta_origen, id_cuenta_destino_resolucion, id_subcuenta_destino_resolucion) "
            "OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datos.fecha_creacion, datos.tipo, monto, concepto, datos.id_persona, datos.id_transaccion_origen,
             datos.id_movimiento_subcuenta_origen, datos.id_cuenta_destino_resolucion,
             datos.id_subcuenta_destino_resolucion),
        )
        return fila_actual(cursor)


@app.put("/obligaciones/{id_obligacion}")
def actualizar_obligacion(id_obligacion: int, datos: Obligacion):
    with transaccion() as cursor:
        cursor.execute("SELECT 1 FROM obligaciones WHERE id_obligacion = ?", (id_obligacion,))
        if not cursor.fetchone():
            raise _rechazar(404, "La obligación no existe.")
        monto, concepto = _validar_obligacion(cursor, datos, _liquidado(cursor, id_obligacion))
        cursor.execute(
            "UPDATE obligaciones SET fecha_creacion = ?, tipo = ?, monto = ?, descripcion = ?, id_persona = ?, "
            "id_transaccion_origen = ?, id_movimiento_subcuenta_origen = ?, id_cuenta_destino_resolucion = ?, "
            "id_subcuenta_destino_resolucion = ? OUTPUT INSERTED.* WHERE id_obligacion = ?",
            (datos.fecha_creacion, datos.tipo, monto, concepto, datos.id_persona, datos.id_transaccion_origen,
             datos.id_movimiento_subcuenta_origen, datos.id_cuenta_destino_resolucion,
             datos.id_subcuenta_destino_resolucion, id_obligacion),
        )
        return fila_actual(cursor)


@app.delete("/obligaciones/{id_obligacion}")
def eliminar_obligacion(id_obligacion: int):
    if not fetch_one("SELECT 1 AS existe FROM obligaciones WHERE id_obligacion = ?", (id_obligacion,)):
        raise _rechazar(404, "La obligación no existe.")
    # Sus liquidaciones solo guardan vinculos a movimientos: se borran con ella, los movimientos no se tocan.
    with transaccion() as cursor:
        cursor.execute("DELETE FROM liquidaciones_obligacion WHERE id_obligacion = ?", (id_obligacion,))
        liquidaciones = cursor.rowcount
        cursor.execute("DELETE FROM obligaciones WHERE id_obligacion = ?", (id_obligacion,))
    return {"eliminado": True, "liquidaciones_eliminadas": liquidaciones}


def _obligaciones_detalle(id_obligacion=None):
    filtro = "WHERE o.id_obligacion = ? " if id_obligacion is not None else ""
    filas = fetch_all(
        "SELECT o.id_obligacion, o.tipo, o.descripcion, o.fecha_creacion, o.monto, e.monto_pendiente, "
        "e.monto_liquidado, e.resuelta, o.id_persona, p.nombre AS persona, o.id_cuenta_destino_resolucion, "
        "c.nombre AS cuenta_destino, o.id_subcuenta_destino_resolucion, s.nombre AS subcuenta_destino, "
        "o.id_transaccion_origen, o.id_movimiento_subcuenta_origen "
        "FROM obligaciones o JOIN vw_estado_obligaciones e ON e.id_obligacion = o.id_obligacion "
        "LEFT JOIN personas p ON p.id_persona = o.id_persona "
        "JOIN cuentas c ON c.id_cuenta = o.id_cuenta_destino_resolucion "
        "LEFT JOIN subcuentas s ON s.id_subcuenta = o.id_subcuenta_destino_resolucion " + filtro +
        "ORDER BY e.resuelta, o.fecha_creacion DESC, o.id_obligacion DESC",
        (id_obligacion,) if id_obligacion is not None else (),
    )
    for f in filas:
        f["concepto"] = _limpiar_descripcion(f.pop("descripcion"))
        f["resuelta"] = bool(f["resuelta"])
        for campo in ("monto", "monto_pendiente", "monto_liquidado"):
            f[campo] = _num(f[campo])
    return filas


@app.get("/obligaciones-detalle")
def listar_obligaciones_detalle():
    """Pantalla Obligaciones: lo pendiente separado en te deben / debes / reposiciones, y las liquidadas aparte."""
    filas = _obligaciones_detalle()
    pendientes = [f for f in filas if not f["resuelta"]]
    por_cobrar = [f for f in pendientes if f["tipo"] == "POR_COBRAR"]
    por_pagar = [f for f in pendientes if f["tipo"] == "POR_PAGAR"]
    reposiciones = [f for f in pendientes if f["tipo"] == "REPOSICION"]
    suma = lambda lista: round(sum(f["monto_pendiente"] for f in lista), 2)
    return {
        "por_cobrar": suma(por_cobrar),
        "por_pagar": suma(por_pagar),
        "balance": round(suma(por_cobrar) - suma(por_pagar), 2),
        "reposicion_pendiente": suma(reposiciones),
        "te_deben": por_cobrar,
        "debes": por_pagar,
        "reposiciones": reposiciones,
        "liquidadas": [f for f in filas if f["resuelta"]],
    }


@app.get("/obligaciones-detalle/{id_obligacion}")
def obtener_obligacion_detalle(id_obligacion: int):
    filas = _obligaciones_detalle(id_obligacion)
    if not filas:
        raise _rechazar(404, "La obligación no existe.")
    obligacion = filas[0]
    obligacion["liquidaciones"] = fetch_all(
        "SELECT id_liquidacion, fecha, monto, descripcion, id_transaccion, id_movimiento_subcuenta "
        "FROM liquidaciones_obligacion WHERE id_obligacion = ? ORDER BY fecha DESC, id_liquidacion DESC",
        (id_obligacion,),
    )
    for liquidacion in obligacion["liquidaciones"]:
        liquidacion["monto"] = _num(liquidacion["monto"])
    # Los vinculos pueden no estar entre los "recientes" de las listas: se devuelven completos.
    obligacion["transaccion_origen"] = (
        fetch_one("SELECT id_transaccion, fecha, tipo, monto, descripcion FROM transacciones WHERE id_transaccion = ?",
                  (obligacion["id_transaccion_origen"],))
        if obligacion["id_transaccion_origen"] else None
    )
    if obligacion["transaccion_origen"]:
        obligacion["transaccion_origen"]["monto"] = _num(obligacion["transaccion_origen"]["monto"])
    movimientos = (
        _movimientos_subcuenta_detalle("WHERE m.id_movimiento_subcuenta = ? ", (obligacion["id_movimiento_subcuenta_origen"],))
        if obligacion["id_movimiento_subcuenta_origen"] else []
    )
    obligacion["movimiento_subcuenta_origen"] = movimientos[0] if movimientos else None
    return obligacion


@app.get("/obligaciones-opciones")
def obtener_opciones_obligacion():
    """Selectores de los formularios de obligacion y liquidacion."""
    recientes = fetch_all(
        "SELECT TOP 60 id_transaccion, fecha, tipo, monto, descripcion FROM transacciones "
        "ORDER BY fecha DESC, id_transaccion DESC"
    )
    for r in recientes:
        r["monto"] = _num(r["monto"])
    return {
        "personas": fetch_all("SELECT id_persona, nombre FROM personas ORDER BY nombre"),
        "cuentas": _cuentas_detalle(),
        "transacciones_recientes": recientes,
        "movimientos_subcuenta_recientes": _movimientos_subcuenta_detalle(
            "", (0, 60), "ORDER BY m.fecha DESC, m.id_movimiento_subcuenta DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        ),
    }


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
    monto = _dinero(liquidacion.monto)
    if monto <= 0:
        raise _rechazar(422, "El monto debe ser mayor que 0.")
    descripcion = _texto_acotado(liquidacion.descripcion, LIMITE_DESCRIPCION, "La descripción")
    with transaccion() as cursor:
        cursor.execute("SELECT monto FROM obligaciones WHERE id_obligacion = ?", (liquidacion.id_obligacion,))
        fila = cursor.fetchone()
        if not fila:
            raise _rechazar(404, "La obligación no existe.")
        pendiente = fila[0] - _liquidado(cursor, liquidacion.id_obligacion)
        if monto > pendiente:
            raise _rechazar(422, "Solo quedan $%s pendientes en esta obligación." % format(max(pendiente, Decimal(0)), ",.2f"))
        if liquidacion.id_transaccion is not None:
            _existe(cursor, "SELECT 1 FROM transacciones WHERE id_transaccion = ?", liquidacion.id_transaccion,
                    "La transacción relacionada no existe.")
        if liquidacion.id_movimiento_subcuenta is not None:
            _existe(cursor, "SELECT 1 FROM movimientos_subcuenta WHERE id_movimiento_subcuenta = ?",
                    liquidacion.id_movimiento_subcuenta, "El movimiento de subcuenta relacionado no existe.")
        cursor.execute(
            "INSERT INTO liquidaciones_obligacion (id_obligacion, fecha, monto, id_transaccion, "
            "id_movimiento_subcuenta, descripcion) OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?)",
            (liquidacion.id_obligacion, liquidacion.fecha, monto, liquidacion.id_transaccion,
             liquidacion.id_movimiento_subcuenta, descripcion),
        )
        return fila_actual(cursor)


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


# ---------- financiamientos ----------

MAX_CUOTAS = 360


def _sumar_meses(fecha, meses):
    indice = fecha.month - 1 + meses
    anio, mes = fecha.year + indice // 12, indice % 12 + 1
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, min(fecha.day, ultimo_dia))


def _calendario(fecha_inicio, monto_total, numero_cuotas):
    """Cuotas mensuales iguales; la primera vence un mes despues del inicio y la ultima absorbe el redondeo."""
    monto_total = _dinero(monto_total)
    base = (monto_total / numero_cuotas).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    cuotas = []
    for numero in range(1, numero_cuotas + 1):
        monto = base if numero < numero_cuotas else monto_total - base * (numero_cuotas - 1)
        cuotas.append((numero, _sumar_meses(fecha_inicio, numero), monto))
    return cuotas


def _validar_financiamiento(cursor, datos):
    descripcion = _texto_acotado(datos.descripcion, LIMITE_DESCRIPCION, "La descripción")
    if not descripcion:
        raise _rechazar(422, "La descripción es obligatoria.")
    monto = _dinero(datos.monto_total)
    if monto <= 0:
        raise _rechazar(422, "El monto total debe ser mayor que 0.")
    if not (1 <= datos.numero_cuotas <= MAX_CUOTAS):
        raise _rechazar(422, "El número de cuotas debe estar entre 1 y %d." % MAX_CUOTAS)
    tasa = None
    if datos.tasa_interes is not None:
        tasa = _dinero(datos.tasa_interes)
        if not (0 <= tasa <= 100):
            raise _rechazar(422, "La tasa de interés debe estar entre 0 y 100 %.")
    if datos.id_transaccion_origen is not None:
        _existe(cursor, "SELECT 1 FROM transacciones WHERE id_transaccion = ?", datos.id_transaccion_origen,
                "La transacción de origen no existe.")
    return descripcion, monto, tasa


@app.get("/financiamientos")
def listar_financiamientos():
    return fetch_all("SELECT * FROM financiamientos ORDER BY fecha_inicio DESC")


@app.get("/financiamientos/{id_financiamiento}")
def obtener_financiamiento(id_financiamiento: int):
    return fetch_one(
        "SELECT * FROM financiamientos WHERE id_financiamiento = ?",
        (id_financiamiento,),
    )


@app.post("/financiamientos/calendario")
def vista_previa_calendario(datos: CalendarioFinanciamiento):
    """El calendario que se generaria (sin guardar nada), para mostrarlo en el formulario."""
    if _dinero(datos.monto_total) <= 0 or not (1 <= datos.numero_cuotas <= MAX_CUOTAS):
        raise _rechazar(422, "Indica un monto total y un número de cuotas válidos.")
    return {
        "cuotas": [
            {"numero_cuota": n, "fecha_vencimiento": f, "monto": _num(m)}
            for n, f, m in _calendario(datos.fecha_inicio, datos.monto_total, datos.numero_cuotas)
        ]
    }


@app.post("/financiamientos")
def crear_financiamiento(datos: FinanciamientoNuevo):
    with transaccion() as cursor:
        descripcion, monto, tasa = _validar_financiamiento(cursor, datos)
        cursor.execute(
            "INSERT INTO financiamientos (descripcion, fecha_inicio, monto_total, numero_cuotas, tasa_interes, "
            "id_transaccion_origen) OUTPUT INSERTED.* VALUES (?, ?, ?, ?, ?, ?)",
            (descripcion, datos.fecha_inicio, monto, datos.numero_cuotas, tasa, datos.id_transaccion_origen),
        )
        creado = fila_actual(cursor)
        if datos.generar_cuotas:
            for numero, vencimiento, monto_cuota in _calendario(datos.fecha_inicio, monto, datos.numero_cuotas):
                cursor.execute(
                    "INSERT INTO cuotas_financiamiento (id_financiamiento, numero_cuota, fecha_vencimiento, monto) "
                    "VALUES (?, ?, ?, ?)",
                    (creado["id_financiamiento"], numero, vencimiento, monto_cuota),
                )
        return creado


@app.put("/financiamientos/{id_financiamiento}")
def actualizar_financiamiento(id_financiamiento: int, datos: Financiamiento):
    with transaccion() as cursor:
        descripcion, monto, tasa = _validar_financiamiento(cursor, datos)
        # Cambiar monto o numero de cuotas no reordena las cuotas que ya existen.
        cursor.execute(
            "UPDATE financiamientos SET descripcion = ?, fecha_inicio = ?, monto_total = ?, numero_cuotas = ?, "
            "tasa_interes = ?, id_transaccion_origen = ? OUTPUT INSERTED.* WHERE id_financiamiento = ?",
            (descripcion, datos.fecha_inicio, monto, datos.numero_cuotas, tasa, datos.id_transaccion_origen,
             id_financiamiento),
        )
        actualizado = fila_actual(cursor)
        if not actualizado:
            raise _rechazar(404, "El financiamiento no existe.")
        return actualizado


@app.delete("/financiamientos/{id_financiamiento}")
def eliminar_financiamiento(id_financiamiento: int):
    if not fetch_one("SELECT 1 AS existe FROM financiamientos WHERE id_financiamiento = ?", (id_financiamiento,)):
        raise _rechazar(404, "El financiamiento no existe.")
    # Sus cuotas se borran con el; los pagos ya registrados como transacciones no se tocan.
    with transaccion() as cursor:
        cursor.execute("DELETE FROM cuotas_financiamiento WHERE id_financiamiento = ?", (id_financiamiento,))
        cuotas = cursor.rowcount
        cursor.execute("DELETE FROM financiamientos WHERE id_financiamiento = ?", (id_financiamiento,))
    return {"eliminado": True, "cuotas_eliminadas": cuotas}


def _financiamientos_detalle(id_financiamiento=None):
    filtro = "WHERE f.id_financiamiento = ? " if id_financiamiento is not None else ""
    filas = fetch_all(
        "SELECT f.id_financiamiento, f.descripcion, f.fecha_inicio, f.monto_total, f.numero_cuotas, f.tasa_interes, "
        "f.id_transaccion_origen, tarjeta.nombre AS tarjeta, "
        "(SELECT COUNT(*) FROM cuotas_financiamiento q WHERE q.id_financiamiento = f.id_financiamiento) AS cuotas_total, "
        "(SELECT COUNT(*) FROM cuotas_financiamiento q WHERE q.id_financiamiento = f.id_financiamiento "
        "AND q.fecha_pago IS NOT NULL) AS cuotas_pagadas, "
        "(SELECT ISNULL(SUM(q.monto), 0) FROM cuotas_financiamiento q WHERE q.id_financiamiento = f.id_financiamiento "
        "AND q.fecha_pago IS NULL) AS monto_restante "
        "FROM financiamientos f "
        "LEFT JOIN transacciones t ON t.id_transaccion = f.id_transaccion_origen "
        "LEFT JOIN cuentas tarjeta ON tarjeta.id_cuenta = t.id_cuenta_origen " + filtro +
        "ORDER BY f.fecha_inicio DESC, f.id_financiamiento DESC",
        (id_financiamiento,) if id_financiamiento is not None else (),
    )
    for f in filas:
        f["monto_total"] = _num(f["monto_total"])
        f["monto_restante"] = _num(f["monto_restante"])
        f["tasa_interes"] = _num(f["tasa_interes"]) if f["tasa_interes"] is not None else None
    return filas


@app.get("/financiamientos-detalle")
def listar_financiamientos_detalle():
    return _financiamientos_detalle()


def _cuotas_de(id_financiamiento):
    cuotas = fetch_all(
        "SELECT q.id_cuota_financiamiento, q.numero_cuota, q.fecha_vencimiento, q.monto, q.fecha_pago, "
        "q.id_transaccion_pago, t.descripcion AS transaccion_pago, t.fecha AS fecha_transaccion_pago "
        "FROM cuotas_financiamiento q LEFT JOIN transacciones t ON t.id_transaccion = q.id_transaccion_pago "
        "WHERE q.id_financiamiento = ? ORDER BY q.numero_cuota, q.id_cuota_financiamiento",
        (id_financiamiento,),
    )
    proxima = None
    for c in cuotas:
        c["monto"] = _num(c["monto"])
        c["pagada"] = c["fecha_pago"] is not None
        if not c["pagada"] and (proxima is None or c["fecha_vencimiento"] < proxima["fecha_vencimiento"]):
            proxima = c
    for c in cuotas:
        c["es_proxima"] = c is proxima
    return cuotas


@app.get("/financiamientos-detalle/{id_financiamiento}")
def obtener_financiamiento_detalle(id_financiamiento: int):
    filas = _financiamientos_detalle(id_financiamiento)
    if not filas:
        raise _rechazar(404, "El financiamiento no existe.")
    return {**filas[0], "cuotas": _cuotas_de(id_financiamiento)}


@app.get("/financiamientos-opciones")
def obtener_opciones_financiamiento():
    """Compras en tarjeta (candidatas a origen de un financiamiento) y transacciones recientes (para pagos)."""
    compras = fetch_all(
        "SELECT TOP 100 t.id_transaccion, t.fecha, t.monto, t.descripcion, c.nombre AS tarjeta FROM transacciones t "
        "JOIN cuentas c ON c.id_cuenta = t.id_cuenta_origen WHERE c.tipo = 'TARJETA_CREDITO' AND t.tipo = 'GASTO' "
        "ORDER BY t.fecha DESC, t.id_transaccion DESC"
    )
    recientes = fetch_all(
        "SELECT TOP 60 id_transaccion, fecha, tipo, monto, descripcion FROM transacciones "
        "ORDER BY fecha DESC, id_transaccion DESC"
    )
    for fila in compras + recientes:
        fila["monto"] = _num(fila["monto"])
    return {"compras_tarjeta": compras, "transacciones_recientes": recientes}


# ---------- cuotas_financiamiento ----------

def _validar_cuota(cursor, datos, id_cuota=None):
    cursor.execute("SELECT 1 FROM financiamientos WHERE id_financiamiento = ?", (datos.id_financiamiento,))
    if not cursor.fetchone():
        raise _rechazar(422, "El financiamiento elegido no existe.")
    if datos.numero_cuota < 1:
        raise _rechazar(422, "El número de cuota debe ser 1 o mayor.")
    monto = _dinero(datos.monto)
    if monto <= 0:
        raise _rechazar(422, "El monto debe ser mayor que 0.")
    cursor.execute(
        "SELECT 1 FROM cuotas_financiamiento WHERE id_financiamiento = ? AND numero_cuota = ? "
        "AND id_cuota_financiamiento <> ?",
        (datos.id_financiamiento, datos.numero_cuota, id_cuota if id_cuota is not None else 0),
    )
    if cursor.fetchone():
        raise _rechazar(409, "Ya existe la cuota %d en este financiamiento." % datos.numero_cuota)
    return monto


@app.get("/cuotas-financiamiento")
def listar_cuotas_financiamiento():
    return fetch_all("SELECT * FROM cuotas_financiamiento ORDER BY fecha_vencimiento")


@app.get("/cuotas-financiamiento/{id_cuota}")
def obtener_cuota_financiamiento(id_cuota: int):
    return fetch_one(
        "SELECT * FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?",
        (id_cuota,),
    )


@app.get("/cuotas-detalle/{id_cuota}")
def obtener_cuota_detalle(id_cuota: int):
    """Una cuota con el nombre de su financiamiento y cuantas cuotas tiene, para las pantallas de cuota."""
    cuota = fetch_one(
        "SELECT q.id_cuota_financiamiento, q.id_financiamiento, q.numero_cuota, q.fecha_vencimiento, q.monto, "
        "q.fecha_pago, q.id_transaccion_pago, f.descripcion AS financiamiento, f.numero_cuotas, "
        "t.descripcion AS transaccion_pago, t.fecha AS fecha_transaccion_pago "
        "FROM cuotas_financiamiento q JOIN financiamientos f ON f.id_financiamiento = q.id_financiamiento "
        "LEFT JOIN transacciones t ON t.id_transaccion = q.id_transaccion_pago WHERE q.id_cuota_financiamiento = ?",
        (id_cuota,),
    )
    if not cuota:
        raise _rechazar(404, "La cuota no existe.")
    cuota["monto"] = _num(cuota["monto"])
    cuota["pagada"] = cuota["fecha_pago"] is not None
    return cuota


@app.post("/cuotas-financiamiento")
def crear_cuota_financiamiento(cuota: CuotaFinanciamiento):
    # Una cuota nueva siempre nace pendiente: el pago se registra con /pagar.
    with transaccion() as cursor:
        monto = _validar_cuota(cursor, cuota)
        cursor.execute(
            "INSERT INTO cuotas_financiamiento (id_financiamiento, numero_cuota, fecha_vencimiento, monto) "
            "OUTPUT INSERTED.* VALUES (?, ?, ?, ?)",
            (cuota.id_financiamiento, cuota.numero_cuota, cuota.fecha_vencimiento, monto),
        )
        return fila_actual(cursor)


@app.put("/cuotas-financiamiento/{id_cuota}")
def actualizar_cuota_financiamiento(id_cuota: int, cuota: CuotaFinanciamiento):
    # Solo cambia numero, monto, vencimiento y financiamiento: el estado de pago se maneja aparte.
    with transaccion() as cursor:
        cursor.execute("SELECT 1 FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?", (id_cuota,))
        if not cursor.fetchone():
            raise _rechazar(404, "La cuota no existe.")
        monto = _validar_cuota(cursor, cuota, id_cuota)
        cursor.execute(
            "UPDATE cuotas_financiamiento SET id_financiamiento = ?, numero_cuota = ?, fecha_vencimiento = ?, "
            "monto = ? OUTPUT INSERTED.* WHERE id_cuota_financiamiento = ?",
            (cuota.id_financiamiento, cuota.numero_cuota, cuota.fecha_vencimiento, monto, id_cuota),
        )
        return fila_actual(cursor)


@app.delete("/cuotas-financiamiento/{id_cuota}")
def eliminar_cuota_financiamiento(id_cuota: int):
    if not fetch_one("SELECT 1 AS existe FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?", (id_cuota,)):
        raise _rechazar(404, "La cuota no existe.")
    execute("DELETE FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?", (id_cuota,))
    return {"eliminado": True}


@app.post("/cuotas-financiamiento/{id_cuota}/pagar")
def pagar_cuota_financiamiento(id_cuota: int, pago: PagoCuota):
    with transaccion() as cursor:
        cursor.execute(
            "SELECT monto, fecha_pago FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?", (id_cuota,)
        )
        cuota = cursor.fetchone()
        if not cuota:
            raise _rechazar(404, "La cuota no existe.")
        if cuota[1] is not None:
            raise _rechazar(409, "Esta cuota ya está pagada.")
        monto = _dinero(pago.monto) if pago.monto is not None else cuota[0]
        if monto <= 0:
            raise _rechazar(422, "El monto debe ser mayor que 0.")
        if pago.id_transaccion_pago is not None:
            _existe(cursor, "SELECT 1 FROM transacciones WHERE id_transaccion = ?", pago.id_transaccion_pago,
                    "La transacción de pago no existe.")
        # Si se paga un monto distinto, la cuota queda con lo que realmente se pago.
        cursor.execute(
            "UPDATE cuotas_financiamiento SET fecha_pago = ?, id_transaccion_pago = ?, monto = ? "
            "OUTPUT INSERTED.* WHERE id_cuota_financiamiento = ?",
            (pago.fecha_pago, pago.id_transaccion_pago, monto, id_cuota),
        )
        return fila_actual(cursor)


@app.post("/cuotas-financiamiento/{id_cuota}/deshacer-pago")
def deshacer_pago_cuota(id_cuota: int):
    with transaccion() as cursor:
        cursor.execute("SELECT fecha_pago FROM cuotas_financiamiento WHERE id_cuota_financiamiento = ?", (id_cuota,))
        cuota = cursor.fetchone()
        if not cuota:
            raise _rechazar(404, "La cuota no existe.")
        if cuota[0] is None:
            raise _rechazar(409, "Esta cuota no está pagada.")
        cursor.execute(
            "UPDATE cuotas_financiamiento SET fecha_pago = NULL, id_transaccion_pago = NULL "
            "OUTPUT INSERTED.* WHERE id_cuota_financiamiento = ?",
            (id_cuota,),
        )
        return fila_actual(cursor)


# ---------- vistas (solo lectura) ----------

@app.get("/saldos-cuentas")
def listar_saldos_cuentas():
    return fetch_all("SELECT * FROM vw_saldo_cuentas")


@app.get("/estado-obligaciones")
def listar_estado_obligaciones():
    return fetch_all("SELECT * FROM vw_estado_obligaciones")


# ---------- resumen (pantalla Main) ----------

MAX_SUBCUENTAS = 3
MAX_CATEGORIAS = 5
MAX_PERSONAS = 4
MAX_ACTIVIDAD = 5


def _num(valor):
    return float(round(Decimal(valor or 0), 2))


def _porcentaje(parte, total):
    if not total:
        return None
    return round(float(parte) / float(total) * 100, 1)


def _variacion_pct(actual, anterior):
    # Sobre el valor absoluto del anterior para que el signo siga indicando subida o bajada.
    if not anterior:
        return None
    return round(float(actual - anterior) / abs(float(anterior)) * 100, 1)


def _limpiar_descripcion(texto):
    if not texto:
        return None
    return re.sub(r"\s*\((?:Activa|Inactiva)\)\s*$", "", texto).strip()


def _saldos_antes_de(fecha):
    # Misma logica que vw_saldo_cuentas, pero solo con transacciones anteriores a `fecha`.
    filas = fetch_all(
        "SELECT cuentas.tipo, "
        "SUM(CASE WHEN cuentas.tipo = 'TARJETA_CREDITO' "
        "THEN salidas.total - entradas.total ELSE entradas.total - salidas.total END) AS saldo "
        "FROM cuentas "
        "CROSS APPLY (SELECT ISNULL(SUM(monto), 0) AS total FROM transacciones "
        "WHERE id_cuenta_destino = cuentas.id_cuenta AND fecha < ?) entradas "
        "CROSS APPLY (SELECT ISNULL(SUM(monto), 0) AS total FROM transacciones "
        "WHERE id_cuenta_origen = cuentas.id_cuenta AND fecha < ?) salidas "
        "GROUP BY cuentas.tipo",
        (fecha, fecha),
    )
    return {
        "ahorro": sum((f["saldo"] for f in filas if f["tipo"] != TARJETA), Decimal(0)),
        "tarjetas": sum((f["saldo"] for f in filas if f["tipo"] == TARJETA), Decimal(0)),
    }


def _resumen_kpis(cuentas, inicio_mes):
    ahorro = sum((c["saldo"] for c in cuentas if c["tipo"] != TARJETA), Decimal(0))
    tarjetas = [c for c in cuentas if c["tipo"] == TARJETA]
    deuda = sum((c["saldo"] for c in tarjetas), Decimal(0))
    limite = sum((c["limite_credito"] or Decimal(0) for c in tarjetas), Decimal(0))
    previo = _saldos_antes_de(inicio_mes)
    return {
        "ahorro": {
            "total": _num(ahorro),
            "cuentas": sum(1 for c in cuentas if c["tipo"] != TARJETA),
            "variacion_pct": _variacion_pct(ahorro, previo["ahorro"]),
        },
        "deuda_tarjetas": {
            "total": _num(deuda),
            "limite_total": _num(limite),
            "uso_pct": _porcentaje(deuda, limite),
            "variacion_pct": _variacion_pct(deuda, previo["tarjetas"]),
        },
        "patrimonio_neto": {
            "total": _num(ahorro - deuda),
            "variacion_pct": _variacion_pct(ahorro - deuda, previo["ahorro"] - previo["tarjetas"]),
        },
    }


def _desglose_subcuentas(saldo_cuenta, subcuentas):
    if not subcuentas:
        return None
    segmentos = [
        {"tipo": "subcuenta", "nombre": s["nombre"] or "Sin nombre", "saldo": s["saldo"]}
        for s in subcuentas[:MAX_SUBCUENTAS]
    ]
    resto = subcuentas[MAX_SUBCUENTAS:]
    if resto:
        segmentos.append(
            {"tipo": "otras", "nombre": "Otras (%d)" % len(resto),
             "saldo": sum((s["saldo"] for s in resto), Decimal(0))}
        )
    sin_asignar = saldo_cuenta - sum((s["saldo"] for s in subcuentas), Decimal(0))
    if sin_asignar > 0:
        segmentos.append({"tipo": "sin_asignar", "nombre": "Sin asignar", "saldo": sin_asignar})
    total = sum((s["saldo"] for s in segmentos), Decimal(0))
    return [
        {"tipo": s["tipo"], "nombre": s["nombre"], "saldo": _num(s["saldo"]),
         "porcentaje": _porcentaje(s["saldo"], total)}
        for s in segmentos
    ]


def _resumen_cuentas(cuentas):
    subcuentas_por_cuenta = {}
    for s in fetch_all(
        "SELECT id_cuenta, nombre, saldo FROM subcuentas "
        "WHERE id_cuenta IS NOT NULL AND saldo > 0 ORDER BY saldo DESC"
    ):
        subcuentas_por_cuenta.setdefault(s["id_cuenta"], []).append(s)

    resultado = []
    for c in cuentas:
        cuenta = {
            "id_cuenta": c["id_cuenta"],
            "nombre": c["nombre"],
            "entidad": c["entidad"],
            "tipo": c["tipo"],
            "saldo": _num(c["saldo"]),
        }
        if c["tipo"] == TARJETA:
            limite = c["limite_credito"]
            cuenta["limite_credito"] = _num(limite) if limite is not None else None
            cuenta["disponible"] = _num(limite - c["saldo"]) if limite is not None else None
            cuenta["uso_pct"] = _porcentaje(c["saldo"], limite)
        else:
            cuenta["subcuentas"] = _desglose_subcuentas(
                c["saldo"], subcuentas_por_cuenta.get(c["id_cuenta"])
            )
        resultado.append(cuenta)
    return resultado


def _resumen_actividad():
    return fetch_all(
        "SELECT TOP %d t.id_transaccion, t.fecha, t.tipo, t.monto, t.descripcion, "
        "categorias.nombre AS categoria, origen.nombre AS cuenta_origen, "
        "destino.nombre AS cuenta_destino "
        "FROM transacciones t "
        "LEFT JOIN categorias ON categorias.id_categoria = t.id_categoria "
        "LEFT JOIN cuentas origen ON origen.id_cuenta = t.id_cuenta_origen "
        "LEFT JOIN cuentas destino ON destino.id_cuenta = t.id_cuenta_destino "
        "ORDER BY t.fecha DESC, t.id_transaccion DESC" % MAX_ACTIVIDAD
    )


def _resumen_gastos(inicio_mes, inicio_mes_siguiente):
    filas = fetch_all(
        "SELECT categorias.id_categoria, categorias.nombre, SUM(t.monto) AS total "
        "FROM transacciones t "
        "LEFT JOIN categorias ON categorias.id_categoria = t.id_categoria "
        "WHERE t.tipo = 'GASTO' AND t.fecha >= ? AND t.fecha < ? "
        "GROUP BY categorias.id_categoria, categorias.nombre ORDER BY total DESC",
        (inicio_mes, inicio_mes_siguiente),
    )
    total = sum((f["total"] for f in filas), Decimal(0))
    # "Otros" y los gastos sin categoria siempre van al final, junto con lo que no cabe en el top.
    nombradas = [f for f in filas if f["nombre"] and f["nombre"].lower() != "otros"]
    principales = nombradas[:MAX_CATEGORIAS]
    resto = [f for f in filas if f not in principales]

    categorias = [
        {"id_categoria": f["id_categoria"], "nombre": f["nombre"], "total": _num(f["total"]),
         "porcentaje": _porcentaje(f["total"], total)}
        for f in principales
    ]
    if resto:
        total_resto = sum((f["total"] for f in resto), Decimal(0))
        categorias.append(
            {"id_categoria": None, "nombre": "Otros", "total": _num(total_resto),
             "porcentaje": _porcentaje(total_resto, total)}
        )
    return {"mes": inicio_mes.isoformat()[:7], "total": _num(total), "categorias": categorias}


def _resumen_obligaciones():
    filas = fetch_all(
        "SELECT o.id_persona, COALESCE(p.nombre, 'Sin persona') AS nombre, o.tipo, "
        "o.descripcion, e.monto_pendiente "
        "FROM obligaciones o "
        "JOIN vw_estado_obligaciones e ON e.id_obligacion = o.id_obligacion "
        "LEFT JOIN personas p ON p.id_persona = o.id_persona "
        "WHERE e.resuelta = 0 AND o.tipo IN ('POR_COBRAR', 'POR_PAGAR')"
    )
    por_cobrar = por_pagar = Decimal(0)
    personas = {}
    for f in filas:
        pendiente = f["monto_pendiente"]
        if f["tipo"] == "POR_COBRAR":
            por_cobrar += pendiente
            firmado = pendiente
        else:
            por_pagar += pendiente
            firmado = -pendiente
        persona = personas.setdefault(
            f["id_persona"],
            {"id_persona": f["id_persona"], "nombre": f["nombre"], "monto": Decimal(0),
             "cantidad": 0, "concepto": None, "mayor": Decimal(0)},
        )
        persona["monto"] += firmado
        persona["cantidad"] += 1
        if pendiente > persona["mayor"]:
            persona["mayor"] = pendiente
            persona["concepto"] = _limpiar_descripcion(f["descripcion"])

    activas = sorted(
        (p for p in personas.values() if p["monto"] != 0), key=lambda p: abs(p["monto"]), reverse=True
    )
    return {
        "por_cobrar": _num(por_cobrar),
        "por_pagar": _num(por_pagar),
        "balance": _num(por_cobrar - por_pagar),
        "personas": [
            {"id_persona": p["id_persona"], "nombre": p["nombre"], "monto": _num(p["monto"]),
             "cantidad": p["cantidad"],
             "concepto": p["concepto"] if p["cantidad"] == 1 else "%d obligaciones" % p["cantidad"]}
            for p in activas[:MAX_PERSONAS]
        ],
    }


@app.get("/resumen")
def obtener_resumen():
    """Todo lo que necesita la pantalla Main, ya calculado.

    - kpis.*.variacion_pct: cambio frente al cierre del mes anterior (null si no hay base).
    - Tarjetas: saldo/deuda segun vw_saldo_cuentas (salidas - entradas); negativo = saldo a favor.
    - gastos_por_categoria: solo transacciones tipo GASTO del mes en curso.
    - obligaciones: solo POR_COBRAR / POR_PAGAR pendientes; monto > 0 te deben, < 0 les debes.
    """
    hoy = date.today()
    inicio_mes = hoy.replace(day=1)
    inicio_mes_siguiente = (inicio_mes + timedelta(days=32)).replace(day=1)

    cuentas = fetch_all(
        "SELECT c.id_cuenta, c.nombre, c.entidad, c.tipo, c.limite_credito, s.saldo_calculado AS saldo "
        "FROM cuentas c JOIN vw_saldo_cuentas s ON s.id_cuenta = c.id_cuenta "
        "ORDER BY CASE WHEN c.tipo = 'TARJETA_CREDITO' THEN 1 ELSE 0 END, c.id_cuenta"
    )
    return {
        "fecha": hoy,
        "kpis": _resumen_kpis(cuentas, inicio_mes),
        "cuentas": _resumen_cuentas(cuentas),
        "actividad_reciente": _resumen_actividad(),
        "gastos_por_categoria": _resumen_gastos(inicio_mes, inicio_mes_siguiente),
        "obligaciones": _resumen_obligaciones(),
    }
