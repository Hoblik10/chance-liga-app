"""Testy predikčních modelů a logu predikcí.

Spuštění:  python test_modely.py
"""

import base64
import io
import os
import tempfile
import unittest
from datetime import datetime

import pandas as pd
from PIL import Image

import data
import hlaseni
import kurzy
import kurz_obrazky
import kurz_zdroje
import modely
import sestavy
import uloziste
import vzhled
import zaznamy


def zapas(domaci, hoste, skore=None, datum="2026-08-01 17:00", stav=None):
    """Pomocník pro sestavení zápasu ve formátu, jaký používá aplikace."""
    if stav is None:
        stav = modely.ODEHRANO if skore else "🕒 Nadcházející"
    return {
        "domaci": domaci,
        "hoste": hoste,
        "datum": datum,
        "stav": stav,
        "skore": skore or "-",
    }


class TestVyhodnoceniTipu(unittest.TestCase):
    def test_vsechny_typy_tipu(self):
        pripady = [
            ("1 (Výhra domácích)", "2:1", True),
            ("1 (Výhra domácích)", "1:1", False),
            ("2 (Výhra hostů)", "0:1", True),
            ("0 (Remíza)", "1:1", True),
            ("1X (Neprohra domácích)", "1:1", True),
            ("1X (Neprohra domácích)", "0:1", False),
            ("02 (Neprohra hostů)", "1:1", True),
            ("02 (Neprohra hostů)", "2:1", False),
        ]
        for tip, skore, ocekavano in pripady:
            with self.subTest(tip=tip, skore=skore):
                self.assertIs(modely.vyhodnot_tip(tip, skore), ocekavano)

    def test_nevalidni_vstupy(self):
        for tip, skore in [("1 (Výhra domácích)", "-"), ("", "2:1"), (None, "2:1")]:
            self.assertIsNone(modely.vyhodnot_tip(tip, skore))

    def test_vysledek_zapasu(self):
        self.assertEqual(modely.vysledek_zapasu("3:0"), "1")
        self.assertEqual(modely.vysledek_zapasu("1:1"), "0")
        self.assertEqual(modely.vysledek_zapasu("0:2"), "2")
        self.assertIsNone(modely.vysledek_zapasu("nehráno"))


class TestPravdepodobnosti(unittest.TestCase):
    def test_normalizace_da_jednicku(self):
        trojice = modely.normalizuj(2.0, 1.0, 1.0)
        self.assertAlmostEqual(sum(trojice), 1.0)

    def test_normalizace_nuly(self):
        self.assertEqual(modely.normalizuj(0, 0, 0), (1 / 3, 1 / 3, 1 / 3))

    def test_index_sily_soucet(self):
        trojice = modely.predikuj_zapas(70.0, 40.0)
        self.assertAlmostEqual(sum(trojice), 1.0, places=6)

    def test_silnejsi_domaci_ma_vetsi_sanci(self):
        p_domaci, _, p_hoste = modely.predikuj_zapas(70.0, 40.0)
        self.assertGreater(p_domaci, p_hoste)

    def test_vyhoda_domaciho_prostredi(self):
        """Při stejné síle musí být favoritem domácí tým."""
        p_domaci, _, p_hoste = modely.predikuj_zapas(50.0, 50.0)
        self.assertGreater(p_domaci, p_hoste)


class TestPoisson(unittest.TestCase):
    def setUp(self):
        # Silný tým A, slabý tým C, průměrný B.
        self.databaze = {
            1: [
                zapas("A", "B", "3:0"),
                zapas("C", "A", "0:2"),
                zapas("B", "C", "2:1"),
            ],
            2: [
                zapas("A", "C", "4:0"),
                zapas("B", "A", "1:1"),
                zapas("C", "B", "0:0"),
            ],
        }
        self.sily, self.prumer_d, self.prumer_h = modely.spocitej_utok_obranu(self.databaze)

    def test_sily_jsou_pro_vsechny_tymy(self):
        self.assertEqual(set(self.sily), {"A", "B", "C"})

    def test_lepsi_utok_ma_vyssi_hodnotu(self):
        self.assertGreater(self.sily["A"]["utok"], self.sily["C"]["utok"])

    def test_lepsi_obrana_ma_nizsi_hodnotu(self):
        """Obrana pod 1.0 znamená, že tým inkasuje méně než průměr."""
        self.assertLess(self.sily["A"]["obrana"], self.sily["C"]["obrana"])

    def test_pravdepodobnosti_davaji_jednicku(self):
        trojice = modely.predikuj_poissonem(self.sily, self.prumer_d, self.prumer_h, "A", "C")
        self.assertAlmostEqual(sum(trojice), 1.0, places=6)

    def test_silnejsi_tym_je_favorit(self):
        p_domaci, _, p_hoste = modely.predikuj_poissonem(
            self.sily, self.prumer_d, self.prumer_h, "A", "C"
        )
        self.assertGreater(p_domaci, p_hoste)

    def test_neznamy_tym_vraci_none(self):
        self.assertIsNone(
            modely.predikuj_poissonem(self.sily, self.prumer_d, self.prumer_h, "A", "Neznámý")
        )

    def test_prazdna_databaze(self):
        sily, prumer_d, prumer_h = modely.spocitej_utok_obranu({})
        self.assertEqual(sily, {})
        self.assertEqual((prumer_d, prumer_h), (0.0, 0.0))

    def test_dixon_coles_zvysuje_remizy(self):
        """Korekce musí přidat pravděpodobnost remízy oproti čistému Poissonu."""
        puvodni_rho = modely.RHO_DIXON_COLES
        try:
            modely.RHO_DIXON_COLES = 0.0
            _, remiza_bez, _ = modely.predikuj_poissonem(
                self.sily, self.prumer_d, self.prumer_h, "B", "C"
            )
            modely.RHO_DIXON_COLES = -0.12
            _, remiza_s, _ = modely.predikuj_poissonem(
                self.sily, self.prumer_d, self.prumer_h, "B", "C"
            )
        finally:
            modely.RHO_DIXON_COLES = puvodni_rho

        self.assertGreater(remiza_s, remiza_bez)

    def test_ocekavane_goly_jsou_kladne(self):
        goly = modely.ocekavane_goly(self.sily, self.prumer_d, self.prumer_h, "A", "C")
        self.assertTrue(all(hodnota > 0 for hodnota in goly))


class TestElo(unittest.TestCase):
    def setUp(self):
        self.databaze = {
            1: [zapas("A", "B", "3:0", datum="2026-07-01 17:00")],
            2: [zapas("B", "A", "0:1", datum="2026-07-08 17:00")],
        }

    def test_vitez_stoupa_porazeny_klesa(self):
        rating = modely.spocitej_elo(self.databaze)
        self.assertGreater(rating["A"], modely.VYCHOZI_ELO)
        self.assertLess(rating["B"], modely.VYCHOZI_ELO)

    def test_elo_je_nulovy_soucet(self):
        rating = modely.spocitej_elo(self.databaze)
        odchylka = sum(hodnota - modely.VYCHOZI_ELO for hodnota in rating.values())
        self.assertAlmostEqual(odchylka, 0.0, places=6)

    def test_remiza_mezi_stejnymi_tymy_snizi_domaci(self):
        """Domácí je favorit, takže remíza mu Elo ubere."""
        rating = modely.spocitej_elo({1: [zapas("A", "B", "1:1")]})
        self.assertLess(rating["A"], modely.VYCHOZI_ELO)

    def test_pravdepodobnosti_davaji_jednicku(self):
        rating = modely.spocitej_elo(self.databaze)
        trojice = modely.predikuj_elem(rating, "A", "B")
        self.assertAlmostEqual(sum(trojice), 1.0, places=6)

    def test_neznamy_tym_vraci_none(self):
        rating = modely.spocitej_elo(self.databaze)
        self.assertIsNone(modely.predikuj_elem(rating, "A", "Neznámý"))


class TestMetriky(unittest.TestCase):
    def test_dokonala_predpoved_ma_nulovy_brier(self):
        self.assertAlmostEqual(modely.brier_score(1.0, 0.0, 0.0, "1"), 0.0)

    def test_uplne_spatna_predpoved(self):
        self.assertAlmostEqual(modely.brier_score(1.0, 0.0, 0.0, "2"), 2.0)

    def test_nahodny_tip_odpovida_hranici(self):
        hodnota = modely.brier_score(1 / 3, 1 / 3, 1 / 3, "1")
        self.assertAlmostEqual(hodnota, modely.BRIER_NAHODNY, places=6)

    def test_logloss_nahodneho_tipu(self):
        hodnota = modely.log_loss(1 / 3, 1 / 3, 1 / 3, "0")
        self.assertAlmostEqual(hodnota, modely.LOGLOSS_NAHODNY, places=6)

    def test_logloss_nula_nespadne(self):
        """Nulová pravděpodobnost nesmí vyhodit ZeroDivisionError ani inf."""
        hodnota = modely.log_loss(0.0, 0.5, 0.5, "1")
        self.assertTrue(hodnota > 0 and hodnota != float("inf"))

    def test_souhrn_metrik(self):
        zaznamy_testu = [
            {"p_domaci": 0.7, "p_remiza": 0.2, "p_hoste": 0.1, "vysledek": "1",
             "tip": "1 (Výhra domácích)", "skore": "2:0"},
            {"p_domaci": 0.2, "p_remiza": 0.3, "p_hoste": 0.5, "vysledek": "2",
             "tip": "2 (Výhra hostů)", "skore": "0:1"},
        ]
        souhrn = modely.spocitej_metriky(zaznamy_testu)
        self.assertEqual(souhrn["zapasu"], 2)
        self.assertEqual(souhrn["trefa_favorita"], 1.0)
        self.assertEqual(souhrn["uspesnost_tipu"], 1.0)
        self.assertLess(souhrn["brier"], modely.BRIER_NAHODNY)

    def test_prazdny_vstup(self):
        self.assertIsNone(modely.spocitej_metriky([]))


