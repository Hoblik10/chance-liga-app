"""Kurzy 1/X/2 ze screenshotu sázkovky.

Tipsport z cloudu často vrátí 403, takže nejjednodušší záloha je fotka
nabídky. Čte se Tesseractem (na Streamlit Cloudu přes ``packages.txt``)
a text se páruje na zápasy zvoleného kola podle názvů týmů.
"""

import io
import re
import unicodedata

from PIL import Image, ImageFilter, ImageOps

import data
import kurzy
import kurz_zdroje

# Zkratky z ChanceLiga.cz / Tipsportu / Fortuny. Použijí se jen tehdy,
# když ten tým v kole opravdu je – „BRN“ by jinak trefilo dva Brna.
ZKRATKY = {
    "sla": "SK Slavia Praha",
    "spa": "AC Sparta Praha",
    "plz": "FC Viktoria Plzeň",
    "vik": "FC Viktoria Plzeň",
    "ban": "FC Baník Ostrava",
    "ost": "FC Baník Ostrava",
    "lib": "FC Slovan Liberec",
    "sig": "SK Sigma Olomouc",
    "olo": "SK Sigma Olomouc",
    "hkr": "FC Hradec Králové",
    "hrk": "FC Hradec Králové",
    "pce": "FK Pardubice",
    "fkp": "FK Pardubice",
    "zln": "FC Zlín",
    "mbl": "FK Mladá Boleslav",
    "bol": "FK Mladá Boleslav",
    "boh": "Bohemians Praha 1905",
    "tep": "FK Teplice",
    "slo": "1. FC Slovácko",
    "jab": "FK Jablonec",
    "fkj": "FK Jablonec",
    "art": "SK Artis Brno",
    "zbr": "FC Zbrojovka Brno",
}

# Desetinný kurz 1.01–99.99, tečka nebo čárka. Ne datum (29. 8.).
KURZ_RE = re.compile(r"(?<![\d])(\d{1,2})[.,](\d{2})(?!\d)")
ODDELOVAC_TYMU = re.compile(
    r"\s+(?:-|–|—|/|vs\.?|versus)\s+", re.IGNORECASE
)


def _bez_diakritiky(text):
    slozeny = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(znak for znak in slozeny if not unicodedata.combining(znak))


def _norm_text(text):
    """Malá písmena, bez diakritiky, jednotné pomlčky. Nové řádky zůstanou."""
    text = _bez_diakritiky(text).lower()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = text.replace("\u00a0", " ")
    return text


def _tokeny_kurzu(text):
    """Pozice a hodnoty desetinných kurzů v textu."""
    nalezene = []
    for shoda in KURZ_RE.finditer(text):
        hodnota = float(f"{shoda.group(1)}.{shoda.group(2)}")
        if kurzy.platny_kurz(hodnota):
            nalezene.append((shoda.start(), hodnota, shoda.end()))
    return nalezene


def _aliasy_tymu(kanonicky):
    """Řetězce, pod kterými tým na screenshotu bývá."""
    klic = kurz_zdroje._klic_tymu(kanonicky)
    aliasy = {klic}
    for surovy, cil in data.MAPA_TYMU.items():
        if cil == kanonicky:
            klic_suroveho = kurz_zdroje._klic_tymu(surovy)
            if klic_suroveho:
                aliasy.add(klic_suroveho)
            surovy_klic = _norm_text(surovy).strip()
            if surovy_klic and " " not in surovy_klic and len(surovy_klic) >= 4:
                aliasy.add(surovy_klic)
    for zkratka, cil in ZKRATKY.items():
        if cil == kanonicky:
            aliasy.add(zkratka)
    # „Boleslav“, „Ostrava“, „Slavia“ na fotce bez prefixu klubu.
    for token in klic.split():
        if len(token) >= 5:
            aliasy.add(token)
    return [alias for alias in sorted(aliasy, key=len, reverse=True) if alias]


def _tymy_v_textu(text, znami):
    """Výskyty týmů v normalizovaném textu, bez překryvů (delší název vyhraje)."""
    norm = _norm_text(text)
    kandidati = []
    for tym in znami:
        for alias in _aliasy_tymu(tym):
            vzor = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            for shoda in re.finditer(vzor, norm):
                kandidati.append((shoda.start(), shoda.end(), tym, len(alias)))
    kandidati.sort(key=lambda radek: (-radek[3], radek[0]))
    obsazeno = []
    vysledek = []
    for start, konec, tym, delka in kandidati:
        if any(not (konec <= a or start >= b) for a, b in obsazeno):
            continue
        obsazeno.append((start, konec))
        vysledek.append((start, konec, tym))
    vysledek.sort(key=lambda radek: radek[0])
    return vysledek


