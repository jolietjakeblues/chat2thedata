"""
Antwoordgenerator voor de RCE Erfgoed Assistent.

Doel:
- SPARQL JSON-resultaten omzetten naar een kort Nederlands antwoord.
- Geen hallucinerende of afgekaptte LLM-antwoorden.
- Geen lijstresultaten presenteren als echte telling.
- COUNT-resultaten wel als telling behandelen.
- Kaart/geometrievelden negeren in de tekstsamenvatting.

Deze generator is bewust grotendeels deterministisch.
Dat is veiliger voor testwerk met linked data dan elke keer een LLM-samenvatting laten schrijven.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


GEOMETRY_FIELD_HINTS = (
    "wkt",
    "geom",
    "geo",
    "geometry",
    "geometrie",
)

URI_FIELD_HINTS = (
    "rm",
    "cho",
    "object",
    "gezicht",
    "complex",
    "terrein",
    "locatie",
    "vondst",
    "grondspoor",
    "werelderfgoed",
)

NUMBER_FIELDS = (
    "nummer",
    "rijksmonumentnummer",
    "gezichtsnummer",
    "complexnummer",
    "objectnummer",
    "archisnummer",
    "archisnummer",
    "werelderfgoednummer",
)

NAME_FIELDS = (
    "naam",
    "titel",
    "label",
    "gezichtsnaam",
    "monumentnaam",
    "objectnaam",
)

LOCATION_FIELDS = (
    "gemeente",
    "woonplaats",
    "adres",
    "straat",
    "provincie",
)

TYPE_FUNCTION_FIELDS = (
    "functie",
    "functielabel",
    "functienaam",
    "typenaam",
    "type",
    "aard",
    "rol",
    "actor",
    "bron",
)


def _bindings(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Haal SPARQL bindings veilig uit het resultaat."""
    return (
        results
        .get("results", {})
        .get("bindings", [])
        or []
    )


def _vars(results: dict[str, Any]) -> list[str]:
    """Haal variabelen veilig uit het resultaat."""
    return (
        results
        .get("head", {})
        .get("vars", [])
        or []
    )


def _value(row: dict[str, Any], key: str) -> str:
    """Haal een stringwaarde uit een SPARQL binding."""
    value = row.get(key)

    if not value:
        return ""

    return str(value.get("value", "")).strip()


def _values(row: dict[str, Any], keys: list[str] | tuple[str, ...]) -> list[str]:
    """Haal meerdere waarden uit een rij."""
    return [
        _value(row, key)
        for key in keys
        if _value(row, key)
    ]


def _is_geometry_var(var: str) -> bool:
    """Detecteer geometrievelden die niet in tekstsamenvattingen horen."""
    name = var.lower()

    return (
        any(hint in name for hint in GEOMETRY_FIELD_HINTS)
        or name.endswith("wkt")
    )


def _is_uri(value: str) -> bool:
    """Detecteer URI's."""
    return value.startswith("http://") or value.startswith("https://")


def _short_uri(value: str) -> str:
    """Maak URI's kort leesbaar."""
    if not _is_uri(value):
        return value

    parts = [
        part
        for part in value.replace("#", "/").split("/")
        if part
    ]

    return parts[-1] if parts else value


def _clean(value: str, max_len: int = 160) -> str:
    """Maak een waarde geschikt voor in lopende tekst."""
    value = str(value or "").strip()

    if not value:
        return ""

    if _is_uri(value):
        value = _short_uri(value)

    value = " ".join(value.split())

    if len(value) > max_len:
        return value[: max_len - 1].rstrip() + "…"

    return value


def _first_existing_var(vars_: list[str], candidates: tuple[str, ...]) -> str | None:
    """Zoek eerste variabele op naam of substring."""
    lower_to_original = {
        var.lower(): var
        for var in vars_
    }

    for candidate in candidates:
        if candidate.lower() in lower_to_original:
            return lower_to_original[candidate.lower()]

    for var in vars_:
        lower = var.lower()

        if any(candidate.lower() in lower for candidate in candidates):
            return var

    return None


def _visible_vars(vars_: list[str]) -> list[str]:
    """Velden die geschikt zijn voor tekst."""
    visible = []

    for var in vars_:
        lower = var.lower()

        if _is_geometry_var(var):
            continue

        if lower in {"wkt", "rmwkt", "gezichtwkt", "geogez", "geormn"}:
            continue

        visible.append(var)

    return visible


def _dedupe_rows(bindings: list[dict[str, Any]], vars_: list[str]) -> list[dict[str, Any]]:
    """
    Dedupliceer rijen op hoofdobject.

    Bij geospatial queries kan hetzelfde gezicht of monument vaker terugkomen
    door meerdere functies, types of geometrieën.
    """
    preferred_keys = [
        "rm",
        "nummer",
        "rijksmonumentnummer",
        "gezicht",
        "gezichtsnummer",
        "complex",
        "terrein",
        "onderzoeksgebied",
        "werelderfgoed",
        "locatie",
        "vondst",
        "grondspoor",
    ]

    keys = [
        key
        for key in preferred_keys
        if key in vars_
    ]

    if not keys:
        return bindings

    seen: set[tuple[str, ...]] = set()
    unique: list[dict[str, Any]] = []

    for row in bindings:
        marker = tuple(
            _value(row, key)
            for key in keys
            if _value(row, key)
        )

        if not marker:
            unique.append(row)
            continue

        if marker in seen:
            continue

        seen.add(marker)
        unique.append(row)

    return unique


