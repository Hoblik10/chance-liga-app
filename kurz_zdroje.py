"""Živé kurzy 1/X/2 z trhu, ne z modelu.

Aplikace je potřebuje k tomu, aby šlo spočítat hodnotu sázky. Férový kurz
``1 / pravděpodobnost modelu`` k tomu nepatří – to je jen převrácená predikce,
takže proti němu model nikdy nemá výhodu.

Zdroje, v tomto pořadí:

1. **Tipsport** – veřejný JSON, který používá jejich web. Žádný klíč.
   Z cloudu (Streamlit Cloud, GitHub, datacentrum) často vrátí 403, protože
   Cloudflare pouští spíš prohlížeč než server. Z domácího počítače to
   občas projde.
2. **API-Football** – oficiální REST, Chance Liga má id 134. Klíč zdarma
   (100 dotazů denně) na dashboard.api-football.com. Kurzy jsou od
   sázkovek jako Bet365 nebo Unibet, **ne od Tipsportu**.

Ani jeden zdroj neumisťuje sázku a nepřihlašuje se k účtu.
"""

import re
import unicodedata
from datetime import datetime

import requests

import data
import kurzy
import nastaveni

TIPSPORT_SOUTEZ = 20  # Chance Liga v URL .../cesko-chance-liga-20
API_FOOTBALL_LIGA = 134
API_FOOTBALL_BET_1X2 = 1

# Pořadí, v jakém se bere sazkovka z API-Football. Pinnacle má nízkou marži,
# ale vsadit v Česku na něj nejde; Bet365 je kompromis mezi dostupností
# a tím, že kurz opravdu existuje.
PORADI_SAZKOVEK = (
    "Bet365",
    "Unibet",
    "1xBet",
    "William Hill",
    "Pinnacle",
    "Bwin",
)

NAZVY_1 = {"1", "home", "domaci", "domácí", "home win", "výhra domácích"}
NAZVY_X = {"x", "0", "draw", "remiza", "remíza", "draw no bet"}
NAZVY_2 = {"2", "away", "hoste", "hosté", "away win", "výhra hostů"}


# --- NÁZVY TÝMŮ ---


def _klic_tymu(nazev):
    """Porovnávací tvar: bez diakritiky, prefixu klubu a slova Praha."""
    text = data.nazev_tymu(nazev)
    slozeny = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(znak for znak in slozeny if not unicodedata.combining(znak)).lower()
    text = text.replace("-", " ").replace(".", " ")
    text = re.sub(r"\b1905\b", " ", text)
    text = re.sub(r"\bpraha\b|\bprague\b", " ", text)
    text = " ".join(text.split())
    for predpona in ("1 fc ", "sk ", "ac ", "fk ", "fc ", "mfk "):
        if text.startswith(predpona):
            text = text[len(predpona) :]
            break
    return " ".join(text.split())


def _znamici_tymy():
    return sorted(set(data.MAPA_TYMU.values()))


def kanonicky_tym(surovy, znami=None):
    """Převede název ze sázkovky na kanonický název z aplikace, nebo None."""
    znami = list(znami) if znami is not None else _znamici_tymy()
    presne = data.nazev_tymu(surovy)
    if presne in znami:
        return presne

    klic = _klic_tymu(surovy)
    if not klic:
        return None

    podle_klice = { _klic_tymu(tym): tym for tym in znami }
    if klic in podle_klice:
        return podle_klice[klic]

    kandidati = [
        tym
        for klic_tymu, tym in podle_klice.items()
        if klic == klic_tymu or klic in klic_tymu.split() or klic_tymu in klic
    ]
    # „brno“ sedí na Zbrojovku i Artis – bereme jen jednoznačnou shodu.
    if len(kandidati) == 1:
        return kandidati[0]
    return None


def _trojice_z_nazvu(prilezitosti):
    """Z mapy název→kurz vytáhne 1/X/2, jinak None."""
    nalezeno = {}
    for nazev, kurz in prilezitosti.items():
        klic = str(nazev or "").strip().lower()
        if klic in NAZVY_1:
            nalezeno["1"] = kurz
        elif klic in NAZVY_X:
            nalezeno["X"] = kurz
        elif klic in NAZVY_2:
            nalezeno["2"] = kurz

    if set(nalezeno) != {"1", "X", "2"}:
        return None
    if not all(kurzy.platny_kurz(nalezeno[klic]) for klic in ("1", "X", "2")):
        return None
    return (
        float(nalezeno["1"]),
        float(nalezeno["X"]),
        float(nalezeno["2"]),
    )


