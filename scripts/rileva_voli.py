#!/usr/bin/env python3
"""
Rilevazione voli diretti Catania (CTA) <-> Roma Fiumicino (FCO)
per il pendolarismo settimanale: andata lunedi', ritorno giovedi'.

Criteri (fissi, vedi COSTANTI):
  - Andata:  lunedi', CTA->FCO, diretto, arrivo a FCO entro le 08:30
  - Ritorno: giovedi' della stessa settimana, FCO->CTA, diretto,
             partenza non prima delle 20:40
  - Solo Fiumicino (mai Ciampino), 1 adulto, solo bagaglio a mano
  - Prezzi in euro, per tratta

Output:
  data/latest.json            ultima rilevazione
  data/history/<YYYY-MM-DD>.json  copia archiviata della stessa rilevazione

Fonti:
  - Ryanair  : API pubblica, nessuna chiave necessaria
  - Amadeus  : opzionale, copre ITA Airways / Wizz / Aeroitalia.
               Attivo solo se le variabili d'ambiente AMADEUS_CLIENT_ID e
               AMADEUS_CLIENT_SECRET sono presenti.

Uso:
  python3 scripts/rileva_voli.py                  # rilevazione reale
  python3 scripts/rileva_voli.py --settimane 4    # meno settimane (test rapido)
  python3 scripts/rileva_voli.py --autotest       # verifica la logica offline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------- COSTANTI

ORIGINE = "CTA"
DESTINAZIONE = "FCO"          # solo Fiumicino, mai Ciampino (CIA)
ADULTI = 1
VALUTA = "EUR"

ARRIVO_MAX_ANDATA = time(8, 30)    # lunedi': a FCO entro le 08:30
PARTENZA_MIN_RITORNO = time(20, 40)  # giovedi': da FCO non prima delle 20:40

SETTIMANE_DEFAULT = 17
TIMEOUT = 30
SOGLIA_PREZZO_ALTO = 150.0   # usata solo come annotazione nel JSON

# Compagnie low cost: la tariffa base include solo il bagaglio piccolo
LOW_COST = {"Ryanair", "Wizz Air", "easyJet"}

NOMI_COMPAGNIE = {
    "FR": "Ryanair",
    "RK": "Ryanair UK",
    "AZ": "ITA Airways",
    "W6": "Wizz Air",
    "W4": "Wizz Air Malta",
    "W9": "Wizz Air UK",
    "U2": "easyJet",
    "EC": "easyJet Europe",
    "XZ": "Aeroitalia",
    "IG": "Air Dolomiti",
    "VY": "Vueling",
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


# ------------------------------------------------------------------ UTILITA'


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def http_json(url: str, headers: dict | None = None, dati: bytes | None = None) -> Any:
    """GET/POST che restituisce JSON. Solleva eccezione in caso di errore."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=dati, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def prossimi_lunedi(da: date, quante: int) -> list[date]:
    """I prossimi `quante` lunedi' a partire da `da` (incluso se e' lunedi')."""
    giorni_avanti = (0 - da.weekday()) % 7
    primo = da + timedelta(days=giorni_avanti)
    return [primo + timedelta(weeks=i) for i in range(quante)]


def etichetta_settimana(lunedi: date) -> str:
    giovedi = lunedi + timedelta(days=3)
    anno, numero, _ = lunedi.isocalendar()
    mesi = ["gen", "feb", "mar", "apr", "mag", "giu",
            "lug", "ago", "set", "ott", "nov", "dic"]
    if lunedi.month == giovedi.month:
        periodo = f"{lunedi.day}-{giovedi.day} {mesi[lunedi.month - 1]}"
    else:
        periodo = (f"{lunedi.day} {mesi[lunedi.month - 1]}"
                   f"-{giovedi.day} {mesi[giovedi.month - 1]}")
    return f"{anno}-W{numero:02d} ({periodo})"


def nome_compagnia(codice: str) -> str:
    return NOMI_COMPAGNIE.get(codice.upper(), codice.upper())


def nota_bagaglio(compagnia: str) -> str | None:
    if compagnia in LOW_COST:
        return "tariffa base: solo bagaglio piccolo sotto il sedile"
    return None


# ------------------------------------------------------------- FONTE RYANAIR


