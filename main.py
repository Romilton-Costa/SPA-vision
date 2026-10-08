from mangum import Mangum
from crawler import SPACrawler
import os
import time
import logging
import threading
import re
import rotina_service
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
from errbit_service import router as errbit_router
import collector
from fastapi import FastAPI
from collector import get_zabbix_cpu_usage
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import json
import asyncio
import hashlib

_TRIBUNAIS_CACHE = {"data": [], "time": 0}
_TRIBUNAIS_CACHE_LOCK = threading.Lock()
TRIBUNAIS_CACHE_TTL = int(os.getenv("TRIBUNAIS_CACHE_TTL_SECONDS", 300))


def _load_tribunais_config() -> list:
    """Lê TRIBUNAIS_CONFIG (JSON) do .env ou, se vazio, de tribunais.json."""
    raw = (os.getenv("TRIBUNAIS_CONFIG") or "").strip()
    path = os.getenv("TRIBUNAIS_CONFIG_FILE", "tribunais.json")
    if not raw and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    if not raw:
        logger.warning("[TRIBUNAIS] TRIBUNAIS_CONFIG vazio ou ausente no .env.")
        return []
    try:
        cfg = json.loads(raw)
    except Exception as e:
        logger.error(
            f"[TRIBUNAIS] JSON invalido em TRIBUNAIS_CONFIG ({e}). Valor lido: {raw[:40]!r}. "
            "Se o JSON ocupa varias linhas, envolva-o em aspas simples no .env: TRIBUNAIS_CONFIG='[ ... ]'"
        )
        return []
    if isinstance(cfg, dict):
        cfg = [cfg]
    if not isinstance(cfg, list):
        logger.error("[TRIBUNAIS] TRIBUNAIS_CONFIG deve ser uma lista JSON.")
        return []
    return [t for t in cfg if isinstance(t, dict)]

def _fetch_tribunais_status_fresh() -> list:
    tribunais = _load_tribunais_config()
    resultados = []
    crawlers_autenticados = {}

    for trib in tribunais:
        link_teste = ""
        try:
            cliente    = trib.get("cliente", "Geral")
            nome       = trib.get("nome", "Tribunal")
            target_url = str(trib.get("url", "")).rstrip("/")
            proc       = trib.get("proc", "")
            soap_id    = trib.get("id", "")
            link_teste = f"{target_url}/judicial_processes/{proc}/movements?soap_setting_id={soap_id}"

            if target_url not in crawlers_autenticados:
                crawler = SPACrawler(base_url=target_url)
                crawler.authenticate(use_healthcheck_creds=True)
                crawlers_autenticados[target_url] = crawler
            else:
                crawler = crawlers_autenticados[target_url]

            status_code, _ = crawler.test_movements(soap_id=soap_id, proc=proc)
            resultados.append({
                "cliente": cliente, "nome": nome,
                "status": "OK" if status_code == 200 else "ERRO",
                "code": status_code, "url": link_teste,
            })
        except Exception as e:
            logger.warning(f"[TRIBUNAIS] Falha ao testar {trib!r}: {e}")
            resultados.append({
                "cliente": trib.get("cliente", "Geral") if isinstance(trib, dict) else "Geral",
                "nome":    trib.get("nome", "Tribunal") if isinstance(trib, dict) else str(trib),
                "status": "ERRO", "code": 0,
                "error": str(e), "url": link_teste,
            })
    return resultados

