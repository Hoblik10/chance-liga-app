"""Sestavení a odeslání hlášení s tipy na další kolo.

Stejný text jde na Telegram z tlačítka v aplikaci i z naplánované úlohy.
Zpráva je tabulka jako nahoře ve webové aplikaci, seřazená od nejvyšší
jistoty. Naplánovaná úloha bere kolo, které se hraje v nejbližších dnech,
ne odložené zbytky staršího kola.
"""

import html
from datetime import datetime, timedelta

import data
import modely
import nastaveni
import zaznamy

VYCHOZI_ZRANENI = list(modely.POKUTA_ZRANENI)[0]

# Pondělní hlášení pokrývá nadcházející víkend, ne zápasy za tři týdny.
HORIZONT_DNU = 8


def dostupna_kola(podklady):
    """Kola, která se mají zobrazit nebo poslat."""
    return podklady["ziva_kola"] or modely.vyber_zobrazena_kola(
        podklady["databaze_kol"], nastaveni.POCET_ZOBRAZENYCH_KOL
    )


def _cas_zapasu(zapas):
    """Čas výkopu v pražském pásmu, nebo None."""
    cas = zapas.get("cas")
    if cas is None:
        return None
    if cas.tzinfo is None:
        return cas.replace(tzinfo=data.PASMO_PRAHA)
    return cas.astimezone(data.PASMO_PRAHA)


def najdi_dalsi_kolo(databaze_kol, kola, ted=None):
    """Kolo, které se hraje v nejbližších dnech.

    První kolo s jakýmkoli nesehraným zápasem nestačí – ve 4. kole můžou
    zbývat dva zápasy přeložené na září, zatímco 5. kolo je příští víkend.
    """
    ted = ted or datetime.now(data.PASMO_PRAHA)
    konec = ted + timedelta(days=HORIZONT_DNU)

    nejlepsi, nejvic = None, -1
    for cislo_kola in sorted(kola):
        pocet = 0
        for zapas in databaze_kol.get(cislo_kola, []):
            if zapas["stav"] == modely.ODEHRANO or zapas["stav"].startswith("🔴"):
                continue
            cas = _cas_zapasu(zapas)
            if cas is None or ted - timedelta(hours=3) <= cas <= konec:
                pocet += 1
        if pocet > nejvic:
            nejvic, nejlepsi = pocet, cislo_kola

    if nejvic > 0:
        return nejlepsi

    for cislo_kola in sorted(kola):
        if any(
            z["stav"] != modely.ODEHRANO and not z["stav"].startswith("🔴")
            for z in databaze_kol.get(cislo_kola, [])
        ):
            return cislo_kola
    return None


def predikce_kola(sily, databaze_kol, kola, vahy, id_tymu_v_lize, rucni_vstupy=None):
    """Predikce všech zápasů ve vybraných kolech.

    ``rucni_vstupy`` je volitelný slovník (kolo, pořadí) ->
    {pohary_d, pohary_h, zraneni_d, zraneni_h} z UI. Bez něj se použije
    odhad únavy z pohárů a kompletní kádr.
    """
    rucni_vstupy = rucni_vstupy or {}
    predikce = {}

    for kolo in kola:
        for poradi, zapas in enumerate(databaze_kol.get(kolo, [])):
            vychozi_d, poznamka_d = data.zjisti_vychozi_unavu(
                zapas["domaci"], zapas, id_tymu_v_lize
            )
            vychozi_h, poznamka_h = data.zjisti_vychozi_unavu(
                zapas["hoste"], zapas, id_tymu_v_lize
            )

            vstupy = rucni_vstupy.get((kolo, poradi), {})
            vysledek = modely.predikuj_vsemi(
                sily,
                zapas["domaci"],
                zapas["hoste"],
                pohary_domaci=vstupy.get("pohary_d", vychozi_d),
                pohary_hoste=vstupy.get("pohary_h", vychozi_h),
                zraneni_domaci=vstupy.get("zraneni_d", VYCHOZI_ZRANENI),
                zraneni_hoste=vstupy.get("zraneni_h", VYCHOZI_ZRANENI),
                vahy=vahy,
            )

            if vysledek is None:
                predikce[(kolo, poradi)] = None
                continue

            vysledek["vychozi_pohary_domaci"] = vychozi_d
            vysledek["vychozi_pohary_hoste"] = vychozi_h
            vysledek["poznamka_domaci"] = poznamka_d
            vysledek["poznamka_hoste"] = poznamka_h
            predikce[(kolo, poradi)] = vysledek

    return predikce


def zapis_do_logu(databaze_kol, kola, predikce, historie_kol, historie_je_ziva):
    """Zapíše predikce a doplní výsledky. Vrací lidský popis, co se stalo."""
    k_zapisu = []

    for kolo in kola:
        for poradi, zapas in enumerate(databaze_kol.get(kolo, [])):
            vysledek = predikce.get((kolo, poradi))
            if not vysledek:
                continue

            modely_zapasu = dict(vysledek["modely"])
            modely_zapasu["ensemble"] = (
                vysledek["p_domaci"],
                vysledek["p_remiza"],
                vysledek["p_hoste"],
            )
            k_zapisu.append(
                {
                    "kolo": kolo,
                    "datum": zapas.get("datum", ""),
                    "domaci": zapas["domaci"],
                    "hoste": zapas["hoste"],
                    "stav": zapas.get("stav", ""),
                    "predikce": modely_zapasu,
                }
            )

    novych = zaznamy.zapis_predikce(k_zapisu)

    if historie_je_ziva:
        doplnenych = zaznamy.doplnit_vysledky(historie_kol)
        return f"Zapsáno {novych} nových predikcí, doplněno {doplnenych} výsledků."

    return (
        f"Zapsáno {novych} nových predikcí. Výsledky se nedoplňují – "
        "běží statická data, ne ověřený rozpis."
    )


