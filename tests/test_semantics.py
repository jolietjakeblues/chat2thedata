import os
import unittest
from unittest.mock import patch

os.environ.setdefault("LLM_PROVIDER", "ollama")

from sparql.executor import _enrich_requested_fields, _enrichment_query
from sparql.semantic_resolver import (
    OWMS_GEMEENTE_CLASS, OWMS_PROVINCIE_CLASS, ResolvedTerm,
    _find_longest, resolve_question,
)
from sparql.semantic_validator import requested_limit, validate_semantics


AMSTERDAM = ResolvedTerm(
    kind="gemeente",
    label="Amsterdam",
    uri="http://standaarden.overheid.nl/owms/terms/Amsterdam",
)


class ResolverTests(unittest.TestCase):
    def test_longest_owms_label_is_resolved(self):
        terms = (
            ("Bergen", "urn:bergen"),
            ("Bergen op Zoom", "urn:bergen-op-zoom"),
        )
        self.assertEqual(
            _find_longest("Monumenten in Bergen op Zoom", terms),
            ("Bergen op Zoom", "urn:bergen-op-zoom"),
        )

    def test_label_before_punctuation_is_resolved(self):
        terms = (("Amsterdam", "urn:amsterdam"),)
        self.assertEqual(
            _find_longest("Welke monumenten staan in Amsterdam?", terms),
            ("Amsterdam", "urn:amsterdam"),
        )

    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_province_is_detected_without_province_word(self, load_terms):
        def terms(class_uri):
            return (
                (("Arnhem", "urn:arnhem"),)
                if class_uri == OWMS_GEMEENTE_CLASS
                else (("Gelderland", "urn:gelderland"),)
            )
        load_terms.side_effect = terms
        result = resolve_question("Welke kastelen staan in Gelderland?")
        self.assertEqual(
            result.resolved,
            (ResolvedTerm("provincie", "Gelderland", "urn:gelderland"),),
        )
        self.assertEqual(result.ambiguous, ())

    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_ambiguous_place_is_flagged_not_silently_resolved(self, load_terms):
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        result = resolve_question("Monumenten in Utrecht")

        self.assertEqual(result.resolved, ())
        self.assertEqual(len(result.ambiguous), 1)
        self.assertEqual(result.ambiguous[0].label, "Utrecht")
        self.assertEqual(
            {candidate.kind for candidate in result.ambiguous[0].candidates},
            {"gemeente", "provincie"},
        )

    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_ambiguity_resolved_via_disambiguation_override(self, load_terms):
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        result = resolve_question(
            "Monumenten in Utrecht", disambiguation={"Utrecht": "gemeente"}
        )

        self.assertEqual(result.ambiguous, ())
        self.assertEqual(
            result.resolved,
            (ResolvedTerm("gemeente", "Utrecht", "urn:utrecht"),),
        )

    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_explicit_keyword_is_never_flagged_as_ambiguous(self, load_terms):
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        result = resolve_question("Monumenten in de gemeente Utrecht")

        self.assertEqual(result.ambiguous, ())
        self.assertEqual(
            result.resolved,
            (ResolvedTerm("gemeente", "Utrecht", "urn:utrecht"),),
        )


class SemanticValidationTests(unittest.TestCase):
    def test_requested_limit_is_detected(self):
        self.assertEqual(requested_limit("Geef 5 monumenten"), 5)

    def test_brk_municipality_filter_is_rejected(self):
        query = """
SELECT DISTINCT ?rm ?nummer ?naam ?adres WHERE {
  ?rm ceo:heeftBasisregistratieRelatie ?relatie .
  ?relatie ceo:heeftBRKRelatie ?brk .
  ?brk ceo:gemeentenaam ?gemeente .
}
LIMIT 5
"""
        errors = validate_semantics(
            "Geef 5 rijksmonumenten in Amsterdam met naam en adres",
            query,
            [AMSTERDAM],
        )
        self.assertTrue(any("heeftGemeente" in error for error in errors))
        self.assertTrue(any("BRKRelatie" in error for error in errors))
        self.assertTrue(any("Projecteer ?naam" in error for error in errors))

    def test_uri_first_bounded_query_is_accepted(self):
        query = """
SELECT DISTINCT ?rm ?nummer WHERE {
  ?rm ceo:heeftBasisregistratieRelatie ?relatie .
  ?relatie ceo:heeftGemeente
    <http://standaarden.overheid.nl/owms/terms/Amsterdam> .
  FILTER EXISTS { ?rm ceo:heeftNaam ?n . ?n ceo:naam ?naamWaarde . }
  FILTER EXISTS {
    ?rm ceo:heeftBasisregistratieRelatie ?a .
    ?a ceo:heeftBAGRelatie ?bag .
    ?bag ceo:volledigAdres ?adresWaarde .
  }
}
LIMIT 5
"""
        self.assertEqual(
            validate_semantics(
                "Geef 5 rijksmonumenten in Amsterdam met naam en adres",
                query,
                [AMSTERDAM],
            ),
            [],
        )


class EnrichmentTests(unittest.TestCase):
    def test_name_and_address_are_merged_by_uri(self):
        uri = "https://linkeddata.cultureelerfgoed.nl/cho-kennis/id/rijksmonument/10804"
        data = {
            "head": {"vars": ["rm", "nummer"]},
            "results": {
                "bindings": [
                    {
                        "rm": {"type": "uri", "value": uri},
                        "nummer": {"type": "literal", "value": "3385"},
                    }
                ]
            },
        }
        side_effect = [
            {uri: {"type": "literal", "value": "Archangel"}},
            {uri: {"type": "literal", "value": "Leidsegracht 88 A"}},
        ]
        with patch("sparql.executor._fetch_enrichment", side_effect=side_effect):
            result = _enrich_requested_fields(data, "Geef naam en adres")

        row = result["results"]["bindings"][0]
        self.assertEqual(row["naam"]["value"], "Archangel")
        self.assertEqual(row["adres"]["value"], "Leidsegracht 88 A")
        self.assertIn("naam", result["head"]["vars"])
        self.assertIn("adres", result["head"]["vars"])

    def test_enrichment_query_uses_only_values_set(self):
        uri = "https://linkeddata.cultureelerfgoed.nl/cho-kennis/id/rijksmonument/10804"
        query = _enrichment_query([uri], "naam")
        self.assertIn(f"<{uri}>", query)
        self.assertIn("GROUP BY ?rm", query)
        self.assertNotIn("heeftBAGRelatie", query)


if __name__ == "__main__":
    unittest.main()
