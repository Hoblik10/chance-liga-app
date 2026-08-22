"""Stažení archivu minulých sezón do složky sezony/.

Testovací klíč TheSportsDB občas kolo odmítne, proto se výsledky slučují
s tím, co už na disku leží – stačí skript spustit znovu a chybějící kola
se doberou.

    python stahni_sezony.py                    # sezóny z nastaveni.ARCHIVNI_SEZONY
    python stahni_sezony.py 2023-2024          # konkrétní ročník
"""

import sys

import data
import nastaveni


def stahni(sezony):
    for sezona in sezony:
        zapasy = data.stahni_sezonu(sezona)
        kola = sorted({zapas["kolo"] for zapas in zapasy})

        # Kola nad rámec sezóny neexistují, hlásí se jen díry uvnitř rozsahu.
        chybi = [kolo for kolo in range(1, max(kola, default=0)) if kolo not in kola]

        print(f"{sezona}: {len(zapasy)} zápasů, {len(kola)} kol")
        if chybi:
            print(f"  chybí kola {chybi} – spusť skript znovu, doplní se")


if __name__ == "__main__":
    stahni(tuple(sys.argv[1:]) or nastaveni.ARCHIVNI_SEZONY)
