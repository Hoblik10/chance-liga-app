"""Soupisky týmů a ruční absence před zápasem.

Kádry se berou z oficiálních soupisek ChanceLiga.cz – stejný web, ze kterého
už padá ligová tabulka. TheSportsDB kádr umí taky, ale testovací klíč pustí
jen deset týmů denně, takže na celou ligu nestačí.

Zranění žádný volný zdroj pro Chance Ligu neservíruje. V aplikaci se proto
u každého týmu zaškrtnou jména; pokuta do modelu se spočítá podle toho,
kdo chybí a kolik v lize hraje.

Oficiální sestava zápasu (základ + lavička) na webu ligy bývá až kolem
výkopu. Páteční Telegram ji tedy ještě nemá, v sobotu odpoledne už jít může.
"""

import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

import data
import modely
import nastaveni

SLOZKA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sestavy")
SOUBOR_KADRU = os.path.join(SLOZKA, "kadry.json")
SOUBOR_ZAPASU = os.path.join(SLOZKA, "zapasy.json")
SOUBOR_ABSENCI = os.path.join(SLOZKA, "absence.csv")

STARI_KADRU = timedelta(hours=12)
STARI_ZAPASU = timedelta(hours=3)

SLOUPCE_ABSENCI = ["tym", "id_hrace", "jmeno", "duvod", "zapsano"]

NAZVY_POZIC = {
    "B": "brankář",
    "O": "obránce",
    "Z": "záložník",
    "U": "útočník",
}

PORADI_POZIC = {"B": 0, "O": 1, "Z": 2, "U": 3}

ADRESA_LIGY = "https://www.chanceliga.cz"


def rok_sezony(sezona=None):
    """2026-2027 → 2027, jak to v URL používá ChanceLiga.cz."""
    text = sezona or nastaveni.SEZONA_SPORTSDB
    casti = str(text).split("-")
    return casti[-1] if casti else text


def _id_z_odkazu(href):
    """/hrac/4201-jakub-surovcik → 4201"""
    shoda = re.search(r"/hrac/(\d+)", href or "")
    return shoda.group(1) if shoda else ""


def _slug_klubu(href):
    """/klub/2-ac-sparta-praha nebo /klub/2027/soupiska/2-ac-sparta-praha."""
    shoda = re.search(r"/klub/(?:\d+/soupiska/)?(\d+-[a-z0-9-]+)", href or "")
    return shoda.group(1) if shoda else ""


def _cislo(text):
    text = (text or "").replace("–", "-").strip()
    if not text or text == "-":
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _uloz_json(cesta, obsah):
    os.makedirs(os.path.dirname(cesta), exist_ok=True)
    with open(cesta, "w", encoding="utf-8") as soubor:
        json.dump(obsah, soubor, ensure_ascii=False, indent=2)


def _nacti_json(cesta):
    if not os.path.exists(cesta):
        return None
    try:
        with open(cesta, encoding="utf-8") as soubor:
            return json.load(soubor)
    except (OSError, json.JSONDecodeError):
        return None


def _stari(zaznam):
    text = (zaznam or {}).get("aktualizovano") or ""
    try:
        cas = datetime.fromisoformat(text)
    except ValueError:
        return timedelta.max
    if cas.tzinfo is not None:
        cas = cas.replace(tzinfo=None)
    return datetime.now() - cas


def _stahni(url):
    odpoved = requests.get(url, headers=nastaveni.HTTP_HLAVICKY, timeout=20)
    odpoved.raise_for_status()
    return odpoved.content


# --- PARSE ---


def parsuj_kluby(html):
    """Seznam klubů z /kluby: slug + kanonický název."""
    soup = BeautifulSoup(html, "html.parser")
    kluby = []
    videne = set()

    for odkaz in soup.find_all("a", href=True):
        slug = _slug_klubu(odkaz["href"])
        if not slug or slug in videne:
            continue
        nazev = " ".join(odkaz.get_text(" ", strip=True).split())
        if len(nazev) < 4:
            continue
        videne.add(slug)
        kluby.append({"slug": slug, "nazev": data.nazev_tymu(nazev)})

    return kluby


