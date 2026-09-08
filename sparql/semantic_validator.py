"""Semantische controles op door een taalmodel gegenereerde SPARQL."""

from __future__ import annotations

import re

from sparql.semantic_resolver import ResolvedTerm


def requested_limit(question: str) -> int | None:
    match = re.search(
        r"\b(?:geef|toon|laat(?:\s+mij)?\s+zien|noem)\s+(\d{1,3})\b",
        question,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


# Archeologische classes: een genoemde plaats kan hier zowel een HUIDIGE
# gemeente zijn (ceo:heeftGemeente werkt daarvoor) als een voormalige
# gemeente/dorpsnaam die alleen nog via BAG/woonplaatsnaam te vinden is
# (bv. Nuth, dat in 2019 opging in Beekdaelen). Vereist daarom een UNION van
# beide paden in plaats van gemeente verplicht te maken (zie GEMEENTE EN
# PROVINCIE in lijst.txt/telling.txt).
ARCHAEOLOGICAL_CLASS_MARKERS = (
    "ceo:vondsten", "ceo:vondstlocatie", "ceo:grondsporen",
    "ceo:archeologischcomplex", "ceo:archeologischterrein",
    "ceo:archeologischonderzoeksgebied",
)


def _is_archaeological_query(query: str) -> bool:
    lowered = query.lower()
    return any(marker in lowered for marker in ARCHAEOLOGICAL_CLASS_MARKERS)


def validate_semantics(question: str, query: str, terms: list[ResolvedTerm]) -> list[str]:
    errors: list[str] = []
    lowered = query.lower()
    archaeological = _is_archaeological_query(query)

    for term in terms:
        if term.kind == "gemeente" and archaeological:
            has_gemeente_uri = f"<{term.uri}>" in query
            has_woonplaats = "woonplaatsnaam" in lowered
            if not (has_gemeente_uri and has_woonplaats):
                errors.append(
                    "Dit is een archeologische vraag met een plaatsnaam. De "
                    "genoemde plaats kan zowel een huidige gemeente als een "
                    "voormalige gemeente/dorpsnaam zijn (bv. Nuth, opgegaan in "
                    "Beekdaelen) — je weet vooraf niet welke. Gebruik een UNION "
                    f"die beide combineert: ceo:heeftGemeente <{term.uri}> ÉN "
                    "BAG/woonplaatsnaam CONTAINS/LCASE, zodat de vraag werkt "
                    "ongeacht welke van de twee de gebruiker bedoelt."
                )
            continue

        if f"<{term.uri}>" not in query:
            errors.append(f'Gebruik de opgeloste {term.kind}-URI <{term.uri}>.')
        required = "ceo:heeftGemeente" if term.kind == "gemeente" else "ceo:heeftProvincie"
        if required.lower() not in lowered:
            errors.append(f"Gebruik {required} via ceo:heeftBasisregistratieRelatie.")
        if term.kind == "gemeente" and (
            "ceo:gemeentenaam" in lowered or "ceo:heeftbrkrelatie" in lowered
        ):
            errors.append("Gebruik voor gemeente geen BRKRelatie of ceo:gemeentenaam.")

    limit = requested_limit(question)
    if limit is not None and not re.search(rf"\bLIMIT\s+{limit}\b", query, re.IGNORECASE):
        errors.append(f"De gebruiker vraagt exact {limit} resultaten; gebruik LIMIT {limit}.")

    asks_name = bool(re.search(r"\bna(?:am|men)\b", question, re.IGNORECASE))
    asks_address = bool(re.search(r"\badres(?:sen)?\b", question, re.IGNORECASE))
    if limit is not None and "?rm" in query and (asks_name or asks_address):
        exists_count = query.upper().count("FILTER EXISTS")
        if asks_name and exists_count < 1:
            errors.append("Gebruik FILTER EXISTS om alleen monumenten met een naam te selecteren.")
        if asks_address and exists_count < (2 if asks_name else 1):
            errors.append("Gebruik een apart FILTER EXISTS voor een BAG-adres.")
        select_part = re.split(r"\bWHERE\b", query, maxsplit=1, flags=re.IGNORECASE)[0]
        if asks_name and re.search(r"\?naam\b", select_part, re.IGNORECASE):
            errors.append("Projecteer ?naam niet; de backend verrijkt de begrensde URI-set.")
        if asks_address and re.search(r"\?adres\b", select_part, re.IGNORECASE):
            errors.append("Projecteer ?adres niet; de backend verrijkt de begrensde URI-set.")
    return errors


# Onderwerpen die, als de vraag ze noemt, een herkenbaar querypatroon moeten
# opleveren. Dit vangt het geval waarin het LLM een deel van een
# meerledige vraag (bv. gemeente + functie + gezicht) stilzwijgend laat
# vallen in plaats van te combineren.
FUNCTIE_KEYWORDS = (
    "functie", "kerk", "kerkhof", "begraafplaats", "molen", "kasteel",
    "boerderij", "school", "fabriek", "toren", "klooster", "gemaal", "sluis",
)
FUNCTIE_PATHS = ("heeftoorspronkelijkefunctie", "heefthuidigefunctie", "heefttype")

GEZICHT_KEYWORDS = ("gezicht", "stadsgezicht", "dorpsgezicht")
SPATIAL_FUNCTIONS = ("geof:sfwithin", "geof:sfintersects")


def validate_completeness(question: str, query: str) -> list[str]:
    """Controleer of onderdelen die letterlijk in de vraag staan ook in de query terugkomen."""
    q = question.lower()
    lowered = query.lower()
    errors: list[str] = []

    if any(keyword in q for keyword in FUNCTIE_KEYWORDS):
        if not any(path in lowered for path in FUNCTIE_PATHS):
            errors.append(
                "De vraag noemt een functie of type (bv. kerk, molen, school, "
                "of het woord 'functie'), maar de query bevat geen "
                "functie/type-patroon. Voeg ceo:heeftOorspronkelijkeFunctie, "
                "ceo:heeftHuidigeFunctie en/of ceo:heeftType toe (zie FUNCTIE EN "
                "TYPE BIJ RIJKSMONUMENTEN in datamodel_rules.txt)."
            )

    if any(keyword in q for keyword in GEZICHT_KEYWORDS):
        has_gezicht_class = "ceo:gezicht" in lowered
        has_spatial = any(fn in lowered for fn in SPATIAL_FUNCTIONS)
        if not (has_gezicht_class and has_spatial):
            errors.append(
                "De vraag noemt een (beschermd) gezicht, maar de query mist "
                "ceo:Gezicht en/of de ruimtelijke relatie. Voeg ceo:Gezicht toe "
                "en filter met geof:sfWithin of geof:sfIntersects op beide "
                "WKT-geometrieën (zie RIJKSMONUMENTEN BINNEN EEN GEZICHT in "
                "datamodel_rules.txt)."
            )

    return errors


# Woorden die naar drie semantisch incompatibele properties kunnen wijzen --
# zie Type 7 in de ambiguïteitstaxonomie (chat2thedata plan-bestand). Multi-
# woord-frasen, niet kale "type"/"soort"/"aard": die komen te vaak voor in
# ongerelateerde vragen (bv. "type kasteel" bedoelt gewoon een functie, geen
# echte Type-7-vraag).
VAAG_WOORD_FRASEN = (
    "soort monument", "soort rijksmonument", "wat voor soort",
    "monumentaard", "aard van het monument", "welke aard",
)
MONUMENTAARD_PATH = "heeftmonumentaard"
FUNCTIE_PATHS_TYPE7 = ("heeftoorspronkelijkefunctie", "heefthuidigefunctie")
TYPE_PATH = "heefttype"


def describe_property_choice(question: str, query: str) -> str | None:
    """Meld welk property-pad gebruikt is bij een vage "soort/aard/type"-vraag.

    Anders dan validate_semantics/validate_completeness hierboven: dit is geen
    foutenlijst die een correctie-retry triggert. heeftMonumentAard, functie
    en heeftType zijn alle drie geldige lezingen van "soort"/"aard"/"type" --
    er is niets te corrigeren, alleen iets om transparant te maken: welke van
    de drie de gegenereerde query daadwerkelijk gebruikt heeft.
    """
    q = question.lower()
    if not any(frase in q for frase in VAAG_WOORD_FRASEN):
        return None

    lowered = query.lower()
    gebruikt = []
    if MONUMENTAARD_PATH in lowered:
        gebruikt.append("monumentaard (uitsluitend archeologisch/onroerend gebouwd)")
    if any(path in lowered for path in FUNCTIE_PATHS_TYPE7):
        gebruikt.append("functie (bv. kerk, kasteel)")
    if TYPE_PATH in lowered:
        gebruikt.append("type")

    if not gebruikt:
        return None

    return (
        f'"soort"/"aard"/"type" is hier geïnterpreteerd via: {", ".join(gebruikt)}. '
        "Bedoelde je iets anders, stel de vraag dan explicieter (bv. \"functie\" of "
        '"monumentaard" met archeologisch/onroerend gebouwd).'
    )