def _format_list(items: list[str], max_items: int = 8) -> str:
    """Maak een korte Nederlandse opsomming."""
    cleaned = [
        _clean(item)
        for item in items
        if _clean(item)
    ]

    seen = []
    for item in cleaned:
        if item not in seen:
            seen.append(item)

    shown = seen[:max_items]

    if not shown:
        return ""

    if len(shown) == 1:
        return shown[0]

    if len(shown) == 2:
        return f"{shown[0]} en {shown[1]}"

    return ", ".join(shown[:-1]) + f" en {shown[-1]}"


def _is_count_result(vars_: list[str], bindings: list[dict[str, Any]]) -> bool:
    """Bepaal of het resultaat een echte telling is."""
    if not bindings:
        return False

    count_like = {
        "aantal",
        "count",
        "total",
        "totaal",
    }

    return any(var.lower() in count_like for var in vars_)


def _number_from_count_result(vars_: list[str], bindings: list[dict[str, Any]]) -> str:
    """Haal de eerste telling uit een COUNT-resultaat."""
    for var in vars_:
        if var.lower() in {"aantal", "count", "total", "totaal"}:
            value = _value(bindings[0], var)

            if value:
                return value

    return ""


def _summarize_count(question: str, results: dict[str, Any]) -> str:
    """Samenvatting voor COUNT-resultaten."""
    vars_ = _vars(results)
    bindings = _bindings(results)

    if not bindings:
        return "De telling gaf geen resultaat terug."

    count_var = None
    for var in vars_:
        if var.lower() in {"aantal", "count", "total", "totaal"}:
            count_var = var
            break

    if not count_var:
        return "De telling gaf een resultaat terug, maar er is geen herkenbare aantalkolom gevonden."

    group_vars = [
        var
        for var in vars_
        if var != count_var
    ]

    if not group_vars:
        aantal = _value(bindings[0], count_var)

        if not aantal:
            return "De telling gaf geen herkenbaar aantal terug."

        return f"De telling geeft {aantal} als resultaat."

    group_var = group_vars[0]
    rows = []

    for row in bindings[:10]:
        label = _clean(_value(row, group_var))
        aantal = _clean(_value(row, count_var))

        if label and aantal:
            rows.append(f"{label}: {aantal}")

    if not rows:
        return "De gegroepeerde telling gaf resultaten terug, maar zonder goed leesbare labels."

    total_groups = len(bindings)
    shown = "; ".join(rows)

    if total_groups > 10:
        return f"De telling geeft {total_groups} groepen terug. De eerste groepen zijn: {shown}."

    return f"De telling geeft {total_groups} groepen terug: {shown}."


def _summarize_geospatial_list(question: str, vars_: list[str], bindings: list[dict[str, Any]]) -> str | None:
    """Speciale samenvatting voor vragen met gezichten/gebieden en monumenten."""
    has_gezicht = "gezicht" in vars_ or "gezichtsnaam" in vars_ or "gezichtsnummer" in vars_
    has_rm = "rm" in vars_ or "nummer" in vars_
    has_geometry = any(_is_geometry_var(var) for var in vars_)

    if not (has_gezicht and has_rm and has_geometry):
        return None

    unique_monuments = _dedupe_rows(bindings, vars_)

    name_var = _first_existing_var(vars_, ("naam", "monumentnaam", "label"))
    number_var = _first_existing_var(vars_, ("nummer", "rijksmonumentnummer"))
    face_name_var = _first_existing_var(vars_, ("gezichtsnaam",))
    face_number_var = _first_existing_var(vars_, ("gezichtsnummer",))
    function_var = _first_existing_var(vars_, ("functieLabel", "functie", "functienaam", "typeNaam", "rol"))
    source_var = _first_existing_var(vars_, ("bron",))

    faces = []
    for row in bindings:
      name = _value(row, face_name_var) if face_name_var else ""
      number = _value(row, face_number_var) if face_number_var else ""

      if name and number:
          faces.append(f"{name} ({number})")
      elif name:
          faces.append(name)
      elif number:
          faces.append(number)

    monument_labels = []

    for row in unique_monuments:
        name = _value(row, name_var) if name_var else ""
        number = _value(row, number_var) if number_var else ""

        if name and number:
            monument_labels.append(f"{name} ({number})")
        elif name:
            monument_labels.append(name)
        elif number:
            monument_labels.append(number)

    face_text = _format_list(faces, max_items=4)
    monument_text = _format_list(monument_labels, max_items=8)

    parts = [
        f"De query gaf {len(bindings)} rijen terug.",
    ]

    if len(unique_monuments) != len(bindings):
        parts.append(f"Na ontdubbeling gaat het om {len(unique_monuments)} unieke rijksmonumenten.")

    if face_text:
        parts.append(f"Ze liggen binnen deze gezichten: {face_text}.")

    if monument_text:
        parts.append(f"Voorbeelden zijn: {monument_text}.")

    if function_var:
        functions = [
            _value(row, function_var)
            for row in bindings
            if _value(row, function_var)
        ]

        function_text = _format_list(functions, max_items=5)

        if function_text:
            parts.append(f"De gevonden functie/type-informatie bevat: {function_text}.")

    if source_var:
        sources = [
            _value(row, source_var)
            for row in bindings
            if _value(row, source_var)
        ]

        source_text = _format_list(sources, max_items=4)

        if source_text:
            parts.append(f"Bron van de match: {source_text}.")

    parts.append("De kaart gebruikt de WKT-geometrie uit de resultaten.")

    return " ".join(parts)


