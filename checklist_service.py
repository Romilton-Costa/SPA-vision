"""Checklists dos clientes (GLPI → planilha do Google).

Lê a planilha "ChamadosChecklistCS_Relatorio" (relatório dos últimos 3 meses,
entidade Checklist) e devolve, por cliente, cada checklist do dia/turno com a
situação: feito (tem data de solução) ou não feito.

A planilha precisa estar compartilhada como "Qualquer pessoa com o link: Leitor".
Endpoint: GET /api/checklist
"""
import csv
import io
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# ─── Configuração ────────────────────────────────────────────────────────────
CHECKLIST_SHEET_ID = os.getenv("CHECKLIST_SHEET_ID", "1Xz_XIKIGbWSL6F1_T5EjnB9pbuWuvQp_9fIQv5gRkKU")
CHECKLIST_CACHE_TTL = int(os.getenv("CHECKLIST_CACHE_TTL_SECONDS", 600))  # 10 min
# A consulta SQL do relatório aplica CONVERT_TZ(-03:00) sobre horários já locais
# do GLPI, então 08:00 aparece como 05:00. Esta correção devolve o horário real.
# Se a consulta for ajustada (sem CONVERT_TZ), defina CHECKLIST_HOUR_FIX=0.
CHECKLIST_HOUR_FIX = int(os.getenv("CHECKLIST_HOUR_FIX", 3))
AFTERNOON_FROM = 12  # abertura a partir de 12h (já corrigida) = checklist da tarde

# Clientes que não entram no painel, mesmo que tenham chamados na planilha
EXCLUDED = ["PGM - Araçatuba/SP"]

# Escala de técnicos por cliente: (manhã 08:00, tarde 15:00)
ESCALA = {
    "PGE -Espírito Santo": ("Eduardo", "Eduardo"),
    "PGE - Maranhão": ("Erick", "Erick"),
    "PGE - Mato Grosso": ("Victor", "Victor"),
    "PGM - Aracruz/ES": ("Athila", "Athila"),
    "PGM - Cariacica/ES": ("Athila", "Athila"),
    "PGM - Cuiabá/MT": ("Julia", "Yuri"),
    "PGM - João Pessoa/PB": ("Angelo", "Angelo"),
    "PGM - Juiz de Fora/MG": ("Izadora", "Marinho"),
    "PGM - Maceió/AL": ("Allan", "Marinho"),
    "PGM - Niterói/RJ": ("Flávio", "Flávio"),
    "PGM Santo Antônio de Jesus/BA": ("Julia", "Sarah"),
    "PGM - São Luís/MA": ("Daysa", "Jefferson"),
    "PGM - Linhares/ES": ("Allan", "Marinho"),
    "PGM - LINHARES IPASLI/ES": ("Allan", "Marinho"),
    "PGM - LINHARES SAAE/ES": ("Allan", "Marinho"),
    "PGM - Teresina/PI": ("Izadora", "Izadora"),
    "PGM - Tianguá/CE": ("Jessica", "Allan"),
    "PGM - Viana/ES": ("Gabriely", "Gabriely"),
    "PGM - Vitória/ES": ("Izadora", "Izadora"),
    "PGM - Jaboatão dos Guararapes/PE": ("Allan", "Yuri"),
    "PGM - Aquiraz/CE": ("Allan", "Yuri"),
    "PGE - Paraíba/PB": ("Allan", "Jessica"),
    "PBPrev - Previdência/PB": ("Jessica", "Allan"),
    "PGM - Camaragibe/PE": ("Izadora", "Juliana"),
    "PGM - Curvelo/MG": ("Jessica", "Sarah"),
    "PGM - Campos dos Goytacazes/RJ": ("Izadora", "Juliana"),
    "PGM - Ilhéus/BA": ("Jessica", "Juliana"),
    "PG - Distrito Federal/DF": ("Clara", "Priscila"),
    "CAGEPA - Paraíba/PB": ("Julia", "Julia"),
    "IPAM - Previdência São Luis/MA": ("Jefferson", "Daysa"),
    "PGM - Amargosa/BA": ("Julia", "Sarah"),
    "PGM - Araruama/RJ": ("Jessica", "Marinho"),
    "PGM - Lucas do Rio Verde/MT": ("Julia", "Juliana"),
    "UEPB - Universidade Federal/PB": ("Jessica", "Sarah"),
    "PGM - Santa Luzia/MG": ("Julia", "Sarah"),
    "PGM - Cachoeiro de Itapimirim/ES": ("Gabriely", "Gabriely"),
}
UF_FALLBACK = {"pgemaranhao": "MA", "pgematogrosso": "MT", "pgeespiritosanto": "ES"}


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


_ESCALA = {_norm(k): v for k, v in ESCALA.items()}
_EXCLUDED = {_norm(c) for c in EXCLUDED}
_DT_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})[ ,]+(\d{1,2}):(\d{2})")


def _parse_br(s: str) -> Optional[datetime]:
    """'28/09/2026 05:00' → datetime já com a correção de horário."""
    m = _DT_RE.search(s or "")
    if not m:
        return None
    d, mo, y, h, mi = map(int, m.groups())
    return datetime(y, mo, d, h, mi) + timedelta(hours=CHECKLIST_HOUR_FIX)


def _decode(s: str) -> str:
    return (s.replace("&gt;", ">").replace("&lt;", "<").replace("&quot;", '"')
             .replace("&#39;", "'").replace("&amp;", "&"))


def _client_name(title: str) -> str:
    return _decode(title).split("->")[-1].strip()


