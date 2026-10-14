# 🚀 SPA VISION

> Plataforma de monitoramento, observabilidade e diagnóstico preventivo do ecossistema SPA.

---

## 📌 Sobre o projeto

O **SPA VISION** é uma solução desenvolvida para centralizar o monitoramento dos ambientes do **SPA (Sistema de Processos Automatizados)**.

O objetivo é disponibilizar uma visão única sobre a saúde das aplicações, integrações e infraestrutura dos clientes, permitindo identificar falhas de forma antecipada e reduzir a necessidade de verificações manuais.

A plataforma combina indicadores funcionais e técnicos, permitindo acompanhar desde problemas no processamento de expedientes até o consumo de recursos das máquinas virtuais.

---

## 🎯 Objetivos

O SPA VISION tem como principais objetivos:

- Monitorar a saúde dos ambientes SPA.
- Identificar expedientes não cadastrados.
- Detectar falhas de integração com o PJe.
- Identificar problemas relacionados a credenciais de integração.
- Monitorar certificados digitais.
- Identificar processos em branco.
- Monitorar consumo de CPU, memória e disco das VMs.
- Monitorar containers Docker.
- Identificar problemas no Nginx.
- Identificar indisponibilidade do Frontend.
- Centralizar informações de diversos clientes.
- Gerar alertas automaticamente.
- Enviar notificações por e-mail.
- Manter histórico dos incidentes.
- Permitir atuação preventiva da equipe de suporte e infraestrutura.

---

# 🏗️ Arquitetura

O SPA VISION é estruturado em três principais áreas de observabilidade:

```text
                    ┌──────────────────────┐
                    │      SPA VISION      │
                    │      Dashboard       │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
     APLICAÇÃO             INTEGRAÇÕES         INFRAESTRUTURA
          │                    │                    │
          ├─ Expedientes       ├─ PJe               ├─ VM
          ├─ Processos         ├─ Credenciais       ├─ CPU
          ├─ Processos         ├─ Certificados      ├─ Memória
          │  em branco         └─ Tribunais         ├─ Disco
          └─ Frontend                               ├─ Docker
                                                    └─ Nginx
```

---

# 🔎 Monitoramentos

## 1. Expedientes não cadastrados

Identifica expedientes que deveriam ter sido cadastrados no SPA, mas que não foram processados corretamente.

### Informações monitoradas

- Cliente.
- Expediente.
- Data de recebimento.
- Origem.
- Data da tentativa de cadastro.
- Status.
- Mensagem de erro.
- Tempo de processamento.

### Status

```text
NORMAL
ATENÇÃO
ALERTA
CRÍTICO
```

---

## 2. Integração com PJe

Monitora falhas relacionadas à integração entre o SPA e o PJe.

### Exemplos

- Usuário ou senha inválidos.
- Falha de autenticação.
- Credencial expirada.
- Acesso recusado.
- Falha de comunicação.
- Erro durante a execução da integração.

### Informações

```text
Cliente
Tribunal
Integração
Usuário
Última tentativa
Quantidade de falhas
Último erro
Status
```

---

## 3. Certificados digitais

O SPA VISION monitora os certificados utilizados pelos ambientes e integrações.

### Classificação sugerida

| Dias restantes | Status |
|---:|---|
| > 30 | 🟢 Normal |
| 16–30 | 🟡 Atenção |
| 8–15 | 🟠 Alerta |
| 1–7 | 🔴 Crítico |
| 0 / expirado | 🔴 Indisponível |

### Informações

- Cliente.
- Certificado.
- Serviço relacionado.
- Data de emissão.
- Data de expiração.
- Dias restantes.
- Status.

---

## 4. Processos em branco

Identifica processos que foram cadastrados ou acessados, mas apresentam informações incompletas ou ausentes.

### Exemplos

- Processo sem número.
- Processo sem dados processuais.
- Processo sem movimentações.
- Processo sem partes.
- Processo sem expediente.
- Falha de carregamento.
- Dados não persistidos.

---

# 🖥️ Monitoramento de infraestrutura

## CPU

Monitoramento do consumo de processamento da VM.

### Faixas sugeridas

| Uso | Status |
|---:|---|
| 0–70% | 🟢 Normal |
| 71–80% | 🟡 Atenção |
| 81–90% | 🟠 Alerta |
| > 90% | 🔴 Crítico |

---

## Memória

Monitoramento da utilização da memória RAM.

São considerados:

- Memória total.
- Memória utilizada.
- Memória disponível.
- Percentual utilizado.
- Swap.

---

## Disco

Monitoramento do espaço disponível na VM.

São considerados:

- Espaço total.
- Espaço utilizado.
- Espaço disponível.
- Percentual utilizado.
- Crescimento do armazenamento.

O monitoramento permite identificar antecipadamente problemas relacionados a:

- Logs.
- Containers.
- Backups.
- Arquivos temporários.
- Banco de dados.
- Arquivos da aplicação.

---

