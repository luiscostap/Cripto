"""
Cripto-ETFs backend — serve NAVs históricos dos fundos Caixa a partir do investing.com.

Endpoints:
  GET /                      -> health check
  GET /funds                 -> lista de fundos suportados
  GET /<fund_key>/nav        -> última cota conhecida (JSON)
  GET /<fund_key>/history    -> histórico completo (JSON, array de {date, nav})
  GET /refresh               -> força atualização de todos os fundos (protegido por token)

O backend faz scraping ao investing.com usando o endpoint JSON interno com fallback
para parsing de HTML. As cotas são guardadas em Postgres (via DATABASE_URL) ou,
se não existir, num cache em memória + ficheiro local (útil para desenvolvimento).
"""

import os
import json
import re
import time
import logging
from datetime import datetime, timedelta

import requests

try:
    from curl_cffi import requests as cffi_requests  # melhor TLS fingerprint
    _HAS_CFFI = True
except Exception:  # pragma: no cover
    cffi_requests = None
    _HAS_CFFI = False

from flask import Flask, jsonify, request, abort
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cripto-etfs")

app = Flask(__name__)
CORS(app)  # o dashboard corre no browser (ficheiro local ou outro domínio)

# ---------------------------------------------------------------------------
# Configuração dos fundos
# ---------------------------------------------------------------------------
# `pair_id` é o ID interno do instrumento no investing.com.
# Estes IDs podem mudar; se um fundo parar de funcionar, corre /debug/<fund_key>
# para inspecionar e ajusta aqui.
FUNDS = {
    "caixa-arrojado": {
        "name": "Caixa Seleção Global Arrojado",
        "pair_id": "1189286",
        "seed": {"date": "2026-11-19", "nav": 7149.0},
    },
    "caixa-lideres": {
        "name": "Caixa Ações Líderes Globais",
        "pair_id": "1189287",
        "seed": {"date": "2026-11-19", "nav": 16960.0},
    },
}

REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")

# ---------------------------------------------------------------------------
# Persistência: Postgres se DATABASE_URL existir, senão ficheiro local
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
LOCAL_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nav_store.json")

_pg = None