def parsuj_soupisku(html):
    """Hráči z tabulky Soupiska: číslo, jméno, post, zápasy, góly."""
    soup = BeautifulSoup(html, "html.parser")
    hraci = []

    for tabulka in soup.find_all("table"):
        radky = tabulka.find_all("tr")
        if not radky:
            continue
        hlavicka = [bunka.get_text(strip=True) for bunka in radky[0].find_all(["th", "td"])]
        if "Hráč" not in hlavicka:
            continue

        for radek in radky[1:]:
            bunky = radek.find_all("td")
            if len(bunky) < 8:
                continue
            odkaz = bunky[1].find("a")
            href = odkaz.get("href", "") if odkaz else ""
            jmeno = (odkaz.get_text(strip=True) if odkaz else bunky[1].get_text(strip=True))
            jmeno = " ".join(jmeno.split())
            identita = _id_z_odkazu(href)
            if not jmeno:
                continue
            hraci.append(
                {
                    "id": identita or jmeno,
                    "jmeno": jmeno,
                    "cislo": (bunky[0].get_text(strip=True) or "–"),
                    "pozice": (bunky[2].get_text(strip=True) or "?")[:1].upper(),
                    "zapasy": _cislo(bunky[7].get_text(strip=True)),
                    "goly": _cislo(bunky[8].get_text(strip=True)) if len(bunky) > 8 else 0,
                }
            )
        if hraci:
            return hraci

    return hraci


def parsuj_sestavu_zapasu(html):
    """Základní jedenáctka a lavička domácích i hostů.

    Prázdný řádek v tabulce odděluje startující od náhradníků. Když tabulky
    ještě nejsou (typicky dny před výkopem), vrací None.
    """
    soup = BeautifulSoup(html, "html.parser")
    tymy = []

    for tabulka in soup.find_all("table"):
        radky = tabulka.find_all("tr")
        if not radky:
            continue
        hlavicka = [bunka.get_text(strip=True) for bunka in radky[0].find_all(["th", "td"])]
        if "Jméno" not in hlavicka or "#" not in hlavicka or "Hráč" in hlavicka:
            continue

        zaklad, nahradnici = [], []
        cil = zaklad
        for radek in radky[1:]:
            bunky = radek.find_all("td")
            if len(bunky) < 4:
                cil = nahradnici
                continue
            odkaz = None
            for bunka in bunky:
                odkaz = bunka.find("a")
                if odkaz:
                    break
            if odkaz is None:
                continue
            identita = _id_z_odkazu(odkaz.get("href", ""))
            jmeno = " ".join(odkaz.get_text(strip=True).split())
            if not identita and not jmeno:
                continue
            cil.append({"id": identita or jmeno, "jmeno": jmeno})

        if zaklad:
            tymy.append({"zaklad": zaklad, "nahradnici": nahradnici})

    if len(tymy) < 2:
        return None
    return {"domaci": tymy[0], "hoste": tymy[1]}


def parsuj_odkazy_zapasu(html):
    """Mapa (domácí, hosté) → slug stránky zápasu z rozpisu."""
    soup = BeautifulSoup(html, "html.parser")
    odkazy = {}

    for polozka in soup.find_all("li"):
        tymy = polozka.select("span.team a")
        odkaz_zapasu = polozka.select_one("span.score a")
        if len(tymy) < 2 or odkaz_zapasu is None:
            continue
        href = odkaz_zapasu.get("href") or ""
        if "/zapas/" not in href:
            continue
        slug = href.split("/zapas/")[-1].split("#")[0]
        if not slug:
            continue

        def _jmeno(odkaz):
            obrazek = odkaz.find("img")
            if obrazek and obrazek.get("alt"):
                return data.nazev_tymu(obrazek["alt"])
            return data.nazev_tymu(odkaz.get_text(strip=True))

        kolo = None
        popisek_kola = polozka.select_one("span.date b")
        if popisek_kola:
            shoda = re.search(r"(\d+)", popisek_kola.get_text())
            if shoda:
                kolo = int(shoda.group(1))

        klic = (_jmeno(tymy[0]), _jmeno(tymy[1]))
        odkazy[klic] = {"slug": slug, "kolo": kolo, "url": urljoin(ADRESA_LIGY, href)}

    return odkazy


# --- KÁDRY ---


def _kadry_z_disku():
    zaznam = _nacti_json(SOUBOR_KADRU) or {}
    tymy = {}
    for nazev, info in (zaznam.get("tymy") or {}).items():
        tymy[nazev] = list(info.get("hraci") or [])
    return tymy, zaznam


