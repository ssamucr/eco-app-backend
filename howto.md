0. Crea el usuario con permisos adecuados en la base de datos.

CREATE LOGIN eco_app WITH PASSWORD = 'contraseña';

USE eco;

CREATE USER eco_app FOR LOGIN eco_app;

ALTER ROLE db_owner ADD MEMBER eco_app;

1. Configura db.py con tu usuario y contraseña de SQL server. (Requiere driver ODBC Driver 17 for SQL Server)
2. pip install -r requirements.txt
3. uvicorn main:app --reload
4. Abre http://localhost:8000/docs