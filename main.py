from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import collector
import uvicorn
import os
import logging
from typing import List, Dict, Any

# ---------------- CONFIG ----------------
APP_NAME = "SPA Processos Pendentes API"
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------- APP ----------------
app = FastAPI(title=APP_NAME, version="1.0.0")

# ---------------- CORS ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # 🔐 mais seguro
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------- MODELS ----------------
class ResumoItem(BaseModel):
    instancia: str
    prefixo: str
    status: str
    total_pendentes: int

class DetalhesResponse(BaseModel):
    instancia: str
    status: str
    total: int
    processos: List[Dict[str, Any]]

# ---------------- ERROR HANDLER ----------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"[ERROR] {request.url} -> {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erro interno no servidor"}
    )

# ---------------- HEALTHCHECK ----------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---------------- DASHBOARD ----------------
@app.get("/", response_class=FileResponse)
async def read_dashboard():
    file_path = "index.html"

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail="Arquivo index.html não encontrado"
        )

    return FileResponse(file_path)

# ---------------- RESUMO ----------------
@app.get("/processos/resumo", response_model=List[ResumoItem])
def get_resumo():
    try:
        results = collector.collect_all()

        resumo = [
            {
                "instancia": r["name"],
                "prefixo": r["prefix"],
                "status": r["status"],
                "total_pendentes": len(r["df"]) if r["df"] is not None else 0
            }
            for r in results
            if r.get("name")
        ]

        return resumo

    except Exception as e:
        logger.error(f"[RESUMO ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar resumo")

# ---------------- DETALHES ----------------
@app.get("/processos/detalhes/{prefix}", response_model=DetalhesResponse)
def get_detalhes(prefix: str):
    try:
        config = collector.get_config(prefix.upper())

        if not config["url"]:
            raise HTTPException(
                status_code=404,
                detail="Configuração não encontrada"
            )

        result = collector.process(config)

        if result["df"] is None:
            return {
                "instancia": result["name"],
                "status": result["status"],
                "total": 0,
                "processos": []
            }

        return {
            "instancia": result["name"],
            "status": result["status"],
            "total": len(result["df"]),
            "processos": result["df"].to_dict(orient="records")
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"[DETALHES ERROR] {prefix} -> {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar detalhes")

# ---------------- START ----------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",  # 🔥 pronto pra docker
        port=8000,
        reload=True
    )