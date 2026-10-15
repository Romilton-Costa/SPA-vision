"""
rotina_collector.py — Monitoramento da rotina de expedientes (last_start/last_finished)
por cliente.

Reaproveita:
  - DNS override e sessão HTTP do collector.py (não duplica config)
  - Cache em memória com TTL próprio (5 min — rotina é sensível a tempo)

Regras (mesma lógica da query SQL):
  - 'NAO RODOU'  → last_start de um dia anterior ao atual
  - 'OK'         → last_start do dia atual
  - 'VERIFICAR'  → qualquer outro caso (last_start nulo, futuro, etc.)
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from collector import (
    _session,
    _authenticate,
    get_config,
    get_all_prefixes,
)

logger = logging.getLogger(__name__)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
ROTINA_CACHE_TTL = int(os.getenv("ROTINA_CACHE_TTL_SECONDS", 300))  # 5 min
ROTINA_ALERT_THRESHOLD = int(os.getenv("ROTINA_ALERT_THRESHOLD", 1))  # alerta se ≥1 cliente falhar
_OFFSET_HOURS = 3  # BRT = UTC-3

# Rotinas que não entram no status do cliente.
# Chave: trecho do nome do cliente (sem acento/espaço); valor: trechos da descrição da rotina.
ROTINAS_IGNORADAS = {
    "jaboatao": ["djediariodejusticaeletronico", "diariodejusticaeletronico"],
}


def _norm(s: Any) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _rotina_ignorada(cliente: str, descricao: str) -> bool:
    n, d = _norm(cliente), _norm(descricao)
    return any(k in n and any(t in d for t in trechos) for k, trechos in ROTINAS_IGNORADAS.items())


# ─── CACHE ──────────────────────────────────────────────────────────────────
_ROTINA_CACHE: Dict[str, Any] = {"data": None, "time": 0}
_ROTINA_CACHE_LOCK = threading.Lock()


# ─── QUERY ──────────────────────────────────────────────────────────────────
# Filtro por prefixo é feito no backend (a query é a mesma para todos os clientes).
# A coluna `description` é o "nome" do job cadastrado no Metabase.
ROTINA_QUERY = """
SELECT
  "public"."judicial_processes_pending_notice_settings"."description" AS "description",
  ("public"."judicial_processes_pending_notice_settings"."last_start") AS "last_start",
  ("public"."judicial_processes_pending_notice_settings"."last_finished") AS "last_finished",
  "public"."judicial_processes_pending_notice_settings"."last_processing" AS "last_processing",
  CASE
    -- 1) DESATIVADO: last_finished é de um dia anterior ao atual
    --    Interpretação: a rotina foi encerrada/desligada para este tribunal
    WHEN ("public"."judicial_processes_pending_notice_settings"."last_finished")::date < CURRENT_DATE
      THEN 'DESATIVADO'

    -- 2) NAO RODOU: last_start é de um dia anterior ao atual (e não foi finalizado)
    WHEN ("public"."judicial_processes_pending_notice_settings"."last_start")::date < CURRENT_DATE
      THEN 'NAO RODOU'

    -- 3) Rodou hoje e já passou mais de 1 hora desde o last_start
    WHEN ("public"."judicial_processes_pending_notice_settings"."last_start")::date = CURRENT_DATE
      AND NOW() - ("public"."judicial_processes_pending_notice_settings"."last_start") > INTERVAL '1 hour'
      THEN 'OK'

    -- 4) Rodou hoje, dentro da janela de 1 hora
    WHEN ("public"."judicial_processes_pending_notice_settings"."last_start")::date = CURRENT_DATE
      AND NOW() - ("public"."judicial_processes_pending_notice_settings"."last_start") <= INTERVAL '1 hour'
      THEN 'OK'

    ELSE 'VERIFICAR'
  END AS "status_rotina"
FROM
  "public"."judicial_processes_pending_notice_settings"
WHERE
  "public"."judicial_processes_pending_notice_settings"."begin_date" < CURRENT_DATE
  AND (
    "public"."judicial_processes_pending_notice_settings"."expiration_date" > CURRENT_DATE
    OR "public"."judicial_processes_pending_notice_settings"."expiration_date" IS NULL
  )
ORDER BY
  "public"."judicial_processes_pending_notice_settings"."id" DESC
