#!/usr/bin/env python3
"""
Comando /treni da aggiungere al bot Telegram ESISTENTE (es. @CiGchatbot,
python-telegram-bot). Interroga in tempo reale l'API non ufficiale di
ViaggiaTreno e risponde con i prossimi treni REGIONALI sulle tratte
configurate: orario, direzione, binario e ritardo (anche i treni in orario).

A differenza del bot-allerta schedulato, qui la risposta e' immediata perche'
il bot e' un processo gia' sempre attivo.

>>> Aggancio del comando: vedi gli snippet in fondo al file. <<<
"""
import time
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Rome")
except Exception:
    TZ = None

import requests

BASE = "http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "http://www.viaggiatreno.it/infomobilita/",
}
TIMEOUT = 12

# --- Tratte da mostrare (codici stazione gia' noti dal bot-allerta) --------- #
TRATTE = [
    {"da": "S08701", "a": "S08409", "etichetta": "Marino → Roma Termini"},
    {"da": "S08409", "a": "S08701", "etichetta": "Roma Termini → Marino"},
    {"da": "S08409", "a": "S08650", "etichetta": "Roma Termini → Ciampino"},
    {"da": "S08409", "a": "S08730", "etichetta": "Roma Termini → Casabianca"},
]
N_TRENI = 4            # quanti treni mostrare per tratta
FINESTRA_MIN = 120     # guarda fino a N minuti in avanti
MAX_CANDIDATI = 25     # tetto di treni da ispezionare per tratta (limita le chiamate)
CAT_REGIONALI = {"REG", "RV", "RGV"}

# --- Cache in-memory (il bot e' persistente) -------------------------------- #
_cache_partenze = {}   # cod -> (epoch, lista)
_cache_fermate = {}    # "YYYYMMDD|num|orig" -> [stops]
_PARTENZE_TTL = 60     # secondi


def _ora_roma():
    return datetime.now(TZ) if TZ else datetime.now()


def _data_vt(dt):
    g = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]
    m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][dt.month - 1]
    off = dt.utcoffset() or timedelta(0)
    sec = int(off.total_seconds())
    seg = "+" if sec >= 0 else "-"
    hh, mm = divmod(abs(sec) // 60, 60)
    return (f"{g} {m} {dt.day:02d} {dt.year} "
            f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} GMT{seg}{hh:02d}{mm:02d}")


def _get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.text.strip():
        return None
    return r


def _partenze(cod):
    now = time.time()
    c = _cache_partenze.get(cod)
    if c and now - c[0] < _PARTENZE_TTL:
        return c[1]
    r = _get(f"{BASE}/partenze/{cod}/{requests.utils.quote(_data_vt(_ora_roma()))}")
    lista = []
    if r is not None:
        try:
            lista = r.json()
        except ValueError:
            lista = []
    _cache_partenze[cod] = (now, lista)
    return lista


def _fermate(num, orig, ts, oggi):
    chiave = f"{oggi}|{num}|{orig}"
    if chiave in _cache_fermate:
        return _cache_fermate[chiave]
    r = _get(f"{BASE}/andamentoTreno/{orig}/{num}/{ts}")
    stops = None
    if r is not None:
        try:
            stops = [f.get("id") for f in (r.json().get("fermate") or []) if f.get("id")]
        except ValueError:
            stops = None
    if stops is not None:
        _cache_fermate[chiave] = stops
    return stops


def _is_reg(t):
    cat = (t.get("categoria") or "").upper()
    return cat in CAT_REGIONALI or "regional" in (t.get("categoriaDescrizione") or "").lower()


def _binario(t):
    eff = (t.get("binarioEffettivoPartenzaDescrizione") or "").strip()
    prog = (t.get("binarioProgrammatoPartenzaDescrizione") or "").strip()
    if eff:
        return eff, True
    if prog:
        return prog, False
    return None, False


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _riga_treno(t):
    num = t.get("numeroTreno", "?")
    cat = t.get("categoria") or "REG"
    part = t.get("compOrarioPartenza", "--")
    dest = _esc(t.get("destinazione", "?"))
    rit = int(t.get("ritardo") or 0)
    stato = "🟢 in orario" if rit <= 0 else f"🔴 +{rit}'"
    bd, conf = _binario(t)
    binstr = ""
    if bd:
        binstr = f" • bin. {_esc(bd)}" + ("" if conf else " (prev.)")
    return f"<code>{part}</code> {cat} {num} → {dest} • {stato}{binstr}"


def tabellone():
    """Messaggio HTML con i prossimi treni regionali per ogni tratta."""
    oggi = _ora_roma().strftime("%Y%m%d")
    now_ms = int(time.time() * 1000)
    lo = now_ms - 5 * 60 * 1000
    hi = now_ms + FINESTRA_MIN * 60 * 1000

    blocchi = [f"🚆 <b>Prossimi treni</b> — {_ora_roma().strftime('%H:%M')}"]
    for tr in TRATTE:
        regionali = sorted(
            (t for t in _partenze(tr["da"]) if _is_reg(t)),
            key=lambda x: x.get("orarioPartenza") or 0,
        )
        trovati, ispezionati = [], 0
        for t in regionali:
            if ispezionati >= MAX_CANDIDATI:
                break
            op = t.get("orarioPartenza")
            if isinstance(op, int) and not (lo <= op <= hi):
                continue
            num, orig, ts = (t.get("numeroTreno"),
                             t.get("codOrigine"),
                             t.get("dataPartenzaTreno"))
            if not (num and orig and ts):
                continue
            ispezionati += 1
            stops = _fermate(num, orig, ts, oggi)
            if not stops or tr["da"] not in stops or tr["a"] not in stops:
                continue
            if stops.index(tr["da"]) >= stops.index(tr["a"]):
                continue
            trovati.append(t)
            if len(trovati) >= N_TRENI:
                break

        testa = f"\n<b>{_esc(tr['etichetta'])}</b>"
        if trovati:
            blocchi.append(testa + "\n" + "\n".join(_riga_treno(t) for t in trovati))
        else:
            blocchi.append(testa + "\n<i>nessun regionale in arrivo a breve</i>")

    return "\n".join(blocchi)


# =========================================================================== #
#  AGGANCIO DEL COMANDO /treni — copia lo snippet adatto alla tua versione di
#  python-telegram-bot nel file principale del bot (quello con Application/
#  Updater), poi: import treni_command
# =========================================================================== #
#
# ---- python-telegram-bot v20+ (ASYNC) -------------------------------------
# import asyncio
# from telegram.ext import CommandHandler
# import treni_command
#
# async def cmd_treni(update, context):
#     await context.bot.send_chat_action(update.effective_chat.id, "typing")
#     testo = await asyncio.to_thread(treni_command.tabellone)   # non blocca il loop
#     await update.message.reply_text(testo, parse_mode="HTML",
#                                     disable_web_page_preview=True)
#
# application.add_handler(CommandHandler("treni", cmd_treni))
#
# ---- python-telegram-bot v13 (SYNC) ---------------------------------------
# from telegram.ext import CommandHandler
# import treni_command
#
# def cmd_treni(update, context):
#     update.message.reply_text(treni_command.tabellone(), parse_mode="HTML",
#                               disable_web_page_preview=True)
#
# dispatcher.add_handler(CommandHandler("treni", cmd_treni))
#
# (Opzionale) registra il comando nel menu del bot via @BotFather -> /setcommands:
#   treni - Prossimi treni e binari sulle mie tratte
