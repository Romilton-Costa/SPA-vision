# main.py (ou errbit_service.py)
from datetime import datetime, timedelta
import re
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from fastapi import APIRouter, HTTPException
from dotenv import load_dotenv

router = APIRouter()

BASE_URL_ERRBIT = os.getenv("BASE_URL_ERRBIT")
LOGIN_ERRBIT = os.getenv("LOGIN_ERRBIT")
PASSWORD_ERRBIT = os.getenv("PASSWORD_ERRBIT")

# Lista de APPS fornecida[cite: 8]
APPS = {
    "PGE Paraíba": "/apps/662aaeaebdb0600008add772",
    "PGE MA": "/apps/6419f64f2277310007708b00",
    "PGM João Pessoa": "/apps/6419f6902277310007708b05",
    # ... adicione os outros da sua lista[cite: 8]
    "PGE Paraíba": "/apps/662aaeaebdb0600008add772",
    
    "PGE MA": "/apps/6419f64f2277310007708b00",
    
    "PGE MT": "/apps/6419f7542277310015708b1e",
    
    
    
    "SPA PGDF - Produção": "/apps/66fc6647b284f90016872dc0",
    
    "PGM São Luís": "/apps/6419f67a2277310015708afe",
   
    
    
    "PGM Juiz de Fora": "/apps/6419f6cb2277310007708b0e",
    
    "PGM João Pessoa": "/apps/6419f6902277310007708b05",
    
    "PGDF - Azure": "/apps/6957f53e4e95e10008475769",
    
    "PGM Maceió": "/apps/6419f7f32277310015708b39",
   
    "PGM Niterói": "/apps/6419f6a42277310015708b02",
    
    "PGM Vitória": "/apps/6419f8012277310015708b3e",
    
    "PGM Cuiabá": "/apps/6419f80c2277310015708b43",
    
    
    "PGM Goytacazes": "/apps/672360dbb6ff4200080bb743",
    
    "PGM Lucas do Rio Verde": "/apps/691da26f4e95e100082d0298",
    
    "PGM Amargosa": "/apps/68caeb2f4e95e1001659c154",
    
    "PGM Santa Luzia": "/apps/68f7bd084e95e1000f19db16",
    
    "POC Niterói RJ": "/apps/64831e7a2277310016d8abdd",
    
    "PGM Santo Antônio de Jesus": "/apps/6419f7ad2277310015708b2e",
    
    "CNI Sistema Indústria": "/apps/6901d2b94e95e1000f1dd117",
    
    "PGE ES - Homologação": "/apps/6419f9702277310015708b63",
    
    "PGM Cariacica": "/apps/6419f6ec2277310007708b12",
    
    "PGM Jaboatão": "/apps/64c7fb6222773100086af2b5",
    
    "PBPrev": "/apps/66709e59b284f900171bf481",
    
    "PGM Camaragibe": "/apps/662a81aebdb0600008adc125",
    
    "PGE ES": "/apps/6419f7442277310015708b19",
    
    "SPA PGDF - Homologação": "/apps/6540a102227731000dbc2cb1",
    
    "PGM Ilheus": "/apps/67f81260a7454300167e3888",
    
    "PGM Linhares": "/apps/69b442872a4d660016a9e74b",
   
    "Cagepa": "/apps/68d408594e95e100080bd3a8",
   
    "PGM Aquiraz": "/apps/64c800cb22773100166b1a7d",
    
    "PGDF - Importador de Créditos": "/apps/69baf2322a4d6600084fbd7a",
   
    "Araruama": "/apps/690cd8bc4e95e1000f2360cb",
    
    "PGM Porto Velho v1": "/apps/68a366094e95e100084e3879",
   
    
    "PGM Curvelo": "/apps/66708bd4b284f900091c1a7b",
    
    "PGM Serra": "/apps/6419f70d2277310007708b16",
   
    "PGM Aracruz": "/apps/6419f7852277310015708b28",
    
    "PGM Teresina": "/apps/6419f6fe2277310015708b0a",
    
    "UEPB": "/apps/691368864e95e1000826d13f",
    
    "PGM Viana": "/apps/6419f7192277310015708b0f",
    
    "PGM Cabo Frio": "/apps/6419f7992277310007708b1b",
    
    "PGM Sergipe": "/apps/68f68d3e4e95e1000819399b",
    
    "IPAM": "/apps/665f565dbdb0600008c04ab9",
   
    "PGM Tiangua - Licenciamento": "/apps/6419f8722277310015708b4d",
    
    "PGM João Pessoa - Homologação": "/apps/6419f8f32277310015708b5a",
    
    "PGM Tiangua": "/apps/6419f7e12277310015708b34",
    
    "PGM Aracatuba": "/apps/6943c6f04e95e10008417356",
   
    "PGM Niterói - Homologação": "/apps/6419f90c2277310015708b5e",
   
    
    "PGM Vila Velha": "/apps/6419f6b62277310007708b0a",
    
    "PGM Rio Preto": "/apps/69d668e72a4d660008752836",
    
    "PGE MA - Homologação": "/apps/6419f8bd2277310015708b51",
    
    "PGM Porto Velho v2": "/apps/683f28764e95e10016b2245a",
    
    "DJE": "/apps/690de3b44e95e1000f23f841",
   
    "PGM Niteroi | SPA 3": "/apps/69eb9ea22a4d66000887eef8",

    
    "ConnectGov": "/apps/657f6a6e22773100078ed954",
    "HG DF": "/apps/648e87062277310016dca1ad",
    "Hom3": "/apps/66ba50c8b284f900168fd27a",
    "Homologação Bugfixes": "/apps/673b6751a745430015014837",
    "Licenciamento HG": "/apps/66fad148b284f9000885eb3f",
    "PGE Paraíba Homologação": "/apps/662ab422bdb0600016add4bc",
    "PGM Juiz de Fora - Homologação": "/apps/6419f93f2277310007708b28",
    "PGM São Luís - Homologação": "/apps/6419f8db2277310015708b55",
    "PGM Teresina - Homologação": "/apps/6419f9542277310007708b2d",
    "PGM Vila Velha - Homologação": "/apps/6419f9242277310007708b23",
   
}