LIMIT
  50; """

# ─── HELPERS ────────────────────────────────────────────────────────────────
def _row_to_dict(row: pd.Series) -> Dict[str, Any]:
    """Converte uma linha do DataFrame em dict serializável (datas → ISO string)."""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (datetime, pd.Timestamp)):
            out[k] = v.isoformat() if pd.notna(v) else None
        elif pd.isna(v):
            out[k] = None
        else:
            out[k] = v
    return out


def _compute_status(records: List[Dict[str, Any]]) -> str:
    """Consolida o status do cliente.

    Prioridade (do mais grave para o menos):
      NAO RODOU > VERIFICAR > OK
      DESATIVADO não gera alerta.
    """
    if not records:
        return "SEM_ROTINA"

    statuses = [r.get("status_rotina") for r in records]

    if "NAO RODOU" in statuses:
        return "NAO RODOU"
    if "VERIFICAR" in statuses:
        return "VERIFICAR"

    if "OK" in statuses:
        return "OK"
    if all(s == "DESATIVADO" for s in statuses):
        return "Alerta"
    return "OK"


def _count_desativadas(records: List[Dict[str, Any]]) -> int:
    return sum(1 for r in records if r.get("status_rotina") == "DESATIVADO")
# ─── FETCH POR CLIENTE ──────────────────────────────────────────────────────
def _fetch_rotina_client(
    cfg: Dict[str, Any],
    timeout: int = 45,
) -> Dict[str, Any]:
    """Busca a rotina de UM cliente. Nunca lança exceção — sempre retorna dict.

    Campos de saída:
        status        → consolidado do cliente (OK, NAO RODOU, DESATIVADO, ...)
        total         → quantidade de rotinas retornadas pela query
        nao_rodou     → quantas rotinas estão com status_rotina = 'NAO RODOU'
        desativadas   → quantas rotinas estão com status_rotina = 'DESATIVADO'
        rotinas       → lista completa de registros (para o modal de detalhes)
        error_message → preenchido apenas quando há falha de coleta
    """
    base = {
        "prefix": cfg["prefix"],
        "name": cfg["name"],
        "status": "ok",
        "total": 0,
        "nao_rodou": 0,
        "desativadas": 0,
        "rotinas": [],
        "error_message": None,
    }

    # ── Configuração ausente ─────────────────────────────────────────────
    if not all([cfg["url"], cfg["username"], cfg["password"]]):
        base["status"] = "no_config"
        base["error_message"] = "Configuração ausente"
        return base

    # ── Autenticação ─────────────────────────────────────────────────────
    token = _authenticate(cfg)
    if not token:
        base["status"] = "auth_error"
        base["error_message"] = "Falha na autenticação com Metabase"
        return base

    payload = {
        "type": "native",
        "native": {
            "query": ROTINA_QUERY,
            "template-tags": {},
        },
        "database": cfg["database"],
    }

    logger.info(f"[ROTINA] {cfg['name']} | db={cfg['database']}")

    # ── Execução com retry ───────────────────────────────────────────────
    last_err: Optional[str] = None
    for attempt in range(3):
        try:
            resp = _session.post(
                f"{cfg['url']}/api/dataset",
                json=payload,
                headers={"X-Metabase-Session": token},
                timeout=timeout,
            )

            if resp.status_code not in (200, 202):
                last_err = f"Metabase retornou HTTP {resp.status_code}"
                logger.error(
                    f"[ROTINA HTTP {resp.status_code}] {cfg['name']}: {resp.text[:300]}"
                )
                break

            data = resp.json().get("data", {})
            rows = data.get("rows", []) or []
            cols = data.get("cols", []) or []

            # ── Sem rotinas cadastradas ──────────────────────────────────
            if not rows:
                base["status"] = "SEM_ROTINA"
                base["total"] = 0
                base["nao_rodou"] = 0
                base["desativadas"] = 0
                base["rotinas"] = []
                logger.info(f"[ROTINA] {cfg['name']} → SEM_ROTINA (0 registros)")
                return base

            # ── Converte resultado em registros ──────────────────────────
            columns = [c["name"] for c in cols]
            df = pd.DataFrame(rows, columns=columns)
            records = [_row_to_dict(r) for _, r in df.iterrows()]
            ignoradas = [r for r in records if _rotina_ignorada(cfg["name"], r.get("description"))]
            if ignoradas:
                records = [r for r in records if r not in ignoradas]
                base["ignoradas"] = [r.get("description") for r in ignoradas]
                logger.info(f"[ROTINA] {cfg['name']} → ignorando: {base['ignoradas']}")
            if not records:
                base["status"] = "SEM_ROTINA"
                base["total"] = 0
                base["nao_rodou"] = 0
                base["desativadas"] = 0
                base["rotinas"] = []
                return base

            # ── Contadores auxiliares ────────────────────────────────────
            base["total"] = len(records)
            base["rotinas"] = records
            base["nao_rodou"] = sum(
                1 for r in records if r.get("status_rotina") == "NAO RODOU"
            )
            base["desativadas"] = sum(
                1 for r in records if r.get("status_rotina") == "DESATIVADO"
            )
            base["status"] = _compute_status(records)

            logger.info(
                f"[ROTINA] {cfg['name']} → {base['status']} "
                f"({base['total']} registros, {base['nao_rodou']} não rodaram, "
                f"{base['desativadas']} desativadas)"
            )
            return base

        except Exception as e:
            last_err = str(e)
            logger.warning(f"[ROTINA RETRY {attempt + 1}] {cfg['name']} → {e}")
            time.sleep(2 ** attempt)

    # ── Falha após 3 tentativas ──────────────────────────────────────────
    base["status"] = "query_error"
    base["error_message"] = last_err or "Timeout ou falha após 3 tentativas"
    return base


# ─── FETCH TODOS OS CLIENTES ────────────────────────────────────────────────
def _fetch_rotina_all(max_workers: int = 12) -> List[Dict[str, Any]]:
    prefixes = get_all_prefixes()
    configs = [get_config(p) for p in prefixes]
    results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_rotina_client, c): c for c in configs}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                cfg = futures[fut]
                logger.error(f"[ROTINA ALL] Erro fatal em {cfg['prefix']}: {e}")
                results.append({
                    "prefix": cfg["prefix"], "name": cfg["name"],
                    "status": "query_error", "total": 0,
                    "rotinas": [], "error_message": str(e),
                })

    # Ordena: falhas primeiro, depois por nome
    order = {"NAO RODOU": 0, "SEM_ROTINA": 1, "VERIFICAR": 2,
             "query_error": 3, "auth_error": 4, "no_config": 5, "OK": 6}
    results.sort(key=lambda r: (order.get(r["status"], 99), r["name"].lower()))
    return results


# ─── API PÚBLICA ────────────────────────────────────────────────────────────
def collect_rotinas(use_cache: bool = True) -> List[Dict[str, Any]]:
    """Retorna a lista consolidada de rotinas por cliente (cache 5min)."""
    if use_cache:
        with _ROTINA_CACHE_LOCK:
            entry = _ROTINA_CACHE.get("data")
            ts = _ROTINA_CACHE.get("time", 0)
        if entry is not None and (time.time() - ts < ROTINA_CACHE_TTL):
            logger.info(f"[ROTINA CACHE HIT] age={int(time.time() - ts)}s")
            return entry

    data = _fetch_rotina_all()
    with _ROTINA_CACHE_LOCK:
        _ROTINA_CACHE["data"] = data
        _ROTINA_CACHE["time"] = time.time()
    return data


def get_rotina_client(prefix: str, use_cache: bool = True) -> Dict[str, Any]:
    """Retorna a rotina detalhada de UM cliente (usada no modal)."""
    prefix = prefix.upper().strip()

    if use_cache:
        with _ROTINA_CACHE_LOCK:
            entry = _ROTINA_CACHE.get("data")
            ts = _ROTINA_CACHE.get("time", 0)
        if entry is not None and (time.time() - ts < ROTINA_CACHE_TTL):
            match = next((r for r in entry if r["prefix"] == prefix), None)
            if match:
                return match

    # Cache miss → busca direto
    cfg = get_config(prefix)
    result = _fetch_rotina_client(cfg)

    # Aquece o cache com esse resultado (merge)
    with _ROTINA_CACHE_LOCK:
        existing = _ROTINA_CACHE.get("data") or []
        existing = [r for r in existing if r["prefix"] != prefix]
        existing.append(result)
        _ROTINA_CACHE["data"] = existing
        _ROTINA_CACHE["time"] = time.time()

    return result


def invalidate_rotina_cache() -> None:
    with _ROTINA_CACHE_LOCK:
        _ROTINA_CACHE["data"] = None
        _ROTINA_CACHE["time"] = 0
    logger.info("[ROTINA CACHE] Invalidado")


def warm_rotina_cache() -> None:
    """Executa em thread separada no startup."""
    logger.info("[ROTINA] Aquecendo cache em background...")
    try:
        data = collect_rotinas(use_cache=False)
        logger.info(f"[ROTINA] Cache aquecido: {len(data)} cliente(s)")
    except Exception as e:
        logger.error(f"[ROTINA WARM] {e}")


def get_rotina_alertas() -> List[Dict[str, Any]]:
    """Retorna apenas clientes com problema (para o popup/som do frontend)."""
    data = collect_rotinas()
    return [
        {
            "prefix": r["prefix"],
            "name": r["name"],
            "status": r["status"],
            "error_message": r.get("error_message"),
        }
        for r in data
        if r["status"] in ("NAO RODOU", "SEM_ROTINA", "VERIFICAR",
                           "query_error", "auth_error", "no_config")
    ]