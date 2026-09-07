"""
Nabewerking van gegenereerde SPARQL queries.

Verantwoordelijkheden:
- Ontbrekende WHERE/accolade rond een top-level GRAPH-blok herstellen
- Verplichte prefixen injecteren als ze ontbreken
- COUNT detecteren in lijstmodus
- Provincie-filterpad normaliseren naar directe URI match
- LCASE = vervangen door CONTAINS
- heeftProvincie zonder prefLabel-pad corrigeren

LIMIT wordt bewust NIET verwijderd: lijstqueries houden hun LIMIT (default
20, zie lijst.txt) zodat een vraag niet per ongeluk de volledige dataset
opvraagt en het endpoint of de rate-limited API zwaar belast.
"""

import re
import logging

from config import PROVINCIE_URI

logger = logging.getLogger(__name__)


REQUIRED_PREFIXES = {
    "ceo:": "PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#>",
    "graph:": "PREFIX graph: <https://linkeddata.cultureelerfgoed.nl/graph/>",
    "skos:": "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>",
    "rdf:": "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>",
    "rdfs:": "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>",
    "xsd:": "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>",
    "geo:": "PREFIX geo: <http://www.opengis.net/ont/geosparql#>",
    "gsp:": "PREFIX gsp: <http://www.opengis.net/ont/geosparql#>",
    "geof:": "PREFIX geof: <http://www.opengis.net/def/function/geosparql/>",
}


