"""Predikční modely pro Chance Ligu.

Modul je záměrně bez Streamlitu a bez síťových dotazů – jsou tu jen čisté
funkce nad daty, takže se dají testovat samostatně.

Modely počítají pravděpodobnosti trojice (výhra domácích, remíza, výhra hostů):

* ``index_sily``  – z bodů a skóre v ligové tabulce, upravený o únavu a zranění
* ``poisson``     – Dixon-Coles nad vstřelenými a inkasovanými góly
* ``elo``         – průběžný rating aktualizovaný po každém odehraném zápase

Výstupy se skládají do ensemble váženého podle naměřeného Brier score.
"""

import math
import re

import pandas as pd

# --- SPOLEČNÉ KONSTANTY ---

# Průměrný bodový zisk na zápas v lize; slouží jako střed škály.
PRUMER_BODU_NA_ZAPAS = 1.35

# Po pár kolech jsou statistiky ještě náhodné, proto index stahujeme k průměru
# ligy. Číslo odpovídá počtu "průměrných" zápasů přimíchaných ke skutečným.
VAHA_PRIORU = 5.0

# Výhoda domácího prostředí jako násobitel indexu síly.
VYHODA_DOMACICH = 1.08

# Únava z pohárů: evropské poháry berou 10–15 % síly.
POKUTA_POHARY = {
    "Bez pohárů": 0.00,
    "Domácí pohár (MOL Cup)": 0.05,
    "Evropský pohár doma": 0.10,
    "Evropský pohár venku": 0.15,
}

# Zranění: absence klíčových hráčů berou 10–15 % síly.
POKUTA_ZRANENI = {
    "Kompletní kádr": 0.00,
    "Chybí 1 opora": 0.05,
    "Chybí 2 opory": 0.10,
    "Chybí 3+ opor": 0.15,
}

# Kolik dní odpočinku se ještě považuje za pohárovou zátěž.
MAX_DNU_UNAVY = 4

ODEHRANO = "✅ Odehráno"
NAZVY_MODELU = ("index_sily", "poisson", "elo")


def _goly(skore):
    """Ze zápisu '2:1' udělá dvojici čísel, jinak vrátí None."""
    if not skore or not re.match(r"^\d+:\d+$", str(skore).strip()):
        return None
    domaci, hoste = str(skore).strip().split(":")
    return int(domaci), int(hoste)


def odehrane_zapasy(databaze_kol):
    """Všechny dohrané zápasy s platným skóre, seřazené podle data."""
    zapasy = []

    for kolo in sorted(databaze_kol):
        for zapas in databaze_kol[kolo]:
            if zapas.get("stav") != ODEHRANO:
                continue
            vysledek = _goly(zapas.get("skore"))
            if vysledek is None:
                continue
            zapasy.append(
                {
                    "kolo": kolo,
                    "datum": zapas.get("datum", ""),
                    "domaci": zapas["domaci"],
                    "hoste": zapas["hoste"],
                    "goly_domaci": vysledek[0],
                    "goly_hoste": vysledek[1],
                }
            )

    zapasy.sort(key=lambda z: z["datum"])
    return zapasy


# --- MODEL 1: INDEX SÍLY Z TABULKY ---


def spocitej_index_sily(df_tabulka):
    """Základní index síly (~50 = průměr ligy) z bodů a skóre v tabulce."""
    indexy = {}

    for _, radek in df_tabulka.iterrows():
        try:
            zapasy = max(int(radek["Z"]), 1)
            body = int(radek["B"])
            vstrelene, inkasovane = (int(cast) for cast in str(radek["Skóre"]).split(":"))
        except (ValueError, KeyError):
            continue

        body_na_zapas = body / zapasy
        rozdil_na_zapas = (vstrelene - inkasovane) / zapasy

        index_syrovy = (
            50.0
            + (body_na_zapas - PRUMER_BODU_NA_ZAPAS) * 18.0
            + rozdil_na_zapas * 6.0
        )

        duvera = zapasy / (zapasy + VAHA_PRIORU)
        index = 50.0 + (index_syrovy - 50.0) * duvera

        indexy[radek["Tým"]] = round(max(index, 5.0), 1)

    return indexy


