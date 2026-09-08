"""
SPARQL query executor.

Verantwoordelijkheden:
- Query uitvoeren op het RCE SPARQL endpoint
- Resultaten dedupliceren op ?rm (monument URI)
- Foutafhandeling voor timeouts en HTTP-fouten
- Eén automatische herkansing bij een tijdelijke netwerkfout (timeout,
  connectiefout, 502/503/504) — bv. wanneer het endpoint of een
  tussenliggende proxy net moet "opstarten" na een periode van inactiviteit
- Fallback op een lokale ruimtelijke join (Shapely) als een
  geof:sfWithin/sfIntersects-query faalt op het endpoint
"""

import logging
import re
import time
from typing import Any

import requests

from config import SPARQL_ENDPOINT, PROVINCIE_NAAM
from sparql import spatial

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30
MAX_ENRICHMENT_OBJECTS = 100
_RM_URI_RE = re.compile(
    r"^https://linkeddata\.cultureelerfgoed\.nl/cho-kennis/id/rijksmonument/\d+$"
)

# Endpointfouten die duiden op een probleem met de ruimtelijke berekening
# zelf (ongeldige geometrie, JTS-topologiefout) in plaats van een fout in
# de query. Bij zo'n fout is een lokale Shapely-fallback zinvol.
SPATIAL_ERROR_MARKERS = ("topologyexception", "jts", "invalid geometry")

MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 2
RETRYABLE_STATUS_CODES = {502, 503, 504}


def _validate_read_query(query: str) -> None:
    """Sta alleen SPARQL-leesqueries toe."""
    without_comments = re.sub(r"(?m)^\s*#[^\r\n]*", "", query)
    without_prefixes = re.sub(
        r"^\s*(?:PREFIX\s+\w*:\s*<[^>]+>\s*)+",
        "",
        without_comments,
        flags=re.IGNORECASE,
    )
    if not re.match(r"^\s*(SELECT|ASK)\b", without_prefixes, re.IGNORECASE):
        raise ValueError("Alleen SELECT- en ASK-queries zijn toegestaan")


def _run(query: str) -> dict[str, Any]:
    """Voer een kale SPARQL query uit en geef het ruwe JSON-resultaat terug."""
    response = requests.get(
        SPARQL_ENDPOINT,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _is_retryable(exc: Exception) -> bool:
    """Wijst deze fout op een tijdelijk netwerk-/opstartprobleem in plaats
    van een structurele fout in de query zelf?"""
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


def _run_with_retry(query: str, retries: int = MAX_RETRIES) -> dict[str, Any]:
    """Voer _run() uit met één automatische herkansing bij een tijdelijke
    netwerkfout. Vangt bijvoorbeeld het geval op waarin het RCE-endpoint (of
    een tussenliggende proxy, zoals bij Render na een periode van
    inactiviteit) nog moet opstarten en de eerste aanvraag daardoor traag of
    kortstondig onbereikbaar is, terwijl een tweede poging vlak daarna wel
    binnen de normale tijd lukt."""
    attempt = 0
    while True:
        try:
            return _run(query)
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        ) as exc:
            if attempt >= retries or not _is_retryable(exc):
                raise
            attempt += 1
            logger.warning(
                "SPARQL-aanvraag faalde (%s) — herkansing %d/%d.",
                exc, attempt, retries,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)


def has_spatial_filter(query: str) -> bool:
    """Bevat de query een geof:sfWithin/sfIntersects-filter?"""
    return spatial.has_spatial_filter(query)


def is_spatial_error(exc: Exception) -> bool:
    """Wijst deze fout op een probleem met de ruimtelijke berekening zelf?"""
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        body = exc.response.text.lower()
        return any(marker in body for marker in SPATIAL_ERROR_MARKERS)
    return False


def execute(query: str, question: str = "") -> dict[str, Any]:
    """
    Voer een SPARQL query uit op het RCE endpoint.

    Faalt de aanvraag met een timeout, connectiefout of 502/503/504 (bv.
    omdat het endpoint of een tussenliggende proxy nog moet opstarten na een
    periode van inactiviteit), dan wordt er automatisch één keer opnieuw
    geprobeerd voordat de fout wordt doorgegeven (zie _run_with_retry).

    Bevat de query daarnaast een geof:sfWithin/sfIntersects-filter en faalt
    die aanroep alsnog met een timeout of een fout die op een
    topologieprobleem wijst, dan wordt de query herhaald zonder de
    ruimtelijke FILTER en wordt de ruimtelijke relatie lokaal met Shapely
    berekend (zie sparql/spatial.py). Kapotte of onherstelbare geometrie
    wordt daarbij overgeslagen in plaats van de hele aanvraag te laten
    mislukken. De LIMIT van de vereenvoudigde query wordt eerst verhoogd
    (spatial.widen_limit) zodat de lokale join niet op een al afgekapt
    kandidaatveld draait; raakt het kandidaatveld toch die verhoogde grens,
    dan bevat het resultaat "incomplete_due_to_limit": True.

    Returns:
        SPARQL JSON resultaat als dict, gededupliceerd op ?rm.

    Raises:
        requests.exceptions.Timeout: Bij timeout (als de herkansing en de
            eventuele fallback niet van toepassing zijn of ook falen).
        requests.exceptions.HTTPError: Bij HTTP-fouten (idem).
    """
    _validate_read_query(query)
    logger.info("Query uitvoeren op %s", SPARQL_ENDPOINT)

    spatial_filter = spatial.extract_spatial_filter(query)

    try:
        data = _run_with_retry(query)
    except (requests.exceptions.Timeout, requests.exceptions.HTTPError) as exc:
        if not spatial_filter or not is_spatial_error(exc):
            raise

        relation, obj_var, gebied_var = spatial_filter
        logger.warning(
            "Ruimtelijke query faalde op het endpoint (%s); val terug op "
            "lokale berekening met Shapely.",
            exc,
        )

        simplified_query = spatial.widen_limit(
            spatial.strip_spatial_filter(query), cap=spatial.FALLBACK_LIMIT
        )
        data = _run_with_retry(simplified_query)
        raw_count = len(data.get("results", {}).get("bindings", []))
        data = spatial.apply_spatial_filter(data, relation, obj_var, gebied_var)

        if raw_count >= spatial.FALLBACK_LIMIT:
            data["incomplete_due_to_limit"] = True
            logger.warning(
                "Ruimtelijke fallback: kandidaatset raakte de cap van %d — "
                "resultaat mogelijk onvolledig.",
                spatial.FALLBACK_LIMIT,
            )

    data = _translate_provincie_uris(data)

    original = len(data.get("results", {}).get("bindings", []))
    data = _deduplicate(data)
    deduped = len(data.get("results", {}).get("bindings", []))

    if original != deduped:
        logger.info("Deduplicatie: %d → %d rijen", original, deduped)

    return _enrich_requested_fields(data, question)


