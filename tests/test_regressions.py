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
from sparql.postprocess import fix_missing_where_wrapper, inject_prefixes, postprocess
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

    def test_missing_where_wrapper_around_graph_is_repaired(self):
        # Gezien op qwen2.5-coder:14b (Ollama): SELECT direct gevolgd door
        # GRAPH zonder omliggende WHERE { } geeft een parserfout op het
        # RCE-endpoint ("Invalid SPARQL query: Parser error ... GRAPH ...").
        broken = (
            "PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#>\n"
            "PREFIX graph: <https://linkeddata.cultureelerfgoed.nl/graph/>\n\n"
            "SELECT (COUNT(DISTINCT ?rm) AS ?aantal)\n"
            "GRAPH graph:instanties-rce {\n"
            "  ?rm a ceo:Rijksmonument .\n"
            "  ?rm ceo:heeftBasisregistratieRelatie ?relatie .\n"
            "  ?relatie ceo:heeftProvincie "
            "<http://standaarden.overheid.nl/owms/terms/Utrecht_(provincie)> .\n"
            "}"
        )
        fixed = fix_missing_where_wrapper(broken)

        self.assertEqual(fixed.count("{"), fixed.count("}"))
        self.assertIn("WHERE {", fixed)
        self.assertIn("GRAPH graph:instanties-rce {", fixed)
        # De WHERE-accolade moet ook echt om het GRAPH-blok heen staan, niet
        # er los naast.
        self.assertRegex(
            fixed, r"WHERE\s*\{\s*GRAPH graph:instanties-rce \{"
        )

        # postprocess() als geheel moet dezelfde query nu geldig maken.
        full = postprocess(broken, "telling")
        self.assertEqual(full.count("{"), full.count("}"))

    def test_already_wrapped_graph_query_is_untouched(self):
        already_valid = (
            "SELECT (COUNT(DISTINCT ?rm) AS ?aantal) WHERE {\n"
            "  GRAPH graph:instanties-rce { ?rm a ceo:Rijksmonument . }\n"
            "}"
        )
        self.assertEqual(fix_missing_where_wrapper(already_valid), already_valid)

    def test_query_without_graph_is_untouched(self):
        query = "SELECT * WHERE { ?rm a ceo:Rijksmonument . }"
        self.assertEqual(fix_missing_where_wrapper(query), query)


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

    def test_widen_limit_raises_low_limit_to_cap(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument } LIMIT 20"
        widened = spatial.widen_limit(query, cap=10_000)
        self.assertIn("LIMIT 10000", widened)
        self.assertNotIn("LIMIT 20", widened)

    def test_widen_limit_leaves_limit_at_or_above_cap_untouched(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument } LIMIT 10000"
        self.assertEqual(spatial.widen_limit(query, cap=10_000), query)

    def test_widen_limit_adds_limit_when_absent(self):
        query = "SELECT ?rm WHERE { ?rm a ceo:Rijksmonument }"
        widened = spatial.widen_limit(query, cap=10_000)
        self.assertIn("LIMIT 10000", widened)

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

    def test_fallback_flags_incomplete_when_candidate_set_hits_cap(self):
        query = (
            "SELECT ?rm ?rmWkt ?gezicht ?gezichtWkt WHERE { "
            "FILTER(geof:sfWithin(?rmWkt, ?gezichtWkt)) } LIMIT 20"
        )
        cap = 25  # groter dan de oorspronkelijke LIMIT 20, anders wordt er niets verbreed
        row = {
            "rm": {"value": "http://x/rm/1"},
            "rmWkt": {"value": "POINT(5.05 52.1)"},
            "gezicht": {"value": "http://x/gz/1"},
            "gezichtWkt": {"value": "POLYGON((5 52, 5 52.2, 5.1 52.2, 5.1 52, 5 52))"},
        }
        # precies `cap` rijen terug -- het kandidaatveld raakte de bovengrens
        capped_json = {
            "head": {"vars": ["rm", "rmWkt", "gezicht", "gezichtWkt"]},
            "results": {"bindings": [row] * cap},
        }
        captured_queries = []

        def fake_run(query):
            captured_queries.append(query)
            if len(captured_queries) == 1:
                response = MagicMock()
                response.text = "TopologyException: side location conflict"
                raise requests.exceptions.HTTPError(response=response)
            return capped_json

        with patch.object(sparql_executor, "_run", side_effect=fake_run), \
                patch.object(spatial, "FALLBACK_LIMIT", cap):
            result = sparql_executor.execute(query)

        self.assertTrue(result.get("incomplete_due_to_limit"))
        self.assertIn(f"LIMIT {cap}", captured_queries[1])

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

    def test_caveat_is_appended_when_present(self):
        results = {
            "head": {"vars": ["aantal"]},
            "results": {"bindings": [
                {"aantal": {"type": "literal", "value": "6331"}}
            ]},
        }
        answer = answer_generator.generate("Hoeveel?", results, caveat="dit dekt niet alles")
        self.assertIn("6331", answer)
        self.assertIn("dit dekt niet alles", answer)

    def test_no_caveat_leaves_answer_unchanged(self):
        results = {
            "head": {"vars": ["aantal"]},
            "results": {"bindings": [
                {"aantal": {"type": "literal", "value": "6331"}}
            ]},
        }
        with_none = answer_generator.generate("Hoeveel?", results, caveat=None)
        without_arg = answer_generator.generate("Hoeveel?", results)
        self.assertEqual(with_none, without_arg)


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

    def test_unexpected_error_is_reported_to_sentry(self):
        import app as app_module
        from sparql import sparql_generator

        boom = RuntimeError("onverwacht")
        with patch.object(sparql_generator, "generate", side_effect=boom), \
                patch.object(app_module, "sentry_sdk") as mock_sentry:
            response = self.client.post(
                "/api/generate-sparql", json={"question": "Hoeveel rijksmonumenten zijn er?"}
            )

        self.assertEqual(response.status_code, 500)
        mock_sentry.capture_exception.assert_called_once_with(boom)

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

    def test_unanswerable_question_returns_limitation_clarification(self):
        from sparql import sparql_generator
        from sparql.answerability import detect_limitation

        limitation = detect_limitation("Welke rijksmonumenten liggen bij een begraafplaats?")

        with patch.object(
            sparql_generator, "generate",
            side_effect=sparql_generator.AnswerabilityLimitationNeeded(limitation),
        ):
            response = self.client.post(
                "/api/generate-sparql",
                json={"question": "Welke rijksmonumenten liggen bij een begraafplaats?"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertNotIn("query", body)
        self.assertEqual(body["clarification"]["type"], "answerability_limitation")
        option_ids = {opt["id"] for opt in body["clarification"]["options"]}
        self.assertEqual(option_ids, {"functie_begraafplaats", "complex_onderdeel"})
        for option in body["clarification"]["options"]:
            self.assertIn("caveat", option)


if __name__ == "__main__":
    unittest.main()