class TestEnsemble(unittest.TestCase):
    def test_prumer_bez_vah(self):
        slozene = modely.slozeni_predikci(
            {"a": (0.6, 0.2, 0.2), "b": (0.4, 0.2, 0.4)}
        )
        self.assertAlmostEqual(slozene[0], 0.5, places=6)
        self.assertAlmostEqual(sum(slozene), 1.0, places=6)

    def test_vahy_posunou_vysledek(self):
        slozene = modely.slozeni_predikci(
            {"a": (0.6, 0.2, 0.2), "b": (0.4, 0.2, 0.4)},
            {"a": 0.9, "b": 0.1},
        )
        self.assertGreater(slozene[0], 0.5)

    def test_none_modely_se_preskoci(self):
        slozene = modely.slozeni_predikci({"a": (0.6, 0.2, 0.2), "b": None})
        self.assertAlmostEqual(slozene[0], 0.6, places=6)

    def test_zadny_model(self):
        self.assertIsNone(modely.slozeni_predikci({"a": None}))

    def test_vahy_az_pri_dostatku_dat(self):
        malo = {"a": {"zapasu": 5, "brier": 0.4}}
        self.assertIsNone(modely.vahy_z_metrik(malo))

    def test_lepsi_model_dostane_vetsi_vahu(self):
        metriky = {
            "dobry": {"zapasu": 30, "brier": 0.40},
            "spatny": {"zapasu": 30, "brier": 0.60},
        }
        vahy = modely.vahy_z_metrik(metriky)
        self.assertGreater(vahy["dobry"], vahy["spatny"])
        self.assertAlmostEqual(sum(vahy.values()), 1.0, places=6)


class TestJistota(unittest.TestCase):
    def test_jistota_je_nejvyssi_pravdepodobnost(self):
        self.assertAlmostEqual(modely.jistota_predikce(0.5, 0.3, 0.2), 0.5)
        self.assertAlmostEqual(modely.jistota_predikce(0.2, 0.3, 0.5), 0.5)

    def test_otevreny_zapas_ma_jistotu_kolem_tretiny(self):
        self.assertAlmostEqual(modely.jistota_predikce(1 / 3, 1 / 3, 1 / 3), 1 / 3)

    def test_nejpravdepodobnejsi_skore(self):
        databaze = {
            1: [zapas("A", "B", "3:0"), zapas("C", "A", "0:2"), zapas("B", "C", "2:1")],
            2: [zapas("A", "C", "4:0"), zapas("B", "A", "1:1"), zapas("C", "B", "0:0")],
        }
        sily, prumer_d, prumer_h = modely.spocitej_utok_obranu(databaze)

        skore, pravdepodobnost = modely.nejpravdepodobnejsi_skore(
            sily, prumer_d, prumer_h, "A", "C"
        )
        self.assertGreaterEqual(skore[0], skore[1])
        self.assertTrue(0 < pravdepodobnost < 1)

    def test_nejpravdepodobnejsi_skore_neznamy_tym(self):
        self.assertIsNone(modely.nejpravdepodobnejsi_skore({}, 1.5, 1.2, "A", "B"))

    def test_prehled_skore_soucty(self):
        databaze = {
            1: [zapas("A", "B", "3:0"), zapas("C", "A", "0:2"), zapas("B", "C", "2:1")],
            2: [zapas("A", "C", "4:0"), zapas("B", "A", "1:1"), zapas("C", "B", "0:0")],
        }
        sily, prumer_d, prumer_h = modely.spocitej_utok_obranu(databaze)
        prehled = modely.prehled_skore(sily, prumer_d, prumer_h, "A", "C")

        self.assertIsNotNone(prehled)
        self.assertEqual(len(prehled["nejcastejsi"]), 5)
        self.assertGreater(prehled["over"][1.5], prehled["over"][2.5])
        self.assertGreater(prehled["over"][2.5], prehled["over"][3.5])
        self.assertTrue(0 < prehled["obe_skoruji"] < 1)
        self.assertAlmostEqual(
            prehled["nejcastejsi"][0][1],
            modely.nejpravdepodobnejsi_skore(sily, prumer_d, prumer_h, "A", "C")[1],
            places=6,
        )

    def test_matice_skore_da_jednicku(self):
        databaze = {1: [zapas("A", "B", "2:1")]}
        sily, prumer_d, prumer_h = modely.spocitej_utok_obranu(databaze)
        matice = modely.matice_skore(sily, prumer_d, prumer_h, "A", "B")

        self.assertAlmostEqual(sum(p for _, p in matice["skore"]), 1.0, places=6)
        self.assertAlmostEqual(
            matice["p_domaci"] + matice["p_remiza"] + matice["p_hoste"], 1.0, places=6
        )


class TestEloRozdilSkore(unittest.TestCase):
    def test_nasobitel_roste_s_rozdilem(self):
        self.assertEqual(modely.nasobitel_rozdilu(1), 1.0)
        self.assertEqual(modely.nasobitel_rozdilu(0), 1.0)
        self.assertEqual(modely.nasobitel_rozdilu(2), 1.5)
        self.assertGreater(modely.nasobitel_rozdilu(4), modely.nasobitel_rozdilu(2))

    def test_velka_vyhra_pohne_ratingem_vic(self):
        tesna = {1: [zapas("A", "B", "1:0", datum="2026-08-01 17:00")]}
        jasna = {1: [zapas("A", "B", "5:0", datum="2026-08-01 17:00")]}

        self.assertGreater(
            modely.spocitej_elo(jasna)["A"], modely.spocitej_elo(tesna)["A"]
        )
        self.assertEqual(
            modely.spocitej_elo(tesna, vazit_rozdilem=False)["A"],
            modely.spocitej_elo(jasna, vazit_rozdilem=False)["A"],
        )


class TestPredikujVsemi(unittest.TestCase):
    def setUp(self):
        databaze = {
            1: [zapas("A", "B", "3:0"), zapas("C", "A", "0:2"), zapas("B", "C", "2:1")],
            2: [zapas("A", "C", "4:0"), zapas("B", "A", "1:1"), zapas("C", "B", "0:0")],
        }
        sily_golu, prumer_d, prumer_h = modely.spocitej_utok_obranu(databaze)
        self.sily = {
            "indexy_sily": {"A": 70.0, "B": 50.0, "C": 35.0},
            "sily_golu": sily_golu,
            "prumer_domaci": prumer_d,
            "prumer_hoste": prumer_h,
            "elo": modely.spocitej_elo(databaze),
        }

    def test_vraci_vsechny_modely_i_ensemble(self):
        vysledek = modely.predikuj_vsemi(self.sily, "A", "C")
        self.assertIsNotNone(vysledek)
        self.assertEqual(set(vysledek["modely"]), set(modely.NAZVY_MODELU))
        self.assertAlmostEqual(
            vysledek["p_domaci"] + vysledek["p_remiza"] + vysledek["p_hoste"],
            1.0,
            places=6,
        )
        self.assertGreater(vysledek["jistota"], 1 / 3)
        self.assertIsNotNone(vysledek["nejcastejsi_skore"])
        self.assertIsNotNone(vysledek["prehled_skore"])
        self.assertEqual(len(vysledek["prehled_skore"]["nejcastejsi"]), modely.POCET_SKORE)

    def test_chybejici_tym_v_jednom_modelu_nespadne(self):
        vysledek = modely.predikuj_vsemi(self.sily, "A", "Neznámý")
        self.assertIsNone(vysledek)


class TestPomocneFunkce(unittest.TestCase):
    def test_forma_bere_posledni_zapasy(self):
        databaze = {
            1: [zapas("A", "B", "1:0", datum="2026-07-01 17:00")],
            2: [zapas("A", "C", "0:1", datum="2026-07-08 17:00")],
        }
        forma = modely.spocitej_formu(databaze)
        self.assertEqual(forma["A"], ["V", "P"])

    def test_bonus_za_formu(self):
        self.assertEqual(modely.bonus_za_formu(["V", "V", "V"]), 4.0)
        self.assertEqual(modely.bonus_za_formu(["P", "P", "P"]), -4.0)
        self.assertEqual(modely.bonus_za_formu([]), 0.0)

    def test_vyber_kol_preskoci_odehrana(self):
        databaze = {
            1: [zapas("A", "B", "1:0")],
            2: [zapas("C", "D")],
            3: [zapas("E", "F")],
        }
        self.assertEqual(modely.vyber_zobrazena_kola(databaze, 2), [2, 3])

    def test_odehrane_zapasy_ignoruji_nedohrane(self):
        databaze = {1: [zapas("A", "B", "1:0"), zapas("C", "D")]}
        self.assertEqual(len(modely.odehrane_zapasy(databaze)), 1)

    def test_tabulka_z_vysledku(self):
        databaze = {1: [zapas("A", "B", "2:0")]}
        df = modely.vypocitej_tabulku_z_vysledku(databaze, {"A", "B"})
        self.assertEqual(df.iloc[0]["Tým"], "A")
        self.assertEqual(df.iloc[0]["B"], 3)
        self.assertEqual(df.iloc[1]["B"], 0)


