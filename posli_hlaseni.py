"""Odešle tipy na další kolo na Telegram.

Spuštění:

    python posli_hlaseni.py          # stáhne data, spočítá, pošle
    python posli_hlaseni.py --suchy  # jen vypíše zprávu, nic neposílá

Naplánovaná úloha na GitHubu tohle spouští každé pondělí ráno.
"""

import argparse
import sys
import traceback

import hlaseni


def main():
    parser = argparse.ArgumentParser(description="Pošle tipy na další kolo na Telegram.")
    parser.add_argument(
        "--suchy",
        action="store_true",
        help="Jen vypíše zprávu, nic neodesílá.",
    )
    argumenty = parser.parse_args()

    try:
        vysledek = hlaseni.priprav_a_posli(odeslat=not argumenty.suchy)
    except Exception:
        traceback.print_exc()
        return 1

    print(vysledek.get("log", ""))

    if vysledek.get("zprava"):
        print()
        print(vysledek["zprava"])

    if not vysledek["ok"]:
        print(f"CHYBA: {vysledek['duvod']}", file=sys.stderr)
        return 1

    if vysledek.get("odeslano"):
        print(f"Odesláno na Telegram ({vysledek['kolo']}. kolo).")
    else:
        print("Suchý běh – zpráva se neodesílala.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
