import socket
import ssl
from urllib.parse import urlparse
from datetime import datetime, UTC

def limpar_host(url):

    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)

    return parsed.hostname
def obter_nome_cliente(url):
    """
    Extrai um nome amigável do cliente sem alterar a estrutura
    de retorno utilizada pelo frontend.
    """

    host = limpar_host(url)
    partes = host.split(".")

    # spa.pge.mt.gov.br -> PGE MT
    if len(partes) >= 3 and partes[0] == "spa" and partes[1] == "pge":
        return f"PGE {partes[2].upper()}"

    # spa.pgm.cuiaba.mt.gov.br -> PGM Cuiabá
    if len(partes) >= 3 and partes[0] == "spa" and partes[1] == "pgm":
        return f"PGM {partes[2].capitalize()}"

    # pgm.vitoria.spa.coreplan.com.br -> PGM Vitória
    if len(partes) >= 2 and partes[0] == "pgm":
        return f"PGM {partes[1].capitalize()}"

    # dividaativa.pge.es.gov.br -> Dívida Ativa ES
    if len(partes) >= 3 and partes[0] == "dividaativa":
        return f"Dívida Ativa {partes[2].upper()}"

    # viana.spa.coreplan.com.br -> Viana
    return partes[0].capitalize()

def verificar_certificado(url):

    host = limpar_host(url)

    contexto = ssl.create_default_context()

    with socket.create_connection((host, 443)) as sock:

        with contexto.wrap_socket(
            sock,
            server_hostname=host
        ) as ssock:

            cert = ssock.getpeercert()

            validade = datetime.strptime(
                cert['notAfter'],
                "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=UTC)

            dias_restantes = (
                validade - datetime.now(UTC)
            ).days

            return {
                "host": host,
                "cliente": obter_nome_cliente(url),
                "validade": validade,
                "dias_restantes": dias_restantes
            }


sites = [
    "https://viana.spa.coreplan.com.br/",
    "https://saj.spa.coreplan.com.br/",
    "https://santaluzia.spa.coreplan.com.br/",
    "https://aracruz.spa.coreplan.com.br/",
    "https://cariacica.spa.coreplan.com.br/",
    "https://maceio.spa.coreplan.com.br/",
    "https://amargosa.spa.coreplan.com.br/",
    "https://ilheus.spa.coreplan.com.br/",
    "https://spa.pg.df.gov.br/users/sign_in",
    "https://tiangua.licenciamento.coreplan.com.br/",
    "https://aquiraz.spa.coreplan.com.br/",
    "https://teresina.spa.coreplan.com.br/",
    "https://tiangua.spa.coreplan.com.br/",
    "https://cni.spa.coreplan.com.br/",
    "https://dividaativa.pge.es.gov.br/",
    "https://pgm.vitoria.spa.coreplan.com.br/",
    "https://linhares.spa.coreplan.com.br/",
    "https://ipam.spa.coreplan.com.br/",
    "https://spa.pge.ma.gov.br/",
    "https://saoluis.spa.coreplan.com.br/",
    "https://araruama.spa.coreplan.com.br/",
    "https://goytacazes.spa.coreplan.com.br/",
    "https://niteroi.spa.coreplan.com.br/",
    "https://spa.pge.mt.gov.br/",
    "https://curvelo.spa.coreplan.com.br/",
    "https://joaopessoa.spa.coreplan.com.br/",
    "http://juizdefora.spa.coreplan.com.br/",
    "https://lucasdorioverde.spa.coreplan.com.br/",
    "https://cagepa.spa.coreplan.com.br/",
    "https://pbprev.spa.coreplan.com.br/",
    "https://uepb.spa.coreplan.com.br/",
    "https://paraiba.spa.coreplan.com.br/",
    "https://camaragibe.spa.coreplan.com.br/",
    "https://jaboatao.spa.coreplan.com.br/",
    "https://spa.pgm.cuiaba.mt.gov.br/",
    "https://cachoeiro.spa.coreplan.com.br/",
    "https://ouropreto.spa.coreplan.com.br/"

]

for site in sites:

    try:

        resultado = verificar_certificado(site)

        print(
            f"""
=================================
Site: {resultado['host']}
Validade: {resultado['validade'].strftime('%d/%m/%Y')}
Dias restantes: {resultado['dias_restantes']}
"""
        )

    except Exception as e:

        print(f"Erro ao verificar {site}")
        print(str(e))