def uprav_silu(zakladni_sila, stav_poharu, stav_zraneni):
    """Sníží index síly o únavu z pohárů a o absence klíčových hráčů."""
    pokuta_pohary = POKUTA_POHARY.get(stav_poharu, 0.0)
    pokuta_zraneni = POKUTA_ZRANENI.get(stav_zraneni, 0.0)

    upravena = zakladni_sila * (1 - pokuta_pohary) * (1 - pokuta_zraneni)
    celkovy_dopad = 1 - (1 - pokuta_pohary) * (1 - pokuta_zraneni)

    return round(upravena, 1), round(celkovy_dopad * 100, 1)


def predikuj_zapas(sila_domaci, sila_hoste):
    """Převede indexy síly na pravděpodobnosti výhry / remízy / prohry."""
    rozdil = sila_domaci * VYHODA_DOMACICH - sila_hoste

    p_remiza = 0.28 * math.exp(-(rozdil**2) / 450.0)
    p_domaci = 1 / (1 + math.exp(-rozdil / 12.0))

    zbytek = 1 - p_remiza
    return p_domaci * zbytek, p_remiza, (1 - p_domaci) * zbytek


# --- MODEL 2: POISSON / DIXON-COLES ---

# Kolik "průměrných" zápasů se přimíchá k útoku a obraně týmu.
VAHA_PRIORU_GOLY = 4.0

# Dixon-Colesova korekce nízkých skóre. Záporná hodnota přidává remízy 0:0 a 1:1,
# které samotný Poisson systematicky podceňuje.
RHO_DIXON_COLES = -0.12

# Nad tolik gólů už je pravděpodobnost zanedbatelná.
MAX_GOLU = 8


def spocitej_utok_obranu(databaze_kol):
    """Síla útoku a obrany každého týmu vůči průměru ligy.

    Hodnota 1.0 znamená přesný ligový průměr, 1.2 o pětinu lepší útok.
    """
    zapasy = odehrane_zapasy(databaze_kol)
    if not zapasy:
        return {}, 0.0, 0.0

    prumer_domaci = sum(z["goly_domaci"] for z in zapasy) / len(zapasy)
    prumer_hoste = sum(z["goly_hoste"] for z in zapasy) / len(zapasy)
    prumer_na_tym = (prumer_domaci + prumer_hoste) / 2

    if prumer_na_tym <= 0:
        return {}, prumer_domaci, prumer_hoste

    statistiky = {}
    for zapas in zapasy:
        for tym, vstrelene, inkasovane in (
            (zapas["domaci"], zapas["goly_domaci"], zapas["goly_hoste"]),
            (zapas["hoste"], zapas["goly_hoste"], zapas["goly_domaci"]),
        ):
            zaznam = statistiky.setdefault(tym, {"zapasy": 0, "vstrelene": 0, "inkasovane": 0})
            zaznam["zapasy"] += 1
            zaznam["vstrelene"] += vstrelene
            zaznam["inkasovane"] += inkasovane

    sily = {}
    for tym, zaznam in statistiky.items():
        pocet = zaznam["zapasy"]
        utok_syrovy = (zaznam["vstrelene"] / pocet) / prumer_na_tym
        obrana_syrova = (zaznam["inkasovane"] / pocet) / prumer_na_tym

        # Po pár kolech jsou poměry divoké, proto se stahují k ligovému průměru.
        duvera = pocet / (pocet + VAHA_PRIORU_GOLY)
        sily[tym] = {
            "utok": 1.0 + (utok_syrovy - 1.0) * duvera,
            "obrana": 1.0 + (obrana_syrova - 1.0) * duvera,
            "zapasy": pocet,
        }

    return sily, prumer_domaci, prumer_hoste


def _poissonova_pravdepodobnost(pocet, stredni_hodnota):
    """P(X = pocet) pro Poissonovo rozdělení."""
    return (
        math.exp(-stredni_hodnota)
        * stredni_hodnota**pocet
        / math.factorial(pocet)
    )


