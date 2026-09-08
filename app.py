"""
RCE Erfgoed Assistent — Flask applicatie

Start:
    pip install -r requirements.txt
    cp .env.example .env
    python app.py
"""

import logging
import os

import requests
import sentry_sdk
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sentry_sdk.integrations.flask import FlaskIntegration
from werkzeug.middleware.proxy_fix import ProxyFix

import config
from answer import answer_generator
from sparql import answerability
from sparql import executor as sparql_executor
from sparql import semantic_resolver
from sparql import sparql_generator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)

if config.SENTRY_DSN:
    # traces_sample_rate=0: alleen fouten, geen performance-tracing (geen
    # reden om daar nu al voor te betalen/data over te verzamelen).
    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.0,
    )


def current_model_name() -> str:
    """Geef de actieve modelnaam terug op basis van LLM_PROVIDER."""

    provider = config.LLM_PROVIDER.lower()

    if provider == "ollama":
        return config.OLLAMA_MODEL

    if provider == "google":
        return config.GOOGLE_MODEL

    if provider == "anthropic":
        return config.ANTHROPIC_MODEL

    return "onbekend"


def api_key_available() -> bool:
    """Controleer of de gekozen provider een API key nodig heeft en heeft."""

    provider = config.LLM_PROVIDER.lower()

    if provider == "ollama":
        return True

    if provider == "google":
        return bool(config.GOOGLE_API_KEY)

    if provider == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)

    return False


app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(__file__), "frontend"),
    static_url_path="",
)

# De frontend wordt door deze app zelf geserveerd (same-origin fetch), dus
# CORS voor andere origins is niet nodig en staat expres niet aan.

# Render (en de meeste PaaS-providers) zetten de app achter één reverse
# proxy; ProxyFix zorgt dat request.remote_addr het echte client-IP is
# (uit X-Forwarded-For) in plaats van het interne proxy-IP. Zonder dit
# zou rate limiting per IP feitelijk één gedeelde limiet voor alle
# bezoekers worden.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")


@app.errorhandler(429)
def _rate_limit_exceeded(e):
    return jsonify({"error": "Te veel verzoeken. Probeer het later opnieuw."}), 429


def _access_code_ok() -> bool:
    """Controleer de optionele toegangscode (APP_ACCESS_CODE)."""
    if not config.APP_ACCESS_CODE:
        return True
    return request.headers.get("X-Access-Code", "") == config.APP_ACCESS_CODE


@app.before_request
def _require_access_code():
    if request.path.startswith("/api/") and request.path != "/api/health":
        if not _access_code_ok():
            return jsonify({"error": "Ongeldige of ontbrekende toegangscode"}), 401


def _json_object():
    """Lees een JSON-object of geef een duidelijke 400-respons."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Verwacht een JSON-object"}), 400)
    return data, None


@app.route("/")
def index():
    """Serveer de frontend."""
    return app.send_static_file("index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health check voor backend, provider, model en endpoint."""

    return jsonify(
        {
            "status": "ok",
            "provider": config.LLM_PROVIDER,
            "api_key_set": api_key_available(),
            "model": current_model_name(),
            "endpoint": config.SPARQL_ENDPOINT,
        }
    )

def detect_mode(question: str) -> str:
    q = question.lower()

    telling_triggers = [
        "hoeveel",
        "aantal",
        "tel ",
        "telling",
        "how many",
        "count",
    ]

    if any(x in q for x in telling_triggers):
        return "telling"

    return "lijst"