def _translate_provincie_uris(data: dict) -> dict:
    """Vertaal ?provURI waarden naar leesbare provincienamen."""
    bindings = data.get("results", {}).get("bindings", [])
    for row in bindings:
        if "provURI" in row:
            uri = row["provURI"].get("value", "")
            naam = PROVINCIE_NAAM.get(uri)
            if naam:
                row["provincie"] = {"type": "literal", "value": naam}
            else:
                # Gebruik het laatste deel van de URI als fallback
                row["provincie"] = {"type": "literal", "value": uri.split("/")[-1]}
    # Voeg provincie toe aan vars als provURI aanwezig is
    vars_ = data.get("head", {}).get("vars", [])
    if "provURI" in vars_ and "provincie" not in vars_:
        idx = vars_.index("provURI")
        vars_.insert(idx, "provincie")
    return data


def _deduplicate(data: dict[str, Any]) -> dict[str, Any]:
    """
    Dedupliceert resultaten op ?rm (monument URI).

    Als ?rm aanwezig is in de resultaten, bewaar dan alleen de eerste
    rij per monument URI. Bij queries zonder ?rm (bijv. COUNT) wordt
    niets aangepast.
    """
    bindings = data.get("results", {}).get("bindings", [])
    vars_ = data.get("head", {}).get("vars", [])

    if "rm" not in vars_ or not bindings:
        return data

    seen: set[str] = set()
    deduped = []

    for row in bindings:
        rm_val = row.get("rm", {}).get("value", "")
        if rm_val and rm_val not in seen:
            seen.add(rm_val)
            deduped.append(row)
        elif not rm_val:
            deduped.append(row)

    data["results"]["bindings"] = deduped
    return data


def _enrichment_query(uris: list[str], field: str) -> str:
    values = "\n".join(f"<{uri}>" for uri in uris)
    if field == "naam":
        pattern = """
    ?rm ceo:heeftNaam ?naamNode .
    ?naamNode ceo:naam ?value .
"""
    elif field == "adres":
        pattern = """
    ?rm ceo:heeftBasisregistratieRelatie ?relatie .
    ?relatie ceo:heeftBAGRelatie ?bag .
    ?bag ceo:volledigAdres ?value .
"""
    else:
        raise ValueError(f"Onbekend verrijkingsveld: {field}")

    return f"""PREFIX graph: <https://linkeddata.cultureelerfgoed.nl/graph/>
PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#>
SELECT ?rm (SAMPLE(?value) AS ?{field})
WHERE {{
  VALUES ?rm {{
    {values}
  }}
  GRAPH graph:instanties-rce {{{pattern}  }}
}}
GROUP BY ?rm"""


def _fetch_enrichment(uris: list[str], field: str) -> dict[str, dict[str, str]]:
    result = _run_with_retry(_enrichment_query(uris, field))
    return {
        row["rm"]["value"]: row[field]
        for row in result.get("results", {}).get("bindings", [])
        if row.get("rm", {}).get("value") and row.get(field, {}).get("value")
    }


def _enrich_requested_fields(data: dict[str, Any], question: str) -> dict[str, Any]:
    bindings = data.get("results", {}).get("bindings", [])
    vars_ = data.get("head", {}).get("vars", [])
    if not bindings or "rm" not in vars_ or not question:
        return data

    fields = []
    if re.search(r"\bna(?:am|men)\b", question, re.IGNORECASE):
        fields.append("naam")
    if re.search(r"\badres(?:sen)?\b", question, re.IGNORECASE):
        fields.append("adres")
    if not fields:
        return data

    uris = []
    for row in bindings:
        uri = row.get("rm", {}).get("value", "")
        if _RM_URI_RE.fullmatch(uri) and uri not in uris:
            uris.append(uri)
        if len(uris) >= MAX_ENRICHMENT_OBJECTS:
            break
    if not uris:
        return data

    for field in fields:
        try:
            values = _fetch_enrichment(uris, field)
        except requests.RequestException as exc:
            logger.warning("Verrijking van %s mislukt: %s", field, exc)
            continue
        for row in bindings:
            uri = row.get("rm", {}).get("value", "")
            if uri in values:
                row[field] = values[uri]
        if field not in vars_:
            vars_.append(field)
    return data
