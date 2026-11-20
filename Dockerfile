# Imagem do backend Cripto-ETFs para Railway (e qualquer plataforma com Docker).
FROM python:3.11-slim

# Dependências de sistema mínimas para compilar psycopg2 e para o curl_cffi
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_app.py .

# Railway injeta a variável PORT; o gunicorn escuta nela.
ENV PORT=8080
EXPOSE 8080

CMD gunicorn backend_app:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 60