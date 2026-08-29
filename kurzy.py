"""Porovnání předpovědi s kurzem sázkové kanceláře.

Vysoká úspěšnost tipu a výdělek jsou dvě různé věci. Dvojitá šance vyjde
v 78 % případů, jenže při kurzu 1.25 je to dlouhodobě ztráta. Co rozhoduje,
je rozdíl mezi pravděpodobností modelu a tou, kterou nabízí kurz.

Kurz v sobě nese marži kanceláře – součet převrácených hodnot 1/X/2 vyjde
kolem 1.05 až 1.08 místo jedničky. Marže se musí odečíst, jinak by model
vypadal, že má výhodu, i když jen počítá to samé co kancelář.

Kurzy musí pocházet ze sázkovky. Aplikace je umí stáhnout z Tipsportu
nebo z API-Football (viz ``kurz_zdroje.py``) a uložit do CSV vedle sebe.

CSV na disku Streamlit Cloudu po restartu zmizí – trvalou zálohu řeší
``uloziste.py`` (prohlížeč a volitelně větev ``data`` na GitHubu).
Ruční opsání zůstává jako záloha, když trh nejde stáhnout.
"""

import os
from datetime import datetime

import pandas as pd

SOUBOR_KURZU = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kurzy.csv")

SLOUPCE = [
    "kolo",
    "domaci",
    "hoste",
    "kurz_1",
    "kurz_0",
    "kurz_2",
    "zdroj",
    "sazkovka",
    "zapsano",
]

# Model není přesnější než trh, takže drobný rozdíl je šum, ne příležitost.
# Pod touhle výhodou se sázka nedoporučuje.
MIN_HODNOTA = 0.05

# Kelly říká, jakou část banku vsadit při dané výhodě. Plný Kelly je na
# odhadnuté pravděpodobnosti moc agresivní, běžně se hraje jeho zlomek.
PODIL_KELLYHO = 0.25

# Kurz pod 1.01 je překlep, nad 100 taky.
MIN_KURZ = 1.01
MAX_KURZ = 100.0

NAZVY_VYSLEDKU = ("1", "0", "2")


def platny_kurz(kurz):
    """Ověří, že jde o smysluplný desetinný kurz."""
    try:
        hodnota = float(kurz)
    except (TypeError, ValueError):
        return False

    return MIN_KURZ <= hodnota <= MAX_KURZ


def implikovane_pravdepodobnosti(kurz_1, kurz_0, kurz_2):
    """Převrácené hodnoty kurzů – včetně marže, takže dají víc než jedničku."""
    return tuple(1.0 / float(kurz) for kurz in (kurz_1, kurz_0, kurz_2))


def marze(kurz_1, kurz_0, kurz_2):
    """O kolik procent je kniha přesazená ve prospěch kanceláře."""
    return sum(implikovane_pravdepodobnosti(kurz_1, kurz_0, kurz_2)) - 1.0


def ocisti_marzi(kurz_1, kurz_0, kurz_2):
    """Pravděpodobnosti trhu po odečtení marže.

    Dělí se poměrově, což je nejjednodušší způsob. Mírně nadhodnocuje
    favority – kanceláře marži rozpouštějí spíš do vysokých kurzů – ale
    na rozdíl od složitějších metod nemá co rozbít.
    """
    syrove = implikovane_pravdepodobnosti(kurz_1, kurz_0, kurz_2)
    soucet = sum(syrove)

    if soucet <= 0:
        return (1 / 3, 1 / 3, 1 / 3)

    return tuple(hodnota / soucet for hodnota in syrove)


def hodnota_sazky(pravdepodobnost, kurz):
    """Očekávaný výnos na jednu vsazenou korunu.

    Nula je nulový součet, 0.08 znamená osm haléřů zisku na korunu.
    """
    return float(pravdepodobnost) * float(kurz) - 1.0


def kelly(pravdepodobnost, kurz, podil=PODIL_KELLYHO):
    """Doporučená část banku podle zlomkového Kellyho vzorce."""
    cisty_zisk = float(kurz) - 1.0
    if cisty_zisk <= 0:
        return 0.0

    plny = hodnota_sazky(pravdepodobnost, kurz) / cisty_zisk
    return max(plny, 0.0) * podil


def prehled_hodnoty(pravdepodobnosti_modelu, kurzy):
    """Pro každý výsledek porovná model s trhem.

    ``pravdepodobnosti_modelu`` i ``kurzy`` jsou trojice v pořadí 1, X, 2.
    """
    trzni = ocisti_marzi(*kurzy)

    return [
        {
            "vysledek": nazev,
            "model": p_model,
            "trh": p_trh,
            "kurz": float(kurz),
            "hodnota": hodnota_sazky(p_model, kurz),
            "kelly": kelly(p_model, kurz),
        }
        for nazev, p_model, p_trh, kurz in zip(
            NAZVY_VYSLEDKU, pravdepodobnosti_modelu, trzni, kurzy
        )
    ]


