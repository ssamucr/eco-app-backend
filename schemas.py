from datetime import date
from typing import Optional

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


class PlanRecurrente(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    activo: bool = True


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
