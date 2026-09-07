import os
import unittest
from unittest.mock import MagicMock, patch

import requests

os.environ.setdefault("LLM_PROVIDER", "ollama")

import config
from answer import answer_generator
from sparql import executor as sparql_executor
from sparql import spatial
from sparql.executor import _validate_read_query
from sparql.postprocess import inject_prefixes
from sparql.semantic_resolver import ResolvedTerm, _find_longest
from sparql.semantic_validator import validate_semantics


class QueryValidationTests(unittest.TestCase):
    def test_select_with_geo_prefix_is_allowed(self):
        _validate_read_query(
            "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
            "SELECT * WHERE { ?s geo:hasGeometry ?g }"
        )

    def test_update_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SELECT- en ASK"):
            _validate_read_query("DELETE WHERE { ?s ?p ?o }")

    def test_missing_geo_prefix_is_injected(self):
        query = inject_prefixes(
            "SELECT * WHERE { ?s geo:hasGeometry ?g . ?g geo:asWKT ?wkt }"
        )
        self.assertIn(
            "PREFIX geo: <http://www.opengis.net/ont/geosparql#>", query
        )


class SemanticResolverTests(unittest.TestCase):
    def test_place_name_matches_despite_trailing_punctuation(self):
        terms = (("Amsterdam", "http://standaarden.overheid.nl/owms/terms/Amsterdam"),)
        self.assertEqual(
            _find_longest("Welke rijksmonumenten staan er in Amsterdam?", terms),
            ("Amsterdam", "http://standaarden.overheid.nl/owms/terms/Amsterdam"),
        )

    def test_no_match_for_unrelated_question(self):
        terms = (("Amsterdam", "http://standaarden.overheid.nl/owms/terms/Amsterdam"),)
        self.assertIsNone(_find_longest("Hoeveel rijksmonumenten zijn er in totaal?", terms))


class SemanticValidatorTests(unittest.TestCase):
    def setUp(self):
        self.terms = [
            ResolvedTerm(
                kind="gemeente",
                label="Amsterdam",
                uri="http://standaarden.overheid.nl/owms/terms/Amsterdam",
            )
        ]

    def test_brk_gemeentenaam_is_rejected_when_gemeente_resolved(self):
        query = (
            "?relatie ceo:heeftBRKRelatie ?brk . "
            "?brk ceo:gemeentenaam ?gemeente ."
        )
        errors = validate_semantics("... Amsterdam?", query, self.terms)
        self.assertTrue(errors)

    def test_heeftgemeente_with_resolved_uri_passes(self):
        query = "?relatie ceo:heeftGemeente <http://standaarden.overheid.nl/owms/terms/Amsterdam> ."
        errors = validate_semantics("... Amsterdam?", query, self.terms)
        self.assertEqual(errors, [])

    def test_archaeological_query_with_only_heeftgemeente_is_rejected(self):
        # Werkt alleen voor een HUIDIGE gemeentenaam (bv. Beekdaelen); een
        # voormalige gemeente/dorpsnaam (bv. Nuth) matcht dan niet, en de
        # query weet niet vooraf welke van de twee de gebruiker bedoelt.
        terms = [
            ResolvedTerm(
                kind="gemeente",
                label="Nuth",
                uri="http://standaarden.overheid.nl/owms/terms/Nuth_(gemeente)",
            )
        ]
        query = (
            "?vondst a ceo:Vondsten . ?vondst ceo:ligtInObject ?locatie . "
            "?locatie a ceo:Vondstlocatie . "
            "?locatie ceo:heeftBasisregistratieRelatie ?relatie . "
            "?relatie ceo:heeftGemeente <http://standaarden.overheid.nl/owms/terms/Nuth_(gemeente)> ."
        )
        errors = validate_semantics("Welke romeinse vondsten liggen in Nuth?", query, terms)
        self.assertTrue(errors)

    def test_archaeological_query_with_only_woonplaatsnaam_is_rejected(self):
        # Werkt alleen voor een voormalige/dorpsnaam; mist een huidige
        # gemeentenaam zoals Beekdaelen.
        terms = [
            ResolvedTerm(
                kind="gemeente",
                label="Nuth",
                uri="http://standaarden.overheid.nl/owms/terms/Nuth_(gemeente)",
            )
        ]
        query = (
            "?vondst a ceo:Vondsten . ?vondst ceo:ligtInObject ?locatie . "
            "?locatie a ceo:Vondstlocatie . "
            "?locatie ceo:heeftBasisregistratieRelatie ?relatie . "
            "?relatie ceo:heeftBAGRelatie ?bag . "
            "?bag ceo:woonplaatsnaam ?woonplaats . "
            'FILTER(CONTAINS(LCASE(STR(?woonplaats)), "nuth"))'
        )
        errors = validate_semantics("Welke romeinse vondsten liggen in Nuth?", query, terms)
        self.assertTrue(errors)

    def test_archaeological_query_with_union_of_both_passes(self):
        terms = [
            ResolvedTerm(
                kind="gemeente",
                label="Nuth",
                uri="http://standaarden.overheid.nl/owms/terms/Nuth_(gemeente)",
            )
        ]
        query = (
            "?vondst a ceo:Vondsten . ?vondst ceo:ligtInObject ?locatie . "
            "?locatie a ceo:Vondstlocatie . "
            "{ ?locatie ceo:heeftBasisregistratieRelatie ?relatie . "
            "?relatie ceo:heeftGemeente <http://standaarden.overheid.nl/owms/terms/Nuth_(gemeente)> . } "
            "UNION "
            "{ ?locatie ceo:heeftBasisregistratieRelatie ?relatie . "
            "?relatie ceo:heeftBAGRelatie ?bag . "
            "?bag ceo:woonplaatsnaam ?woonplaats . "
            'FILTER(CONTAINS(LCASE(STR(?woonplaats)), "nuth")) }'
        )
        errors = validate_semantics("Welke romeinse vondsten liggen in Nuth?", query, terms)
        self.assertEqual(errors, [])


