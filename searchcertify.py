import socket
import ssl

from urllib.parse import urlparse
from datetime import datetime, UTC


def limpar_host(url):

    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)

    return parsed.hostname


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
                "validade": validade,
                "dias_restantes": dias_restantes
            }


sites = [
    "https://viana.spa.coreplan.com.br/",
    "https://saj.spa.coreplan.com.br/",
    "https://santaluzia.spa.coreplan.com.br/",
    "https://aracruz.spa.coreplan.com.br/",
    "https://cariacica.spa.coreplan.com.br/"
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
    "https://portovelho.spa.coreplan.com.br/",
    "https://sergipe.spa.coreplan.com.br/"
    "https://aracatuba.spa.coreplan.com.br/",
    "https://spa.pge.mt.gov.br/"


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