def _summarize_generic_list(question: str, vars_: list[str], bindings: list[dict[str, Any]]) -> str:
    """Algemene samenvatting voor lijstresultaten."""
    visible_vars = _visible_vars(vars_)
    unique_rows = _dedupe_rows(bindings, vars_)

    name_var = _first_existing_var(visible_vars, NAME_FIELDS)
    number_var = _first_existing_var(visible_vars, NUMBER_FIELDS)
    location_var = _first_existing_var(visible_vars, LOCATION_FIELDS)
    type_var = _first_existing_var(visible_vars, TYPE_FUNCTION_FIELDS)
    source_var = _first_existing_var(visible_vars, ("bron",))

    labels = []

    for row in unique_rows:
        name = _value(row, name_var) if name_var else ""
        number = _value(row, number_var) if number_var else ""

        if name and number:
            labels.append(f"{name} ({number})")
        elif name:
            labels.append(name)
        elif number:
            labels.append(number)

    parts = [
        f"De query gaf {len(bindings)} rijen terug.",
    ]

    if len(unique_rows) != len(bindings):
        parts.append(f"Na ontdubbeling blijven {len(unique_rows)} unieke resultaten over.")

    label_text = _format_list(labels, max_items=8)

    if label_text:
        parts.append(f"Voorbeelden zijn: {label_text}.")

    if location_var:
        locations = [
            _value(row, location_var)
            for row in bindings
            if _value(row, location_var)
        ]

        location_text = _format_list(locations, max_items=6)

        if location_text:
            parts.append(f"Locaties in de resultaten: {location_text}.")

    if type_var:
        types = [
            _value(row, type_var)
            for row in bindings
            if _value(row, type_var)
        ]

        type_text = _format_list(types, max_items=6)

        if type_text:
            parts.append(f"Gevonden kenmerken: {type_text}.")

    if source_var:
        sources = [
            _value(row, source_var)
            for row in bindings
            if _value(row, source_var)
        ]

        source_text = _format_list(sources, max_items=4)

        if source_text:
            parts.append(f"Bron van de match: {source_text}.")

    if any(_is_geometry_var(var) for var in vars_):
        parts.append("Er is geometrie aanwezig, dus de resultaten kunnen op de kaart worden getoond.")

    parts.append("Let op: bij een lijstquery is dit het aantal opgehaalde rijen, niet automatisch het totale aantal in de dataset.")

    return " ".join(parts)


def generate(question: str, results: dict[str, Any], caveat: str | None = None) -> str:
    """
    Genereer een Nederlands antwoord bij SPARQL JSON-resultaten.

    Args:
        question: De oorspronkelijke gebruikersvraag.
        results: SPARQL JSON result dict.
        caveat: Optionele kanttekening (bv. van een gekozen deelinterpretatie
            uit sparql/answerability.py) die achter het antwoord getoond wordt.

    Returns:
        Een korte, volledige Nederlandse tekst.
    """
    vars_ = _vars(results)
    bindings = _bindings(results)

    if not results:
        answer = "Er zijn geen SPARQL-resultaten ontvangen."
    elif not bindings:
        answer = "Ik vond geen resultaten voor deze vraag. Probeer eventueel een bredere vraag of controleer de gegenereerde SPARQL-query."
    elif _is_count_result(vars_, bindings):
        answer = _summarize_count(question, results)
    else:
        geospatial_summary = _summarize_geospatial_list(question, vars_, bindings)
        answer = geospatial_summary or _summarize_generic_list(question, vars_, bindings)

    if results.get("incomplete_due_to_limit"):
        answer += (
            "\n\nLet op: de ruimtelijke berekening moest terugvallen op een lokale "
            "benadering en het kandidaatveld raakte daarbij een bovengrens — dit "
            "resultaat kan onvolledig zijn."
        )

    if caveat:
        answer = f"{answer}\n\nLet op: {caveat}"

    return answer