def _korekce_dixon_coles(goly_domaci, goly_hoste, lambda_domaci, lambda_hoste):
    """Úprava pravděpodobnosti u nízkých skóre, kde Poisson selhává."""
    if goly_domaci == 0 and goly_hoste == 0:
        return 1.0 - lambda_domaci * lambda_hoste * RHO_DIXON_COLES
    if goly_domaci == 0 and goly_hoste == 1:
        return 1.0 + lambda_domaci * RHO_DIXON_COLES
    if goly_domaci == 1 and goly_hoste == 0:
        return 1.0 + lambda_hoste * RHO_DIXON_COLES
    if goly_domaci == 1 and goly_hoste == 1:
        return 1.0 - RHO_DIXON_COLES
    return 1.0


def ocekavane_goly(sily, prumer_domaci, prumer_hoste, domaci, hoste):
    """Očekávaný počet gólů obou týmů v konkrétním zápase."""
    sila_domaci = sily.get(domaci)
    sila_hoste = sily.get(hoste)
    if not sila_domaci or not sila_hoste:
        return None

    lambda_domaci = sila_domaci["utok"] * sila_hoste["obrana"] * prumer_domaci
    lambda_hoste = sila_hoste["utok"] * sila_domaci["obrana"] * prumer_hoste

    # Nulová lambda by rozbila Poissona, drobná podlaha to ošetří.
    return max(lambda_domaci, 0.05), max(lambda_hoste, 0.05)


def predikuj_poissonem(sily, prumer_domaci, prumer_hoste, domaci, hoste):
    """Pravděpodobnosti 1/X/2 z Dixon-Colesova modelu."""
    lambdy = ocekavane_goly(sily, prumer_domaci, prumer_hoste, domaci, hoste)
    if lambdy is None:
        return None

    lambda_domaci, lambda_hoste = lambdy

    p_domaci = p_remiza = p_hoste = 0.0
    for goly_d in range(MAX_GOLU + 1):
        for goly_h in range(MAX_GOLU + 1):
            pravdepodobnost = (
                _poissonova_pravdepodobnost(goly_d, lambda_domaci)
                * _poissonova_pravdepodobnost(goly_h, lambda_hoste)
                * _korekce_dixon_coles(goly_d, goly_h, lambda_domaci, lambda_hoste)
            )

            if goly_d > goly_h:
                p_domaci += pravdepodobnost
            elif goly_d == goly_h:
                p_remiza += pravdepodobnost
            else:
                p_hoste += pravdepodobnost

    return normalizuj(p_domaci, p_remiza, p_hoste)


def nejpravdepodobnejsi_skore(sily, prumer_domaci, prumer_hoste, domaci, hoste):
    """Nejpravděpodobnější přesný výsledek podle Poissona."""
    lambdy = ocekavane_goly(sily, prumer_domaci, prumer_hoste, domaci, hoste)
    if lambdy is None:
        return None

    lambda_domaci, lambda_hoste = lambdy
    nejlepsi, nejlepsi_p = None, -1.0

    for goly_d in range(MAX_GOLU + 1):
        for goly_h in range(MAX_GOLU + 1):
            pravdepodobnost = (
                _poissonova_pravdepodobnost(goly_d, lambda_domaci)
                * _poissonova_pravdepodobnost(goly_h, lambda_hoste)
                * _korekce_dixon_coles(goly_d, goly_h, lambda_domaci, lambda_hoste)
            )
            if pravdepodobnost > nejlepsi_p:
                nejlepsi, nejlepsi_p = (goly_d, goly_h), pravdepodobnost

    return nejlepsi, nejlepsi_p


# --- MODEL 3: ELO ---

VYCHOZI_ELO = 1500.0

# Jak silně jeden zápas pohne ratingem.
K_FAKTOR = 20.0

# Výhoda domácího prostředí v Elo bodech.
VYHODA_ELO = 60.0


