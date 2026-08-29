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


def _je_vykop_nebo_datum(cele, desetiny):
    """Výkop 19.00 nebo datum 29.08 z fotky není kurz 1/X/2."""
    if cele in (16, 17, 18, 19, 20, 21) and desetiny in (0, 30):
        return True
    if cele >= 10 and 1 <= desetiny <= 12:
        return True
    return False


def _tokeny_kurzu(text):
    """Pozice a hodnoty desetinných kurzů v textu."""
    nalezene = []
    for shoda in KURZ_RE.finditer(text):
        cele = int(shoda.group(1))
        desetiny = int(shoda.group(2))
        if _je_vykop_nebo_datum(cele, desetiny):
            continue
        hodnota = float(f"{cele}.{desetiny:02d}")
        if kurzy.platny_kurz(hodnota):
            nalezene.append((shoda.start(), hodnota, shoda.end()))
    return nalezene


def _platna_trojice_trhu(trojice):
    """1/X/2 ze sázkovky má marži; náhodná trojice čísel skoro nikdy."""
    try:
        k1, kx, k2 = (float(x) for x in trojice)
    except (TypeError, ValueError):
        return False
    if not all(kurzy.platny_kurz(hodnota) for hodnota in (k1, kx, k2)):
        return False
    soucet = (1.0 / k1) + (1.0 / kx) + (1.0 / k2)
    return 1.01 <= soucet <= 1.28


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


def _tymy_na_radku(radek, znami):
    """Týmy na jednom řádku, zleva doprava."""
    return [vyskyt[2] for vyskyt in _tymy_v_textu(radek, znami)]


def _klasifikuj_radek(radek, znami):
    return {
        "tymy": _tymy_na_radku(radek, znami),
        "kurzy": [token[1] for token in _tokeny_kurzu(radek)],
        "text": radek,
    }


def _trojice_v_okne(tokeny, start, konec):
    """Nejtěsnější trojice kurzů v okně, která vypadá jako 1/X/2."""
    v_okne = [t for t in tokeny if start <= t[0] < konec]
    kandidati = []
    for index in range(len(v_okne) - 2):
        trojice = (v_okne[index][1], v_okne[index + 1][1], v_okne[index + 2][1])
        if not _platna_trojice_trhu(trojice):
            continue
        rozpeti = v_okne[index + 2][0] - v_okne[index][2]
        kandidati.append((rozpeti, index, trojice))
    if not kandidati:
        return None
    kandidati.sort()
    return kandidati[0][2]


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


def _z_tabulky_sloupcu(text, znami):
    """Dva zápasy vedle sebe: nahoře týmy, pod nimi sloupce 1 / X / 2."""
    radky = [_klasifikuj_radek(radek.strip(), znami) for radek in text.splitlines() if radek.strip()]
    nabidka = []
    index = 0
    while index < len(radky) - 4:
        horni, dolni = radky[index], radky[index + 1]
        pocet = len(horni["tymy"])
        if (
            pocet >= 2
            and len(dolni["tymy"]) == pocet
            and not horni["kurzy"]
            and not dolni["kurzy"]
        ):
            dalsi = radky[index + 2 : index + 5]
            if len(dalsi) == 3 and all(
                len(radek["kurzy"]) == pocet and not radek["tymy"] for radek in dalsi
            ):
                for sloupec in range(pocet):
                    domaci = horni["tymy"][sloupec]
                    hoste = dolni["tymy"][sloupec]
                    trojice = (
                        dalsi[0]["kurzy"][sloupec],
                        dalsi[1]["kurzy"][sloupec],
                        dalsi[2]["kurzy"][sloupec],
                    )
                    if domaci != hoste and _platna_trojice_trhu(trojice):
                        nabidka.append(_polozka(domaci, hoste, trojice))
                index += 5
                continue
        index += 1
    return nabidka


