from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class Persona(BaseModel):
    nombre: str


class Categoria(BaseModel):
    nombre: str
    descripcion: Optional[str] = None


class Cuenta(BaseModel):
    nombre: str
    entidad: Optional[str] = None
    tipo: str
    limite_credito: Optional[float] = None


class Subcuenta(BaseModel):
    nombre: Optional[str] = None
    saldo: Optional[float] = None
    saldo_meta: Optional[float] = None
    descripcion: Optional[str] = None
    id_cuenta: Optional[int] = None


class SubcuentaInicial(BaseModel):
    nombre: str
    monto: float = 0
    saldo_meta: Optional[float] = None
    descripcion: Optional[str] = None


class CuentaNueva(Cuenta):
    # El saldo de una cuenta se calcula desde transacciones: el saldo inicial se registra como una.
    saldo_inicial: Optional[float] = None
    subcuentas: List[SubcuentaInicial] = []


class PlanRecurrente(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    activo: bool = True


class DestinoPlan(BaseModel):
    """Destino dentro de un plan. Con id (al editar) solo cambia su estado; sin id se crea."""
    id_plan_recurrente_destino: Optional[int] = None
    id_cuenta_destino: int
    id_subcuenta_destino: Optional[int] = None
    monto: Optional[float] = None
    porcentaje: Optional[float] = None
    activo: bool = True


class PlanConDestinos(PlanRecurrente):
    destinos: List[DestinoPlan] = []


class EjecucionPlan(BaseModel):
    id_cuenta_origen: int
    fecha: date
    monto_base: Optional[float] = None
    simular: bool = False


class PlanRecurrenteDestino(BaseModel):
    id_plan: int
    id_cuenta_destino: int
    id_subcuenta_destino: Optional[int] = None
    monto: Optional[float] = None
    porcentaje: Optional[float] = None
    activo: bool = True


class Transaccion(BaseModel):
    fecha: date
    tipo: str
    monto: float
    id_cuenta_origen: Optional[int] = None
    id_cuenta_destino: Optional[int] = None
    id_categoria: Optional[int] = None
    descripcion: Optional[str] = None
    referencia: Optional[str] = None


class MovimientoSubcuentaInicial(BaseModel):
    """Movimiento de subcuenta que se crea junto con una transaccion (hereda su fecha y queda vinculado)."""
    tipo: str
    monto: float
    id_subcuenta_origen: Optional[int] = None
    id_subcuenta_destino: Optional[int] = None
    id_categoria: Optional[int] = None
    descripcion: Optional[str] = None


class TransaccionNueva(Transaccion):
    movimientos_subcuenta: List[MovimientoSubcuentaInicial] = []


class TransaccionEditar(Transaccion):
    agregar_movimientos_subcuenta: List[MovimientoSubcuentaInicial] = []
    quitar_movimientos_subcuenta: List[int] = []


class MovimientoSubcuenta(BaseModel):
    fecha: date
    tipo: str
    monto: float
    id_subcuenta_origen: Optional[int] = None
    id_subcuenta_destino: Optional[int] = None
    id_categoria: Optional[int] = None
    descripcion: Optional[str] = None
    id_transaccion_relacionada: Optional[int] = None


class Financiamiento(BaseModel):
    descripcion: str
    fecha_inicio: date
    monto_total: float
    numero_cuotas: int
    tasa_interes: Optional[float] = None
    id_transaccion_origen: Optional[int] = None


class FinanciamientoNuevo(Financiamiento):
    # Genera el calendario de cuotas iguales al crear; si es False, las cuotas se agregan a mano despues.
    generar_cuotas: bool = True


class CalendarioFinanciamiento(BaseModel):
    fecha_inicio: date
    monto_total: float
    numero_cuotas: int


class PagoCuota(BaseModel):
    fecha_pago: date
    monto: Optional[float] = None
    id_transaccion_pago: Optional[int] = None


class CuotaFinanciamiento(BaseModel):
    id_financiamiento: int
    numero_cuota: int
    fecha_vencimiento: date
    monto: float
    id_transaccion_pago: Optional[int] = None
    fecha_pago: Optional[date] = None


class Obligacion(BaseModel):
    fecha_creacion: date
    tipo: str
    monto: float
    descripcion: Optional[str] = None
    id_persona: Optional[int] = None
    id_transaccion_origen: Optional[int] = None
    id_movimiento_subcuenta_origen: Optional[int] = None
    id_cuenta_destino_resolucion: int
    id_subcuenta_destino_resolucion: Optional[int] = None


class LiquidacionObligacion(BaseModel):
    id_obligacion: int
    fecha: date
    monto: float
    id_transaccion: Optional[int] = None
    id_movimiento_subcuenta: Optional[int] = None
    descripcion: Optional[str] = None