def spocitej_elo(databaze_kol):
    """Projde odehrané zápasy chronologicky a vrátí aktuální rating týmů."""
    rating = {}

    for zapas in odehrane_zapasy(databaze_kol):
        domaci, hoste = zapas["domaci"], zapas["hoste"]
        rating.setdefault(domaci, VYCHOZI_ELO)
        rating.setdefault(hoste, VYCHOZI_ELO)

        rozdil = rating[domaci] + VYHODA_ELO - rating[hoste]
        ocekavane = 1.0 / (1.0 + 10.0 ** (-rozdil / 400.0))

        if zapas["goly_domaci"] > zapas["goly_hoste"]:
            skutecne = 1.0
        elif zapas["goly_domaci"] < zapas["goly_hoste"]:
            skutecne = 0.0
        else:
            skutecne = 0.5

        zmena = K_FAKTOR * (skutecne - ocekavane)
        rating[domaci] += zmena
        rating[hoste] -= zmena

    return {tym: round(hodnota, 1) for tym, hodnota in rating.items()}


def predikuj_elem(rating, domaci, hoste):
    """Pravděpodobnosti 1/X/2 z Elo ratingu obou týmů."""
    if domaci not in rating or hoste not in rating:
        return None

    rozdil = rating[domaci] + VYHODA_ELO - rating[hoste]

    # Očekávané skóre v Elo smyslu je výhra + polovina remízy.
    ocekavane = 1.0 / (1.0 + 10.0 ** (-rozdil / 400.0))

    # Čím vyrovnanější zápas, tím vyšší šance na remízu.
    p_remiza = 0.30 * math.exp(-(rozdil**2) / 160000.0)
    zbytek = 1.0 - p_remiza

    return normalizuj(ocekavane * zbytek, p_remiza, (1.0 - ocekavane) * zbytek)


# --- SPOLEČNÉ NÁSTROJE NAD PRAVDĚPODOBNOSTMI ---


def normalizuj(p_domaci, p_remiza, p_hoste):
    """Zaručí, že trojice dá dohromady jedničku."""
    soucet = p_domaci + p_remiza + p_hoste
    if soucet <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return p_domaci / soucet, p_remiza / soucet, p_hoste / soucet


def tip_z_pravdepodobnosti(p_domaci, p_remiza, p_hoste):
    """Vybere sázkařský tip podle rozložení pravděpodobností."""
    if p_domaci >= 0.55:
        return "1 (Výhra domácích)"
    if p_hoste >= 0.55:
        return "2 (Výhra hostů)"
    if p_remiza >= max(p_domaci, p_hoste):
        return "0 (Remíza)"
    if p_domaci > p_hoste:
        return "1X (Neprohra domácích)"
    return "02 (Neprohra hostů)"


def vyhodnot_tip(tip, skore):
    """Porovná tip se skutečným výsledkem. Vrací True/False, nebo None."""
    vysledek = _goly(skore)
    if not tip or vysledek is None:
        return None

    goly_domaci, goly_hoste = vysledek
    znacka = tip.split(" ", 1)[0]

    if znacka == "1":
        return goly_domaci > goly_hoste
    if znacka == "2":
        return goly_hoste > goly_domaci
    if znacka == "0":
        return goly_domaci == goly_hoste
    if znacka == "1X":
        return goly_domaci >= goly_hoste
    if znacka == "02":
        return goly_hoste >= goly_domaci
    return None


def vysledek_zapasu(skore):
    """Ze skóre určí, která z možností 1/0/2 nastala."""
    goly = _goly(skore)
    if goly is None:
        return None

    goly_domaci, goly_hoste = goly
    if goly_domaci > goly_hoste:
        return "1"
    if goly_domaci < goly_hoste:
        return "2"
    return "0"


# --- FORMA, ÚNAVA, VÝBĚR KOL ---


def spocitej_formu(databaze_kol, pocet=5):
    """Z odehraných zápasů odvodí formu (posledních N výsledků) pro každý tým."""
    historie = {}

    for zapas in odehrane_zapasy(databaze_kol):
        if zapas["goly_domaci"] > zapas["goly_hoste"]:
            vysledky = ("V", "P")
        elif zapas["goly_domaci"] < zapas["goly_hoste"]:
            vysledky = ("P", "V")
        else:
            vysledky = ("R", "R")

        historie.setdefault(zapas["domaci"], []).append(vysledky[0])
        historie.setdefault(zapas["hoste"], []).append(vysledky[1])

    return {tym: vysledky[-pocet:] for tym, vysledky in historie.items()}