def _z_svisleho_1x2(text, znami):
    """Domácí nahoře se svým kurzem, pod ním remíza, pod ním hosté."""
    radky = [radek.strip() for radek in text.splitlines() if radek.strip()]
    nabidka = []
    index = 0
    while index < len(radky) - 2:
        k0 = [t[1] for t in _tokeny_kurzu(radky[index])]
        k1 = [t[1] for t in _tokeny_kurzu(radky[index + 1])]
        k2 = [t[1] for t in _tokeny_kurzu(radky[index + 2])]
        if len(k0) == 1 and len(k1) == 1 and len(k2) == 1:
            t0 = _tymy_na_radku(KURZ_RE.sub(" ", radky[index]), znami)
            t1 = _tymy_na_radku(KURZ_RE.sub(" ", radky[index + 1]), znami)
            t2 = _tymy_na_radku(KURZ_RE.sub(" ", radky[index + 2]), znami)
            if t0 and t2 and t0[0] != t2[0] and not t1:
                trojice = (k0[0], k1[0], k2[0])
                if _platna_trojice_trhu(trojice):
                    nabidka.append(_polozka(t0[0], t2[0], trojice))
                    index += 3
                    continue
        index += 1
    return nabidka


def _z_paru_a_svislych_kurzu(text, znami):
    """Nejdřív dvojice týmů, pod nimi tři kurzy (nahoře 1, uprostřed X, dole 2).

    Tesseract občas přečte nejdřív všechny názvy a teprve potom sloupec
    kurzů – pak se páruje pořadím: první dva týmy k prvním třem kurzům.
    """
    nabidka = []
    tymy_fronta = []
    kurzy_fronta = []

    def vyplach():
        while len(tymy_fronta) >= 2 and len(kurzy_fronta) >= 3:
            domaci = tymy_fronta.pop(0)
            hoste = tymy_fronta.pop(0)
            trojice = (
                kurzy_fronta.pop(0),
                kurzy_fronta.pop(0),
                kurzy_fronta.pop(0),
            )
            if domaci != hoste and _platna_trojice_trhu(trojice):
                nabidka.append(_polozka(domaci, hoste, trojice))
        kurzy_fronta.clear()

    for radek in text.splitlines():
        radek = radek.strip()
        if not radek:
            continue
        tymy = _tymy_na_radku(radek, znami)
        hod = [token[1] for token in _tokeny_kurzu(radek)]
        if tymy and not hod:
            if kurzy_fronta:
                vyplach()
            for tym in tymy:
                if not tymy_fronta or tymy_fronta[-1] != tym:
                    tymy_fronta.append(tym)
        elif hod and not tymy:
            kurzy_fronta.extend(hod)
        else:
            if kurzy_fronta or (tymy and hod):
                vyplach()

    vyplach()
    return nabidka


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
        if (
            domaci
            and hoste
            and domaci != hoste
            and _platna_trojice_trhu((tokeny[0][1], tokeny[1][1], tokeny[2][1]))
        ):
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
            okno = " ".join(radky[index : index + 6])
            tokeny = _tokeny_kurzu(okno)
            if len(tokeny) >= 3:
                trojice = (tokeny[0][1], tokeny[1][1], tokeny[2][1])
                if _platna_trojice_trhu(trojice):
                    nabidka.append(_polozka(domaci, hoste, trojice))
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
        _z_tabulky_sloupcu(text, znami),
        _z_svisleho_1x2(text, znami),
        _z_paru_a_svislych_kurzu(text, znami),
        _z_radku_s_pomlckou(text, znami),
        _z_bloku_dvou_tymu(text, znami),
        _z_pozic_zapasu(text, zapasy),
    )


def _slova_z_dict(data):
    """Tesseract image_to_data → slova s pozicí."""
    slova = []
    pocet = len(data.get("text") or [])
    for index in range(pocet):
        text = str(data["text"][index] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][index])
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0:
            continue
        try:
            slova.append(
                {
                    "text": text,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                    "width": int(data["width"][index]),
                    "height": int(data["height"][index]),
                }
            )
        except (TypeError, ValueError):
            continue
    return slova


