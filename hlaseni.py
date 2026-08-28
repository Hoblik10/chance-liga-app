"""Sestavení a odeslání hlášení s tipy na další kolo.

Stejný text jde na Telegram z tlačítka v aplikaci i z naplánované úlohy.
Naplánovaná úloha bere kolo, které se hraje v nejbližších dnech,
ne odložené zbytky staršího kola.
"""

import json
import os
from datetime import datetime, timedelta

import data
import modely
import nastaveni
import sestavy
import zaznamy

SOUBOR_TELEGRAMU = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "telegram_odeslano.json"
)

VYCHOZI_ZRANENI = list(modely.POKUTA_ZRANENI)[0]

# Páteční hlášení pokrývá nadcházející víkend, ne zápasy za tři týdny.
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


def _zraneni_tymu(tym, vstupy, klic, kadry, absence):
    """Číslo z UI, jinak z uložených absencí, jinak kompletní kádr."""
    if klic in vstupy:
        return vstupy[klic]
    if kadry and tym in kadry:
        return sestavy.pokuta_pro_tym(tym, kadry, absence)
    return VYCHOZI_ZRANENI


def predikce_kola(
    sily,
    databaze_kol,
    kola,
    vahy,
    id_tymu_v_lize,
    rucni_vstupy=None,
    cil_tipu=None,
    kadry=None,
    absence=None,
):
    """Predikce všech zápasů ve vybraných kolech.

    ``rucni_vstupy`` je volitelný slovník (kolo, pořadí) ->
    {pohary_d, pohary_h, zraneni_d, zraneni_h} z UI. Bez něj se použije
    odhad únavy z pohárů a absence uložené v ``sestavy/absence.csv``.

    ``cil_tipu`` rozhoduje, jestli má tip vycházet co nejčastěji (dvojitá
    šance), nebo rovnou pojmenovat vítěze.
    """
    rucni_vstupy = rucni_vstupy or {}
    kadry = kadry or {}
    absence = absence or {}
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
            zraneni_d = _zraneni_tymu(
                zapas["domaci"], vstupy, "zraneni_d", kadry, absence
            )
            zraneni_h = _zraneni_tymu(
                zapas["hoste"], vstupy, "zraneni_h", kadry, absence
            )
            vysledek = modely.predikuj_vsemi(
                sily,
                zapas["domaci"],
                zapas["hoste"],
                pohary_domaci=vstupy.get("pohary_d", vychozi_d),
                pohary_hoste=vstupy.get("pohary_h", vychozi_h),
                zraneni_domaci=zraneni_d,
                zraneni_hoste=zraneni_h,
                vahy=vahy,
                cil_tipu=cil_tipu,
            )

            if vysledek is None:
                predikce[(kolo, poradi)] = None
                continue

            vysledek["vychozi_pohary_domaci"] = vychozi_d
            vysledek["vychozi_pohary_hoste"] = vychozi_h
            vysledek["poznamka_domaci"] = poznamka_d
            vysledek["poznamka_hoste"] = poznamka_h
            vysledek["chybejici_domaci"] = vstupy.get("chybejici_d") or sestavy.jmena_hracu(
                kadry.get(zapas["domaci"]) or [], absence.get(zapas["domaci"]) or []
            )
            vysledek["chybejici_hoste"] = vstupy.get("chybejici_h") or sestavy.jmena_hracu(
                kadry.get(zapas["hoste"]) or [], absence.get(zapas["hoste"]) or []
            )
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

        prehled = vysledek.get("prehled_skore") or {}
        nejcastejsi = prehled.get("nejcastejsi") or ([skore] if skore else [])
        over = (prehled.get("over") or {}).get(2.5)
        obe = prehled.get("obe_skoruji")

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
                "top_skore": nejcastejsi,
                "over_25": over,
                "obe_skoruji": obe,
                "ocekavane": prehled.get("ocekavane"),
            }
        )

    radky.sort(key=lambda r: r["jistota"], reverse=True)
    return radky


def text_top_skore(nejcastejsi, pocet=3):
    """2:1 (12%) · 1:1 (11%) · 2:0 (10%)"""
    if not nejcastejsi:
        return "–"

    return " · ".join(
        f"{goly_d}:{goly_h} ({p:.0%})"
        for (goly_d, goly_h), p in nejcastejsi[:pocet]
    )


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
        f"Chance Liga · {kolo}. kolo",
        "od nejvyšší jistoty",
        "",
    ]

    for cislo, radek in enumerate(radky, start=1):
        domaci = kratky_nazev(radek["domaci"])
        hoste = kratky_nazev(radek["hoste"])
        tip = radek["tip"].split(" ")[0]
        vykop = kratky_vykop(radek["datum"])

        bloky.append(f"{cislo}. {domaci} – {hoste}")
        if vykop:
            bloky.append(vykop)
        bloky.append(
            f"1 {radek['p_domaci']:.0%} · "
            f"X {radek['p_remiza']:.0%} · "
            f"2 {radek['p_hoste']:.0%}"
        )
        bloky.append(f"Tip {tip} · jistota {radek['jistota']:.0%}")
        bloky.append(f"skóre {text_top_skore(radek.get('top_skore'))}")
        if radek.get("over_25") is not None and radek.get("obe_skoruji") is not None:
            bloky.append(
                f"přes 2.5 {radek['over_25']:.0%} · "
                f"obě skórují {radek['obe_skoruji']:.0%}"
            )
        bloky.append("")

    return "\n".join(bloky).strip()