def _warm_tribunais_cache() -> None:
    logger.info("[TRIBUNAIS] Verificando status...")
    try:
        result = _fetch_tribunais_status_fresh()
        with _TRIBUNAIS_CACHE_LOCK:
            _TRIBUNAIS_CACHE["data"] = result
            _TRIBUNAIS_CACHE["time"] = time.time()
        logger.info(f"[TRIBUNAIS] Cache atualizado: {len(result)} tribunal(is).")
    except Exception as e:
        logger.error(f"[TRIBUNAIS WARM] {e}")



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
    # ── Tribunais (background, cache 5min) ──
    logger.info("[STARTUP] Disparando aquecimento de status dos tribunais...")
    threading.Thread(target=_warm_tribunais_cache, daemon=True).start()
    scheduler.add_job(
        _warm_tribunais_cache, "interval", minutes=5, id="tribunais_refresh_job"
    )

    # ── Expedientes (bloqueante na 1ª carga) ──
    logger.info("[STARTUP] Pré-carregando dados de expedientes...")
    try:
        collector.collect_all(use_cache=False)
    except Exception as e:
        logger.warning(f"[STARTUP WARN] Falha na carga inicial de expedientes: {e}")

    # ── SSL (background, cache 1h) ──
    logger.info("[STARTUP] Disparando aquecimento de certificados SSL...")
    threading.Thread(target=collector._warm_ssl_cache, daemon=True).start()
    scheduler.add_job(collector._warm_ssl_cache, "interval", hours=1, id="ssl_refresh_job")

    # ── Rotinas (background, cache 5min) ──
    logger.info("[STARTUP] Disparando aquecimento de rotinas...")
    threading.Thread(target=rotina_service.collect_rotinas, daemon=True).start()
    scheduler.add_job(
        rotina_service.collect_rotinas,
        "interval", minutes=5, id="rotina_refresh_job",
    )

    # ── Expedientes — refresh periódico ──
    scheduler.add_job(_scheduled_refresh, "interval", minutes=15, id="refresh_job")

    scheduler.start()
    logger.info(
        "[SCHEDULER] Agendado: expedientes/15min | SSL/1h | rotinas/5min | tribunais/5min"
    )

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
app.include_router(errbit_router)
# No main.py
@app.get("/api/stream", tags=["infra"])
async def stream_events(request: Request):
    loop = asyncio.get_running_loop()

    async def event_gen():
        last_hash = None
        while True:
            if await request.is_disconnected():
                break
            try:
                rotina = await loop.run_in_executor(None, rotina_service.collect_rotinas)
                with _TRIBUNAIS_CACHE_LOCK:
                    trib = list(_TRIBUNAIS_CACHE.get("data") or [])
                payload = {
                    "ts": int(time.time() * 1000),
                    "tribunais": trib,
                    "rotina": [
                        {"prefix": r["prefix"], "name": r["name"],
                         "status": r["status"], "total": r["total"]}
                        for r in rotina
                    ],
                }
                blob = json.dumps(payload, sort_keys=True)
                h = hashlib.md5(blob.encode()).hexdigest()
                if h != last_hash:
                    last_hash = h
                    yield f"data: {blob}\n\n"
                else:
                    yield ": keepalive\n\n"
            except Exception as e:
                logger.warning(f"[SSE] {e}")
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(5)
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
@app.get("/api/zabbix/history/{host_name}", tags=["infra"])
def api_zabbix_history(host_name: str, hours: int = 24):
    try:
        cpu = collector.get_zabbix_history(host_name, "system.cpu.util", hours)
        mem = collector.get_zabbix_history(host_name, "vm.memory.util", hours)
        return {"host": host_name, "hours": hours, "cpu": cpu, "memory": mem}
    except Exception as e:
        logger.error(f"[ZABBIX HISTORY] {host_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/status/tribunais")
def get_tribunais_status():
    """Devolve o último resultado do cache (atualizado a cada 5 min pelo scheduler).
    Não faz login/crawl na requisição: evita 500 e timeout com dezenas de clientes."""
    with _TRIBUNAIS_CACHE_LOCK:
        return list(_TRIBUNAIS_CACHE.get("data") or [])