class TestZaznamy(unittest.TestCase):
    def setUp(self):
        popisovac, self.cesta = tempfile.mkstemp(suffix=".csv")
        os.close(popisovac)
        os.remove(self.cesta)

    def tearDown(self):
        if os.path.exists(self.cesta):
            os.remove(self.cesta)

    def _zapas_k_zapisu(self, stav="🕒 Nadcházející", predikce=None):
        return {
            "kolo": 5,
            "datum": "2026-08-22 17:00",
            "domaci": "A",
            "hoste": "B",
            "stav": stav,
            "predikce": predikce or {"elo": (0.5, 0.3, 0.2)},
        }

    def test_zapis_a_nacteni(self):
        pocet = zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)
        self.assertEqual(pocet, 1)

        df = zaznamy.nacti_zaznamy(self.cesta)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["model"], "elo")

    def test_predikce_se_nezapise_dvakrat(self):
        zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)
        podruhe = zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)
        self.assertEqual(podruhe, 0)

    def test_zapsana_predikce_se_neprepise(self):
        """Klíčová vlastnost: jednou zapsaná predikce se nesmí změnit."""
        zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)
        zaznamy.zapis_predikce(
            [self._zapas_k_zapisu(predikce={"elo": (0.1, 0.1, 0.8)})], cesta=self.cesta
        )

        df = zaznamy.nacti_zaznamy(self.cesta)
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(float(df.iloc[0]["p_domaci"]), 0.5)

    def test_odehrany_zapas_se_nezapisuje(self):
        pocet = zaznamy.zapis_predikce(
            [self._zapas_k_zapisu(stav=modely.ODEHRANO)], cesta=self.cesta
        )
        self.assertEqual(pocet, 0)

    def test_odlozeny_zapas_se_nezapisuje(self):
        pocet = zaznamy.zapis_predikce(
            [self._zapas_k_zapisu(stav="🔴 Odloženo")], cesta=self.cesta
        )
        self.assertEqual(pocet, 0)

    def test_doplneni_vysledku(self):
        zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)

        databaze = {5: [zapas("A", "B", "2:1", datum="2026-08-22 17:00")]}
        doplneno = zaznamy.doplnit_vysledky(databaze, cesta=self.cesta)
        self.assertEqual(doplneno, 1)

        df = zaznamy.nacti_zaznamy(self.cesta)
        self.assertEqual(df.iloc[0]["skore"], "2:1")
        self.assertEqual(df.iloc[0]["vysledek"], "1")

    def test_doplneni_je_idempotentni(self):
        zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)
        databaze = {5: [zapas("A", "B", "2:1", datum="2026-08-22 17:00")]}

        zaznamy.doplnit_vysledky(databaze, cesta=self.cesta)
        podruhe = zaznamy.doplnit_vysledky(databaze, cesta=self.cesta)
        self.assertEqual(podruhe, 0)

    def test_metriky_po_vyhodnoceni(self):
        zaznamy.zapis_predikce(
            [self._zapas_k_zapisu(predikce={"elo": (0.7, 0.2, 0.1)})], cesta=self.cesta
        )
        zaznamy.doplnit_vysledky(
            {5: [zapas("A", "B", "2:1", datum="2026-08-22 17:00")]}, cesta=self.cesta
        )

        metriky = zaznamy.metriky_podle_modelu(cesta=self.cesta)
        self.assertIn("elo", metriky)
        self.assertEqual(metriky["elo"]["zapasu"], 1)
        self.assertLess(metriky["elo"]["brier"], modely.BRIER_NAHODNY)

    def test_chybejici_soubor_nevadi(self):
        self.assertTrue(zaznamy.nacti_zaznamy("neexistujici.csv").empty)
        self.assertEqual(zaznamy.metriky_podle_modelu(cesta="neexistujici.csv"), {})

    def test_zapsane_predikce_kola(self):
        """Archiv musí ukázat tip, který vznikl před výkopem."""
        zaznamy.zapis_predikce(
            [
                self._zapas_k_zapisu(
                    predikce={"elo": (0.7, 0.2, 0.1), "ensemble": (0.6, 0.25, 0.15)}
                )
            ],
            cesta=self.cesta,
        )

        zapasy = zaznamy.zapsane_predikce_kola(5, cesta=self.cesta)
        self.assertIn(("A", "B"), zapasy)

        ensemble = zapasy[("A", "B")]["ensemble"]
        self.assertAlmostEqual(ensemble["p_domaci"], 0.6)
        self.assertTrue(ensemble["tip"].startswith("1"))
        self.assertIn("elo", zapasy[("A", "B")])

    def test_zapsane_predikce_jineho_kola(self):
        zaznamy.zapis_predikce([self._zapas_k_zapisu()], cesta=self.cesta)
        self.assertEqual(zaznamy.zapsane_predikce_kola(6, cesta=self.cesta), {})


class TestHlaseni(unittest.TestCase):
    def setUp(self):
        self.zapasy = [
            zapas("A", "B", datum="2026-08-22 17:00"),
            zapas("C", "D", datum="2026-08-22 20:00"),
            zapas("E", "F", "1:0", datum="2026-08-22 15:00"),
        ]
        self.predikce = {
            (5, 0): {
                "p_domaci": 0.62,
                "p_remiza": 0.22,
                "p_hoste": 0.16,
                "tip": "1 (Výhra domácích)",
                "jistota": 0.62,
                "nejcastejsi_skore": ((2, 0), 0.14),
            },
            (5, 1): {
                "p_domaci": 0.30,
                "p_remiza": 0.28,
                "p_hoste": 0.42,
                "tip": "02 (Neprohra hostů)",
                "jistota": 0.42,
                "nejcastejsi_skore": ((1, 1), 0.11),
            },
            (5, 2): {
                "p_domaci": 0.80,
                "p_remiza": 0.12,
                "p_hoste": 0.08,
                "tip": "1 (Výhra domácích)",
                "jistota": 0.80,
                "nejcastejsi_skore": ((2, 0), 0.20),
            },
        }

    def test_souhrn_preskoci_odehrane_a_radi_podle_jistoty(self):
        radky = hlaseni.radky_souhrnu(5, self.zapasy, self.predikce)
        self.assertEqual([r["domaci"] for r in radky], ["A", "C"])
        self.assertGreater(radky[0]["jistota"], radky[1]["jistota"])

    def test_zprava_obsahuje_tip_i_skore(self):
        zprava = hlaseni.sestav_zpravu(5, self.zapasy, self.predikce)
        self.assertIn("A – B", zprava)
        self.assertIn("62%", zprava)
        self.assertIn("2:0", zprava)
        self.assertIn("Tip 1", zprava)
        self.assertNotIn("<pre>", zprava)
        # Odehraný zápas do zprávy nepatří, i kdyby měl vyšší jistotu.
        self.assertNotIn("E – F", zprava)

    def test_text_top_skore(self):
        self.assertEqual(hlaseni.text_top_skore([]), "–")
        self.assertEqual(
            hlaseni.text_top_skore([((2, 1), 0.12), ((1, 1), 0.11)], pocet=2),
            "2:1 (12%) · 1:1 (11%)",
        )

    def test_zprava_vynecha_odlozeny_zapas(self):
        zapasy = self.zapasy + [
            zapas("FC Hradec Králové", "FC Viktoria Plzeň", stav="🔴 Odloženo")
        ]
        predikce = dict(self.predikce)
        predikce[(5, 3)] = self.predikce[(5, 0)]
        zprava = hlaseni.sestav_zpravu(5, zapasy, predikce)
        self.assertNotIn("Plzeň", zprava)
        self.assertNotIn("Hradec", zprava)

    def test_kratky_vykop(self):
        self.assertEqual(hlaseni.kratky_vykop("22.08.2026 20:00"), "22.08. 20:00")

    def test_prazdne_kolo(self):
        self.assertIsNone(hlaseni.sestav_zpravu(5, [zapas("A", "B", "1:0")], {}))

    def test_najdi_dalsi_kolo(self):
        databaze = {
            4: [zapas("A", "B", "1:0")],
            5: [zapas("C", "D")],
        }
        self.assertEqual(hlaseni.najdi_dalsi_kolo(databaze, [4, 5]), 5)
        self.assertIsNone(hlaseni.najdi_dalsi_kolo(databaze, [4]))

    def test_telegram_se_stejny_den_neduplikuje(self):
        with tempfile.TemporaryDirectory() as slozka:
            cesta = os.path.join(slozka, "telegram.json")
            ted = datetime(2026, 8, 28, 8, 0)
            hlaseni.uloz_odeslani_telegramu(
                6, sezona="2026-2027", ted=ted, cesta=cesta
            )
            self.assertTrue(
                hlaseni.uz_odeslano_dnes(
                    6, sezona="2026-2027", ted=ted, cesta=cesta
                )
            )
            self.assertFalse(
                hlaseni.uz_odeslano_dnes(
                    6,
                    sezona="2026-2027",
                    ted=datetime(2026, 8, 29, 8, 0),
                    cesta=cesta,
                )
            )
            self.assertFalse(
                hlaseni.uz_odeslano_dnes(
                    7, sezona="2026-2027", ted=ted, cesta=cesta
                )
            )

    def test_telegram_sobotni_zaloha_neduplikuje_patek(self):
        with tempfile.TemporaryDirectory() as slozka:
            cesta = os.path.join(slozka, "telegram.json")
            patek = datetime(2026, 8, 28, 8, 15, tzinfo=data.PASMO_PRAHA)
            hlaseni.uloz_odeslani_telegramu(
                6, sezona="2026-2027", ted=patek, cesta=cesta
            )
            self.assertTrue(
                hlaseni.uz_odeslano_nedavno(
                    6,
                    sezona="2026-2027",
                    ted=datetime(2026, 8, 29, 8, 15, tzinfo=data.PASMO_PRAHA),
                    cesta=cesta,
                )
            )
            self.assertFalse(
                hlaseni.uz_odeslano_nedavno(
                    6,
                    sezona="2026-2027",
                    ted=datetime(2026, 9, 4, 8, 15, tzinfo=data.PASMO_PRAHA),
                    cesta=cesta,
                )
            )

    def test_najdi_dalsi_kolo_preskoci_odlozene_zbytky(self):
        """Ve 4. kole zbývají jen zářijové odklady, 5. kolo je příští víkend."""
        from datetime import datetime

        praha = data.PASMO_PRAHA
        ted = datetime(2026, 8, 17, 8, 0, tzinfo=praha)

        def s_casem(domaci, hoste, cas, stav="🕒 Nadcházející"):
            polozka = zapas(domaci, hoste)
            polozka["cas"] = cas
            polozka["stav"] = stav
            return polozka

        databaze = {
            4: [
                s_casem("A", "B", datetime(2026, 8, 16, 20, 0, tzinfo=praha), modely.ODEHRANO),
                s_casem("C", "D", datetime(2026, 9, 2, 18, 0, tzinfo=praha)),
            ],
            5: [
                s_casem("E", "F", datetime(2026, 8, 22, 17, 0, tzinfo=praha)),
            ],
        }
        self.assertEqual(hlaseni.najdi_dalsi_kolo(databaze, [4, 5], ted=ted), 5)

    def test_kratky_nazev(self):
        self.assertEqual(hlaseni.kratky_nazev("SK Slavia Praha"), "Slavia")
        self.assertEqual(hlaseni.kratky_nazev("Bohemians Praha 1905"), "Bohemians")
        self.assertEqual(hlaseni.kratky_nazev("1. FC Slovácko"), "Slovácko")


