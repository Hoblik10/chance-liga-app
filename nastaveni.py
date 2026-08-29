"""Konfigurace a odesílání na Telegram.

Modul funguje ve Streamlitu i mimo něj, aby stejný kód šel spustit
z aplikace i z naplánované úlohy na GitHubu:

* v aplikaci se hodnoty berou ze souboru ``.streamlit/secrets.toml``
* v naplánované úloze z proměnných prostředí (GitHub Secrets)
"""

import os
import sys

import requests


def nacti_secret(klic, vychozi=""):
    """Hodnota ze Streamlit secrets, jinak z proměnných prostředí."""
    if "streamlit" in sys.modules:
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            import streamlit as st

            if get_script_run_ctx() is not None:
                return st.secrets[klic]
        except Exception:
            pass

    return os.environ.get(klic, vychozi)


TELEGRAM_TOKEN = nacti_secret("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = nacti_secret("TELEGRAM_CHAT_ID")

# TheSportsDB – zdroj rozpisu, výsledků, tabulky a pohárových zápasů.
# Klíč "123" je veřejný testovací a funguje bez registrace, jen má nižší limity.
SPORTSDB_KEY = nacti_secret("SPORTSDB_KEY", "123") or "123"
SPORTSDB_LIGA = "4631"  # Czech First League

# API-Football (api-sports.io) – volitelné kurzy 1/X/2 na Chance Ligu (id 134).
# Zdarma 100 dotazů denně. Tipsport v té sadě sázkovek není.
API_FOOTBALL_KEY = nacti_secret("API_FOOTBALL_KEY")

# Záloha kurzů mimo disk Streamlit Cloudu. Čte se až v uloziste.py,
# ať token nemusí existovat při importu. Větev data, ne main.

# TheSportsDB značí sezónu rozsahem let.
SEZONA_SPORTSDB = nacti_secret("SEZONA", "2026-2027") or "2026-2027"

# Starší ročníky stažené ve složce sezony/. Na začátku sezóny stojí Poisson
# a Elo skoro jen na nich, protože z pár odehraných kol se nic spolehlivého
# odvodit nedá.
ARCHIVNI_SEZONY = (
    "2021-2022",
    "2022-2023",
    "2023-2024",
    "2024-2025",
    "2025-2026",
)

# Kolik nadcházejících kol se má zobrazit.
POCET_ZOBRAZENYCH_KOL = 2

# Pod tímto počtem řádků je tabulka z API neúplná a sáhne se po webu.
MIN_TYMU_V_TABULCE = 12

HTTP_HLAVICKY = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def telegram_nastaven():
    """Je vůbec kam posílat?"""
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def poslat_na_telegram(zprava):
    """Odešle zprávu; vrací True při úspěchu."""
    if not telegram_nastaven():
        return False

    try:
        odpoved = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": zprava},
            timeout=10,
        )
        if odpoved.status_code != 200:
            print(
                f"Telegram odmítl zprávu ({odpoved.status_code}): {odpoved.text}",
                file=sys.stderr,
            )
            return False
        return True
    except requests.RequestException:
        return False
