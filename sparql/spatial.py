"""
Lokale fallback voor ruimtelijke SPARQL-joins (geof:sfWithin / geof:sfIntersects).

Waarom: geof:sfWithin/geof:sfIntersects op het RCE Virtuoso-endpoint kan
falen met een TopologyException (ongeldige/zelf-overlappende polygonen,
bv. bij complexe gezicht-polygonen) of een timeout, vooral zodra een naam-
filter meerdere kandidaat-gebieden matcht (bv. twee gezichten die allebei
"Dordrecht" in de naam hebben). Bij zo'n fout herhaalt de executor de query
zonder de ruimtelijke FILTER, en wordt de ruimtelijke test hier lokaal met
Shapely uitgevoerd. Ongeldige geometrie wordt gerepareerd (buffer(0)) of,
als dat niet lukt, overgeslagen in plaats van de hele query te laten
crashen.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from shapely import wkt as shapely_wkt
from shapely.errors import ShapelyError

logger = logging.getLogger(__name__)

_SPATIAL_FILTER_RE = re.compile(
    r"FILTER\s*\(\s*geof:(sfWithin|sfIntersects)\s*\(\s*(\?\w+)\s*,\s*(\?\w+)\s*\)\s*\)\s*\.?",
    re.IGNORECASE,
)


def has_spatial_filter(query: str) -> bool:
    return bool(_SPATIAL_FILTER_RE.search(query))


def extract_spatial_filter(query: str) -> tuple[str, str, str] | None:
    """Vind de eerste geof:sfWithin/sfIntersects-filter.

    Geeft (relatie, object_variabele, gebied_variabele) terug, of None.
    """
    match = _SPATIAL_FILTER_RE.search(query)
    if not match:
        return None
    relation, obj_var, gebied_var = match.groups()
    return relation, obj_var.lstrip("?"), gebied_var.lstrip("?")


def strip_spatial_filter(query: str) -> str:
    """Verwijder de geof:sfWithin/sfIntersects-FILTER; de rest blijft ongewijzigd."""
    return _SPATIAL_FILTER_RE.sub("", query)


FALLBACK_LIMIT = 10_000  # praktische cap voor Virtuoso, zelfde orde als de RCE-eigen limieten

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)


def widen_limit(query: str, cap: int = FALLBACK_LIMIT) -> str:
    """
    Verhoog een bestaande LIMIT naar `cap`, of voeg er een toe als er geen is.

    Nodig omdat de fallback de ruimtelijke FILTER lokaal (met Shapely)
    toepast NA het endpoint-antwoord: blijft de oorspronkelijke, vaak kleine
    LIMIT (bv. 20 voor een lijstquery) staan, dan wordt het kandidaatveld al
    op het endpoint afgekapt vóórdat de lokale join ooit een kans krijgt --
    het resultaat kan dan stil onvolledig zijn (in het ergste geval 0 rijen
    terwijl er wel degelijk matches bestaan).
    """
    match = _LIMIT_RE.search(query)

    if match:
        if int(match.group(1)) >= cap:
            return query
        return query[: match.start(1)] + str(cap) + query[match.end(1) :]

    return f"{query.rstrip()}\nLIMIT {cap}"


def _strip_wkt_prefix(value: str) -> str:
    """Verwijder een eventuele CRS-URI of SRID-prefix vóór de WKT-tekst."""
    value = value.strip()
    value = re.sub(r"^<[^>]+>\s*", "", value)
    value = re.sub(r"^SRID=\d+;\s*", "", value, flags=re.IGNORECASE)
    return value


def _parse_geometry(wkt_value: str):
    """Parse WKT naar een geldige Shapely-geometrie; repareer of geef None."""
    try:
        geom = shapely_wkt.loads(_strip_wkt_prefix(wkt_value))
    except (ShapelyError, ValueError):
        return None

    if geom.is_valid:
        return geom

    try:
        repaired = geom.buffer(0)
    except (ShapelyError, ValueError):
        return None

    return repaired if repaired.is_valid and not repaired.is_empty else None


def apply_spatial_filter(
    data: dict[str, Any], relation: str, obj_var: str, gebied_var: str
) -> dict[str, Any]:
    """Filter bindings lokaal op de ruimtelijke relatie.

    Rijen met ontbrekende, onleesbare of onherstelbaar ongeldige geometrie
    worden overgeslagen (gelogd), in plaats van de hele aanvraag te laten
    mislukken.
    """
    bindings = data.get("results", {}).get("bindings", [])
    kept: list[dict[str, Any]] = []
    skipped = 0

    for row in bindings:
        obj_wkt = row.get(obj_var, {}).get("value")
        gebied_wkt = row.get(gebied_var, {}).get("value")

        if not obj_wkt or not gebied_wkt:
            skipped += 1
            continue

        obj_geom = _parse_geometry(obj_wkt)
        gebied_geom = _parse_geometry(gebied_wkt)

        if obj_geom is None or gebied_geom is None:
            skipped += 1
            continue

        try:
            if relation.lower() == "sfwithin":
                match = obj_geom.within(gebied_geom)
            else:
                match = obj_geom.intersects(gebied_geom)
        except (ShapelyError, ValueError):
            skipped += 1
            continue

        if match:
            kept.append(row)

    if skipped:
        logger.warning(
            "Lokale ruimtelijke filter (%s): %d rij(en) overgeslagen "
            "wegens ontbrekende of ongeldige geometrie.",
            relation,
            skipped,
        )

    data["results"]["bindings"] = kept
    return data
