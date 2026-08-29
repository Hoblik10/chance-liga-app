import os

import pandas as pd
import streamlit as st

import streamlit.components.v1 as components

import data
import hlaseni
import kurzy
import kurz_obrazky
import kurz_zdroje
import modely
import nastaveni
import sestavy
import uloziste
import vzhled
import zaznamy

st.set_page_config(
    page_title="Chance Liga - AI Sázkařský Portál",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="auto",
)
vzhled.vloz_styly()


def obnov_ulozene_kurzy():
    """Doplní CSV z GitHubu a z prohlížeče, než se z něj čte UI."""
    if not st.session_state.get("_kurzy_github_ok"):
        if kurzy.nacti_tabulku().empty:
            vzdalene = uloziste.nacti_z_githubu()
            if vzdalene is not None and not vzdalene.empty:
                kurzy.sluc_do_souboru(vzdalene)
        st.session_state["_kurzy_github_ok"] = True

    try:
        raw = st.query_params.get(uloziste.PARAMETR_URL)
    except Exception:
        raw = None
    if raw and not st.session_state.get("_kurzy_kjson_ok"):
        z_url = uloziste.dekoduj_zalohu(raw)
        if z_url is not None and not z_url.empty:
            kurzy.sluc_do_souboru(z_url)
        st.session_state["_kurzy_kjson_ok"] = True

    uloziste.sync_prohlizec(st, components, kurzy.nacti_tabulku())


def zalohuj_kurzy_po_ulozeni():
    """Po zápisu CSV zkopíruje kurzy do prohlížeče a volitelně na GitHub."""
    df = kurzy.nacti_tabulku()
    uloziste.sync_prohlizec(st, components, df)
    if not uloziste.github_nastaven():
        return
    ok, zprava = uloziste.uloz_na_github(df)
    if ok:
        st.session_state.pop("_kurzy_github_chyba", None)
    else:
        st.session_state["_kurzy_github_chyba"] = zprava


obnov_ulozene_kurzy()

# --- NAČTENÍ DAT A SÍL ---
podklady = data.nacti_podklady()
sily = data.spocitej_sily(podklady)

databaze_kol = podklady["databaze_kol"]
ziva_kola = podklady["ziva_kola"]
id_tymu_v_lize = podklady["id_tymu_v_lize"]
df_tabulka = podklady["tabulka"]
forma_tymu = sily["forma"]
historie_kol = podklady["historie_kol"]
historie_je_ziva = podklady["historie_je_ziva"]
zapasy_zdroj = podklady["zapasy_zdroj"]
tabulka_zdroj = podklady["tabulka_zdroj"]
historie_zdroj = podklady["historie_zdroj"]
kadry, kadry_zdroj = sestavy.nacti_kadry()
absence = sestavy.nacti_absence()

# --- SIDEBAR (Výběr kola a Kompletní tabulka) ---
st.sidebar.header("📌 Navigace")
dostupna_kola = ziva_kola or modely.vyber_zobrazena_kola(
    databaze_kol, nastaveni.POCET_ZOBRAZENYCH_KOL
)

# Nabídka drží i dohraná kola, aby šlo zpětně projít výsledky a tipy.
vsechna_kola = sorted(set(historie_kol) | set(dostupna_kola))
vychozi_kolo = dostupna_kola[0] if dostupna_kola else vsechna_kola[-1]


def popis_kola(kolo):
    zapasy_kola = databaze_kol.get(kolo, [])
    if zapasy_kola and all(z["stav"] == modely.ODEHRANO for z in zapasy_kola):
        return f"{kolo}. Kolo (archiv)"
    return f"{kolo}. Kolo"


zvolene_kolo = st.sidebar.selectbox(
    "Vyber kolo:",
    options=vsechna_kola,
    index=vsechna_kola.index(vychozi_kolo),
    format_func=popis_kola,
)
st.sidebar.caption(zapasy_zdroj)

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Jaký chceš tip")
CILE_TIPU = {
    modely.CIL_USPESNOST: "Ať vychází co nejčastěji (dvojitá šance)",
    modely.CIL_INFORMACE: "Ať řekne vítěze, když si věří",
}
cil_tipu = st.sidebar.radio(
    "Co má tip maximalizovat:",
    options=tuple(CILE_TIPU),
    format_func=CILE_TIPU.get,
    label_visibility="collapsed",
)
st.sidebar.caption(
    "Dvojitá šance pokrývá dvě možnosti ze tří, takže vyjde podstatně častěji – "
    "za cenu toho, že toho míň řekne."
)

st.sidebar.markdown("---")
st.sidebar.subheader("🏆 Aktuální tabulka Chance Ligy")
st.sidebar.caption(tabulka_zdroj)
st.sidebar.dataframe(df_tabulka, width="stretch")
st.sidebar.caption(kadry_zdroj)

if nastaveni.SPORTSDB_KEY == "123":
    st.sidebar.markdown("---")
    st.sidebar.info(
        "ℹ️ Běží veřejný testovací klíč TheSportsDB, který ořezává tabulku a omezuje "
        "počet dotazů. Vlastní klíč zdarma získáš registrací na "
        "[thesportsdb.com](https://www.thesportsdb.com/) – pak ho vlož jako "
        "`SPORTSDB_KEY` do secrets."
    )

if not nastaveni.telegram_nastaven():
    st.sidebar.markdown("---")
    st.sidebar.warning(
        "⚠️ Telegram není nakonfigurován. Doplň `TELEGRAM_TOKEN` a `TELEGRAM_CHAT_ID` "
        "do `.streamlit/secrets.toml` (lokálně) nebo do App settings → Secrets (Streamlit Cloud)."
    )

