from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.routes import router

app = FastAPI(title="Testing Jobs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(router)

@app.get("/Health", summary="Health check endpoint")
def root():
    return {"message": "Health check ok", "status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)