class TestCasVPraze(unittest.TestCase):
    def test_letni_cas_posune_o_dve_hodiny(self):
        cas = data.cas_v_praze({"strTimestamp": "2026-08-16T18:00:00"})
        self.assertEqual(cas.strftime("%Y-%m-%d %H:%M"), "2026-08-16 20:00")

    def test_odpoledni_vykop(self):
        cas = data.cas_v_praze({"strTimestamp": "2026-08-16T13:00:00"})
        self.assertEqual(cas.strftime("%Y-%m-%d %H:%M"), "2026-08-16 15:00")

    def test_zimni_cas_posune_o_hodinu(self):
        cas = data.cas_v_praze({"strTimestamp": "2026-12-05T15:00:00"})
        self.assertEqual(cas.strftime("%Y-%m-%d %H:%M"), "2026-12-05 16:00")

    def test_prepad_pres_pulnoc(self):
        cas = data.cas_v_praze({"strTimestamp": "2026-08-16T23:00:00"})
        self.assertEqual(cas.strftime("%Y-%m-%d %H:%M"), "2026-08-17 01:00")

    def test_fallback_z_dateevent(self):
        cas = data.cas_v_praze({"dateEvent": "2026-08-16", "strTime": "18:00:00"})
        self.assertEqual(cas.strftime("%Y-%m-%d %H:%M"), "2026-08-16 20:00")

    def test_neplatny_vstup(self):
        self.assertIsNone(data.cas_v_praze({}))

    def test_format_vykopu(self):
        cas = data.cas_v_praze({"strTimestamp": "2026-08-16T18:00:00"})
        self.assertEqual(data.formatuj_vykop(cas), "16.08.2026 20:00")

    def test_preved_zapas_pouziva_prazsky_cas(self):
        zapas = data._preved_zapas(
            {
                "strHomeTeam": "Slavia Prague",
                "strAwayTeam": "Sparta Prague",
                "strTimestamp": "2026-08-16T18:00:00",
                "strStatus": "NS",
                "strPostponed": "no",
            }
        )
        self.assertEqual(zapas["datum"], "16.08.2026 20:00")

    def test_oznac_prelozene(self):
        from datetime import datetime

        praha = data.PASMO_PRAHA

        def polozka(cas):
            return {
                "cas": datetime.fromisoformat(cas).replace(tzinfo=praha),
                "stav": "🕒 Nadcházející",
                "datum": cas,
            }

        zapasy = [
            polozka("2026-08-15 17:00"),
            polozka("2026-08-16 20:00"),
            polozka("2026-09-02 18:00"),
        ]
        vysledek = data.oznac_prelozene(zapasy)
        self.assertEqual(vysledek[0]["stav"], "🕒 Nadcházející")
        self.assertEqual(vysledek[1]["stav"], "🕒 Nadcházející")
        self.assertTrue(vysledek[2]["stav"].startswith("🔴"))
        self.assertIn("02.09.2026", vysledek[2]["poznamka_termin"])

    def test_odklad_bez_nahradniho_terminu(self):
        """Hradec–Plzeň zůstalo na 23. 8., LFA ale zápas odložila."""
        from datetime import datetime

        praha = data.PASMO_PRAHA
        zapasy = [
            {
                "domaci": "SK Slavia Praha",
                "hoste": "Bohemians Praha 1905",
                "cas": datetime(2026, 8, 22, 20, 0, tzinfo=praha),
                "stav": "🕒 Nadcházející",
            },
            {
                "domaci": "FK Teplice",
                "hoste": "FC Zbrojovka Brno",
                "cas": datetime(2026, 8, 23, 17, 0, tzinfo=praha),
                "stav": "🕒 Nadcházející",
            },
            {
                "domaci": "FC Hradec Králové",
                "hoste": "FC Viktoria Plzeň",
                "cas": datetime(2026, 8, 23, 17, 0, tzinfo=praha),
                "stav": "🕒 Nadcházející",
            },
        ]
        vysledek = data.oznac_prelozene(zapasy)
        self.assertEqual(vysledek[0]["stav"], "🕒 Nadcházející")
        self.assertEqual(vysledek[1]["stav"], "🕒 Nadcházející")
        self.assertTrue(vysledek[2]["stav"].startswith("🔴"))
        self.assertIn("náhradní termín", vysledek[2]["poznamka_termin"])

    def test_odklad_bez_terminu_po_presunu_data_neplati(self):
        """Nový termín už není 23. 8. – bere se jako běžný, nebo jako posun kola."""
        from datetime import datetime

        praha = data.PASMO_PRAHA
        zapas = {
            "domaci": "FC Hradec Králové",
            "hoste": "FC Viktoria Plzeň",
            "cas": datetime(2026, 10, 14, 18, 0, tzinfo=praha),
            "stav": "🕒 Nadcházející",
        }
        self.assertFalse(data._je_odklad_bez_terminu(zapas))

    def test_odklad_bohemians_boleslav_bez_terminu(self):
        praha = data.PASMO_PRAHA
        zapas = {
            "domaci": "Bohemians Praha 1905",
            "hoste": "FK Mladá Boleslav",
            "cas": datetime(2026, 8, 29, 17, 0, tzinfo=praha),
            "stav": "🕒 Nadcházející",
        }
        self.assertTrue(data._je_odklad_bez_terminu(zapas))
        vysledek = data.oznac_prelozene([zapas])
        self.assertTrue(vysledek[0]["stav"].startswith("🔴"))

    def test_parsuj_odlozene_z_chanceligy(self):
        nalezene = data.parsuj_odlozene_zapasy(HTML_ODLOZENEHO_ZAPASU)
        self.assertEqual(
            nalezene, [("Bohemians Praha 1905", "FK Mladá Boleslav")]
        )

    def test_oznac_odklad_z_webu(self):
        zapasy = [
            zapas("Bohemians Praha 1905", "FK Mladá Boleslav"),
            zapas("FK Pardubice", "SK Artis Brno"),
        ]
        data._oznac_odklady_z_webu(
            zapasy, odklady=[("Bohemians Praha 1905", "FK Mladá Boleslav")]
        )
        self.assertTrue(zapasy[0]["stav"].startswith("🔴"))
        self.assertEqual(zapasy[1]["stav"], "🕒 Nadcházející")


class TestChronologie(unittest.TestCase):
    def test_cas_zapasu_zvlada_oba_tvary(self):
        self.assertEqual(
            modely.cas_zapasu({"datum": "15.08.2026 17:00"}),
            datetime(2026, 8, 15, 17, 0),
        )
        self.assertEqual(
            modely.cas_zapasu({"datum": "2026-08-15 17:00"}),
            datetime(2026, 8, 15, 17, 0),
        )

    def test_razeni_nejde_podle_retezce(self):
        """Textově by '05.09.' předběhlo '30.08.', chronologicky ne."""
        databaze = {
            1: [zapas("A", "B", "1:0", datum="30.08.2026 17:00")],
            2: [zapas("C", "D", "2:0", datum="05.09.2026 17:00")],
        }
        poradi = [z["domaci"] for z in modely.odehrane_zapasy(databaze)]
        self.assertEqual(poradi, ["A", "C"])


class TestUtlumHistorie(unittest.TestCase):
    def test_starsi_zapasy_vazi_min(self):
        """A i B daly čtyři góly, ale A nedávno – útlum musí A upřednostnit."""
        databaze = {
            1: [
                zapas("A", "X", "0:0", datum="2024-08-01 17:00"),
                zapas("B", "X", "4:0", datum="2024-08-01 17:00"),
            ],
            2: [
                zapas("A", "Y", "4:0", datum="2026-08-01 17:00"),
                zapas("B", "Y", "0:0", datum="2026-08-01 17:00"),
            ],
        }

        s_utlumem, _, _ = modely.spocitej_utok_obranu(databaze, polocas_dnu=180.0)
        bez_utlumu, _, _ = modely.spocitej_utok_obranu(databaze, polocas_dnu=None)

        self.assertGreater(s_utlumem["A"]["utok"], s_utlumem["B"]["utok"])
        self.assertAlmostEqual(
            bez_utlumu["A"]["utok"], bez_utlumu["B"]["utok"], places=6
        )

    def test_bez_polocasu_vazi_vsechno_stejne(self):
        databaze = {
            1: [zapas("A", "B", "2:0", datum="2024-08-01 17:00")],
            2: [zapas("B", "A", "2:0", datum="2026-08-01 17:00")],
        }
        sily, _, _ = modely.spocitej_utok_obranu(databaze, polocas_dnu=None)
        self.assertAlmostEqual(sily["A"]["utok"], sily["B"]["utok"], places=6)


class TestPrenosElo(unittest.TestCase):
    def test_leto_stahne_rating_k_prumeru(self):
        stara_sezona = {
            1: [zapas("A", "B", "3:0", datum="2025-04-01 17:00")],
        }
        s_novou = {
            **stara_sezona,
            2: [zapas("C", "D", "1:1", datum="2025-07-20 17:00")],
        }

        pred_letem = modely.spocitej_elo(stara_sezona)["A"]
        po_lete = modely.spocitej_elo(s_novou)["A"]

        self.assertLess(po_lete, pred_letem)
        self.assertGreater(po_lete, modely.VYCHOZI_ELO)

    def test_zimni_pauza_rating_nestahuje(self):
        databaze = {
            1: [zapas("A", "B", "3:0", datum="2025-12-10 17:00")],
            2: [zapas("C", "D", "1:1", datum="2026-02-10 17:00")],
        }
        jen_prvni = {1: databaze[1]}

        self.assertEqual(
            modely.spocitej_elo(databaze)["A"], modely.spocitej_elo(jen_prvni)["A"]
        )


class TestTipy(unittest.TestCase):
    def test_dvojita_sance_vynecha_nejmene_pravdepodobne(self):
        self.assertEqual(modely.dvojita_sance(0.5, 0.3, 0.2), modely.POPISY_TIPU["1X"])
        self.assertEqual(modely.dvojita_sance(0.2, 0.3, 0.5), modely.POPISY_TIPU["02"])
        self.assertEqual(modely.dvojita_sance(0.45, 0.1, 0.45), modely.POPISY_TIPU["12"])

    def test_cil_uspesnost_tipuje_vzdy_dvojitou_sanci(self):
        tip = modely.tip_z_pravdepodobnosti(
            0.8, 0.15, 0.05, cil=modely.CIL_USPESNOST
        )
        self.assertEqual(tip, modely.POPISY_TIPU["1X"])

    def test_cil_informace_rekne_viteze(self):
        tip = modely.tip_z_pravdepodobnosti(
            0.8, 0.15, 0.05, cil=modely.CIL_INFORMACE
        )
        self.assertEqual(tip, modely.POPISY_TIPU["1"])

    def test_cil_informace_pod_prahem_couvne(self):
        tip = modely.tip_z_pravdepodobnosti(
            0.4, 0.35, 0.25, cil=modely.CIL_INFORMACE
        )
        self.assertEqual(tip, modely.POPISY_TIPU["1X"])

    def test_vyhodnoceni_tipu_bez_remizy(self):
        self.assertIs(modely.vyhodnot_tip(modely.POPISY_TIPU["12"], "2:1"), True)
        self.assertIs(modely.vyhodnot_tip(modely.POPISY_TIPU["12"], "1:1"), False)