def _radky_z_boxu(slova):
    """Slova seskupí do řádků shora dolů, v řádku zleva doprava."""
    if not slova:
        return []
    serazena = sorted(slova, key=lambda slovo: (slovo["top"], slovo["left"]))
    radky = []
    aktualni = [serazena[0]]
    for slovo in serazena[1:]:
        ref = aktualni[-1]
        tolerance = max(12, int(ref["height"] * 0.7))
        if abs(slovo["top"] - ref["top"]) <= tolerance:
            aktualni.append(slovo)
        else:
            radky.append(sorted(aktualni, key=lambda s: s["left"]))
            aktualni = [slovo]
    radky.append(sorted(aktualni, key=lambda s: s["left"]))
    return radky


def _text_z_radku(radky_slov):
    return "\n".join(" ".join(slovo["text"] for slovo in radek) for radek in radky_slov)


def _kurz_z_textu(text):
    tokeny = _tokeny_kurzu(text)
    return tokeny[0][1] if len(tokeny) == 1 else None


def _z_svislych_boxu(slova, zapasy):
    """Tři kurzy pod sebou se stejným X = 1, X, 2. Týmy nalevo / nad nimi."""
    znami = sorted({z["domaci"] for z in zapasy} | {z["hoste"] for z in zapasy})
    kurzy_boxy = []
    tymy_boxy = []
    for slovo in slova:
        hodnota = _kurz_z_textu(slovo["text"])
        if hodnota is not None:
            kurzy_boxy.append({**slovo, "hodnota": hodnota})
            continue
        tymy = _tymy_na_radku(slovo["text"], znami)
        if len(tymy) == 1:
            tymy_boxy.append({**slovo, "tym": tymy[0]})

    # Delší názvy („Slavia Praha“) vzniknou spojením slov na řádku.
    for radek in _radky_z_boxu(slova):
        text = " ".join(slovo["text"] for slovo in radek)
        tymy = _tymy_na_radku(KURZ_RE.sub(" ", text), znami)
        if not tymy:
            continue
        levy = min(slovo["left"] for slovo in radek)
        horni = min(slovo["top"] for slovo in radek)
        for tym in tymy:
            tymy_boxy.append(
                {
                    "text": tym,
                    "left": levy,
                    "top": horni,
                    "width": 40,
                    "height": radek[0]["height"],
                    "tym": tym,
                }
            )

    pouzite = set()
    nabidka = []
    podle_y = sorted(kurzy_boxy, key=lambda k: (k["top"], k["left"]))
    for i, prvni in enumerate(podle_y):
        if id(prvni) in pouzite:
            continue
        sloupce = [
            dalsi
            for dalsi in podle_y[i + 1 :]
            if id(dalsi) not in pouzite
            and dalsi["top"] > prvni["top"]
            and abs(dalsi["left"] - prvni["left"]) <= max(36, prvni["width"])
        ]
        sloupce.sort(key=lambda k: k["top"])
        if len(sloupce) < 2:
            continue
        druhy, treti = sloupce[0], sloupce[1]
        mezera = max(prvni["height"], 14) * 4
        if druhy["top"] - prvni["top"] > mezera or treti["top"] - druhy["top"] > mezera:
            continue
        trojice = (prvni["hodnota"], druhy["hodnota"], treti["hodnota"])
        if not _platna_trojice_trhu(trojice):
            continue
        y0 = prvni["top"] - 3 * max(prvni["height"], 16)
        y1 = treti["top"] + treti["height"] + max(treti["height"], 16)
        x_kurz = prvni["left"]
        kandidati = [
            tym
            for tym in tymy_boxy
            if y0 <= tym["top"] <= y1 and tym["left"] < x_kurz - 8
        ]
        kandidati.sort(key=lambda tym: tym["top"])
        videne = []
        for tym in kandidati:
            if tym["tym"] not in videne:
                videne.append(tym["tym"])
        if len(videne) < 2:
            continue
        domaci, hoste = videne[0], videne[1]
        if domaci == hoste:
            continue
        nabidka.append(_polozka(domaci, hoste, trojice))
        pouzite.update((id(prvni), id(druhy), id(treti)))
    return nabidka


