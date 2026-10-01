# Testing Agents

SaaS de testing web asistido por agentes. Recibe la URL de un sitio, lo explora, genera
escenarios de prueba con un LLM y entrega un reporte. Este repositorio contiene el **Paso 1
(Motor MVP)**: API, base de datos, cola y worker.

El plan de trabajo vive en Notion:
[Tablero · SaaS Web Testing Agents — Paso 1 (Motor MVP)](https://app.notion.com/p/7b50aed7e8c34d909f606ed3c49f1642).

## Estado actual

| Bloque | Tarea | Estado |
|---|---|---|
| A — Esqueleto | Repositorio y estructura de carpetas | Hecho |
| A — Esqueleto | Postgres y Redis con docker-compose | Hecho |
| A — Esqueleto | API FastAPI: `POST /jobs` y `GET /jobs/{id}` | Hecho |
| A — Esqueleto | Modelos SQLAlchemy + CRUD | Hecho (falta Alembic) |
| A — Esqueleto | Validación anti-SSRF de la URL | Pendiente |
| B — Queue y Worker | Encolar jobs con RQ, worker, manejo de fallos | Pendiente |
| C a F | Explorer, Designer, Documenter, Calidad | Pendiente |

## Arquitectura

```
cliente ──POST /jobs──▶ API (FastAPI) ──▶ PostgreSQL  ◀── worker (RQ)  [pendiente]
             ◀──202────┘                       ▲              ▲
cliente ──GET /jobs/{id}──▶ API ───────────────┘              │
                                                 Redis (cola) ┘  [pendiente]
```

Patrón de job asíncrono: `POST /jobs` solo guarda el job en estado `queued` y responde `202`.
El trabajo real (crawl, LLM, reporte) lo hará el worker en otro proceso; el cliente consulta
el avance con `GET /jobs/{id}`. API y worker comparten estado **por la base de datos**, nunca
por memoria.

### Modelo de datos

```
job (uuid) ──1:N──▶ pages (serial) ──1:N──▶ scenarios (serial)
```

| Tabla | Campos clave |
|---|---|
| `job` | `id` UUID, `url`, `status` (`queued` / `running` / `done` / `failed`), `pages_crawled`, `total_scenarios`, `report_path`, `error`, `created_at`, `started_at`, `finished_at` |
| `pages` | `job_id`, `url`, `title`, `page_type`, `screenshot_path` |
| `scenarios` | `job_id`, `page_id`, `scenario_code` (`SC-001`…, único por job), `title`, `steps` (JSONB), `expected_result`, `priority`, `category` |

Borrar un job elimina en cascada sus páginas y escenarios.

## Estructura del repositorio

```
TestingAgents/
├── .env                  # variables locales (NO se versiona)
├── .env.example          # plantilla de variables
├── pyrightconfig.json    # apunta Pylance al venv de app/
├── .vscode/settings.json # intérprete de VS Code
├── FE/                   # frontend (vacío por ahora)
└── app/                  # backend Python (proyecto uv)
    ├── pyproject.toml / uv.lock
    ├── docker-compose.yml   # Postgres 16 + Redis 7
    ├── main.py              # app FastAPI, CORS, /Health
    ├── config/variables.py  # Settings (pydantic-settings) leídos del .env
    ├── db/
    │   ├── conn.py          # engine, sesión, Base, dependencia get_db
    │   ├── models_db/models_db.py  # tablas Job, Page, Scenario
    │   ├── db_crud.py       # operaciones de base de datos
    │   └── init_db.py       # crea las tablas (atajo de desarrollo)
    ├── models/models.py     # esquemas Pydantic de entrada/salida
    ├── routes/routes.py     # endpoints /jobs
    ├── agents/  worker/  tests/   # pendientes
    └── .venv/               # creado por uv (NO se versiona)
```

## Requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/) (gestiona Python y dependencias; instala solo Python 3.12)
- Opcional: [DBeaver](https://dbeaver.io/) para ver la base de datos

## Puesta en marcha

### 1. Variables de entorno

```powershell
copy .env.example .env
```

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `DB_USER` | usuario de PostgreSQL | `myuser` |
| `DB_PASSWORD` | contraseña de PostgreSQL | `password` |
| `DB_NAME` | base de datos | `local` |
| `DB_PORT` | puerto expuesto en tu máquina | `5433` |
| `ENDPOINT` | host de PostgreSQL visto desde la API | `localhost` |
| `REDIS_PORT` | puerto expuesto de Redis | `6379` |

`DB_PORT` es `5433` y no `5432` a propósito: si tienes un PostgreSQL instalado en Windows,
ocupa el 5432 y DBeaver o la API se conectarían a ese en vez de al contenedor
(síntoma: `password authentication failed for user "myuser"`).

El usuario y la contraseña se fijan **la primera vez** que se crea el volumen de Postgres.
Si los cambias después, hay que recrearlo: `docker compose ... down -v` (borra los datos).

### 2. Base de datos y Redis

Desde la raíz del repositorio:

```powershell
docker compose --env-file .env -f app/docker-compose.yml up -d
docker ps        # postgres_db y redis_cache deben estar "Up"
```

El `--env-file .env` es necesario porque el compose está en `app/` y el `.env` en la raíz.

### 3. Dependencias de Python

```powershell
cd app
uv sync
```

Crea `app/.venv` con Python 3.12 y todo lo de `uv.lock`. No hace falta activar el entorno:
`uv run <comando>` lo usa automáticamente.

### 4. Crear las tablas

```powershell
uv run python -m db.init_db
```

Atajo de desarrollo que crea las tablas desde los modelos. La tarea WTA-4 del tablero pide
reemplazarlo por migraciones con Alembic (ver "Siguientes pasos").

### 5. Levantar la API

```powershell
uv run uvicorn main:app --reload
```

- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/Health

## Uso de la API

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/jobs` | `202` con el job creado; `422` si la URL no es válida |
| `GET` | `/jobs` | lista de jobs (más recientes primero), `limit` y `offset` opcionales |
| `GET` | `/jobs/{id}` | el job; `404` si no existe; `422` si el id no es UUID |
| `GET` | `/jobs/{id}/pages` | páginas exploradas del job |
| `GET` | `/jobs/{id}/scenarios` | escenarios generados del job |
| `DELETE` | `/jobs/{id}` | `204`; borra también páginas y escenarios |

```powershell
# crear un job
$job = Invoke-RestMethod -Method Post http://127.0.0.1:8000/jobs `
  -ContentType "application/json" -Body '{"url": "https://example.com"}'

# consultarlo
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.id)"
```

## Verificar y depurar

```powershell
# estado de los contenedores
docker ps
docker logs postgres_db --tail 20

# Postgres y Redis responden
docker exec -it postgres_db pg_isready -U myuser -d local
docker exec -it redis_cache redis-cli ping          # PONG

# SQL directo
docker exec -it postgres_db psql -U myuser -d local -c "SELECT id, url, status FROM job;"

# borrar todas las tablas y empezar de cero
docker exec -it postgres_db psql -U myuser -d local -c "DROP TABLE scenarios, pages, job; DROP TYPE job_status;"
```

**DBeaver**: nueva conexión PostgreSQL con host `localhost`, puerto `5433`, base `local`,
usuario `myuser`, contraseña `password`.

**Apagar todo**: `docker compose --env-file .env -f app/docker-compose.yml down`
(añade `-v` para borrar también los datos).

## Desarrollo

### VS Code

El proyecto Python vive en `app/`, no en la raíz, así que VS Code no detecta el `.venv`
solo. `pyrightconfig.json` y `.vscode/settings.json` ya lo apuntan; si aun así los imports
salen en rojo: `Ctrl+Shift+P` → *Python: Select Interpreter* → *Enter interpreter path* →
`app\.venv\Scripts\python.exe`, y luego *Developer: Reload Window*.

### Añadir dependencias

```powershell
cd app
uv add nombre-del-paquete      # actualiza pyproject.toml y uv.lock
```

Commitea `pyproject.toml` y `uv.lock` juntos.

### Convenciones

- Las funciones de `db_crud.py` reciben la sesión como parámetro (`db: Session`); nunca
  usan una sesión global. En las rutas se obtiene con `Depends(get_db)`.
- Entrada y salida de la API siempre con esquemas Pydantic (`models/models.py`); los modelos
  SQLAlchemy no se exponen directamente.
- `status` de un job es el enum `JobStatus`, no texto libre.
- Operaciones de contador (`pages_crawled`, `total_scenarios`) se hacen con `UPDATE`
  atómico en la misma transacción que el insert, con commit por página.
- `create_engine(..., echo=True)` imprime el SQL en consola; desactívalo si molesta.

## Siguientes pasos (según el tablero)

1. **Alembic** (cierra WTA-4):
   ```powershell
   cd app
   uv add alembic
   uv run alembic init alembic
   ```
   En `alembic/env.py`: importar `Base` y `DATABASE_URL` de `db.conn`, importar
   `db.models_db.models_db`, y poner `target_metadata = Base.metadata`. Luego
   `uv run alembic revision --autogenerate -m "initial tables"` y
   `uv run alembic upgrade head`. Si ya creaste tablas con `init_db`, bórralas antes.
2. **Validación anti-SSRF** de la URL (WTA-5): rechazar IPs privadas, `localhost`, etc.
3. **RQ + worker** (WTA-6 a 8): `uv add rq`; el worker usa `mark_job_running`,
   `mark_job_done` y `mark_job_failed` de `db_crud.py`.
4. **Explorer** (WTA-9 a 14): `create_page` ya incrementa `pages_crawled`.
5. **Designer** (WTA-15 a 18): `create_scenarios` ya genera los códigos `SC-XXX`.