class TestKalibrace(unittest.TestCase):
    def test_zplosteni_snizi_jistotu(self):
        trojice = modely.kalibruj(0.7, 0.2, 0.1, sila=0.5)
        self.assertAlmostEqual(sum(trojice), 1.0, places=6)
        self.assertLess(trojice[0], 0.7)

    def test_vyostreni_zvysi_jistotu(self):
        trojice = modely.kalibruj(0.7, 0.2, 0.1, sila=1.5)
        self.assertGreater(trojice[0], 0.7)

    def test_poradi_zustava(self):
        trojice = modely.kalibruj(0.5, 0.3, 0.2, sila=0.4)
        self.assertEqual(list(trojice), sorted(trojice, reverse=True))

    def test_sila_jedna_nemeni_nic(self):
        self.assertEqual(modely.kalibruj(0.5, 0.3, 0.2, sila=1.0), (0.5, 0.3, 0.2))

    def test_spolehlivost_porovna_slib_se_skutecnosti(self):
        zaznamy_pasma = [
            {"p_domaci": 0.7, "p_remiza": 0.2, "p_hoste": 0.1, "vysledek": "1"},
            {"p_domaci": 0.7, "p_remiza": 0.2, "p_hoste": 0.1, "vysledek": "2"},
        ]
        pasma = modely.spolehlivost(zaznamy_pasma)

        self.assertEqual(len(pasma), 1)
        self.assertEqual(pasma[0]["zapasu"], 2)
        self.assertAlmostEqual(pasma[0]["slibeno"], 0.7)
        self.assertAlmostEqual(pasma[0]["skutecnost"], 0.5)


class TestKurzy(unittest.TestCase):
    def test_marze_vyjde_z_prehnane_knihy(self):
        # Férová kniha 2.0 / 4.0 / 4.0 dá přesně jedničku.
        self.assertAlmostEqual(kurzy.marze(2.0, 4.0, 4.0), 0.0, places=6)
        self.assertGreater(kurzy.marze(1.9, 3.6, 3.8), 0.0)

    def test_ocisteni_marze_da_jednicku(self):
        trzni = kurzy.ocisti_marzi(1.9, 3.6, 3.8)
        self.assertAlmostEqual(sum(trzni), 1.0, places=6)
        self.assertGreater(trzni[0], trzni[1])

    def test_hodnota_je_nulova_pri_ferovem_kurzu(self):
        """Kurz přesně podle modelu nesmí vypadat jako příležitost."""
        self.assertAlmostEqual(kurzy.hodnota_sazky(0.5, 2.0), 0.0, places=6)
        self.assertAlmostEqual(kurzy.hodnota_sazky(0.5, 2.2), 0.1, places=6)
        self.assertLess(kurzy.hodnota_sazky(0.5, 1.8), 0.0)

    def test_kelly_neroste_do_zaporu(self):
        self.assertEqual(kurzy.kelly(0.3, 2.0), 0.0)
        self.assertGreater(kurzy.kelly(0.6, 2.0), 0.0)

    def test_kelly_je_zlomkovy(self):
        plny = kurzy.kelly(0.6, 2.0, podil=1.0)
        ctvrtinovy = kurzy.kelly(0.6, 2.0, podil=0.25)
        self.assertAlmostEqual(ctvrtinovy, plny * 0.25, places=6)

    def test_nejlepsi_hodnota_respektuje_prah(self):
        model = (0.55, 0.25, 0.20)

        self.assertIsNone(kurzy.nejlepsi_hodnota(model, (1.80, 3.60, 4.50)))

        nalezena = kurzy.nejlepsi_hodnota(model, (2.10, 3.60, 4.50))
        self.assertEqual(nalezena["vysledek"], "1")
        self.assertGreater(nalezena["hodnota"], kurzy.MIN_HODNOTA)

    def test_rozdil_od_trhu(self):
        shodny = kurzy.ocisti_marzi(2.0, 4.0, 4.0)
        self.assertAlmostEqual(kurzy.rozdil_od_trhu(shodny, (2.0, 4.0, 4.0)), 0.0, places=6)
        self.assertGreater(kurzy.rozdil_od_trhu((0.8, 0.1, 0.1), (2.0, 4.0, 4.0)), 0.0)

    def test_neplatne_kurzy(self):
        for kurz in (None, "", 0.5, 1.0, 200.0, "abc"):
            self.assertFalse(kurzy.platny_kurz(kurz))
        for kurz in (1.01, 2.5, "3.2", 99.0):
            self.assertTrue(kurzy.platny_kurz(kurz))


class TestUlozeneKurzy(unittest.TestCase):
    def setUp(self):
        self.docasny = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        self.docasny.close()
        os.unlink(self.docasny.name)
        self.cesta = self.docasny.name

    def tearDown(self):
        if os.path.exists(self.cesta):
            os.unlink(self.cesta)

    def test_chybejici_soubor_nevadi(self):
        self.assertEqual(kurzy.nacti_kurzy(self.cesta), {})

    def test_zapis_a_nacteni(self):
        kurzy.uloz_kurz(5, "Slavia", "Sparta", (1.8, 3.7, 4.2), cesta=self.cesta)
        ulozene = kurzy.nacti_kurzy(self.cesta)

        self.assertEqual(ulozene[(5, "Slavia", "Sparta")], (1.8, 3.7, 4.2))

    def test_kurz_se_prepisuje(self):
        """Kurzy se hýbou až do výkopu, platí ten poslední."""
        kurzy.uloz_kurz(5, "Slavia", "Sparta", (1.8, 3.7, 4.2), cesta=self.cesta)
        kurzy.uloz_kurz(5, "Slavia", "Sparta", (1.7, 3.8, 4.5), cesta=self.cesta)

        ulozene = kurzy.nacti_kurzy(self.cesta)
        self.assertEqual(len(ulozene), 1)
        self.assertEqual(ulozene[(5, "Slavia", "Sparta")], (1.7, 3.8, 4.5))

    def test_neplatny_kurz_se_neulozi(self):
        with self.assertRaises(ValueError):
            kurzy.uloz_kurz(5, "Slavia", "Sparta", (1.8, 0.5, 4.2), cesta=self.cesta)

    def test_ulozene_kurzy_nesou_zdroj(self):
        kurzy.uloz_kurz(
            6,
            "Slavia",
            "Sparta",
            (1.85, 3.6, 4.1),
            cesta=self.cesta,
            zdroj="tipsport",
            sazkovka="Tipsport",
        )
        info = kurzy.nacti_kurzy_info(self.cesta)
        self.assertEqual(info[(6, "Slavia", "Sparta")]["sazkovka"], "Tipsport")
        self.assertEqual(info[(6, "Slavia", "Sparta")]["kurzy"], (1.85, 3.6, 4.1))

    def test_novejsi_kurz_pri_slouceni_vyhraje(self):
        starsi = pd.DataFrame(
            [
                {
                    "kolo": 6,
                    "domaci": "Slavia",
                    "hoste": "Sparta",
                    "kurz_1": 1.8,
                    "kurz_0": 3.5,
                    "kurz_2": 4.4,
                    "zdroj": "rucne",
                    "sazkovka": "",
                    "zapsano": "2026-08-28 10:00",
                }
            ]
        )
        novejsi = pd.DataFrame(
            [
                {
                    "kolo": 6,
                    "domaci": "Slavia",
                    "hoste": "Sparta",
                    "kurz_1": 1.7,
                    "kurz_0": 3.6,
                    "kurz_2": 4.6,
                    "zdroj": "rucne",
                    "sazkovka": "",
                    "zapsano": "2026-08-29 01:00",
                }
            ]
        )
        sloucene = kurzy.sluc_tabulky(starsi, novejsi)
        self.assertEqual(len(sloucene), 1)
        self.assertEqual(float(sloucene.iloc[0]["kurz_1"]), 1.7)

    def test_sluc_do_souboru_doplni_jiny_zapas(self):
        kurzy.uloz_kurz(6, "Slavia", "Sparta", (1.8, 3.7, 4.2), cesta=self.cesta)
        dalsi = pd.DataFrame(
            [
                {
                    "kolo": 6,
                    "domaci": "Plzeň",
                    "hoste": "Baník",
                    "kurz_1": 2.1,
                    "kurz_0": 3.3,
                    "kurz_2": 3.4,
                    "zdroj": "rucne",
                    "sazkovka": "",
                    "zapsano": "2026-08-29 02:00",
                }
            ]
        )
        kurzy.sluc_do_souboru(dalsi, cesta=self.cesta)
        ulozene = kurzy.nacti_kurzy(self.cesta)
        self.assertEqual(len(ulozene), 2)
        self.assertEqual(ulozene[(6, "Plzeň", "Baník")], (2.1, 3.3, 3.4))

    def test_kurzy_se_lisi(self):
        self.assertFalse(kurzy.kurzy_se_lisi((1.8, 3.5, 4.2), (1.8, 3.5, 4.2)))
        self.assertTrue(kurzy.kurzy_se_lisi((1.8, 3.5, 4.2), (1.85, 3.5, 4.2)))
        self.assertTrue(kurzy.kurzy_se_lisi(None, (1.8, 3.5, 4.2)))
        self.assertFalse(kurzy.kurzy_se_lisi(None, None))

    def test_dopln_prazdna_pole_po_ocr(self):
        """Prázdný number_input po fotce dostane hodnoty z CSV, ruční kurz nechá."""
        zapasy = [
            {"domaci": "Slavia", "hoste": "Sparta"},
            {"domaci": "Plzeň", "hoste": "Baník"},
        ]
        ulozene = {
            (6, "Slavia", "Sparta"): (1.55, 4.20, 5.80),
            (6, "Plzeň", "Baník"): (1.90, 3.50, 3.80),
        }
        session = {
            kurzy.klic_pole_kurzu(6, 0, "1"): None,
            kurzy.klic_pole_kurzu(6, 0, "X"): None,
            kurzy.klic_pole_kurzu(6, 0, "2"): None,
            kurzy.klic_pole_kurzu(6, 1, "1"): 2.05,
            kurzy.klic_pole_kurzu(6, 1, "X"): 3.40,
            kurzy.klic_pole_kurzu(6, 1, "2"): 3.50,
        }

        kurzy.dopln_prazdna_pole(session, 6, zapasy, ulozene)

        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 0, "1")], 1.55)
        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 0, "X")], 4.20)
        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 0, "2")], 5.80)
        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 1, "1")], 2.05)

        kurzy.dopln_prazdna_pole(session, 6, zapasy, ulozene, prepsat=True)
        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 1, "1")], 1.90)
        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 1, "X")], 3.50)

    def test_dopln_prazdna_pole_prepise_neplatny_zbytek(self):
        zapasy = [{"domaci": "Slavia", "hoste": "Sparta"}]
        ulozene = {(6, "Slavia", "Sparta"): (1.70, 3.80, 4.90)}
        session = {kurzy.klic_pole_kurzu(6, 0, "1"): 0.0}

        kurzy.dopln_prazdna_pole(session, 6, zapasy, ulozene)

        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 0, "1")], 1.70)
        self.assertEqual(session[kurzy.klic_pole_kurzu(6, 0, "X")], 3.80)