def radky_souhrnu(kolo, zapasy, predikce):
    """Nesehrané zápasy kola seřazené od nejvyšší jistoty."""
    radky = []

    for poradi, zapas in enumerate(zapasy):
        if zapas["stav"] == modely.ODEHRANO or zapas["stav"].startswith("🔴"):
            continue

        vysledek = predikce.get((kolo, poradi))
        if not vysledek:
            continue

        skore = vysledek.get("nejcastejsi_skore")
        if skore:
            (goly_d, goly_h), p_skore = skore
            skore_text = f"{goly_d}:{goly_h} ({p_skore:.0%})"
        else:
            skore_text = "–"

        radky.append(
            {
                "poradi": poradi,
                "domaci": zapas["domaci"],
                "hoste": zapas["hoste"],
                "datum": data.formatuj_vykop(zapas.get("cas")) or zapas.get("datum", ""),
                "p_domaci": vysledek["p_domaci"],
                "p_remiza": vysledek["p_remiza"],
                "p_hoste": vysledek["p_hoste"],
                "tip": vysledek["tip"],
                "jistota": vysledek["jistota"],
                "skore": skore_text,
            }
        )

    radky.sort(key=lambda r: r["jistota"], reverse=True)
    return radky


def kratky_nazev(tym):
    """Zkrátí název, aby se vešel na jeden řádek v telefonu."""
    if tym == "Bohemians Praha 1905":
        return "Bohemians"
    for predpona in ("1. FC ", "SK ", "AC ", "FK ", "FC "):
        if tym.startswith(predpona):
            tym = tym[len(predpona):]
            break
    return tym.replace(" Praha", "").strip()


def kratky_vykop(datum):
    """22.08.2026 20:00 → 22.08. 20:00"""
    if not datum:
        return ""
    return datum.replace(".2026", ".").replace(".2027", ".").strip()


def sestav_zpravu(kolo, zapasy, predikce):
    """Hlášení čitelné na telefonu – jeden zápas = jeden blok, ne široká tabulka."""
    radky = radky_souhrnu(kolo, zapasy, predikce)
    if not radky:
        return None

    bloky = [
        f"<b>Chance Liga · {kolo}. kolo</b>",
        "od nejvyšší jistoty",
        "",
    ]

    for cislo, radek in enumerate(radky, start=1):
        domaci = html.escape(kratky_nazev(radek["domaci"]))
        hoste = html.escape(kratky_nazev(radek["hoste"]))
        tip = html.escape(radek["tip"].split(" ")[0])
        vykop = html.escape(kratky_vykop(radek["datum"]))
        skore = html.escape(radek["skore"])

        bloky.append(f"<b>{cislo}. {domaci} – {hoste}</b>")
        if vykop:
            bloky.append(vykop)
        bloky.append(
            f"1 {radek['p_domaci']:.0%} · "
            f"X {radek['p_remiza']:.0%} · "
            f"2 {radek['p_hoste']:.0%}"
        )
        bloky.append(f"Tip {tip} · jistota {radek['jistota']:.0%} · {skore}")
        bloky.append("")

    return "\n".join(bloky).strip()


def priprav_a_posli(odeslat=True):
    """Celý tok pro naplánovanou úlohu: stáhnout, spočítat, zapsat, poslat.

    Vrací slovník se stavem, aby GitHub Actions viděl, co se stalo.
    """
    podklady = data.nacti_podklady()
    sily = data.spocitej_sily(podklady)
    kola = dostupna_kola(podklady)
    vahy = modely.vahy_z_metrik(zaznamy.metriky_podle_modelu())

    predikce = predikce_kola(
        sily,
        podklady["databaze_kol"],
        kola,
        vahy,
        podklady["id_tymu_v_lize"],
    )

    log = zapis_do_logu(
        podklady["databaze_kol"],
        kola,
        predikce,
        podklady["historie_kol"],
        podklady["historie_je_ziva"],
    )

    kolo = najdi_dalsi_kolo(podklady["databaze_kol"], kola)
    if kolo is None:
        return {"ok": False, "duvod": "Žádné nesehrané kolo k odeslání.", "log": log}

    zprava = sestav_zpravu(kolo, podklady["databaze_kol"][kolo], predikce)
    if not zprava:
        return {"ok": False, "duvod": "Pro další kolo nešla spočítat žádná predikce.", "log": log}

    if not odeslat:
        return {"ok": True, "odeslano": False, "kolo": kolo, "zprava": zprava, "log": log}

    if not nastaveni.telegram_nastaven():
        return {"ok": False, "duvod": "Telegram není nakonfigurován.", "zprava": zprava, "log": log}

    if not nastaveni.poslat_na_telegram(zprava):
        return {"ok": False, "duvod": "Odeslání na Telegram selhalo.", "zprava": zprava, "log": log}

    return {"ok": True, "odeslano": True, "kolo": kolo, "zprava": zprava, "log": log}
