import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()


class SPACrawler:
    def __init__(self):
        self.base_url = os.getenv("SPA_BASE_URL").rstrip("/")
        self.email = os.getenv("SPA_SUPPORT_EMAIL")
        self.password = os.getenv("SPA_SUPPORT_PASSWORD")
        self.proc = os.getenv("INT_PROC")
        self.soap_id = os.getenv("INT_SOAP_ID")

        self.session = requests.Session()
        self.csrf_token = None

    def authenticate(self):
        login_url = f"{self.base_url}/users/sign_in"

        res = self.session.get(login_url)
        soup = BeautifulSoup(res.text, "html.parser")

        meta = soup.find("meta", {"name": "csrf-token"})
        if not meta:
            raise Exception("CSRF token não encontrado")

        self.csrf_token = meta["content"]

        login_res = self.session.post(
            login_url,
            data={
                "user[email]": self.email,
                "user[password]": self.password,
                "authenticity_token": self.csrf_token,
            },
            headers={
                "x-csrf-token": self.csrf_token,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allow_redirects=True,
        )

        if "/sign_in" in login_res.url:
            raise Exception("Login falhou")

    def test_movements(self,soap_id=None):
        target_id = soap_id if soap_id else self.soap_id
        url = f"{self.base_url}/judicial_processes/{self.proc}/movements"

        res = self.session.get(
            url,
            params={"soap_setting_id": target_id},
            headers={
                "accept": "text/plain, */*; q=0.01",
                "x-requested-with": "XMLHttpRequest",
                "x-csrf-token": self.csrf_token,
                "referer": f"{self.base_url}/procedures/62124",
            },
        )

        return res.status_code, res.text[:300]