@app.route("/api/generate-sparql", methods=["POST"])
@limiter.limit(config.RATE_LIMIT_LLM)
def generate_sparql():
    """Stap 1: vertaal natuurlijke vraag naar SPARQL."""

    data, error = _json_object()
    if error:
        return error
    question = str(data.get("question") or "").strip()
    frontend_mode = data.get("mode")
    disambiguation = data.get("disambiguation")
    if not isinstance(disambiguation, dict):
        disambiguation = None
    limitation_choice = data.get("limitation_choice")
    if not isinstance(limitation_choice, str) or not limitation_choice:
        limitation_choice = None

    detected_mode = detect_mode(question)
    mode = "telling" if detected_mode == "telling" else (frontend_mode or "lijst")

    if not question:
        return jsonify({"error": "Geen vraag opgegeven"}), 400

    if len(question) > config.MAX_QUESTION_LENGTH:
        return (
            jsonify(
                {"error": f"Vraag is te lang (max {config.MAX_QUESTION_LENGTH} tekens)."}
            ),
            400,
        )

    if mode not in ("lijst", "telling"):
        return jsonify({"error": "Ongeldige modus. Gebruik 'lijst' of 'telling'."}), 400

    try:
        result = sparql_generator.generate(question, mode, disambiguation, limitation_choice)
        return jsonify({"query": result.query, "caveat": result.caveat})

    except sparql_generator.ClarificationNeeded as exc:
        return jsonify({"clarification": semantic_resolver.describe_ambiguity(exc.ambiguous)})

    except sparql_generator.AnswerabilityLimitationNeeded as exc:
        return jsonify({"clarification": answerability.describe_limitation(exc.limitation)})

    except Exception as e:
        logger.exception("Fout bij SPARQL generatie")
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/execute-sparql", methods=["POST"])
@limiter.limit(config.RATE_LIMIT_SPARQL)
def execute_sparql():
    """Stap 2: voer SPARQL query uit op het RCE endpoint."""

    data, error = _json_object()
    if error:
        return error
    query = str(data.get("query") or "").strip()
    question = str(data.get("question") or "").strip()

    if not query:
        return jsonify({"error": "Geen query opgegeven"}), 400

    try:
        results = sparql_executor.execute(query, question=question)
        return jsonify(results)

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    except requests.exceptions.Timeout:
        if sparql_executor.has_spatial_filter(query):
            return (
                jsonify(
                    {
                        "error": (
                            "De ruimtelijke vergelijking (binnen een gezicht/gebied) "
                            "kostte te veel tijd, ook na een lokale herberekening. "
                            "Probeer de vraag specifieker te maken, bijvoorbeeld met "
                            "één gezicht of gemeente tegelijk."
                        )
                    }
                ),
                504,
            )
        return jsonify({"error": "Endpoint timeout (>30s)"}), 504

    except requests.exceptions.HTTPError as e:
        if sparql_executor.is_spatial_error(e) and sparql_executor.has_spatial_filter(query):
            return (
                jsonify(
                    {
                        "error": (
                            "De ruimtelijke vergelijking (binnen een gezicht/gebied) "
                            "faalde door een fout in de brongeometrie, ook na een "
                            "lokale herberekening. Probeer de vraag specifieker te "
                            "maken, bijvoorbeeld met één gezicht of gemeente tegelijk."
                        )
                    }
                ),
                502,
            )
        return (
            jsonify(
                {
                    "error": (
                        f"Endpoint fout: {e.response.status_code} — "
                        f"{e.response.text[:300]}"
                    )
                }
            ),
            502,
        )

    except Exception as e:
        logger.exception("Fout bij SPARQL uitvoering")
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-answer", methods=["POST"])
@limiter.limit(config.RATE_LIMIT_LLM)
def generate_answer():
    """Stap 3: vertaal SPARQL resultaten naar leesbaar antwoord."""

    data, error = _json_object()
    if error:
        return error
    question = str(data.get("question") or "").strip()
    if len(question) > config.MAX_QUESTION_LENGTH:
        return (
            jsonify(
                {"error": f"Vraag is te lang (max {config.MAX_QUESTION_LENGTH} tekens)."}
            ),
            400,
        )
    results = data.get("results", {})
    if not isinstance(results, dict):
        return jsonify({"error": "'results' moet een JSON-object zijn"}), 400
    caveat = data.get("caveat")
    if not isinstance(caveat, str) or not caveat:
        caveat = None

    try:
        answer = answer_generator.generate(question, results, caveat=caveat)
        return jsonify({"answer": answer})

    except Exception as e:
        logger.exception("Fout bij antwoord generatie")
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", config.FLASK_PORT))

    logger.info("SPARQL endpoint: %s", config.SPARQL_ENDPOINT)
    logger.info("Provider: %s", config.LLM_PROVIDER)
    logger.info("Model: %s", current_model_name())
    logger.info("Server start op poort %d", port)

    app.run(
        host="0.0.0.0",
        debug=config.FLASK_DEBUG,
        port=port,
    )