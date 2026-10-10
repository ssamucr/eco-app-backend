-- =====================================================================================================
-- Eco: script completo de la base de datos (estructura, sin datos).
--
-- Crea la base `eco` con todas sus tablas, restricciones y vistas. Es idempotente: se puede ejecutar sobre una base
-- existente sin perder datos (solo crea lo que falte; nunca borra nada).
--
-- Uso (con un login que pueda crear bases de datos, por ejemplo tu usuario de Windows):
--     sqlcmd -S localhost\SQLEXPRESS -E -i database\eco.sql
-- Despues crea el usuario de la aplicacion con database\usuario.sql.
--
-- Notas del modelo:
--   * El saldo de una cuenta NO se almacena: se calcula desde `transacciones` (vw_saldo_cuentas).
--   * El estado de una obligacion se calcula desde `liquidaciones_obligacion` (vw_estado_obligaciones).
--   * `subcuentas.saldo` si se almacena y lo mantiene la aplicacion junto con `movimientos_subcuenta`.
--   * Los ciclos (`ciclo_config`, `ciclo_config_dia`, `ciclo`) definen los periodos entre pagas.
-- =====================================================================================================

IF DB_ID(N'eco') IS NULL
    CREATE DATABASE eco;
GO

USE eco;
GO

-- ---------------------------------------------------------------------------------------------------
-- Tablas
-- ---------------------------------------------------------------------------------------------------

