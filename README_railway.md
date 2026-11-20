# Deploy no Railway — backend NAVs (fundos Caixa)

Railway não lê `render.yaml`, por isso usamos uma imagem Docker. Estes são os
ficheiros que precisas de ter no repositório GitHub:

| Ficheiro | Papel |
|---|---|
| `backend_app.py` | A aplicação Flask |
| `requirements.txt` | Dependências Python |
| `Dockerfile` | Imagem Docker do serviço |
| `railway.json` | Configuração do Railway (build + healthcheck) |

## Passo a passo

1. **Repo no GitHub** — cria um repositório e faz upload dos quatro ficheiros acima
   (raiz do repo, não em subpastas).

2. **Cria o projeto no Railway** — em [railway.app](https://railway.app) entra com o
   GitHub → **New Project → Deploy from GitHub repo** → escolhe o repositório.
   O Railway deteta o `Dockerfile` e começa a construir sozinho.

3. **Adiciona a base de dados Postgres** — dentro do projeto, **New → Database →
   PostgreSQL**. O Railway cria a base e expõe a variável `DATABASE_URL`.

4. **Liga a base ao serviço** — no serviço web, aba **Variables** → **New Variable →
   Add Reference → PostgreSQL → DATABASE_URL**. Isto faz a app gravar o histórico no
   Postgres (sem isto, usa ficheiro local efémero e perde os dados a cada deploy).

5. **Define o REFRESH_TOKEN** — na mesma aba **Variables**, cria uma variável
   `REFRESH_TOKEN` com uma password à tua escolha (ex. uma string longa aleatória).
   É a chave que protege o endpoint `/refresh`.

6. **Gera o domínio público** — aba **Settings → Networking → Generate Domain**. Ficas
   com um URL tipo `https://cripto-etfs-nav-production.up.railway.app`.

7. **Testa** — abre `SEU_URL/` no browser (deve dizer `"status":"ok"`) e depois
   `SEU_URL/caixa-arrojado/history`.

8. **Liga ao dashboard** — no `cripto-etfs_monitor.html`, na barra dos fundos, cola o
   URL do Railway e clica **Guardar URL** → **Sincronizar fundos**.

## Atualização automática (grátis)

Tal como no Render, o Railway não corre um cron free por defeito. Usa o
[cron-job.org](https://cron-job.org):

- URL: `SEU_URL/refresh?token=O_TEU_REFRESH_TOKEN`
- Agenda: dias úteis, por exemplo às **10:00** e **17:00** (fuso Europe/Lisbon).

## Notas

- O Railway tem um plano de teste gratuito com créditos iniciais; depois disso é
  pay-as-you-go (barato para este serviço pequeno). Confirma os limites atuais no site.
- Se um fundo parar de devolver dados, abre `SEU_URL/debug/<fundo>` e ajusta o
  `pair_id` no dicionário `FUNDS` do `backend_app.py`.
- O serviço expõe a porta via `$PORT`, que o Railway injeta automaticamente — está já
  tratado no `Dockerfile`.