def ryanair(origine: str, destinazione: str, giorno: date) -> list[dict]:
    """Voli Ryanair del giorno indicato. Lista vuota se nessuno o errore."""
    params = urllib.parse.urlencode({
        "ADT": ADULTI, "CHD": 0, "INF": 0, "TEEN": 0,
        "DateOut": giorno.isoformat(),
        "Origin": origine, "Destination": destinazione,
        "FlexDaysBeforeOut": 0, "FlexDaysOut": 0,
        "RoundTrip": "false", "ToUs": "AGREED",
        "IncludeConnectingFlights": "false",
        "promoCode": "",
    })
    url = f"https://www.ryanair.com/api/booking/v4/it-it/availability?{params}"
    try:
        dati = http_json(url, headers={"Accept-Language": "it-IT"})
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        log(f"  ! Ryanair {origine}->{destinazione} {giorno}: {e}")
        return []

    voli: list[dict] = []
    for trip in dati.get("trips") or []:
        for d in trip.get("dates") or []:
            for f in d.get("flights") or []:
                # solo diretti
                if len(f.get("segments") or []) > 1:
                    continue
                tariffa = f.get("regularFare") or {}
                tariffe = tariffa.get("fares") or []
                if not tariffe:
                    continue  # posto esaurito
                prezzo = tariffe[0].get("amount")
                orari = f.get("time") or []
                if prezzo is None or len(orari) < 2:
                    continue
                voli.append(_volo(
                    partenza_iso=orari[0],
                    arrivo_iso=orari[1],
                    compagnia="Ryanair",
                    numero=(f.get("flightNumber") or "").replace(" ", " ").strip(),
                    prezzo=float(prezzo),
                    fonte="ryanair",
                ))
    return voli


# ------------------------------------------------------------- FONTE AMADEUS


_token_cache: dict[str, Any] = {}


def amadeus_token() -> str | None:
    cid = os.environ.get("AMADEUS_CLIENT_ID")
    seg = os.environ.get("AMADEUS_CLIENT_SECRET")
    if not cid or not seg:
        return None
    if _token_cache.get("token"):
        return _token_cache["token"]
    host = os.environ.get("AMADEUS_HOST", "test.api.amadeus.com")
    corpo = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": seg,
    }).encode()
    try:
        r = http_json(
            f"https://{host}/v1/security/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            dati=corpo,
        )
        _token_cache["token"] = r.get("access_token")
        return _token_cache["token"]
    except Exception as e:  # noqa: BLE001 - fonte opzionale, non deve bloccare
        log(f"  ! Amadeus autenticazione fallita: {e}")
        return None


def amadeus(origine: str, destinazione: str, giorno: date) -> list[dict]:
    token = amadeus_token()
    if not token:
        return []
    host = os.environ.get("AMADEUS_HOST", "test.api.amadeus.com")
    params = urllib.parse.urlencode({
        "originLocationCode": origine,
        "destinationLocationCode": destinazione,
        "departureDate": giorno.isoformat(),
        "adults": ADULTI,
        "nonStop": "true",
        "currencyCode": VALUTA,
        "max": 50,
    })
    url = f"https://{host}/v2/shopping/flight-offers?{params}"
    try:
        dati = http_json(url, headers={"Authorization": f"Bearer {token}"})
    except Exception as e:  # noqa: BLE001
        log(f"  ! Amadeus {origine}->{destinazione} {giorno}: {e}")
        return []

    voli: list[dict] = []
    for offerta in dati.get("data") or []:
        itinerari = offerta.get("itineraries") or []
        if len(itinerari) != 1:
            continue
        segmenti = itinerari[0].get("segments") or []
        if len(segmenti) != 1:
            continue  # solo diretti
        s = segmenti[0]
        if s.get("departure", {}).get("iataCode") != origine:
            continue
        if s.get("arrival", {}).get("iataCode") != destinazione:
            continue
        prezzo = (offerta.get("price") or {}).get("grandTotal")
        if prezzo is None:
            continue
        codice = s.get("carrierCode", "")
        voli.append(_volo(
            partenza_iso=s["departure"]["at"],
            arrivo_iso=s["arrival"]["at"],
            compagnia=nome_compagnia(codice),
            numero=f"{codice} {s.get('number', '')}".strip(),
            prezzo=float(prezzo),
            fonte="amadeus",
        ))
    return voli


# --------------------------------------------------------------- COSTRUZIONE


def _volo(partenza_iso: str, arrivo_iso: str, compagnia: str,
          numero: str, prezzo: float, fonte: str) -> dict:
    p = datetime.fromisoformat(partenza_iso.replace("Z", "+00:00"))
    a = datetime.fromisoformat(arrivo_iso.replace("Z", "+00:00"))
    volo = {
        "partenza": p.strftime("%H:%M"),
        "arrivo": a.strftime("%H:%M"),
        "compagnia": compagnia,
        "numero_volo": numero or None,
        "prezzo_eur": round(prezzo, 2),
        "fonte": fonte,
        "_p": p,   # campi interni, rimossi prima di serializzare
        "_a": a,
    }
    nota = nota_bagaglio(compagnia)
    if nota:
        volo["bagaglio"] = nota
    return volo


