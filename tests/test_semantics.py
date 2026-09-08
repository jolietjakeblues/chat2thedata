import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("LLM_PROVIDER", "ollama")

from sparql.executor import _enrich_requested_fields, _enrichment_query
from sparql.semantic_resolver import (
    OWMS_GEMEENTE_CLASS, OWMS_PROVINCIE_CLASS, ResolvedTerm,
    _find_longest, build_semantic_context, resolve_question,
)
from sparql.semantic_validator import (
    describe_property_choice, requested_limit, validate_semantics,
)


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

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_province_is_detected_without_province_word(self, load_terms, load_woonplaats):
        def terms(class_uri):
            return (
                (("Arnhem", "urn:arnhem"),)
                if class_uri == OWMS_GEMEENTE_CLASS
                else (("Gelderland", "urn:gelderland"),)
            )
        load_terms.side_effect = terms
        load_woonplaats.return_value = ()
        result = resolve_question("Welke kastelen staan in Gelderland?")
        self.assertEqual(
            result.resolved,
            (ResolvedTerm("provincie", "Gelderland", "urn:gelderland"),),
        )
        self.assertEqual(result.ambiguous, ())

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_ambiguous_place_is_flagged_not_silently_resolved(self, load_terms, load_woonplaats):
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        load_woonplaats.return_value = ()
        result = resolve_question("Monumenten in Utrecht")

        self.assertEqual(result.resolved, ())
        self.assertEqual(len(result.ambiguous), 1)
        self.assertEqual(result.ambiguous[0].label, "Utrecht")
        self.assertEqual(
            {candidate.kind for candidate in result.ambiguous[0].candidates},
            {"gemeente", "provincie"},
        )

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_ambiguity_resolved_via_disambiguation_override(self, load_terms, load_woonplaats):
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        load_woonplaats.return_value = ()
        result = resolve_question(
            "Monumenten in Utrecht", disambiguation={"Utrecht": "gemeente"}
        )

        self.assertEqual(result.ambiguous, ())
        self.assertEqual(
            result.resolved,
            (ResolvedTerm("gemeente", "Utrecht", "urn:utrecht"),),
        )

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_explicit_keyword_is_never_flagged_as_ambiguous(self, load_terms, load_woonplaats):
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        load_woonplaats.return_value = ()
        result = resolve_question("Monumenten in de gemeente Utrecht")

        self.assertEqual(result.ambiguous, ())
        self.assertEqual(
            result.resolved,
            (ResolvedTerm("gemeente", "Utrecht", "urn:utrecht"),),
        )

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_three_way_tie_on_utrecht_is_flagged(self, load_terms, load_woonplaats):
        # "Utrecht" is tegelijk gemeentenaam, provincienaam en woonplaatsnaam.
        load_terms.return_value = (("Utrecht", "urn:utrecht"),)
        load_woonplaats.return_value = (("Utrecht", "Utrecht"),)
        result = resolve_question("Monumenten in Utrecht")

        self.assertEqual(result.resolved, ())
        self.assertEqual(len(result.ambiguous), 1)
        self.assertEqual(
            {candidate.kind for candidate in result.ambiguous[0].candidates},
            {"gemeente", "provincie", "plaats"},
        )

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_two_way_tie_plaats_versus_gemeente(self, load_terms, load_woonplaats):
        # "Zeist": wel gemeente en plaats, geen provincie -- de casus die dit
        # traject in gang zette.
        def terms(class_uri):
            return (("Zeist", "urn:gemeente-zeist"),) if class_uri == OWMS_GEMEENTE_CLASS else ()
        load_terms.side_effect = terms
        load_woonplaats.return_value = (("Zeist", "Zeist"),)
        result = resolve_question("Welke rijksmonumenten staan er in Zeist?")

        self.assertEqual(result.resolved, ())
        self.assertEqual(
            {candidate.kind for candidate in result.ambiguous[0].candidates},
            {"gemeente", "plaats"},
        )

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_explicit_woonplaats_keyword_resolves_without_ambiguity(self, load_terms, load_woonplaats):
        def terms(class_uri):
            return (("Zeist", "urn:gemeente-zeist"),) if class_uri == OWMS_GEMEENTE_CLASS else ()
        load_terms.side_effect = terms
        load_woonplaats.return_value = (("Zeist", "Zeist"),)
        result = resolve_question("Welke rijksmonumenten staan er in de woonplaats Zeist?")

        self.assertEqual(result.ambiguous, ())
        self.assertEqual(result.resolved, (ResolvedTerm("plaats", "Zeist", "Zeist"),))

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_bare_plaats_word_does_not_trigger_explicit_keyword(self, load_terms, load_woonplaats):
        # Regressietest tegen het substring-risico: "plaats" komt voor in
        # "vindplaats" en zou zonder woordgrens-bewuste check ten onrechte de
        # expliciete-keyword-tak triggeren.
        def terms(class_uri):
            return (("Zeist", "urn:gemeente-zeist"),) if class_uri == OWMS_GEMEENTE_CLASS else ()
        load_terms.side_effect = terms
        load_woonplaats.return_value = (("Zeist", "Zeist"),)
        result = resolve_question("Welke vindplaats in Zeist heeft de meeste vondsten?")

        # Geen expliciete "woonplaats" in de vraag -> normale tie-break, niet
        # automatisch kind="plaats".
        self.assertEqual(
            {candidate.kind for candidate in result.ambiguous[0].candidates},
            {"gemeente", "plaats"},
        )

    @patch("sparql.semantic_resolver._load_woonplaats_terms")
    @patch("sparql.semantic_resolver._load_owms_terms")
    def test_ambiguity_resolved_via_disambiguation_override_to_plaats(self, load_terms, load_woonplaats):
        def terms(class_uri):
            return (("Zeist", "urn:gemeente-zeist"),) if class_uri == OWMS_GEMEENTE_CLASS else ()
        load_terms.side_effect = terms
        load_woonplaats.return_value = (("Zeist", "Zeist"),)
        result = resolve_question(
            "Welke rijksmonumenten staan er in Zeist?", disambiguation={"Zeist": "plaats"}
        )

        self.assertEqual(result.ambiguous, ())
        self.assertEqual(result.resolved, (ResolvedTerm("plaats", "Zeist", "Zeist"),))


