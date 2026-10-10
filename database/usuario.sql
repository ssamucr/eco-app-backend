-- Crea el usuario con el que se conecta la aplicacion (el que va en el archivo .env del backend).
-- Reemplaza <TU_CLAVE> por una contrasena larga y propia antes de ejecutar. No subas este archivo con la clave puesta.
-- Ejecutar despues de database/eco.sql, con un login administrador:
--     sqlcmd -S localhost\SQLEXPRESS -E -i database\usuario.sql

USE master;
GO
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'eco_app')
    CREATE LOGIN eco_app WITH PASSWORD = N'<TU_CLAVE>';
GO
USE eco;
GO
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'eco_app')
    CREATE USER eco_app FOR LOGIN eco_app;
GO
ALTER ROLE db_owner ADD MEMBER eco_app;
GO
