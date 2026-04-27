"""
main.py — API FastAPI para o painel SPA Processos Pendentes
Melhorias:
  - Scheduler interno (APScheduler) que recarrega dados a cada 1h
  - Rate limiting por IP (slowapi)
  - Validação de parâmetros de data
  - CORS configurável por variável de ambiente
  - Endpoint /cache/refresh para forçar recarga manual
  - Compatível com Vercel (via Mangum) e execução local
"""
from mangum import Mangum
import os
import logging
import re
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Request, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.background import BackgroundScheduler

import collector

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ─── RATE LIMITER ────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per hour", "20 per minute"])

# ─── SCHEDULER (recarrega cache a cada 1h) ───────────────────────────────────
scheduler = BackgroundScheduler()


def _scheduled_refresh():
    """Executa sem filtro de data para pré-aquecer o cache padrão."""
    logger.info("[SCHEDULER] Iniciando recarga automática do cache...")
    try:
        collector.invalidate_cache()
        collector.collect_all(use_cache=False)
        logger.info("[SCHEDULER] Cache recarregado com sucesso.")
    except Exception as e:
        logger.error(f"[SCHEDULER ERROR] {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Carga inicial ao subir
    logger.info("[STARTUP] Pré-carregando dados...")
    try:
        collector.collect_all(use_cache=False)
    except Exception as e:
        logger.warning(f"[STARTUP WARN] Falha na carga inicial: {e}")

    # Agenda recarga a cada 1h
    scheduler.add_job(_scheduled_refresh, "interval", hours=1, id="refresh_job")
    scheduler.start()
    logger.info("[SCHEDULER] Agendado: recarga a cada 1 hora.")

    yield  # app rodando

    scheduler.shutdown(wait=False)
    logger.info("[SHUTDOWN] Scheduler encerrado.")


# ─── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SPA Processos Pendentes",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENV", "production") != "production" else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── CORS ────────────────────────────────────────────────────────────────────
_raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:8000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ─── MODELOS ─────────────────────────────────────────────────────────────────
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    if not DATE_RE.match(v):
        raise ValueError("Data deve estar no formato YYYY-MM-DD")
    return v


class ResumoItem(BaseModel):
    instancia: str
    prefixo: str
    status: str
    total_pendentes: int
    cached: bool = False


class DetalhesResponse(BaseModel):
    instancia: str
    status: str
    total: int
    processos: List[Dict[str, Any]]
    cached: bool = False


class CacheStatus(BaseModel):
    message: str


# ─── HELPERS ────────────────────────────────────────────────────────────────
def _parse_dates(
    dataInicio: Optional[str] = Query(None, description="YYYY-MM-DD"),
    dataFim: Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    try:
        return _validate_date(dataInicio), _validate_date(dataFim)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ─── HEALTHCHECK ────────────────────────────────────────────────────────────
@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok", "scheduler": scheduler.running}


# ─── CONFIGURAÇÃO DE CAMINHOS ───────────────────────────────────────────────
# Define a raiz do projeto de forma absoluta
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Tenta encontrar a pasta 'public' (ajusta conforme sua estrutura)
# Se o main.py estiver dentro de /api ou /src, o ".." sobe um nível.
# Se estiver na raiz, remova o "..".
PUBLIC_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "public"))

# Monta arquivos estáticos (CSS, JS) para que o index.html consiga carregá-los
# O check os.path.exists evita que o app quebre se a pasta sumir no deploy
if os.path.exists(PUBLIC_DIR):
    app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/", response_class=FileResponse, tags=["frontend"])
async def dashboard():
    index_path = os.path.join(PUBLIC_DIR, "index.html")
    
    if not os.path.isfile(index_path):
        logger.error(f"ERRO: index.html não encontrado em: {index_path}")
        # Se falhar, tenta uma busca alternativa na raiz por segurança
        index_path = os.path.join(BASE_DIR, "index.html")
        if not os.path.isfile(index_path):
            raise HTTPException(status_code=404, detail="Arquivo index.html não localizado no servidor.")
            
    return FileResponse(index_path)
# ─── RESUMO ──────────────────────────────────────────────────────────────────
@app.get("/processos/resumo", response_model=List[ResumoItem], tags=["processos"])
@limiter.limit("30/minute")
def get_resumo(
    request: Request,
    dates=Depends(_parse_dates),
):
    data_inicio, data_fim = dates
    try:
        cached_before = collector.get_cached(data_inicio, data_fim)
        results = collector.collect_all(data_inicio, data_fim)
        is_cached = cached_before is not None

        return [
            {
                "instancia": r["name"],
                "prefixo": r["prefix"],
                "status": r["status"],
                "total_pendentes": r["total"],
                "cached": is_cached,
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"[RESUMO ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar resumo")


# ─── DETALHES ────────────────────────────────────────────────────────────────
@app.get("/processos/detalhes/{prefix}", response_model=DetalhesResponse, tags=["processos"])
@limiter.limit("20/minute")
def get_detalhes(
    request: Request,
    prefix: str,
    dates=Depends(_parse_dates),
):
    data_inicio, data_fim = dates

    # Sanitiza prefix
    prefix = re.sub(r"[^A-Z0-9_]", "", prefix.upper())
    if not prefix:
        raise HTTPException(status_code=400, detail="Prefixo inválido")

    try:
        # Tenta servir do cache global primeiro
        cached = collector.get_cached(data_inicio, data_fim)
        if cached:
            match = next((r for r in cached if r["prefix"] == prefix), None)
            if match:
                return {
                    "instancia": match["name"],
                    "status": match["status"],
                    "total": match["total"],
                    "processos": match["records"],
                    "cached": True,
                }

        # Senão, consulta direto
        cfg = collector.get_config(prefix)
        if not cfg["url"]:
            raise HTTPException(status_code=404, detail="Configuração não encontrada")

        result = collector.process(cfg, data_inicio, data_fim)

        records = result["df"].to_dict(orient="records") if result["df"] is not None else []
        return {
            "instancia": result["name"],
            "status": result["status"],
            "total": len(records),
            "processos": records,
            "cached": False,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DETALHES ERROR] {prefix} → {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar detalhes")


# ─── FORÇAR REFRESH DO CACHE ─────────────────────────────────────────────────
@app.get("/cache/refresh", response_model=CacheStatus, tags=["infra"])
@limiter.limit("5/minute")
def force_refresh(
    request: Request,
    x_admin_key: Optional[str] = Query(None, alias="key"),
):
    """
    Força a invalidação e recarga do cache.
    Protegido por ADMIN_KEY definido no .env
    """
    expected = os.getenv("ADMIN_KEY", "")
    if expected and x_admin_key != expected:
        raise HTTPException(status_code=403, detail="Chave inválida")

    collector.invalidate_cache()
    # Dispara em background para não bloquear a resposta
    import threading
    threading.Thread(target=collector.collect_all, kwargs={"use_cache": False}, daemon=True).start()
    return {"message": "Cache invalidado. Recarga em andamento."}


# ─── ENTRYPOINT LOCAL ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENV", "production") != "production",
    )

# ─── VERCEL HANDLER ──────────────────────────────────────────────────────────
# O Mangum adapta o ASGI (FastAPI) para o formato AWS Lambda / Vercel serverless
try:
    from mangum import Mangum
    handler = Mangum(app, lifespan="on")
except ImportError:
    pass  # Não está rodando na Vercel
    
handler = Mangum(app)