class LoadWoonplaatsTermsTests(unittest.TestCase):
    def test_parses_sparql_json_into_naam_naam_pairs(self):
        from sparql.semantic_resolver import _load_woonplaats_terms

        self.addCleanup(_load_woonplaats_terms.cache_clear)
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "results": {"bindings": [{"naam": {"value": "Zeist"}}, {"naam": {"value": "Utrecht"}}]}
        }
        with patch("sparql.semantic_resolver.requests.get", return_value=fake_response) as mock_get:
            terms = _load_woonplaats_terms()

        fake_response.raise_for_status.assert_called_once()
        self.assertEqual(terms, (("Zeist", "Zeist"), ("Utrecht", "Utrecht")))
        self.assertIn("woonplaatsnaam", mock_get.call_args.kwargs["params"]["query"])


class SemanticContextTests(unittest.TestCase):
    def test_plaats_term_uses_exact_match_instruction_not_uri(self):
        context = build_semantic_context((ResolvedTerm("plaats", "Zeist", "Zeist"),))
        self.assertIn("EXACTE match", context)
        self.assertIn("ceo:woonplaatsnaam", context)
        self.assertNotIn("<Zeist>", context)

    def test_gemeente_term_still_uses_uri_form(self):
        context = build_semantic_context(
            (ResolvedTerm("gemeente", "Amsterdam", "http://standaarden.overheid.nl/owms/terms/Amsterdam"),)
        )
        self.assertIn("<http://standaarden.overheid.nl/owms/terms/Amsterdam>", context)


class DescribePropertyChoiceTests(unittest.TestCase):
    def test_fires_on_vague_word_and_names_monumentaard(self):
        query = (
            "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument . "
            "?rm ceo:heeftMonumentAard ?aard }"
        )
        caveat = describe_property_choice("Wat voor soort monument is dit?", query)
        self.assertIsNotNone(caveat)
        self.assertIn("monumentaard", caveat)

    def test_fires_on_vague_word_and_names_functie(self):
        query = (
            "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument . "
            "?rm ceo:heeftOorspronkelijkeFunctie ?f }"
        )
        caveat = describe_property_choice("Welke aard heeft dit rijksmonument?", query)
        self.assertIsNotNone(caveat)
        self.assertIn("functie", caveat)

    def test_no_vague_word_gives_no_caveat(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument . ?rm ceo:heeftType ?t }"
        self.assertIsNone(describe_property_choice("Welke rijksmonumenten staan in Zeist?", query))

    def test_vague_word_but_no_matching_path_gives_no_caveat(self):
        # Het LLM negeerde de vraag volledig -- ander soort fout, niet dit type.
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"
        self.assertIsNone(describe_property_choice("Wat voor soort monument is dit?", query))


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