def parsuj_nabidku_z_boxu(slova, zapasy):
    """Kurzy podle souřadnic: nahoře domácí, pod ním remíza, dole hosté."""
    if not slova or not zapasy:
        return []
    radky = _radky_z_boxu(slova)
    text = _text_z_radku(radky)
    return _sluc_nabidku(
        _z_svislych_boxu(slova, zapasy),
        parsuj_nabidku_z_textu(text, zapasy),
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


def _nejlepsi_ocr(obrazek, zapasy):
    """Vyzkouší PSM a vybere čtení, ze kterého jde složit nejvíc zápasů."""
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as chyba:
        raise RuntimeError(
            "Chybí pytesseract. Do requirements.txt patří pytesseract "
            "a na Streamlit Cloudu do packages.txt tesseract-ocr."
        ) from chyba

    pripraveny = _priprav_obrazek(obrazek)
    kandidati = []
    try:
        for jazyk in _jazyky_tesseractu():
            for psm in ("6", "4", "11"):
                try:
                    data = pytesseract.image_to_data(
                        pripraveny,
                        lang=jazyk,
                        config=f"--psm {psm}",
                        output_type=Output.DICT,
                    )
                except pytesseract.TesseractError:
                    continue
                slova = _slova_z_dict(data)
                text = _text_z_radku(_radky_z_boxu(slova))
                if not text.strip():
                    try:
                        text = pytesseract.image_to_string(
                            pripraveny, lang=jazyk, config=f"--psm {psm}"
                        ) or ""
                    except pytesseract.TesseractError:
                        text = ""
                nabidka = _sluc_nabidku(
                    parsuj_nabidku_z_boxu(slova, zapasy),
                    parsuj_nabidku_z_textu(text, zapasy),
                )
                kandidati.append((len(nabidka), len(_tokeny_kurzu(text)), text, slova))
    except pytesseract.TesseractNotFoundError as chyba:
        raise RuntimeError(
            "Tesseract v systému není. Na Streamlit Cloudu přidej "
            "do packages.txt řádek tesseract-ocr (a tesseract-ocr-ces)."
        ) from chyba

    if not kandidati:
        return "", []
    kandidati.sort(reverse=True)
    return kandidati[0][2], kandidati[0][3]


def ocr_obrazek(obrazek, ocr=None, zapasy=None):
    """Přečte text z PIL obrázku. ``ocr`` je volitelná náhrada v testech."""
    if ocr is not None:
        return ocr(obrazek) or ""
    text, _slova = _nejlepsi_ocr(obrazek, zapasy or [])
    return text


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
    slova_vse = []
    chyby = []
    for soubor in soubory or []:
        try:
            obrazek = _nacti_pil(soubor)
            if ocr is not None:
                texty.append(ocr(obrazek) or "")
            else:
                text, slova = _nejlepsi_ocr(obrazek, zapasy)
                texty.append(text)
                slova_vse.extend(slova)
        except Exception as chyba:
            jmeno = getattr(soubor, "name", "fotka")
            chyby.append(f"{jmeno}: {chyba}")
    text = "\n".join(cast for cast in texty if cast)
    nabidka = _sluc_nabidku(
        parsuj_nabidku_z_boxu(slova_vse, zapasy),
        parsuj_nabidku_z_textu(text, zapasy),
    )
    if not nabidka and text and not chyby:
        chyby.append(
            "Na fotce jsou nějaká čísla, ale nepovedlo se je přiřadit k zápasům "
            "tohoto kola. Zkus screenshot celé nabídky, kde jdou vidět názvy týmů "
            "i tři kurzy pod sebou (1 nahoře, remíza, 2 dole)."
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
