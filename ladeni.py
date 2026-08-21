"""Hledání parametrů modelů na archivu minulých sezón.

Čísla jako rho = -0.12 nebo domácí výhoda 1.08 se do kódu dostala odhadem
z cizích lig. Tenhle skript projde archiv a najde hodnoty, se kterými by
modely na české lize předpovídaly nejlíp.

Aby se nevybraly hodnoty vyladěné na šum, hledá se jen na starších sezónách
a poslední ročník slouží jako kontrola. Když se výsledek na kontrolní sezóně
zhorší, parametr se nevyplatí měnit.

Výsledek posledního běhu: doladění tvaru předpovědi (rho, domácí výhoda,
remízy) sice srazilo log loss na učících sezónách z 0.9599 na 0.9553, ale na
kontrolní sezóně bylo o chlup horší (1.0082 proti 1.0078). Jinými slovy se
model naučil šum, a proto v kódu zůstaly původní hodnoty. Přeneslo se jen
vážení modelů – to je v ``modely.VYCHOZI_VAHY``.

    python ladeni.py
"""

import math

import backtest
import modely

# Parametry, které mění výpočet sil týmů – po každé změně se musí přehrát
# celá historie, takže je jich málo a mřížka je hrubá.
MRIZKA_STAVU = {
    "polocas_dnu": (180.0, 270.0, 365.0, 550.0, 730.0, 1100.0, None),
    "prenos_pres_leto": (0.45, 0.6, 0.75, 0.85, 0.95, 1.0),
}

# Parametry samotné předpovědi. Ty se dají zkoušet nad hotovými silami,
# takže jich může být víc.
MRIZKA_PREDIKCE = {
    "RHO_DIXON_COLES": (-0.28, -0.24, -0.20, -0.16, -0.12, -0.08, 0.0),
    "VYHODA_DOMACICH": (1.02, 1.05, 1.08, 1.11, 1.14),
    "STRMOST_INDEX": (5.0, 7.0, 9.0, 10.0, 12.0, 15.0),
    "SILA_REMIZY_INDEX": (0.25, 0.28, 0.31, 0.34, 0.38, 0.42),
    "SIRKA_REMIZY_INDEX": (150.0, 250.0, 450.0, 700.0, 1000.0),
    "VYHODA_ELO": (45.0, 60.0, 75.0, 90.0, 110.0, 130.0),
    "SILA_REMIZY_ELO": (0.26, 0.30, 0.34, 0.38, 0.42),
    "SIRKA_REMIZY_ELO": (20000.0, 40000.0, 80000.0, 160000.0, 320000.0),
    "SILA_KALIBRACE": (0.8, 0.9, 1.0, 1.1, 1.2, 1.35),
}

# Kolikrát se projdou všechny parametry dokola.
POCET_PRUCHODU = 2


def ztrata(predikce, nazev="ensemble"):
    """Log loss – měří, jak moc si model věřil ve špatných předpovědích."""
    metriky = modely.spocitej_metriky(backtest.zaznamy_modelu(predikce, nazev))
    return metriky["log_loss"] if metriky else math.inf


def _rozdel(predikce, kontrolni_sezona):
    """Rozdělí predikce na učící část a kontrolní sezónu."""
    return (
        [z for z in predikce if z["sezona"] != kontrolni_sezona],
        [z for z in predikce if z["sezona"] == kontrolni_sezona],
    )


def _hodnoty_predikce():
    return {nazev: getattr(modely, nazev) for nazev in MRIZKA_PREDIKCE}


def _nastav(hodnoty):
    for nazev, hodnota in hodnoty.items():
        setattr(modely, nazev, hodnota)


def najdi_parametry_stavu(sezony, kontrolni_sezona, vychozi):
    """Hrubé hledání útlumu historie a přenosu Elo přes léto."""
    nejlepsi = dict(vychozi)
    stavy = backtest.stav_po_dnech(sezony, **nejlepsi)
    ucici, _ = _rozdel(backtest.predikce_ze_stavu(stavy), kontrolni_sezona)
    nejlepsi_ztrata = ztrata(ucici)

    print(f"Výchozí stav: {nejlepsi} -> log loss {nejlepsi_ztrata:.4f}")

    for _ in range(POCET_PRUCHODU):
        zlepseno = False

        for nazev, hodnoty in MRIZKA_STAVU.items():
            for hodnota in hodnoty:
                if hodnota == nejlepsi[nazev]:
                    continue

                kandidat = {**nejlepsi, nazev: hodnota}
                predikce = backtest.predikce_ze_stavu(
                    backtest.stav_po_dnech(sezony, **kandidat)
                )
                ucici, _ = _rozdel(predikce, kontrolni_sezona)
                nova = ztrata(ucici)

                if nova < nejlepsi_ztrata - 1e-5:
                    nejlepsi, nejlepsi_ztrata, zlepseno = kandidat, nova, True
                    print(f"  {nazev} = {hodnota} -> {nova:.4f}")

        if not zlepseno:
            break

    return nejlepsi, nejlepsi_ztrata


