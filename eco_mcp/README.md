# eco_mcp — servidor MCP de Eco

Permite que Claude (Claude.ai, móvil, Desktop o Code) consulte y registre datos de Eco por medio de un conector remoto.
Vive en su propio paquete: `main.py`, `auth.py` y el resto de la API no lo conocen.

## Cómo está armado

```
Claude ──HTTPS──► /mcp  (eco_mcp.asgi)
                   │  verifica el token OAuth (seguridad.py)
                   │  herramientas (lectura.py / escritura.py)
                   ▼
                  ClienteApi (api.py): llama a la API de Eco EN EL MISMO PROCESO con el mismo token
                   ▼
                  main.app  → auth.py (token + lista de usuarios) → reglas de negocio → Postgres
```

- `asgi.py` es el punto de entrada (`uvicorn eco_mcp.asgi:app`). Reparte: `/mcp` y `/.well-known/*` van al MCP; todo lo
  demás va a la API sin modificar. Si falta configuración, solo se sirve la API (nunca un MCP abierto).
- El MCP **no toca la base ni repite reglas**: cada herramienta llama a un endpoint de la API, que valida y calcula.
  La API vuelve a exigir el token y la lista de usuarios, así que hay dos controles independientes.

## Autenticación (OAuth 2.1, sin credenciales fijas)

Supabase Auth actúa como servidor OAuth (registro dinámico de clientes). Al agregar el conector, Claude descubre
la autenticación por `/.well-known/oauth-protected-resource/mcp`, se registra, y el usuario aprueba el acceso en la
pantalla de consentimiento del frontend (`/oauth/consent`). El token se renueva solo.

`seguridad.VerificadorEco` acepta un token solo si: firma ES256 válida (JWKS de Supabase), emisor y audiencia correctos,
no vencido, **lleva `client_id`** (es decir, salió del flujo OAuth; el token de la sesión web no sirve) y el usuario está
en `ECO_USUARIOS_PERMITIDOS`.

Limitación conocida: Supabase no soporta RFC 8707 (resource indicators), así que el token no queda atado a este servidor.
Mientras el proyecto de Supabase solo lo use Eco y haya un único usuario permitido, el riesgo es bajo.

## Herramientas

Lectura: `resumen`, `cuentas`, `movimientos`, `analitica`, `ciclos`, `obligaciones`, `planes_recurrentes`,
`financiamientos`, `catalogos`.

Escritura (todas en dos pasos: sin `confirmar` solo muestran una vista previa; con `confirmar=true` guardan):
`registrar_gasto`, `registrar_ingreso`, `registrar_transferencia`, `pagar_tarjeta`, `crear_obligacion`,
`liquidar_obligacion`. No hay herramientas para borrar ni editar. Un movimiento idéntico (mismo día, tipo, monto,
cuentas y descripción) se rechaza para evitar duplicados por reintentos, salvo `permitir_duplicado=true`.
`registrar_gasto` acepta `generar_reposicion=true` en gastos de tarjeta: crea además la obligación de reposición por el
mismo monto (se salda luego con `liquidar_obligacion`, pagando la tarjeta desde una cuenta).
`asignar_a_subcuenta` reparte saldo sin asignar de una cuenta (p. ej. un interés) a una de sus subcuentas; no mueve dinero entre cuentas.

## Variables de entorno

| Variable | Para qué |
|---|---|
| `SUPABASE_URL` | Proyecto que emite los tokens (el mismo de la API). |
| `ECO_USUARIOS_PERMITIDOS` | Correos o ids con acceso (el mismo de la API). |
| `ECO_MCP_URL_PUBLICA` | URL pública de la API, p. ej. `https://api.eco.samu0x.dev` (sin `/mcp`). Activa el MCP. |
| `ECO_MCP_HOSTS_PERMITIDOS` | Hosts que pueden llamar a `/mcp` (por defecto, el de la URL pública). |
| `ECO_MCP_ESCRITURA` | `0` deja solo lectura. |
| `ECO_MCP_ZONA_UTC` | Desplazamiento de "hoy" para fechas omitidas (por defecto `-5`). |
| `ECO_MCP_SIN_AUTENTICACION` | `1` apaga el login. Solo con URL pública local (pruebas). |

## Probar en local (nunca contra producción)

Contra una copia desechable de la base:

```bash
ECO_AUTH_DESACTIVADA=1 ECO_MCP_SIN_AUTENTICACION=1 ECO_MCP_URL_PUBLICA=http://localhost:8000 \
  uvicorn eco_mcp.asgi:app --port 8000
python -m eco_mcp.pruebas.probar_local        # herramientas + escrituras (limpia lo que crea)
python -m eco_mcp.pruebas.probar_seguridad    # verificador de tokens
```

Con login activado (sin escribir nada): arranca con `SUPABASE_URL`, `ECO_USUARIOS_PERMITIDOS` y
`ECO_MCP_URL_PUBLICA=http://localhost:8002` y ejecuta `PROBAR_URL=http://localhost:8002 python -m eco_mcp.pruebas.probar_seguridad`.

Requiere Python 3.10+ (la API sola corre con menos; la imagen de Render usa 3.12).

## Alta de un cliente en Supabase

Dashboard → Authentication → OAuth Server: activar, ruta de autorización `/oauth/consent`, registro dinámico.
Site URL en Authentication → URL Configuration: `https://eco.samu0x.dev`. Para revocar el acceso de Claude, borrar su
cliente en esa misma pantalla.