if not nastaveni.API_FOOTBALL_KEY:
    st.sidebar.markdown("---")
    st.sidebar.info(
        "ℹ️ Kurzy 1/X/2 umí aplikace vzít z Tipsportu, ale z cloudu to "
        "Cloudflare často zahodí. Záloha je klíč `API_FOOTBALL_KEY` "
        "(zdarma na [dashboard.api-football.com](https://dashboard.api-football.com/)) "
        "– Chance Liga tam je, Tipsport v těch sázkovkách ale ne."
    )

POPISKY_MODELU = {
    "index_sily": "Index síly (tabulka)",
    "poisson": "Poisson / Dixon-Coles",
    "elo": "Elo rating",
    "ensemble": "Ensemble (složené)",
}

# Váhy modelů se odvozují z naměřené přesnosti; dokud není dost zápasů,
# vrátí se None a všechny modely mají stejné slovo.
metriky_modelu = zaznamy.metriky_podle_modelu()
vahy_modelu = modely.vahy_z_metrik(metriky_modelu)

if vahy_modelu:
    popis_vah = ", ".join(
        f"{POPISKY_MODELU.get(nazev, nazev)} {vaha:.0%}"
        for nazev, vaha in sorted(vahy_modelu.items(), key=lambda polozka: -polozka[1])
    )
else:
    vychozi = ", ".join(
        f"{POPISKY_MODELU.get(nazev, nazev)} {vaha:.0%}"
        for nazev, vaha in sorted(
            modely.VYCHOZI_VAHY.items(), key=lambda polozka: -polozka[1]
        )
    )
    popis_vah = (
        f"{vychozi} – z archivu minulých sezón (na vážení podle živých "
        f"výsledků je potřeba aspoň {modely.MIN_ZAPASU_PRO_VAHY} "
        f"vyhodnocených zápasů na model)"
    )


rucni_vstupy = {}
for _kolo in dostupna_kola:
    for _poradi, _zapas in enumerate(databaze_kol.get(_kolo, [])):
        vstupy = {}
        if f"pd_{_kolo}_{_poradi}" in st.session_state:
            vstupy["pohary_d"] = st.session_state[f"pd_{_kolo}_{_poradi}"]
        if f"ph_{_kolo}_{_poradi}" in st.session_state:
            vstupy["pohary_h"] = st.session_state[f"ph_{_kolo}_{_poradi}"]
        klic_d = f"abs_d_{_kolo}_{_poradi}"
        klic_h = f"abs_h_{_kolo}_{_poradi}"
        if kadry.get(_zapas["domaci"]):
            if klic_d not in st.session_state:
                st.session_state[klic_d] = list(absence.get(_zapas["domaci"]) or [])
            ids_d = list(st.session_state[klic_d] or [])
            vstupy["zraneni_d"] = modely.pokuta_z_absenci(
                kadry.get(_zapas["domaci"]) or [], ids_d
            )
            vstupy["chybejici_d"] = sestavy.jmena_hracu(
                kadry.get(_zapas["domaci"]) or [], ids_d
            )
        elif f"zd_{_kolo}_{_poradi}" in st.session_state:
            vstupy["zraneni_d"] = st.session_state[f"zd_{_kolo}_{_poradi}"]
        if kadry.get(_zapas["hoste"]):
            if klic_h not in st.session_state:
                st.session_state[klic_h] = list(absence.get(_zapas["hoste"]) or [])
            ids_h = list(st.session_state[klic_h] or [])
            vstupy["zraneni_h"] = modely.pokuta_z_absenci(
                kadry.get(_zapas["hoste"]) or [], ids_h
            )
            vstupy["chybejici_h"] = sestavy.jmena_hracu(
                kadry.get(_zapas["hoste"]) or [], ids_h
            )
        elif f"zh_{_kolo}_{_poradi}" in st.session_state:
            vstupy["zraneni_h"] = st.session_state[f"zh_{_kolo}_{_poradi}"]
        if vstupy:
            rucni_vstupy[(_kolo, _poradi)] = vstupy

predikce = hlaseni.predikce_kola(
    sily,
    databaze_kol,
    dostupna_kola,
    vahy_modelu,
    id_tymu_v_lize,
    rucni_vstupy,
    cil_tipu=cil_tipu,
    kadry=kadry,
    absence=absence,
)


def tip_zapasu(kolo, poradi):
    """Tip modelu; když ho nelze spočítat, řekne to natvrdo."""
    vysledek = predikce.get((kolo, poradi))
    if vysledek:
        return vysledek["tip"]
    return "bez tipu (tým chybí v ligové tabulce)"


def vyber_absenci(tym, klic, kadr):
    """Zaškrtávací seznam hráčů, kteří nemají nastoupit.

    Výběr se hned zapíše do ``sestavy/absence.csv``, aby ho vidělo i
    páteční hlášení na Telegram.
    """
    if not kadr:
        st.selectbox(
            "Zranění (soupiska se nenačetla)",
            list(modely.POKUTA_ZRANENI),
            key=klic.replace("abs_d_", "zd_").replace("abs_h_", "zh_"),
        )
        return

    popisky = {hrac["id"]: sestavy.popisek_hrace(hrac) for hrac in kadr}
    volby = [hrac["id"] for hrac in sestavy.serad_hrace(kadr)]
    st.multiselect(
        "Kdo chybí (zraněný, trest, nehraje)",
        volby,
        format_func=lambda identita, _p=popisky: _p.get(identita, identita),
        key=klic,
    )
    vybrani = [str(x) for x in (st.session_state.get(klic) or [])]
    ulozene = [str(x) for x in (absence.get(tym) or [])]
    if vybrani != ulozene:
        sestavy.uloz_absence_tymu(tym, vybrani, kadr)
        absence[tym] = vybrani