def nejlepsi_hodnota(pravdepodobnosti_modelu, kurzy, min_hodnota=MIN_HODNOTA):
    """Výsledek s nejvyšší očekávanou hodnotou, pokud nějaký překročí práh."""
    nejlepsi = max(
        prehled_hodnoty(pravdepodobnosti_modelu, kurzy), key=lambda r: r["hodnota"]
    )

    return nejlepsi if nejlepsi["hodnota"] >= min_hodnota else None


def rozdil_od_trhu(pravdepodobnosti_modelu, kurzy):
    """Jak daleko je model od trhu – součet absolutních odchylek.

    Velký rozdíl neznamená výhodu, ale spíš že se model plete. Trh má
    k dispozici sestavy i sázkařské peníze, model jen výsledky a góly.
    """
    trzni = ocisti_marzi(*kurzy)
    return sum(abs(p - t) for p, t in zip(pravdepodobnosti_modelu, trzni))


# --- ULOŽENÉ KURZY ---


def nacti_tabulku(cesta=SOUBOR_KURZU):
    """Načte uložené kurzy; když soubor není, vrátí prázdnou tabulku."""
    if not os.path.exists(cesta):
        return pd.DataFrame(columns=SLOUPCE)

    try:
        df = pd.read_csv(cesta)
    except (pd.errors.EmptyDataError, OSError):
        return pd.DataFrame(columns=SLOUPCE)

    for sloupec in SLOUPCE:
        if sloupec not in df.columns:
            df[sloupec] = None

    return df[SLOUPCE]


def nacti_kurzy(cesta=SOUBOR_KURZU):
    """Uložené kurzy jako (kolo, domácí, hosté) -> trojice kurzů."""
    df = nacti_tabulku(cesta)
    ulozene = {}

    for _, radek in df.iterrows():
        trojice = (radek["kurz_1"], radek["kurz_0"], radek["kurz_2"])
        if not all(platny_kurz(kurz) for kurz in trojice):
            continue

        try:
            klic = (int(radek["kolo"]), str(radek["domaci"]), str(radek["hoste"]))
        except (TypeError, ValueError):
            continue

        ulozene[klic] = tuple(float(kurz) for kurz in trojice)

    return ulozene


def nacti_kurzy_info(cesta=SOUBOR_KURZU):
    """Uložené kurzy včetně zdroje: (kolo, domácí, hosté) -> dict."""
    df = nacti_tabulku(cesta)
    ulozene = {}

    for _, radek in df.iterrows():
        trojice = (radek["kurz_1"], radek["kurz_0"], radek["kurz_2"])
        if not all(platny_kurz(kurz) for kurz in trojice):
            continue

        try:
            klic = (int(radek["kolo"]), str(radek["domaci"]), str(radek["hoste"]))
        except (TypeError, ValueError):
            continue

        ulozene[klic] = {
            "kurzy": tuple(float(kurz) for kurz in trojice),
            "zdroj": "" if pd.isna(radek.get("zdroj")) else str(radek.get("zdroj") or ""),
            "sazkovka": (
                ""
                if pd.isna(radek.get("sazkovka"))
                else str(radek.get("sazkovka") or "")
            ),
        }

    return ulozene


def uloz_kurz(
    kolo,
    domaci,
    hoste,
    kurzy,
    cesta=SOUBOR_KURZU,
    cas=None,
    zdroj="",
    sazkovka="",
):
    """Zapíše nebo přepíše kurzy jednoho zápasu.

    Na rozdíl od predikcí se kurzy přepisovat **musí** – hýbou se až do
    výkopu a zajímá nás ten, za který se dá vsadit teď.
    """
    if not all(platny_kurz(kurz) for kurz in kurzy):
        raise ValueError(f"Neplatné kurzy: {kurzy}")

    df = nacti_tabulku(cesta)
    stejny = (
        (df["kolo"].astype(str) == str(kolo))
        & (df["domaci"] == domaci)
        & (df["hoste"] == hoste)
    )
    df = df[~stejny] if not df.empty else df

    novy = pd.DataFrame(
        [
            {
                "kolo": kolo,
                "domaci": domaci,
                "hoste": hoste,
                "kurz_1": float(kurzy[0]),
                "kurz_0": float(kurzy[1]),
                "kurz_2": float(kurzy[2]),
                "zdroj": zdroj or "",
                "sazkovka": sazkovka or "",
                "zapsano": (cas or datetime.now()).strftime("%Y-%m-%d %H:%M"),
            }
        ]
    )

    vysledek = pd.concat([df, novy], ignore_index=True)
    return uloz_tabulku(vysledek, cesta)