def _kurz_z_polozky(polozka):
    if not isinstance(polozka, dict):
        return None
    for klic in ("odd", "currentOdd", "opportunityRate", "rate", "value"):
        if klic in polozka and kurzy.platny_kurz(polozka[klic]):
            return float(polozka[klic])
    return None


def _prilezitosti_z_uzlu(uzel):
    """Sběr názvů příležitostí z jednoho JSON objektu zápasu."""
    mapa = {}

    def pridej(nazev, kurz):
        if nazev is None or kurz is None:
            return
        mapa[str(nazev)] = kurz

    for klic in ("odds", "opportunities", "matchOpportunities"):
        for polozka in uzel.get(klic) or []:
            if not isinstance(polozka, dict):
                continue
            nazev = (
                polozka.get("opportunityName")
                or polozka.get("name")
                or polozka.get("title")
                or polozka.get("opportName")
            )
            pridej(nazev, _kurz_z_polozky(polozka))

    for klic, nazev in (("odd1", "1"), ("oddX", "X"), ("odd0", "X"), ("odd2", "2")):
        if klic in uzel:
            pridej(nazev, uzel.get(klic) if kurzy.platny_kurz(uzel.get(klic)) else None)

    # Tabulky nabídek (event tables) – buňky 1 / X / 2 v prvním boxu.
    for tabulka in uzel.get("eventTables") or uzel.get("boxes") or []:
        if not isinstance(tabulka, dict):
            continue
        for box in tabulka.get("boxes") or [tabulka]:
            if not isinstance(box, dict):
                continue
            for bunka in box.get("cells") or []:
                if not isinstance(bunka, dict):
                    continue
                pridej(bunka.get("name") or bunka.get("oppNumber"), _kurz_z_polozky(bunka))

    return mapa


def _nazvy_souperu(uzel):
    for klic_d, klic_h in (
        ("opp1", "opp2"),
        ("opponent1", "opponent2"),
        ("homeName", "awayName"),
        ("participant1", "participant2"),
    ):
        if uzel.get(klic_d) and uzel.get(klic_h):
            return str(uzel[klic_d]), str(uzel[klic_h])

    for klic in ("name", "matchName", "title"):
        text = uzel.get(klic)
        if not text or " - " not in str(text):
            continue
        leva, prava = str(text).split(" - ", 1)
        if leva.strip() and prava.strip():
            return leva.strip(), prava.strip()
    return None


def zapasy_z_nabidky(koren, zdroj, sazkovka):
    """Rekurzivně vytáhne zápasy s platnou trojicí 1/X/2."""
    nalezene = []

    def prohledej(uzel):
        if isinstance(uzel, dict):
            souperi = _nazvy_souperu(uzel)
            trojice = _trojice_z_nazvu(_prilezitosti_z_uzlu(uzel))
            if souperi and trojice:
                nalezene.append(
                    {
                        "domaci_surove": souperi[0],
                        "hoste_surove": souperi[1],
                        "kurzy": trojice,
                        "zdroj": zdroj,
                        "sazkovka": sazkovka,
                    }
                )
            for hodnota in uzel.values():
                prohledej(hodnota)
        elif isinstance(uzel, list):
            for hodnota in uzel:
                prohledej(hodnota)

    prohledej(koren)
    return nalezene


def sparuj_nabidku(nabidka, zapasy):
    """Přiřadí stažené kurzy k zápasům aplikace podle názvů týmů."""
    znami = [z["domaci"] for z in zapasy] + [z["hoste"] for z in zapasy]
    znami = sorted(set(znami))
    parovane = {}
    pouzite = set()

    for zapas in zapasy:
        klic_zapasu = (zapas["domaci"], zapas["hoste"])
        for index, nabidnuty in enumerate(nabidka):
            if index in pouzite:
                continue
            domaci = kanonicky_tym(nabidnuty["domaci_surove"], znami)
            hoste = kanonicky_tym(nabidnuty["hoste_surove"], znami)
            if domaci == zapas["domaci"] and hoste == zapas["hoste"]:
                parovane[klic_zapasu] = nabidnuty
                pouzite.add(index)
                break

    return parovane


