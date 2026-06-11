from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Pan Majster",
    description="Robocze srodowisko aplikacji Pan Majster.",
    version="0.1.0",
)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pan-majster"}

