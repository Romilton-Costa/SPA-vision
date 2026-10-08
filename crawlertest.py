@dataclass
class ClientConfig:
    name: str
    base_url: str
    email: str
    password: str
    default_proc: Optional[str] = None
    tribunals: dict[str, str] = field(default_factory=dict)  # {"TRT2": "10", ...}

    @classmethod
def load_clients(cls, prefixes: Optional[Iterable[str]] = None) -> list[ClientConfig]:
    if prefixes is None:
        env_prefixes = os.getenv("CLIENT_PREFIXES", "").strip()
        if env_prefixes:
            prefixes = [p.strip() for p in env_prefixes.split(",") if p.strip()]
        else:
            prefixes = cls._discover_prefixes()
    prefixes = list(prefixes)

    clients: list[ClientConfig] = []
    for prefix in prefixes:
        missing = [k for k in cls.REQUIRED if not cls._get(prefix, k)]
        if missing:
            logger.warning("Cliente '%s' ignorado — faltam: %s",
                           prefix, ", ".join(f"{prefix}_{m}" for m in missing))
            continue

        client = ClientConfig(
            name=prefix,
            base_url=cls._get(prefix, "URL").rstrip("/"),
            email=cls._get(prefix, "EMAIL"),
            password=cls._get(prefix, "PASSWORD"),
            default_proc=cls._get(prefix, "PROC"),
        )

        # ---- Tribunais específicos do cliente ----
        trib_prefix = f"{prefix}_TRIBUNAL_"
        for var, val in os.environ.items():
            if var.startswith(trib_prefix):
                nome = var[len(trib_prefix):].strip().upper()
                if nome and val:
                    client.tribunals[nome] = val

        # Fallback: se o cliente não tem nenhum tribunal explícito,
        # aceita <PREFIX>_SOAP_ID como "DEFAULT"
        if not client.tribunals:
            sid = cls._get(prefix, "SOAP_ID")
            if sid:
                client.tribunals["DEFAULT"] = sid
                logger.info("[%s] Usando SOAP_ID como tribunal DEFAULT.", prefix)

        # Tribunais globais (aplicados a quem não tem o mesmo nome)
        for var, val in os.environ.items():
            if var.startswith("TRIBUNAL_") and not var.startswith(trib_prefix):
                nome = var[len("TRIBUNAL_"):].strip().upper()
                if nome and val:
                    client.tribunals.setdefault(nome, val)

        clients.append(client)

    logger.info("Clientes carregados: %s", {
        c.name: list(c.tribunals.keys()) for c in clients
    })
    return clients

    def fetch_movements(self, proc: str, tribunal: str, soap_id: str) -> MovementResult:
    try:
        self.authenticate()
    except Exception as e:
        return MovementResult(
            client=self.config.name, proc=proc, tribunal=tribunal,
            soap_id=soap_id, status_code=None, ok=False, error=str(e),
        )

    url = f"{self.config.base_url}/judicial_processes/{proc}/movements"
    headers = {
        "accept": "text/plain, */*; q=0.01",
        "x-requested-with": "XMLHttpRequest",
        "x-csrf-token": self.csrf_token or "",
        "referer": f"{self.config.base_url}/procedures/{proc}",
    }

    import time
    t0 = time.perf_counter()
    try:
        res = self.session.get(
            url,
            params={"soap_setting_id": soap_id},
            headers=headers,
            timeout=self.timeout,
        )
    except requests.RequestException as e:
        return MovementResult(
            client=self.config.name, proc=proc, tribunal=tribunal,
            soap_id=soap_id, status_code=None, ok=False,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            error=f"Request error: {e}",
        )

    elapsed = int((time.perf_counter() - t0) * 1000)

    # ---- Detecta sessão expirada / login ----
    content_type = res.headers.get("content-type", "")
    is_login_redirect = "/sign_in" in res.url
    is_html_login = "text/html" in content_type and "<form" in res.text[:2000].lower()

    if is_login_redirect or is_html_login:
        return MovementResult(
            client=self.config.name, proc=proc, tribunal=tribunal,
            soap_id=soap_id, status_code=res.status_code, ok=False,
            elapsed_ms=elapsed,
            body_preview=res.text[:300],
            error="Sessão expirada / redirecionado para login",
        )

    ok = res.status_code == 200
    return MovementResult(
        client=self.config.name, proc=proc, tribunal=tribunal,
        soap_id=soap_id, status_code=res.status_code, ok=ok,
        elapsed_ms=elapsed, body_preview=res.text[:300],
        error=None if ok else f"HTTP {res.status_code}",
    )
@dataclass
class MovementResult:
    client: str
    proc: str
    tribunal: str
    soap_id: str
    status_code: Optional[int]
    ok: bool
    elapsed_ms: Optional[int] = None
    body_preview: str = ""
    error: Optional[str] = None

def _run_client(self, client: ClientConfig, proc_override: Optional[str]) -> list[MovementResult]:
    proc = proc_override or client.default_proc
    if not proc:
        logger.warning("[%s] Sem processo definido — ignorando.", client.name)
        return []
    if not client.tribunals:
        logger.warning("[%s] Sem tribunais configurados — ignorando.", client.name)
        return []

    crawler = SPACrawler(client)
    results: list[MovementResult] = []
    try:
        crawler.authenticate()
        for tribunal, sid in sorted(client.tribunals.items()):
            logger.info("[%s] Tribunal %s (soap_id=%s) proc=%s",
                        client.name, tribunal, sid, proc)
            r = crawler.fetch_movements(proc, tribunal, sid)
            results.append(r)
            logger.info("[%s] %s -> HTTP %s | ok=%s | %sms",
                        client.name, tribunal, r.status_code, r.ok, r.elapsed_ms)
    except Exception as e:
        logger.error("[%s] Falha na autenticação: %s", client.name, e)
        for tribunal, sid in client.tribunals.items():
            results.append(MovementResult(
                client=client.name, proc=proc, tribunal=tribunal,
                soap_id=sid, status_code=None, ok=False, error=str(e),
            ))
    finally:
        crawler.close()
    return results

    def print_report(results: list[MovementResult]) -> None:
    if not results:
        print("\n⚠️  Nenhum resultado.")
        return

    # Agrupa por cliente
    por_cliente: dict[str, list[MovementResult]] = {}
    for r in results:
        por_cliente.setdefault(r.client, []).append(r)

    for cliente, items in por_cliente.items():
        print(f"\n=== Cliente: {cliente} ===")
        print(f"{'TRIBUNAL':<12} {'SOAP_ID':<8} {'PROC':<24} {'HTTP':<6} {'OK':<6} {'ms':<7} ERRO")
        print("-" * 90)
        for r in sorted(items, key=lambda x: x.tribunal):
            print(f"{r.tribunal:<12} {r.soap_id:<8} {r.proc:<24} "
                  f"{str(r.status_code or '-'):<6} {str(r.ok):<6} "
                  f"{str(r.elapsed_ms or '-'):<7} {r.error or ''}")

    total = len(results)
    ok = sum(1 for r in results if r.ok)
    print(f"\nResumo geral: {ok}/{total} OK ({ok/total*100:.1f}%)")