class TestUlozisteKurzu(unittest.TestCase):
    def test_kodovani_prezije_cestu_tam_a_zpet(self):
        df = pd.DataFrame(
            [
                {
                    "kolo": 6,
                    "domaci": "Bohemians Praha 1905",
                    "hoste": "FK Mladá Boleslav",
                    "kurz_1": 1.85,
                    "kurz_0": 3.6,
                    "kurz_2": 4.1,
                    "zdroj": "rucne",
                    "sazkovka": "",
                    "zapsano": "2026-08-29 01:40",
                }
            ]
        )
        kod = uloziste.zakoduj_zalohu(df)
        zpet = uloziste.dekoduj_zalohu(kod)
        self.assertTrue(kod)
        self.assertEqual(
            kurzy.tabulka_na_zaznamy(zpet)[0]["kurz_1"],
            1.85,
        )
        self.assertEqual(
            kurzy.tabulka_na_zaznamy(zpet)[0]["domaci"],
            "Bohemians Praha 1905",
        )
        self.assertIsNone(uloziste.dekoduj_zalohu(""))
        self.assertIsNone(uloziste.dekoduj_zalohu("neni-base64%%%"))

    def test_html_obsahuje_zalohu(self):
        html = uloziste.html_prohlizece("abc123")
        self.assertIn("chance_liga_kurzy_v1", html)
        self.assertIn("abc123", html)
        self.assertIn("kjson", html)

    def test_sync_prohlizece_zapise_parametr(self):
        df = pd.DataFrame(
            [
                {
                    "kolo": 5,
                    "domaci": "Slavia",
                    "hoste": "Sparta",
                    "kurz_1": 1.9,
                    "kurz_0": 3.5,
                    "kurz_2": 4.0,
                    "zdroj": "rucne",
                    "sazkovka": "",
                    "zapsano": "2026-08-29 03:00",
                }
            ]
        )

        class DummySt:
            def __init__(self):
                self.query_params = {}

        class DummyComponents:
            def __init__(self):
                self.htmls = []

            def html(self, zdroj, height=0):
                self.htmls.append(zdroj)

        st_modul = DummySt()
        komponenty = DummyComponents()
        kod = uloziste.sync_prohlizec(st_modul, komponenty, df)
        self.assertTrue(kod)
        self.assertEqual(st_modul.query_params[uloziste.PARAMETR_URL], kod)
        self.assertEqual(len(komponenty.htmls), 1)
        self.assertIn(kod, komponenty.htmls[0])

    def test_nacteni_z_githubu(self):
        csv_text = (
            "kolo,domaci,hoste,kurz_1,kurz_0,kurz_2,zdroj,sazkovka,zapsano\n"
            "6,Slavia,Sparta,1.75,3.6,4.5,rucne,,2026-08-29 01:00\n"
        )
        obsah = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")

        class Odpoved:
            status_code = 200

            def json(self):
                return {"content": obsah, "sha": "abc"}

        df = uloziste.nacti_z_githubu(get=lambda *args, **kwargs: Odpoved())
        self.assertEqual(float(df.iloc[0]["kurz_1"]), 1.75)
        self.assertEqual(df.iloc[0]["domaci"], "Slavia")

    def test_github_bez_tokenu_nezapisuje(self):
        original = uloziste.github_token
        uloziste.github_token = lambda: ""
        try:
            ok, zprava = uloziste.uloz_na_github(pd.DataFrame(columns=kurzy.SLOUPCE))
        finally:
            uloziste.github_token = original
        self.assertFalse(ok)
        self.assertIn("GITHUB_TOKEN", zprava)

    def test_popis_zalohy_zmeni_se_s_tokenem(self):
        original = uloziste.github_token
        uloziste.github_token = lambda: ""
        try:
            bez = uloziste.popis_zalohy()
        finally:
            uloziste.github_token = original
        self.assertIn("prohlížeči", bez)
        uloziste.github_token = lambda: "token"
        try:
            s_tokenem = uloziste.popis_zalohy()
        finally:
            uloziste.github_token = original
        self.assertIn("GitHubu", s_tokenem)

    def test_uloz_na_github_zapise_soubor(self):
        put_tela = []

        class Odpoved:
            def __init__(self, code, data=None):
                self.status_code = code
                self._data = data or {}

            def json(self):
                return self._data

        def get(url, **kwargs):
            if url.endswith("/git/ref/heads/data"):
                return Odpoved(200, {"object": {"sha": "data-sha"}})
            return Odpoved(404)

        def put(url, **kwargs):
            put_tela.append(kwargs.get("json") or {})
            return Odpoved(201)

        df = pd.DataFrame(
            [
                {
                    "kolo": 6,
                    "domaci": "Slavia",
                    "hoste": "Sparta",
                    "kurz_1": 1.8,
                    "kurz_0": 3.5,
                    "kurz_2": 4.2,
                    "zdroj": "rucne",
                    "sazkovka": "",
                    "zapsano": "2026-08-29 04:00",
                }
            ]
        )
        original = uloziste.github_token
        uloziste.github_token = lambda: "token"
        try:
            ok, zprava = uloziste.uloz_na_github(df, get=get, put=put)
        finally:
            uloziste.github_token = original
        self.assertTrue(ok, zprava)
        self.assertEqual(len(put_tela), 1)
        self.assertEqual(put_tela[0]["branch"], "data")
        self.assertIn("content", put_tela[0])


ZAPASY_PRO_FOTKU = [
    {"domaci": "SK Slavia Praha", "hoste": "AC Sparta Praha"},
    {"domaci": "FC Viktoria Plzeň", "hoste": "FC Baník Ostrava"},
    {"domaci": "Bohemians Praha 1905", "hoste": "FK Mladá Boleslav"},
]


def _png_bajty():
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, format="PNG")
    return buf.getvalue()


