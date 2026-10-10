"""Vista de analitica: indicadores calculados en el servidor sobre los ciclos. Solo lectura, no modifica nada."""
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from main import (
    SALDO_INICIAL,
    TARJETA,
    _asegurar_ciclos,
    _ciclos_detalle,
    _cuentas_detalle,
    _limpiar_descripcion,
    _num,
    _obligaciones_detalle,
    _porcentaje,
    _rechazar,
    _saldos_antes_de,
    _totales_periodo,
    _variacion_pct,
    app,
    fetch_all,
    fetch_one,
    obtener_ciclo_actual,
)

UMBRAL_HORMIGA = Decimal("5.00")  # un gasto de hasta este monto cuenta como "gasto hormiga"
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MAX_DIAS_CICLO_PATRON = 16
MIN_CICLOS_PROMEDIO = 2  # ciclos completos necesarios para hablar de "tu promedio"
DESCRIPCIONES_SIN_APORTE = ("Asignación de saldo", "Ajuste de asignación")  # no son aportes a una meta
MIN_DIAS_PROYECCION = 4  # dias de ciclo necesarios para que la proyeccion de cierre sea confiable


def _dinero_texto(valor):
    valor = float(valor)
    return ("-" if valor < 0 else "") + "$" + format(abs(valor), ",.2f")


def _sin_saldo_inicial():
    return "ISNULL(t.descripcion, '') <> '%s'" % SALDO_INICIAL


# ---------- periodo e historial ----------

