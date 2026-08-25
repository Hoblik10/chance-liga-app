"""Zpětné přehrání minulých sezón.

Bez měření je každá úprava modelu jen dojem. Backtest projde archiv den po
dni a pro každý zápas počítá predikci **jen z toho, co skončilo před jeho
výkopem** – jinak by si model četl výsledky, které má teprve odhadnout.

Výpočet je rozdělený na dva kroky. ``stav_po_dnech`` je ta drahá část (síly
týmů k danému dni), ``predikce_ze_stavu`` už jen skládá pravděpodobnosti.
Ladění parametrů predikce tak nemusí pokaždé přepočítávat celou historii.

Spuštění z příkazové řádky:

    python backtest.py                 # obě stažené sezóny
    python backtest.py 2025-2026       # jen jedna
"""

import sys
from datetime import datetime

import pandas as pd

import data
import modely
import nastaveni

# Sezóny, které jsou stažené v archivu, od nejstarší.
SEZONY = nastaveni.ARCHIVNI_SEZONY

# Hranice mezi rozjezdem sezóny a jejím zbytkem (v odehraných zápasech).
# 60 zápasů je zhruba osm kol – dokud jich není tolik, stojí modely
# hlavně na archivu.
ZAPASU_NA_ROZJEZD = 60


def nacti_historii(sezony):
    """Zápasy zadaných sezón v chronologickém pořadí."""
    zapasy = []

    for sezona in sezony:
        for zaznam in data.nacti_sezonu(sezona, stahni=False):
            zapasy.append(
                {
                    **zaznam,
                    "sezona": sezona,
                    "stav": modely.ODEHRANO,
                    # Předpočítaný čas ušetří statisíce převodů z řetězce,
                    # protože stejné zápasy se procházejí pro každý hrací den.
                    "cas": datetime.strptime(zaznam["datum"], "%Y-%m-%d %H:%M"),
                }
            )

    zapasy.sort(key=lambda z: z["datum"])
    return zapasy


def _databaze(zapasy):
    """Zápasy poskládané do struktury kolo -> zápasy, jak ji čekají modely.

    Klíčem je dvojice (sezóna, kolo) – čísla kol se v nadstavbě opakují.
    """
    databaze = {}
    for zapas in zapasy:
        databaze.setdefault((zapas["sezona"], zapas["kolo"]), []).append(zapas)
    return databaze


def _sily_pred_dnem(odehrane_sezony, drivejsi, tymy_sezony, parametry):
    """Vstupy modelů podle stavu před daným hracím dnem."""
    databaze_sezony = _databaze(odehrane_sezony)

    # Tabulka patří jen rozehrané sezóně; góly a Elo čerpají i ze starších.
    tabulka = modely.vypocitej_tabulku_z_vysledku(databaze_sezony, tymy_sezony)
    if tabulka is None:
        tabulka = pd.DataFrame(columns=["Tým", "B", "Z", "V", "R", "P", "Skóre"])

    return data.spocitej_sily(
        {
            "databaze_kol": databaze_sezony,
            "historie_kol": _databaze(drivejsi + odehrane_sezony),
            "tabulka": tabulka,
            "forma": {},
        },
        **parametry,
    )


def stav_po_dnech(sezony=SEZONY, **parametry):
    """Pro každý hrací den vrátí síly týmů a zápasy, které se ten den hrály.

    Pojmenované argumenty (``polocas_dnu``, ``prenos_pres_leto``) jdou dál
    do výpočtu sil, aby se daly hledat jejich nejlepší hodnoty.
    """
    stavy = []

    for poradi, sezona in enumerate(sezony):
        drivejsi = nacti_historii(sezony[:poradi])
        zapasy_sezony = nacti_historii([sezona])
        tymy = sorted({t for z in zapasy_sezony for t in (z["domaci"], z["hoste"])})

        podle_dne = {}
        for zapas in zapasy_sezony:
            podle_dne.setdefault(zapas["datum"][:10], []).append(zapas)

        odehrane = []
        for den in sorted(podle_dne):
            stavy.append(
                {
                    "sezona": sezona,
                    "sily": _sily_pred_dnem(odehrane, drivejsi, tymy, parametry),
                    "zapasy": podle_dne[den],
                    "odehrano_pred": len(odehrane),
                }
            )
            odehrane.extend(podle_dne[den])

    return stavy


def predikce_ze_stavu(stavy, vahy=None):
    """Predikce všech zápasů z připravených denních stavů."""
    predikce = []

    for stav in stavy:
        for zapas in stav["zapasy"]:
            vysledek = modely.predikuj_vsemi(
                stav["sily"], zapas["domaci"], zapas["hoste"], vahy=vahy
            )
            if vysledek is None:
                continue

            predikce.append(
                {
                    "sezona": stav["sezona"],
                    "kolo": zapas["kolo"],
                    "datum": zapas["datum"],
                    "domaci": zapas["domaci"],
                    "hoste": zapas["hoste"],
                    "skore": zapas["skore"],
                    "vysledek": modely.vysledek_zapasu(zapas["skore"]),
                    "odehrano_pred": stav["odehrano_pred"],
                    "modely": vysledek["modely"],
                    "ensemble": (
                        vysledek["p_domaci"],
                        vysledek["p_remiza"],
                        vysledek["p_hoste"],
                    ),
                }
            )

    return predikce