# 🐳 Docker

Nos ambientes que utilizam Docker, o SPA VISION poderá acompanhar:

```text
Container
Status
CPU
Memória
Uptime
Restart Count
Healthcheck
```

Exemplo:

```text
spa_front     RUNNING     CPU: 32%    RAM: 1.8 GB
spa_main      RUNNING     CPU: 45%    RAM: 2.4 GB
redis         RUNNING     CPU: 05%    RAM: 800 MB
```

O aumento anormal do número de reinicializações pode indicar problemas de:

- Memória.
- Aplicação.
- Configuração.
- Dependências.
- Falha de processo.

---

# 🌐 Nginx

## Próxima evolução

O SPA VISION deverá monitorar o Nginx dos clientes.

### Eventos monitorados

- HTTP 502.
- HTTP 503.
- HTTP 504.
- Upstream indisponível.
- Connection refused.
- Timeout.
- Nginx parado.
- Problemas de certificado HTTPS.
- Problemas de DNS.

### Exemplo

```text
Cliente: PGE-MA

Nginx
Status: CRÍTICO

HTTP: 502 Bad Gateway

Upstream:
10.0.1.134:3000

Erro:
Connection refused
```

---

# 🖥️ Frontend

O monitoramento deverá validar não apenas se a VM está online, mas se o **Frontend do SPA está efetivamente disponível**.

### Camadas de validação

```text
VM
 ↓
Docker
 ↓
Container
 ↓
Nginx
 ↓
Frontend
 ↓
Aplicação SPA
```

Isso permite diferenciar:

```text
VM ONLINE
Nginx ONLINE
Frontend OFFLINE
```

de:

```text
VM OFFLINE
```

---

# ❤️ Health Check

A aplicação poderá disponibilizar endpoints para validação da saúde do serviço.

Exemplos:

```http
GET /health
GET /healthcheck
GET /up
```

Resposta esperada:

```json
{
  "status": "ok",
  "application": "spa"
}
```

Caso o serviço não responda corretamente, o SPA VISION deverá registrar a ocorrência.

---

# 🔔 Sistema de alertas

O SPA VISION deverá possuir um mecanismo de alertas capaz de detectar alterações nos indicadores monitorados.

### Eventos

- Expediente não cadastrado.
- Erro de integração PJe.
- Certificado próximo da expiração.
- Certificado expirado.
- Processo em branco.
- CPU elevada.
- Memória elevada.
- Disco cheio.
- Container reiniciando.
- Nginx com erro.
- Frontend indisponível.
- VM indisponível.

---

# 📧 Alertas por e-mail

Os eventos críticos poderão gerar notificações automáticas.

Exemplo:

```text
[SPA VISION][CRÍTICO] Frontend indisponível — PGE-MA

Cliente:
PGE-MA

Problema:
Frontend do SPA indisponível.

Status:
HTTP 502 Bad Gateway

Horário:
14/10/2026 10:35

Nginx:
Ativo

Upstream:
10.0.1.134:3000

Ação recomendada:
Verificar container/frontend e logs do Nginx.
```

---

# 🔄 Alertas de recuperação

Além da detecção, o sistema deverá identificar quando o problema for resolvido.

Exemplo:

```text
[SPA VISION][RECUPERADO] Frontend — PGE-MA

O serviço voltou a responder normalmente.

Início:
10:35

Recuperação:
10:48

Duração:
13 minutos
```

---

# 🚦 Severidade

Os eventos serão classificados de acordo com seu impacto.

| Severidade | Descrição |
|---|---|
| 🟢 INFO | Informação operacional |
| 🟡 ATENÇÃO | Condição que pode evoluir para problema |
| 🟠 ALERTA | Degradação que necessita acompanhamento |
| 🔴 CRÍTICO | Indisponibilidade ou risco elevado |

---

# 🖥️ Dashboard

O dashboard principal deverá apresentar uma visão consolidada dos clientes.

Exemplo:

```text
╔══════════════════════════════════════════╗
║              SPA VISION                  ║
╠══════════════════════════════════════════╣
║ Clientes monitorados              42     ║
║                                          ║
║ Expedientes não cadastrados       12     ║
║ Integrações com erro               03     ║
║ Certificados em alerta             05     ║
║ Processos em branco                07     ║
║ VMs em estado crítico               02     ║
╚══════════════════════════════════════════╝
```

---

# 🏢 Visão por cliente

Cada cliente deverá possuir uma visão individualizada.

```text
PGE-MA

STATUS GERAL: ATENÇÃO

Integrações
├── PJe ................ OK
├── Certificado ........ 12 dias
└── Autenticação ....... OK

SPA
├── Expedientes ........ 02 pendentes
├── Processos .......... OK
└── Processos em branco  01

Infraestrutura
├── CPU ................. 74%
├── Memória ............. 79%
├── Disco ............... 68%
└── Containers .......... OK
```

---

# 📊 Histórico

