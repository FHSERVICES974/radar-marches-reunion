"""Tests unitaires pour _parse_deadline_date (server.py).

Cas couverts :
  - Date numérique simple
  - Date littérale avec et sans année
  - Plage de dates (tiret ou « au »)  → retourne la DERNIÈRE date
  - Texte éditorial avec numéro d'édition avant la vraie date
  - Texte vague sans date explicite   → None
  - Chaîne vide / None                → None
"""

import datetime
import sys
import types
import unittest

# ── Isolation : on importe uniquement _parse_deadline_date et _FR_MONTH_NUM ──
# On charge server.py dans un module séparé pour éviter les effets de bord
# (connexion BDD, threads, etc.).
import importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("_srv", pathlib.Path("server.py"))
_mod  = types.ModuleType("_srv")
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

parse = _mod._parse_deadline_date


REF = datetime.date(2026, 1, 15)   # date de référence fixe pour les tests sans année


class TestNumericFormat(unittest.TestCase):
    def test_slash(self):
        self.assertEqual(parse("Clôture : 15/08/2026"), datetime.date(2026, 8, 15))

    def test_dot(self):
        self.assertEqual(parse("avant le 31.12.2025"), datetime.date(2025, 12, 31))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(parse("32/01/2026"))


class TestLiteralFormat(unittest.TestCase):
    def test_with_year(self):
        self.assertEqual(parse("avant le 31 août 2026"), datetime.date(2026, 8, 31))

    def test_1er(self):
        self.assertEqual(parse("1er mai 2027"), datetime.date(2027, 5, 1))

    def test_abbrev_month(self):
        self.assertEqual(parse("avant le 10 sept. 2026"), datetime.date(2026, 9, 10))

    def test_without_year_uses_ref(self):
        # "20 mars" avec ref=2026-01-15 → 2026-03-20 (dans le futur)
        self.assertEqual(parse("20 mars", ref=REF), datetime.date(2026, 3, 20))

    def test_without_year_past_rolls_forward(self):
        # "5 janvier" avec ref=2026-05-01 → 5 janv 2026 est 115 jours avant ref (>90j) → +1 an
        result = parse("5 janvier", ref=datetime.date(2026, 5, 1))
        self.assertEqual(result, datetime.date(2027, 1, 5))


class TestRanges(unittest.TestCase):
    """Le cas central de la tâche : plages de dates ambiguës."""

    def test_tiret_same_month(self):
        # "2 juin–8 août 2026" → deadline = 8 août 2026
        self.assertEqual(parse("éd. 2025 : 2 juin–8 août 2026"), datetime.date(2026, 8, 8))

    def test_tiret_no_year(self):
        # "2 juin – 8 août" sans année, ref=janv 2026 → 8 août 2026
        self.assertEqual(parse("2 juin – 8 août", ref=REF), datetime.date(2026, 8, 8))

    def test_du_au(self):
        # "du 3 mars au 15 septembre 2026" → 15 septembre 2026
        self.assertEqual(parse("du 3 mars au 15 septembre 2026"), datetime.date(2026, 9, 15))

    def test_two_numeric_dates(self):
        # Deux dates numériques : prend la dernière
        self.assertEqual(parse("01/06/2026 au 30/09/2026"), datetime.date(2026, 9, 30))

    def test_editorial_number_before_date(self):
        # "éd. 2025 : 15 septembre 2026" — le « 2025 » ne doit pas perturber
        self.assertEqual(parse("éd. 2025 : 15 septembre 2026"), datetime.date(2026, 9, 15))


class TestAmbiguousTextReturnsNone(unittest.TestCase):
    """Textes vagues sans date explicite → None."""

    def test_season(self):
        self.assertIsNone(parse("appel attendu à l'automne 2026"))

    def test_year_only(self):
        self.assertIsNone(parse("résultats courant 2027"))

    def test_empty(self):
        self.assertIsNone(parse(""))

    def test_none_input(self):
        self.assertIsNone(parse(None))

    def test_plain_text(self):
        self.assertIsNone(parse("Voir le site de l'organisateur"))

    def test_permanent(self):
        self.assertIsNone(parse("Appel permanent"))


if __name__ == "__main__":
    unittest.main()
