"""Načítání živých dat o Chance Lize.

Modul nezná Streamlit, takže stejné funkce používá webová aplikace
i naplánovaná úloha, která posílá tipy na Telegram.

Pořadí zdrojů je vždy stejné: TheSportsDB → scraping webu → dopočet
z odehraných zápasů → statická záloha v kódu.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    PASMO_PRAHA = ZoneInfo("Europe/Prague")
except Exception:
    # GitHub runner bez tzdata – radši UTC+2 než pád celé úlohy.
    PASMO_PRAHA = timezone(timedelta(hours=2))

import pandas as pd
import requests
from bs4 import BeautifulSoup

import modely
import nastaveni
import staticka_data

# TheSportsDB posílá strTimestamp v UTC bez označení zóny. Výkop se musí
# ukázat v českém čase, jinak je v létě o dvě hodiny vedle.

# Zkrácené a anglické názvy, pod kterými týmy vedou jednotlivé zdroje.
MAPA_TYMU = {
    "Slavia": "SK Slavia Praha",
    "Jablonec": "FK Jablonec",
    "Ml. Boleslav": "FK Mladá Boleslav",
    "Mladá Boleslav": "FK Mladá Boleslav",
    "Teplice": "FK Teplice",
    "Hradec Kr.": "FC Hradec Králové",
    "Hradec Králové": "FC Hradec Králové",
    "Liberec": "FC Slovan Liberec",
    "Zbrojovka": "FC Zbrojovka Brno",
    "Sigma": "SK Sigma Olomouc",
    "Sigma Olomouc": "SK Sigma Olomouc",
    "Bohemians": "Bohemians Praha 1905",
    "Sparta": "AC Sparta Praha",
    "Ostrava": "FC Baník Ostrava",
    "Baník": "FC Baník Ostrava",
    "Plzeň": "FC Viktoria Plzeň",
    "Artis": "SK Artis Brno",
    "Artis Brno": "SK Artis Brno",
    "Slovácko": "1. FC Slovácko",
    "Pardubice": "FK Pardubice",
    "Zlín": "FC Zlín",
    "Slavia Prague": "SK Slavia Praha",
    "Sparta Prague": "AC Sparta Praha",
    "Viktoria Plzen": "FC Viktoria Plzeň",
    "Viktoria Plzeň": "FC Viktoria Plzeň",
    "Slovan Liberec": "FC Slovan Liberec",
    "Banik Ostrava": "FC Baník Ostrava",
    "Baník Ostrava": "FC Baník Ostrava",
    "Zbrojovka Brno": "FC Zbrojovka Brno",
    "Bohemians 1905": "Bohemians Praha 1905",
    "Slovacko": "1. FC Slovácko",
    "Zlin": "FC Zlín",
    "Mlada Boleslav": "FK Mladá Boleslav",
    "Hradec Kralove": "FC Hradec Králové",
}

# Stavy zápasu podle pole strStatus.
STAVY_API = {
    "FT": "✅ Odehráno", "AET": "✅ Odehráno", "PEN": "✅ Odehráno", "Match Finished": "✅ Odehráno",
    "1H": "⏳ Probíhá", "2H": "⏳ Probíhá", "HT": "⏳ Probíhá", "ET": "⏳ Probíhá", "LIVE": "⏳ Probíhá",
    "PST": "🔴 Odloženo", "Match Postponed": "🔴 Odloženo",
    "CANC": "🔴 Zrušeno", "ABD": "🔴 Zrušeno",
}

# Zdroje tabulky se zkoušejí v tomto pořadí, dokud jeden nevrátí platná data.
ZDROJE_TABULKY = [
    ("ChanceLiga.cz", "https://www.chanceliga.cz/tabulka"),
    ("iDNES", "https://fotbal.idnes.cz/fotbal/1-liga/tabulka"),
    ("Eurofotbal", "https://www.eurofotbal.cz/chance-liga/tabulka/"),
]


# --- JEDNODUCHÁ CACHE ---
# Streamlit má vlastní cache, ale ta mimo aplikaci nefunguje. Tahle drží
# odpovědi v paměti procesu a přežije i překreslení stránky, protože
# importovaný modul se načítá jen jednou.

_cache = {}


def _cachovane(sekund):
    """Zapamatuje si návratovou hodnotu funkce na zadanou dobu."""

    def dekorator(funkce):
        def obal(*argumenty):
            klic = (funkce.__name__, argumenty)
            zaznam = _cache.get(klic)

            if zaznam and datetime.now() < zaznam[0]:
                return zaznam[1]

            vysledek = funkce(*argumenty)
            _cache[klic] = (datetime.now() + timedelta(seconds=sekund), vysledek)
            return vysledek

        obal.__name__ = funkce.__name__
        obal.__doc__ = funkce.__doc__
        return obal

    return dekorator


def vycisti_cache():
    """Zahodí uložené odpovědi – pro vynucené načtení znovu."""
    _cache.clear()


# --- SCRAPING LIGOVÉ TABULKY ---


def parsuj_tabulku_bs4(html):
    """Vytáhne ligovou tabulku z HTML.

    Očekává řádek ve tvaru: pořadí | klub | Z | V | R | P | G+ | G- | RG | B
    """
    soup = BeautifulSoup(html, "html.parser")

    for tabulka in soup.find_all("table"):
        radky = []
        for tr in tabulka.find_all("tr"):
            bunky = [
                text
                for text in (c.get_text(strip=True) for c in tr.find_all(["td", "th"]))
                if text
            ]
            if len(bunky) < 10 or not re.match(r"^\d+\.?$", bunky[0]):
                continue

            try:
                radky.append(
                    {
                        "Tým": MAPA_TYMU.get(bunky[1], bunky[1]),
                        "B": int(bunky[9]),
                        "Z": int(bunky[2]),
                        "V": int(bunky[3]),
                        "R": int(bunky[4]),
                        "P": int(bunky[5]),
                        "Skóre": f"{int(bunky[6])}:{int(bunky[7])}",
                    }
                )
            except ValueError:
                continue

        if len(radky) >= 10:
            df = pd.DataFrame(radky)
            df.index = df.index + 1
            return df

    return None


@_cachovane(1800)
def nacti_ligovou_tabulku_z_webu():
    """Postupně zkouší zdroje a vrátí první platnou tabulku."""
    chyby = []

    for nazev, url in ZDROJE_TABULKY:
        try:
            odpoved = requests.get(url, headers=nastaveni.HTTP_HLAVICKY, timeout=15)
            odpoved.raise_for_status()
            df = parsuj_tabulku_bs4(odpoved.content)
            if df is not None:
                return df, nazev
            chyby.append(f"{nazev}: tabulka nenalezena")
        except requests.RequestException as chyba:
            chyby.append(f"{nazev}: {type(chyba).__name__}")

    raise ValueError("; ".join(chyby))


# --- THESPORTSDB ---


def _sportsdb_dotaz(endpoint, parametry, pokusy=3):
    """Zavolá TheSportsDB a vrátí rozparsovanou odpověď."""
    for pokus in range(pokusy):
        odpoved = requests.get(
            f"https://www.thesportsdb.com/api/v1/json/{nastaveni.SPORTSDB_KEY}/{endpoint}",
            params=parametry,
            headers=nastaveni.HTTP_HLAVICKY,
            timeout=20,
        )

        # Testovací klíč má nízký limit dotazů, chvíli počkáme a zkusíme znovu.
        if odpoved.status_code == 429 and pokus < pokusy - 1:
            time.sleep(2.0 * (pokus + 1))
            continue

        odpoved.raise_for_status()
        return odpoved.json() or {}

    raise ValueError("TheSportsDB odmítá další dotazy (limit testovacího klíče).")


def cas_v_praze(zaznam):
    """Převede čas zápasu z TheSportsDB na pražské pásmo.

    ``strTimestamp`` i ``strTime`` jsou UTC. ``strTimeLocal`` u odložených
    zápasů občas zůstane na původním termínu, proto se na něj nespoléháme.
    """
    surove = (zaznam.get("strTimestamp") or "").strip()
    try:
        cas = datetime.fromisoformat(surove)
    except ValueError:
        datum = (zaznam.get("dateEvent") or "").strip()
        hodina = (zaznam.get("strTime") or "00:00:00").strip()
        try:
            cas = datetime.fromisoformat(f"{datum}T{hodina}")
        except ValueError:
            return None

    if cas.tzinfo is None:
        cas = cas.replace(tzinfo=timezone.utc)

    return cas.astimezone(PASMO_PRAHA)


def formatuj_vykop(cas):
    """Čitelné datum a čas výkopu pro UI i Telegram."""
    if cas is None:
        return ""
    return cas.strftime("%d.%m.%Y %H:%M")


# Zápas posunutý o tolik dní od zbytku kola se bere jako přeložený.
DNU_PRO_PRELOZENI = 5


def oznac_prelozene(zapasy):
    """Označí zápasy, jejichž termín leží mimo zbytek kola.

    TheSportsDB u přeložených zápasů často nechá strPostponed = no, jen
    posune datum o týdny. Bez tohohle by 4. kolo tvářilo Brno–Hradec
    na 2. 9. jako běžný víkendový zápas.
    """
    casy = [z["cas"] for z in zapasy if z.get("cas")]
    if len(casy) < 3:
        return zapasy

    serazene = sorted(casy)
    stred = serazene[len(serazene) // 2]

    for zapas in zapasy:
        cas = zapas.get("cas")
        if cas is None:
            continue
        if abs((cas - stred).days) < DNU_PRO_PRELOZENI:
            continue
        if zapas["stav"] == modely.ODEHRANO or zapas["stav"].startswith("🔴"):
            continue
        zapas["stav"] = "🔴 Odloženo"
        zapas["poznamka_termin"] = f"Přeloženo na {formatuj_vykop(cas)}"

    return zapasy


def _preved_zapas(zaznam):
    """Převede záznam z TheSportsDB na strukturu používanou aplikací."""
    if (zaznam.get("strPostponed") or "no").lower() == "yes":
        stav = "🔴 Odloženo"
    else:
        stav = STAVY_API.get(zaznam.get("strStatus"), "🕒 Nadcházející")

    domaci_g = zaznam.get("intHomeScore")
    hoste_g = zaznam.get("intAwayScore")
    if domaci_g is not None and hoste_g is not None:
        skore = f"{domaci_g}:{hoste_g}"
        # Skóre je vyplněné i u rozehraných zápasů, dokončený musí hlásit i stav.
        if stav == "🕒 Nadcházející":
            stav = "✅ Odehráno"
    else:
        skore = "-"

    cas = cas_v_praze(zaznam)
    datum = formatuj_vykop(cas) if cas else (zaznam.get("dateEvent") or "")

    domaci = zaznam.get("strHomeTeam", "")
    hoste = zaznam.get("strAwayTeam", "")

    return {
        "domaci": MAPA_TYMU.get(domaci, domaci),
        "hoste": MAPA_TYMU.get(hoste, hoste),
        "datum": datum,
        "cas": cas,
        "stav": stav,
        "skore": skore,
        "tip": "",
        "id_domaci": zaznam.get("idHomeTeam"),
        "id_hoste": zaznam.get("idAwayTeam"),
    }


@_cachovane(1800)
def zjisti_aktualni_kolo():
    """Číslo kola nejbližšího nesehraného zápasu."""
    data = _sportsdb_dotaz("eventsnextleague.php", {"id": nastaveni.SPORTSDB_LIGA})
    for zaznam in data.get("events") or []:
        if zaznam.get("intRound"):
            return int(zaznam["intRound"])

    raise ValueError("Nepodařilo se určit aktuální kolo.")


@_cachovane(1800)
def nacti_kolo(cislo_kola):
    """Stáhne všechny zápasy jednoho kola."""
    data = _sportsdb_dotaz(
        "eventsround.php",
        {
            "id": nastaveni.SPORTSDB_LIGA,
            "r": cislo_kola,
            "s": nastaveni.SEZONA_SPORTSDB,
        },
    )

    zapasy = [_preved_zapas(z) for z in (data.get("events") or [])]
    zapasy = oznac_prelozene(zapasy)
    zapasy.sort(key=lambda z: z.get("cas") or datetime.min.replace(tzinfo=PASMO_PRAHA))
    return zapasy


@_cachovane(3600)
def nacti_historii_sezony(aktualni_kolo):
    """Stáhne všechna dosud odehraná kola – podklad pro Poissona a Elo.

    Endpoint pro celou sezónu je na testovacím klíči oříznutý, po kolech
    ale chodí kompletní.
    """
    kola = {}

    for cislo_kola in range(1, aktualni_kolo + 1):
        try:
            zapasy = nacti_kolo(cislo_kola)
        except Exception:
            # Jedno chybějící kolo modely nepoloží, jen o něco zpřesní méně.
            continue
        if zapasy:
            kola[cislo_kola] = zapasy

    if not kola:
        raise ValueError("Nepodařilo se stáhnout žádné odehrané kolo.")

    return kola


@_cachovane(1800)
def nacti_tabulku_a_formu_sportsdb():
    """Syrová tabulka i forma z API, bez ohledu na úplnost."""
    data = _sportsdb_dotaz(
        "lookuptable.php",
        {"l": nastaveni.SPORTSDB_LIGA, "s": nastaveni.SEZONA_SPORTSDB},
    )

    radky, forma = [], {}
    for zaznam in data.get("table") or []:
        nazev = zaznam.get("strTeam", "")
        tym = MAPA_TYMU.get(nazev, nazev)

        radky.append(
            {
                "Tým": tym,
                "B": int(zaznam.get("intPoints") or 0),
                "Z": int(zaznam.get("intPlayed") or 0),
                "V": int(zaznam.get("intWin") or 0),
                "R": int(zaznam.get("intDraw") or 0),
                "P": int(zaznam.get("intLoss") or 0),
                "Skóre": f"{zaznam.get('intGoalsFor') or 0}:{zaznam.get('intGoalsAgainst') or 0}",
            }
        )

        # strForm chodí jako "WWDL" od nejstaršího po nejnovější.
        preklad = {"W": "V", "D": "R", "L": "P"}
        forma[tym] = [preklad[z] for z in (zaznam.get("strForm") or "") if z in preklad]

    return radky, forma


def nacti_tabulku_sportsdb():
    """Ligová tabulka z API, jen pokud je kompletní."""
    radky, forma = nacti_tabulku_a_formu_sportsdb()

    if not radky:
        raise ValueError("Tabulka z TheSportsDB je prázdná.")

    # Testovací klíč vrací jen prvních pár řádků, takový výstup je k ničemu.
    if len(radky) < nastaveni.MIN_TYMU_V_TABULCE:
        raise ValueError(
            f"API vrátilo jen {len(radky)} týmů – testovací klíč ořezává výstup"
        )

    df = pd.DataFrame(radky)
    df.index = df.index + 1
    return df, forma


@_cachovane(3600)
def nacti_pohary_tymu(id_tymu):
    """Zápasy týmu mimo ligu – z nich se odvozuje pohárová únava."""
    pohary = []

    for endpoint in ("eventslast.php", "eventsnext.php"):
        data = _sportsdb_dotaz(endpoint, {"id": id_tymu})
        zaznamy = data.get("results") or data.get("events") or []

        for zaznam in zaznamy:
            if zaznam.get("idLeague") == nastaveni.SPORTSDB_LIGA:
                continue

            cas = cas_v_praze(zaznam)
            if cas is None:
                continue

            nazev_souteze = zaznam.get("strLeague", "")
            pohary.append(
                {
                    "cas": cas,
                    "soutez": nazev_souteze,
                    "evropsky": "uefa" in nazev_souteze.lower(),
                    "doma": zaznam.get("idHomeTeam") == id_tymu,
                }
            )

    return pohary


# --- POSKLÁDÁNÍ VŠECH PODKLADŮ ---


def zjisti_vychozi_unavu(tym, zapas, id_tymu_v_lize):
    """Předvyplní stupeň únavy podle posledního pohárového zápasu týmu.

    Vrací (stupeň, poznámka). Neprázdná poznámka znamená, že se únavu
    nepodařilo zjistit – hodnota je jen výchozí, ne ověřená.
    """
    # U dohraných zápasů nemá smysl utrácet dotazy z limitu API.
    if zapas.get("stav", "").startswith("✅"):
        return "Bez pohárů", ""

    id_tymu = id_tymu_v_lize.get(tym)
    cas_zapasu = zapas.get("cas")
    if not id_tymu or cas_zapasu is None:
        return "Bez pohárů", "Tým není v živém rozpisu, nastav únavu ručně."

    try:
        return modely.odvod_unavu(nacti_pohary_tymu(id_tymu), cas_zapasu), ""
    except Exception as chyba:
        return "Bez pohárů", f"Poháry se nepodařilo načíst ({chyba}), nastav únavu ručně."


def nacti_podklady(pocet_kol=None):
    """Stáhne rozpis, tabulku, formu a historii a vrátí je pohromadě.

    Nikdy nevyhodí výjimku kvůli výpadku zdroje – vždycky se dopracuje
    aspoň ke statické záloze. Popisy zdrojů se vracejí spolu s daty,
    aby aplikace mohla ukázat, odkud čísla pocházejí.
    """
    if pocet_kol is None:
        pocet_kol = nastaveni.POCET_ZOBRAZENYCH_KOL

    databaze_kol = {kolo: list(zapasy) for kolo, zapasy in staticka_data.STATICKA_DATABAZE.items()}
    ziva_kola = []
    id_tymu_v_lize = {}

    # 1) Živý rozpis. Odložené zápasy se propíšou samy (strPostponed = yes).
    try:
        aktualni_kolo = zjisti_aktualni_kolo()

        for cislo_kola in range(aktualni_kolo, aktualni_kolo + pocet_kol):
            zapasy_kola = nacti_kolo(cislo_kola)
            if zapasy_kola:
                databaze_kol[cislo_kola] = zapasy_kola
                ziva_kola.append(cislo_kola)

        if ziva_kola:
            popis = ", ".join(f"{k}." for k in ziva_kola)
            zapasy_zdroj = (
                f"✅ Živý rozpis z TheSportsDB (kolo {popis}, {nastaveni.SEZONA_SPORTSDB})"
            )
        else:
            zapasy_zdroj = "📁 Statická databáze v kódu (API nevrátilo žádné zápasy)"
    except Exception as chyba_api:
        zapasy_zdroj = f"⚠️ TheSportsDB selhalo ({chyba_api}), běží statická databáze"

    for cislo_kola in ziva_kola:
        for zapas in databaze_kol[cislo_kola]:
            if zapas.get("id_domaci"):
                id_tymu_v_lize[zapas["domaci"]] = zapas["id_domaci"]
            if zapas.get("id_hoste"):
                id_tymu_v_lize[zapas["hoste"]] = zapas["id_hoste"]

    # 2) Tabulka: API -> web -> dopočet z výsledků -> statická záloha.
    forma_tymu = {}
    try:
        df_tabulka, forma_tymu = nacti_tabulku_sportsdb()
        tabulka_zdroj = f"✅ Živá tabulka z TheSportsDB ({nastaveni.SEZONA_SPORTSDB})"
    except Exception as chyba_api_tabulky:
        try:
            df_tabulka, zdroj_nazev = nacti_ligovou_tabulku_z_webu()
            tabulka_zdroj = f"✅ Živá tabulka z {zdroj_nazev} ({chyba_api_tabulky})"
        except Exception as chyba_web:
            vsechny_tymy = {
                tym
                for zapasy in databaze_kol.values()
                for zapas in zapasy
                for tym in (zapas["domaci"], zapas["hoste"])
            }
            df_tabulka = modely.vypocitej_tabulku_z_vysledku(databaze_kol, vsechny_tymy)
            if df_tabulka is not None:
                tabulka_zdroj = f"📊 Tabulka dopočítaná z výsledků v databázi ({chyba_web})"
            else:
                df_tabulka = staticka_data.zalohova_tabulka()
                tabulka_zdroj = f"❌ Běží statická záloha ({chyba_web})"

        # I neúplná odpověď z API nese formu, kterou stojí za to použít.
        try:
            _, forma_tymu = nacti_tabulku_a_formu_sportsdb()
        except Exception:
            forma_tymu = {}

    # 3) Historie celé sezóny pro Poissona a Elo.
    historie_zdroj = ""
    historie_kol = dict(databaze_kol)

    # Výsledky do logu se smí doplňovat jen z ověřených dat. Statická databáze
    # v kódu by mohla nést překlep a nastálo pokazit měření přesnosti.
    historie_je_ziva = False

    if ziva_kola:
        try:
            historie_kol = nacti_historii_sezony(min(ziva_kola))
            historie_je_ziva = True
            historie_zdroj = (
                f"✅ Historie sezóny z TheSportsDB "
                f"({len(modely.odehrane_zapasy(historie_kol))} odehraných zápasů)"
            )
        except Exception as chyba_historie:
            historie_zdroj = (
                f"⚠️ Historii nešlo načíst ({chyba_historie}), počítá se ze zobrazených kol"
            )

    return {
        "databaze_kol": databaze_kol,
        "ziva_kola": ziva_kola,
        "id_tymu_v_lize": id_tymu_v_lize,
        "tabulka": df_tabulka,
        "forma": forma_tymu,
        "historie_kol": historie_kol,
        "historie_je_ziva": historie_je_ziva,
        "zapasy_zdroj": zapasy_zdroj,
        "tabulka_zdroj": tabulka_zdroj,
        "historie_zdroj": historie_zdroj,
    }


def spocitej_sily(podklady):
    """Z podkladů odvodí vstupy všech tří modelů."""
    databaze_kol = podklady["databaze_kol"]
    forma_tymu = dict(podklady["forma"])

    # Týmy, ke kterým API formu nedalo, se dopočítají z odehraných zápasů.
    for tym, forma in modely.spocitej_formu(databaze_kol).items():
        if not forma_tymu.get(tym):
            forma_tymu[tym] = forma

    # Index síly se počítá z tabulky, takže se hýbe s každým odehraným kolem.
    indexy_sily = modely.spocitej_index_sily(podklady["tabulka"])

    # Aktuální forma index posune o ±4 body.
    for tym, forma in forma_tymu.items():
        if tym in indexy_sily:
            indexy_sily[tym] = round(indexy_sily[tym] + modely.bonus_za_formu(forma), 1)

    sily_golu, prumer_domaci, prumer_hoste = modely.spocitej_utok_obranu(
        podklady["historie_kol"]
    )

    return {
        "indexy_sily": indexy_sily,
        "forma": forma_tymu,
        "sily_golu": sily_golu,
        "prumer_domaci": prumer_domaci,
        "prumer_hoste": prumer_hoste,
        "elo": modely.spocitej_elo(podklady["historie_kol"]),
    }