def _uf(name: str) -> str:
    m = re.search(r"/([A-Z]{2})\s*$", name)
    return m.group(1) if m else UF_FALLBACK.get(_norm(name), "")


def parse_csv(text: str) -> List[Dict[str, Any]]:
    """Converte o CSV da planilha na lista de clientes com seus checklists."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("Planilha vazia")
    head = [_norm(h) for h in rows[0]]

    def col(n: str) -> int:
        return head.index(_norm(n)) if _norm(n) in head else -1

    i_e, i_id, i_t, i_tec, i_a, i_s = (col(c) for c in
        ("Entidade", "ID", "Titulo", "Tecnico", "Data_abertura", "Data_solucao"))
    if min(i_e, i_id, i_t, i_a) < 0:
        raise ValueError("A planilha precisa das colunas Entidade, ID, Titulo, Data_abertura e Data_solucao")

    get = lambda r, i: (r[i] if 0 <= i < len(r) else "").strip()
    clientes: Dict[str, Dict[str, Any]] = {}
    for r in rows[1:]:
        if _norm(get(r, i_e)) != "checklist":
            continue
        aberto = _parse_br(get(r, i_a))
        if not aberto:
            continue
        nome = _client_name(get(r, i_t))
        key = _norm(nome)
        if not key or key in _EXCLUDED:
            continue
        sol_raw = get(r, i_s)
        sol = _parse_br(sol_raw) if sol_raw else None
        turno = "T" if aberto.hour >= AFTERNOON_FROM else "M"
        c = clientes.setdefault(key, {"nome": nome, "uf": _uf(nome), "escala": None, "_dias": {}})
        c["nome"] = nome
        rec = {
            "data": aberto.strftime("%Y-%m-%d"),
            "turno": turno,
            "id": get(r, i_id),
            "aberto": aberto.strftime("%H:%M"),
            "feito": bool(sol_raw),
            "solucao": sol.strftime("%Y-%m-%d %H:%M") if sol else (sol_raw or None),
            "tecnico": get(r, i_tec) or None,
        }
        slot = (rec["data"], turno)
        atual = c["_dias"].get(slot)
        # duplicado no mesmo dia/turno: fica o mais recente (maior ID)
        if not atual or _int(rec["id"]) > _int(atual["id"]):
            c["_dias"][slot] = rec

    out = []
    for key, c in clientes.items():
        esc = _ESCALA.get(key)
        out.append({
            "nome": c["nome"],
            "uf": c["uf"],
            "escala": {"manha": esc[0], "tarde": esc[1]} if esc else None,
            "checklists": sorted(c["_dias"].values(), key=lambda x: (x["data"], x["turno"])),
        })
    # clientes da escala sem nenhum chamado no período também aparecem
    for nome, (m, t) in ESCALA.items():
        if _norm(nome) not in clientes and _norm(nome) not in _EXCLUDED:
            out.append({"nome": nome, "uf": _uf(nome), "escala": {"manha": m, "tarde": t}, "checklists": []})
    out.sort(key=lambda x: _norm(x["nome"]))
    return out


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ─── Leitura da planilha + cache ─────────────────────────────────────────────
_CACHE: Dict[str, Any] = {"data": None, "time": 0.0, "error": None}
_LOCK = threading.Lock()


def _fetch_csv() -> str:
    urls = [
        f"https://docs.google.com/spreadsheets/d/{CHECKLIST_SHEET_ID}/export?format=csv",
        f"https://docs.google.com/spreadsheets/d/{CHECKLIST_SHEET_ID}/gviz/tq?tqx=out:csv&headers=1",
    ]
    last = None
    for u in urls:
        try:
            r = requests.get(u, timeout=30)
            if r.ok and "entidade" in r.text[:300].lower():
                r.encoding = "utf-8"
                return r.text
            last = f"HTTP {r.status_code}" if not r.ok else "resposta sem a coluna Entidade (planilha privada?)"
        except requests.RequestException as e:
            last = str(e)
    raise RuntimeError(f"Não foi possível ler a planilha de checklists: {last}")


def collect_checklists(force: bool = False) -> Dict[str, Any]:
    with _LOCK:
        fresh = _CACHE["data"] is not None and time.time() - _CACHE["time"] < CHECKLIST_CACHE_TTL
        if fresh and not force:
            return _CACHE["data"]
    try:
        clientes = parse_csv(_fetch_csv())
        datas = sorted({c["data"] for cl in clientes for c in cl["checklists"]})
        data = {
            "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            "periodo": {"inicio": datas[0] if datas else None, "fim": datas[-1] if datas else None},
            "total": sum(len(cl["checklists"]) for cl in clientes),
            "clientes": clientes,
        }
        with _LOCK:
            _CACHE.update(data=data, time=time.time(), error=None)
        logger.info(f"[CHECKLIST] {data['total']} checklist(s) de {len(clientes)} cliente(s).")
        return data
    except Exception as e:
        logger.error(f"[CHECKLIST] {e}")
        with _LOCK:
            _CACHE["error"] = str(e)
            if _CACHE["data"] is not None:
                return _CACHE["data"]  # mantém o último dado bom
        raise


@router.get("/api/checklist", tags=["checklist"])
def api_checklist():
    """Checklists por cliente (feito = chamado com data de solução)."""
    try:
        return collect_checklists()
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


if __name__ == "__main__":
    # Teste manual: python checklist_service.py [arquivo.csv]
    import json
    import sys
    texto = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else _fetch_csv()
    res = parse_csv(texto)
    print(json.dumps({"clientes": len(res), "checklists": sum(len(c["checklists"]) for c in res)}, ensure_ascii=False))
