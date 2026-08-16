"""Testy predikčních modelů a logu predikcí.

Spuštění:  python test_modely.py
"""

import os
import tempfile
import unittest

import data
import hlaseni
import modely
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