def bonus_za_formu(forma):
    """Forma posune index síly maximálně o ±4 body."""
    if not forma:
        return 0.0

    body = {"V": 1.0, "R": 0.0, "P": -1.0}
    prumer = sum(body[v] for v in forma) / len(forma)
    return round(prumer * 4.0, 1)


def odvod_unavu(pohary_tymu, datum_zapasu):
    """Podle posledního pohárového zápasu určí stupeň únavy."""
    if not pohary_tymu or datum_zapasu is None:
        return "Bez pohárů"

    predchozi = [p for p in pohary_tymu if p["cas"] < datum_zapasu]
    if not predchozi:
        return "Bez pohárů"

    posledni = max(predchozi, key=lambda p: p["cas"])
    dnu = (datum_zapasu - posledni["cas"]).days

    if dnu > MAX_DNU_UNAVY:
        return "Bez pohárů"
    if not posledni["evropsky"]:
        return "Domácí pohár (MOL Cup)"
    return "Evropský pohár doma" if posledni["doma"] else "Evropský pohár venku"


def vyber_zobrazena_kola(databaze_kol, pocet=2):
    """Vrátí aktuální a následující kola – odehraná a zrušená se přeskakují."""
    nedohrane = [
        kolo
        for kolo in sorted(databaze_kol)
        if any(
            z["stav"] not in (ODEHRANO, "🔴 Zrušeno", "🔴 Odloženo")
            for z in databaze_kol[kolo]
        )
    ]

    if not nedohrane:
        return sorted(databaze_kol)[-pocet:]

    return nedohrane[:pocet]


def vypocitej_tabulku_z_vysledku(databaze_kol, tymy):
    """Dopočítá ligovou tabulku z odehraných zápasů v databázi."""
    if not tymy:
        return None

    statistiky = {
        tym: {"Z": 0, "V": 0, "R": 0, "P": 0, "GF": 0, "GA": 0, "B": 0}
        for tym in tymy
    }

    for zapas in odehrane_zapasy(databaze_kol):
        domaci, hoste = zapas["domaci"], zapas["hoste"]
        if domaci not in statistiky or hoste not in statistiky:
            continue

        goly_domaci, goly_hoste = zapas["goly_domaci"], zapas["goly_hoste"]

        for tym, vstrelene, inkasovane in (
            (domaci, goly_domaci, goly_hoste),
            (hoste, goly_hoste, goly_domaci),
        ):
            statistiky[tym]["Z"] += 1
            statistiky[tym]["GF"] += vstrelene
            statistiky[tym]["GA"] += inkasovane

        if goly_domaci > goly_hoste:
            statistiky[domaci]["V"] += 1
            statistiky[domaci]["B"] += 3
            statistiky[hoste]["P"] += 1
        elif goly_domaci < goly_hoste:
            statistiky[hoste]["V"] += 1
            statistiky[hoste]["B"] += 3
            statistiky[domaci]["P"] += 1
        else:
            statistiky[domaci]["R"] += 1
            statistiky[hoste]["R"] += 1
            statistiky[domaci]["B"] += 1
            statistiky[hoste]["B"] += 1

    radky = [
        {
            "Tým": tym,
            "B": stat["B"],
            "Z": stat["Z"],
            "V": stat["V"],
            "R": stat["R"],
            "P": stat["P"],
            "Skóre": f"{stat['GF']}:{stat['GA']}",
        }
        for tym, stat in sorted(
            statistiky.items(),
            key=lambda item: (-item[1]["B"], item[1]["GF"] - item[1]["GA"], item[1]["GF"]),
        )
    ]

    df = pd.DataFrame(radky)
    df.index = df.index + 1
    return df


# --- METRIKY PŘESNOSTI ---