def _declared_prefixes(query: str) -> set[str]:
    """Geef alle prefixnamen terug die al gedeclareerd zijn."""
    return {
        match.group(1) + ":"
        for match in re.finditer(
            r"^\s*PREFIX\s+([A-Za-z][\w-]*)\s*:",
            query,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    }


def inject_prefixes(query: str) -> str:
    """Voeg gebruikte prefixen toe als ze ontbreken."""
    declared = _declared_prefixes(query)
    additions: list[str] = []

    for prefix, declaration in REQUIRED_PREFIXES.items():
        if prefix in query and prefix not in declared:
            additions.append(declaration)

    if additions:
        return "\n".join(additions) + "\n\n" + query

    return query


def fix_missing_where_wrapper(query: str) -> str:
    """
    Herstel een ontbrekende WHERE/accolade rond een top-level GRAPH-blok.

    Sommige modellen (vooral kleinere lokale) genereren af en toe:
        SELECT (COUNT(DISTINCT ?rm) AS ?aantal)
        GRAPH graph:instanties-rce { ... }
    zonder omliggende WHERE { }. Dat is geen geldige SPARQL: GRAPH mag
    alleen binnen een group graph pattern ({ }) staan. Voeg de ontbrekende
    accolades toe zodat het GRAPH-blok in een geldig WHERE-blok valt.

    Staat er tussen SELECT en GRAPH al een { (dus WHERE { GRAPH ... of
    kaal { GRAPH ...), dan is de query al goed gevormd en wordt niets
    aangepast.
    """
    match = re.search(r"SELECT\b[^{]*?(\bGRAPH\s+\S+\s*\{)", query, re.IGNORECASE)

    if not match:
        return query

    graph_brace_start = match.end() - 1

    depth = 0
    close_pos = None

    for i in range(graph_brace_start, len(query)):
        if query[i] == "{":
            depth += 1
        elif query[i] == "}":
            depth -= 1
            if depth == 0:
                close_pos = i
                break

    if close_pos is None:
        return query

    insert_where_at = match.start(1)
    query = query[:insert_where_at] + "WHERE {\n" + query[insert_where_at:]
    close_pos += len("WHERE {\n")
    query = query[: close_pos + 1] + "\n}" + query[close_pos + 1 :]

    return query


def has_count(query: str) -> bool:
    """Geeft True als de query een COUNT bevat."""
    return bool(re.search(r"\bCOUNT\b", query, re.IGNORECASE))


def fix_provincie_pad(query: str) -> str:
    """
    Zorg dat heeftProvincie altijd via rdfs:label loopt als het model een variabele gebruikt.

    Als het LLM alleen ?brr ceo:heeftProvincie ?prov . genereert
    zonder rdfs:label stap, voeg die dan toe.
    """
    if "heeftProvincie" not in query:
        return query

    if "provURI" in query or "rdfs:label" in query:
        return query

    query = re.sub(
        r"([?]\w+)\s+ceo:heeftProvincie\s+([?]\w+)\s*\.",
        lambda m: (
            m.group(1)
            + " ceo:heeftProvincie ?provURI .\n"
            + "?provURI rdfs:label "
            + m.group(2)
            + " ."
        ),
        query,
    )

    return query


def normalize_provincie_uri(query: str) -> str:
    """
    Vervang provincie-label-filter door directe URI match.

    Van:
      ?provURI rdfs:label ?provincie .
      FILTER(CONTAINS(LCASE(STR(?provincie)), "utrecht"))

    Naar:
      ceo:heeftProvincie <http://...Utrecht_(provincie)> .
    """
    if "heeftProvincie" not in query:
        return query

    m = re.search(r'FILTER[^"]*"([^"]+)"', query, re.IGNORECASE)

    if not m:
        return query

    ctx = query[max(0, m.start() - 150): m.end() + 50].lower()

    if not any(x in ctx for x in ["provinci", "provlabel", "provuri"]):
        return query

    zoekterm = m.group(1).lower().strip()
    uri = PROVINCIE_URI.get(zoekterm)

    if not uri:
        logger.warning(
            "Onbekende provincie: '%s' - filter niet genormaliseerd",
            zoekterm,
        )
        return query

    logger.info(
        "Provincie '%s' genormaliseerd naar URI: %s",
        zoekterm,
        uri,
    )

    lines = query.split("\n")

    lines = [
        line
        for line in lines
        if not (
            "rdfs:label" in line
            and re.search(r"\?prov\w*", line, re.IGNORECASE)
        )
    ]

    lines = [
        line
        for line in lines
        if not ("FILTER" in line and zoekterm in line.lower())
    ]

    query = "\n".join(lines)

    query = re.sub(
        r"ceo:heeftProvincie\s+[?]\w+\s*\.",
        "ceo:heeftProvincie <" + uri + "> .",
        query,
    )

    return query


def fix_label_filter(query: str) -> str:
    """
    Vervang FILTER(LCASE(?x) = "waarde") door CONTAINS-variant.
    Taalgelabelde strings kunnen niet altijd netjes met = vergeleken worden.
    """
    return re.sub(
        r"FILTER\s*\(\s*LCASE\s*\(\s*STR\s*\((\?\w+)\)\s*\)\s*=\s*(\"[^\"]+\")\s*\)",
        lambda m: "FILTER(CONTAINS(LCASE(STR(" + m.group(1) + ")), " + m.group(2) + "))",
        query,
        flags=re.IGNORECASE,
    )

def is_incomplete_query(query: str) -> bool:
    """Geeft True als de SPARQL-query duidelijk onvolledig is."""
    q = query.strip()

    return (
        q.count("<") != q.count(">")
        or q.count("{") != q.count("}")
        or "SELECT" not in q.upper()
        or "WHERE" not in q.upper()
    )

def postprocess(query: str, mode: str) -> str:
    """
    Pas alle nabewerking toe op een gegenereerde SPARQL query.

    Volgorde is belangrijk:
    1. Strip backticks
    2. Herstel ontbrekende WHERE/accolade rond een top-level GRAPH-blok
    3. Inject prefixen
    4. Fix provincie pad
    5. Normaliseer provincie naar URI
    6. Fix label filters
    7. Inject prefixen opnieuw, want fixes kunnen rdfs toevoegen
    """
    query = query.replace("```sparql", "").replace("```", "").strip()

    query = fix_missing_where_wrapper(query)
    query = inject_prefixes(query)
    query = fix_provincie_pad(query)
    query = normalize_provincie_uri(query)
    query = fix_label_filter(query)
    query = inject_prefixes(query)

    return query