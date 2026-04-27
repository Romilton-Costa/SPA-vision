"""
collector.py — Coleta dados dos Metabases configurados no .env
- Fix: template-tags agora usa o formato correto para queries nativas com [[AND ...]]
- Cache em memória com TTL de 1h por instância
- Thread pool para coleta paralela
"""

import requests
import pandas as pd
import os
import time
import logging
import threading
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ─── HTTP SESSION ────────────────────────────────────────────────────────────
def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

_session = _create_session()

# ─── QUERY NATIVA ───────────────────────────────────────────────────────────
# Os [[AND ...]] são cláusulas opcionais do Metabase:
# só são incluídas na SQL gerada se o parâmetro correspondente for enviado.
NATIVE_QUERY = """
SELECT *
FROM judicial_processes_pending_notice_validations
WHERE registered = false
AND DATE(created_at) >= current_date - interval '7' day
AND DATE(created_at) >= COALESCE(CAST({{dataInicio}} AS DATE), '2026-04-01')
[[AND DATE(created_at) >= CAST({{dataInicio}} AS DATE)]]
[[AND DATE(created_at) <= CAST({{dataFim}} AS DATE)]]
"""

# ─── TOKEN CACHE (por prefixo) ───────────────────────────────────────────────
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_TOKEN_TTL = 300  # 5 min

# ─── DATA CACHE (resultado das queries, 1h) ──────────────────────────────────
_DATA_CACHE: Dict[str, Dict[str, Any]] = {}
_DATA_CACHE_LOCK = threading.Lock()
DATA_CACHE_TTL = int(os.getenv("DATA_CACHE_TTL_SECONDS", 3600))  # padrão: 1h


# ─── CONFIG ──────────────────────────────────────────────────────────────────
def get_config(prefix: str) -> Dict[str, Any]:
    return {
        "prefix": prefix,
        "name": os.getenv(f"{prefix}_NAME", prefix),
        "url": os.getenv(f"{prefix}_URL", "").rstrip("/"),
        "username": os.getenv(f"{prefix}_USERNAME", ""),
        "password": os.getenv(f"{prefix}_PASSWORD", ""),
        "database": int(os.getenv(f"{prefix}_DB", 2)),
    }


def get_all_prefixes() -> List[str]:
    return [p.strip() for p in os.getenv("METABASES", "").split(",") if p.strip()]


# ─── AUTH ────────────────────────────────────────────────────────────────────
def _authenticate(cfg: Dict[str, Any]) -> Optional[str]:
    cache = _TOKEN_CACHE.get(cfg["prefix"])
    if cache and (time.time() - cache["time"] < _TOKEN_TTL):
        return cache["token"]

    try:
        logger.info(f"[AUTH] {cfg['name']}")
        r = _session.post(
            f"{cfg['url']}/api/session",
            json={"username": cfg["username"], "password": cfg["password"]},
            timeout=10,
        )
        r.raise_for_status()
        token = r.json().get("id")
        _TOKEN_CACHE[cfg["prefix"]] = {"token": token, "time": time.time()}
        return token
    except Exception as e:
        logger.error(f"[AUTH ERROR] {cfg['name']} → {e}")
        return None


