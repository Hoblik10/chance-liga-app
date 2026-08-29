"""Záloha kurzů mimo disk Streamlit Cloudu.

``kurzy.csv`` na cloudu zmizí při spánku, rebootu i novém deployi – disk
je jen klon gitu. Enter v poli kurzů navíc sám o sobě nic nezapisuje.

Tři vrstvy, od nejrychlejší:

1. CSV na disku – platí, dokud kontejner žije (místní běh i aktuální
   session na cloudu).
2. Prohlížeč – parametr ``kjson`` v URL a ``localStorage``. Přežije
   obnovení stránky; po restartu cloudu zbude, když se otevře stejný
   prohlížeč.
3. GitHub, větev ``data`` – přežije všechno, i jiný telefon. Zápis chce
   ``GITHUB_TOKEN`` v secrets. Veřejné čtení jde i bez tokenu, jakmile
   větev existuje.

Do ``main`` se kurzy nesahejí: Streamlit Cloud by se po každém uložení
znovu deployoval.
"""

import base64
import json
import sys
from io import StringIO

import pandas as pd
import requests

import kurzy
import nastaveni

VETEV_ZALOHY = "data"
SOUBOR_ZALOHY = "kurzy.csv"
PARAMETR_URL = "kjson"
KLIC_LOCAL_STORAGE = "chance_liga_kurzy_v1"
MAX_ZALOHA_ZAPASU = 40
MAX_DELKA_URL = 1800
GITHUB_API = "https://api.github.com"


def github_repo():
    """Repozitář ve tvaru vlastník/název."""
    return (
        nastaveni.nacti_secret("GITHUB_REPO", "Hoblik10/chance-liga-app")
        or "Hoblik10/chance-liga-app"
    )


def github_token():
    """PAT nebo fine-grained token s právem Contents: Read and write."""
    return nastaveni.nacti_secret("GITHUB_TOKEN") or nastaveni.nacti_secret(
        "GITHUB_PAT"
    )


def github_nastaven():
    """Šlo by zapisovat na GitHub?"""
    return bool(github_token())


def zakoduj_zalohu(df, limit=MAX_ZALOHA_ZAPASU):
    """Tabulka → krátký text pro URL / localStorage."""
    zaznamy = kurzy.tabulka_na_zaznamy(df)
    if not zaznamy:
        return ""
    zaznamy.sort(key=lambda radek: radek.get("zapsano") or "", reverse=True)
    surove = json.dumps(
        zaznamy[:limit], ensure_ascii=False, separators=(",", ":")
    )
    return (
        base64.urlsafe_b64encode(surove.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def dekoduj_zalohu(text):
    """Text z URL / localStorage → tabulka, nebo None při nesmyslu."""
    if not text or not str(text).strip():
        return None
    surove = str(text).strip()
    try:
        doplnek = "=" * ((4 - len(surove) % 4) % 4)
        nacteno = json.loads(
            base64.urlsafe_b64decode(surove + doplnek).decode("utf-8")
        )
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(nacteno, list):
        return None
    return kurzy.tabulka_z_zaznamu(nacteno)


def html_prohlizece(payload):
    """Skript, který drží zálohu v localStorage a při prázdném disku ji vrátí.

    Běží v iframe komponenty Streamlitu, proto sahá na ``parent`` / ``top``.
    Když je ``payload`` prázdný a v prohlížeči něco je, doplní ``kjson``
    do URL a obnoví stránku – Python to v dalším běhu sloučí do CSV.
    """
    telo = json.dumps(payload or "")
    klic = json.dumps(KLIC_LOCAL_STORAGE)
    parametr = json.dumps(PARAMETR_URL)
    return f"""<!DOCTYPE html>
<html><body><script>
(function() {{
  const KEY = {klic};
  const PARAM = {parametr};
  const incoming = {telo};
  function candidates() {{
    return [window.parent, window.top, window];
  }}
  function pickWindow() {{
    for (const w of candidates()) {{
      try {{
        void w.localStorage;
        void w.location.href;
        return w;
      }} catch (e) {{}}
    }}
    return null;
  }}
  const w = pickWindow();
  if (!w) return;
  try {{
    if (incoming) {{
      w.localStorage.setItem(KEY, incoming);
      return;
    }}
    const stored = w.localStorage.getItem(KEY);
    if (!stored) return;
    const url = new URL(w.location.href);
    if (url.searchParams.get(PARAM)) return;
    url.searchParams.set(PARAM, stored);
    w.location.replace(url.toString());
  }} catch (e) {{}}
}})();
</script></body></html>
"""


def _hlavicky(token=None):
    hlavicky = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "chance-liga-app",
    }
    if token:
        hlavicky["Authorization"] = f"Bearer {token}"
    return hlavicky


def nacti_z_githubu(get=None):
    """Kurzy z větve data, nebo None když větev/soubor není."""
    get = get or requests.get
    repo = github_repo()
    url = f"{GITHUB_API}/repos/{repo}/contents/{SOUBOR_ZALOHY}"
    try:
        odpoved = get(
            url,
            headers=_hlavicky(github_token()),
            params={"ref": VETEV_ZALOHY},
            timeout=10,
        )
    except requests.RequestException:
        return None
    if getattr(odpoved, "status_code", 0) != 200:
        return None
    try:
        telo = odpoved.json()
        obsah = telo.get("content") or ""
        csv_text = base64.b64decode(obsah.replace("\n", "")).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError, AttributeError):
        return None
    try:
        df = pd.read_csv(StringIO(csv_text))
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError):
        return None
    for sloupec in kurzy.SLOUPCE:
        if sloupec not in df.columns:
            df[sloupec] = None
    return df[kurzy.SLOUPCE]