KEYWORD = "Não chegaram expedientes/avisos!"

def get_errbit_session():
    session = requests.Session()
    login_page = session.get(f"{BASE_URL_ERRBIT}/users/sign_in", timeout=15)
    soup = BeautifulSoup(login_page.text, "html.parser")
    csrf = soup.find("meta", attrs={"name": "csrf-token"})
    
    if not csrf:
        raise Exception("Não encontrou CSRF token no Errbit")
        
    session.post(
        f"{BASE_URL_ERRBIT}/users/sign_in",
        data={"user[email]": LOGIN_ERRBIT, "user[password]": PASSWORD_ERRBIT},
        headers={"X-CSRF-Token": csrf["content"]},
        timeout=15,
    )
    return session

def extract_errbit_detail(session, href):
    """Extrai a mensagem real e a data do erro"""
    try:
        resp = session.get(f"{BASE_URL_ERRBIT}{href}", timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        content_div = soup.find("div", id="content")
        error_title = "Erro de Sistema"
        if content_div and content_div.find("h4"):
            error_title = content_div.find("h4").get_text(strip=True)


        clean_msg = "Verificar no Errbit"
        for pre in soup.find_all("pre"):
            txt = pre.get_text(strip=True)
            match = re.search(r'["\']mensagem["\']\s*=>\s*["\']([^"\']+)', txt, re.I)
            if match:
                clean_msg = match.group(1)
                break
        
        occ_label = soup.find("th", string=re.compile(r"Occurred", re.I))
        occurred_at = occ_label.find_next_sibling("td").get_text(strip=True) if occ_label else ""
        
        return {
            "title": error_title,
            "message": clean_msg, 
            "occurred_at": occurred_at}
    except:
        return {"title": "Erro na extração", "message": "Erro de conexão", "occurred_at": ""}
'''
@router.get("/api/errbit/expedientes")
async def get_expedientes_errors():
    """Endpoint unificado no FastAPI[cite: 6]"""
    try:
        session = get_errbit_session()
        all_errors = []
        today_iso = datetime.now().strftime("%Y-%m-%d")
        today_abbr = datetime.now().strftime("%b %d")

        # Para testes, você pode limitar o loop a 5 apps para ser mais rápido
        for name, path in APPS.items():
            url = f"{BASE_URL_ERRBIT}{path}?page=1"
            resp = session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            for row in soup.find_all("tr"):
                a = row.find("a", class_="nowrap")
                if not a: continue
                
                title = (a.get("title") or a.get_text(strip=True)).upper()
                if KEYWORD.upper() in title:
                    detail = extract_errbit_detail(session, a.get("href"))
                    occ_at = detail["occurred_at"]
                    
                    if today_iso in occ_at or today_abbr in occ_at:
                        all_errors.append({
                            "app": name,
                            "message": detail["message"],
                            "url": f"{BASE_URL_ERRBIT}{a.get('href')}",
                            "occurred_at": occ_at
                        })
        
        return {"total": len(all_errors), "errors": all_errors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
'''
@router.get("/api/errbit/expedientes")
async def get_expedientes_errors():
    """Endpoint unificado no FastAPI[cite: 14]"""
    try:
        session = get_errbit_session()
        all_errors = []
        
        # 1. Definimos as datas dos últimos 3 dias para um filtro mais útil
        recent_dates = []
        for i in range(1):
            d = datetime.now() - timedelta(days=i)
            recent_dates.append(d.strftime("%Y-%m-%d")) # Formato 2026-05-03
            recent_dates.append(d.strftime("%b %d"))    # Formato May 03

        # Para testes, limite o loop se houver muitas APPS
        for name, path in list(APPS.items()):
            url = f"{BASE_URL_ERRBIT}{path}?page=1"
            resp = session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            for row in soup.find_all("tr"):
                a = row.find("a", class_="nowrap")
                if not a: continue
                
                title = (a.get("title") or a.get_text(strip=True)).upper()
                if KEYWORD.upper() in title:
                    detail = extract_errbit_detail(session, a.get("href"))
                    occ_at = detail["occurred_at"]
                    
                    # 2. ALTERAÇÃO: Verifica se a data do erro está entre os dias recentes
                    # Se quiser ver TUDO de qualquer dia, remova este 'if'
                    is_recent = any(date_str in occ_at for date_str in recent_dates)
                    
                    if is_recent:
                        detail = extract_errbit_detail(session, a.get("href"))
                        all_errors.append({
                            "app_name": name, # Ajustado para bater com seu JS
                            "title": detail["title"],
                            "message": detail["message"],
                            "link": f"{BASE_URL_ERRBIT}{a.get('href')}", # Ajustado para 'link'
                            "occ_at": occ_at # Ajustado para 'occ_at'
                        })
        
        return {"total": len(all_errors), "errors": all_errors}
    except Exception as e:
        print(f"Erro no processamento: {e}")
        raise HTTPException(status_code=500, detail=str(e))