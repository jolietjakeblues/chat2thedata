"""
Controleert of gegenereerde SPARQL daadwerkelijk geldige grammatica is.

In tegenstelling tot semantic_validator.py (dat inhoudelijke/dekkings-
heuristieken checkt) en postprocess.py (dat specifieke, al-bekende
modelfouten herstelt), gebruikt dit bestand een echte SPARQL-grammatica-
parser (rdflib) om elke syntaxfout te vangen — ook fouten die nog nooit
eerder zijn gezien en dus geen eigen regex-fix hebben. Empirisch geverifieerd
dat noch postprocess.py's fixes, noch rce-cho-mcp's validate_query_structured
dit soort fouten (niet-afgesloten URI, dubbele vergelijkingsoperator, etc.)
betrouwbaar vangen — die controleren alleen een vaste lijst bekende patronen.
"""

from __future__ import annotations

from rdflib.plugins.sparql import prepareQuery


def validate_syntax(query: str) -> list[str]:
    """Geeft een foutmelding terug als de query geen geldige SPARQL is."""
    try:
        prepareQuery(query)
    except Exception as exc:
        return [f"SPARQL-syntaxfout: {exc}"]
    return []