def _datum_v_praze(ted=None):
    ted = ted or datetime.now(data.PASMO_PRAHA)
    if ted.tzinfo is None:
        ted = ted.replace(tzinfo=data.PASMO_PRAHA)
    return ted.astimezone(data.PASMO_PRAHA).strftime("%Y-%m-%d")


def nacti_odeslani_telegramu(cesta=None):
    """Poslední úspěšné páteční odeslání, nebo prázdný slovník."""
    cesta = cesta or SOUBOR_TELEGRAMU
    if not os.path.exists(cesta):
        return {}
    try:
        with open(cesta, encoding="utf-8") as soubor:
            return json.load(soubor) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def uz_odeslano_dnes(kolo, sezona=None, ted=None, cesta=None):
    """Stejné kolo ve stejný pražský den už na Telegram šlo."""
    zaznam = nacti_odeslani_telegramu(cesta)
    return (
        str(zaznam.get("kolo")) == str(kolo)
        and str(zaznam.get("sezona") or "") == str(sezona or nastaveni.SEZONA_SPORTSDB)
        and str(zaznam.get("datum") or "") == _datum_v_praze(ted)
    )


def uz_odeslano_nedavno(kolo, sezona=None, ted=None, cesta=None, hodin=36):
    """Stejné kolo v posledních hodinách už šlo – sobotní záloha nemá duplikovat pátek."""
    zaznam = nacti_odeslani_telegramu(cesta)
    if str(zaznam.get("kolo")) != str(kolo):
        return False
    if str(zaznam.get("sezona") or "") != str(sezona or nastaveni.SEZONA_SPORTSDB):
        return False

    ted = ted or datetime.now(data.PASMO_PRAHA)
    if ted.tzinfo is None:
        ted = ted.replace(tzinfo=data.PASMO_PRAHA)

    cas_text = str(zaznam.get("cas") or "")
    try:
        odeslano = datetime.strptime(cas_text, "%Y-%m-%d %H:%M")
    except ValueError:
        return str(zaznam.get("datum") or "") == _datum_v_praze(ted)

    if odeslano.tzinfo is None:
        odeslano = odeslano.replace(tzinfo=data.PASMO_PRAHA)
    return timedelta(0) <= (ted - odeslano) < timedelta(hours=hodin)


def uloz_odeslani_telegramu(kolo, sezona=None, ted=None, cesta=None):
    """Zapíše, že tohle kolo dnes na Telegram opravdu odešlo."""
    cesta = cesta or SOUBOR_TELEGRAMU
    obsah = {
        "kolo": kolo,
        "sezona": sezona or nastaveni.SEZONA_SPORTSDB,
        "datum": _datum_v_praze(ted),
        "cas": (ted or datetime.now(data.PASMO_PRAHA)).strftime("%Y-%m-%d %H:%M"),
    }
    with open(cesta, "w", encoding="utf-8") as soubor:
        json.dump(obsah, soubor, ensure_ascii=False, indent=2)
    return obsah


def priprav_a_posli(odeslat=True):
    """Celý tok pro naplánovanou úlohu: stáhnout, spočítat, zapsat, poslat.

    Vrací slovník se stavem, aby GitHub Actions viděl, co se stalo.
    """
    podklady = data.nacti_podklady()
    sily = data.spocitej_sily(podklady)
    kola = dostupna_kola(podklady)
    vahy = modely.vahy_z_metrik(zaznamy.metriky_podle_modelu())
    kadry, _ = sestavy.nacti_kadry()
    absence = sestavy.nacti_absence()

    predikce = predikce_kola(
        sily,
        podklady["databaze_kol"],
        kola,
        vahy,
        podklady["id_tymu_v_lize"],
        kadry=kadry,
        absence=absence,
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

    if uz_odeslano_nedavno(kolo):
        return {
            "ok": True,
            "odeslano": False,
            "kolo": kolo,
            "zprava": zprava,
            "log": log,
            "duvod": "Toto kolo už na Telegram nedávno šlo.",
        }

    if not nastaveni.telegram_nastaven():
        return {"ok": False, "duvod": "Telegram není nakonfigurován.", "zprava": zprava, "log": log}

    if not nastaveni.poslat_na_telegram(zprava):
        return {"ok": False, "duvod": "Odeslání na Telegram selhalo.", "zprava": zprava, "log": log}

    uloz_odeslani_telegramu(kolo)
    return {"ok": True, "odeslano": True, "kolo": kolo, "zprava": zprava, "log": log}