# ─── QUERY ───────────────────────────────────────────────────────────────────
def _run_query(
    cfg: Dict[str, Any],
    token: str,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    FIX CRÍTICO: o Metabase espera que template-tags seja um dicionário
    com o NOME do parâmetro como chave e um objeto com 'type' e 'value'.
    Para queries nativas com [[AND ...]], o type correto é 'text' e o
    Metabase substitui {{dataInicio}} / {{dataFim}} na query antes de
    executar — então a comparação de data precisa ser feita via CAST na SQL.
    """
    template_tags: Dict[str, Any] = {}

    if data_inicio:
        template_tags["dataInicio"] = {"type": "text", "value": data_inicio}
    if data_fim:
        template_tags["dataFim"] = {"type": "text", "value": data_fim}

    payload = {
        "type": "native",
        "native": {
            "query": NATIVE_QUERY,
            "template-tags": {
                # Declara os parâmetros possíveis para o Metabase reconhecer
                "dataInicio": {
                    "id": "dataInicio",
                    "name": "dataInicio",
                    "display-name": "Data Início",
                    "type": "text",
                    "required": False,
                },
                "dataFim": {
                    "id": "dataFim",
                    "name": "dataFim",
                    "display-name": "Data Fim",
                    "type": "text",
                    "required": False,
                },
            },
        },
        "database": cfg["database"],
        # Parâmetros de execução (valores reais enviados aqui)
        "parameters": [
            {"type": "text", "target": ["variable", ["template-tag", k]], "value": v["value"]}
            for k, v in template_tags.items()
        ],
    }

    logger.info(f"[QUERY] {cfg['name']} | dataInicio={data_inicio} | dataFim={data_fim}")

    for attempt in range(3):
        try:
            resp = _session.post(
                f"{cfg['url']}/api/dataset",
                json=payload,
                headers={"X-Metabase-Session": token},
                timeout=45,
            )

            if resp.status_code not in (200, 202):
                logger.error(f"[QUERY HTTP {resp.status_code}] {cfg['name']}: {resp.text[:400]}")
                return None

            data = resp.json().get("data", {})
            rows = data.get("rows")
            cols = data.get("cols")

            if not rows:
                logger.warning(f"[EMPTY] {cfg['name']} — 0 registros")
                return pd.DataFrame()  # retorna df vazio, não None

            columns = [c["name"] for c in cols]
            logger.info(f"[OK] {cfg['name']} → {len(rows)} registros")
            return pd.DataFrame(rows, columns=columns)

        except Exception as e:
            logger.warning(f"[RETRY {attempt + 1}] {cfg['name']} → {e}")
            time.sleep(2 ** attempt)

    return None


# ─── PROCESS (uma instância) ─────────────────────────────────────────────────
def process(
    cfg: Dict[str, Any],
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> Dict[str, Any]:
    if not all([cfg["url"], cfg["username"], cfg["password"]]):
        return {"prefix": cfg["prefix"], "name": cfg["name"], "status": "no_config", "df": None}

    token = _authenticate(cfg)
    if not token:
        return {"prefix": cfg["prefix"], "name": cfg["name"], "status": "auth_error", "df": None}

    df = _run_query(cfg, token, data_inicio, data_fim)
    return {
        "prefix": cfg["prefix"],
        "name": cfg["name"],
        "status": "ok" if df is not None else "query_error",
        "df": df,
    }


# ─── DATA CACHE KEY ──────────────────────────────────────────────────────────
def _cache_key(data_inicio: Optional[str], data_fim: Optional[str]) -> str:
    return f"{data_inicio or ''}_{data_fim or ''}"


def get_cached(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> Optional[List[Dict]]:
    key = _cache_key(data_inicio, data_fim)
    with _DATA_CACHE_LOCK:
        entry = _DATA_CACHE.get(key)
        if entry and (time.time() - entry["time"] < DATA_CACHE_TTL):
            age = int(time.time() - entry["time"])
            logger.info(f"[CACHE HIT] key={key} age={age}s")
            return entry["data"]
    return None


def set_cached(
    data: List[Dict],
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> None:
    key = _cache_key(data_inicio, data_fim)
    with _DATA_CACHE_LOCK:
        _DATA_CACHE[key] = {"data": data, "time": time.time()}
    logger.info(f"[CACHE SET] key={key} | {len(data)} instâncias")


def invalidate_cache() -> None:
    with _DATA_CACHE_LOCK:
        _DATA_CACHE.clear()
    logger.info("[CACHE] Cache invalidado")


# ─── COLLECT ALL ─────────────────────────────────────────────────────────────
def collect_all(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    max_workers: int = 6,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Coleta dados de todas as instâncias configuradas.
    - Usa cache em memória (TTL configurável via DATA_CACHE_TTL_SECONDS).
    - use_cache=False força recarga imediata (usado pelo scheduler).
    """
    if use_cache:
        cached = get_cached(data_inicio, data_fim)
        if cached is not None:
            return cached

    prefixes = get_all_prefixes()
    configs = [get_config(p) for p in prefixes]

    results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process, c, data_inicio, data_fim): c for c in configs}
        for future in as_completed(futures):
            result = future.result()
            logger.info(f"[RESULT] {result['name']} → {result['status']}")
            results.append(result)

    # Serializa para cache (sem DataFrames)
    serializable = [
        {
            "prefix": r["prefix"],
            "name": r["name"],
            "status": r["status"],
            "total": len(r["df"]) if r["df"] is not None else 0,
            "records": r["df"].to_dict(orient="records") if r["df"] is not None else [],
        }
        for r in results
    ]

    set_cached(serializable, data_inicio, data_fim)
    return serializable