"""Calculadora de custo de impressão 3D.

Servidor HTTP só com stdlib. Config padrão em data/config.json,
histórico de cálculos em data/historico.db (SQLite).
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA / "config.json"
DB_PATH = DATA / "historico.db"
INDEX = BASE / "static" / "index.html"

CONFIG_DEFAULT = {
    "preco_kg": 120.0,      # R$ por kg de filamento
    "tarifa_kwh": 0.85,     # R$ por kWh
    "consumo_w": 150.0,     # consumo médio da impressora em watts
    "margem": 100.0,        # % sobre o custo
}
CONFIG_KEYS = tuple(CONFIG_DEFAULT)


def load_config():
    cfg = dict(CONFIG_DEFAULT)
    if CONFIG_PATH.exists():
        try:
            cfg.update({k: float(v) for k, v in json.loads(CONFIG_PATH.read_text()).items() if k in CONFIG_KEYS})
        except (ValueError, TypeError):
            pass
    return cfg


def save_config(data):
    cfg = load_config()
    for k in CONFIG_KEYS:
        if k in data:
            cfg[k] = num(data[k], k)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_PATH)
    return cfg


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS calculos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                preco_kg REAL NOT NULL,
                gramas REAL NOT NULL,
                horas INTEGER NOT NULL,
                minutos INTEGER NOT NULL,
                tarifa_kwh REAL NOT NULL,
                consumo_w REAL NOT NULL,
                margem REAL NOT NULL,
                custo_material REAL NOT NULL,
                custo_energia REAL NOT NULL,
                custo_total REAL NOT NULL,
                lucro REAL NOT NULL,
                preco_final REAL NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 1,
                itens_placa INTEGER NOT NULL DEFAULT 1
            )""")
        cols = {r["name"] for r in con.execute("PRAGMA table_info(calculos)")}
        if "quantidade" not in cols:
            con.execute("ALTER TABLE calculos ADD COLUMN quantidade INTEGER NOT NULL DEFAULT 1")
        if "itens_placa" not in cols:
            con.execute("ALTER TABLE calculos ADD COLUMN itens_placa INTEGER NOT NULL DEFAULT 1")


class BadInput(ValueError):
    pass


def num(v, campo):
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise BadInput(f"Campo inválido: {campo}")
    if n < 0 or n != n or n == float("inf"):
        raise BadInput(f"Campo inválido: {campo}")
    return n


def quantidade(v):
    q = int(num(1 if v in (None, "") else v, "quantidade"))
    if not 1 <= q <= 100_000:
        raise BadInput("Quantidade deve ser entre 1 e 100000")
    return q


def itens_placa(v):
    n = int(num(1 if v in (None, "") else v, "itens na placa"))
    if not 1 <= n <= 1000:
        raise BadInput("Itens na placa deve ser entre 1 e 1000")
    return n


def calcular(d):
    """gramas/horas/minutos referem-se à placa inteira; custos saem por item."""
    preco_kg = num(d.get("preco_kg"), "preço do kg")
    gramas = num(d.get("gramas"), "gramas")
    horas = int(num(d.get("horas", 0), "horas"))
    minutos = int(num(d.get("minutos", 0), "minutos"))
    tarifa = num(d.get("tarifa_kwh"), "tarifa kWh")
    consumo = num(d.get("consumo_w"), "consumo W")
    margem = num(d.get("margem"), "margem")
    qtd = quantidade(d.get("quantidade"))
    itens = itens_placa(d.get("itens_placa"))

    tempo_h = (horas + minutos / 60) / itens
    gramas_un = gramas / itens
    custo_material = gramas_un / 1000 * preco_kg
    custo_energia = consumo / 1000 * tempo_h * tarifa
    custo_total = custo_material + custo_energia
    lucro = custo_total * margem / 100
    r = lambda x: round(x, 2)
    return {
        "preco_kg": preco_kg, "gramas": gramas, "horas": horas, "minutos": minutos,
        "tarifa_kwh": tarifa, "consumo_w": consumo, "margem": margem, "quantidade": qtd,
        "itens_placa": itens, "gramas_un": round(gramas_un, 3),
        "kwh": round(consumo / 1000 * tempo_h, 3),
        "custo_material": r(custo_material), "custo_energia": r(custo_energia),
        "custo_total": r(custo_total), "lucro": r(lucro),
        "preco_final": r(custo_total + lucro),
    }


COLS = ("nome", "criado_em", "preco_kg", "gramas", "horas", "minutos", "tarifa_kwh",
        "consumo_w", "margem", "custo_material", "custo_energia", "custo_total",
        "lucro", "preco_final", "quantidade", "itens_placa")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise BadInput("Payload grande demais")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise BadInput("JSON inválido")
        if not isinstance(data, dict):
            raise BadInput("JSON inválido")
        return data

    def _route(self, method):
        url = urlparse(self.path)
        p = url.path.rstrip("/") or "/"
        try:
            if method == "GET" and p == "/":
                return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            if p == "/api/config":
                if method == "GET":
                    return self._send(200, load_config())
                if method == "PUT":
                    return self._send(200, save_config(self._json()))
            if p == "/api/calcular" and method == "POST":
                return self._send(200, calcular(self._json()))
            if p == "/api/historico":
                if method == "GET":
                    q = parse_qs(url.query).get("q", [""])[0].strip()
                    with db() as con:
                        rows = con.execute(
                            "SELECT * FROM calculos WHERE nome LIKE ? ORDER BY id DESC LIMIT 500",
                            (f"%{q}%",)).fetchall()
                    return self._send(200, [dict(r) for r in rows])
                if method == "POST":
                    d = self._json()
                    nome = str(d.get("nome", "")).strip()[:200]
                    if not nome:
                        raise BadInput("Informe um nome para salvar")
                    res = calcular(d)
                    res["nome"] = nome
                    res["criado_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    with db() as con:
                        cur = con.execute(
                            f"INSERT INTO calculos ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                            [res[c] for c in COLS])
                        res["id"] = cur.lastrowid
                    return self._send(201, res)
            if p.startswith("/api/historico/") and method in ("DELETE", "PATCH"):
                try:
                    rid = int(p.rsplit("/", 1)[1])
                except ValueError:
                    raise BadInput("id inválido")
                with db() as con:
                    if method == "DELETE":
                        con.execute("DELETE FROM calculos WHERE id = ?", (rid,))
                    else:
                        q = quantidade(self._json().get("quantidade"))
                        con.execute("UPDATE calculos SET quantidade = ? WHERE id = ?", (q, rid))
                return self._send(200, {"ok": True})
            return self._send(404, {"erro": "não encontrado"})
        except BadInput as e:
            return self._send(400, {"erro": str(e)})

    def do_GET(self): self._route("GET")
    def do_POST(self): self._route("POST")
    def do_PUT(self): self._route("PUT")
    def do_DELETE(self): self._route("DELETE")
    def do_PATCH(self): self._route("PATCH")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    print(f"Rodando em http://0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
