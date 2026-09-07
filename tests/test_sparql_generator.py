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
                patch.object(sparql_generator, "_generate", return_value="SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"), \
                patch.object(sparql_generator, "postprocess", side_effect=lambda q, mode: q), \
                patch.object(sparql_generator, "validate_semantics", return_value=[]), \
                patch.object(sparql_generator, "validate_completeness", return_value=[]):
            query = sparql_generator.generate(
                "Monumenten in Utrecht", "lijst", disambiguation={"Utrecht": "gemeente"}
            )

        self.assertIn("Rijksmonument", query)


if __name__ == "__main__":
    unittest.main()
