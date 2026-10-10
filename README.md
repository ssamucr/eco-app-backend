# Eco — API

API REST de **Eco**, una app de finanzas personales: cuentas y tarjetas, subcuentas (metas), movimientos,
personas y obligaciones (quién te debe / a quién debes), planes recurrentes, financiamientos en cuotas y una
vista de analítica con exportación a Excel y PDF. El saldo de cada cuenta se calcula a partir de sus movimientos,
y el servidor entrega ya calculados los totales, porcentajes y calendarios que muestra el frontend.

Hecha con **FastAPI** + **PostgreSQL** (`psycopg2`). Pensada para correr con **Supabase** (base de datos y login)
y **Render** (la API). El frontend vive en [eco-app-frontend](https://github.com/ssamucr/eco-app-frontend).

## Requisitos

- Python 3.8 o superior
- Un Postgres: el de Supabase, o uno local (por ejemplo con Docker)

## Puesta en marcha local

1. Un Postgres local (opcional, para desarrollo):

   ```bash
   docker run -d --name eco-pg -e POSTGRES_PASSWORD=TU_CLAVE -e POSTGRES_DB=eco -p 5433:5432 postgres:16
   ```

2. Crea las tablas. El script `database/eco.sql` es idempotente: se puede volver a ejecutar sin riesgo y nunca
   borra nada. Con `psql`: `psql "$DATABASE_URL" -f database/eco.sql`; o pégalo en el editor SQL de Supabase.

3. Instala las dependencias:

   ```bash
   python -m venv venv
   venv\Scripts\activate        # en Linux/macOS: source venv/bin/activate
   pip install -r requirements.txt
   ```

4. Copia `.env.example` como `.env` y completa `DATABASE_URL`. Para desarrollo local sin login agrega
   `ECO_AUTH_DESACTIVADA=1` (nunca en producción). El `.env` no se sube a git.

5. Arranca el servidor y abre la documentación interactiva en <http://localhost:8000/docs>:

   ```bash
   uvicorn main:app --reload --port 8000
   ```

## Variables de entorno

| Variable                  | Para qué sirve                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------- |
| `DATABASE_URL`            | Cadena de conexión de Postgres (en Supabase usa el *Session pooler*)                        |
| `SUPABASE_URL`            | `https://<proyecto>.supabase.co`: de ahí sale la clave pública que verifica los tokens      |
| `ECO_USUARIOS_PERMITIDOS` | Correos (o ids de usuario) separados por coma que pueden entrar. Vacío = nadie entra        |
| `ECO_CORS_ORIGENES`       | Orígenes del frontend permitidos, separados por coma (por defecto `http://localhost:5173`)  |
| `ECO_AUTH_DESACTIVADA`    | `1` solo en desarrollo local: la API no pide token                                          |
| `PORT`                    | Lo define Render; en local se usa 8000                                                      |

## Seguridad

- Toda ruta, salvo `GET /health`, exige un token de Supabase Auth de un usuario de `ECO_USUARIOS_PERMITIDOS`.
  Si falta la configuración, la API rechaza todo (falla cerrado).
- Las tablas tienen RLS activado y sin políticas, y se revoca el acceso a los roles de la API pública de
  Supabase: solo esta API (con su propia conexión) puede leer y escribir.
- Desactiva el registro público en Supabase (Authentication → Sign In / Providers → *Allow new users to sign up*).

## Despliegue

Con el `Dockerfile` incluido (Render lo detecta). Define en el servicio las variables de la tabla; `DATABASE_URL`
va como secreto. Render gratis duerme tras 15 minutos sin uso y tarda cerca de un minuto en despertar.

## Base de datos

- `database/eco.sql`: esquema completo (estructura, sin datos): tablas, vistas, RLS y permisos.
- `database/migrar_desde_sqlserver.py`: copia única y verificada de los datos desde la versión anterior en SQL
  Server (requiere `pyodbc`, que ya no es dependencia de la API). Solo corre sobre una base vacía.