def _ciclo_seleccionado(id_ciclo, hoy):
    _asegurar_ciclos()
    if id_ciclo is not None:
        encontrados = _ciclos_detalle("WHERE c.id_ciclo = ? ", (id_ciclo,))
        if not encontrados:
            raise _rechazar(404, "El ciclo no existe.")
        return encontrados[0]
    actual = obtener_ciclo_actual()
    if actual:
        return actual
    inicio = hoy.replace(day=1)
    fin = (inicio + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return {
        "id_ciclo": None, "id_ciclo_config": None, "config": "Mes calendario", "fecha_inicio": inicio,
        "fecha_fin": fin, "ajustado": False, "es_actual": True, "dias": (fin - inicio).days + 1,
        "id_anterior": None, "id_siguiente": None,
    }


def _meses_atras(inicio, cantidad):
    """Meses calendario anteriores a `inicio`, del mas antiguo al mas reciente (sin configuracion de ciclos)."""
    meses = []
    for _ in range(cantidad):
        fin = inicio - timedelta(days=1)
        inicio = fin.replace(day=1)
        meses.append({"id_ciclo": None, "id_ciclo_config": None, "config": "Mes calendario",
                      "fecha_inicio": inicio, "fecha_fin": fin, "ajustado": False, "es_actual": False,
                      "dias": (fin - inicio).days + 1})
    return list(reversed(meses))


def _ciclos_del_historial(ciclo, cantidad, hoy):
    """Los `cantidad` ciclos que terminan en el elegido (inclusive), del mas antiguo al mas reciente."""
    if ciclo["id_ciclo_config"] is not None:
        filas = _ciclos_detalle(
            "WHERE c.id_ciclo_config = ? AND c.fecha_inicio <= ? ",
            (ciclo["id_ciclo_config"], ciclo["fecha_inicio"]), "TOP %d" % cantidad,
        )
        return list(reversed(filas))
    return _meses_atras(ciclo["fecha_inicio"], cantidad - 1) + [dict(ciclo, es_actual=ciclo["fecha_inicio"] <= hoy <= ciclo["fecha_fin"])]


def _primera_fecha(hoy):
    """Donde empieza el historial util: el primer ingreso registrado (antes hay gastos sueltos sin su contexto)."""
    fila = fetch_one(
        "SELECT MIN(fecha) AS f FROM transacciones t WHERE t.tipo = 'INGRESO' AND " + _sin_saldo_inicial()
    ) or {}
    if fila.get("f"):
        return fila["f"]
    fila = fetch_one("SELECT MIN(fecha) AS f FROM transacciones t WHERE " + _sin_saldo_inicial()) or {}
    return fila.get("f") or hoy


def _metricas_ciclo(c, hoy, primera):
    hasta = min(c["fecha_fin"], hoy)
    ingresos, gastos = _totales_periodo(c["fecha_inicio"], c["fecha_fin"])
    al_cierre = _saldos_antes_de(hasta + timedelta(days=1))
    neto = al_cierre["ahorro"] - al_cierre["tarjetas"]
    return {
        "id_ciclo": c["id_ciclo"], "fecha_inicio": c["fecha_inicio"], "fecha_fin": c["fecha_fin"],
        "dias": c["dias"], "es_actual": c["es_actual"],
        # un ciclo que empieza antes de la primera transaccion registrada no tiene sus datos completos
        "parcial": c["fecha_inicio"] < primera,
        "ingresos": _num(ingresos), "gastos": _num(gastos), "balance": _num(ingresos - gastos),
        "tasa_ahorro": _porcentaje(ingresos - gastos, ingresos),
        "patrimonio": {"ahorro": _num(al_cierre["ahorro"]), "deuda": _num(al_cierre["tarjetas"]), "neto": _num(neto)},
    }


# ---------- gastos ----------

def _gastos_por_categoria(desde, hasta):
    filas = fetch_all(
        "SELECT c.id_categoria, ISNULL(c.nombre, 'Sin categoría') AS nombre, SUM(t.monto) AS total "
        "FROM transacciones t LEFT JOIN categorias c ON c.id_categoria = t.id_categoria "
        "WHERE t.tipo = 'GASTO' AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial() + " "
        "GROUP BY c.id_categoria, c.nombre",
        (desde, hasta),
    )
    return {f["id_categoria"]: (f["nombre"], f["total"]) for f in filas}


def _categorias(ciclo, anterior, comparables):
    actuales = _gastos_por_categoria(ciclo["fecha_inicio"], ciclo["fecha_fin"])
    previas = _gastos_por_categoria(anterior["fecha_inicio"], anterior["fecha_fin"]) if anterior else {}
    historicas = [_gastos_por_categoria(c["fecha_inicio"], c["fecha_fin"]) for c in comparables]
    total = sum((v[1] for v in actuales.values()), Decimal(0))
    claves = set(actuales) | set(previas)
    resultado = []
    for clave in claves:
        nombre = (actuales.get(clave) or previas.get(clave))[0]
        monto = actuales[clave][1] if clave in actuales else Decimal(0)
        previo = previas[clave][1] if clave in previas else Decimal(0)
        promedio = (
            sum((h[clave][1] for h in historicas if clave in h), Decimal(0)) / len(historicas) if historicas else None
        )
        resultado.append({
            "id_categoria": clave, "nombre": nombre, "total": _num(monto), "porcentaje": _porcentaje(monto, total),
            "anterior": _num(previo), "variacion_pct": _variacion_pct(monto, previo) if previo else None,
            "promedio": _num(promedio) if promedio is not None else None,
            "vs_promedio_pct": _variacion_pct(monto, promedio) if promedio else None,
        })
    resultado.sort(key=lambda c: c["total"], reverse=True)
    return resultado


def _mayores_gastos(ciclo):
    filas = fetch_all(
        "SELECT TOP 10 t.id_transaccion, t.fecha, t.descripcion, t.monto, c.nombre AS categoria, o.nombre AS cuenta "
        "FROM transacciones t LEFT JOIN categorias c ON c.id_categoria = t.id_categoria "
        "LEFT JOIN cuentas o ON o.id_cuenta = t.id_cuenta_origen "
        "WHERE t.tipo = 'GASTO' AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial() + " "
        "ORDER BY t.monto DESC, t.fecha DESC",
        (ciclo["fecha_inicio"], ciclo["fecha_fin"]),
    )
    for f in filas:
        f["monto"] = _num(f["monto"])
        f["descripcion"] = f["descripcion"] or "Sin descripción"
    return filas


def _gastos_por_cuenta(ciclo, total):
    filas = fetch_all(
        "SELECT o.id_cuenta, o.nombre, o.tipo, SUM(t.monto) AS total FROM transacciones t "
        "JOIN cuentas o ON o.id_cuenta = t.id_cuenta_origen "
        "WHERE t.tipo = 'GASTO' AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial() + " "
        "GROUP BY o.id_cuenta, o.nombre, o.tipo ORDER BY total DESC",
        (ciclo["fecha_inicio"], ciclo["fecha_fin"]),
    )
    return [
        {"id_cuenta": f["id_cuenta"], "nombre": f["nombre"], "es_tarjeta": f["tipo"] == TARJETA,
         "total": _num(f["total"]), "porcentaje": _porcentaje(f["total"], total)}
        for f in filas
    ]


def _hormiga(ciclo, total):
    fila = fetch_one(
        "SELECT COUNT(*) AS cantidad, ISNULL(SUM(t.monto), 0) AS total FROM transacciones t "
        "WHERE t.tipo = 'GASTO' AND t.monto <= ? AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial(),
        (UMBRAL_HORMIGA, ciclo["fecha_inicio"], ciclo["fecha_fin"]),
    )
    descripcion = "ISNULL(NULLIF(LTRIM(RTRIM(t.descripcion)), ''), 'Sin descripción')"
    detalle = fetch_all(
        "SELECT TOP 6 " + descripcion + " AS descripcion, COUNT(*) AS veces, SUM(t.monto) AS total FROM transacciones t "
        "WHERE t.tipo = 'GASTO' AND t.monto <= ? AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial() + " "
        "GROUP BY " + descripcion + " ORDER BY SUM(t.monto) DESC",
        (UMBRAL_HORMIGA, ciclo["fecha_inicio"], ciclo["fecha_fin"]),
    )
    return {
        "umbral": _num(UMBRAL_HORMIGA), "cantidad": fila["cantidad"], "total": _num(fila["total"]),
        "porcentaje": _porcentaje(fila["total"], total),
        "detalle": [{"descripcion": d["descripcion"], "veces": d["veces"], "total": _num(d["total"])} for d in detalle],
    }


# ---------- ritmo y patrones ----------

def _gasto_por_dia(desde, hasta):
    filas = fetch_all(
        "SELECT t.fecha, SUM(t.monto) AS total FROM transacciones t "
        "WHERE t.tipo = 'GASTO' AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial() + " GROUP BY t.fecha",
        (desde, hasta),
    )
    return {f["fecha"]: f["total"] for f in filas}


def _acumulado(ciclo, hasta_dia=None):
    por_dia = _gasto_por_dia(ciclo["fecha_inicio"], ciclo["fecha_fin"])
    serie, suma = [], Decimal(0)
    for i in range(ciclo["dias"]):
        dia = ciclo["fecha_inicio"] + timedelta(days=i)
        if hasta_dia is not None and dia > hasta_dia:
            serie.append(None)
            continue
        suma += por_dia.get(dia, Decimal(0))
        serie.append(suma)
    return serie


def _ritmo(ciclo, anterior, promedio_gastos, hoy):
    """Gasto acumulado dia a dia, comparado con el ciclo anterior, y proyeccion del cierre si el ciclo sigue en curso."""
    hasta = min(ciclo["fecha_fin"], hoy)
    actual = _acumulado(ciclo, hasta)
    previo = _acumulado(anterior) if anterior else []
    transcurridos = max((hasta - ciclo["fecha_inicio"]).days + 1, 0)
    restantes = max((ciclo["fecha_fin"] - hoy).days, 0) if ciclo["es_actual"] else 0
    gasto_a_hoy = actual[transcurridos - 1] if transcurridos else Decimal(0)
    ritmo_diario = gasto_a_hoy / transcurridos if transcurridos else Decimal(0)
    proyeccion = gasto_a_hoy + ritmo_diario * restantes if ciclo["es_actual"] else None
    puntos = []
    for i in range(max(ciclo["dias"], len(previo))):
        punto = {"dia": i + 1, "actual": _num(actual[i]) if i < len(actual) and actual[i] is not None else None,
                 "anterior": _num(previo[i]) if i < len(previo) else None, "proyeccion": None}
        if ciclo["es_actual"] and restantes and transcurridos and i >= transcurridos - 1 and i < ciclo["dias"]:
            punto["proyeccion"] = _num(gasto_a_hoy + ritmo_diario * (i + 1 - transcurridos))
        puntos.append(punto)
    disponible_por_dia = None
    if ciclo["es_actual"] and restantes and promedio_gastos is not None:
        disponible_por_dia = max((promedio_gastos - gasto_a_hoy) / restantes, Decimal(0))
    return {
        "puntos": puntos, "gasto_a_hoy": _num(gasto_a_hoy), "dias_transcurridos": transcurridos,
        "dias_restantes": restantes, "ritmo_diario": _num(ritmo_diario),
        "proyeccion_confiable": transcurridos >= MIN_DIAS_PROYECCION,
        "proyeccion_cierre": _num(proyeccion) if proyeccion is not None else None,
        "promedio_ciclo": _num(promedio_gastos) if promedio_gastos is not None else None,
        "disponible_por_dia": _num(disponible_por_dia) if disponible_por_dia is not None else None,
        "es_actual": ciclo["es_actual"],
    }


def _gastos_del_historial(ciclos):
    if not ciclos:
        return []
    return fetch_all(
        "SELECT t.fecha, t.monto, t.descripcion, c.nombre AS categoria FROM transacciones t "
        "LEFT JOIN categorias c ON c.id_categoria = t.id_categoria "
        "WHERE t.tipo = 'GASTO' AND t.fecha >= ? AND t.fecha <= ? AND " + _sin_saldo_inicial() + " ORDER BY t.fecha",
        (ciclos[0]["fecha_inicio"], ciclos[-1]["fecha_fin"]),
    )


def _indice_ciclo(ciclos, fecha):
    for i, c in enumerate(ciclos):
        if c["fecha_inicio"] <= fecha <= c["fecha_fin"]:
            return i
    return None


def _patrones(gastos, ciclos, hoy):
    por_semana = [{"dia": DIAS_SEMANA[i], "total": Decimal(0), "cantidad": 0} for i in range(7)]
    por_offset = defaultdict(lambda: Decimal(0))
    recurrentes = defaultdict(lambda: {"ciclos": set(), "montos": [], "ultima": None, "categorias": defaultdict(int)})
    for g in gastos:
        idx = _indice_ciclo(ciclos, g["fecha"])
        if idx is None:
            continue
        dia = por_semana[g["fecha"].weekday()]
        dia["total"] += g["monto"]
        dia["cantidad"] += 1
        por_offset[(idx, (g["fecha"] - ciclos[idx]["fecha_inicio"]).days)] += g["monto"]
        if g["descripcion"]:
            clave = " ".join(g["descripcion"].lower().split())
            r = recurrentes[clave]
            r["ciclos"].add(idx)
            r["montos"].append(g["monto"])
            r["ultima"] = max(r["ultima"], g["fecha"]) if r["ultima"] else g["fecha"]
            r["nombre"] = r.get("nombre") or g["descripcion"].strip()
            r["categorias"][g["categoria"] or "Sin categoría"] += 1

    # los ciclos con datos completos y terminados; si no hay, todos los que haya
    completos = [i for i, c in enumerate(ciclos) if not c["parcial"] and c["fecha_fin"] < hoy]
    base = completos or list(range(len(ciclos)))
    largo = min(max((c["dias"] for c in ciclos), default=0), MAX_DIAS_CICLO_PATRON)
    por_dia_ciclo = []
    for offset in range(largo):
        con_dia = [i for i in base if offset < ciclos[i]["dias"]]
        total = sum((por_offset[(i, offset)] for i in con_dia), Decimal(0))
        por_dia_ciclo.append({"dia": offset + 1, "promedio": _num(total / len(con_dia)) if con_dia else 0})

    lista_recurrentes = []
    for r in recurrentes.values():
        if len(r["ciclos"]) < 2:
            continue
        total = sum(r["montos"], Decimal(0))
        lista_recurrentes.append({
            "descripcion": r["nombre"], "veces": len(r["montos"]), "ciclos": len(r["ciclos"]),
            "promedio": _num(total / len(r["montos"])), "ultimo": _num(r["montos"][-1]), "total": _num(total),
            "por_ciclo": _num(total / len(r["ciclos"])), "ultima_fecha": r["ultima"],
            "categoria": max(r["categorias"], key=r["categorias"].get),
        })
    lista_recurrentes.sort(key=lambda r: r["por_ciclo"], reverse=True)
    return {
        "por_dia_semana": [
            {"dia": d["dia"], "total": _num(d["total"]), "cantidad": d["cantidad"],
             "promedio": _num(d["total"] / d["cantidad"]) if d["cantidad"] else 0}
            for d in por_semana
        ],
        "por_dia_ciclo": por_dia_ciclo,
        "recurrentes": lista_recurrentes[:12],
        "ciclos_usados": len(base),
    }


# ---------- situacion, metas, deuda, obligaciones ----------

def _situacion(cuentas, gasto_diario, gasto_historial=None, dias_historial=None):
    ahorro = sum((c["saldo"] for c in cuentas if c["tipo"] != TARJETA), 0.0)
    tarjetas = [c for c in cuentas if c["tipo"] == TARJETA]
    deuda = sum((c["saldo"] for c in tarjetas), 0.0)
    return {
        "ahorro": round(ahorro, 2), "deuda": round(deuda, 2), "patrimonio_neto": round(ahorro - deuda, 2),
        "gasto_diario": _num(gasto_diario) if gasto_diario else None,
        "cobertura_dias": int(ahorro / float(gasto_diario)) if gasto_diario and ahorro > 0 else None,
        "gasto_historial": _num(gasto_historial) if gasto_historial is not None else None,
        "dias_historial": dias_historial,
    }


def _metas(cuentas, historial, hoy, primera, historia_suficiente):
    """Avance de cada subcuenta con meta y en cuantos ciclos la completa al ritmo que se le aporta.

    Se espera que casi todas se gasten al llegar a la meta (reserva para un gasto futuro) y vuelvan a empezar, asi que
    gastar de la subcuenta no impide estimar. El ritmo por ciclo es lo que el plan recurrente activo aporta (monto fijo
    por pago) mas el promedio de los aportes libres, cuando ya hay historial para promediar. No cuentan como aporte el
    saldo inicial ni los ajustes de saldo."""
    if not historial:
        return []
    desde = max(historial[0]["fecha_inicio"], primera)
    hasta = min(historial[-1]["fecha_fin"], hoy)
    aportes_plan = {
        f["id"]: (f["monto"], f["nombre_plan"])
        for f in fetch_all(
            "SELECT d.id_subcuenta_destino AS id, d.monto, p.nombre AS nombre_plan FROM plan_recurrente_destinos d "
            "JOIN plan_recurrente p ON p.id_plan = d.id_plan "
            "WHERE p.activo = 1 AND d.activo = 1 AND d.monto IS NOT NULL AND d.id_subcuenta_destino IS NOT NULL"
        )
    }
    # lo que entra a la subcuenta sin ser saldo inicial, ajuste ni un pago del plan ("<plan>: <subcuenta>")
    libres = {
        f["id"]: f["total"]
        for f in fetch_all(
            "SELECT m.id_subcuenta_destino AS id, SUM(m.monto) AS total FROM movimientos_subcuenta m "
            "WHERE m.id_subcuenta_destino IS NOT NULL AND m.fecha >= ? AND m.fecha <= ? "
            "AND ISNULL(m.descripcion, '') NOT IN (?, ?) "
            "AND NOT EXISTS (SELECT 1 FROM plan_recurrente p "
            "WHERE LEFT(ISNULL(m.descripcion, ''), LEN(p.nombre) + 2) = p.nombre + ': ') "
            "GROUP BY m.id_subcuenta_destino",
            (desde, hasta) + DESCRIPCIONES_SIN_APORTE,
        )
    }
    dias_cubiertos = max((hasta - desde).days + 1, 1)
    dias_ciclo = historial[-1]["dias"]
    metas = []
    for cuenta in cuentas:
        for s in cuenta.get("subcuentas", []):
            if not s["saldo_meta"]:
                continue
            falta = max(Decimal(str(s["saldo_meta"])) - Decimal(str(s["saldo"])), Decimal(0))
            plan = aportes_plan.get(s["id_subcuenta"])
            del_plan = Decimal(str(plan[0])) if plan else None
            total_libre = Decimal(str(libres.get(s["id_subcuenta"], 0)))
            libre = total_libre / dias_cubiertos * dias_ciclo if historia_suficiente and total_libre > 0 else None
            aporte = (del_plan or Decimal(0)) + (libre or Decimal(0))
            metas.append({
                "id_subcuenta": s["id_subcuenta"], "nombre": s["nombre"], "cuenta": cuenta["nombre"],
                "saldo": s["saldo"], "meta": s["saldo_meta"],
                "porcentaje": min(round(s["saldo"] / s["saldo_meta"] * 100, 1), 100.0), "falta": _num(falta),
                "plan": plan[1] if plan else None,
                "aporte_plan": _num(del_plan) if del_plan is not None else None,
                "aporte_libre": _num(libre) if libre is not None else None,
                "aportes_libres_sin_historial": total_libre > 0 and not historia_suficiente,
                "aporte_por_ciclo": _num(aporte) if aporte > 0 else None,
                "ciclos_restantes": math.ceil(falta / aporte) if falta > 0 and aporte > 0 else None,
                "alcanzada": falta == 0,
            })
    metas.sort(key=lambda m: (m["alcanzada"], -m["porcentaje"]))
    return metas


def _deuda(cuentas, hoy):
    tarjetas = [
        {"id_cuenta": c["id_cuenta"], "nombre": c["nombre"], "saldo": c["saldo"], "limite": c.get("limite_credito"),
         "uso_pct": c.get("uso_pct"), "disponible": c.get("disponible")}
        for c in cuentas if c["tipo"] == TARJETA
    ]
    cuotas = fetch_all(
        "SELECT TOP 8 q.id_cuota_financiamiento, f.id_financiamiento, f.descripcion, q.numero_cuota, f.numero_cuotas, "
        "q.fecha_vencimiento, q.monto FROM cuotas_financiamiento q "
        "JOIN financiamientos f ON f.id_financiamiento = q.id_financiamiento "
        "WHERE q.fecha_pago IS NULL ORDER BY q.fecha_vencimiento, q.numero_cuota"
    )
    for q in cuotas:
        q["monto"] = _num(q["monto"])
        q["dias_para_vencer"] = (q["fecha_vencimiento"] - hoy).days
    restante = fetch_one(
        "SELECT ISNULL(SUM(monto), 0) AS total, COUNT(*) AS cuotas FROM cuotas_financiamiento WHERE fecha_pago IS NULL"
    )
    return {
        "tarjetas": tarjetas, "cuotas_proximas": cuotas,
        "financiamiento_restante": _num(restante["total"]), "cuotas_pendientes": restante["cuotas"],
    }


def _obligaciones(hoy):
    pendientes = [o for o in _obligaciones_detalle() if not o["resuelta"]]
    cubetas = {
        "POR_COBRAR": [Decimal(0)] * 3, "POR_PAGAR": [Decimal(0)] * 3, "REPOSICION": [Decimal(0)] * 3,
    }
    personas = {}
    for o in pendientes:
        dias = (hoy - o["fecha_creacion"]).days
        cubeta = 0 if dias <= 30 else 1 if dias <= 60 else 2
        cubetas[o["tipo"]][cubeta] += Decimal(str(o["monto_pendiente"]))
        if o["tipo"] == "REPOSICION":
            continue
        p = personas.setdefault(
            o["id_persona"], {"id_persona": o["id_persona"], "nombre": o["persona"] or "Sin persona",
                              "te_deben": Decimal(0), "debes": Decimal(0), "dias": 0},
        )
        p["te_deben" if o["tipo"] == "POR_COBRAR" else "debes"] += Decimal(str(o["monto_pendiente"]))
        p["dias"] = max(p["dias"], dias)
    lista = sorted(personas.values(), key=lambda p: abs(p["te_deben"] - p["debes"]), reverse=True)
    etiquetas = ["0 a 30 días", "31 a 60 días", "Más de 60 días"]
    return {
        "personas": [
            {"id_persona": p["id_persona"], "nombre": p["nombre"], "te_deben": _num(p["te_deben"]),
             "debes": _num(p["debes"]), "neto": _num(p["te_deben"] - p["debes"]), "dias_mas_antigua": p["dias"]}
            for p in lista[:8]
        ],
        "antiguedad": [
            {"rango": etiquetas[i], "te_deben": _num(cubetas["POR_COBRAR"][i]), "debes": _num(cubetas["POR_PAGAR"][i]),
             "reposiciones": _num(cubetas["REPOSICION"][i])}
            for i in range(3)
        ],
        "reposiciones": {
            "cantidad": sum(1 for o in pendientes if o["tipo"] == "REPOSICION"),
            "total": _num(sum((Decimal(str(o["monto_pendiente"])) for o in pendientes if o["tipo"] == "REPOSICION"), Decimal(0))),
        },
    }


# ---------- insights ----------

def _insights(ciclo, kpis, categorias, ritmo, hormiga, patrones, situacion, deuda, obligaciones, metas, primeros_dias_pct):
    lista = []
    ingresos, gastos = _dinero_texto(kpis["ingresos"]), _dinero_texto(kpis["gastos"])
    excluye = "Solo cuenta ingresos y gastos reales: no incluye transferencias entre tus cuentas, pagos a tarjeta ni saldos iniciales."

    def agregar(nivel, texto, explicacion):
        """`explicacion`: lineas que se muestran al abrir el hallazgo (formula, cuenta con tus numeros, reglas)."""
        lista.append({"nivel": nivel, "texto": texto, "explicacion": explicacion})

    if kpis["ingresos"] > 0:
        if kpis["balance"] < 0:
            agregar("alerta", "Este ciclo gastas más de lo que ingresas: el balance es %s." % _dinero_texto(kpis["balance"]), [
                "Balance = ingresos − gastos",
                "%s − %s = %s" % (ingresos, gastos, _dinero_texto(kpis["balance"])),
                excluye,
            ])
        elif kpis["tasa_ahorro"] is not None:
            explicacion = [
                "Tasa de ahorro = (ingresos − gastos) ÷ ingresos",
                "(%s − %s) ÷ %s = %.1f%%" % (ingresos, gastos, ingresos, kpis["tasa_ahorro"]),
                excluye,
                "Se resalta como buena desde el 20%.",
            ]
            if kpis["tasa_ahorro"] >= 20:
                agregar("ok", "Estás ahorrando el %.0f%% de tus ingresos este ciclo." % kpis["tasa_ahorro"], explicacion)
            else:
                agregar("info", "Tu tasa de ahorro este ciclo es %.0f%%." % kpis["tasa_ahorro"], explicacion)
    if ritmo["es_actual"] and ritmo["proyeccion_confiable"] and ritmo["proyeccion_cierre"] is not None and ritmo["promedio_ciclo"]:
        proy, prom = ritmo["proyeccion_cierre"], ritmo["promedio_ciclo"]
        explicacion = [
            "Proyección = gasto a hoy + (gasto a hoy ÷ días transcurridos) × días restantes",
            "%s + (%s ÷ %d) × %d = %s" % (
                _dinero_texto(ritmo["gasto_a_hoy"]), _dinero_texto(ritmo["gasto_a_hoy"]), ritmo["dias_transcurridos"],
                ritmo["dias_restantes"], _dinero_texto(proy)),
            "Tu promedio (%s) es lo que gastaste en promedio en los ciclos completos anteriores." % _dinero_texto(prom),
            "Se avisa cuando la proyección se aleja más del 10% de ese promedio. Supone que el resto del ciclo gastas al mismo ritmo diario.",
        ]
        if proy > prom * 1.1:
            agregar("alerta", "A este ritmo cerrarás el ciclo con %s en gastos, %.0f%% más que tu promedio (%s)."
                    % (_dinero_texto(proy), (proy / prom - 1) * 100, _dinero_texto(prom)), explicacion)
        elif proy < prom * 0.9:
            agregar("ok", "A este ritmo gastarás %s, %.0f%% menos que tu promedio (%s)."
                    % (_dinero_texto(proy), (1 - proy / prom) * 100, _dinero_texto(prom)), explicacion)
    subidas = [c for c in categorias if c["vs_promedio_pct"] is not None and c["vs_promedio_pct"] >= 25
               and c["total"] - (c["promedio"] or 0) >= 10]
    if subidas:
        c = max(subidas, key=lambda x: x["total"] - (x["promedio"] or 0))
        agregar("alerta", "%s va %.0f%% sobre tu promedio (%s frente a %s)."
                % (c["nombre"], c["vs_promedio_pct"], _dinero_texto(c["total"]), _dinero_texto(c["promedio"])), [
            "Variación = (gasto del ciclo − promedio) ÷ promedio",
            "(%s − %s) ÷ %s = +%.1f%%" % (_dinero_texto(c["total"]), _dinero_texto(c["promedio"]), _dinero_texto(c["promedio"]), c["vs_promedio_pct"]),
            "El promedio sale de los ciclos completos anteriores de esa categoría.",
            "Se avisa si la categoría sube 25% o más y la diferencia es de al menos $10.00.",
        ])
    bajadas = [c for c in categorias if c["vs_promedio_pct"] is not None and c["vs_promedio_pct"] <= -25
               and (c["promedio"] or 0) - c["total"] >= 10]
    if bajadas:
        c = max(bajadas, key=lambda x: (x["promedio"] or 0) - x["total"])
        agregar("ok", "Gastas %.0f%% menos que tu promedio en %s." % (abs(c["vs_promedio_pct"]), c["nombre"]), [
            "Variación = (gasto del ciclo − promedio) ÷ promedio",
            "(%s − %s) ÷ %s = %.1f%%" % (_dinero_texto(c["total"]), _dinero_texto(c["promedio"]), _dinero_texto(c["promedio"]), c["vs_promedio_pct"]),
            "Se resalta si la categoría baja 25% o más y la diferencia es de al menos $10.00.",
        ])
    if hormiga["cantidad"] >= 5 and (hormiga["porcentaje"] or 0) >= 10:
        agregar("info", "%d gastos pequeños (hasta %s) suman %s: el %.0f%% de tu gasto."
                % (hormiga["cantidad"], _dinero_texto(hormiga["umbral"]), _dinero_texto(hormiga["total"]), hormiga["porcentaje"]), [
            "Gasto hormiga = cada compra de hasta %s" % _dinero_texto(hormiga["umbral"]),
            "%s ÷ %s = %.1f%% del gasto del ciclo" % (_dinero_texto(hormiga["total"]), gastos, hormiga["porcentaje"]),
            "Se avisa con 5 o más compras pequeñas que pesen 10% o más del gasto. El detalle está en «Gastos hormiga».",
        ])
    if primeros_dias_pct is not None and primeros_dias_pct >= 35:
        agregar("info", "El %.0f%% de tu gasto ocurre en los primeros 3 días del ciclo, justo después de la paga." % primeros_dias_pct, [
            "Porcentaje = gasto de los primeros 3 días ÷ gasto total del ciclo",
            "Gasto total del ciclo: %s; los primeros 3 días concentran el %.1f%%." % (gastos, primeros_dias_pct),
            "Se muestra desde el 35%, cuando el ciclo ya lleva al menos 3 días.",
        ])
    for t in deuda["tarjetas"]:
        if t["uso_pct"] is not None and t["uso_pct"] >= 70:
            agregar("alerta", "%s usa el %.0f%% de su límite." % (t["nombre"], t["uso_pct"]), [
                "Uso del límite = deuda ÷ límite de crédito",
                "%s ÷ %s = %.1f%%" % (_dinero_texto(t["saldo"]), _dinero_texto(t["limite"] or 0), t["uso_pct"]),
                "Se avisa desde el 70%.",
            ])
    for q in deuda["cuotas_proximas"]:
        if q["dias_para_vencer"] <= 7:
            donde = "Cuota %d de %d del financiamiento «%s»: %s, vence el %s." % (
                q["numero_cuota"], q["numero_cuotas"], q["descripcion"], _dinero_texto(q["monto"]), q["fecha_vencimiento"].isoformat())
            if q["dias_para_vencer"] < 0:
                agregar("alerta", "La cuota %d de «%s» (%s) está vencida desde hace %d días."
                        % (q["numero_cuota"], q["descripcion"], _dinero_texto(q["monto"]), -q["dias_para_vencer"]),
                        [donde, "Se avisa cuando una cuota sin pagar ya venció o vence en 7 días o menos."])
            else:
                agregar("alerta", "La cuota %d de «%s» (%s) vence en %d días."
                        % (q["numero_cuota"], q["descripcion"], _dinero_texto(q["monto"]), q["dias_para_vencer"]),
                        [donde, "Se avisa cuando una cuota sin pagar ya venció o vence en 7 días o menos."])
    viejas = [a for a in obligaciones["antiguedad"] if a["rango"].startswith("Más") and a["te_deben"] > 0]
    if viejas:
        agregar("info", "Te deben %s desde hace más de 60 días." % _dinero_texto(viejas[0]["te_deben"]), [
            "Suma de lo pendiente por cobrar en obligaciones creadas hace más de 60 días.",
            "Total: %s. Puedes ver el detalle por persona en «Personas y obligaciones»." % _dinero_texto(viejas[0]["te_deben"]),
        ])
    cobertura = situacion["cobertura_dias"]
    if cobertura is not None and (cobertura < 30 or cobertura >= 90):
        explicacion = [
            "Cobertura = ahorro disponible ÷ gasto diario promedio",
            "%s ÷ %s por día = %d días" % (_dinero_texto(situacion["ahorro"]), _dinero_texto(situacion["gasto_diario"]), cobertura),
            "Ahorro disponible: suma de los saldos de tus cuentas que no son tarjetas, incluido lo reservado en subcuentas.",
        ]
        if situacion.get("gasto_historial") is not None and situacion.get("dias_historial"):
            explicacion.append("Gasto diario: %s gastados en %d días de historial ÷ %d = %s."
                               % (_dinero_texto(situacion["gasto_historial"]), situacion["dias_historial"],
                                  situacion["dias_historial"], _dinero_texto(situacion["gasto_diario"])))
        explicacion.append("Es una referencia: no resta la deuda de tus tarjetas y, con poco historial, cambia mucho de un ciclo a otro.")
        explicacion.append("Se avisa por debajo de 30 días y se resalta desde 90.")
        agregar("alerta" if cobertura < 30 else "ok", "Tu ahorro cubre unos %d días de gasto al ritmo actual." % cobertura, explicacion)
    alcanzadas = [m for m in metas if m["alcanzada"]]
    if alcanzadas:
        agregar("ok", "Ya alcanzaste la meta de %s." % ", ".join(m["nombre"] for m in alcanzadas[:3]), [
            "Una meta se alcanza cuando el saldo de la subcuenta llega a su monto meta.",
        ] + ["%s: %s de %s" % (m["nombre"], _dinero_texto(m["saldo"]), _dinero_texto(m["meta"])) for m in alcanzadas[:3]])
    recurrentes = patrones["recurrentes"]
    if recurrentes:
        total_rec = _dinero_texto(sum(r["por_ciclo"] for r in recurrentes))
        explicacion = [
            "Un gasto es recurrente cuando aparece con la misma descripción (sin importar mayúsculas) en 2 o más ciclos distintos. La categoría no cuenta.",
            "Por ciclo = total gastado con esa descripción ÷ ciclos en que apareció",
        ] + ["«%s»: %s ÷ %d ciclos = %s por ciclo (%d compras)"
             % (r["descripcion"], _dinero_texto(r["total"]), r["ciclos"], _dinero_texto(r["por_ciclo"]), r["veces"]) for r in recurrentes[:5]]
        if len(recurrentes) > 1:
            agregar("info", "Tus %d gastos recurrentes suman unos %s por ciclo." % (len(recurrentes), total_rec), explicacion)
        else:
            agregar("info", "Tu gasto recurrente «%s» ronda los %s por ciclo." % (recurrentes[0]["descripcion"], total_rec), explicacion)
    orden = {"alerta": 0, "info": 1, "ok": 2}
    lista.sort(key=lambda i: orden[i["nivel"]])
    return lista[:10]


# ---------- ensamblado ----------

def construir_analitica(id_ciclo=None, ciclos=6):
    hoy = date.today()
    cantidad = min(max(ciclos, 2), 24)
    ciclo = _ciclo_seleccionado(id_ciclo, hoy)
    primera = _primera_fecha(hoy)
    lista = _ciclos_del_historial(ciclo, cantidad, hoy)
    # solo ciclos que ya tienen algo registrado (los anteriores a la primera transaccion no aportan nada)
    lista = [c for c in lista if c["fecha_fin"] >= primera or c["fecha_inicio"] == ciclo["fecha_inicio"]]
    historial = [_metricas_ciclo(c, hoy, primera) for c in lista]
    for h, c in zip(historial, lista):
        h["id_ciclo"], h["es_seleccionado"] = c["id_ciclo"], c["fecha_inicio"] == ciclo["fecha_inicio"]

    idx = next(i for i, h in enumerate(historial) if h["es_seleccionado"])
    seleccionado = historial[idx]
    anterior_ciclo = lista[idx - 1] if idx > 0 else None
    anterior = historial[idx - 1] if idx > 0 else None
    comparables = [c for c, h in zip(lista, historial) if not h["parcial"] and not h["es_seleccionado"] and c["fecha_fin"] < hoy]
    comparables_h = [h for h in historial if not h["parcial"] and not h["es_seleccionado"] and h["fecha_fin"] < hoy]
    # con muy pocos ciclos completos un "promedio" no dice nada: no se calcula
    if len(comparables_h) < MIN_CICLOS_PROMEDIO:
        comparables, comparables_h = [], []
    promedios = {
        "ingresos": _num(sum(Decimal(str(h["ingresos"])) for h in comparables_h) / len(comparables_h)) if comparables_h else None,
        "gastos": _num(sum(Decimal(str(h["gastos"])) for h in comparables_h) / len(comparables_h)) if comparables_h else None,
        "tasa_ahorro": round(sum(h["tasa_ahorro"] for h in comparables_h if h["tasa_ahorro"] is not None) / max(sum(1 for h in comparables_h if h["tasa_ahorro"] is not None), 1), 1) if comparables_h else None,
        "ciclos": len(comparables_h),
    }
    promedio_gastos = Decimal(str(promedios["gastos"])) if promedios["gastos"] is not None else None

    kpis = {
        "ingresos": seleccionado["ingresos"], "gastos": seleccionado["gastos"], "balance": seleccionado["balance"],
        "tasa_ahorro": seleccionado["tasa_ahorro"],
        "ingresos_variacion_pct": _variacion_pct(seleccionado["ingresos"], anterior["ingresos"]) if anterior else None,
        "gastos_variacion_pct": _variacion_pct(seleccionado["gastos"], anterior["gastos"]) if anterior else None,
    }

    cuentas_detalle = _cuentas_detalle()
    cuentas = [c for c in cuentas_detalle]
    desde_h, hasta_h = lista[0]["fecha_inicio"], min(lista[-1]["fecha_fin"], hoy)
    dias_hist = max((hasta_h - max(desde_h, primera)).days + 1, 1)
    gasto_hist = _totales_periodo(max(desde_h, primera), hasta_h)[1]
    gasto_diario = gasto_hist / dias_hist if gasto_hist else None

    categorias = _categorias(ciclo, anterior_ciclo, comparables)
    gastos_hist = _gastos_del_historial(lista)
    for c, h in zip(lista, historial):
        c["parcial"] = h["parcial"]
    patrones = _patrones(gastos_hist, lista, hoy)
    total_gastos = Decimal(str(seleccionado["gastos"]))
    hormiga = _hormiga(ciclo, total_gastos)
    ritmo = _ritmo(ciclo, anterior_ciclo, promedio_gastos, hoy)
    situacion = _situacion(cuentas, gasto_diario, gasto_hist, dias_hist)
    metas = _metas(cuentas, lista, hoy, primera, bool(comparables_h))
    deuda = _deuda(cuentas, hoy)
    obligaciones = _obligaciones(hoy)

    primeros = None
    if seleccionado["gastos"]:
        por_dia = _gasto_por_dia(ciclo["fecha_inicio"], ciclo["fecha_fin"])
        primeros3 = sum((por_dia.get(ciclo["fecha_inicio"] + timedelta(days=i), Decimal(0)) for i in range(3)), Decimal(0))
        primeros = _porcentaje(primeros3, total_gastos) if ciclo["dias"] > 6 and (ciclo["fecha_fin"] < hoy or (hoy - ciclo["fecha_inicio"]).days >= 3) else None

    return {
        "fecha": hoy,
        "ciclo": {k: ciclo[k] for k in ("id_ciclo", "id_ciclo_config", "config", "fecha_inicio", "fecha_fin", "ajustado",
                                          "es_actual", "dias", "id_anterior", "id_siguiente")},
        "historial": historial,
        "historial_corto": len(comparables_h) < 3,
        "ciclos_para_promedio": MIN_CICLOS_PROMEDIO,
        "promedios": promedios,
        "kpis": kpis,
        "situacion": situacion,
        "gastos": {
            "total": seleccionado["gastos"], "categorias": categorias, "mayores": _mayores_gastos(ciclo),
            "por_cuenta": _gastos_por_cuenta(ciclo, total_gastos), "hormiga": hormiga,
        },
        "ritmo": ritmo,
        "patrones": patrones,
        "metas": metas,
        "deuda": deuda,
        "obligaciones": obligaciones,
        "insights": _insights(ciclo, kpis, categorias, ritmo, hormiga, patrones, situacion, deuda, obligaciones, metas, primeros),
    }


@app.get("/analitica")
def obtener_analitica(id_ciclo: Optional[int] = None, ciclos: int = 6):
    """Indicadores de la vista de analitica para un ciclo (el actual por defecto) y los `ciclos` anteriores."""
    return construir_analitica(id_ciclo, ciclos)