class TestKurzObrazky(unittest.TestCase):
    def test_radek_s_pomlckou_a_carkou(self):
        text = "SK Slavia Praha - AC Sparta Praha 1,55 4,20 5,80"
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        self.assertEqual(len(nabidka), 1)
        self.assertEqual(nabidka[0]["domaci_surove"], "SK Slavia Praha")
        self.assertEqual(nabidka[0]["hoste_surove"], "AC Sparta Praha")
        self.assertEqual(nabidka[0]["kurzy"], (1.55, 4.20, 5.80))

    def test_mobilni_dva_radky_a_kurzy_pod_nimi(self):
        text = (
            "Slavia Praha\n"
            "Sparta Praha\n"
            "1.55  4.20  5.80\n"
            "Viktoria Plzeň\n"
            "Baník Ostrava\n"
            "1.90 3.50 3.80\n"
        )
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, ZAPASY_PRO_FOTKU)
        self.assertEqual(
            parovane[("SK Slavia Praha", "AC Sparta Praha")]["kurzy"],
            (1.55, 4.20, 5.80),
        )
        self.assertEqual(
            parovane[("FC Viktoria Plzeň", "FC Baník Ostrava")]["kurzy"],
            (1.90, 3.50, 3.80),
        )

    def test_zkratky_z_chanceligy(self):
        text = "BOH - MBL 2.10 3.30 3.40"
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        self.assertEqual(len(nabidka), 1)
        self.assertEqual(nabidka[0]["domaci_surove"], "Bohemians Praha 1905")
        self.assertEqual(nabidka[0]["hoste_surove"], "FK Mladá Boleslav")
        self.assertEqual(nabidka[0]["kurzy"], (2.10, 3.30, 3.40))

    def test_cele_kolo_z_pozic(self):
        text = (
            "Chance Liga\n"
            "Slavia 1.55 4.20 5.80 Sparta\n"
            "Plzeň 1.90 3.50 3.80 Ostrava\n"
            "Bohemians 2.05 3.25 3.60 Boleslav\n"
        )
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, ZAPASY_PRO_FOTKU)
        self.assertEqual(len(parovane), 3)
        self.assertEqual(
            parovane[("Bohemians Praha 1905", "FK Mladá Boleslav")]["kurzy"][0],
            2.05,
        )

    def test_tri_kurzy_pod_sebou_u_zapasu(self):
        """Na fotce je 1 nahoře, remíza, 2 dole – ne vedle sebe přes zápasy."""
        text = (
            "SK Slavia Praha\n"
            "AC Sparta Praha\n"
            "1.55\n"
            "4.20\n"
            "5.80\n"
            "FC Viktoria Plzeň\n"
            "FC Baník Ostrava\n"
            "1.90\n"
            "3.50\n"
            "3.80\n"
        )
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, ZAPASY_PRO_FOTKU)
        self.assertEqual(
            parovane[("SK Slavia Praha", "AC Sparta Praha")]["kurzy"],
            (1.55, 4.20, 5.80),
        )
        self.assertEqual(
            parovane[("FC Viktoria Plzeň", "FC Baník Ostrava")]["kurzy"],
            (1.90, 3.50, 3.80),
        )

    def test_ocr_nejdriv_nazvy_pak_sloupec_kurzu(self):
        text = (
            "Slavia Praha\n"
            "Sparta Praha\n"
            "Viktoria Plzeň\n"
            "Baník Ostrava\n"
            "1.55\n"
            "4.20\n"
            "5.80\n"
            "1.90\n"
            "3.50\n"
            "3.80\n"
        )
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, ZAPASY_PRO_FOTKU)
        self.assertEqual(
            parovane[("SK Slavia Praha", "AC Sparta Praha")]["kurzy"],
            (1.55, 4.20, 5.80),
        )
        self.assertEqual(
            parovane[("FC Viktoria Plzeň", "FC Baník Ostrava")]["kurzy"],
            (1.90, 3.50, 3.80),
        )

    def test_dva_sloupce_zapasu_vedle_sebe(self):
        text = (
            "Slavia Praha  Viktoria Plzeň\n"
            "Sparta Praha  Baník Ostrava\n"
            "1.55 1.90\n"
            "4.20 3.50\n"
            "5.80 3.80\n"
        )
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, ZAPASY_PRO_FOTKU)
        self.assertEqual(
            parovane[("SK Slavia Praha", "AC Sparta Praha")]["kurzy"],
            (1.55, 4.20, 5.80),
        )
        self.assertEqual(
            parovane[("FC Viktoria Plzeň", "FC Baník Ostrava")]["kurzy"],
            (1.90, 3.50, 3.80),
        )

    def test_svisle_1x2_s_nazvem_tymu_u_kurzu(self):
        text = (
            "1 SK Slavia Praha 1.55\n"
            "X 4.20\n"
            "2 AC Sparta Praha 5.80\n"
        )
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu(text, ZAPASY_PRO_FOTKU)
        self.assertEqual(len(nabidka), 1)
        self.assertEqual(nabidka[0]["kurzy"], (1.55, 4.20, 5.80))
        self.assertEqual(nabidka[0]["domaci_surove"], "SK Slavia Praha")
        self.assertEqual(nabidka[0]["hoste_surove"], "AC Sparta Praha")

    def test_boxy_tri_kurzy_pod_sebou(self):
        slova = [
            {"text": "Slavia", "left": 10, "top": 10, "width": 80, "height": 16},
            {"text": "Sparta", "left": 10, "top": 40, "width": 80, "height": 16},
            {"text": "1.55", "left": 200, "top": 10, "width": 40, "height": 16},
            {"text": "4.20", "left": 200, "top": 40, "width": 40, "height": 16},
            {"text": "5.80", "left": 200, "top": 70, "width": 40, "height": 16},
        ]
        nabidka = kurz_obrazky.parsuj_nabidku_z_boxu(slova, ZAPASY_PRO_FOTKU)
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, ZAPASY_PRO_FOTKU)
        self.assertEqual(
            parovane[("SK Slavia Praha", "AC Sparta Praha")]["kurzy"],
            (1.55, 4.20, 5.80),
        )

    def test_nacti_a_uloz_pres_falesne_ocr(self):
        text = "Slavia Praha - Sparta Praha 1.70 3.80 4.90"
        docasny = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        docasny.close()
        try:
            shrnuti = kurz_obrazky.nacti_a_uloz(
                6,
                ZAPASY_PRO_FOTKU,
                [_png_bajty()],
                cesta=docasny.name,
                ocr=lambda _obrazek: text,
            )
            self.assertEqual(shrnuti["ulozeno"], 1)
            self.assertEqual(shrnuti["zdroj"], "screenshot")
            ulozene = kurzy.nacti_kurzy(docasny.name)
            self.assertEqual(
                ulozene[(6, "SK Slavia Praha", "AC Sparta Praha")],
                (1.70, 3.80, 4.90),
            )
            self.assertIn("uloženo", kurz_obrazky.popis_vysledku(shrnuti).lower())
        finally:
            os.unlink(docasny.name)

    def test_prazdny_text_neulozi_nic(self):
        nabidka = kurz_obrazky.parsuj_nabidku_z_textu("", ZAPASY_PRO_FOTKU)
        self.assertEqual(nabidka, [])
        self.assertIn("fotce", kurz_obrazky.popis_vysledku(
            {
                "ulozeno": 0,
                "nabidnuto": 0,
                "nesparovano": 0,
                "chyby": ["Na fotce se kurzy 1/X/2 nepodařilo přečíst."],
            }
        ))

    def test_vzhled_ma_zlomy_pro_telefon_i_tablet(self):
        self.assertIn("max-width: 768px", vzhled.STYLY)
        self.assertIn("max-width: 1100px", vzhled.STYLY)
        self.assertIn("stHorizontalBlock", vzhled.STYLY)
        self.assertIn("overflow-x", vzhled.STYLY)


HTML_ODLOZENEHO_ZAPASU = """
<li>
 <span class="game-container">
  <span class="team"><a href="/klub/20-bohemians-praha-1905"><img alt="Bohemians Praha 1905"/><b>BOH</b></a></span>
  <span class="score-container"><span class="info">odloženo</span></span>
  <span class="team"><a href="/klub/8-fk-mlada-boleslav"><img alt="FK Mladá Boleslav"/><b>MBL</b></a></span>
 </span>
</li>
<li>
 <span class="game-container">
  <span class="team"><a href="/klub/1"><img alt="FK Pardubice"/><b>FKP</b></a></span>
  <span class="score-container"><span class="info">zítra 17:00</span></span>
  <span class="team"><a href="/klub/2"><img alt="SK Artis Brno"/><b>ART</b></a></span>
 </span>
</li>
"""


HTML_SOUPISKY = """
<table class="table">
<tr><th>#</th><th>Hráč</th><th>Po</th><th>Národnost</th><th>Narozen</th><th>Výška</th><th>Váha</th><th>Z</th><th>G</th></tr>
<tr><td>44</td><td><a href="/hrac/4201-jakub-surovcik">Jakub Surovčík</a></td><td>B</td><td></td><td></td><td></td><td></td><td>5</td><td>-</td></tr>
<tr><td>47</td><td><a href="/hrac/5159-krisztian-hegyi">Krisztián Hegyi</a></td><td>B</td><td></td><td></td><td></td><td></td><td>0</td><td>-</td></tr>
<tr><td>10</td><td><a href="/hrac/3885-adam-karabec">Adam Karabec</a></td><td>Z</td><td></td><td></td><td></td><td></td><td>5</td><td>1</td></tr>
<tr><td>-</td><td><a href="/hrac/4448-veljko-birmancevic">Veljko Birmančević</a></td><td>Z</td><td></td><td></td><td></td><td></td><td>0</td><td>0</td></tr>
</table>
"""

HTML_SESTAVY_JEDNOHO_TYMU = """
<div class="roster-container home">
<table class="table border-bottom">
<tr><th></th><th>#</th><th>P</th><th>Jméno</th><th>G</th></tr>
<tr><td></td><td>1</td><td>B</td><td><a href="/hrac/100-a-hrdina">A. Hrdina</a></td><td></td></tr>
<tr><td></td><td>26</td><td>O</td><td><a href="/hrac/101-f-vedral">F. Vedral</a></td><td></td></tr>
<tr><td colspan="5"></td></tr>
<tr><td></td><td>78</td><td>B</td><td><a href="/hrac/102-o-prodelal">O. Prodělal</a></td><td></td></tr>
</table>
</div>
<div class="roster-container away">
<table class="table border-bottom">
<tr><th></th><th>#</th><th>P</th><th>Jméno</th><th>G</th></tr>
<tr><td colspan="5">Sestava prozatím není k dispozici</td></tr>
</table>
</div>
"""

HTML_SESTAVY = """
<table class="table border-bottom">
<tr><th></th><th>#</th><th>P</th><th>Jméno</th><th>G</th></tr>
<tr><td></td><td>44</td><td>B</td><td><a href="/hrac/4201-jakub-surovcik">J. Surovčík</a></td><td></td></tr>
<tr><td></td><td>10</td><td>Z</td><td><a href="/hrac/3885-adam-karabec">A. Karabec</a></td><td></td></tr>
<tr><td colspan="5"></td></tr>
<tr><td></td><td>47</td><td>B</td><td><a href="/hrac/5159-krisztian-hegyi">K. Hegyi</a></td><td></td></tr>
</table>
<table class="table border-bottom">
<tr><th></th><th>#</th><th>P</th><th>Jméno</th><th>G</th></tr>
<tr><td></td><td>1</td><td>B</td><td><a href="/hrac/1-host">H. Gólman</a></td><td></td></tr>
<tr><td colspan="5"></td></tr>
<tr><td></td><td>12</td><td>B</td><td><a href="/hrac/2-nahradnik">N. Náhradník</a></td><td></td></tr>
</table>
"""

HTML_KLUBU = """
<a href="/klub/2-ac-sparta-praha">AC Sparta Praha</a>
<a href="/klub/5-sk-slavia-praha">SK Slavia Praha</a>
<a href="/klub/16-1-fc-slovacko">1.FC Slovácko</a>
"""