def najdi_parametry_predikce(stavy, kontrolni_sezona):
    """Postupné doladění tvaru předpovědi nad hotovými silami."""
    nejlepsi = _hodnoty_predikce()
    _nastav(nejlepsi)
    ucici, _ = _rozdel(backtest.predikce_ze_stavu(stavy), kontrolni_sezona)
    nejlepsi_ztrata = ztrata(ucici)

    print(f"\nVýchozí předpověď -> log loss {nejlepsi_ztrata:.4f}")

    for _ in range(POCET_PRUCHODU):
        zlepseno = False

        for nazev, hodnoty in MRIZKA_PREDIKCE.items():
            for hodnota in hodnoty:
                if hodnota == nejlepsi[nazev]:
                    continue

                _nastav({**nejlepsi, nazev: hodnota})
                ucici, _ = _rozdel(
                    backtest.predikce_ze_stavu(stavy), kontrolni_sezona
                )
                nova = ztrata(ucici)

                if nova < nejlepsi_ztrata - 1e-5:
                    nejlepsi = {**nejlepsi, nazev: hodnota}
                    nejlepsi_ztrata, zlepseno = nova, True
                    print(f"  {nazev} = {hodnota} -> {nova:.4f}")

            _nastav(nejlepsi)

        if not zlepseno:
            break

    return nejlepsi, nejlepsi_ztrata


def _ztrata_vah(predikce, vahy):
    """Log loss ensemble s danými vahami modelů."""
    ztraty = []

    for zapas in predikce:
        slozene = modely.slozeni_predikci(zapas["modely"], vahy)
        if slozene is None:
            continue
        hodnota = modely.log_loss(*modely.kalibruj(*slozene), zapas["vysledek"])
        if hodnota is not None:
            ztraty.append(hodnota)

    return sum(ztraty) / len(ztraty) if ztraty else math.inf


def najdi_vahy(predikce, kontrolni_sezona, krok=0.05):
    """Prohledá váhy tří modelů po zadaném kroku."""
    ucici, _ = _rozdel(predikce, kontrolni_sezona)
    kroku = int(round(1 / krok))

    nejlepsi, nejlepsi_ztrata = None, math.inf
    for i in range(kroku + 1):
        for j in range(kroku + 1 - i):
            vahy = dict(
                zip(modely.NAZVY_MODELU, (i * krok, j * krok, 1 - (i + j) * krok))
            )
            nova = _ztrata_vah(ucici, vahy)
            if nova < nejlepsi_ztrata:
                nejlepsi, nejlepsi_ztrata = vahy, nova

    return nejlepsi, nejlepsi_ztrata


def _shrnuti(popis, predikce, vahy=None):
    ucici_ztrata = _ztrata_vah(predikce, vahy) if vahy else ztrata(predikce)
    print(f"{popis}: log loss {ucici_ztrata:.4f} ({len(predikce)} zápasů)")


if __name__ == "__main__":
    sezony = backtest.SEZONY
    kontrolni = sezony[-1]
    print(f"Učí se na {', '.join(sezony[:-1])}, kontrola na {kontrolni}.\n")

    parametry_stavu, _ = najdi_parametry_stavu(
        sezony,
        kontrolni,
        {
            "polocas_dnu": modely.POLOCAS_DNU,
            "prenos_pres_leto": modely.PRENOS_PRES_LETO,
        },
    )

    stavy = backtest.stav_po_dnech(sezony, **parametry_stavu)
    parametry_predikce, _ = najdi_parametry_predikce(stavy, kontrolni)

    predikce = backtest.predikce_ze_stavu(stavy)
    vahy, _ = najdi_vahy(predikce, kontrolni)

    ucici, kontrola = _rozdel(predikce, kontrolni)

    print("\n--- NALEZENÉ HODNOTY ---")
    for nazev, hodnota in {**parametry_stavu, **parametry_predikce}.items():
        print(f"{nazev} = {hodnota}")
    print("váhy modelů = " + ", ".join(f"{n} {v:.2f}" for n, v in vahy.items()))

    print("\n--- KONTROLNÍ SEZÓNA ---")
    _shrnuti("stejné váhy", kontrola)
    _shrnuti("nalezené váhy", kontrola, vahy)
    print()
    print(backtest.tabulka_metrik(kontrola).to_string())
