import unittest

from sparql.answerability import describe_limitation, detect_limitation


class DetectLimitationTests(unittest.TestCase):
    def test_fires_on_begraafplaats_with_nabijheid_term(self):
        limitation = detect_limitation("Welke rijksmonumenten liggen bij een begraafplaats?")
        self.assertIsNotNone(limitation)
        self.assertEqual(len(limitation.partial_options), 2)
        ids = {opt.id for opt in limitation.partial_options}
        self.assertEqual(ids, {"functie_begraafplaats", "complex_onderdeel"})

    def test_fires_on_kerkhof_nabij(self):
        limitation = detect_limitation("Welke rijksmonumenten staan nabij een kerkhof?")
        self.assertIsNotNone(limitation)

    def test_does_not_fire_without_nabijheid_term(self):
        # "is een begraafplaats" is eenduidig beantwoordbaar via het functiepad,
        # geen reden om de gebruiker lastig te vallen.
        self.assertIsNone(detect_limitation("Welke rijksmonumenten zijn een begraafplaats?"))

    def test_does_not_fire_on_unrelated_question(self):
        self.assertIsNone(detect_limitation("Hoeveel rijksmonumenten zijn er in Utrecht?"))


class DescribeLimitationTests(unittest.TestCase):
    def test_shape(self):
        limitation = detect_limitation("Welke rijksmonumenten liggen bij een begraafplaats?")
        described = describe_limitation(limitation)

        self.assertEqual(described["type"], "answerability_limitation")
        self.assertIn("message", described)
        self.assertEqual(len(described["options"]), 2)
        for option in described["options"]:
            self.assertIn("id", option)
            self.assertIn("label", option)
            self.assertIn("caveat", option)


if __name__ == "__main__":
    unittest.main()