def nacti_kadry(vynutit=False):
    """Kádry z disku, případně čerstvé z ChanceLiga.cz.

    Vrací (slovník tým → hráči, lidský popis zdroje). Při výpadku webu
    nechá poslední uloženou verzi.
    """
    tymy, zaznam = _kadry_z_disku()
    cerstve = bool(tymy) and _stari(zaznam) < STARI_KADRU

    if tymy and cerstve and not vynutit:
        kdy = (zaznam.get("aktualizovano") or "")[:16].replace("T", " ")
        return tymy, f"✅ Soupisky z ChanceLiga.cz ({kdy}, {len(tymy)} týmů)"

    try:
        nove = stahni_kadry()
        if nove:
            return nove, (
                f"✅ Soupisky z ChanceLiga.cz, právě obnoveno ({len(nove)} týmů)"
            )
    except Exception as chyba:
        if tymy:
            return tymy, f"⚠️ Soupisky z cache ({chyba})"
        return {}, f"⚠️ Soupisky se nepodařilo načíst ({chyba})"

    if tymy:
        return tymy, f"📁 Soupisky z cache ({len(tymy)} týmů)"
    return {}, "⚠️ Soupisky chybí"


def stahni_kadry():
    """Stáhne soupisku každého klubu a uloží ji na disk."""
    kluby = parsuj_kluby(_stahni(f"{ADRESA_LIGY}/kluby"))
    if not kluby:
        raise ValueError("Na ChanceLiga.cz se nenašel seznam klubů.")

    rok = rok_sezony()
    tymy = {}
    for klub in kluby:
        url = f"{ADRESA_LIGY}/klub/{rok}/soupiska/{klub['slug']}"
        hraci = parsuj_soupisku(_stahni(url))
        if not hraci:
            continue
        tymy[klub["nazev"]] = {
            "slug": klub["slug"],
            "url": url,
            "hraci": hraci,
        }

    if len(tymy) < 10:
        raise ValueError(f"Přišlo jen {len(tymy)} soupisek, to nestačí.")

    _uloz_json(
        SOUBOR_KADRU,
        {
            "aktualizovano": datetime.now().isoformat(timespec="seconds"),
            "zdroj": "ChanceLiga.cz",
            "sezona": nastaveni.SEZONA_SPORTSDB,
            "tymy": tymy,
        },
    )
    return {nazev: info["hraci"] for nazev, info in tymy.items()}


def serad_hrace(hraci):
    """Brankáři, obrana, záloha, útok; v postu podle počtu zápasů."""
    return sorted(
        hraci,
        key=lambda h: (
            PORADI_POZIC.get(h.get("pozice"), 9),
            -int(h.get("zapasy") or 0),
            h.get("jmeno") or "",
        ),
    )


def popisek_hrace(hrac):
    """44 Jakub Surovčík (brankář, 5 záp.)"""
    cislo = hrac.get("cislo") or "–"
    jmeno = hrac.get("jmeno") or "?"
    pozice = NAZVY_POZIC.get(hrac.get("pozice"), hrac.get("pozice") or "?")
    zapasy = int(hrac.get("zapasy") or 0)
    return f"{cislo} {jmeno} ({pozice}, {zapasy} záp.)"


def jmena_hracu(kadr, identita):
    """id → jméno; neznámé id nechá jak je."""
    hledane = [str(x) for x in identita if x]
    mapa = {str(h.get("id")): h.get("jmeno") or str(h.get("id")) for h in kadr}
    return [mapa.get(i, i) for i in hledane]


# --- ZÁPASY / SESTAVY ---


def nacti_odkazy_zapasu(vynutit=False):
    """(domácí, hosté) → {slug, kolo} z rozpisu, s cache na disku."""
    zaznam = _nacti_json(SOUBOR_ZAPASU) or {}
    if not vynutit and zaznam.get("zapasy") and _stari(zaznam) < STARI_ZAPASU:
        return {
            (polozka["domaci"], polozka["hoste"]): polozka
            for polozka in zaznam["zapasy"]
        }

    try:
        rok = rok_sezony()
        url = (
            f"{ADRESA_LIGY}/rozpis-zapasu/{rok}"
            "?id_stage=1&month=0&round=0&type=1"
        )
        parsovane = parsuj_odkazy_zapasu(_stahni(url))
        seznam = [
            {
                "domaci": domaci,
                "hoste": hoste,
                "slug": info["slug"],
                "kolo": info.get("kolo"),
            }
            for (domaci, hoste), info in parsovane.items()
        ]
        _uloz_json(
            SOUBOR_ZAPASU,
            {
                "aktualizovano": datetime.now().isoformat(timespec="seconds"),
                "zapasy": seznam,
            },
        )
        return {(p["domaci"], p["hoste"]): p for p in seznam}
    except Exception:
        if zaznam.get("zapasy"):
            return {
                (p["domaci"], p["hoste"]): p for p in zaznam["zapasy"]
            }
        return {}


