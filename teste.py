"""
collector.py — Coleta dados dos Metabases configurados no .env
- Fix: template-tags agora usa o formato correto para queries nativas com [[AND ...]]
- Cache em memória com TTL de 1h por instância
- Thread pool para coleta paralela
- Cache SSL independente (TTL 1h, verificação assíncrona)
- TTL separado de 20min para MB5 (Mato Grosso)
"""
from searchcertify import verificar_certificado, sites
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
AND DATE(created_at) >= COALESCE(CAST({{dataInicio}} AS DATE), '2026-04-01')
[[AND DATE(created_at) >= CAST({{dataInicio}} AS DATE)]]
[[AND DATE(created_at) <= CAST({{dataFim}} AS DATE)]]
"""


QUERY_MT="""
SELECT *
FROM judicial_processes_pending_notice_validations
WHERE registered = false
AND DATE(created_at) >= COALESCE(CAST({{dataInicio}} AS DATE), '2026-04-01')
[[AND DATE(created_at) >= CAST({{dataInicio}} AS DATE)]]
[[AND DATE(created_at) <= CAST({{dataFim}} AS DATE)]]
"""


# ─── QUERY CADASTROS EM BRANCO (MB5 apenas) ─────────────────────────────────
# Identifica expedientes cadastrados em branco: validações sem uma notificação
# cadastrada correspondente (LEFT JOIN retorna NULL no lado direito).
QUERY_MB5_BLANK = """
SELECT DISTINCT "judicial_processes_pending_notice_validations".*
FROM "judicial_processes_pending_notice_validations"
LEFT OUTER JOIN "judicial_processes_pending_notices"
  ON "judicial_processes_pending_notices"."id" = "judicial_processes_pending_notice_validations"."judicial_processes_pending_notice_id"