class TestSestavy(unittest.TestCase):
    def test_parsuj_soupisku(self):
        hraci = sestavy.parsuj_soupisku(HTML_SOUPISKY)
        self.assertEqual(len(hraci), 4)
        surovcik = hraci[0]
        self.assertEqual(surovcik["id"], "4201")
        self.assertEqual(surovcik["jmeno"], "Jakub Surovčík")
        self.assertEqual(surovcik["pozice"], "B")
        self.assertEqual(surovcik["zapasy"], 5)

    def test_parsuj_kluby_sjednoti_slovacko(self):
        kluby = sestavy.parsuj_kluby(HTML_KLUBU)
        nazvy = {k["nazev"] for k in kluby}
        self.assertIn("AC Sparta Praha", nazvy)
        self.assertIn("1. FC Slovácko", nazvy)
        slugs = {k["slug"] for k in kluby}
        self.assertIn("2-ac-sparta-praha", slugs)

    def test_parsuj_sestavu_oddeli_lavicku(self):
        sestava = sestavy.parsuj_sestavu_zapasu(HTML_SESTAVY)
        self.assertIsNotNone(sestava)
        self.assertEqual([h["id"] for h in sestava["domaci"]["zaklad"]], ["4201", "3885"])
        self.assertEqual([h["id"] for h in sestava["domaci"]["nahradnici"]], ["5159"])
        self.assertEqual(len(sestava["hoste"]["zaklad"]), 1)

    def test_parsuj_sestavu_i_kdyz_ma_jedenactku_jen_domaci(self):
        sestava = sestavy.parsuj_sestavu_zapasu(HTML_SESTAVY_JEDNOHO_TYMU)
        self.assertIsNotNone(sestava)
        self.assertEqual(
            [h["jmeno"] for h in sestava["domaci"]["zaklad"]],
            ["A. Hrdina", "F. Vedral"],
        )
        self.assertEqual(
            [h["jmeno"] for h in sestava["domaci"]["nahradnici"]],
            ["O. Prodělal"],
        )
        self.assertEqual(sestava["hoste"]["zaklad"], [])

    def test_stoji_za_stazeni_sestavy_jen_kolem_vykopu(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        praha = ZoneInfo("Europe/Prague")
        vykop = datetime(2026, 8, 29, 17, 0, tzinfo=praha)
        zapas = {
            "stav": "🕒 Nadcházející",
            "cas": vykop,
            "skore": "-",
        }
        self.assertTrue(
            sestavy.stoji_za_stazeni_sestavy(zapas, ted=vykop - timedelta(hours=1))
        )
        self.assertFalse(
            sestavy.stoji_za_stazeni_sestavy(zapas, ted=vykop - timedelta(hours=6))
        )
        odlozeny = dict(zapas, stav="🔴 Odloženo")
        self.assertFalse(sestavy.stoji_za_stazeni_sestavy(odlozeny, ted=vykop))

    def test_id_mimo_sestavu(self):
        kadr = sestavy.parsuj_soupisku(HTML_SOUPISKY)
        sestava = sestavy.parsuj_sestavu_zapasu(HTML_SESTAVY)
        mimo = sestavy.id_mimo_sestavu(kadr, sestava["domaci"])
        self.assertEqual(mimo, ["4448"])

    def test_pokuta_gólmana_je_vyssi_nez_lavicky(self):
        kadr = sestavy.parsuj_soupisku(HTML_SOUPISKY)
        pokuta_gólman = modely.pokuta_z_absenci(kadr, ["4201"])
        pokuta_nahradnik = modely.pokuta_z_absenci(kadr, ["5159"])
        pokuta_opora = modely.pokuta_z_absenci(kadr, ["3885"])
        self.assertGreater(pokuta_gólman, pokuta_nahradnik)
        self.assertGreater(pokuta_opora, pokuta_nahradnik)
        self.assertLess(pokuta_gólman, modely.MAX_POKUTA_ZRANENI)

    def test_uprav_silu_bere_cislo(self):
        sila, dopad = modely.uprav_silu(50.0, "Bez pohárů", 0.10)
        self.assertAlmostEqual(sila, 45.0)
        self.assertAlmostEqual(dopad, 10.0)

    def test_uprav_silu_stary_popisek_funguje(self):
        sila_komplet, _ = modely.uprav_silu(50.0, "Bez pohárů", "Kompletní kádr")
        sila_opora, _ = modely.uprav_silu(50.0, "Bez pohárů", "Chybí 1 opora")
        self.assertGreater(sila_komplet, sila_opora)

    def test_absence_se_ulozi_a_nactou(self):
        with tempfile.TemporaryDirectory() as slozka:
            cesta = os.path.join(slozka, "absence.csv")
            kadr = sestavy.parsuj_soupisku(HTML_SOUPISKY)
            sestavy.uloz_absence_tymu(
                "AC Sparta Praha", ["4201", "4448"], kadr, cesta=cesta
            )
            nactene = sestavy.nacti_absence(cesta)
            self.assertEqual(nactene["AC Sparta Praha"], ["4201", "4448"])
            sestavy.uloz_absence_tymu("AC Sparta Praha", ["3885"], kadr, cesta=cesta)
            nactene = sestavy.nacti_absence(cesta)
            self.assertEqual(nactene["AC Sparta Praha"], ["3885"])

    def test_nazev_tymu(self):
        self.assertEqual(data.nazev_tymu("1.FC Slovácko"), "1. FC Slovácko")
        self.assertEqual(data.nazev_tymu("SK Slavia Praha"), "SK Slavia Praha")


TIPSPORT_NABIDKA = {
    "matches": [
        {
            "name": "Slavia Praha - Sparta Praha",
            "opp1": "Slavia Praha",
            "opp2": "Sparta Praha",
            "odds": [
                {"opportunityName": "1", "odd": 2.05},
                {"opportunityName": "X", "odd": 3.40},
                {"opportunityName": "2", "odd": 3.55},
            ],
        },
        {
            "name": "Baník Ostrava - Sigma Olomouc",
            "odds": [
                {"opportunityName": "1", "currentOdd": 1.95},
                {"opportunityName": "Remíza", "currentOdd": 3.50},
                {"opportunityName": "2", "currentOdd": 3.80},
            ],
        },
    ]
}

API_FOOTBALL_ODDS = [
    {
        "fixture": {"id": 111},
        "bookmakers": [
            {
                "id": 6,
                "name": "Bwin",
                "bets": [
                    {
                        "id": 1,
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": "2.20"},
                            {"value": "Draw", "odd": "3.20"},
                            {"value": "Away", "odd": "3.40"},
                        ],
                    }
                ],
            },
            {
                "id": 8,
                "name": "Bet365",
                "bets": [
                    {
                        "id": 1,
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": "2.10"},
                            {"value": "Draw", "odd": "3.30"},
                            {"value": "Away", "odd": "3.50"},
                        ],
                    }
                ],
            },
        ],
    }
]

API_FOOTBALL_FIXTURES = [
    {
        "fixture": {"id": 111},
        "teams": {
            "home": {"name": "Sparta Prague"},
            "away": {"name": "Slavia Prague"},
        },
    }
]


class TestKurzZdroje(unittest.TestCase):
    def test_kanonicky_tym_slavi_tipsport_i_anglictinu(self):
        znami = [
            "SK Slavia Praha",
            "AC Sparta Praha",
            "FC Baník Ostrava",
            "SK Sigma Olomouc",
            "FC Viktoria Plzeň",
            "SK Artis Brno",
            "FC Zbrojovka Brno",
        ]
        self.assertEqual(
            kurz_zdroje.kanonicky_tym("Slavia Praha", znami), "SK Slavia Praha"
        )
        self.assertEqual(
            kurz_zdroje.kanonicky_tym("Sparta Prague", znami), "AC Sparta Praha"
        )
        self.assertEqual(kurz_zdroje.kanonicky_tym("Plzen", znami), "FC Viktoria Plzeň")
        self.assertEqual(
            kurz_zdroje.kanonicky_tym("Banik Ostrava", znami), "FC Baník Ostrava"
        )
        self.assertIsNone(kurz_zdroje.kanonicky_tym("Brno", znami))

    def test_tipsport_json_da_1x2(self):
        nabidka = kurz_zdroje.zapasy_z_nabidky(TIPSPORT_NABIDKA, "tipsport", "Tipsport")
        self.assertEqual(len(nabidka), 2)
        slavia = nabidka[0]
        self.assertEqual(slavia["kurzy"], (2.05, 3.40, 3.55))
        self.assertEqual(slavia["sazkovka"], "Tipsport")

    def test_sparovani_s_kanonickymi_nazvy(self):
        nabidka = kurz_zdroje.zapasy_z_nabidky(TIPSPORT_NABIDKA, "tipsport", "Tipsport")
        zapasy = [
            zapas("SK Slavia Praha", "AC Sparta Praha"),
            zapas("FC Baník Ostrava", "SK Sigma Olomouc"),
            zapas("FK Teplice", "FK Jablonec"),
        ]
        parovane = kurz_zdroje.sparuj_nabidku(nabidka, zapasy)
        self.assertEqual(len(parovane), 2)
        self.assertEqual(
            parovane[("SK Slavia Praha", "AC Sparta Praha")]["kurzy"],
            (2.05, 3.40, 3.55),
        )
        self.assertNotIn(("FK Teplice", "FK Jablonec"), parovane)

    def test_api_football_bere_bet365_pred_bwin(self):
        nabidka = kurz_zdroje.zapasy_z_api_football(
            API_FOOTBALL_ODDS, API_FOOTBALL_FIXTURES
        )
        self.assertEqual(len(nabidka), 1)
        self.assertEqual(nabidka[0]["sazkovka"], "Bet365")
        self.assertEqual(nabidka[0]["kurzy"], (2.10, 3.30, 3.50))
        self.assertEqual(nabidka[0]["domaci_surove"], "Sparta Prague")

    def test_nacti_a_uloz_z_tipsportu(self):
        class _Odpoved:
            status_code = 200

            def json(self):
                return TIPSPORT_NABIDKA

        class _Session:
            def get(self, *args, **kwargs):
                return _Odpoved()

            def post(self, *args, **kwargs):
                raise AssertionError("POST se nemá volat, GET už kurzy má")

        docasny = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        docasny.close()
        try:
            zapasy = [zapas("SK Slavia Praha", "AC Sparta Praha")]
            shrnuti = kurz_zdroje.nacti_a_uloz(
                6, zapasy, session=_Session(), cesta=docasny.name
            )
            self.assertEqual(shrnuti["ulozeno"], 1)
            self.assertEqual(shrnuti["zdroj"], "tipsport")
            ulozene = kurzy.nacti_kurzy(docasny.name)
            self.assertEqual(
                ulozene[(6, "SK Slavia Praha", "AC Sparta Praha")],
                (2.05, 3.40, 3.55),
            )
        finally:
            os.unlink(docasny.name)

    def test_bez_klice_api_football_rekne_proc(self):
        nabidka, chyba = kurz_zdroje.stahni_api_football(klic="")
        self.assertEqual(nabidka, [])
        self.assertIn("API_FOOTBALL_KEY", chyba)

    def test_popis_vysledku_odlisi_api_od_tipsportu(self):
        text = kurz_zdroje.popis_vysledku(
            {
                "ulozeno": 8,
                "nabidnuto": 8,
                "nesparovano": 0,
                "zdroj": "api-football",
                "sazkovka": "Bet365",
                "chyby": ["Tipsport z tohohle serveru nepustil (HTTP 403, Cloudflare)."],
            }
        )
        self.assertIn("Bet365", text)
        self.assertIn("nejsou kurzy Tipsportu", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