# Náhodný tip 1/3 na každou možnost; slouží jako referenční hranice.
BRIER_NAHODNY = 2 / 3
LOGLOSS_NAHODNY = math.log(3)


def brier_score(p_domaci, p_remiza, p_hoste, vysledek):
    """Brier score jedné předpovědi (0 = přesná, 2 = úplně vedle)."""
    skutecne = {
        "1": (1.0, 0.0, 0.0),
        "0": (0.0, 1.0, 0.0),
        "2": (0.0, 0.0, 1.0),
    }.get(vysledek)

    if skutecne is None:
        return None

    return sum(
        (p - o) ** 2
        for p, o in zip((p_domaci, p_remiza, p_hoste), skutecne)
    )


def log_loss(p_domaci, p_remiza, p_hoste, vysledek):
    """Logaritmická ztráta jedné předpovědi (nižší je lepší)."""
    trefena = {"1": p_domaci, "0": p_remiza, "2": p_hoste}.get(vysledek)
    if trefena is None:
        return None

    # Nula by dala nekonečno, proto se pravděpodobnost ořízne.
    return -math.log(max(trefena, 1e-12))


def spocitej_metriky(zaznamy):
    """Souhrn přesnosti pro seznam vyhodnocených předpovědí.

    Očekává položky s klíči p_domaci, p_remiza, p_hoste, vysledek a tip.
    """
    briery, ztraty, trefy_tipu, trefy_nejvyssi = [], [], [], []

    for zaznam in zaznamy:
        vysledek = zaznam.get("vysledek")
        if vysledek not in ("1", "0", "2"):
            continue

        trojice = (zaznam["p_domaci"], zaznam["p_remiza"], zaznam["p_hoste"])
        briery.append(brier_score(*trojice, vysledek))
        ztraty.append(log_loss(*trojice, vysledek))

        # Trefa nejvyšší pravděpodobnosti je přísnější než sázkařský tip 1X/02.
        nejvyssi = ("1", "0", "2")[trojice.index(max(trojice))]
        trefy_nejvyssi.append(nejvyssi == vysledek)

        sedel = vyhodnot_tip(zaznam.get("tip"), zaznam.get("skore"))
        if sedel is not None:
            trefy_tipu.append(sedel)

    if not briery:
        return None

    return {
        "zapasu": len(briery),
        "brier": sum(briery) / len(briery),
        "log_loss": sum(ztraty) / len(ztraty),
        "trefa_favorita": sum(trefy_nejvyssi) / len(trefy_nejvyssi),
        "uspesnost_tipu": (sum(trefy_tipu) / len(trefy_tipu)) if trefy_tipu else None,
    }


# --- ENSEMBLE ---

# Dokud není naměřeno aspoň tolik zápasů, váhy se neodvozují z výsledků.
MIN_ZAPASU_PRO_VAHY = 20


def vahy_z_metrik(metriky_modelu):
    """Váhy modelů podle naměřeného Brier score.

    Dokud není dost dat, mají všechny modely stejnou váhu – vážit podle
    tří zápasů by byl jen šum.
    """
    pouzitelne = {
        nazev: metriky
        for nazev, metriky in metriky_modelu.items()
        if metriky and metriky["zapasu"] >= MIN_ZAPASU_PRO_VAHY
    }

    if not pouzitelne:
        return None

    # Lepší (nižší) Brier dostane vyšší váhu; posun drží váhy konečné.
    syrove = {
        nazev: max(BRIER_NAHODNY - metriky["brier"], 0.01)
        for nazev, metriky in pouzitelne.items()
    }

    soucet = sum(syrove.values())
    return {nazev: hodnota / soucet for nazev, hodnota in syrove.items()}