def text_chybejicich(jmena):
    if not jmena:
        return ""
    if len(jmena) <= 3:
        return ", ".join(jmena)
    return ", ".join(jmena[:3]) + f" +{len(jmena) - 3}"


try:
    zaznamy_stav = "📝 " + hlaseni.zapis_do_logu(
        databaze_kol, dostupna_kola, predikce, historie_kol, historie_je_ziva
    )
except Exception as chyba_zaznamu:
    zaznamy_stav = f"⚠️ Log predikcí se nepodařilo aktualizovat ({chyba_zaznamu})."


# --- HLAVNÍ OBSAH ---
st.title("⚽ Chance Liga: AI Sázkařský Portál")
st.header(f"📅 Přehled zápasů – {zvolene_kolo}. Kolo")

zápasy = databaze_kol.get(zvolene_kolo, [])

# Archiv ukazuje, co modely tipovaly před výkopem, ne dnešní přepočet.
zapsane_kola = zaznamy.zapsane_predikce_kola(zvolene_kolo)

ZNACKY_VYHODNOCENI = {True: "✅ vyšel", False: "❌ nevyšel"}


def archivni_tip(zapas):
    """Tip zapsaný před zápasem; u starších kol v logu chybět může."""
    ulozene = zapsane_kola.get((zapas["domaci"], zapas["hoste"]), {})
    return (ulozene.get("ensemble") or {}).get("tip") or zapas.get("tip") or ""

souhrn = hlaseni.radky_souhrnu(zvolene_kolo, zápasy, predikce)

# Kurzy musí být ze sázkovky. Tady se jen načtou už uložené.
ulozene_kurzy_info = kurzy.nacti_kurzy_info()
ulozene_kurzy = {
    klic: info["kurzy"] for klic, info in ulozene_kurzy_info.items()
}


def kurzy_zapasu(zapas):
    """Uložené kurzy zápasu ve zvoleném kole, nebo None."""
    return ulozene_kurzy.get((zvolene_kolo, zapas["domaci"], zapas["hoste"]))


def info_kurzu(zapas):
    """Uložené kurzy včetně toho, odkud přišly."""
    return ulozene_kurzy_info.get((zvolene_kolo, zapas["domaci"], zapas["hoste"])) or {}


