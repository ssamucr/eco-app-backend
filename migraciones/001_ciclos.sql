-- Ciclos de dinero (por ejemplo, el período entre dos quincenas).
-- Script idempotente: se puede ejecutar varias veces. Solo agrega tablas nuevas.

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