IF OBJECT_ID(N'dbo.personas', N'U') IS NULL
BEGIN
    CREATE TABLE personas (
    	-- Personas (Deudores o Acreedores)
    	id_persona INT IDENTITY(1,1) PRIMARY KEY
    	, nombre VARCHAR(50) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.categorias', N'U') IS NULL
BEGIN
    CREATE TABLE categorias (
    	-- Categorias de gasto, para clasificar transacciones y movimientos de subcuenta
    	id_categoria INT IDENTITY(1,1) PRIMARY KEY
    	, nombre VARCHAR(30) NOT NULL
    	, descripcion VARCHAR(50)
    );
END;
GO

IF OBJECT_ID(N'dbo.cuentas', N'U') IS NULL
BEGIN
    CREATE TABLE cuentas (
    	-- Cuentas y Tarjetas de Credito
    	-- El saldo NO se almacena aqui: siempre se calcula a partir de transacciones (ver vw_saldo_cuentas)
    	id_cuenta INT IDENTITY(1,1) PRIMARY KEY
    	, nombre VARCHAR(30) NOT NULL
    	, entidad VARCHAR(30)
    	, tipo VARCHAR(20) NOT NULL -- AHORRO, CORRIENTE, TARJETA_CREDITO, EFECTIVO...
    	, limite_credito DECIMAL(18, 2)
    );
END;
GO

IF OBJECT_ID(N'dbo.subcuentas', N'U') IS NULL
BEGIN
    CREATE TABLE subcuentas (
    	id_subcuenta INT IDENTITY(1,1) PRIMARY KEY
    	, nombre VARCHAR(30)
    	, saldo DECIMAL(18, 2)
    	, saldo_meta DECIMAL(18, 2)
    	, descripcion VARCHAR(50)
    	, id_cuenta INT
    	, CONSTRAINT fk_subcuenta_id_cuenta
    	FOREIGN KEY (id_cuenta) REFERENCES cuentas(id_cuenta)
    );
END;
GO

IF OBJECT_ID(N'dbo.plan_recurrente', N'U') IS NULL
BEGIN
    CREATE TABLE plan_recurrente ( -- *
    	-- Encabezado de un plan que distribuye automaticamente el dinero que llega (ej. tu salario)
    	-- entre distintas cuentas/subcuentas objetivo. El detalle de a donde va cada parte
    	-- esta en plan_recurrente_destinos.
    	id_plan INT IDENTITY(1,1) PRIMARY KEY
    	, nombre VARCHAR(50) NOT NULL
    	, descripcion VARCHAR(200)
    	, activo BIT NOT NULL DEFAULT 1
    );
END;
GO

IF OBJECT_ID(N'dbo.plan_recurrente_destinos', N'U') IS NULL
BEGIN
    CREATE TABLE plan_recurrente_destinos ( -- *
    	-- Cada fila es una porcion del ingreso: se define por monto fijo o por porcentaje del ingreso
    	-- recibido (nunca ambos). El porcentaje siempre se calcula sobre el monto total del ingreso,
    	-- no sobre lo que quede despues de aplicar otras lineas.
    	-- activo permite desactivar una linea sin borrarla (ej. mientras ahorras para otra meta).
    	id_plan_recurrente_destino INT IDENTITY(1,1) PRIMARY KEY
    	, id_plan INT NOT NULL
    	, id_cuenta_destino INT NOT NULL
    	, id_subcuenta_destino INT NULL
    	, monto DECIMAL(18, 2) NULL
    	, porcentaje DECIMAL(5, 2) NULL
    	, activo BIT NOT NULL DEFAULT 1
    	, CONSTRAINT fk_plan_recurrente_destino_id_plan
    	FOREIGN KEY (id_plan) REFERENCES plan_recurrente(id_plan)
    	, CONSTRAINT fk_plan_recurrente_destino_id_cuenta_destino
    	FOREIGN KEY (id_cuenta_destino) REFERENCES cuentas(id_cuenta)
    	, CONSTRAINT fk_plan_recurrente_destino_id_subcuenta_destino
    	FOREIGN KEY (id_subcuenta_destino) REFERENCES subcuentas(id_subcuenta)
    	, CONSTRAINT ck_plan_recurrente_destino_monto_xor_porcentaje
    	CHECK (
    		(monto IS NOT NULL AND porcentaje IS NULL)
    		OR (monto IS NULL AND porcentaje IS NOT NULL)
    	)
    	, CONSTRAINT ck_plan_recurrente_destino_monto
    	CHECK (monto IS NULL OR monto > 0)
    	, CONSTRAINT ck_plan_recurrente_destino_porcentaje
    	CHECK (porcentaje IS NULL OR (porcentaje > 0 AND porcentaje <= 100))
    );
END;
GO

IF OBJECT_ID(N'dbo.transacciones', N'U') IS NULL
BEGIN
    CREATE TABLE transacciones ( -- *
    	id_transaccion INT IDENTITY(1,1) PRIMARY KEY
    	, fecha DATE NOT NULL
    	, tipo VARCHAR(20) NOT NULL -- INGRESO, GASTO, TRANSFERENCIA, PAGO_TARJETA
    	, monto DECIMAL(18, 2) NOT NULL
    	, id_cuenta_origen INT
    	, id_cuenta_destino INT
    	, id_categoria INT NULL
    	, descripcion VARCHAR(100)
    	, referencia VARCHAR(30)
    	, CONSTRAINT fk_trx_id_cuenta_or
    	FOREIGN KEY (id_cuenta_origen) REFERENCES cuentas(id_cuenta)
    	, CONSTRAINT fk_trx_id_cuenta_dst
    	FOREIGN KEY (id_cuenta_destino) REFERENCES cuentas(id_cuenta)
    	, CONSTRAINT fk_trx_id_categoria
    	FOREIGN KEY (id_categoria) REFERENCES categorias(id_categoria)
    	, CONSTRAINT ck_trx_monto
    	CHECK (monto > 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.movimientos_subcuenta', N'U') IS NULL
BEGIN
    CREATE TABLE movimientos_subcuenta ( -- *
    	id_movimiento_subcuenta INT IDENTITY(1,1) PRIMARY KEY
    	, fecha DATE NOT NULL
    	, tipo VARCHAR(20) NOT NULL -- ASIGNACION, GASTO, TRANSFERENCIA, REPOSICION
    	, monto DECIMAL(18, 2) NOT NULL
    	, id_subcuenta_origen INT
    	, id_subcuenta_destino INT
    	, id_categoria INT NULL
    	, descripcion VARCHAR(100)
    	, id_transaccion_relacionada INT
    	, CONSTRAINT fk_trx_id_subcuenta_or
    	FOREIGN KEY (id_subcuenta_origen) REFERENCES subcuentas(id_subcuenta)
    	, CONSTRAINT fk_trx_id_subcuenta_dst
    	FOREIGN KEY (id_subcuenta_destino) REFERENCES subcuentas(id_subcuenta)
    	, CONSTRAINT fk_mov_transaccion
    	FOREIGN KEY (id_transaccion_relacionada) REFERENCES transacciones(id_transaccion)
    	, CONSTRAINT fk_mov_id_categoria
    	FOREIGN KEY (id_categoria) REFERENCES categorias(id_categoria)
    	, CONSTRAINT ck_mov_sub_monto
    	CHECK (monto > 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.financiamientos', N'U') IS NULL
BEGIN
    CREATE TABLE financiamientos (
    	-- Compras grandes en tarjeta que se difieren en cuotas, en vez de pagarse dentro del mes (caso 6)
    	id_financiamiento INT IDENTITY(1,1) PRIMARY KEY
    	, descripcion VARCHAR(100) NOT NULL
    	, fecha_inicio DATE NOT NULL
    	, monto_total DECIMAL(18, 2) NOT NULL
    	, numero_cuotas INT NOT NULL
    	, tasa_interes DECIMAL(5, 2) NULL
    	, id_transaccion_origen INT NULL
    	, CONSTRAINT fk_financiamiento_id_transaccion_origen
    	FOREIGN KEY (id_transaccion_origen) REFERENCES transacciones(id_transaccion)
    	, CONSTRAINT ck_financiamiento_monto_total
    	CHECK (monto_total > 0)
    	, CONSTRAINT ck_financiamiento_numero_cuotas
    	CHECK (numero_cuotas > 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.cuotas_financiamiento', N'U') IS NULL
BEGIN
    CREATE TABLE cuotas_financiamiento (
    	-- Calendario de pago de un financiamiento. id_transaccion_pago y fecha_pago quedan en NULL
    	-- hasta que la cuota realmente se pague.
    	id_cuota_financiamiento INT IDENTITY(1,1) PRIMARY KEY
    	, id_financiamiento INT NOT NULL
    	, numero_cuota INT NOT NULL
    	, fecha_vencimiento DATE NOT NULL
    	, monto DECIMAL(18, 2) NOT NULL
    	, id_transaccion_pago INT NULL
    	, fecha_pago DATE NULL
    	, CONSTRAINT fk_cuota_financiamiento_id_financiamiento
    	FOREIGN KEY (id_financiamiento) REFERENCES financiamientos(id_financiamiento)
    	, CONSTRAINT fk_cuota_financiamiento_id_transaccion_pago
    	FOREIGN KEY (id_transaccion_pago) REFERENCES transacciones(id_transaccion)
    	, CONSTRAINT ck_cuota_financiamiento_monto
    	CHECK (monto > 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.obligaciones', N'U') IS NULL
BEGIN
    CREATE TABLE obligaciones ( -- *
    	id_obligacion INT IDENTITY(1,1) PRIMARY KEY

    	, fecha_creacion DATE NOT NULL

    	, tipo VARCHAR(20) NOT NULL -- POR_COBRAR, POR_PAGAR, REPOSICION

    	, monto DECIMAL(18,2) NOT NULL

    	, descripcion VARCHAR(100)

    	, id_persona INT NULL

    	-- Origen: de donde salio (o por que se genero) el dinero que dio pie a esta obligacion
    	, id_transaccion_origen INT NULL
    	, id_movimiento_subcuenta_origen INT NULL

    	-- Destino de resolucion: a donde debe llegar el dinero cuando se salde la deuda o se repona el retiro
    	, id_cuenta_destino_resolucion INT NOT NULL
    	, id_subcuenta_destino_resolucion INT NULL

    	, CONSTRAINT ck_obligacion_monto
    		CHECK (monto > 0)

    	, CONSTRAINT fk_obligacion_persona
    		FOREIGN KEY (id_persona)
    		REFERENCES personas(id_persona)

    	, CONSTRAINT fk_obligacion_transaccion
    		FOREIGN KEY (id_transaccion_origen)
    		REFERENCES transacciones(id_transaccion)

    	, CONSTRAINT fk_obligacion_mov_sub
    		FOREIGN KEY (id_movimiento_subcuenta_origen)
    		REFERENCES movimientos_subcuenta(id_movimiento_subcuenta)

    	, CONSTRAINT fk_obligacion_cuenta_destino
    		FOREIGN KEY (id_cuenta_destino_resolucion)
    		REFERENCES cuentas(id_cuenta)

    	, CONSTRAINT fk_obligacion_subcuenta_destino
    		FOREIGN KEY (id_subcuenta_destino_resolucion)
    		REFERENCES subcuentas(id_subcuenta)
    );
END;
GO

IF OBJECT_ID(N'dbo.liquidaciones_obligacion', N'U') IS NULL
BEGIN
    CREATE TABLE liquidaciones_obligacion ( -- *
    	id_liquidacion INT IDENTITY(1,1) PRIMARY KEY

    	, id_obligacion INT NOT NULL

    	, fecha DATE NOT NULL

    	, monto DECIMAL(18,2) NOT NULL

    	, id_transaccion INT NULL

    	, id_movimiento_subcuenta INT NULL

    	, descripcion VARCHAR(100)

    	, CONSTRAINT fk_liq_obligacion
    		FOREIGN KEY (id_obligacion)
    		REFERENCES obligaciones(id_obligacion)

    	, CONSTRAINT fk_liq_transaccion
    		FOREIGN KEY (id_transaccion)
    		REFERENCES transacciones(id_transaccion)

    	, CONSTRAINT fk_liq_mov_sub
    		FOREIGN KEY (id_movimiento_subcuenta)
    		REFERENCES movimientos_subcuenta(id_movimiento_subcuenta)

    	, CONSTRAINT ck_liq_monto
    		CHECK (monto > 0)
    );
END;

-- ---------------------------------------------------------------------------------------------------
-- Vistas
-- ---------------------------------------------------------------------------------------------------
GO

CREATE OR ALTER VIEW vw_saldo_cuentas AS
-- Saldo calculado de cada cuenta a partir de transacciones (nunca se almacena).
-- Cuentas normales (ahorro, corriente, efectivo): saldo = entradas - salidas.
-- Tarjetas de credito: saldo = salidas - entradas (representa la deuda actual, no dinero disponible).
SELECT
	cuentas.id_cuenta
	, cuentas.nombre
	, cuentas.tipo
	, CASE
		WHEN cuentas.tipo = 'TARJETA_CREDITO' THEN salidas.total - entradas.total
		ELSE entradas.total - salidas.total
	END AS saldo_calculado
FROM cuentas
CROSS APPLY (
	SELECT ISNULL(SUM(monto), 0) AS total
	FROM transacciones
	WHERE id_cuenta_destino = cuentas.id_cuenta
) entradas
CROSS APPLY (
	SELECT ISNULL(SUM(monto), 0) AS total
	FROM transacciones
	WHERE id_cuenta_origen = cuentas.id_cuenta
) salidas;
GO

CREATE OR ALTER VIEW vw_estado_obligaciones AS
-- Estado de cada obligacion a partir de sus liquidaciones (reemplaza monto_resuelto y fecha_resuelta,
-- que ya no se almacenan: liquidaciones_obligacion es la fuente de verdad).
SELECT
	obligaciones.id_obligacion
	, obligaciones.tipo
	, obligaciones.monto
	, ISNULL(liquidaciones.monto_liquidado, 0) AS monto_liquidado
	, obligaciones.monto - ISNULL(liquidaciones.monto_liquidado, 0) AS monto_pendiente
	, CASE
		WHEN ISNULL(liquidaciones.monto_liquidado, 0) >= obligaciones.monto THEN 1
		ELSE 0
	END AS resuelta
	, liquidaciones.fecha_ultima_liquidacion
FROM obligaciones
LEFT JOIN (
	SELECT
		id_obligacion
		, SUM(monto) AS monto_liquidado
		, MAX(fecha) AS fecha_ultima_liquidacion
	FROM liquidaciones_obligacion
	GROUP BY id_obligacion
) liquidaciones
	ON liquidaciones.id_obligacion = obligaciones.id_obligacion;
GO

-- ---------------------------------------------------------------------------------------------------
-- Ciclos (periodos entre pagas). Mismo contenido que migraciones/001_ciclos.sql.
-- ---------------------------------------------------------------------------------------------------

IF OBJECT_ID('dbo.ciclo_config', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ciclo_config (
        id_ciclo_config   INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ciclo_config PRIMARY KEY,
        nombre            VARCHAR(30)  NOT NULL CONSTRAINT UQ_ciclo_config_nombre UNIQUE,
        -- Si el dia de inicio cae en fin de semana: NINGUNO, ANTERIOR (dia habil anterior) o SIGUIENTE.
        ajuste_fin_semana VARCHAR(10)  NOT NULL CONSTRAINT DF_ciclo_config_ajuste DEFAULT 'ANTERIOR',
        es_principal      BIT          NOT NULL CONSTRAINT DF_ciclo_config_principal DEFAULT 0,
        activo            BIT          NOT NULL CONSTRAINT DF_ciclo_config_activo DEFAULT 1,
        CONSTRAINT CK_ciclo_config_ajuste CHECK (ajuste_fin_semana IN ('NINGUNO', 'ANTERIOR', 'SIGUIENTE'))
    );
    -- Solo una configuracion puede ser la principal.
    CREATE UNIQUE INDEX UX_ciclo_config_principal ON dbo.ciclo_config (es_principal) WHERE es_principal = 1;
END;
GO

IF OBJECT_ID('dbo.ciclo_config_dia', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ciclo_config_dia (
        id_ciclo_config INT     NOT NULL,
        -- Dia del mes en que arranca un ciclo (31 = ultimo dia del mes).
        dia             TINYINT NOT NULL,
        CONSTRAINT PK_ciclo_config_dia PRIMARY KEY (id_ciclo_config, dia),
        CONSTRAINT CK_ciclo_config_dia CHECK (dia BETWEEN 1 AND 31),
        CONSTRAINT FK_ciclo_config_dia_config FOREIGN KEY (id_ciclo_config)
            REFERENCES dbo.ciclo_config (id_ciclo_config) ON DELETE CASCADE
    );
END;
GO

IF OBJECT_ID('dbo.ciclo', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ciclo (
        id_ciclo        INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ciclo PRIMARY KEY,
        id_ciclo_config INT  NOT NULL,
        fecha_inicio    DATE NOT NULL,
        fecha_fin       DATE NOT NULL,           -- inclusive
        ajustado        BIT  NOT NULL CONSTRAINT DF_ciclo_ajustado DEFAULT 0,
        CONSTRAINT FK_ciclo_config FOREIGN KEY (id_ciclo_config)
            REFERENCES dbo.ciclo_config (id_ciclo_config) ON DELETE CASCADE,
        CONSTRAINT UQ_ciclo_inicio UNIQUE (id_ciclo_config, fecha_inicio),
        CONSTRAINT CK_ciclo_fechas CHECK (fecha_fin >= fecha_inicio)
    );
END;
GO
