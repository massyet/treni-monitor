# 🚆 treni-monitor

Bot di notifica (non interattivo) che controlla a intervalli regolari i **ritardi**
dei treni **regionali** su una o più **tratte** che ti interessano, e ti manda un
messaggio Telegram **solo quando c'è qualcosa di nuovo**. Gira interamente su
**GitHub Actions**: nessun server, nessun hosting.

Dati da [ViaggiaTreno](http://www.viaggiatreno.it) (API non ufficiale di Trenitalia).

---

## L'idea: configuri tratte, non treni

Invece di elencare 15 numeri di treno, indichi le **coppie di stazioni**
(es. `ROMA TERMINI → CIAMPINO`). A ogni giro lo script:

1. chiama **una volta** `partenze` per ogni stazione di partenza (il ritardo in
   tempo reale è già dentro quella risposta);
2. tiene solo i **regionali** in partenza nella finestra oraria utile;
3. verifica quali di quei treni **passano davvero per la tua destinazione**
   (anche se è una fermata intermedia, non il capolinea);
4. ti avvisa se sono in ritardo oltre soglia, se peggiorano, se vengono soppressi,
   o quando **rientrano in orario** (`✅`).

Quando cambia l'orario ferroviario non devi aggiornare nulla.

### Perché è leggero
La lista delle fermate di un treno non cambia durante la giornata, quindi viene
scaricata una volta e messa in **cache giornaliera** (`state.json`). Dopo il primo
giro del giorno restano quasi solo le chiamate `partenze` (2–3 per esecuzione).

---

## Setup (≈10 minuti)

### 1. Bot Telegram
@BotFather → `/newbot` → copia il **token**.

### 2. chat_id
Scrivi un messaggio al bot, poi apri
`https://api.telegram.org/bot<TOKEN>/getUpdates` e leggi `"chat":{"id":...}`.

### 3. Secret nel repo
**Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

### 4. Tratte in `config.json`
```json
{
  "soglia_ritardo_min": 5,
  "step_riavviso_min": 10,
  "finestra_minuti": 75,
  "tratte": [
    { "da": "MARINO LAZIALE", "a": "ROMA TERMINI", "etichetta": "Marino → Roma" }
  ]
}
```
- **da / a**: nomi delle stazioni (li scrive lo script in chiaro; vedi nota sotto).
- **etichetta** *(opzionale)*: nome mostrato nella notifica.
- **soglia_ritardo_min**: minuti di ritardo oltre cui scatta l'avviso.
- **step_riavviso_min**: di quanto deve peggiorare per ri-avvisarti.
- **finestra_minuti**: quanto in avanti guardare tra i treni in partenza.

> **Verifica i codici stazione.** Lo script risolve i nomi via ViaggiaTreno e
> nei log di Actions stampa cosa ha scelto, es.
> `[info] stazione 'CIAMPINO' -> S08600 (CIAMPINO)`. Controlla la prima volta che
> abbia preso la stazione giusta (i nomi a volte hanno varianti). Se sbaglia,
> forza il codice con `"da_cod": "S#####"` / `"a_cod": "S#####"` nella tratta.

### 5. Attiva
Push di tutto → tab **Actions** → abilita → **Run workflow** per la prima prova.

---

## Note pratiche

- **Orari del cron**: GitHub usa **UTC**. `*/10 5-19` ≈ 07:00–21:00 ora italiana in
  estate (CEST). In inverno (CET) sarebbe 06:00–20:00: sposta la finestra in
  `.github/workflows/monitor.yml` se ti serve.
- **Frequenza**: il cron di GitHub ha un minimo di 5 minuti e può ritardare di
  qualche minuto sotto carico.
- **Repo inattivo**: i workflow schedulati si fermano dopo ~60 giorni senza commit.
- **Stato**: `state.json` è gestito via cache di Actions (non sporca la git history).
- **Solo regionali**: filtra categorie REG / RV / RGV. Per includere altri tipi,
  modifica `CAT_REGIONALI` in `monitor.py`.
- **Soppressioni**: rilevate se `partenze` le espone (`provvedimento`). Per una
  rilevazione garantita servirebbe una chiamata live `andamentoTreno` sui treni
  imminenti: facile da aggiungere se ti interessa.
- **API non ufficiale**: ViaggiaTreno non è documentata da Trenitalia e i dati
  sono disponibili solo per la giornata in corso. Lo script è difensivo e non va
  in crash su risposte anomale; nel dubbio guarda i log del workflow.

## Test in locale
```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=...  TELEGRAM_CHAT_ID=...
python monitor.py
```
