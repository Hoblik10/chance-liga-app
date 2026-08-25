"""Ukládání predikcí a jejich zpětné vyhodnocení.

Aby mělo měření přesnosti smysl, musí být predikce zapsaná **před** zápasem
a už se nikdy nepřepsat. Proto se existující řádky nikdy neaktualizují –
doplňuje se do nich jen skutečný výsledek, jakmile je zápas dohraný.

Data leží v jednoduchém CSV vedle aplikace, takže se dají otevřít v Excelu
nebo verzovat.
"""

import os

import pandas as pd

import modely

SOUBOR_ZAZNAMU = os.path.join(os.path.dirname(os.path.abspath(__file__)), "predikce_log.csv")

SLOUPCE = [
    "klic",
    "zapsano",
    "kolo",
    "datum",
    "domaci",
    "hoste",
    "model",
    "p_domaci",
    "p_remiza",
    "p_hoste",
    "tip",
    "skore",
    "vysledek",
]


def klic_zapasu(kolo, domaci, hoste, model):
    """Jednoznačný identifikátor jedné predikce."""
    return f"{kolo}|{domaci}|{hoste}|{model}"


def nacti_zaznamy(cesta=SOUBOR_ZAZNAMU):
    """Načte log predikcí; když neexistuje, vrátí prázdnou tabulku."""
    if not os.path.exists(cesta):
        return pd.DataFrame(columns=SLOUPCE)

    try:
        df = pd.read_csv(cesta, dtype={"klic": str, "skore": str, "vysledek": str})
    except (pd.errors.EmptyDataError, OSError):
        return pd.DataFrame(columns=SLOUPCE)

    for sloupec in SLOUPCE:
        if sloupec not in df.columns:
            df[sloupec] = None

    return df[SLOUPCE]


def uloz_zaznamy(df, cesta=SOUBOR_ZAZNAMU):
    """Zapíše log zpět na disk."""
    df.to_csv(cesta, index=False, encoding="utf-8")


def zapis_predikce(zapasy_s_predikcemi, cesta=SOUBOR_ZAZNAMU, cas=None):
    """Zapíše predikce zápasů, které ještě nejsou dohrané.

    ``zapasy_s_predikcemi`` je seznam slovníků s klíči kolo, datum, domaci,
    hoste, stav a predikce (název modelu -> trojice pravděpodobností).

    Už zapsaná predikce se **nepřepisuje** – jinak by se model mohl zpětně
    "opravit" podle výsledku a měření by ztratilo smysl.

    Vrací počet nově zapsaných řádků.
    """
    df = nacti_zaznamy(cesta)
    existujici = set(df["klic"].dropna().astype(str))
    razitko = (cas or pd.Timestamp.now()).strftime("%Y-%m-%d %H:%M:%S")

    nove = []
    for zapas in zapasy_s_predikcemi:
        # Dohraný nebo odložený zápas se nezaznamenává, predikce by byla pozdní.
        if zapas.get("stav") == modely.ODEHRANO:
            continue
        if str(zapas.get("stav", "")).startswith("🔴"):
            continue

        for nazev_modelu, trojice in (zapas.get("predikce") or {}).items():
            if not trojice:
                continue

            klic = klic_zapasu(zapas["kolo"], zapas["domaci"], zapas["hoste"], nazev_modelu)
            if klic in existujici:
                continue

            p_domaci, p_remiza, p_hoste = trojice
            nove.append(
                {
                    "klic": klic,
                    "zapsano": razitko,
                    "kolo": zapas["kolo"],
                    "datum": zapas.get("datum", ""),
                    "domaci": zapas["domaci"],
                    "hoste": zapas["hoste"],
                    "model": nazev_modelu,
                    "p_domaci": round(p_domaci, 6),
                    "p_remiza": round(p_remiza, 6),
                    "p_hoste": round(p_hoste, 6),
                    "tip": modely.tip_z_pravdepodobnosti(p_domaci, p_remiza, p_hoste),
                    "skore": None,
                    "vysledek": None,
                }
            )
            existujici.add(klic)

    if not nove:
        return 0

    df = pd.concat([df, pd.DataFrame(nove)], ignore_index=True)
    uloz_zaznamy(df, cesta)
    return len(nove)


