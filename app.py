import os

import pandas as pd
import streamlit as st

import data
import hlaseni
import modely
import nastaveni
import zaznamy

st.set_page_config(
    page_title="Chance Liga - AI Sázkařský Portál",
    page_icon="⚽",
    layout="wide",
)

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

# --- SIDEBAR (Výběr kola a Kompletní tabulka) ---
st.sidebar.header("📌 Navigace")
dostupna_kola = ziva_kola or modely.vyber_zobrazena_kola(
    databaze_kol, nastaveni.POCET_ZOBRAZENYCH_KOL
)
zvolene_kolo = st.sidebar.selectbox(
    "Vyber kolo:", options=dostupna_kola, format_func=lambda x: f"{x}. Kolo"
)
st.sidebar.caption(zapasy_zdroj)

st.sidebar.markdown("---")
st.sidebar.subheader("🏆 Aktuální tabulka Chance Ligy")
st.sidebar.caption(tabulka_zdroj)
st.sidebar.dataframe(df_tabulka, width="stretch")

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
    popis_vah = (
        f"zatím stejné (na vážení podle přesnosti je potřeba "
        f"aspoň {modely.MIN_ZAPASU_PRO_VAHY} vyhodnocených zápasů na model)"
    )


rucni_vstupy = {}
for _kolo in dostupna_kola:
    for _poradi, _zapas in enumerate(databaze_kol.get(_kolo, [])):
        vstupy = {}
        if f"pd_{_kolo}_{_poradi}" in st.session_state:
            vstupy["pohary_d"] = st.session_state[f"pd_{_kolo}_{_poradi}"]
        if f"ph_{_kolo}_{_poradi}" in st.session_state:
            vstupy["pohary_h"] = st.session_state[f"ph_{_kolo}_{_poradi}"]
        if f"zd_{_kolo}_{_poradi}" in st.session_state:
            vstupy["zraneni_d"] = st.session_state[f"zd_{_kolo}_{_poradi}"]
        if f"zh_{_kolo}_{_poradi}" in st.session_state:
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
)


def tip_zapasu(kolo, poradi):
    """Tip modelu; když ho nelze spočítat, řekne to natvrdo."""
    vysledek = predikce.get((kolo, poradi))
    if vysledek:
        return vysledek["tip"]
    return "bez tipu (tým chybí v ligové tabulce)"


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

# TLAČÍTKO PRO HROMADNÉ ODESLÁNÍ TIPŮ TOHOTO KOLA
if st.button(f"📤 Odeslat zbývající tipy pro {zvolene_kolo}. kolo na Telegram", type="primary", width="stretch"):
    zprava = hlaseni.sestav_zpravu(zvolene_kolo, zápasy, predikce)
    if not zprava:
        st.warning(f"Všechny zápasy {zvolene_kolo}. kola už byly odehrány, není co posílat.")
    elif nastaveni.poslat_na_telegram(zprava):
        st.success("✅ Hromadné tipy byly úspěšně odeslány na Telegram!")
    else:
        st.error("❌ Nepodařilo se odeslat zprávu na Telegram.")

souhrn = hlaseni.radky_souhrnu(zvolene_kolo, zápasy, predikce)
if souhrn:
    st.subheader("🎯 Nejjistější tipy kola")
    st.caption(
        "Seřazeno od nejvyšší jistoty modelu. Jistota je jen nejvyšší z pravděpodobností "
        "1/X/2 – bez kurzu z toho neplyne, že se sázka vyplatí."
    )
    st.table(
        pd.DataFrame(
            [
                {
                    "Zápas": f"{r['domaci']} – {r['hoste']}",
                    "1": f"{r['p_domaci']:.0%}",
                    "X": f"{r['p_remiza']:.0%}",
                    "2": f"{r['p_hoste']:.0%}",
                    "Tip": r["tip"].split(" ")[0],
                    "Jistota": f"{r['jistota']:.0%}",
                    "Nejčastější skóre": r["skore"],
                }
                for r in souhrn
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
            st.markdown(f"**Stav:** {z['stav']} | **Konečné skóre:** {z['skore']}")

            puvodni_tip = z.get('tip')
            if not puvodni_tip:
                st.caption("Pro tento zápas nebyl zaznamenán žádný tip.")
            else:
                st.markdown(f"🎯 **Zaznamenaný tip:** {puvodni_tip}")
                sedel = modely.vyhodnot_tip(puvodni_tip, z['skore'])
                if sedel is True:
                    st.success("Vyhodnocení: ✅ Tip vyšel")
                elif sedel is False:
                    st.error("Vyhodnocení: ❌ Tip nevyšel")
                else:
                    st.caption("Vyhodnocení: výsledek se nepodařilo porovnat.")

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
                            "Únava se předvyplní z pohárových zápasů. Po ruční změně "
                            "se predikce hned přepočítá."
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
                            st.selectbox(
                                "Zranění", list(modely.POKUTA_ZRANENI), key=f"zd_{zvolene_kolo}_{i}"
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
                            st.selectbox(
                                "Zranění", list(modely.POKUTA_ZRANENI), key=f"zh_{zvolene_kolo}_{i}"
                            )

                        if "sila_domaci" in p:
                            st.markdown(
                                f"* **{z['domaci']}:** {p['sila_domaci_zaklad']} → **{p['sila_domaci']}** "
                                f"(oslabení {p['dopad_domaci']} %)\n"
                                f"* **{z['hoste']}:** {p['sila_hoste_zaklad']} → **{p['sila_hoste']}** "
                                f"(oslabení {p['dopad_hoste']} %)"
                            )
                        else:
                            st.caption(
                                "Index síly není k dispozici (tým chybí v tabulce), "
                                "predikci nesou Poisson a Elo."
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
                    if p.get("nejcastejsi_skore"):
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
        st.table(
            pd.DataFrame(radky_presnosti).sort_values("Brier").set_index("Model")
        )
        st.caption(
            "Úspěšnost tipu je zavádějící metrika – model, který vždy tipne "
            "favorita, ji má vysokou i bez skutečné hodnoty. Rozhoduj se podle Brier score."
        )


with st.expander("📝 Poslední zapsané predikce"):
    prehled = zaznamy.prehled_zaznamu(pocet=40)
    if prehled.empty:
        st.markdown("Log je zatím prázdný.")
    else:
        st.dataframe(prehled, hide_index=True, width="stretch")
        st.caption(f"Ukládá se do `{os.path.basename(zaznamy.SOUBOR_ZAZNAMU)}`.")