# --- TIPSPORT ---


def _tipsport_hlavicky():
    return {
        **nastaveni.HTTP_HLAVICKY,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "cs,en;q=0.9",
        "Referer": "https://www.tipsport.cz/kurzy/fotbal/cesko-chance-liga-20",
        "Origin": "https://www.tipsport.cz",
        "X-Requested-With": "XMLHttpRequest",
    }


def _tipsport_session():
    session = requests.Session()
    session.headers.update(_tipsport_hlavicky())
    # Cloudflare si často nechá cookies z homepage. Když to neprojde,
    # další REST stejně dostane 403 a volající to uvidí jako srozumitelnou chybu.
    try:
        session.get("https://www.tipsport.cz/", timeout=15)
    except requests.RequestException:
        pass
    return session


def _chyba_tipsportu(odpoved):
    if odpoved.status_code == 403:
        return (
            "Tipsport z tohohle serveru nepustil (HTTP 403, Cloudflare). "
            "Z cloudu to typicky nejde; z tvého počítače občas ano. "
            "Jinak použij klíč API-Football, nebo kurzy opiš ručně."
        )
    if odpoved.status_code == 429:
        return "Tipsport teď odmítá další dotazy (limit). Zkus to za chvíli."
    return f"Tipsport vrátil HTTP {odpoved.status_code}."


def stahni_tipsport(session=None):
    """Stáhne 1/X/2 Chance Ligy z veřejného JSON Tipsportu.

    Vrací ``(seznam_zapasu, chyba)``. Seznam může být prázdný i bez chyby,
    když soutěž zrovna nemá otevřenou nabídku.
    """
    session = session or _tipsport_session()
    url_matches = (
        f"https://www.tipsport.cz/rest/offer/v3/sports/COMPETITION/"
        f"{TIPSPORT_SOUTEZ}/matches"
    )
    try:
        odpoved = session.get(
            url_matches, params={"fromResults": "false"}, timeout=20
        )
    except requests.RequestException as chyba:
        return [], f"Tipsport neodpovídá ({type(chyba).__name__})."

    if odpoved.status_code != 200:
        return [], _chyba_tipsportu(odpoved)

    try:
        telo = odpoved.json()
    except ValueError:
        return [], "Tipsport vrátil odpověď, která není JSON."

    nalezene = zapasy_z_nabidky(telo, "tipsport", "Tipsport")
    if nalezene:
        return nalezene, None

    # Seznam zápasů někdy nese jen názvy. Doplníme kurzy z nabídky soutěže.
    try:
        nabidka = session.post(
            "https://www.tipsport.cz/rest/offer/v2/offer",
            json={
                "results": False,
                "highlightAnyTime": False,
                "limit": 75,
                "type": "COMPETITION",
                "id": TIPSPORT_SOUTEZ,
                "fulltexts": [],
                "matchIds": [],
                "matchViewFilters": [],
            },
            timeout=20,
        )
    except requests.RequestException as chyba:
        return [], f"Tipsport nabídka neodpovídá ({type(chyba).__name__})."

    if nabidka.status_code != 200:
        return [], _chyba_tipsportu(nabidka)

    try:
        nalezene = zapasy_z_nabidky(nabidka.json(), "tipsport", "Tipsport")
    except ValueError:
        return [], "Tipsport vrátil nabídku, která není JSON."

    if not nalezene:
        return [], "Tipsport nabídl zápasy, ale bez kurzů 1/X/2."
    return nalezene, None


# --- API-FOOTBALL ---


def sezonni_rok(sezona=None):
    """API-Football značí sezónu počátečním rokem (2026-2027 → 2026)."""
    text = sezona or nastaveni.SEZONA_SPORTSDB
    try:
        return int(str(text).split("-")[0])
    except (TypeError, ValueError):
        return datetime.now().year


def _api_football_hlavicky(klic):
    return {
        "x-apisports-key": klic,
        "x-rapidapi-key": klic,
        "Accept": "application/json",
    }