def _validate_date(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    if not DATE_RE.match(v):
        raise ValueError("Data deve estar no formato YYYY-MM-DD")
    return v
@app.get("/api/zabbix/cpu", tags=["infra"])
def api_zabbix_cpu():
    """
    Retorna a taxa de uso de CPU (%) de cada cliente monitorado no Zabbix.
    """
    dados = collector.get_zabbix_cpu_usage()
    lista = [{"name": nome, "cpu_usage": valor} for nome, valor in dados.items()]
    return {
        "total": len(lista),
        "data": lista
    }

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
PUBLIC_DIR = os.path.dirname(os.path.abspath(__file__))

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
@app.get("/checklist", response_class=FileResponse, tags=["frontend"])
async def checklist():
    """Segunda página HTML que consome outra fonte de dados."""
    path = os.path.join(PUBLIC_DIR, "checklist.html")
    if not os.path.isfile(path):
        logger.error(f"ERRO: dashboard2.html não encontrado em: {path}")
        raise HTTPException(status_code=404, detail="checklist.html não encontrado")
    return FileResponse(path)

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

        # Busca dados de uso de CPU
        try:
            # get_zabbix_cpu_usage() já retorna um dict {nome_instancia: cpu_usage}
            cpu_map = collector.get_zabbix_cpu_usage()
        except Exception as cpu_err:
            logger.warning(f"[ZABBIX CPU WARN] Falha ao coletar dados de CPU: {cpu_err}")
            cpu_map = {}

        return [
            {
                "instancia": r["name"],
                "prefixo": r["prefix"],
                "status": r["status"],
                "total_pendentes": r["total"],
                "cached": is_cached,
                "cpu_usage": cpu_map.get(r["name"]) or cpu_map.get(r["prefix"])
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"[RESUMO ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar resumo")

@app.get("/api/status/certificados")
def get_ssl_status():
    try:
        # threshold_days=10: mostra certificados que expiram nos próximos 10 dias
        alertas = collector.collect_ssl_alerts(threshold_days=10)
        return alertas
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
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


# ─── CADASTROS EM BRANCO (MB5) ───────────────────────────────────────────────
@app.get("/processos/blank-notices", tags=["processos"])
@limiter.limit("30/minute")
def get_blank_notices(
    request: Request,
    dates=Depends(_parse_dates),
):
    """Retorna resumo dos cadastros em branco detectados no MB5 (PGE Mato Grosso).
    Sempre retorna 200 com campo 'status' — erros são representados no payload.
    """
    data_inicio, data_fim = dates
    try:
        result = collector.collect_blank_notices(data_inicio, data_fim)
        return {
            "instancia": result["name"],
            "prefixo": result["prefix"],
            "status": result["status"],
            "total": result["total"],
            "error_message": result.get("error_message"),
        }
    except Exception as e:
        logger.error(f"[BLANK NOTICES ERROR] {e}")
        return {
            "instancia": "PGE Mato Grosso",
            "prefixo": "MB5",
            "status": "error",
            "total": 0,
            "error_message": str(e),
        }



# ─── CADASTROS EM BRANCO — TODOS OS CLIENTES (exceto MB5) ───────────────────
@app.get("/processos/blank-notices/todos", tags=["processos"])
@limiter.limit("30/minute")
def get_blank_notices_todos(
    request: Request,
    dates=Depends(_parse_dates),
):
    """Retorna o resumo de cadastros em branco para TODOS os clientes configurados,
    exceto o MB5 (que possui endpoint próprio em /processos/blank-notices).
    Sempre retorna 200 — erros por cliente são representados no item da lista.
    """
    data_inicio, data_fim = dates
    try:
        results = collector.collect_blank_notices_todos_clientes(data_inicio, data_fim)
        return [
            {
                "instancia": r["name"],
                "prefixo": r["prefix"],
                "status": r["status"],
                "total": r["total"],
                "error_message": r.get("error_message"),
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"[BLANK ALL ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar resumo de cadastros em branco")


@app.get("/processos/blank-notices/todos/{prefix}", tags=["processos"])
@limiter.limit("20/minute")
def get_blank_notices_todos_detalhes(
    request: Request,
    prefix: str,
    dates=Depends(_parse_dates),
):
    """Retorna a lista completa de cadastros em branco de UM cliente específico
    (exceto MB5), usada pelo modal de detalhes do frontend."""
    data_inicio, data_fim = dates

    # Sanitiza prefix (mesmo padrão usado em /processos/detalhes/{prefix})
    prefix = re.sub(r"[^A-Z0-9_]", "", prefix.upper())
    if not prefix or prefix == "MB5":
        raise HTTPException(status_code=400, detail="Prefixo inválido")

    try:
        result = collector.get_blank_notices_client(prefix, data_inicio, data_fim)
        return {
            "instancia": result["name"],
            "prefixo": result["prefix"],
            "status": result["status"],
            "total": result["total"],
            "processos": result["records"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BLANK ALL DETALHES ERROR] {prefix} → {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar detalhes dos cadastros em branco")
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
    threading.Thread(target=collector.collect_all, kwargs={"use_cache": False}, daemon=True).start()
    return {"message": "Cache invalidado. Recarga em andamento."}
@app.get("/api/rotina/status", tags=["rotina"])
@limiter.limit("60/minute")
def get_rotina_status(request: Request):
    """Resumo da rotina por cliente (card principal do dashboard)."""
    try:
        dados = rotina_service.collect_rotinas()
        return [
            {
                "prefix": r["prefix"],
                "name": r["name"],
                "status": r["status"],
                "total": r["total"],
                "error_message": r.get("error_message"),
            }
            for r in dados
        ]
    except Exception as e:
        logger.error(f"[ROTINA STATUS ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao coletar rotinas")


@app.get("/api/rotina/alertas", tags=["rotina"])
@limiter.limit("60/minute")
def get_rotina_alertas(request: Request):
    """Retorna apenas clientes com problema — usado pelo popup + som no frontend."""
    try:
        return rotina_service.get_rotina_alertas()
    except Exception as e:
        logger.error(f"[ROTINA ALERTAS ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao coletar alertas de rotina")


@app.get("/api/rotina/detalhes/{prefix}", tags=["rotina"])
@limiter.limit("30/minute")
def get_rotina_detalhes(request: Request, prefix: str):
    """Detalhes da rotina de UM cliente (usado no modal)."""
    prefix = re.sub(r"[^A-Z0-9_]", "", prefix.upper())
    if not prefix:
        raise HTTPException(status_code=400, detail="Prefixo inválido")

    try:
        result = rotina_service.get_rotina_client(prefix)
        return result
    except Exception as e:
        logger.error(f"[ROTINA DETALHES ERROR] {prefix} → {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar detalhes da rotina")


@app.get("/api/rotina/refresh", tags=["rotina"])
@limiter.limit("10/minute")
def refresh_rotina(request: Request):
    """Invalida o cache da rotina e força nova coleta em background."""
    rotina_service.invalidate_rotina_cache()
    threading.Thread(
        target=rotina_service.collect_rotinas,
        kwargs={"use_cache": False},
        daemon=True,
    ).start()
    return {"message": "Cache de rotina invalidado. Recarga em andamento."}

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