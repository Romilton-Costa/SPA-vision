import requests
import pandas as pd
import os
import time
import logging
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------- SESSION (COM RETRY) ----------------
def create_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"]
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session

session = create_session()

# ---------------- CONSTANTS ----------------
NATIVE_QUERY = """
SELECT * FROM judicial_processes_pending_notice_validations
WHERE registered = false
"""

TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
TOKEN_TTL = 300  # 5 minutos

# ---------------- CONFIG ----------------
def get_config(prefix: str) -> Dict[str, Any]:
    return {
        "prefix": prefix,
        "name": os.getenv(f"{prefix}_NAME", ""),
        "url": os.getenv(f"{prefix}_URL", "").rstrip("/"),
        "username": os.getenv(f"{prefix}_USERNAME", ""),
        "password": os.getenv(f"{prefix}_PASSWORD", ""),
        "database": int(os.getenv(f"{prefix}_DB", 2))
    }

# ---------------- AUTH ----------------
def authenticate(cfg: Dict[str, Any]) -> Optional[str]:
    cache = TOKEN_CACHE.get(cfg["prefix"])

    if cache and (time.time() - cache["time"] < TOKEN_TTL):
        return cache["token"]

    try:
        logger.info(f"[AUTH] {cfg['name']}")

        r = session.post(
            f"{cfg['url']}/api/session",
            json={
                "username": cfg["username"],
                "password": cfg["password"]
            },
            timeout=10
        )

        r.raise_for_status()

        token = r.json().get("id")

        TOKEN_CACHE[cfg["prefix"]] = {
            "token": token,
            "time": time.time()
        }

        return token

    except requests.exceptions.RequestException as e:
        logger.error(f"[AUTH ERROR] {cfg['name']} -> {e}")
        return None

# ---------------- QUERY ----------------
def run_query(cfg, token, retries=2):
    payload = {
        "type": "native",
        "native": {
            "query": NATIVE_QUERY,
            "template-tags": {}
        },
        "database": cfg["database"]
    }

    for _ in range(retries):
        try:
            resp = session.post(
                f"{cfg['url']}/api/dataset",
                json=payload,
                headers={"X-Metabase-Session": token},
                timeout=30
            )

            if resp.status_code not in [200, 202]:
                print(f"[QUERY ERROR] {cfg['name']} -> {resp.status_code}")
                print(resp.text[:300])
                return None

            json_data = resp.json()

            if "data" not in json_data or not json_data["data"]:
                print(f"[EMPTY DATA] {cfg['name']}")
                return None

            data = json_data["data"]

            # 🔥 proteção extra
            if "rows" not in data or "cols" not in data:
                print(f"[INVALID FORMAT] {cfg['name']}")
                print(data)
                return None

            columns = [col["name"] for col in data["cols"]]
            rows = data["rows"]

            print(f"[OK] {cfg['name']} -> {len(rows)} registros")

            return pd.DataFrame(rows, columns=columns)

        except Exception as e:
            print(f"[QUERY EXCEPTION] {cfg['name']} -> {e}")
            time.sleep(1)

    return None
# ---------------- PROCESS ----------------
def process(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not all([cfg["url"], cfg["username"], cfg["password"]]):
        return {
            "prefix": cfg["prefix"],
            "name": cfg["name"],
            "status": "no_config",
            "df": None
        }

    token = authenticate(cfg)

    if not token:
        return {
            "prefix": cfg["prefix"],
            "name": cfg["name"],
            "status": "auth_error",
            "df": None
        }

    df = run_query(cfg, token)

    return {
        "prefix": cfg["prefix"],
        "name": cfg["name"],
        "status": "ok" if df is not None else "query_error",
        "df": df
    }

# ---------------- MAIN ----------------
def collect_all(max_workers: int = 5):
    prefixes = [
        p.strip()
        for p in os.getenv("METABASES", "").split(",")
        if p.strip()
    ]

    configs = [get_config(p) for p in prefixes]

    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process, c): c for c in configs}

        for future in as_completed(futures):
            result = future.result()
            logger.info(f"[RESULT] {result['name']} -> {result['status']}")
            results.append(result)

    return results