def _api_football_get(cesta, parametry, klic, get=None):
    get = get or requests.get
    odpoved = get(
        f"https://v3.football.api-sports.io{cesta}",
        params=parametry,
        headers=_api_football_hlavicky(klic),
        timeout=20,
    )
    odpoved.raise_for_status()
    telo = odpoved.json() or {}
    chyby = telo.get("errors") or {}
    if chyby:
        raise ValueError(str(chyby))
    return telo.get("response") or []


def _je_sazka_1x2(sazka):
    if sazka.get("id") == API_FOOTBALL_BET_1X2:
        return True
    nazev = str(sazka.get("name") or "").lower()
    return nazev in {"match winner", "1x2", "fulltime result", "win-draw-win"}


def _1x2_z_bookmakeru(bookmakers):
    """Vybere jednu sazkovku a z ní Match Winner."""
    serazene = sorted(
        bookmakers or [],
        key=lambda b: (
            PORADI_SAZKOVEK.index(b.get("name"))
            if b.get("name") in PORADI_SAZKOVEK
            else 99
        ),
    )
    for kniha in serazene:
        for sazka in kniha.get("bets") or []:
            if not _je_sazka_1x2(sazka):
                continue
            prilezitosti = {}
            for hodnota in sazka.get("values") or []:
                popisek = str(hodnota.get("value") or "").strip()
                prilezitosti[popisek] = hodnota.get("odd")
            trojice = _trojice_z_nazvu(prilezitosti)
            if trojice:
                return trojice, kniha.get("name") or "API-Football"
    return None, None


def _tymy_z_fixture(polozka):
    tymy = polozka.get("teams") or {}
    domaci = (tymy.get("home") or {}).get("name")
    hoste = (tymy.get("away") or {}).get("name")
    if domaci and hoste:
        return domaci, hoste
    return None


def zapasy_z_api_football(odds_response, fixtures_response=None):
    """Spojí /odds a /fixtures do stejného tvaru jako Tipsport."""
    fixtures = {
        (polozka.get("fixture") or {}).get("id"): polozka
        for polozka in (fixtures_response or [])
        if (polozka.get("fixture") or {}).get("id") is not None
    }
    nalezene = []
    for polozka in odds_response or []:
        fixture = polozka.get("fixture") or {}
        fixture_id = fixture.get("id")
        souperi = _tymy_z_fixture(polozka) or _tymy_z_fixture(
            fixtures.get(fixture_id) or {}
        )
        if not souperi:
            continue
        trojice, sazkovka = _1x2_z_bookmakeru(polozka.get("bookmakers") or [])
        if not trojice:
            continue
        nalezene.append(
            {
                "domaci_surove": souperi[0],
                "hoste_surove": souperi[1],
                "kurzy": trojice,
                "zdroj": "api-football",
                "sazkovka": sazkovka,
            }
        )
    return nalezene


def stahni_api_football(klic=None, get=None):
    """Stáhne 1/X/2 Chance Ligy z API-Football. Vrací (seznam, chyba)."""
    klic = klic if klic is not None else nastaveni.API_FOOTBALL_KEY
    if not klic:
        return [], (
            "API-Football klíč chybí. Zdarma na dashboard.api-football.com, "
            "pak `API_FOOTBALL_KEY` do secrets. Kurzy ale nebudou z Tipsportu."
        )

    try:
        odds = _api_football_get(
            "/odds",
            {
                "league": API_FOOTBALL_LIGA,
                "season": sezonni_rok(),
                "bet": API_FOOTBALL_BET_1X2,
            },
            klic,
            get=get,
        )
    except requests.HTTPError as chyba:
        kod = getattr(chyba.response, "status_code", None)
        if kod in (401, 403):
            return [], "API-Football klíč odmítlo (neplatný, nebo vyčerpaný limit)."
        if kod == 429:
            return [], "API-Football má teď plný limit (100 dotazů denně na free plánu)."
        return [], f"API-Football vrátilo HTTP {kod}."
    except (requests.RequestException, ValueError) as chyba:
        return [], f"API-Football selhalo ({chyba})."

    if not odds:
        return [], (
            "API-Football na Chance Ligu teď žádné kurzy 1/X/2 nemá. "
            "Na free plánu drží jen zhruba posledních 7 dní a coverage.odds "
            "u ligy musí být zapnuté."
        )

    idcka = [
        str((polozka.get("fixture") or {}).get("id"))
        for polozka in odds
        if (polozka.get("fixture") or {}).get("id")
    ]
    fixtures = []
    if idcka and not any(_tymy_z_fixture(polozka) for polozka in odds):
        try:
            fixtures = _api_football_get(
                "/fixtures",
                {"ids": "-".join(idcka[:20])},
                klic,
                get=get,
            )
        except (requests.RequestException, ValueError):
            fixtures = []

    nalezene = zapasy_z_api_football(odds, fixtures)
    if not nalezene:
        return [], "API-Football vrátilo kurzy, ale bez 1/X/2 nebo názvů týmů."
    return nalezene, None