WHERE "judicial_processes_pending_notices"."id" IS NULL
AND DATE("judicial_processes_pending_notice_validations"."created_at") >= COALESCE(CAST({{dataInicio}} AS DATE), '2026-04-01')
[[AND DATE("judicial_processes_pending_notice_validations"."created_at") >= CAST({{dataInicio}} AS DATE)]]
[[AND DATE("judicial_processes_pending_notice_validations"."created_at") <= CAST({{dataFim}} AS DATE)]]
"""


# ─── TOKEN CACHE (por prefixo) ───────────────────────────────────────────────
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_TOKEN_TTL = 300  # 5 min

# ─── DATA CACHE (resultado das queries) ──────────────────────────────────────
_DATA_CACHE: Dict[str, Dict[str, Any]] = {}
_DATA_CACHE_LOCK = threading.Lock()
DATA_CACHE_TTL = int(os.getenv("DATA_CACHE_TTL_SECONDS", 600))      # padrão: 10min
DATA_CACHE_TTL_MB5 = int(os.getenv("MB5_CACHE_TTL_SECONDS", 1200))  # MB5: 20min fixo

# ─── SSL CACHE (verificações de certificado, TTL 1h) ─────────────────────────
_SSL_CACHE: Dict[str, Any] = {"data": None, "time": 0}
_SSL_CACHE_LOCK = threading.Lock()
SSL_CACHE_TTL = int(os.getenv("SSL_CACHE_TTL_SECONDS", 3600))  # 1h


# ─── CONFIG ──────────────────────────────────────────────────────────────────
def get_config(prefix: str) -> Dict[str, Any]:
    # MB5 sempre usa database 2
    if prefix == "MB5":
        db_id = 2
    else:
        raw_db = os.getenv(f"{prefix}_DB")

        try:
            db_id = int(raw_db) if raw_db else None
        except (ValueError, TypeError):
            db_id = None

    return {
        "prefix": prefix,
        "name": os.getenv(f"{prefix}_NAME", prefix),
        "url": os.getenv(f"{prefix}_URL", "").rstrip("/"),
        "username": os.getenv(f"{prefix}_USERNAME", ""),
        "password": os.getenv(f"{prefix}_PASSWORD", ""),
        "database": db_id,
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

    query_to_use = QUERY_MT if cfg["prefix"] == "MB5" else NATIVE_QUERY

    template_tags: Dict[str, Any] = {}

    if data_inicio:
        template_tags["dataInicio"] = {"type": "text", "value": data_inicio}
    if data_fim:
        template_tags["dataFim"] = {"type": "text", "value": data_fim}

    payload = {
    "type": "native",
    "native": {
        "query": query_to_use,
        "template-tags": {
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
    "parameters": [
        {
            "type": "text",
            "target": ["variable", ["template-tag", k]],
            "value": v["value"]
        }
        for k, v in template_tags.items()
    ],
}

# Só adiciona database se existir
    if cfg["database"] is not None:
        payload["database"] = cfg["database"]

    logger.info(f"[QUERY] {cfg['name']} | db={cfg['database']} | dataInicio={data_inicio} | dataFim={data_fim}")

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



def _cache_key(data_inicio: Optional[str] = None, data_fim: Optional[str] = None) -> str:
    """Gera uma chave única baseada nas datas para evitar resultados repetidos."""
    # Se a data for None ou vazia, usamos 'default' para a chave
    s = data_inicio if data_inicio else "default"
    e = data_fim if data_fim else "default"
    return f"data_resumo_{s}_{e}"

def get_cached(data_inicio: Optional[str] = None, data_fim: Optional[str] = None) -> Optional[List[Dict]]:
    # CORREÇÃO: Agora passamos as datas para a função da chave
    key = _cache_key(data_inicio, data_fim)
    with _DATA_CACHE_LOCK:
        entry = _DATA_CACHE.get(key)
        if entry and (time.time() - entry["time"] < DATA_CACHE_TTL):
            age = int(time.time() - entry["time"])
            logger.info(f"[CACHE HIT] key={key} age={age}s")
            return entry["data"]
    return None


def get_cached_mb5(data_inicio: Optional[str] = None, data_fim: Optional[str] = None) -> Optional[Dict]:
    """Cache exclusivo para MB5 com TTL de 20 minutos."""
    key = f"mb5_blank_{data_inicio or 'default'}_{data_fim or 'default'}"
    with _DATA_CACHE_LOCK:
        entry = _DATA_CACHE.get(key)
        if entry and (time.time() - entry["time"] < DATA_CACHE_TTL_MB5):
            age = int(time.time() - entry["time"])
            logger.info(f"[MB5 CACHE HIT] key={key} age={age}s")
            return entry["data"]
    return None


def set_cached_mb5(
    data: Dict,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> None:
    key = f"mb5_blank_{data_inicio or 'default'}_{data_fim or 'default'}"
    with _DATA_CACHE_LOCK:
        _DATA_CACHE[key] = {"data": data, "time": time.time()}
    logger.info(f"[MB5 CACHE SET] key={key}")

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


# ─── COLLECT BLANK NOTICES (MB5) ────────────────────────────────────────────
def collect_blank_notices(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> Dict[str, Any]:
    """Executa a query de cadastros em branco exclusivamente para MB5.
    Cache TTL: 20 minutos (independente do cache principal).
    Sempre retorna um dict com 'status' mesmo em caso de erro.
    """
    # ── Tenta cache MB5 primeiro ──────────────────────────────────────────────
    cached = get_cached_mb5(data_inicio, data_fim)
    if cached is not None:
        logger.info("[MB5 BLANK] Servido do cache (20min TTL)")
        return cached

    cfg = get_config("MB5")
    if not all([cfg["url"], cfg["username"], cfg["password"]]):
        result = {"prefix": "MB5", "name": cfg["name"], "status": "no_config",
                  "total": 0, "records": [], "error_message": "Configuração ausente (.env)"}
        set_cached_mb5(result, data_inicio, data_fim)
        return result

    token = _authenticate(cfg)
    if not token:
        result = {"prefix": "MB5", "name": cfg["name"], "status": "auth_error",
                  "total": 0, "records": [], "error_message": "Falha na autenticação com Metabase"}
        # Não armazena em cache erros de auth (pode ser transitório)
        return result

    template_tags: Dict[str, Any] = {}
    if data_inicio:
        template_tags["dataInicio"] = {"type": "text", "value": data_inicio}
    if data_fim:
        template_tags["dataFim"] = {"type": "text", "value": data_fim}

    payload = {
        "type": "native",
        "native": {
            "query": QUERY_MB5_BLANK,
            "template-tags": {
                "dataInicio": {
                    "id": "dataInicio", "name": "dataInicio",
                    "display-name": "Data Início", "type": "text", "required": False,
                },
                "dataFim": {
                    "id": "dataFim", "name": "dataFim",
                    "display-name": "Data Fim", "type": "text", "required": False,
                },
            },
        },
        "database": cfg["database"],
        "parameters": [
            {"type": "text", "target": ["variable", ["template-tag", k]], "value": v["value"]}
            for k, v in template_tags.items()
        ],
    }

    logger.info(f"[BLANK NOTICES] MB5 | db={cfg['database']} | dataInicio={data_inicio} | dataFim={data_fim}")
    for attempt in range(3):
        try:
            resp = _session.post(
                f"{cfg['url']}/api/dataset",
                json=payload,
                headers={"X-Metabase-Session": token},
                timeout=45,
            )
            if resp.status_code not in (200, 202):
                logger.error(f"[BLANK NOTICES HTTP {resp.status_code}] {resp.text[:400]}")
                result = {"prefix": "MB5", "name": cfg["name"], "status": "query_error",
                          "total": 0, "records": [],
                          "error_message": f"Metabase retornou HTTP {resp.status_code}"}
                set_cached_mb5(result, data_inicio, data_fim)
                return result

            data = resp.json().get("data", {})
            rows = data.get("rows", [])
            cols = data.get("cols", [])

            if not rows:
                logger.info("[BLANK NOTICES] MB5 — 0 registros em branco")
                result = {"prefix": "MB5", "name": cfg["name"], "status": "ok",
                          "total": 0, "records": []}
                set_cached_mb5(result, data_inicio, data_fim)
                return result

            columns = [c["name"] for c in cols]
            df = pd.DataFrame(rows, columns=columns)
            records = df.to_dict(orient="records")
            logger.info(f"[BLANK NOTICES] MB5 → {len(records)} registros")
            result = {"prefix": "MB5", "name": cfg["name"], "status": "ok",
                      "total": len(records), "records": records}
            set_cached_mb5(result, data_inicio, data_fim)
            return result

        except Exception as e:
            logger.warning(f"[BLANK NOTICES RETRY {attempt + 1}] MB5 → {e}")
            time.sleep(2 ** attempt)

    result = {"prefix": "MB5", "name": cfg["name"], "status": "query_error",
              "total": 0, "records": [],
              "error_message": "Timeout ou falha após 3 tentativas"}
    # Armazena erro por 2min para não sobrecarregar o Metabase
    with _DATA_CACHE_LOCK:
        key = f"mb5_blank_{data_inicio or 'default'}_{data_fim or 'default'}"
        _DATA_CACHE[key] = {"data": result, "time": time.time() - DATA_CACHE_TTL_MB5 + 120}
    return result


# ─── CACHE POR INSTÂNCIA (TTL individual) ────────────────────────────────────
def _instance_cache_key(prefix: str, data_inicio: Optional[str], data_fim: Optional[str]) -> str:
    s = data_inicio or "default"
    e = data_fim or "default"
    return f"instance_{prefix}_{s}_{e}"


def _get_cached_instance(prefix: str, data_inicio: Optional[str], data_fim: Optional[str]) -> Optional[Dict]:
    ttl = DATA_CACHE_TTL_MB5 if prefix == "MB5" else DATA_CACHE_TTL
    key = _instance_cache_key(prefix, data_inicio, data_fim)
    with _DATA_CACHE_LOCK:
        entry = _DATA_CACHE.get(key)
        if entry and (time.time() - entry["time"] < ttl):
            return entry["data"]
    return None


def _set_cached_instance(prefix: str, data: Dict, data_inicio: Optional[str], data_fim: Optional[str]) -> None:
    key = _instance_cache_key(prefix, data_inicio, data_fim)
    with _DATA_CACHE_LOCK:
        _DATA_CACHE[key] = {"data": data, "time": time.time()}
    logger.info(f"[CACHE SET] instance={prefix} key={key}")


# ─── COLLECT ALL ─────────────────────────────────────────────────────────────
def collect_all(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    max_workers: int = 12,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Coleta todos os prefixes em paralelo.
    - MB5: cache TTL de 20 min (independente do ciclo principal)
    - Demais: cache TTL padrão (DATA_CACHE_TTL)
    O cache global de resumo (get_cached) é mantido para respostas rápidas
    quando nenhuma instância está expirada.
    """
    prefixes = get_all_prefixes()

    if use_cache:
        # Verifica se TODAS as instâncias ainda estão válidas no cache individual
        all_cached = []
        miss = False
        for p in prefixes:
            entry = _get_cached_instance(p, data_inicio, data_fim)
            if entry is None:
                miss = True
                break
            all_cached.append(entry)
        if not miss:
            logger.info("[CACHE] Todos as instâncias servidas do cache individual.")
            return all_cached

    configs = [get_config(p) for p in prefixes]

    # Separa MB5 das demais para controle de cache individual
    results: List[Dict[str, Any]] = []

    def _process_with_cache(cfg: Dict) -> Dict:
        # Se use_cache, tenta cache individual primeiro
        if use_cache:
            cached = _get_cached_instance(cfg["prefix"], data_inicio, data_fim)
            if cached is not None:
                logger.info(f"[CACHE HIT instance] {cfg['prefix']}")
                return cached
        result = process(cfg, data_inicio, data_fim)
        serialized = {
            "prefix": result["prefix"],
            "name": result["name"],
            "status": result["status"],
            "total": len(result["df"]) if result["df"] is not None else 0,
            "records": result["df"].to_dict(orient="records") if result["df"] is not None else [],
        }
        _set_cached_instance(cfg["prefix"], serialized, data_inicio, data_fim)
        return serialized

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_with_cache, c): c for c in configs}
        for future in as_completed(futures):
            r = future.result()
            logger.info(f"[RESULT] {r['name']} → {r['status']}")
            results.append(r)

    # Mantém o cache global de resumo (para get_cached usado em /processos/resumo)
    set_cached(results, data_inicio, data_fim)
    return results

