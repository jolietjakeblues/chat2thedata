"""
Configuratie — laadt .env en exporteert alle constanten.
Kopieer .env.example naar .env en vul je waarden in.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# LLM provider: 'anthropic', 'google' of 'ollama'
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:14b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "12000"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "1800"))

def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Omgevingsvariabele '{key}' is niet ingesteld. "
            f"Kopieer .env.example naar .env en vul hem in."
        )
    return value



ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

_LEGACY_SPARQL_ENDPOINT = (
    "https://api.linkeddata.cultureelerfgoed.nl/"
    "datasets/rce/cho/services/cho/sparql"
)
_DEFAULT_SPARQL_ENDPOINT = (
    "https://api.linkeddata.cultureelerfgoed.nl/datasets/rce/cho/sparql"
)
SPARQL_ENDPOINT = os.getenv(
    "SPARQL_ENDPOINT",
    _DEFAULT_SPARQL_ENDPOINT,
).strip().rstrip("/")
if SPARQL_ENDPOINT == _LEGACY_SPARQL_ENDPOINT:
    SPARQL_ENDPOINT = _DEFAULT_SPARQL_ENDPOINT
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-1.5-pro")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# Bescherming tegen misbruik van de (betaalde) LLM-provider en het endpoint
MAX_QUESTION_LENGTH = int(os.getenv("MAX_QUESTION_LENGTH", "500"))
APP_ACCESS_CODE = os.getenv("APP_ACCESS_CODE", "")
RATE_LIMIT_LLM = os.getenv("RATE_LIMIT_LLM", "10/minute;100/day")
RATE_LIMIT_SPARQL = os.getenv("RATE_LIMIT_SPARQL", "30/minute;300/day")

# Error tracking. Leeg = uitgeschakeld (zelfde patroon als APP_ACCESS_CODE).
SENTRY_DSN = os.getenv("SENTRY_DSN", "")


def active_api_key() -> str:
    """Geef de API-key van de actieve provider terug; Ollama heeft geen key."""
    provider = LLM_PROVIDER.strip().lower()
    if provider == "ollama":
        return ""
    if provider == "google":
        return GOOGLE_API_KEY
    if provider == "anthropic":
        return ANTHROPIC_API_KEY
    raise EnvironmentError(
        "Ongeldige LLM_PROVIDER. Gebruik 'ollama', 'google' of 'anthropic'."
    )


def require_active_api_key() -> str:
    """Valideer de cloudprovider en bijbehorende API-key."""
    provider = LLM_PROVIDER.strip().lower()
    value = active_api_key()
    if provider == "ollama":
        return ""
    if not value:
        key = "GOOGLE_API_KEY" if provider == "google" else "ANTHROPIC_API_KEY"
        raise EnvironmentError(f"Omgevingsvariabele '{key}' is niet ingesteld.")
    return value

SPARQL_PREFIXES = """\
PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#>
PREFIX graph: <https://linkeddata.cultureelerfgoed.nl/graph/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX gsp: <http://www.opengis.net/ont/geosparql#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>"""

# Exacte provincie URIs uit de OWMS thesaurus
# Alle gangbare spelwijzen mappen naar één URI per provincie
_BASE = "http://standaarden.overheid.nl/owms/terms/"
# Omgekeerde mapping: URI → leesbare naam
PROVINCIE_NAAM = {
    "http://standaarden.overheid.nl/owms/terms/Drenthe":              "Drenthe",
    "http://standaarden.overheid.nl/owms/terms/Flevoland":            "Flevoland",
    "http://standaarden.overheid.nl/owms/terms/Fryslan":              "Fryslân",
    "http://standaarden.overheid.nl/owms/terms/Gelderland":           "Gelderland",
    "http://standaarden.overheid.nl/owms/terms/Groningen_(provincie)":"Groningen",
    "http://standaarden.overheid.nl/owms/terms/Limburg":              "Limburg",
    "http://standaarden.overheid.nl/owms/terms/Noord-Brabant":        "Noord-Brabant",
    "http://standaarden.overheid.nl/owms/terms/Noord-Holland":        "Noord-Holland",
    "http://standaarden.overheid.nl/owms/terms/Overijssel":           "Overijssel",
    "http://standaarden.overheid.nl/owms/terms/Utrecht_(provincie)":  "Utrecht",
    "http://standaarden.overheid.nl/owms/terms/Zeeland":              "Zeeland",
    "http://standaarden.overheid.nl/owms/terms/Zuid-Holland":         "Zuid-Holland",
}

PROVINCIE_URI = {
    "drenthe":              _BASE + "Drenthe",
    "flevoland":            _BASE + "Flevoland",
    "friesland":            _BASE + "Fryslan",
    "fryslan":              _BASE + "Fryslan",
    "fryslân":              _BASE + "Fryslan",
    "frysland":             _BASE + "Fryslan",
    "gelderland":           _BASE + "Gelderland",
    "groningen":            _BASE + "Groningen_(provincie)",
    "provincie groningen":  _BASE + "Groningen_(provincie)",
    "limburg":              _BASE + "Limburg",
    "noord-brabant":        _BASE + "Noord-Brabant",
    "noord brabant":        _BASE + "Noord-Brabant",
    "noordbrabant":         _BASE + "Noord-Brabant",
    "n-brabant":            _BASE + "Noord-Brabant",
    "brabant":              _BASE + "Noord-Brabant",
    "noord-holland":        _BASE + "Noord-Holland",
    "noord holland":        _BASE + "Noord-Holland",
    "noordholland":         _BASE + "Noord-Holland",
    "n-holland":            _BASE + "Noord-Holland",
    "overijssel":           _BASE + "Overijssel",
    "utrecht":              _BASE + "Utrecht_(provincie)",
    "provincie utrecht":    _BASE + "Utrecht_(provincie)",
    "zeeland":              _BASE + "Zeeland",
    "zuid-holland":         _BASE + "Zuid-Holland",
    "zuid holland":         _BASE + "Zuid-Holland",
    "zuidholland":          _BASE + "Zuid-Holland",
    "z-holland":            _BASE + "Zuid-Holland",
}
