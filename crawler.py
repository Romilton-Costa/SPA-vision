import os
import requests
import logging
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class SPACrawler:

    def __init__(self, base_url: str = None, email: str = None, password: str = None):
        self.base_url = (base_url or os.getenv("SPA_URL", "")).rstrip("/")
        self.email = email or os.getenv("SPA_EMAIL", "")
        self.password = password or os.getenv("SPA_PASSWORD", "")
        
        self.hc_email = os.getenv("SPA_HEALTHCHECK_EMAIL", self.email)
        self.hc_password = os.getenv("SPA_HEALTHCHECK_PASSWORD", self.password)

        self.session = requests.Session()
        self.csrf_token = None

    def authenticate(self, use_healthcheck_creds: bool = True, base_url: str = None):
        if base_url:
            self.base_url = base_url.rstrip("/")

        if not self.base_url:
            raise RuntimeError("URL do SPA não informada.")

        email = self.hc_email if use_healthcheck_creds else self.email
        password = self.hc_password if use_healthcheck_creds else self.password

        if not email or not password:
            raise RuntimeError("Credenciais de acesso não configuradas.")

        login_url = f"{self.base_url}/users/sign_in"

        res = self.session.get(login_url, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        meta = soup.find("meta", {"name": "csrf-token"})
        if not meta:
            raise RuntimeError("CSRF Token não localizado no SPA.")

        self.csrf_token = meta["content"]

        login_res = self.session.post(
            login_url,
            data={
                "user[email]": email,
                "user[password]": password,
                "authenticity_token": self.csrf_token,
            },
            headers={
                "x-csrf-token": self.csrf_token,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allow_redirects=True,
            timeout=15,
        )

        if "/sign_in" in login_res.url:
            raise RuntimeError(f"Falha de autenticação para {email}")

    def test_movements(self, soap_id: str, proc: str, base_url: str = None):
        if base_url:
            self.base_url = base_url.rstrip("/")

        url = f"{self.base_url}/judicial_processes/{proc}/movements"

        res = self.session.get(
            url,
            params={"soap_setting_id": soap_id},
            headers={
                "accept": "text/plain, */*; q=0.01",
                "x-requested-with": "XMLHttpRequest",
                "x-csrf-token": self.csrf_token or "",
                "referer": f"{self.base_url}/judicial_processes/{proc}",
            },
            timeout=15,
        )
        return res.status_code, res.text[:300]