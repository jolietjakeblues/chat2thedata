"""Resolveer labels uit gebruikersvragen naar gezaghebbende OWMS-URI's."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
import logging
import re
import unicodedata

import requests

import config

logger = logging.getLogger(__name__)

OWMS_GEMEENTE_CLASS = "http://standaarden.overheid.nl/owms/terms/Gemeente"
OWMS_PROVINCIE_CLASS = "http://standaarden.overheid.nl/owms/terms/Provincie"


@dataclass(frozen=True)
class ResolvedTerm:
    kind: str
    label: str
    uri: str


@dataclass(frozen=True)
class AmbiguousTerm:
    """Een label dat op meerdere, niet-overlappende manieren op te vatten is."""

    label: str
    candidates: tuple[ResolvedTerm, ...]


@dataclass(frozen=True)
class ResolutionResult:
    resolved: tuple[ResolvedTerm, ...] = field(default_factory=tuple)
    ambiguous: tuple[AmbiguousTerm, ...] = field(default_factory=tuple)

    @property
    def has_ambiguity(self) -> bool:
        return bool(self.ambiguous)


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^\w-]+", " ", value.casefold(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=4)
def _load_owms_terms(class_uri: str) -> tuple[tuple[str, str], ...]:
    query = f"""
PREFIX graph: <https://linkeddata.cultureelerfgoed.nl/graph/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?uri ?label
WHERE {{
  GRAPH graph:owms {{
    ?uri a <{class_uri}> ;
         skos:prefLabel ?label .
  }}
}}
""".strip()
    response = requests.get(
        config.SPARQL_ENDPOINT,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=15,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    return tuple(
        (row["label"]["value"], row["uri"]["value"])
        for row in bindings
        if row.get("label", {}).get("value") and row.get("uri", {}).get("value")
    )


@lru_cache(maxsize=1)
def _load_woonplaats_terms() -> tuple[tuple[str, str], ...]:
    """Haal alle bekende woonplaatsnamen op (ceo:woonplaatsnaam op ceo:BAGRelatie).

    Anders dan gemeente/provincie heeft een woonplaatsnaam geen eigen
    resolvebare SKOS-concept-URI in OWMS -- het is een kale string-property.
    Geeft daarom (naam, naam)-paren terug (label == uri) zodat _find_longest()
    ongewijzigd herbruikbaar is; het "uri"-veld draagt hier de exacte,
    bevestigde canonieke schrijfwijze, geen resolvebare URI.
    """
    query = """
PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#>
PREFIX graph: <https://linkeddata.cultureelerfgoed.nl/graph/>
SELECT DISTINCT ?naam WHERE {
  GRAPH graph:instanties-rce {
    ?bag ceo:woonplaatsnaam ?naam .
  }
}
""".strip()
    response = requests.get(
        config.SPARQL_ENDPOINT,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=15,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    return tuple(
        (row["naam"]["value"], row["naam"]["value"])
        for row in bindings
        if row.get("naam", {}).get("value")
    )


def _find_longest(question: str, terms: tuple[tuple[str, str], ...]) -> tuple[str, str] | None:
    normalised_question = _normalise(question)
    matches = [
        (label, uri)
        for label, uri in terms
        if re.search(rf"(?<!\w){re.escape(_normalise(label))}(?!\w)", normalised_question)
    ]
    return max(matches, key=lambda item: len(_normalise(item[0]))) if matches else None


def resolve_question(
    question: str, disambiguation: dict[str, str] | None = None
) -> ResolutionResult:
    """Resolveer een plaatslabel naar een gemeentelijke of provinciale OWMS-URI.

    Bij een expliciet "gemeente"/"provincie" in de vraag is er niets dubbelzinnigs
    en wordt die keuze direct gebruikt. Matcht een naam zonder zo'n keyword op
    zowel de gemeente- als de provincie-vocabulaire (bv. "Utrecht"), dan is dat een
    echte tie die de resultatenset verandert: die wordt teruggegeven als
    ``ambiguous`` in plaats van stilzwijgend opgelost, tenzij ``disambiguation``
    al een keuze voor dat label bevat (normalised label -> "gemeente"/"provincie").
    """
    q = _normalise(question)
    try:
        matches = {
            "gemeente": _find_longest(question, _load_owms_terms(OWMS_GEMEENTE_CLASS)),
            "provincie": _find_longest(question, _load_owms_terms(OWMS_PROVINCIE_CLASS)),
            "plaats": _find_longest(question, _load_woonplaats_terms()),
        }
    except requests.RequestException as exc:
        raise RuntimeError("OWMS/woonplaats-resolutie via het RCE endpoint is mislukt") from exc

    if "provincie" in q:
        kind = "provincie"
    elif "gemeente" in q:
        kind = "gemeente"
    elif "woonplaats" in q:
        # Bewust alleen "woonplaats", niet kaal "plaats": dat laatste komt als
        # substring voor in courante woorden (vindplaats, standplaats,
        # geboorteplaats) en zou op de genormaliseerde vraagtekst ten onrechte
        # matchen.
        kind = "plaats"
    else:
        available = [(candidate, match) for candidate, match in matches.items() if match]
        if not available:
            return ResolutionResult()

        if len(available) > 1:
            label = available[0][1][0]
            normalised_disambiguation = {
                _normalise(key): value for key, value in (disambiguation or {}).items()
            }
            override = normalised_disambiguation.get(_normalise(label))
            available_by_kind = dict(available)
            if override in available_by_kind:
                kind = override
                match = available_by_kind[kind]
                return ResolutionResult(
                    resolved=(ResolvedTerm(kind=kind, label=match[0], uri=match[1]),)
                )

            candidates = tuple(
                ResolvedTerm(kind=candidate_kind, label=match[0], uri=match[1])
                for candidate_kind, match in available
            )
            return ResolutionResult(ambiguous=(AmbiguousTerm(label=label, candidates=candidates),))

        kind, _ = available[0]

    match = matches[kind]
    if not match:
        return ResolutionResult()
    label, uri = match
    return ResolutionResult(resolved=(ResolvedTerm(kind=kind, label=label, uri=uri),))


def describe_ambiguity(ambiguous: tuple[AmbiguousTerm, ...]) -> dict:
    """Zet de eerste onopgeloste ambiguïteit om in een JSON-serialiseerbare vraag."""
    term = ambiguous[0]
    return {
        "type": "entity_ambiguity",
        "message": (
            f'"{term.label}" kan meerdere dingen zijn. Deze geven mogelijk '
            "verschillende resultaten."
        ),
        "options": [
            {
                "id": candidate.kind,
                "label": f"{candidate.kind.capitalize()} {candidate.label}",
                "term_label": term.label,
            }
            for candidate in term.candidates
        ],
    }


def build_semantic_context(terms: Sequence[ResolvedTerm]) -> str:
    if not terms:
        return ""
    lines = ["OPGELOSTE BEGRIPPEN. DEZE URI'S/WAARDEN ZIJN VERPLICHT:"]
    for term in terms:
        if term.kind == "plaats":
            lines.append(
                f'- plaats (woonplaats) "{term.label}"; gebruik via '
                "ceo:heeftBasisregistratieRelatie -> ceo:heeftBAGRelatie -> "
                f'ceo:woonplaatsnaam, EXACTE match "{term.label}" (geen CONTAINS/LCASE).'
            )
            continue
        property_name = "ceo:heeftGemeente" if term.kind == "gemeente" else "ceo:heeftProvincie"
        lines.append(
            f'- {term.kind} "{term.label}" = <{term.uri}>; '
            f"gebruik via ceo:heeftBasisregistratieRelatie en {property_name}."
        )
    lines.append(
        "Gebruik geen labeltekst, BRK/gemeentenaam of vrije CONTAINS-matching op "
        "woonplaatsnaam als vervanging voor de hierboven opgegeven URI's/exacte waarden."
    )
    return "\n".join(lines)