def predikuj_vsemi(
    sily,
    domaci,
    hoste,
    pohary_domaci="Bez pohárů",
    pohary_hoste="Bez pohárů",
    zraneni_domaci="Kompletní kádr",
    zraneni_hoste="Kompletní kádr",
    vahy=None,
):
    """Spočítá predikci všemi modely a složí je dohromady.

    ``sily`` je slovník s klíči indexy_sily, sily_golu, prumer_domaci,
    prumer_hoste a elo – přesně to, co vrací ``data.spocitej_sily``.

    Vrací None, když zápas nedokázal spočítat ani jeden model.
    """
    vysledek = {"modely": {}}

    # Model 1: index síly z tabulky, upravený o únavu a zranění.
    sila_domaci_zaklad = sily["indexy_sily"].get(domaci)
    sila_hoste_zaklad = sily["indexy_sily"].get(hoste)

    if sila_domaci_zaklad is not None and sila_hoste_zaklad is not None:
        sila_domaci, dopad_domaci = uprav_silu(
            sila_domaci_zaklad, pohary_domaci, zraneni_domaci
        )
        sila_hoste, dopad_hoste = uprav_silu(
            sila_hoste_zaklad, pohary_hoste, zraneni_hoste
        )

        vysledek.update(
            {
                "sila_domaci_zaklad": sila_domaci_zaklad,
                "sila_hoste_zaklad": sila_hoste_zaklad,
                "sila_domaci": sila_domaci,
                "sila_hoste": sila_hoste,
                "dopad_domaci": dopad_domaci,
                "dopad_hoste": dopad_hoste,
            }
        )
        vysledek["modely"]["index_sily"] = predikuj_zapas(sila_domaci, sila_hoste)

    # Model 2: Dixon-Coles nad vstřelenými a inkasovanými góly.
    argumenty_golu = (
        sily["sily_golu"],
        sily["prumer_domaci"],
        sily["prumer_hoste"],
        domaci,
        hoste,
    )
    vysledek["modely"]["poisson"] = predikuj_poissonem(*argumenty_golu)
    vysledek["ocekavane_goly"] = ocekavane_goly(*argumenty_golu)
    vysledek["nejcastejsi_skore"] = nejpravdepodobnejsi_skore(*argumenty_golu)

    # Model 3: Elo rating aktualizovaný po každém odehraném zápase.
    vysledek["modely"]["elo"] = predikuj_elem(sily["elo"], domaci, hoste)
    vysledek["elo_domaci"] = sily["elo"].get(domaci)
    vysledek["elo_hoste"] = sily["elo"].get(hoste)

    slozene = slozeni_predikci(vysledek["modely"], vahy)
    if slozene is None:
        return None

    vysledek["p_domaci"], vysledek["p_remiza"], vysledek["p_hoste"] = slozene
    vysledek["tip"] = tip_z_pravdepodobnosti(*slozene)
    vysledek["jistota"] = jistota_predikce(*slozene)
    return vysledek


def slozeni_predikci(predikce_modelu, vahy=None):
    """Zprůměruje pravděpodobnosti jednotlivých modelů.

    ``predikce_modelu`` je slovník název modelu -> trojice pravděpodobností.
    Modely, které predikci nedaly (None), se přeskakují.
    """
    dostupne = {
        nazev: trojice
        for nazev, trojice in predikce_modelu.items()
        if trojice is not None
    }

    if not dostupne:
        return None

    if vahy:
        pouzite = {n: vahy.get(n, 0.0) for n in dostupne}
        if sum(pouzite.values()) <= 0:
            pouzite = {n: 1.0 for n in dostupne}
    else:
        pouzite = {n: 1.0 for n in dostupne}

    celkem = sum(pouzite.values())
    p_domaci = sum(dostupne[n][0] * pouzite[n] for n in dostupne) / celkem
    p_remiza = sum(dostupne[n][1] * pouzite[n] for n in dostupne) / celkem
    p_hoste = sum(dostupne[n][2] * pouzite[n] for n in dostupne) / celkem

    return normalizuj(p_domaci, p_remiza, p_hoste)


# --- JISTOTA PREDIKCE ---


def jistota_predikce(p_domaci, p_remiza, p_hoste):
    """Nejvyšší z pravděpodobností – jak moc si model zápasem věří.

    Hodnota kolem 1/3 znamená úplně otevřený zápas.
    """
    return max(p_domaci, p_remiza, p_hoste)