def stahni_sestavu_zapasu(domaci, hoste, odkazy=None):
    """Oficiální sestava konkrétního utkání, nebo None když ještě není."""
    odkazy = odkazy if odkazy is not None else nacti_odkazy_zapasu()
    info = odkazy.get((domaci, hoste))
    if not info:
        return None
    html = _stahni(f"{ADRESA_LIGY}/zapas/{info['slug']}")
    return parsuj_sestavu_zapasu(html)


def id_mimo_sestavu(kadr, sestava_tymu):
    """Hráči z kádru, kteří nejsou v základu ani na lavičce."""
    if not sestava_tymu:
        return []
    v_zapase = {
        str(h.get("id"))
        for skupina in (sestava_tymu.get("zaklad"), sestava_tymu.get("nahradnici"))
        for h in (skupina or [])
        if h.get("id")
    }
    if not v_zapase:
        return []
    return [
        hrac["id"]
        for hrac in kadr
        if str(hrac.get("id")) and str(hrac["id"]) not in v_zapase
    ]


# --- ABSENCE ---


def nacti_absence(cesta=SOUBOR_ABSENCI):
    """Tým → seznam id hráčů, kteří nemají nastoupit."""
    if not os.path.exists(cesta):
        return {}
    try:
        df = pd.read_csv(cesta, dtype=str)
    except (pd.errors.EmptyDataError, OSError):
        return {}

    vysledek = {}
    if "tym" not in df.columns or "id_hrace" not in df.columns:
        return {}
    for _, radek in df.iterrows():
        tym = str(radek["tym"]).strip()
        identita = str(radek["id_hrace"]).strip()
        if not tym or not identita or identita.lower() == "nan":
            continue
        vysledek.setdefault(tym, []).append(identita)
    return vysledek


def uloz_absence_tymu(tym, identita, kadr=None, cesta=SOUBOR_ABSENCI, cas=None):
    """Nahradí seznam chybějících u jednoho týmu."""
    kadr = kadr or []
    jmena = {str(h.get("id")): h.get("jmeno") or "" for h in kadr}
    kdy = (cas or datetime.now()).strftime("%Y-%m-%d %H:%M")

    if os.path.exists(cesta):
        try:
            df = pd.read_csv(cesta, dtype=str)
        except (pd.errors.EmptyDataError, OSError):
            df = pd.DataFrame(columns=SLOUPCE_ABSENCI)
    else:
        df = pd.DataFrame(columns=SLOUPCE_ABSENCI)

    for sloupec in SLOUPCE_ABSENCI:
        if sloupec not in df.columns:
            df[sloupec] = None
    df = df[df["tym"] != tym] if not df.empty else df

    nove = pd.DataFrame(
        [
            {
                "tym": tym,
                "id_hrace": str(i),
                "jmeno": jmena.get(str(i), ""),
                "duvod": "chybi",
                "zapsano": kdy,
            }
            for i in identita
            if i
        ]
    )
    vysledek = pd.concat([df[SLOUPCE_ABSENCI], nove], ignore_index=True)
    os.makedirs(os.path.dirname(cesta) or ".", exist_ok=True)
    vysledek.to_csv(cesta, index=False, encoding="utf-8")
    return vysledek


def pokuta_pro_tym(tym, kadry, absence):
    """Číslo 0–0.20, nebo nula když kádr či výběr chybí."""
    return modely.pokuta_z_absenci(kadry.get(tym) or [], absence.get(tym) or [])


if __name__ == "__main__":
    tymy, popis = nacti_kadry(vynutit=True)
    print(popis)
    for nazev, hraci in sorted(tymy.items()):
        print(f"  {nazev}: {len(hraci)} hráčů")