def _trojice_v_okne(tokeny, start, konec):
    """První těsná trojice kurzů v okně, typicky řádek 1/X/2."""
    v_okne = [t for t in tokeny if start <= t[0] < konec]
    for index in range(len(v_okne) - 2):
        prvni, _druhy, treti = v_okne[index], v_okne[index + 1], v_okne[index + 2]
        if treti[0] - prvni[2] <= 80:
            return (prvni[1], v_okne[index + 1][1], treti[1])
    if len(v_okne) >= 3:
        return (v_okne[0][1], v_okne[1][1], v_okne[2][1])
    return None


def _polozka(domaci, hoste, trojice):
    return {
        "domaci_surove": domaci,
        "hoste_surove": hoste,
        "kurzy": tuple(float(x) for x in trojice),
        "zdroj": "screenshot",
        "sazkovka": "fotka",
    }


def _tym_z_kusu(surovy, znami):
    nalezeny = kurz_zdroje.kanonicky_tym(surovy, znami)
    if nalezeny:
        return nalezeny
    klic = kurz_zdroje._klic_tymu(surovy)
    cil = ZKRATKY.get(klic)
    if cil in znami:
        return cil
    return None


def _z_radku_s_pomlckou(text, znami):
    """Řádky ve tvaru 'Slavia - Sparta 1.55 4.20 5.80'."""
    nabidka = []
    for radek in text.splitlines():
        tokeny = _tokeny_kurzu(radek)
        if len(tokeny) < 3:
            continue
        bez = KURZ_RE.sub(" ", radek)
        casti = ODDELOVAC_TYMU.split(bez, maxsplit=1)
        if len(casti) != 2:
            continue
        domaci = _tym_z_kusu(casti[0], znami)
        hoste = _tym_z_kusu(casti[1], znami)
        if domaci and hoste and domaci != hoste:
            nabidka.append(_polozka(domaci, hoste, (tokeny[0][1], tokeny[1][1], tokeny[2][1])))
    return nabidka


def _z_bloku_dvou_tymu(text, znami):
    """Dva řádky s týmy a hned pod nimi 1/X/2 – typický mobilní Tipsport."""
    radky = [radek.strip() for radek in text.splitlines() if radek.strip()]
    nabidka = []
    index = 0
    while index < len(radky) - 1:
        domaci = _tym_z_kusu(radky[index], znami)
        hoste = _tym_z_kusu(radky[index + 1], znami)
        if domaci and hoste and domaci != hoste:
            okno = " ".join(radky[index + 2 : index + 5])
            tokeny = _tokeny_kurzu(okno)
            if len(tokeny) >= 3:
                nabidka.append(
                    _polozka(domaci, hoste, (tokeny[0][1], tokeny[1][1], tokeny[2][1]))
                )
                index += 3
                continue
        index += 1
    return nabidka


def _z_pozic_zapasu(text, zapasy):
    """Pro každý zápas kola najde oba týmy a nejbližší trojici kurzů."""
    znami = sorted({z["domaci"] for z in zapasy} | {z["hoste"] for z in zapasy})
    vyskyty = _tymy_v_textu(text, znami)
    tokeny = _tokeny_kurzu(text)
    nabidka = []
    for zapas in zapasy:
        pozice_d = [v for v in vyskyty if v[2] == zapas["domaci"]]
        pozice_h = [v for v in vyskyty if v[2] == zapas["hoste"]]
        if not pozice_d or not pozice_h:
            continue
        start = min(pozice_d[0][0], pozice_h[0][0])
        konec_paru = max(pozice_d[0][1], pozice_h[0][1])
        dalsi = [
            v[0]
            for v in vyskyty
            if v[0] > konec_paru and v[2] not in (zapas["domaci"], zapas["hoste"])
        ]
        konec = min(dalsi) if dalsi else len(text)
        trojice = _trojice_v_okne(tokeny, start, konec)
        if trojice:
            nabidka.append(_polozka(zapas["domaci"], zapas["hoste"], trojice))
    return nabidka


def _sluc_nabidku(*seznamy):
    """První výskyt zápasu vyhraje – přesnější formáty jdou dřív."""
    sloucene = []
    videne = set()
    for seznam in seznamy:
        for polozka in seznam:
            klic = (polozka["domaci_surove"], polozka["hoste_surove"])
            if klic in videne:
                continue
            videne.add(klic)
            sloucene.append(polozka)
    return sloucene


def parsuj_nabidku_z_textu(text, zapasy):
    """Z OCR textu vytáhne nabídku ve stejném tvaru jako Tipsport JSON."""
    if not text or not zapasy:
        return []
    znami = sorted({z["domaci"] for z in zapasy} | {z["hoste"] for z in zapasy})
    return _sluc_nabidku(
        _z_radku_s_pomlckou(text, znami),
        _z_bloku_dvou_tymu(text, znami),
        _z_pozic_zapasu(text, zapasy),
    )


