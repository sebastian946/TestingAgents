# app — backend (FastAPI + SQLAlchemy)

La documentación completa (puesta en marcha, variables de entorno, API, convenciones)
está en el [README de la raíz del repositorio](../README.md).

Comandos rápidos, desde esta carpeta:

```powershell
uv sync                          # dependencias
uv run python -m db.init_db      # crear tablas
uv run uvicorn main:app --reload # API en http://127.0.0.1:8000/docs
```
