#!/usr/bin/env python3
"""
Monitoraggio ritardi treni per TRATTE (A -> B) tramite l'API non ufficiale
di ViaggiaTreno. Pensato per girare schedulato su GitHub Actions.

Idea: tu configuri solo le coppie di stazioni (es. ROMA TERMINI -> CIAMPINO).
Lo script scopre da solo, a ogni esecuzione, quali treni REGIONALI partono da A
e passano per B, e ti avvisa su Telegram quando uno e' in ritardo oltre soglia,
peggiora, viene soppresso o rientra in orario. Niente liste di treni da mantenere.

Efficienza:
- 1 sola chiamata `partenze` per stazione di partenza: contiene gia' il ritardo live.
- la lista fermate di un treno (per sapere se passa per B) NON cambia in giornata,
  quindi viene scaricata una volta e messa in cache giornaliera in state.json.
  Dopo il primo giro restano quasi solo le chiamate `partenze`.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Rome")
except Exception:  # fallback estremo
    TZ = None

import requests

BASE = "http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno"
HEADERS = {"User-Agent": "Mozilla/5.0 (treni-monitor; +https://github.com)"}
TIMEOUT = 15

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", ROOT / "config.json"))
STATE_PATH = Path(os.environ.get("STATE_PATH", ROOT / "state.json"))

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

GIORNI = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MESI = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CAT_REGIONALI = {"REG", "RV", "RGV"}


# --------------------------------------------------------------------------- #
# HTTP + tempo
# --------------------------------------------------------------------------- #
def http_get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"[warn] richiesta fallita {url}: {e}")
        return None
    if r.status_code != 200 or not r.text.strip():
        print(f"[warn] risposta vuota/non valida {url}: HTTP {r.status_code}")
        return None
    return r


def ora_roma():
    return datetime.now(TZ) if TZ else datetime.now()


def data_vt(dt):
    """Formato data richiesto da `partenze`: 'Thu Jun 19 2026 07:00:00 GMT+0200'.
    Costruito a mano per non dipendere dalla locale del sistema."""
    off = dt.utcoffset() or timedelta(0)
    sec = int(off.total_seconds())
    segno = "+" if sec >= 0 else "-"
    hh, mm = divmod(abs(sec) // 60, 60)
    return (f"{GIORNI[dt.weekday()]} {MESI[dt.month - 1]} {dt.day:02d} "
            f"{dt.year} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} "
            f"GMT{segno}{hh:02d}{mm:02d}")


# --------------------------------------------------------------------------- #
# ViaggiaTreno
# --------------------------------------------------------------------------- #
def risolvi_stazione(nome, cache):
    """Nome stazione -> codice (S#####). Usa autocompletaStazione, con cache."""
    nome = nome.strip()
    if nome in cache:
        return cache[nome]
    r = http_get(f"{BASE}/autocompletaStazione/{requests.utils.quote(nome)}")
    if r is None:
        return None
    righe = [x for x in r.text.strip().splitlines() if "|" in x]
    if not righe:
        return None
    scelta = righe[0]
    up = nome.upper()
    for riga in righe:           # preferisci match esatto del nome
        if riga.split("|", 1)[0].strip().upper() == up:
            scelta = riga
            break
    nome_uff, codice = scelta.split("|", 1)
    cache[nome] = codice.strip()
    print(f"[info] stazione '{nome}' -> {codice.strip()} ({nome_uff.strip()})")
    return cache[nome]


def partenze(cod_stazione):
    url = f"{BASE}/partenze/{cod_stazione}/{requests.utils.quote(data_vt(ora_roma()))}"
    r = http_get(url)
    if r is None:
        return []
    try:
        return r.json()
    except ValueError:
        print(f"[warn] JSON partenze non valido per {cod_stazione}")
        return []


def fermate_treno(cod_origine, numero, ts):
    """Lista dei codici stazione (in ordine) toccati dal treno. Per la cache
    giornaliera: l'elenco fermate non cambia durante il giorno."""
    r = http_get(f"{BASE}/andamentoTreno/{cod_origine}/{numero}/{ts}")
    if r is None:
        return None
    try:
        dati = r.json()
    except ValueError:
        return None
    fermate = dati.get("fermate") or []
    return [f.get("id") for f in fermate if f.get("id")]


def is_regionale(treno):
    cat = (treno.get("categoria") or "").upper()
    desc = (treno.get("categoriaDescrizione") or "").lower()
    return cat in CAT_REGIONALI or "regional" in desc


# --------------------------------------------------------------------------- #
# Notifiche
# --------------------------------------------------------------------------- #
def signature(ritardo, soppresso, soglia, step):
    if soppresso:
        return "SOPP"
    if ritardo >= soglia:
        return f"RIT{ritardo // step}"
    return "OK"


def html_escape(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def formatta(etichetta, treno, ritardo, soppresso):
    numero = treno.get("numeroTreno", "?")
    cat = (treno.get("categoria") or "REG")
    dest = treno.get("destinazione", "?")
    part = treno.get("compOrarioPartenza", "--")
    testa = f"🚆 <b>{html_escape(etichetta)}</b>"
    riga = f"{cat} {numero} • part. {part} • dir. {html_escape(dest)}"
    if soppresso:
        stato = "❌ <b>SOPPRESSO</b>"
    else:
        stato = f"🔴 Ritardo <b>+{ritardo} min</b>"
    return f"{testa}\n{riga}\n{stato}"


def invia_telegram(testo):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": testo,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[warn] Telegram HTTP {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        print(f"[warn] invio Telegram fallito: {e}")


# --------------------------------------------------------------------------- #
# Stato
# --------------------------------------------------------------------------- #
def carica_stato():
    try:
        s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("notif", {})
    s.setdefault("fermate", {})
    s.setdefault("stazioni", {})
    return s


def salva_stato(s):
    try:
        STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError as e:
        print(f"[warn] salvataggio stato fallito: {e}")


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
def serve_tratta(treno, cod_a, cod_b, cache_fermate, oggi):
    """True se il treno passa per A e poi per B. Usa cache giornaliera."""
    numero = treno.get("numeroTreno")
    cod_orig = treno.get("codOrigine")
    ts = treno.get("dataPartenzaTreno")
    if not (numero and cod_orig and ts):
        return False
    chiave = f"{oggi}|{numero}|{cod_orig}"
    stops = cache_fermate.get(chiave)
    if stops is None:
        stops = fermate_treno(cod_orig, numero, ts)
        if stops is None:
            return False
        cache_fermate[chiave] = stops
    if cod_a not in stops or cod_b not in stops:
        return False
    return stops.index(cod_a) < stops.index(cod_b)


def main():
    if not TG_TOKEN or not TG_CHAT:
        print("[errore] TELEGRAM_TOKEN / TELEGRAM_CHAT_ID non impostati")
        sys.exit(1)

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    soglia = int(cfg.get("soglia_ritardo_min", 5))
    step = max(1, int(cfg.get("step_riavviso_min", 10)))
    finestra = int(cfg.get("finestra_minuti", 75))
    tratte = cfg.get("tratte", [])

    st = carica_stato()
    oggi = ora_roma().strftime("%Y-%m-%d")
    # pulizia cache fermate dei giorni passati
    st["fermate"] = {k: v for k, v in st["fermate"].items()
                     if k.startswith(oggi + "|")}

    now_ms = int(time.time() * 1000)
    lim_min = now_ms - 15 * 60 * 1000      # treni partiti da poco
    lim_max = now_ms + finestra * 60 * 1000  # treni in partenza a breve

    partenze_cache = {}   # cod_stazione -> lista treni (1 fetch per stazione/run)
    messaggi = []

    for tr in tratte:
        etichetta = tr.get("etichetta") or f"{tr.get('da')} → {tr.get('a')}"
        cod_a = tr.get("da_cod") or risolvi_stazione(tr["da"], st["stazioni"])
        cod_b = tr.get("a_cod") or risolvi_stazione(tr["a"], st["stazioni"])
        if not cod_a or not cod_b:
            print(f"[warn] tratta '{etichetta}': stazione non risolta, salto")
            continue

        if cod_a not in partenze_cache:
            partenze_cache[cod_a] = partenze(cod_a)
        treni = partenze_cache[cod_a]

        for t in treni:
            if not is_regionale(t):
                continue
            op = t.get("orarioPartenza")  # ts ms partenza programmata
            if isinstance(op, int) and not (lim_min <= op <= lim_max):
                continue
            if not serve_tratta(t, cod_a, cod_b, st["fermate"], oggi):
                continue

            ritardo = int(t.get("ritardo") or 0)
            soppresso = t.get("provvedimento") == 1
            sig = signature(ritardo, soppresso, soglia, step)

            chiave = f"{etichetta}|{t.get('numeroTreno')}"
            prec = st["notif"].get(chiave, {}).get("sig")
            if sig == prec:
                continue

            if sig == "OK":
                if prec and prec != "OK":
                    messaggi.append(
                        f"✅ <b>{html_escape(etichetta)}</b> — "
                        f"treno {t.get('numeroTreno')} di nuovo in orario."
                    )
            else:
                messaggi.append(formatta(etichetta, t, ritardo, soppresso))

            st["notif"][chiave] = {"sig": sig, "ts": int(time.time())}

    salva_stato(st)

    if messaggi:
        invia_telegram("\n\n———\n\n".join(messaggi))
        print(f"[ok] inviati {len(messaggi)} aggiornamenti")
    else:
        print("[ok] nessuna novita'")


if __name__ == "__main__":
    main()