def _priprav_obrazek(obrazek):
    """Šedá, kontrast, zvětšení drobných screenshotů – Tesseract čte líp."""
    sedivy = ImageOps.grayscale(obrazek.convert("RGB"))
    sedivy = ImageOps.autocontrast(sedivy)
    sirka, vyska = sedivy.size
    if sirka < 1200:
        nasobek = max(2, int(1200 / max(sirka, 1)))
        sedivy = sedivy.resize(
            (sirka * nasobek, vyska * nasobek), Image.Resampling.LANCZOS
        )
    return sedivy.filter(ImageFilter.SHARPEN)


def _jazyky_tesseractu():
    try:
        import pytesseract

        dostupne = (pytesseract.get_languages(config="") or []) if hasattr(
            pytesseract, "get_languages"
        ) else []
    except Exception:
        return ("eng",)
    if "ces" in dostupne:
        return ("ces+eng", "eng")
    return ("eng",)


def ocr_obrazek(obrazek, ocr=None):
    """Přečte text z PIL obrázku. ``ocr`` je volitelná náhrada v testech."""
    if ocr is not None:
        return ocr(obrazek) or ""
    try:
        import pytesseract
    except ImportError as chyba:
        raise RuntimeError(
            "Chybí pytesseract. Do requirements.txt patří pytesseract "
            "a na Streamlit Cloudu do packages.txt tesseract-ocr."
        ) from chyba

    pripraveny = _priprav_obrazek(obrazek)
    texty = []
    try:
        for jazyk in _jazyky_tesseractu():
            for psm in ("6", "4", "11"):
                try:
                    texty.append(
                        pytesseract.image_to_string(
                            pripraveny,
                            lang=jazyk,
                            config=f"--psm {psm}",
                        )
                    )
                except pytesseract.TesseractError:
                    continue
    except pytesseract.TesseractNotFoundError as chyba:
        raise RuntimeError(
            "Tesseract v systému není. Na Streamlit Cloudu přidej "
            "do packages.txt řádek tesseract-ocr (a tesseract-ocr-ces)."
        ) from chyba

    if not texty:
        return ""
    return max(texty, key=lambda text: len(_tokeny_kurzu(text)))


def _nacti_pil(soubor):
    if hasattr(soubor, "getvalue"):
        data = soubor.getvalue()
    elif isinstance(soubor, (bytes, bytearray)):
        data = bytes(soubor)
    else:
        data = soubor.read()
    obrazek = Image.open(io.BytesIO(data))
    obrazek.load()
    return obrazek


def nacti_nabidku_z_obrazku(soubory, zapasy, ocr=None):
    """OCR všech fotek. Vrací (nabídka, spojený text, chyby)."""
    texty = []
    chyby = []
    for soubor in soubory or []:
        try:
            obrazek = _nacti_pil(soubor)
            texty.append(ocr_obrazek(obrazek, ocr=ocr))
        except Exception as chyba:
            jmeno = getattr(soubor, "name", "fotka")
            chyby.append(f"{jmeno}: {chyba}")
    text = "\n".join(cast for cast in texty if cast)
    nabidka = parsuj_nabidku_z_textu(text, zapasy)
    if not nabidka and text and not chyby:
        chyby.append(
            "Na fotce jsou nějaká čísla, ale nepovedlo se je přiřadit k zápasům "
            "tohoto kola. Zkus screenshot celé nabídky, kde jdou vidět názvy týmů "
            "i trojice 1/X/2."
        )
    if not text and not chyby:
        chyby.append("Z fotky se nepodařilo přečíst žádný text.")
    return nabidka, text, chyby


def nacti_a_uloz(kolo, zapasy, soubory, cesta=None, cas=None, ocr=None):
    """Přečte fotky, spáruje se zápasy kola a uloží. Stejné shrnutí jako trh."""
    nabidka, text, chyby = nacti_nabidku_z_obrazku(soubory, zapasy, ocr=ocr)
    parovane = kurz_zdroje.sparuj_nabidku(nabidka, zapasy) if nabidka else {}
    ulozeno = kurz_zdroje.uloz_sparovane(
        kolo, zapasy, parovane, cas=cas, cesta=cesta
    )
    return {
        "ulozeno": ulozeno,
        "nabidnuto": len(nabidka),
        "nesparovano": max(len(nabidka) - ulozeno, 0),
        "zdroj": "screenshot",
        "sazkovka": "fotka",
        "chyby": chyby,
        "text": text,
    }


def popis_vysledku(shrnuti):
    """Hláška do Streamlitu."""
    if shrnuti["ulozeno"]:
        text = f"Z fotky uloženo {shrnuti['ulozeno']} zápasů."
        if shrnuti["nesparovano"]:
            text += f" {shrnuti['nesparovano']} dalších se k tomuto kolu nepodařilo přiřadit."
        return text
    if shrnuti["chyby"]:
        return " ".join(shrnuti["chyby"])
    return "Na fotce se kurzy 1/X/2 nepodařilo přečíst."