O SPA VISION deverá armazenar o histórico dos indicadores.

Isso permitirá identificar tendências e responder perguntas como:

- Quando o problema começou?
- O problema é recorrente?
- O consumo está aumentando?
- Houve alteração após uma atualização?
- Qual cliente apresenta maior quantidade de incidentes?
- Qual integração apresenta maior quantidade de falhas?

---

# 🚨 Gestão de incidentes

Cada problema identificado poderá receber um identificador único.

Exemplo:

```text
INC-2026-000152

Cliente: PGE-MA
Tipo: Nginx
Severidade: CRÍTICO
Status: Em andamento

Início:
14/10/2026 10:35

Última verificação:
14/10/2026 10:42
```

Após a recuperação:

```text
INC-2026-000152

Status: RESOLVIDO

Início: 10:35
Fim: 10:48
Duração: 13 minutos
```

---

# 📈 Indicadores futuros

Com o armazenamento histórico, poderão ser disponibilizados indicadores como:

- Disponibilidade por cliente.
- Quantidade de incidentes.
- Quantidade de alertas.
- Tempo médio de resolução.
- Clientes com maior número de ocorrências.
- Integrações com maior quantidade de falhas.
- Certificados próximos da expiração.
- VMs com maior consumo.
- Quantidade de indisponibilidades do Frontend.
- Quantidade de erros HTTP 5xx.
- Histórico de CPU.
- Histórico de memória.
- Histórico de disco.

---

# 🗺️ Roadmap

## Fase 1 — Monitoramento funcional

- [x] Expedientes não cadastrados
- [x] Erros de integração
- [x] Monitoramento de certificados
- [x] Processos em branco

## Fase 2 — Infraestrutura

- [x] CPU
- [x] Memória
- [x] Disco
- [ ] Docker avançado
- [ ] Healthcheck dos containers

## Fase 3 — Disponibilidade

- [ ] Monitoramento Nginx
- [ ] Identificação de HTTP 502
- [ ] Identificação de HTTP 503
- [ ] Identificação de HTTP 504
- [ ] Monitoramento do Frontend
- [ ] Health Check da aplicação
- [ ] Monitoramento de VM indisponível

## Fase 4 — Alertas

- [ ] Motor de alertas
- [ ] Alertas por e-mail
- [ ] Alertas de recuperação
- [ ] Controle de recorrência
- [ ] Classificação por severidade
- [ ] Histórico de incidentes

## Fase 5 — Observabilidade

- [ ] Dashboard histórico
- [ ] Métricas de disponibilidade
- [ ] Indicadores por cliente
- [ ] SLA
- [ ] MTTR
- [ ] Relatórios operacionais
- [ ] Análise de tendências

---

# 🧩 Modelo de monitoramento

Cada monitor deverá seguir um fluxo semelhante:

```text
┌──────────────────────┐
│      Coletar dado    │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│      Validar dado    │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│  Classificar status  │
└──────────┬───────────┘
           ↓
      ┌────┴────┐
      ↓         ↓
    NORMAL   PROBLEMA
                │
                ↓
        ┌───────────────┐
        │ Gerar alerta  │
        └───────┬───────┘
                ↓
        ┌───────────────┐
        │ Notificar     │
        └───────┬───────┘
                ↓
        ┌───────────────┐
        │ Acompanhar    │
        │ recuperação   │
        └───────────────┘
```

---

# 🔐 Segurança

O SPA VISION deverá considerar os seguintes requisitos:

- Não armazenar senhas em texto puro.
- Proteger credenciais de integrações.
- Utilizar variáveis de ambiente para secrets.
- Restringir acesso ao dashboard.
- Utilizar HTTPS.
- Implementar autenticação.
- Registrar acessos administrativos.
- Restringir endpoints de monitoramento.
- Evitar exposição de informações sensíveis nos logs.
- Utilizar princípio de menor privilégio.

---

# 📝 Estrutura sugerida de um evento

```json
{
  "client": "PGE-MA",
  "monitor": "nginx",
  "status": "critical",
  "severity": "critical",
  "message": "502 Bad Gateway",
  "detected_at": "2026-10-14T10:35:00",
  "metadata": {
    "http_status": 502,
    "upstream": "10.0.1.134:3000"
  }
}
```

---

# 🎯 Resultado esperado

O SPA VISION deverá evoluir para uma plataforma capaz de responder rapidamente:

> **"Como está a saúde de todos os ambientes SPA neste momento?"**

E, principalmente:

> **"Existe algum problema acontecendo ou prestes a acontecer que precisa da nossa atenção?"**

A proposta é transformar o monitoramento em uma operação **proativa, centralizada e orientada a indicadores**, reduzindo o tempo de identificação e diagnóstico dos problemas e aumentando a disponibilidade dos ambientes SPA.

---

## 🚀 SPA VISION

**Monitorar → Detectar → Alertar → Diagnosticar → Recuperar**

> **Uma visão centralizada da saúde dos ambientes SPA.**