# --- SPOLEČNÉ NAČTENÍ ---


def nacti_nabidku(session=None, klic=None, get=None):
    """Zkusí Tipsport, při neúspěchu API-Football. Vrací dict s výsledkem."""
    nabidka, chyba_tipsport = stahni_tipsport(session=session)
    if nabidka:
        return {
            "nabidka": nabidka,
            "zdroj": "tipsport",
            "sazkovka": "Tipsport",
            "chyby": [],
        }

    nabidka_api, chyba_api = stahni_api_football(klic=klic, get=get)
    if nabidka_api:
        sazkovky = sorted({z["sazkovka"] for z in nabidka_api})
        return {
            "nabidka": nabidka_api,
            "zdroj": "api-football",
            "sazkovka": ", ".join(sazkovky),
            "chyby": [chyba_tipsport] if chyba_tipsport else [],
        }

    chyby = [zprava for zprava in (chyba_tipsport, chyba_api) if zprava]
    return {"nabidka": [], "zdroj": None, "sazkovka": None, "chyby": chyby}


def uloz_sparovane(kolo, zapasy, parovane, cas=None, cesta=None):
    """Zapíše spárované kurzy do CSV. Vrací počet uložených zápasů."""
    argumenty = {}
    if cesta is not None:
        argumenty["cesta"] = cesta
    if cas is not None:
        argumenty["cas"] = cas

    pocet = 0
    for zapas in zapasy:
        klic = (zapas["domaci"], zapas["hoste"])
        nabidnuty = parovane.get(klic)
        if not nabidnuty:
            continue
        kurzy.uloz_kurz(
            kolo,
            zapas["domaci"],
            zapas["hoste"],
            nabidnuty["kurzy"],
            zdroj=nabidnuty.get("zdroj") or "",
            sazkovka=nabidnuty.get("sazkovka") or "",
            **argumenty,
        )
        pocet += 1
    return pocet


def nacti_a_uloz(kolo, zapasy, session=None, klic=None, get=None, cesta=None, cas=None):
    """Stáhne trh, spáruje se zápasy kola a uloží. Vrací shrnutí pro UI."""
    vysledek = nacti_nabidku(session=session, klic=klic, get=get)
    parovane = sparuj_nabidku(vysledek["nabidka"], zapasy) if vysledek["nabidka"] else {}
    ulozeno = uloz_sparovane(kolo, zapasy, parovane, cas=cas, cesta=cesta)
    nespárováno = len(vysledek["nabidka"]) - ulozeno
    return {
        "ulozeno": ulozeno,
        "nabidnuto": len(vysledek["nabidka"]),
        "nesparovano": max(nespárováno, 0),
        "zdroj": vysledek["zdroj"],
        "sazkovka": vysledek["sazkovka"],
        "chyby": vysledek["chyby"],
    }


def popis_vysledku(shrnuti):
    """Krátká zpráva do Streamlitu."""
    if shrnuti["ulozeno"]:
        text = (
            f"Uloženo {shrnuti['ulozeno']} zápasů z "
            f"{shrnuti['sazkovka'] or shrnuti['zdroj']}."
        )
        if shrnuti["zdroj"] == "api-football":
            text += " To nejsou kurzy Tipsportu."
        if shrnuti["chyby"]:
            text += " Tipsport nešel: " + shrnuti["chyby"][0]
        return text

    if shrnuti["chyby"]:
        return " ".join(shrnuti["chyby"])
    return "Trh teď na Chance Ligu nic 1/X/2 nenabízí."