if souhrn:
    # TLAČÍTKO PRO HROMADNÉ ODESLÁNÍ TIPŮ TOHOTO KOLA
    if st.button(f"📤 Odeslat zbývající tipy pro {zvolene_kolo}. kolo na Telegram", type="primary", width="stretch"):
        zprava = hlaseni.sestav_zpravu(zvolene_kolo, zápasy, predikce)
        if not zprava:
            st.warning(f"Všechny zápasy {zvolene_kolo}. kola už byly odehrány, není co posílat.")
        elif nastaveni.poslat_na_telegram(zprava):
            st.success("✅ Hromadné tipy byly úspěšně odeslány na Telegram!")
        else:
            st.error("❌ Nepodařilo se odeslat zprávu na Telegram.")

    st.subheader("🎯 Nejjistější tipy kola")
    st.caption(
        "Seřazeno od nejvyšší jistoty modelu. Jistota je jen nejvyšší z pravděpodobností "
        "1/X/2 – bez kurzu z toho neplyne, že se sázka vyplatí. Přesné skóre na archivu "
        "sedí v 12 % zápasů, pět nejčastějších dohromady v 49 %."
    )
    vzhled.siroka_tabulka(
        pd.DataFrame(
            [
                {
                    "Zápas": f"{r['domaci']} – {r['hoste']}",
                    "1": f"{r['p_domaci']:.0%}",
                    "X": f"{r['p_remiza']:.0%}",
                    "2": f"{r['p_hoste']:.0%}",
                    "Tip": r["tip"].split(" ")[0],
                    "Jistota": f"{r['jistota']:.0%}",
                    "Skóre": hlaseni.text_top_skore(r.get("top_skore"), pocet=2),
                    "Přes 2.5": (
                        f"{r['over_25']:.0%}" if r.get("over_25") is not None else "–"
                    ),
                    "Obě skórují": (
                        f"{r['obe_skoruji']:.0%}"
                        if r.get("obe_skoruji") is not None
                        else "–"
                    ),
                }
                for r in souhrn
            ]
        )
    )

    # --- HODNOTA PROTI KURZU ---
    st.subheader("💰 Kurzy z trhu")
    st.caption(uloziste.popis_zalohy())
    st.caption(
        "Hodnota sázky dává smysl jen proti kurzu ze sázkovky – ne proti "
        "převrácené pravděpodobnosti modelu. Tlačítko zkusí Tipsport; když "
        "Cloudflare server nepustí, sáhne po API-Football (Bet365 a další, "
        "ne Tipsport). Jinak nahraj screenshot nabídky, nebo kurzy opiš."
    )
    if st.session_state.get("_kurzy_github_chyba"):
        st.warning(
            "Kurzy jsou v tomhle prohlížeči, na GitHub se záloha "
            f"nepropsala: {st.session_state['_kurzy_github_chyba']}"
        )
    if st.button("📥 Načíst kurzy tohoto kola", key=f"nacti_kurzy_{zvolene_kolo}"):
        shrnuti = kurz_zdroje.nacti_a_uloz(zvolene_kolo, zápasy)
        zprava = kurz_zdroje.popis_vysledku(shrnuti)
        if shrnuti["ulozeno"]:
            zalohuj_kurzy_po_ulozeni()
            st.success(zprava)
            st.rerun()
        else:
            st.warning(zprava)

    st.caption(
        "Když trh nejde stáhnout, nahraj screenshot nabídky 1/X/2 "
        "(Tipsport, Chance, Fortuna…). Jde i víc fotek najednou – "
        "celé kolo, nebo zápasy po jednom."
    )
    fotky_kurzu = st.file_uploader(
        "Screenshot kurzů (fotka z galerie, JPG i PNG, i víc najednou)",
        # „image“ = image/* — na Windows jinak .jpg / .jfif / image/jpeg
        # často neprojde, i když na telefonu stejný soubor jde.
        type=["image", ".jpg", ".jpeg", ".jfif", ".png", ".webp"],
        accept_multiple_files=True,
        key=f"fotky_kurzu_{zvolene_kolo}",
    )
    if fotky_kurzu:
        podpis_fotek = tuple((soubor.name, soubor.size) for soubor in fotky_kurzu)
        if st.session_state.get(f"_ocr_podpis_{zvolene_kolo}") != podpis_fotek:
            with st.spinner("Čtu kurzy z fotek…"):
                shrnuti_fotek = kurz_obrazky.nacti_a_uloz(
                    zvolene_kolo, zápasy, fotky_kurzu
                )
            st.session_state[f"_ocr_podpis_{zvolene_kolo}"] = podpis_fotek
            st.session_state[f"_ocr_vysledek_{zvolene_kolo}"] = shrnuti_fotek
            if shrnuti_fotek["ulozeno"]:
                zalohuj_kurzy_po_ulozeni()
                st.rerun()
        shrnuti_fotek = st.session_state.get(f"_ocr_vysledek_{zvolene_kolo}") or {}
        zprava_fotek = kurz_obrazky.popis_vysledku(shrnuti_fotek) if shrnuti_fotek else ""
        if shrnuti_fotek.get("ulozeno"):
            st.success(zprava_fotek)
        elif zprava_fotek:
            st.warning(zprava_fotek)
        if shrnuti_fotek.get("text"):
            with st.expander("Text, který se z fotky podařilo přečíst"):
                st.code(shrnuti_fotek["text"])

    radky_hodnoty = []
    for radek in souhrn:
        zadane = ulozene_kurzy.get(
            (zvolene_kolo, radek["domaci"], radek["hoste"])
        )
        if not zadane:
            continue

        trojice = (radek["p_domaci"], radek["p_remiza"], radek["p_hoste"])
        nejlepsi = kurzy.nejlepsi_hodnota(trojice, zadane)
        if not nejlepsi:
            continue

        info = ulozene_kurzy_info.get(
            (zvolene_kolo, radek["domaci"], radek["hoste"])
        ) or {}
        radky_hodnoty.append(
            {
                "Zápas": f"{radek['domaci']} – {radek['hoste']}",
                "Sázka": nejlepsi["vysledek"],
                "Kurz": f"{nejlepsi['kurz']:.2f}",
                "Sázkovka": info.get("sazkovka") or info.get("zdroj") or "–",
                "Model": f"{nejlepsi['model']:.0%}",
                "Trh": f"{nejlepsi['trh']:.0%}",
                "Výhoda": f"{nejlepsi['hodnota']:+.1%}",
                "Kelly": f"{nejlepsi['kelly']:.1%} banku",
            }
        )

    ma_kurzy_kola = any(
        (zvolene_kolo, radek["domaci"], radek["hoste"]) in ulozene_kurzy
        for radek in souhrn
    )
    if radky_hodnoty:
        st.markdown("**Kde má model výhodu proti kurzu**")
        vzhled.siroka_tabulka(pd.DataFrame(radky_hodnoty).set_index("Zápas"))
        st.caption(
            f"Zobrazí se jen sázky s výhodou aspoň {kurzy.MIN_HODNOTA:.0%}. "
            "Výhoda je očekávaný výnos na vsazenou korunu podle modelu – "
            "a stojí a padá s tím, jestli má model pravdu. Trh vidí i sestavy, "
            "takže velký rozdíl bývá spíš chyba modelu než příležitost."
        )
    elif not ma_kurzy_kola:
        st.caption(
            "Zatím tu není žádný kurz ze sázkovky, takže není co porovnávat."
        )

elif zápasy:
    st.subheader("📦 Archiv kola")
    st.caption(
        "Tipy pocházejí z logu – jsou to ty, které vznikly před výkopem, "
        "ne dnešní přepočet."
    )
    vzhled.siroka_tabulka(
        pd.DataFrame(
            [
                {
                    "Zápas": f"{z['domaci']} – {z['hoste']}",
                    "Termín": z.get("datum", ""),
                    "Skóre": z.get("skore", "-"),
                    "Tip": (archivni_tip(z) or "–").split(" ")[0],
                    "Vyhodnocení": ZNACKY_VYHODNOCENI.get(
                        modely.vyhodnot_tip(archivni_tip(z), z.get("skore")), "–"
                    ),
                }
                for z in zápasy
            ]
        ).set_index("Zápas")
    )

st.divider()

