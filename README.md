# Eco — API

API REST de **Eco**, una app de finanzas personales: cuentas y tarjetas, subcuentas (metas), movimientos,
personas y obligaciones (quién te debe / a quién debes), planes recurrentes y financiamientos en cuotas.
El saldo de cada cuenta se calcula a partir de sus movimientos, y el servidor entrega ya calculados los totales,
porcentajes y calendarios que muestra el frontend.

Hecha con **FastAPI** + **SQL Server** (`pyodbc`). El frontend vive en
[eco-app-frontend](https://github.com/ssamucr/eco-app-frontend).

## Requisitos

- Python 3.8 o superior
- SQL Server (por ejemplo, SQL Server Express) y el driver **ODBC Driver 17 for SQL Server**
- Una base de datos llamada `eco` con las tablas y vistas de la app (`vw_saldo_cuentas`, `vw_estado_obligaciones`, ...).
  El script del esquema no está en este repositorio.

## Puesta en marcha

1. Crea el usuario de la aplicación en SQL Server:

   ```sql
   CREATE LOGIN eco_app WITH PASSWORD = 'tu_clave';
   USE eco;
   CREATE USER eco_app FOR LOGIN eco_app;
   ALTER ROLE db_owner ADD MEMBER eco_app;
   ```

2. Instala las dependencias:

   ```bash
   python -m venv venv
   venv\Scripts\activate        # en Linux/macOS: source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configura la conexión: copia `.env.example` como `.env` y completa tus datos
   (`ECO_DB_USUARIO`, `ECO_DB_CONTRASENA`, y `ECO_DB_SERVIDOR` si no es `localhost\SQLEXPRESS`).
   El `.env` no se sube a git.

4. Arranca el servidor:

   ```bash
   uvicorn main:app --reload --port 8000
   ```

5. Documentación interactiva en <http://localhost:8000/docs>.

> CORS solo permite peticiones desde `http://localhost:5173` (el frontend en desarrollo).
> Para otro origen, cambia `allow_origins` en `main.py`.