def _warm_ssl_cache() -> None:
    """Aquece o cache SSL em background — chamado no startup sem bloquear."""
    logger.info("[SSL] Iniciando verificação de certificados em background...")
    try:
        result = _fetch_ssl_alerts_fresh(threshold_days=10)
        with _SSL_CACHE_LOCK:
            _SSL_CACHE["data"] = result
            _SSL_CACHE["time"] = time.time()
        logger.info(f"[SSL] Cache aquecido: {len(result)} alerta(s) encontrado(s).")
    except Exception as e:
        logger.error(f"[SSL WARM] Erro ao aquecer cache: {e}")


def _fetch_ssl_alerts_fresh(threshold_days: int = 10) -> List[Dict[str, Any]]:
    """Verifica todos os sites em paralelo (ThreadPool) e retorna alertas."""
    alertas: List[Dict[str, Any]] = []

    def _check(url: str) -> Optional[Dict[str, Any]]:
        try:
            info = verificar_certificado(url)
            # Sempre retorna, o filtro é feito depois
            return {
                "host": info["host"],
                "dias_restantes": info["dias_restantes"],
                "validade": info["validade"].strftime("%d/%m/%Y"),
            }
        except Exception as e:
            logger.warning(f"[SSL CHECK] Falha em {url}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_check, url): url for url in sites}
        for future in as_completed(futures):
            res = future.result()
            if res and res["dias_restantes"] <= threshold_days:
                alertas.append(res)

    # Ordena pelos que estão mais perto de expirar
    alertas.sort(key=lambda x: x["dias_restantes"])
    return alertas


def collect_ssl_alerts(threshold_days: int = 10) -> List[Dict[str, Any]]:
    """Retorna alertas de certificados usando cache em memória (TTL 1h).
    A verificação real nunca bloqueia a requisição — serve do cache e,
    se expirado, dispara refresh em background e retorna o dado anterior.
    """
    with _SSL_CACHE_LOCK:
        cached_data = _SSL_CACHE.get("data")
        cached_time = _SSL_CACHE.get("time", 0)
        age = time.time() - cached_time

    if cached_data is not None and age < SSL_CACHE_TTL:
        logger.info(f"[SSL] Cache hit (age={int(age)}s)")
        return cached_data

    if cached_data is not None:
        # Cache expirou → refresh em background, retorna dado anterior imediatamente
        logger.info("[SSL] Cache expirado — disparando refresh em background")
        threading.Thread(target=_warm_ssl_cache, daemon=True).start()
        return cached_data

    # Primeira chamada: sem cache ainda — bloqueia mas com timeout curto por site
    logger.info("[SSL] Primeira carga — buscando certificados...")
    try:
        result = _fetch_ssl_alerts_fresh(threshold_days=threshold_days)
        with _SSL_CACHE_LOCK:
            _SSL_CACHE["data"] = result
            _SSL_CACHE["time"] = time.time()
        return result
    except Exception as e:
        logger.error(f"[SSL] Erro na carga inicial: {e}")
        return []