# Vykreslení jednotlivých zápasů
for i, z in enumerate(zápasy):
    with st.container():
        # Odehrané zápasy (zobrazení výsledku a kontrola úspěšnosti)
        if z['stav'] == "✅ Odehráno":
            st.markdown(f"### 🏟️ {z['domaci']} vs {z['hoste']} (ARCHIV)")
            st.markdown(
                f"**Stav:** {z['stav']} | **Konečné skóre:** {z['skore']} | "
                f"**Termín:** {z.get('datum', '–')}"
            )

            ulozene = zapsane_kola.get((z["domaci"], z["hoste"]), {})
            puvodni_tip = archivni_tip(z)

            if not puvodni_tip:
                st.caption(
                    "Pro tento zápas není v logu žádná predikce – aplikace ho "
                    "poprvé viděla až po výkopu."
                )
            else:
                st.markdown(f"🎯 **Zaznamenaný tip:** {puvodni_tip}")
                sedel = modely.vyhodnot_tip(puvodni_tip, z['skore'])
                if sedel is True:
                    st.success("Vyhodnocení: ✅ Tip vyšel")
                elif sedel is False:
                    st.error("Vyhodnocení: ❌ Tip nevyšel")
                else:
                    st.caption("Vyhodnocení: výsledek se nepodařilo porovnat.")

            if ulozene:
                with st.expander("🔬 Co který model tipoval před zápasem"):
                    radky_modelu = []
                    for nazev_modelu in (*modely.NAZVY_MODELU, "ensemble"):
                        hodnoty = ulozene.get(nazev_modelu)
                        if not hodnoty:
                            continue
                        radky_modelu.append(
                            {
                                "Model": POPISKY_MODELU.get(nazev_modelu, nazev_modelu),
                                "1": f"{hodnoty['p_domaci']:.0%}",
                                "X": f"{hodnoty['p_remiza']:.0%}",
                                "2": f"{hodnoty['p_hoste']:.0%}",
                                "Tip": str(hodnoty["tip"]).split(" ")[0],
                                "Vyhodnocení": ZNACKY_VYHODNOCENI.get(
                                    modely.vyhodnot_tip(hodnoty["tip"], z["skore"]), "–"
                                ),
                            }
                        )

                    if radky_modelu:
                        st.table(pd.DataFrame(radky_modelu).set_index("Model"))

                    zapsano = (ulozene.get("ensemble") or {}).get("zapsano")
                    if zapsano:
                        st.caption(f"Predikce zapsaná {zapsano}, tedy před výkopem.")

        # Odložené a zrušené zápasy (stav hlásí přímo API)
        elif z['stav'].startswith("🔴"):
            st.markdown(f"### 🏟️ {z['domaci']} vs {z['hoste']}")
            if z.get("poznamka_termin"):
                st.warning(f"{z['poznamka_termin']} – v tomto kole se nehraje, tip se neodesílá.")
            else:
                st.markdown(f"🕒 Původní termín: {z['datum']}")
                st.warning(f"{z['stav']} – zápas se v tomto kole nehraje, tip se neodesílá.")

        # Nadcházející / Dnes hrané zápasy
        else:
            col1, col2 = st.columns([3, 2])
            
            with col1:
                st.markdown(f"### 🏟️ {z['domaci']} vs {z['hoste']}")
                st.text(f"🕒 Výkop: {z['datum']} | Stav: {z['stav']}")

                forma_domaci = forma_tymu.get(z['domaci'], [])
                forma_hoste = forma_tymu.get(z['hoste'], [])
                if forma_domaci or forma_hoste:
                    st.caption(
                        f"📈 Forma – {z['domaci']}: {' '.join(forma_domaci) or '–'} | "
                        f"{z['hoste']}: {' '.join(forma_hoste) or '–'}"
                    )

                p = predikce.get((zvolene_kolo, i))

                with st.expander("📐 Vstupy modelu (únava z pohárů + zranění)"):
                    if p is None:
                        st.warning(
                            "Tým nebyl nalezen v ligové tabulce, model pro tento zápas nelze spočítat."
                        )
                    else:
                        st.caption(
                            "Únava se předvyplní z pohárových zápasů. Soupisky se "
                            "stahují z ChanceLiga.cz; zranění a tresty zaškrtni ručně "
                            "– žádný volný zdroj je pro tuhle ligu nenesí. Výběr platí, "
                            "dokud ho nesmažeš, i v dalším kole."
                        )
                        stupne_poharu = list(modely.POKUTA_POHARY)

                        m1, m2 = st.columns(2)
                        with m1:
                            st.markdown(f"**{z['domaci']}** (domácí)")
                            st.selectbox(
                                "Poháry",
                                stupne_poharu,
                                index=stupne_poharu.index(p["vychozi_pohary_domaci"]),
                                key=f"pd_{zvolene_kolo}_{i}",
                            )
                            if p["poznamka_domaci"]:
                                st.caption(f"⚠️ {p['poznamka_domaci']}")
                            vyber_absenci(
                                z["domaci"],
                                f"abs_d_{zvolene_kolo}_{i}",
                                kadry.get(z["domaci"]) or [],
                            )
                        with m2:
                            st.markdown(f"**{z['hoste']}** (hosté)")
                            st.selectbox(
                                "Poháry",
                                stupne_poharu,
                                index=stupne_poharu.index(p["vychozi_pohary_hoste"]),
                                key=f"ph_{zvolene_kolo}_{i}",
                            )
                            if p["poznamka_hoste"]:
                                st.caption(f"⚠️ {p['poznamka_hoste']}")
                            vyber_absenci(
                                z["hoste"],
                                f"abs_h_{zvolene_kolo}_{i}",
                                kadry.get(z["hoste"]) or [],
                            )

                        if st.button(
                            "📋 Načíst oficiální sestavu zápasu",
                            key=f"ns_{zvolene_kolo}_{i}",
                        ):
                            try:
                                nactena = sestavy.stahni_sestavu_zapasu(
                                    z["domaci"], z["hoste"]
                                )
                                st.session_state[f"sest_{zvolene_kolo}_{i}"] = (
                                    nactena or {"prazdna": True}
                                )
                            except Exception as chyba_sestavy:
                                st.session_state[f"sest_{zvolene_kolo}_{i}"] = {
                                    "chyba": str(chyba_sestavy)
                                }

                        nactena_sestava = st.session_state.get(f"sest_{zvolene_kolo}_{i}")
                        if nactena_sestava and nactena_sestava.get("chyba"):
                            st.caption(
                                f"Sestavu se nepodařilo stáhnout ({nactena_sestava['chyba']})."
                            )
                        elif nactena_sestava and nactena_sestava.get("prazdna"):
                            st.caption(
                                "Oficiální sestava na ChanceLiga.cz ještě není. "
                                "Bývá až kolem výkopu, na páteční Telegram to nestihne."
                            )
                        elif nactena_sestava and nactena_sestava.get("domaci"):
                            def _jmena(skupina):
                                return ", ".join(
                                    h["jmeno"] for h in (skupina or []) if h.get("jmeno")
                                ) or "–"

                            st.caption(
                                f"Základ {z['domaci']}: {_jmena(nactena_sestava['domaci']['zaklad'])}"
                            )
                            st.caption(
                                f"Základ {z['hoste']}: {_jmena(nactena_sestava['hoste']['zaklad'])}"
                            )
                            if st.button(
                                "Označit, kdo v soupisce zápasu není",
                                key=f"os_{zvolene_kolo}_{i}",
                            ):
                                for strana, tym in (
                                    ("domaci", z["domaci"]),
                                    ("hoste", z["hoste"]),
                                ):
                                    klic = (
                                        f"abs_d_{zvolene_kolo}_{i}"
                                        if strana == "domaci"
                                        else f"abs_h_{zvolene_kolo}_{i}"
                                    )
                                    mimo = sestavy.id_mimo_sestavu(
                                        kadry.get(tym) or [],
                                        nactena_sestava.get(strana),
                                    )
                                    st.session_state[klic] = mimo
                                    sestavy.uloz_absence_tymu(
                                        tym, mimo, kadry.get(tym) or []
                                    )
                                    absence[tym] = mimo
                                st.rerun()

                        if "sila_domaci" in p:
                            chybi_d = text_chybejicich(p.get("chybejici_domaci") or [])
                            chybi_h = text_chybejicich(p.get("chybejici_hoste") or [])
                            dodatek_d = f" – {chybi_d}" if chybi_d else ""
                            dodatek_h = f" – {chybi_h}" if chybi_h else ""
                            st.markdown(
                                f"* **{z['domaci']}:** {p['sila_domaci_zaklad']} → **{p['sila_domaci']}** "
                                f"(oslabení {p['dopad_domaci']} %{dodatek_d})\n"
                                f"* **{z['hoste']}:** {p['sila_hoste_zaklad']} → **{p['sila_hoste']}** "
                                f"(oslabení {p['dopad_hoste']} %{dodatek_h})"
                            )
                        else:
                            st.caption(
                                "Index síly není k dispozici (tým chybí v tabulce), "
                                "predikci nesou Poisson a Elo."
                            )

                with st.expander("💰 Kurzy a hodnota sázky"):
                    info = info_kurzu(z)
                    zadane = info.get("kurzy") or kurzy_zapasu(z)
                    if p is None:
                        st.caption(
                            "Bez predikce nejde hodnotu spočítat – model tenhle "
                            "zápas neumí."
                        )
                    else:
                        st.caption(
                            "Sem patří kurz ze sázkovky. Prázdné pole je záměr – "
                            "modelové 1/p by vypadalo jako trh, ale nic takového "
                            "vsadit nejde. Po vyplnění všech tří se kurzy uloží "
                            "samy (Enter stačí)."
                        )
                        if info.get("sazkovka") or info.get("zdroj"):
                            st.caption(
                                f"Uloženo z: {info.get('sazkovka') or info.get('zdroj')}"
                            )
                        trojice_modelu = (p["p_domaci"], p["p_remiza"], p["p_hoste"])

                        sloupce_kurzu = st.columns(3)
                        zadane_kurzy = tuple(
                            sloupce_kurzu[poradi_kurzu].number_input(
                                popisek,
                                min_value=kurzy.MIN_KURZ,
                                max_value=kurzy.MAX_KURZ,
                                value=(
                                    float(zadane[poradi_kurzu]) if zadane else None
                                ),
                                step=0.05,
                                placeholder="ze sázkovky",
                                key=f"kurz_trh_{popisek}_{zvolene_kolo}_{i}",
                            )
                            for poradi_kurzu, popisek in enumerate(("1", "X", "2"))
                        )
                        platne_kurzy = all(
                            kurzy.platny_kurz(hodnota) for hodnota in zadane_kurzy
                        )

                        if platne_kurzy and kurzy.kurzy_se_lisi(zadane, zadane_kurzy):
                            kurzy.uloz_kurz(
                                zvolene_kolo,
                                z["domaci"],
                                z["hoste"],
                                zadane_kurzy,
                                zdroj="rucne",
                                sazkovka="",
                            )
                            zalohuj_kurzy_po_ulozeni()
                            st.session_state[f"kurz_ulozen_{zvolene_kolo}_{i}"] = True
                            st.rerun()

                        if st.session_state.get(f"kurz_ulozen_{zvolene_kolo}_{i}"):
                            st.caption("Uloženo – po obnovení stránky tu zůstanou.")

                        if st.button("💾 Uložit kurzy", key=f"ku_{zvolene_kolo}_{i}"):
                            if not platne_kurzy:
                                st.error("Doplň všechny tři kurzy 1 / X / 2.")
                            else:
                                kurzy.uloz_kurz(
                                    zvolene_kolo,
                                    z["domaci"],
                                    z["hoste"],
                                    zadane_kurzy,
                                    zdroj="rucne",
                                    sazkovka="",
                                )
                                zalohuj_kurzy_po_ulozeni()
                                st.session_state[f"kurz_ulozen_{zvolene_kolo}_{i}"] = True
                                st.success(
                                    "Kurzy uloženy, projeví se v přehledu kola."
                                )
                                st.rerun()

                        if not platne_kurzy:
                            st.info(
                                "Bez kurzů ze sázkovky nejde spočítat, jestli "
                                "má model výhodu. Načti je tlačítkem nahoře, "
                                "nebo je opiš."
                            )
                        else:
                            st.table(
                                pd.DataFrame(
                                    [
                                        {
                                            "Výsledek": radek["vysledek"],
                                            "Model": f"{radek['model']:.0%}",
                                            "Trh (bez marže)": f"{radek['trh']:.0%}",
                                            "Kurz": f"{radek['kurz']:.2f}",
                                            "Výhoda": f"{radek['hodnota']:+.1%}",
                                            "Kelly": f"{radek['kelly']:.1%}",
                                        }
                                        for radek in kurzy.prehled_hodnoty(
                                            trojice_modelu, zadane_kurzy
                                        )
                                    ]
                                ).set_index("Výsledek")
                            )

                            nejlepsi_sazka = kurzy.nejlepsi_hodnota(
                                trojice_modelu, zadane_kurzy
                            )
                            if nejlepsi_sazka:
                                st.success(
                                    f"Podle modelu má výhodu **{nejlepsi_sazka['vysledek']}** "
                                    f"při kurzu {nejlepsi_sazka['kurz']:.2f}: "
                                    f"{nejlepsi_sazka['hodnota']:+.1%} na korunu, "
                                    f"Kelly doporučuje {nejlepsi_sazka['kelly']:.1%} banku."
                                )
                            else:
                                st.info(
                                    f"Žádný výsledek nemá výhodu aspoň "
                                    f"{kurzy.MIN_HODNOTA:.0%} – tady se sázet nevyplatí."
                                )

                            st.caption(
                                f"Marže kanceláře: {kurzy.marze(*zadane_kurzy):.1%} | "
                                f"odchylka modelu od trhu: "
                                f"{kurzy.rozdil_od_trhu(trojice_modelu, zadane_kurzy):.0%}"
                            )

                if st.button(f"📲 Poslat tento tip na Telegram", key=f"tg_{zvolene_kolo}_{i}"):
                    zprava = (
                        f"Chance Liga (Kolo {zvolene_kolo})\n\n"
                        f"{z['domaci']} vs {z['hoste']}\n"
                        f"Datum: {z['datum']}\n\n"
                        f"Tip: {tip_zapasu(zvolene_kolo, i)}"
                    )
                    if p:
                        zprava += (
                            f"\nPravděpodobnosti: 1 {p['p_domaci']:.0%} | "
                            f"X {p['p_remiza']:.0%} | 2 {p['p_hoste']:.0%}"
                        )
                        prehled = p.get("prehled_skore")
                        if prehled:
                            zprava += (
                                f"\nSkóre: {hlaseni.text_top_skore(prehled['nejcastejsi'])}"
                                f"\nPřes 2.5: {prehled['over'][2.5]:.0%}"
                                f" · obě skórují: {prehled['obe_skoruji']:.0%}"
                            )
                    if nastaveni.poslat_na_telegram(zprava):
                        st.success("Odesláno na Telegram!")
                    else:
                        st.error("Chyba odeslání.")

            with col2:
                st.markdown("#### 📊 Predikce modelů")
                if p is None:
                    st.markdown("Žádný model nedokázal tento zápas spočítat.")
                else:
                    st.markdown(
                        f"- Výhra domácích: **{p['p_domaci']:.0%}**\n"
                        f"- Remíza: **{p['p_remiza']:.0%}**\n"
                        f"- Výhra hostů: **{p['p_hoste']:.0%}**"
                    )
                    st.info(f"🎯 **Tip (ensemble):** {p['tip']}")
                    st.caption(f"Jistota modelu: **{p['jistota']:.0%}**")

                    prehled = p.get("prehled_skore")
                    if prehled:
                        ocekavane = prehled["ocekavane"]
                        st.caption(
                            f"Očekávané góly: **{ocekavane[0]:.1f} : {ocekavane[1]:.1f}**"
                        )
                        st.table(
                            pd.DataFrame(
                                [
                                    {
                                        "Skóre": f"{goly_d}:{goly_h}",
                                        "Šance": f"{pravdepodobnost:.0%}",
                                    }
                                    for (goly_d, goly_h), pravdepodobnost in prehled[
                                        "nejcastejsi"
                                    ]
                                ]
                            ).set_index("Skóre")
                        )
                        st.caption(
                            f"Přes 1.5: **{prehled['over'][1.5]:.0%}** · "
                            f"přes 2.5: **{prehled['over'][2.5]:.0%}** · "
                            f"přes 3.5: **{prehled['over'][3.5]:.0%}**"
                        )
                        st.caption(
                            f"Obě skórují: **{prehled['obe_skoruji']:.0%}** · "
                            f"čisté konto domácích: **{prehled['ciste_vitezstvi_domaci']:.0%}** · "
                            f"hostů: **{prehled['ciste_vitezstvi_hoste']:.0%}**"
                        )
                    elif p.get("nejcastejsi_skore"):
                        (goly_d, goly_h), p_skore = p["nejcastejsi_skore"]
                        st.caption(
                            f"Nejčastější skóre podle Poissona: **{goly_d}:{goly_h}** "
                            f"({p_skore:.0%})"
                        )

                    with st.expander("🔬 Rozpad podle modelů"):
                        radky_modelu = []
                        for nazev_modelu in modely.NAZVY_MODELU:
                            trojice = p["modely"].get(nazev_modelu)
                            if not trojice:
                                continue
                            radky_modelu.append(
                                {
                                    "Model": POPISKY_MODELU.get(nazev_modelu, nazev_modelu),
                                    "1": f"{trojice[0]:.0%}",
                                    "X": f"{trojice[1]:.0%}",
                                    "2": f"{trojice[2]:.0%}",
                                    "Tip": modely.tip_z_pravdepodobnosti(*trojice).split(" ")[0],
                                }
                            )

                        if radky_modelu:
                            st.table(pd.DataFrame(radky_modelu).set_index("Model"))

                        st.caption(
                            f"Váhy: {popis_vah}"
                        )

                        if p.get("ocekavane_goly"):
                            gd, gh = p["ocekavane_goly"]
                            st.caption(f"Očekávané góly: {gd:.2f} – {gh:.2f}")
                        if p.get("elo_domaci") and p.get("elo_hoste"):
                            st.caption(
                                f"Elo: {z['domaci']} {p['elo_domaci']:.0f} · "
                                f"{z['hoste']} {p['elo_hoste']:.0f}"
                            )

        st.divider()