def prehraj(sezony=SEZONY, vahy=None, **parametry):
    """Přehraje sezóny za sebou; každá vidí ty předchozí jako historii."""
    return predikce_ze_stavu(stav_po_dnech(sezony, **parametry), vahy=vahy)


def zaznamy_modelu(predikce, nazev):
    """Predikce jednoho modelu ve tvaru, který čeká ``spocitej_metriky``."""
    zaznamy = []

    for zapas in predikce:
        trojice = (
            zapas["ensemble"] if nazev == "ensemble" else zapas["modely"].get(nazev)
        )
        if trojice is None:
            continue

        zaznamy.append(
            {
                "p_domaci": trojice[0],
                "p_remiza": trojice[1],
                "p_hoste": trojice[2],
                "vysledek": zapas["vysledek"],
                "skore": zapas["skore"],
                "tip": modely.tip_z_pravdepodobnosti(*trojice),
            }
        )

    return zaznamy


def metriky(predikce, nazvy=modely.NAZVY_MODELU + ("ensemble",)):
    """Brier score, log loss a úspěšnost pro každý model."""
    return {
        nazev: modely.spocitej_metriky(zaznamy_modelu(predikce, nazev))
        for nazev in nazvy
    }


def _radek_metrik(nazev, hodnoty):
    return {
        "Model": nazev,
        "Zápasů": hodnoty["zapasu"],
        "Brier": round(hodnoty["brier"], 4),
        "Log loss": round(hodnoty["log_loss"], 4),
        "Trefa favorita": round(hodnoty["trefa_favorita"], 3),
        "Úspěšnost tipu": (
            round(hodnoty["uspesnost_tipu"], 3)
            if hodnoty["uspesnost_tipu"] is not None
            else None
        ),
    }


def tabulka_metrik(predikce):
    """Souhrn přesnosti jako DataFrame, včetně náhodného tipu pro srovnání."""
    radky = [
        _radek_metrik(nazev, hodnoty)
        for nazev, hodnoty in metriky(predikce).items()
        if hodnoty
    ]

    radky.append(
        {
            "Model": "náhodný tip",
            "Zápasů": len(predikce),
            "Brier": round(modely.BRIER_NAHODNY, 4),
            "Log loss": round(modely.LOGLOSS_NAHODNY, 4),
            "Trefa favorita": None,
            "Úspěšnost tipu": None,
        }
    )

    return pd.DataFrame(radky).set_index("Model")


def tabulka_tipu(predikce, nazev="ensemble"):
    """Kolik tipů vyjde podle toho, co mají maximalizovat."""
    popisy = {
        modely.CIL_USPESNOST: "úspěšnost (dvojitá šance)",
        modely.CIL_INFORMACE: "informace (vítěz nad prahem)",
    }

    radky = []
    for cil, popis in popisy.items():
        trefy = []
        for zapas in predikce:
            trojice = (
                zapas["ensemble"] if nazev == "ensemble" else zapas["modely"].get(nazev)
            )
            if trojice is None:
                continue

            sedel = modely.vyhodnot_tip(
                modely.tip_z_pravdepodobnosti(*trojice, cil=cil), zapas["skore"]
            )
            if sedel is not None:
                trefy.append(sedel)

        if trefy:
            radky.append(
                {
                    "Cíl tipu": popis,
                    "Zápasů": len(trefy),
                    "Vyšlo": round(sum(trefy) / len(trefy), 3),
                }
            )

    return pd.DataFrame(radky).set_index("Cíl tipu")


def tabulka_podle_faze(predikce, nazev="ensemble"):
    """Přesnost zvlášť pro rozjezd sezóny a pro její zbytek.

    Na začátku sezóny stojí modely skoro jen na archivu, takže právě tam se
    pozná, jestli starší ročníky pomáhají.
    """
    faze = {
        f"rozjezd (<{ZAPASU_NA_ROZJEZD} zápasů)": [
            z for z in predikce if z["odehrano_pred"] < ZAPASU_NA_ROZJEZD
        ],
        "zbytek sezóny": [
            z for z in predikce if z["odehrano_pred"] >= ZAPASU_NA_ROZJEZD
        ],
    }

    radky = []
    for popis, vybrane in faze.items():
        hodnoty = modely.spocitej_metriky(zaznamy_modelu(vybrane, nazev))
        if hodnoty:
            radky.append({**_radek_metrik(nazev, hodnoty), "Model": popis})

    return pd.DataFrame(radky).set_index("Model")


if __name__ == "__main__":
    zvolene = tuple(sys.argv[1:]) or SEZONY
    vysledky = prehraj(zvolene)

    print(f"Přehráno {len(vysledky)} zápasů ze sezón {', '.join(zvolene)}.\n")
    print(tabulka_metrik(vysledky).to_string())

    print("\nÚspěšnost tipu podle zvoleného cíle:")
    print(tabulka_tipu(vysledky).to_string())

    if len(zvolene) > 1:
        print("\nEnsemble podle fáze sezóny (jen ročníky, které mají archiv):")
        s_archivem = [z for z in vysledky if z["sezona"] != zvolene[0]]
        print(tabulka_podle_faze(s_archivem).to_string())
