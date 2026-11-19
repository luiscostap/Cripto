# Backend alojado — NAVs dos fundos Caixa

Este backend serve as cotas (NAV) dos fundos **Caixa Seleção Global Arrojado** e
**Caixa Ações Líderes Globais**, fazendo scraping ao investing.com e guardando o
histórico numa base de dados Postgres. O dashboard HTML passa a pedir os dados a
este serviço, deixando de precisar do `.bat` e da importação manual.

## Ficheiros

| Ficheiro | Papel |
|---|---|
| `backend_app.py` | A aplicação Flask (rotas `/`, `/<fundo>/nav`, `/<fundo>/history`, `/refresh`, `/debug/<fundo>`) |
| `requirements.txt` | Dependências Python |
| `render.yaml` | Blueprint do Render (web service + Postgres + cron) |
| `README_backend.md` | Este guia |

## Endpoints

```
GET  /                          health check
GET  /funds                     lista de fundos
GET  /caixa-arrojado/nav        {"nav":..., "date":..., "change_pct":...}
GET  /caixa-arrojado/history    {"history":[{"date":...,"nav":...}, ...]}
GET  /caixa-lideres/nav
GET  /caixa-lideres/history
GET  /refresh?token=SEGREDO     força atualização de todos os fundos
GET  /debug/<fundo>             diagnóstico do scraping (útil se um fundo falhar)
```

## Deploy no Render (passo a passo)

1. **Põe os ficheiros num repositório GitHub.** Cria um repo novo (pode ser público ou
   privado) e faz upload de `backend_app.py`, `requirements.txt` e `render.yaml`.
2. No [Render](https://render.com), entra e escolhe **New → Blueprint**.
3. Liga a tua conta GitHub e seleciona o repositório. O Render lê o `render.yaml` e
   propõe criar:
   - o **web service** `cripto-etfs-nav` (plano free),
   - a **base de dados** `cripto-etfs-db` (Postgres free),
   - o **cron job** de atualização.
4. Clica **Apply**. Espera o primeiro build (2-4 min). No fim terás um URL do tipo
   `https://cripto-etfs-nav.onrender.com`.
5. Abre esse URL no browser — deves ver `{"status":"ok",...}`.
6. Testa os fundos: `https://cripto-etfs-nav.onrender.com/caixa-arrojado/history`

### Variáveis de ambiente

O `render.yaml` já cria a `DATABASE_URL` (ligada ao Postgres) e gera um `REFRESH_TOKEN`
aleatório. Para o consultares: no dashboard do Render → o serviço → separador
**Environment**. Guarda o token, vais precisar dele para o `/refresh`.

## Atualização automática — importante

O **cron job do Render é um serviço pago** (plano starter, ~7 USD/mês). Se quiseres manter
tudo a custo zero, tens duas opções gratuitas:

**Opção A — cron-job.org (recomendada, grátis):**
1. Cria conta em [cron-job.org](https://cron-job.org).
2. Cria um cron job com URL:
   `https://cripto-etfs-nav.onrender.com/refresh?token=O_TEU_TOKEN`
3. Agenda para dias úteis, por exemplo às **10:00** e às **17:00** (fuso Europe/Lisbon).
   O investing.com publica as cotas uma vez por dia, por isso 1-2 execuções chegam.

**Opção B — sem cron:** abre tu o URL `/refresh?token=...` uma vez por dia, ou deixa o
dashboard chamar `/refresh` sozinho ao carregar (já está preparado para isso).

## Limitação do plano free do Render

O web service free **adormece após ~15 min sem tráfego**; o primeiro pedido a seguir
demora ~30-60 s (cold start). O cron-job.org a chamar o serviço de hora a hora também
serve para o manter acordado.

## Como o scraping funciona (e como resolver se falhar)

O backend usa o endpoint JSON interno do investing.com:

```
https://api.investing.com/api/financialdata/historical/<pair_id>?...
```

com `curl_cffi` a imitar o TLS de um Chrome real (aumenta muito a taxa de sucesso contra
o anti-bot). O `pair_id` de cada fundo está no topo do `backend_app.py` em `FUNDS`.

**Se um fundo parar de devolver dados**, o investing.com pode ter mudado o ID do
instrumento. Para descobrir o novo:
1. Abre a página do fundo no investing.com.
2. Vê o URL / o código-fonte à procura do `pairId`.
3. Substitui no dicionário `FUNDS` e faz novo deploy.

O endpoint `/debug/<fundo>` mostra o que o scraping devolveu e ajuda a diagnosticar.

## Desenvolvimento local

```bash
pip install -r requirements.txt
python backend_app.py          # corre em http://localhost:8080
```

Sem `DATABASE_URL` definido, guarda os dados em `nav_store.json` (útil para testar).