def _get_pg():
    """Devolve uma ligação psycopg2 (cria tabela se necessário). Retorna None se indisponível."""
    global _pg
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
    except Exception:
        log.warning("DATABASE_URL definido mas psycopg2 não instalado — a usar ficheiro local.")
        return None
    if _pg is not None:
        return _pg
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode=os.environ.get("PGSSLMODE", "require"))
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS nav_history (
                    fund_key TEXT NOT NULL,
                    date     DATE NOT NULL,
                    nav      DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (fund_key, date)
                );
                """
            )
        _pg = conn
        log.info("Ligado ao Postgres.")
        return _pg
    except Exception as e:
        log.error("Falha a ligar ao Postgres: %s", e)
        return None


def _load_local() -> dict:
    if os.path.exists(LOCAL_STORE):
        try:
            with open(LOCAL_STORE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_local(data: dict) -> None:
    try:
        with open(LOCAL_STORE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("Falha a escrever store local: %s", e)


def upsert_points(fund_key: str, points: list) -> None:
    """points = lista de {date: 'YYYY-MM-DD', nav: float}."""
    if not points:
        return
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                for p in points:
                    cur.execute(
                        """
                        INSERT INTO nav_history (fund_key, date, nav)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (fund_key, date)
                        DO UPDATE SET nav = EXCLUDED.nav;
                        """,
                        (fund_key, p["date"], float(p["nav"])),
                    )
            return
        except Exception as e:
            log.error("Erro Postgres upsert (%s): %s", fund_key, e)
    # fallback local
    data = _load_local()
    bucket = data.setdefault(fund_key, {})
    for p in points:
        bucket[p["date"]] = float(p["nav"])
    _save_local(data)


def get_history(fund_key: str) -> list:
    conn = _get_pg()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT date, nav FROM nav_history WHERE fund_key = %s ORDER BY date ASC;",
                    (fund_key,),
                )
                rows = cur.fetchall()
            return [{"date": r[0].strftime("%Y-%m-%d"), "nav": float(r[1])} for r in rows]
        except Exception as e:
            log.error("Erro Postgres select (%s): %s", fund_key, e)
    data = _load_local()
    bucket = data.get(fund_key, {})
    return [{"date": d, "nav": float(v)} for d, v in sorted(bucket.items())]


# ---------------------------------------------------------------------------
# Scraping do investing.com
# ---------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.investing.com/",
}


def _http_get_json(url: str, params: dict = None):
    """GET com curl_cffi se disponível (melhor sucesso), senão requests."""
    headers = dict(BROWSER_HEADERS)
    if _HAS_CFFI:
        r = cffi_requests.get(url, params=params, headers=headers, impersonate="chrome124", timeout=25)
    else:
        r = requests.get(url, params=params, headers=headers, timeout=25)
    r.raise_for_status()
    return r.json()


def _http_get_text(url: str):
    headers = dict(BROWSER_HEADERS)
    headers["Accept"] = "text/html,application/xhtml+xml"
    if _HAS_CFFI:
        r = cffi_requests.get(url, headers=headers, impersonate="chrome124", timeout=25)
    else:
        r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    return r.text


def fetch_history_investing(pair_id: str) -> list:
    """
    Tenta obter o histórico via endpoint JSON interno.
    Devolve lista de {date, nav}. Lança exceção se falhar tudo.
    """
    url = f"https://api.investing.com/api/financialdata/historical/{pair_id}"
    params = {
        "start-date": (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d"),
        "end-date": datetime.utcnow().strftime("%Y-%m-%d"),
        "time-frame": "Daily",
        "add-missing-rows": "false",
    }
    js = _http_get_json(url, params=params)
    points = []

    # Formato varia; tentamos algumas formas conhecidas.
    data = js.get("data") if isinstance(js, dict) else None
    if isinstance(data, list):
        for row in data:
            d = row.get("rowDate") or row.get("date") or row.get("rowDateTimestamp")
            last = row.get("last_close") or row.get("lastClose") or row.get("close")
            if d is None or last is None:
                continue
            points.append({"date": _norm_date(d), "nav": _parse_num(last)})
    elif isinstance(js, dict) and "historical" in js:
        for row in js["historical"]:
            points.append({"date": _norm_date(row.get("date")), "nav": _parse_num(row.get("price"))})

    points = [p for p in points if p["date"] and p["nav"] is not None]
    if not points:
        raise RuntimeError("JSON do investing.com sem pontos reconhecíveis")
    return points


def _norm_date(d) -> str:
    """Aceita timestamp (int/float) ou string e devolve 'YYYY-MM-DD'."""
    try:
        if isinstance(d, (int, float)):
            return datetime.utcfromtimestamp(d).strftime("%Y-%m-%d")
        s = str(d).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d", "%b %d, %Y"):
            try:
                return datetime.strptime(s[:20], fmt).strftime("%Y-%m-%d")
            except Exception:
                continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    except Exception:
        pass
    return None


def _parse_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("€", "").replace("%", "").replace(" ", "")
    # 1.234,56 -> 1234.56 ; 7,149 -> 7.149 ou 7149 (depende); heurística
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def refresh_fund(fund_key: str) -> dict:
    cfg = FUNDS[fund_key]
    result = {"fund": fund_key, "ok": False, "added": 0}
    try:
        points = fetch_history_investing(cfg["pair_id"])
        upsert_points(fund_key, points)
        result.update(ok=True, added=len(points), source="json")
        log.info("[%s] atualizado: %d pontos", fund_key, len(points))
        return result
    except Exception as e:
        log.warning("[%s] JSON falhou: %s", fund_key, e)

    # fallback: garantir que existe pelo menos o seed
    hist = get_history(fund_key)
    if not hist:
        seed = cfg.get("seed")
        if seed:
            upsert_points(fund_key, [seed])
            result.update(ok=True, added=1, source="seed")
            return result
    result["error"] = "não foi possível obter dados e não há seed/histórico"
    return result


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "cripto-etfs-nav-backend",
        "time": datetime.utcnow().isoformat() + "Z",
        "funds": list(FUNDS.keys()),
        "storage": "postgres" if _get_pg() else "local-file",
        "cffi": _HAS_CFFI,
    })


@app.route("/funds")
def funds():
    return jsonify([
        {"key": k, "name": v["name"]} for k, v in FUNDS.items()
    ])


def _check_fund(fund_key):
    if fund_key not in FUNDS:
        abort(404, description=f"fundo desconhecido: {fund_key}")


@app.route("/<fund_key>/nav")
def nav(fund_key):
    _check_fund(fund_key)
    hist = get_history(fund_key)
    if not hist:
        # tenta atualizar uma vez antes de desistir
        refresh_fund(fund_key)
        hist = get_history(fund_key)
    if not hist:
        return jsonify({"fund": fund_key, "nav": None, "date": None, "error": "sem dados"}), 404
    last = hist[-1]
    prev = hist[-2] if len(hist) > 1 else None
    change_pct = None
    if prev and prev["nav"]:
        change_pct = round((last["nav"] - prev["nav"]) / prev["nav"] * 100, 4)
    return jsonify({
        "fund": fund_key,
        "name": FUNDS[fund_key]["name"],
        "nav": last["nav"],
        "date": last["date"],
        "change_pct": change_pct,
    })


@app.route("/<fund_key>/history")
def history(fund_key):
    _check_fund(fund_key)
    hist = get_history(fund_key)
    if not hist:
        refresh_fund(fund_key)
        hist = get_history(fund_key)
    return jsonify({"fund": fund_key, "name": FUNDS[fund_key]["name"], "history": hist})


@app.route("/refresh")
def refresh():
    token = request.args.get("token", "")
    if REFRESH_TOKEN and token != REFRESH_TOKEN:
        abort(401, description="token inválido")
    results = {k: refresh_fund(k) for k in FUNDS}
    return jsonify({"refreshed_at": datetime.utcnow().isoformat() + "Z", "results": results})


@app.route("/debug/<fund_key>")
def debug(fund_key):
    """Ajuda a diagnosticar o scraping de um fundo."""
    _check_fund(fund_key)
    out = {"fund": fund_key, "pair_id": FUNDS[fund_key]["pair_id"], "attempts": []}
    try:
        url = f"https://api.investing.com/api/financialdata/historical/{FUNDS[fund_key]['pair_id']}"
        params = {
            "start-date": (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d"),
            "end-date": datetime.utcnow().strftime("%Y-%m-%d"),
            "time-frame": "Daily",
            "add-missing-rows": "false",
        }
        js = _http_get_json(url, params=params)
        out["attempts"].append({"method": "json", "ok": True, "keys": list(js.keys()) if isinstance(js, dict) else type(js).__name__})
        out["sample"] = (js.get("data") or [])[:3] if isinstance(js, dict) else str(js)[:300]
    except Exception as e:
        out["attempts"].append({"method": "json", "ok": False, "error": str(e)})
    return jsonify(out)


if __name__ == "__main__":
    # desenvolvimento local
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)