def _sha_vetve(repo, vetev, get, token):
    url = f"{GITHUB_API}/repos/{repo}/git/ref/heads/{vetev}"
    try:
        odpoved = get(url, headers=_hlavicky(token), timeout=10)
    except requests.RequestException:
        return None
    if getattr(odpoved, "status_code", 0) != 200:
        return None
    try:
        return odpoved.json()["object"]["sha"]
    except (ValueError, KeyError, TypeError):
        return None


def _vytvor_vetve(repo, token, get=None, post=None):
    """Založí větev data z defaultní větve, když ještě není."""
    get = get or requests.get
    post = post or requests.post
    if _sha_vetve(repo, VETEV_ZALOHY, get, token):
        return True
    for kandidat in ("main", "master"):
        sha = _sha_vetve(repo, kandidat, get, token)
        if sha:
            break
    else:
        return False
    try:
        odpoved = post(
            f"{GITHUB_API}/repos/{repo}/git/refs",
            headers=_hlavicky(token),
            json={"ref": f"refs/heads/{VETEV_ZALOHY}", "sha": sha},
            timeout=10,
        )
    except requests.RequestException:
        return False
    return getattr(odpoved, "status_code", 0) in (201, 422)


def uloz_na_github(df, get=None, put=None, post=None):
    """Zapíše celé CSV na větev data. Vrací (ok, zpráva)."""
    token = github_token()
    if not token:
        return False, "chybí GITHUB_TOKEN"
    get = get or requests.get
    put = put or requests.put
    post = post or requests.post
    repo = github_repo()
    if not _vytvor_vetve(repo, token, get=get, post=post):
        return False, "nejde založit větev data"

    tabulka = kurzy.sluc_tabulky(df)
    csv_text = tabulka.to_csv(index=False, encoding="utf-8")
    obsah = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    url = f"{GITHUB_API}/repos/{repo}/contents/{SOUBOR_ZALOHY}"
    sha = None
    try:
        existujici = get(
            url,
            headers=_hlavicky(token),
            params={"ref": VETEV_ZALOHY},
            timeout=10,
        )
        if getattr(existujici, "status_code", 0) == 200:
            sha = existujici.json().get("sha")
    except (requests.RequestException, ValueError, AttributeError):
        sha = None

    telo = {
        "message": "Aktualizace kurzů",
        "content": obsah,
        "branch": VETEV_ZALOHY,
    }
    if sha:
        telo["sha"] = sha
    try:
        odpoved = put(url, headers=_hlavicky(token), json=telo, timeout=15)
    except requests.RequestException as chyba:
        return False, str(chyba)
    if getattr(odpoved, "status_code", 0) in (200, 201):
        return True, "ok"
    return False, f"GitHub {getattr(odpoved, 'status_code', '?')}"


def popis_zalohy():
    """Krátká nápověda do UI podle toho, co je nastavené."""
    if github_nastaven():
        return (
            "Kurzy se ukládají samy po vyplnění 1/X/2. Záloha je v tomhle "
            "prohlížeči a na GitHubu (větev data), takže přežijí obnovení "
            "i restart cloudu."
        )
    return (
        "Kurzy se ukládají samy po vyplnění 1/X/2 – Enter stačí, tlačítko "
        "není potřeba. Na Cloudu disk po restartu zmizí, proto se záloha "
        "drží v tomhle prohlížeči. Z jiného zařízení nebo po vymazání dat "
        "webu zbývá doplnit GITHUB_TOKEN do Secrets (větev data, ne main)."
    )


def sync_prohlizec(st_modul, components_modul, df):
    """Zapíše zálohu do URL a do localStorage. Nic neposílá na GitHub."""
    kod = zakoduj_zalohu(df)
    try:
        components_modul.html(html_prohlizece(kod), height=0)
    except Exception:
        print("localStorage komponenta selhala", file=sys.stderr)
    if not kod or len(kod) > MAX_DELKA_URL:
        return kod
    try:
        if st_modul.query_params.get(PARAMETR_URL) != kod:
            st_modul.query_params[PARAMETR_URL] = kod
    except Exception:
        pass
    return kod
