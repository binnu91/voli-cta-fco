# voli-cta-fco

Rilevazione notturna dei voli diretti **Catania (CTA) ↔ Roma Fiumicino (FCO)**
compatibili con il pendolarismo settimanale: andata il lunedì, ritorno il giovedì.

Il workflow GitHub Actions gira ogni notte, interroga le fonti e salva il
risultato in `data/latest.json`, più una copia archiviata in
`data/history/<YYYY-MM-DD>.json`. Un'attività pianificata di Claude legge quel
file e invia il riepilogo.

## Criteri applicati

| | |
|---|---|
| Andata | lunedì, CTA→FCO, diretto, arrivo a FCO **entro le 08:30** |
| Ritorno | giovedì della stessa settimana, FCO→CTA, diretto, partenza **non prima delle 20:40** |
| Aeroporto | solo Fiumicino, mai Ciampino |
| Passeggeri | 1 adulto, solo bagaglio a mano |
| Prezzi | euro, **per tratta** |
| Orizzonte | 17 settimane |

Per le low cost (Ryanair, Wizz, easyJet) la tariffa rilevata è quella base:
include **solo il bagaglio piccolo** sotto il sedile. Il campo `bagaglio` del
JSON lo segnala volo per volo.

## Messa in funzione

1. Crea un repo **pubblico** chiamato `voli-cta-fco` e carica questi file
   mantenendo la struttura delle cartelle.
2. Vai in **Settings → Actions → General → Workflow permissions** e scegli
   **Read and write permissions** (serve al workflow per committare i dati).
3. Apri la scheda **Actions**, seleziona *Rileva voli CTA-FCO* e lancia
   **Run workflow** a mano la prima volta.
4. Quando il run è verde, controlla che `data/latest.json` esista e sia
   raggiungibile a questo indirizzo:
   `https://raw.githubusercontent.com/<tuo-utente>/voli-cta-fco/main/data/latest.json`

Da quel momento l'attività pianificata notturna ha qualcosa da leggere.

## Fonti dati

**Ryanair** — API pubblica, nessuna chiave necessaria. Funziona subito.

**Amadeus** *(opzionale, consigliata)* — serve a coprire ITA Airways, Wizz Air e
Aeroitalia, che non hanno API pubbliche. Senza questa fonte vedrai **solo voli
Ryanair**, che sulla CTA–FCO è una fetta importante ma non tutto il mercato.

Per attivarla: registrati su [developers.amadeus.com](https://developers.amadeus.com)
(piano gratuito), crea un'app e aggiungi in **Settings → Secrets and variables →
Actions** i due secret `AMADEUS_CLIENT_ID` e `AMADEUS_CLIENT_SECRET`.

> L'ambiente *test* di Amadeus restituisce dati parziali e non sempre aggiornati.
> Per prezzi reali serve passare all'ambiente di produzione: in quel caso
> aggiungi la **variabile** (non secret) `AMADEUS_HOST` con valore
> `api.amadeus.com`.

I prezzi rilevati sono indicativi e vanno confermati sul sito della compagnia
al momento dell'acquisto.

## Uso in locale

```bash
python3 scripts/rileva_voli.py --autotest      # verifica la logica, senza rete
python3 scripts/rileva_voli.py --settimane 2   # rilevazione reale, test rapido
python3 scripts/rileva_voli.py                 # rilevazione completa (17 settimane)
```

Solo libreria standard: nessuna dipendenza da installare.

## Struttura di `data/latest.json`

```json
{
  "rilevazione_utc": "2026-10-02T03:12:44Z",
  "criteri": { "...": "..." },
  "fonti_attive": ["ryanair", "amadeus"],
  "settimane": [
    {
      "settimana": "2026-W41 (5-8 ott)",
      "andata": {
        "data": "2026-10-05",
        "tratta": "CTA→FCO",
        "migliore": {
          "partenza": "06:15",
          "arrivo": "07:40",
          "compagnia": "Ryanair",
          "numero_volo": "FR 4567",
          "prezzo_eur": 39.99,
          "fonte": "ryanair",
          "bagaglio": "tariffa base: solo bagaglio piccolo sotto il sedile"
        },
        "validi": []
      },
      "ritorno": { "...": "..." },
      "totale_eur": 115.49
    }
  ]
}
```

`migliore` è `null` e `totale_eur` è `null` quando per quel giorno non esiste
nessun volo compatibile con i criteri.

## Manutenzione

- I criteri di orario stanno nelle costanti in testa a `scripts/rileva_voli.py`
  (`ARRIVO_MAX_ANDATA`, `PARTENZA_MIN_RITORNO`): per cambiarli basta modificare lì.
- Se un run notturno fallisce, `data/latest.json` resta quello del giorno prima:
  il riepilogo lo segnalerà come rilevazione più vecchia di 36 ore.
- `data/history/` cresce di un file al giorno. Se dà fastidio, cancella
  periodicamente i file vecchi: servono solo al confronto con il giorno prima.