# --- PŘESNOST MODELŮ ---
st.markdown("---")
st.header("🎯 Přesnost modelů")
st.caption(zaznamy_stav)
if historie_zdroj:
    st.caption(historie_zdroj)

st.markdown(
    "Měří se jen predikce zapsané **před** zápasem. "
    f"**Brier score** (0 = dokonalé) a **log loss** jsou nižší lepší; "
    f"náhodné tipnutí dá Brier {modely.BRIER_NAHODNY:.3f} a log loss "
    f"{modely.LOGLOSS_NAHODNY:.3f}. Model má cenu jen tehdy, když je pod touhle hranicí."
)

if not metriky_modelu:
    st.info(
        "Zatím není vyhodnocená žádná predikce. Záznamy vznikají průběžně – "
        "výsledky se doplní, jakmile se zapsané zápasy odehrají."
    )
else:
    radky_presnosti = []
    for nazev_modelu, metriky in sorted(metriky_modelu.items()):
        if not metriky:
            continue
        radky_presnosti.append(
            {
                "Model": POPISKY_MODELU.get(nazev_modelu, nazev_modelu),
                "Zápasů": metriky["zapasu"],
                "Brier": round(metriky["brier"], 3),
                "Log loss": round(metriky["log_loss"], 3),
                "Trefa favorita": f"{metriky['trefa_favorita']:.0%}",
                "Úspěšnost tipu": (
                    f"{metriky['uspesnost_tipu']:.0%}"
                    if metriky["uspesnost_tipu"] is not None
                    else "–"
                ),
            }
        )

    if radky_presnosti:
        vzhled.siroka_tabulka(
            pd.DataFrame(radky_presnosti).sort_values("Brier").set_index("Model")
        )
        st.caption(
            "Úspěšnost tipu je zavádějící metrika – model, který vždy tipne "
            "favorita, ji má vysokou i bez skutečné hodnoty. Rozhoduj se podle Brier score."
        )

    radky_spolehlivosti = zaznamy.spolehlivost_modelu()
    if radky_spolehlivosti:
        st.subheader("📏 Sedí slíbená jistota?")
        st.table(
            pd.DataFrame(
                [
                    {
                        "Pásmo jistoty": radek["pasmo"],
                        "Zápasů": radek["zapasu"],
                        "Model sliboval": f"{radek['slibeno']:.0%}",
                        "Skutečnost": f"{radek['skutecnost']:.0%}",
                    }
                    for radek in radky_spolehlivosti
                ]
            ).set_index("Pásmo jistoty")
        )
        st.caption(
            "Kdyby model sliboval 60 % a trefoval polovinu, jsou jeho čísla "
            "nafouknutá a tipy nad prahem vycházejí méně, než tvrdí. "
            "Pár desítek zápasů ale ještě nic neprozradí – čti to až po delší době."
        )


with st.expander("📝 Poslední zapsané predikce"):
    prehled = zaznamy.prehled_zaznamu(pocet=40)
    if prehled.empty:
        st.markdown("Log je zatím prázdný.")
    else:
        st.dataframe(prehled, hide_index=True, width="stretch")
        st.caption(f"Ukládá se do `{os.path.basename(zaznamy.SOUBOR_ZAZNAMU)}`.")