def dedup(voli: list[dict]) -> list[dict]:
    """Stesso volo da fonti diverse: tiene il prezzo piu' basso."""
    migliori: dict[tuple, dict] = {}
    for v in voli:
        chiave = (v["compagnia"], v["partenza"], v["arrivo"])
        if chiave not in migliori or v["prezzo_eur"] < migliori[chiave]["prezzo_eur"]:
            migliori[chiave] = v
    return list(migliori.values())


def pulisci(volo: dict) -> dict:
    return {k: v for k, v in volo.items() if not k.startswith("_")}


def tratta(origine: str, destinazione: str, giorno: date,
           filtro, fonti) -> dict:
    """Costruisce il blocco andata/ritorno per un giorno."""
    grezzi: list[dict] = []
    for fonte in fonti:
        grezzi.extend(fonte(origine, destinazione, giorno))

    validi = [v for v in dedup(grezzi) if filtro(v)]
    validi.sort(key=lambda v: (v["prezzo_eur"], v["_p"]))

    return {
        "data": giorno.isoformat(),
        "tratta": f"{origine}→{destinazione}",
        "migliore": pulisci(validi[0]) if validi else None,
        "validi": [pulisci(v) for v in validi],
    }


def filtro_andata(v: dict) -> bool:
    return v["_a"].time() <= ARRIVO_MAX_ANDATA


def filtro_ritorno(v: dict) -> bool:
    return v["_p"].time() >= PARTENZA_MIN_RITORNO