def parsuj_cas(hodnota):
    """Čas zapsání kurzu; chybějící nebo divný údaj je nejstarší možný."""
    if hodnota is None or (isinstance(hodnota, float) and pd.isna(hodnota)):
        return datetime.min
    if isinstance(hodnota, datetime):
        return hodnota
    text = str(hodnota).strip()
    if not text or text.lower() == "nan":
        return datetime.min
    for format_casu in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, format_casu)
        except ValueError:
            continue
    return datetime.min


def kurzy_se_lisi(stare, nove):
    """True, když trojice 1/X/2 není stejná (včetně chybějící proti vyplněné)."""
    if stare is None and nove is None:
        return False
    if stare is None or nove is None:
        return True
    try:
        return any(abs(float(a) - float(b)) > 0.001 for a, b in zip(stare, nove))
    except (TypeError, ValueError):
        return True


def radek_z_serie(radek):
    """Jeden zápas z tabulky, nebo None když kurzy nedávají smysl."""
    trojice = (radek["kurz_1"], radek["kurz_0"], radek["kurz_2"])
    if not all(platny_kurz(kurz) for kurz in trojice):
        return None
    try:
        kolo = int(radek["kolo"])
    except (TypeError, ValueError):
        return None
    return {
        "kolo": kolo,
        "domaci": str(radek["domaci"]),
        "hoste": str(radek["hoste"]),
        "kurz_1": float(trojice[0]),
        "kurz_0": float(trojice[1]),
        "kurz_2": float(trojice[2]),
        "zdroj": "" if pd.isna(radek.get("zdroj")) else str(radek.get("zdroj") or ""),
        "sazkovka": (
            "" if pd.isna(radek.get("sazkovka")) else str(radek.get("sazkovka") or "")
        ),
        "zapsano": (
            "" if pd.isna(radek.get("zapsano")) else str(radek.get("zapsano") or "")
        ),
    }


def tabulka_na_zaznamy(df):
    """Tabulka kurzů jako seznam slovníků – pro JSON zálohu."""
    if df is None or df.empty:
        return []
    zaznamy = []
    for _, radek in df.iterrows():
        prevedeny = radek_z_serie(radek)
        if prevedeny:
            zaznamy.append(prevedeny)
    return zaznamy


def tabulka_z_zaznamu(zaznamy):
    """JSON záloha zpět na tabulku."""
    if not zaznamy:
        return pd.DataFrame(columns=SLOUPCE)
    radky = []
    for zaznam in zaznamy:
        try:
            radek = radek_z_serie(
                {
                    "kolo": zaznam["kolo"],
                    "domaci": zaznam["domaci"],
                    "hoste": zaznam["hoste"],
                    "kurz_1": zaznam["kurz_1"],
                    "kurz_0": zaznam["kurz_0"],
                    "kurz_2": zaznam["kurz_2"],
                    "zdroj": zaznam.get("zdroj", ""),
                    "sazkovka": zaznam.get("sazkovka", ""),
                    "zapsano": zaznam.get("zapsano", ""),
                }
            )
        except (KeyError, TypeError):
            continue
        if radek:
            radky.append(radek)
    if not radky:
        return pd.DataFrame(columns=SLOUPCE)
    return pd.DataFrame(radky)[SLOUPCE]


def uloz_tabulku(df, cesta=SOUBOR_KURZU):
    """Přepíše CSV tabulkou kurzů. Prázdná tabulka zapíše jen hlavičku."""
    if df is None or df.empty:
        vysledek = pd.DataFrame(columns=SLOUPCE)
    else:
        vysledek = df[SLOUPCE].copy()
    adresar = os.path.dirname(os.path.abspath(cesta))
    if adresar:
        os.makedirs(adresar, exist_ok=True)
    vysledek.to_csv(cesta, index=False, encoding="utf-8")
    return vysledek


def sluc_tabulky(*tabulky):
    """Sloučí tabulky. Stejný zápas vyhraje ten s novějším časem zápisu."""
    nejnovejsi = {}
    for df in tabulky:
        if df is None or getattr(df, "empty", True):
            continue
        for _, radek in df.iterrows():
            prevedeny = radek_z_serie(radek)
            if not prevedeny:
                continue
            klic = (prevedeny["kolo"], prevedeny["domaci"], prevedeny["hoste"])
            stary = nejnovejsi.get(klic)
            if stary is None or parsuj_cas(prevedeny["zapsano"]) >= parsuj_cas(
                stary["zapsano"]
            ):
                nejnovejsi[klic] = prevedeny
    if not nejnovejsi:
        return pd.DataFrame(columns=SLOUPCE)
    return pd.DataFrame(list(nejnovejsi.values()))[SLOUPCE]


def sluc_do_souboru(df_nove, cesta=SOUBOR_KURZU):
    """Doplní CSV o nové/novější kurzy a soubor uloží."""
    sloucene = sluc_tabulky(nacti_tabulku(cesta), df_nove)
    return uloz_tabulku(sloucene, cesta)
