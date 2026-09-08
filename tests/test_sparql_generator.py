import os
import unittest
from unittest.mock import patch

os.environ.setdefault("LLM_PROVIDER", "ollama")

from sparql import sparql_generator
from sparql.semantic_resolver import AmbiguousTerm, ResolutionResult, ResolvedTerm


UTRECHT_AMBIGUOUS = ResolutionResult(
    ambiguous=(
        AmbiguousTerm(
            label="Utrecht",
            candidates=(
                ResolvedTerm("gemeente", "Utrecht", "urn:gemeente-utrecht"),
                ResolvedTerm("provincie", "Utrecht", "urn:provincie-utrecht"),
            ),
        ),
    )
)

UTRECHT_GEMEENTE_RESOLVED = ResolutionResult(
    resolved=(ResolvedTerm("gemeente", "Utrecht", "urn:gemeente-utrecht"),)
)


class ClarificationNeededTests(unittest.TestCase):
    def test_ambiguous_resolution_raises_clarification_needed_before_llm_call(self):
        with patch.object(sparql_generator, "resolve_question", return_value=UTRECHT_AMBIGUOUS), \
                patch.object(sparql_generator, "_generate") as mock_generate:
            with self.assertRaises(sparql_generator.ClarificationNeeded) as ctx:
                sparql_generator.generate("Monumenten in Utrecht", "lijst")

        mock_generate.assert_not_called()
        self.assertEqual(ctx.exception.ambiguous, UTRECHT_AMBIGUOUS.ambiguous)

    def test_disambiguation_lets_generation_proceed(self):
        with patch.object(sparql_generator, "resolve_question", return_value=UTRECHT_GEMEENTE_RESOLVED), \
                patch.object(sparql_generator, "_generate", return_value="PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#> SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            result = sparql_generator.generate(
                "Monumenten in Utrecht", "lijst", disambiguation={"Utrecht": "gemeente"}
            )

        self.assertIn("Rijksmonument", result.query)
        self.assertEqual(result.caveats, ())


class AnswerabilityLimitationTests(unittest.TestCase):
    QUESTION = "Welke rijksmonumenten liggen bij een begraafplaats?"

    def test_limitation_without_choice_raises_before_llm_call(self):
        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate") as mock_generate:
            with self.assertRaises(sparql_generator.AnswerabilityLimitationNeeded) as ctx:
                sparql_generator.generate(self.QUESTION, "lijst")

        mock_generate.assert_not_called()
        self.assertEqual(len(ctx.exception.limitation.partial_options), 2)

    def test_valid_limitation_choice_proceeds_with_hint_and_caveat(self):
        captured_prompts = []

        def fake_generate(prompt_input, system_prompt):
            captured_prompts.append(prompt_input)
            return "PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#> SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"

        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate", side_effect=fake_generate), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            result = sparql_generator.generate(
                self.QUESTION, "lijst", limitation_choice="functie_begraafplaats"
            )

        self.assertIn("Rijksmonument", result.query)
        self.assertEqual(len(result.caveats), 1)
        self.assertIn("geregistreerd staan", result.caveats[0])
        self.assertTrue(
            any("heeftFunctieNaam" in prompt for prompt in captured_prompts),
            "de prompt_hint van de gekozen optie moet in de LLM-prompt terechtkomen",
        )

    def test_unrelated_question_never_raises_limitation(self):
        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate", return_value="PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#> SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            result = sparql_generator.generate("Welke rijksmonumenten zijn een begraafplaats?", "lijst")

        self.assertEqual(result.caveats, ())


class PropertyChoiceCaveatTests(unittest.TestCase):
    def test_vague_word_question_adds_property_choice_caveat(self):
        query_with_functie = (
            "PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#> "
            "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument . "
            "?rm ceo:heeftOorspronkelijkeFunctie ?f }"
        )
        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate", return_value=query_with_functie), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            result = sparql_generator.generate("Wat voor soort monument is dit?", "lijst")

        self.assertEqual(len(result.caveats), 1)
        self.assertIn("functie", result.caveats[0])

    def test_limitation_caveat_and_property_choice_caveat_can_coexist(self):
        # Randgeval: een vraag die zowel de begraafplaats-beperking als een
        # vage-woord-frase raakt -- bevestigt dat caveats een tuple is die
        # meerdere onafhankelijke bronnen tegelijk kan dragen.
        question = "Wat voor soort monument ligt er bij een begraafplaats?"
        query_with_functie = (
            "PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#> "
            "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument . "
            "?rm ceo:heeftOorspronkelijkeFunctie ?f }"
        )
        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate", return_value=query_with_functie), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            result = sparql_generator.generate(
                question, "lijst", limitation_choice="functie_begraafplaats"
            )

        self.assertEqual(len(result.caveats), 2)
        self.assertTrue(any("geregistreerd staan" in c for c in result.caveats))
        self.assertTrue(any("functie" in c for c in result.caveats))


class SyntaxValidationTests(unittest.TestCase):
    def test_still_invalid_after_retry_raises_syntax_invalid(self):
        # De "correctie"-aanroep levert hier weer een query zonder PREFIX op
        # -- syntactisch ongeldig, en dat blijft zo na de ene herkansing.
        broken_query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"

        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate", return_value=broken_query), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            with self.assertRaises(sparql_generator.SparqlSyntaxInvalid):
                sparql_generator.generate("Hoeveel rijksmonumenten zijn er?", "telling")

    def test_valid_query_never_raises_syntax_invalid(self):
        valid_query = (
            "PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#> "
            "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"
        )

        with patch.object(sparql_generator, "resolve_question", return_value=ResolutionResult()), \
                patch.object(sparql_generator, "_generate", return_value=valid_query), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            result = sparql_generator.generate("Hoeveel rijksmonumenten zijn er?", "telling")

        self.assertEqual(result.query, valid_query)


if __name__ == "__main__":
    unittest.main()
