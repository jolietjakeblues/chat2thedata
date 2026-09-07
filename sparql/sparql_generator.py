"""
SPARQL query generator — ondersteunt Anthropic, Google Gemini en Ollama.
Provider wordt bepaald via LLM_PROVIDER in config/environment.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import config
from sparql.answerability import detect_limitation
from sparql.postprocess import postprocess, has_count
from sparql.semantic_resolver import ResolutionResult, build_semantic_context, resolve_question
from sparql.semantic_validator import validate_completeness, validate_semantics

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class ClarificationNeeded(Exception):
    """De vraag is echt dubbelzinnig; de gebruiker moet eerst kiezen."""

    def __init__(self, ambiguous):
        self.ambiguous = ambiguous
        super().__init__("Vraag is dubbelzinnig, verduidelijking nodig")


class AnswerabilityLimitationNeeded(Exception):
    """De vraag heeft geen eenduidige SPARQL-vertaling; leg de beperking uit."""

    def __init__(self, limitation):
        self.limitation = limitation
        super().__init__("Vraag heeft geen eenduidige SPARQL-vertaling")


@dataclass(frozen=True)
class GenerationResult:
    query: str
    caveat: str | None = None


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _load_optional_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"

    if not path.exists():
        logger.warning("Optioneel promptbestand ontbreekt: %s", path)
        return ""

    return path.read_text(encoding="utf-8")


def _build_system_prompt(mode: str) -> str:
    """
    Bouw de volledige system prompt.

    De basisprompt bepaalt de modus:
    - lijst
    - telling

    datamodel_rules.txt bevat harde regels uit de CEO ontologie en uit bewezen instance-patronen.
    Die regels beperken hallucinaties in classes, properties en property-paden.
    """

    base_prompt = _load_prompt(mode)
    datamodel_rules = _load_optional_prompt("datamodel_rules")

    parts = [base_prompt]

    if datamodel_rules.strip():
        parts.append(datamodel_rules)

    return "\n\n".join(parts)


def _generate_anthropic(question: str, system_prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.require_active_api_key())

    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1200,
        system=system_prompt,
        messages=[{"role": "user", "content": question}],
    )

    return message.content[0].text


def _generate_google(question: str, system_prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=config.require_active_api_key())

    model = genai.GenerativeModel(
        model_name=config.GOOGLE_MODEL,
        system_instruction=system_prompt,
    )

    response = model.generate_content(question)

    return response.text


def _generate_ollama(question: str, system_prompt: str) -> str:
    import ollama

    response = ollama.chat(
        model=config.OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        options={
            "temperature": 0,
            "num_ctx": int(getattr(config, "OLLAMA_NUM_CTX", 12000)),
            "num_predict": int(getattr(config, "OLLAMA_NUM_PREDICT", 1800)),
        },
    )

    return response["message"]["content"]


def _generate(question: str, system_prompt: str) -> str:
    provider = config.LLM_PROVIDER.lower()

    if provider == "ollama":
        return _generate_ollama(question, system_prompt)

    if provider == "google":
        return _generate_google(question, system_prompt)

    if provider == "anthropic":
        return _generate_anthropic(question, system_prompt)

    raise ValueError(
        f"Onbekende LLM_PROVIDER: {config.LLM_PROVIDER}. "
        "Gebruik 'ollama', 'google' of 'anthropic'."
    )


def generate(
    question: str,
    mode: str,
    disambiguation: dict[str, str] | None = None,
    limitation_choice: str | None = None,
) -> GenerationResult:
    """
    Genereer een SPARQL query op basis van een natuurlijke vraag.

    Args:
        question:          De vraag in natuurlijke taal.
        mode:              'lijst' of 'telling'.
        disambiguation:    Optionele keuze uit een eerdere entiteits-clarificatie
                            (genormaliseerd label -> "gemeente"/"provincie").
        limitation_choice: Optionele keuze uit een eerdere beperkings-clarificatie
                            (id van de gekozen PartialOption).

    Returns:
        GenerationResult met de nabewerkte SPARQL query en, als een
        deelinterpretatie is gekozen, de bijbehorende kanttekening.

    Raises:
        ClarificationNeeded: als de vraag een naam bevat die zowel gemeente
            als provincie kan zijn en er geen expliciet "gemeente"/"provincie"
            in de vraag staat, en er (nog) geen disambiguation is opgegeven.
        AnswerabilityLimitationNeeded: als de vraag geen eenduidige SPARQL-
            vertaling heeft (zie sparql/answerability.py) en er nog geen
            limitation_choice is opgegeven.
    """

    system_prompt = _build_system_prompt(mode)

    try:
        resolution = resolve_question(question, disambiguation)
    except RuntimeError as exc:
        logger.warning("Gemeente/provincie-resolutie overgeslagen: %s", exc)
        resolution = ResolutionResult()

    if resolution.has_ambiguity:
        raise ClarificationNeeded(resolution.ambiguous)

    caveat = None
    chosen_hint = None

    limitation = detect_limitation(question)
    if limitation is not None:
        if limitation_choice is None:
            raise AnswerabilityLimitationNeeded(limitation)

        chosen = next(
            (o for o in limitation.partial_options if o.id == limitation_choice), None
        )
        if chosen is not None:
            caveat = chosen.caveat
            chosen_hint = chosen.prompt_hint

    resolved_terms = resolution.resolved
    semantic_context = build_semantic_context(resolved_terms)
    prompt_input = f"{question}\n\n{semantic_context}" if semantic_context else question

    if chosen_hint:
        prompt_input = f"{prompt_input}\n\n{chosen_hint}"

    logger.info(
        "Query genereren via %s (modus: %s)",
        config.LLM_PROVIDER,
        mode,
    )

    query = _generate(prompt_input, system_prompt)
    query = postprocess(query, mode)

    if mode == "lijst" and has_count(query):
        logger.warning("LLM genereerde COUNT in lijstmodus — correctie-aanroep")

        corrected = (
            prompt_input
            + " (geef een lijst van individuele resultaten, geen telling)"
        )

        query = _generate(corrected, system_prompt)
        query = postprocess(query, mode)

    semantic_errors = validate_semantics(question, query, resolved_terms)
    completeness_errors = validate_completeness(question, query)
    all_errors = semantic_errors + completeness_errors

    if all_errors:
        logger.warning("Validatie gaf correcties: %s", all_errors)

        corrected = (
            prompt_input
            + "\n\nCORRIGEER DE VORIGE QUERY, DEZE MISTE ONDERDELEN UIT DE VRAAG:\n- "
            + "\n- ".join(all_errors)
        )

        query = _generate(corrected, system_prompt)
        query = postprocess(query, mode)

    logger.info("Query gegenereerd (%d tekens)", len(query))

    return GenerationResult(query=query, caveat=caveat)