def doplnit_vysledky(databaze_kol, cesta=SOUBOR_ZAZNAMU):
    """Doplní k zapsaným predikcím skutečné výsledky odehraných zápasů.

    Vrací počet nově doplněných řádků.
    """
    df = nacti_zaznamy(cesta)
    if df.empty:
        return 0

    vysledky = {}
    for zapas in modely.odehrane_zapasy(databaze_kol):
        skore = f"{zapas['goly_domaci']}:{zapas['goly_hoste']}"
        vysledky[(zapas["kolo"], zapas["domaci"], zapas["hoste"])] = skore

    if not vysledky:
        return 0

    doplneno = 0
    for index, radek in df.iterrows():
        if pd.notna(radek.get("vysledek")) and str(radek.get("vysledek")).strip():
            continue

        try:
            kolo = int(radek["kolo"])
        except (TypeError, ValueError):
            continue

        skore = vysledky.get((kolo, radek["domaci"], radek["hoste"]))
        if not skore:
            continue

        df.at[index, "skore"] = skore
        df.at[index, "vysledek"] = modely.vysledek_zapasu(skore)
        doplneno += 1

    if doplneno:
        uloz_zaznamy(df, cesta)

    return doplneno


def _zaznamy_z_radku(skupina):
    """Řádky logu ve tvaru, který čekají funkce v ``modely``."""
    return [
        {
            "p_domaci": float(radek["p_domaci"]),
            "p_remiza": float(radek["p_remiza"]),
            "p_hoste": float(radek["p_hoste"]),
            "vysledek": str(radek["vysledek"]),
            "tip": radek["tip"],
            "skore": radek["skore"],
        }
        for _, radek in skupina.iterrows()
    ]


def _vyhodnocene(cesta):
    """Zápisy, u kterých už je známý výsledek."""
    df = nacti_zaznamy(cesta)
    if df.empty:
        return df

    return df[df["vysledek"].isin(["1", "0", "2"])]


def metriky_podle_modelu(cesta=SOUBOR_ZAZNAMU):
    """Přesnost jednotlivých modelů nad vyhodnocenými predikcemi."""
    vyhodnocene = _vyhodnocene(cesta)
    if vyhodnocene.empty:
        return {}

    return {
        str(nazev_modelu): modely.spocitej_metriky(_zaznamy_z_radku(skupina))
        for nazev_modelu, skupina in vyhodnocene.groupby("model")
    }


def spolehlivost_modelu(nazev="ensemble", cesta=SOUBOR_ZAZNAMU):
    """Porovná slíbenou jistotu se skutečnou úspěšností jednoho modelu."""
    vyhodnocene = _vyhodnocene(cesta)
    if vyhodnocene.empty:
        return []

    return modely.spolehlivost(
        _zaznamy_z_radku(vyhodnocene[vyhodnocene["model"] == nazev])
    )


def zapsane_predikce_kola(kolo, cesta=SOUBOR_ZAZNAMU):
    """Predikce zapsané před zápasy jednoho kola.

    Vrací (domácí, hosté) -> název modelu -> hodnoty. Archiv v aplikaci
    musí ukazovat, co model tipoval **tehdy**, ne co by tipoval dnes.
    """
    df = nacti_zaznamy(cesta)
    if df.empty:
        return {}

    zapasy = {}
    for _, radek in df.iterrows():
        try:
            if int(radek["kolo"]) != int(kolo):
                continue
            hodnoty = {
                "p_domaci": float(radek["p_domaci"]),
                "p_remiza": float(radek["p_remiza"]),
                "p_hoste": float(radek["p_hoste"]),
            }
        except (TypeError, ValueError):
            continue

        hodnoty["tip"] = radek["tip"]
        hodnoty["zapsano"] = radek["zapsano"]
        zapasy.setdefault((radek["domaci"], radek["hoste"]), {})[str(radek["model"])] = hodnoty

    return zapasy


def prehled_zaznamu(cesta=SOUBOR_ZAZNAMU, pocet=50):
    """Posledních N zápisů pro zobrazení v aplikaci."""
    df = nacti_zaznamy(cesta)
    if df.empty:
        return df

    return df.sort_values("zapsano", ascending=False).head(pocet)