class RetryTests(unittest.TestCase):
    """Eén automatische herkansing bij een tijdelijke opstartvertraging
    (bv. het RCE-endpoint of een tussenliggende proxy na inactiviteit)."""

    def test_timeout_succeeds_on_retry(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"
        calls = {"n": 0}
        success = {
            "head": {"vars": ["rm"]},
            "results": {"bindings": [{"rm": {"value": "http://x/1"}}]},
        }

        def fake_run(_query):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.exceptions.Timeout("opstartvertraging")
            return success

        with patch.object(sparql_executor, "_run", side_effect=fake_run), \
                patch("time.sleep", lambda _s: None):
            result = sparql_executor.execute(query)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(result["results"]["bindings"]), 1)

    def test_persistent_timeout_is_reraised_after_retry(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"

        def always_timeout(_query):
            raise requests.exceptions.Timeout("blijvend")

        with patch.object(sparql_executor, "_run", side_effect=always_timeout), \
                patch("time.sleep", lambda _s: None):
            with self.assertRaises(requests.exceptions.Timeout):
                sparql_executor.execute(query)

    def test_non_retryable_http_error_is_not_retried(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"
        calls = {"n": 0}

        def bad_request(_query):
            calls["n"] += 1
            response = MagicMock(status_code=400)
            raise requests.exceptions.HTTPError(response=response)

        with patch.object(sparql_executor, "_run", side_effect=bad_request):
            with self.assertRaises(requests.exceptions.HTTPError):
                sparql_executor.execute(query)

        self.assertEqual(calls["n"], 1)


class SpatialFallbackTests(unittest.TestCase):
    def test_self_intersecting_polygon_is_repaired(self):
        bowtie = "POLYGON((0 0, 2 2, 2 0, 0 2, 0 0))"
        geom = spatial._parse_geometry(bowtie)
        self.assertIsNotNone(geom)
        self.assertTrue(geom.is_valid)

    def test_unparseable_wkt_returns_none(self):
        self.assertIsNone(spatial._parse_geometry("NOT WKT AT ALL"))

    def test_apply_spatial_filter_keeps_only_matching_rows(self):
        data = {
            "head": {"vars": ["rm", "rmWkt", "gezicht", "gezichtWkt"]},
            "results": {"bindings": [
                {
                    "rm": {"value": "http://x/rm/1"},
                    "rmWkt": {"value": "POINT(5.05 52.1)"},
                    "gezicht": {"value": "http://x/gz/1"},
                    "gezichtWkt": {"value": "POLYGON((5 52, 5 52.2, 5.1 52.2, 5.1 52, 5 52))"},
                },
                {
                    "rm": {"value": "http://x/rm/2"},
                    "rmWkt": {"value": "POINT(6 53)"},
                    "gezicht": {"value": "http://x/gz/1"},
                    "gezichtWkt": {"value": "POLYGON((5 52, 5 52.2, 5.1 52.2, 5.1 52, 5 52))"},
                },
            ]},
        }
        result = spatial.apply_spatial_filter(data, "sfWithin", "rmWkt", "gezichtWkt")
        kept = [row["rm"]["value"] for row in result["results"]["bindings"]]
        self.assertEqual(kept, ["http://x/rm/1"])

    def test_executor_falls_back_to_local_join_on_topology_exception(self):
        query = (
            "SELECT ?rm ?rmWkt ?gezicht ?gezichtWkt WHERE { "
            "FILTER(geof:sfWithin(?rmWkt, ?gezichtWkt)) }"
        )
        fallback_json = {
            "head": {"vars": ["rm", "rmWkt", "gezicht", "gezichtWkt"]},
            "results": {"bindings": [
                {
                    "rm": {"value": "http://x/rm/1"},
                    "rmWkt": {"value": "POINT(5.05 52.1)"},
                    "gezicht": {"value": "http://x/gz/1"},
                    "gezichtWkt": {"value": "POLYGON((5 52, 5 52.2, 5.1 52.2, 5.1 52, 5 52))"},
                },
            ]},
        }
        calls = {"n": 0}

        def fake_run(_query):
            calls["n"] += 1
            if calls["n"] == 1:
                response = MagicMock()
                response.text = "TopologyException: side location conflict"
                raise requests.exceptions.HTTPError(response=response)
            return fallback_json

        with patch.object(sparql_executor, "_run", side_effect=fake_run):
            result = sparql_executor.execute(query)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(result["results"]["bindings"]), 1)

    def test_executor_reraises_non_spatial_http_error(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"

        def fake_run(_query):
            response = MagicMock()
            response.text = "internal server error"
            raise requests.exceptions.HTTPError(response=response)

        with patch.object(sparql_executor, "_run", side_effect=fake_run):
            with self.assertRaises(requests.exceptions.HTTPError):
                sparql_executor.execute(query)


class AnswerTests(unittest.TestCase):
    def test_count_uses_sparql_value(self):
        results = {
            "head": {"vars": ["aantal"]},
            "results": {"bindings": [
                {"aantal": {"type": "literal", "value": "6331"}}
            ]},
        }
        self.assertIn("6331", answer_generator.generate("Hoeveel?", results))


class ApiTests(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            "/api/generate-sparql", data="geen json", content_type="text/plain"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Verwacht een JSON-object")

    def test_root_serves_map_frontend(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("/vendor/leaflet/leaflet.js", html)
        self.assertIn("resultMap", html)
        response.close()

    def test_ollama_needs_no_api_key(self):
        with patch.object(config, "LLM_PROVIDER", "ollama"):
            response = self.client.get("/api/health")
        self.assertTrue(response.get_json()["api_key_set"])

    def test_ambiguous_question_returns_clarification_not_query(self):
        from sparql import sparql_generator
        from sparql.semantic_resolver import AmbiguousTerm, ResolvedTerm

        ambiguous = (
            AmbiguousTerm(
                label="Utrecht",
                candidates=(
                    ResolvedTerm("gemeente", "Utrecht", "urn:gemeente-utrecht"),
                    ResolvedTerm("provincie", "Utrecht", "urn:provincie-utrecht"),
                ),
            ),
        )

        with patch.object(
            sparql_generator, "generate",
            side_effect=sparql_generator.ClarificationNeeded(ambiguous),
        ):
            response = self.client.post(
                "/api/generate-sparql", json={"question": "Monumenten in Utrecht"}
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertNotIn("query", body)
        self.assertEqual(body["clarification"]["type"], "entity_ambiguity")
        option_ids = {opt["id"] for opt in body["clarification"]["options"]}
        self.assertEqual(option_ids, {"gemeente", "provincie"})


if __name__ == "__main__":
    unittest.main()