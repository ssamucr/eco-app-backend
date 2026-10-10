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
- Permisos de administrador en SQL Server para crear la base de datos (por ejemplo, tu usuario de Windows)

## Puesta en marcha

1. Crea la base de datos y el usuario de la aplicación. El script `database/eco.sql` crea la base `eco` completa
   (tablas, restricciones y vistas, sin datos) y se puede volver a ejecutar sin riesgo: nunca borra nada.

   ```bash
   sqlcmd -S localhost\SQLEXPRESS -E -i database\eco.sql
   ```

   Luego abre `database/usuario.sql`, cambia `<TU_CLAVE>` por una contraseña propia y ejecútalo:

   ```bash
   sqlcmd -S localhost\SQLEXPRESS -E -i database\usuario.sql
   ```

2. Instala las dependencias:

   ```bash
   python -m venv venv
   venv\Scripts\activate        # en Linux/macOS: source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configura la conexión: copia `.env.example` como `.env` y completa tus datos
   (`ECO_DB_USUARIO`, `ECO_DB_CONTRASENA` —la misma clave del paso 1— y `ECO_DB_SERVIDOR` si no es
   `localhost\SQLEXPRESS`). El `.env` no se sube a git.

4. Arranca el servidor:

   ```bash
   uvicorn main:app --reload --port 8000
   ```

5. Documentación interactiva en <http://localhost:8000/docs>.

> CORS solo permite peticiones desde `http://localhost:5173` (el frontend en desarrollo).
> Para otro origen, cambia `allow_origins` en `main.py`.

## Base de datos

- `database/eco.sql`: esquema completo (estructura, sin datos). Es la referencia para crear la base desde cero.
- `database/usuario.sql`: crea el usuario de la aplicación (hay que poner tu propia contraseña).