def rileva(settimane: int, fonti) -> dict:
    oggi = datetime.now(timezone.utc).date()
    risultato_settimane: list[dict] = []

    for lunedi in prossimi_lunedi(oggi, settimane):
        giovedi = lunedi + timedelta(days=3)
        log(f"-> {etichetta_settimana(lunedi)}")

        andata = tratta(ORIGINE, DESTINAZIONE, lunedi, filtro_andata, fonti)
        ritorno = tratta(DESTINAZIONE, ORIGINE, giovedi, filtro_ritorno, fonti)

        totale = None
        if andata["migliore"] and ritorno["migliore"]:
            totale = round(
                andata["migliore"]["prezzo_eur"] + ritorno["migliore"]["prezzo_eur"], 2
            )

        risultato_settimane.append({
            "settimana": etichetta_settimana(lunedi),
            "andata": andata,
            "ritorno": ritorno,
            "totale_eur": totale,
        })

    return {
        "rilevazione_utc": datetime.now(timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "criteri": {
            "andata": f"lunedi', {ORIGINE}->{DESTINAZIONE}, diretto, "
                      f"arrivo entro {ARRIVO_MAX_ANDATA.strftime('%H:%M')}",
            "ritorno": f"giovedi', {DESTINAZIONE}->{ORIGINE}, diretto, "
                       f"partenza non prima di {PARTENZA_MIN_RITORNO.strftime('%H:%M')}",
            "passeggeri": ADULTI,
            "bagaglio": "solo bagaglio a mano",
            "valuta": VALUTA,
            "prezzi": "per tratta",
            "soglia_prezzo_alto_eur": SOGLIA_PREZZO_ALTO,
        },
        "fonti_attive": [f.__name__ for f in fonti],
        "settimane": risultato_settimane,
    }


# ----------------------------------------------------------------- AUTOTEST


def autotest() -> int:
    """Verifica la logica senza rete, con voli finti."""
    base = date(2026, 10, 5)  # un lunedi'

    def finta_andata(o, d, g):
        return [
            _volo(f"{g}T06:15:00", f"{g}T07:40:00", "Ryanair", "FR 4567", 39.99, "x"),
            _volo(f"{g}T09:00:00", f"{g}T10:25:00", "ITA Airways", "AZ 1708", 59.0, "x"),
            _volo(f"{g}T05:40:00", f"{g}T07:05:00", "Wizz Air", "W6 1234", 170.0, "x"),
        ]

    def finto_ritorno(o, d, g):
        return [
            _volo(f"{g}T21:05:00", f"{g}T22:25:00", "ITA Airways", "AZ 1725", 75.5, "x"),
            _volo(f"{g}T18:30:00", f"{g}T19:55:00", "Ryanair", "FR 4568", 29.99, "x"),
        ]

    def fonte(o, d, g):
        return finta_andata(o, d, g) if o == ORIGINE else finto_ritorno(o, d, g)

    esiti = []

    # 1. i lunedi' generati sono effettivamente lunedi'
    lun = prossimi_lunedi(base, 17)
    esiti.append(("17 lunedi' consecutivi",
                  len(lun) == 17 and all(x.weekday() == 0 for x in lun)
                  and (lun[1] - lun[0]).days == 7))

    # 2. filtro andata: scarta l'arrivo alle 10:25, tiene 07:40 e 07:05
    a = tratta(ORIGINE, DESTINAZIONE, base, filtro_andata, [fonte])
    orari_a = sorted(v["arrivo"] for v in a["validi"])
    esiti.append(("andata: solo arrivi entro 08:30", orari_a == ["07:05", "07:40"]))
    esiti.append(("andata: migliore = piu' economico", a["migliore"]["prezzo_eur"] == 39.99))
    esiti.append(("andata: nota bagaglio su Ryanair",
                  "bagaglio" in a["migliore"]))

    # 3. filtro ritorno: scarta la partenza alle 18:30
    giovedi = base + timedelta(days=3)
    r = tratta(DESTINAZIONE, ORIGINE, giovedi, filtro_ritorno, [fonte])
    esiti.append(("ritorno: solo partenze dalle 20:40", [v["partenza"] for v in r["validi"]] == ["21:05"]))
    esiti.append(("ritorno: ITA senza nota bagaglio", "bagaglio" not in r["migliore"]))

    # 4. totale
    tot = round(a["migliore"]["prezzo_eur"] + r["migliore"]["prezzo_eur"], 2)
    esiti.append(("totale andata+ritorno", tot == 115.49))

    # 5. dedup tiene il prezzo minore
    v1 = _volo(f"{base}T06:15:00", f"{base}T07:40:00", "Ryanair", "FR 4567", 39.99, "a")
    v2 = _volo(f"{base}T06:15:00", f"{base}T07:40:00", "Ryanair", "FR 4567", 24.99, "b")
    d = dedup([v1, v2])
    esiti.append(("dedup: tiene il prezzo minore",
                  len(d) == 1 and d[0]["prezzo_eur"] == 24.99))

    # 6. settimana senza voli compatibili -> migliore None, totale None
    s = rileva(1, [lambda o, dd, g: []])
    w = s["settimane"][0]
    esiti.append(("nessun volo: migliore/totale a null",
                  w["andata"]["migliore"] is None and w["totale_eur"] is None))

    # 7. il JSON prodotto e' serializzabile (nessun campo interno rimasto)
    try:
        json.dumps(rileva(2, [fonte]))
        serializzabile = True
    except TypeError:
        serializzabile = False
    esiti.append(("JSON serializzabile", serializzabile))

    # 8. etichetta settimana
    esiti.append(("etichetta settimana",
                  etichetta_settimana(base) == "2026-W41 (5-8 ott)"))

    ok = 0
    for nome, esito in esiti:
        print(f"  [{'OK ' if esito else 'FAIL'}] {nome}")
        ok += bool(esito)
    print(f"\n{ok}/{len(esiti)} verifiche superate")
    return 0 if ok == len(esiti) else 1


# --------------------------------------------------------------------- MAIN


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settimane", type=int, default=SETTIMANE_DEFAULT)
    ap.add_argument("--autotest", action="store_true",
                    help="verifica la logica offline e termina")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    if args.autotest:
        return autotest()

    fonti = [ryanair]
    if os.environ.get("AMADEUS_CLIENT_ID") and os.environ.get("AMADEUS_CLIENT_SECRET"):
        fonti.append(amadeus)
        log("Fonti: Ryanair + Amadeus")
    else:
        log("Fonti: solo Ryanair (AMADEUS_CLIENT_ID/SECRET non impostati)")

    dati = rileva(args.settimane, fonti)

    con_voli = sum(1 for s in dati["settimane"] if s["totale_eur"] is not None)
    log(f"\nSettimane coperte: {len(dati['settimane'])}, "
        f"con andata+ritorno validi: {con_voli}")

    if con_voli == 0:
        log("ATTENZIONE: nessuna settimana con voli compatibili. "
            "Possibile fonte non raggiungibile.")

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "history"), exist_ok=True)

    testo = json.dumps(dati, ensure_ascii=False, indent=2) + "\n"
    with open(os.path.join(args.out, "latest.json"), "w", encoding="utf-8") as f:
        f.write(testo)
    oggi = datetime.now(timezone.utc).date().isoformat()
    with open(os.path.join(args.out, "history", f"{oggi}.json"), "w",
              encoding="utf-8") as f:
        f.write(testo)

    log(f"Scritti {args.out}/latest.json e {args.out}/history/{oggi}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
