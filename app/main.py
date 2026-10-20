import warnings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.variables import settings
from observability import init_sentry
from routes.auth_routes import router as auth_router
from routes.billing_routes import router as billing_router
from routes.routes import router
from security.auth import MIN_SECRET_LENGTH

init_sentry("api")
app = FastAPI(title="Testing Jobs")

# "*" only works for local development: a browser refuses credentials with a wildcard
# origin, and in production CORS_ORIGINS names the frontend's exact origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials="*" not in settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(router)

if not settings.auth_secret_key:
    raise RuntimeError(
        "AUTH_SECRET_KEY is not set: the API signs login tokens with it. Add it to .env, e.g. "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
if len(settings.auth_secret_key) < MIN_SECRET_LENGTH or "change-me" in settings.auth_secret_key:
    warnings.warn(
        "AUTH_SECRET_KEY is short or still the placeholder: tokens can be forged. Generate one with "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\"",
        stacklevel=1,
    )


@app.get("/Health", summary="Health check endpoint")
def root():
    return {"message": "Health check ok", "status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
