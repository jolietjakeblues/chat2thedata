# RCE Erfgoed Assistent

[![Continuous integration](https://github.com/jolietjakeblues/chat2thedata/actions/workflows/ci.yml/badge.svg)](https://github.com/jolietjakeblues/chat2thedata/actions/workflows/ci.yml)

Een webapplicatie waarmee je in gewone taal vragen kunt stellen aan de linked data van de Rijksdienst voor het Cultureel Erfgoed (RCE).

De applicatie vertaalt Nederlandse vragen automatisch naar SPARQL, bevraagt het RCE endpoint en geeft een leesbaar antwoord terug.

De app ondersteunt meerdere LLM-providers:

- Ollama (lokaal, geen API-kosten)
- Anthropic Claude
- Google Gemini

De applicatie is geoptimaliseerd voor de Cultureel Erfgoed Ontologie (CEO) en bevat extra datamodelregels om betere SPARQL queries te genereren.

**Live demo:** https://chat2thedata.onrender.com

---

# Functionaliteit

- Stel vragen in natuurlijke taal
- Genereer automatisch SPARQL queries
- Bekijk en bewerk SPARQL queries
- Voer SPARQL direct uit op het RCE endpoint
- Krijg een leesbaar Nederlands antwoord
- Exporteer resultaten als CSV
- Interactieve kaartweergave via Leaflet
- Automatische herkenning van WKT-geometrie
- Ondersteuning voor ruimtelijke queries (geof:sfWithin, geof:sfIntersects), met automatische lokale fallback bij endpointfouten (zie [Ruimtelijke queries en foutafhandeling](#ruimtelijke-queries-en-foutafhandeling))
- Automatische detectie van lijst- of tellingvragen
- Automatische resolutie van gemeente-/provincienamen naar de officiële OWMS-URI
- Bij een plaatsnaam die zowel gemeente als provincie kan zijn (bv. "Utrecht"), vraagt de app het na met een verduidelijkingskaart in plaats van stil te kiezen — behalve als de vraag zelf al "gemeente" of "provincie" bevat
- Rate limiting, optionele toegangscode en een maximale vraaglengte om misbruik van de (betaalde) LLM-provider te voorkomen

Ondersteunde objecttypen:

- Rijksmonumenten
- Complexen
- Archeologische complexen
- Archeologische terreinen
- Archeologische onderzoeksgebieden
- Vondsten
- Grondsporen
- Functies
- Actoren
- Materialen
- Stijlen
- Beschermde gezichten
- Werelderfgoed
- Vondstlocaties

**Screenshot:** _nog toe te voegen — sla een screenshot van de kaartweergave op als `docs/screenshot.png` en verwijs er hier met `![Kaartweergave](docs/screenshot.png)` naar._

---

# Voorbeeldvragen

## Rijksmonumenten

- Welke rijksmonumenten staan er in Zeist?
- Hoeveel kerken zijn er in Utrecht?
- Welke archeologische rijksmonumenten zijn er in Utrecht?
- Welke kastelen staan er in Gelderland?
- Welke rijksmonumenten liggen binnen beschermd gezicht Dordrecht?
- Welke kerken liggen binnen een beschermd gezicht?
- Welke werelderfgoedlocaties zijn er?

## Architectuur

- Wie is de architect van het Rijksmuseum?
- Welke monumenten zijn ontworpen door Cuypers?
- Welke monumenten hebben een neogotische stijl?

## Archeologie

- Welke archeologische complexen zijn er?
- Welke archeologische terreinen zijn er in Limburg?
- Welke vondsten bevatten aardewerk?
- Welke grondsporen horen bij een vondstlocatie?
- Toon archeologische terreinen op de kaart
- Welke Romeinse vondsten liggen in Nuth?

---

# Architectuur

```text
frontend/
  index.html                 — interface met kaartweergave (enige frontend, geserveerd door Flask)
  vendor/leaflet/             — gevendorde Leaflet-assets

answer/
  answer_generator.py        — genereert leesbare antwoorden (grotendeels deterministisch)

sparql/
  executor.py                 — voert SPARQL queries uit, incl. ruimtelijke fallback
  spatial.py                  — lokale ruimtelijke join met Shapely (fallback bij TopologyException/timeout)
  postprocess.py               — normalisatie en correcties op gegenereerde queries
  sparql_generator.py          — genereert SPARQL via LLM, incl. semantische validatie/correctie
  semantic_resolver.py         — resolveert gemeente-/provincienamen naar OWMS-URI's
  semantic_validator.py        — controleert of de query klopt met de opgeloste termen en de vraag compleet dekt

  prompts/
    lijst.txt                 — regels voor lijstqueries
    telling.txt                — regels voor tellingqueries
    datamodel_rules.txt        — centrale CEO-kennisbasis

tests/
  test_regressions.py          — regressietests (pytest/unittest)
  test_semantics.py            — resolver-/validator-tests op semantisch niveau
  test_sparql_generator.py     — tests voor de generatiepijplijn incl. verduidelijkingsstap

config.py                      — configuratie, laadt .env
app.py                          — Flask backend
requirements.txt                — Python dependencies (productie)
requirements-dev.txt            — dev-dependencies (o.a. pytest)
.env.example                    — voorbeeldconfiguratie
LICENSE                         — MIT-licentietekst
```

---

# Vereisten

- Python 3.10+
- Git
- Ollama (optioneel, voor lokaal gebruik zonder API-kosten)

---

# Installatie

## Repository clonen

```bash
git clone https://github.com/jolietjakeblues/chat2thedata.git
cd chat2thedata
```

## Virtual environment aanmaken

```powershell
python -m venv .venv
```

## Virtual environment activeren

### Windows PowerShell

```powershell
.venv\Scripts\activate
```

### Linux/macOS

```bash
source .venv/bin/activate
```

## Dependencies installeren

```powershell
pip install -r requirements.txt
```

Voor ontwikkelwerk (inclusief pytest voor de testsuite):

```powershell
pip install -r requirements-dev.txt
```

---

# Ollama installeren

Download Ollama:

https://ollama.com/download

Controleer daarna:

```powershell
ollama list
```

Installeer bijvoorbeeld:

```powershell
ollama pull qwen2.5-coder:14b
```

---

# Configuratie

Kopieer eerst:

```powershell
copy .env.example .env
```

## Alle environment variables

| Variabele | Verplicht | Default | Omschrijving |
|---|---|---|---|
| `LLM_PROVIDER` | nee | `ollama` | `ollama`, `anthropic` of `google` |
| `OLLAMA_BASE_URL` | nee | `http://localhost:11434` | Alleen bij `LLM_PROVIDER=ollama` |
| `OLLAMA_MODEL` | nee | `qwen2.5-coder:14b` | Alleen bij `LLM_PROVIDER=ollama` |
| `OLLAMA_NUM_CTX` | nee | `12000` | Context-venster voor Ollama |
| `OLLAMA_NUM_PREDICT` | nee | `1800` | Max. output-tokens voor Ollama |
| `ANTHROPIC_API_KEY` | ja, bij `LLM_PROVIDER=anthropic` | — | API-key, kost geld per aanroep |
| `ANTHROPIC_MODEL` | nee | `claude-sonnet-4-5` | Alleen bij `LLM_PROVIDER=anthropic` |
| `GOOGLE_API_KEY` | ja, bij `LLM_PROVIDER=google` | — | API-key, kost geld per aanroep |
| `GOOGLE_MODEL` | nee | `gemini-1.5-pro` | Alleen bij `LLM_PROVIDER=google` |
| `SPARQL_ENDPOINT` | nee | `https://api.linkeddata.cultureelerfgoed.nl/datasets/rce/cho/sparql` | Het RCE CHO SPARQL-endpoint |
| `FLASK_PORT` | nee | `5000` | Lokale poort; Render zet zelf `PORT` |
| `FLASK_DEBUG` | nee | `false` | **Nooit `true` zetten in een publieke deploy** — de Werkzeug-debugger geeft dan externe code-executie |
| `MAX_QUESTION_LENGTH` | nee | `500` | Max. lengte van een vraag, tegen misbruik |
| `APP_ACCESS_CODE` | nee | leeg (uit) | Vul in om een toegangscode te verplichten; de frontend vraagt er dan automatisch om |
| `RATE_LIMIT_LLM` | nee | `10/minute;100/day` | Rate limit per IP op `/api/generate-sparql` en `/api/generate-answer` |
| `RATE_LIMIT_SPARQL` | nee | `30/minute;300/day` | Rate limit per IP op `/api/execute-sparql` |
| `SENTRY_DSN` | nee | leeg (uit) | Vul in om onverwachte 500's automatisch naar Sentry te sturen |

---

# Applicatie starten

```powershell
python app.py
```

Open daarna:

http://127.0.0.1:5000

---

# Testen

```powershell
pip install -r requirements-dev.txt
pytest tests/ -v
```

De testsuite draait ook zonder `pytest` via de standaardbibliotheek:

```powershell
python -m unittest discover -s tests -v
```

De tests hebben geen draaiende Ollama-instantie of geldige API-key nodig — `LLM_PROVIDER` wordt in de tests op `ollama` gezet en de LLM-aanroepen zelf worden niet getest, alleen de generatie-, validatie- en foutafhandelingslogica eromheen.

---

# Deployment met Render

1. Maak op [render.com](https://render.com) een nieuwe **Web Service** aan, gekoppeld aan deze GitHub-repository.
2. **Build command:** `pip install -r requirements.txt`
3. **Start command:** `gunicorn app:app` (start bewust met één worker — zie de opmerking hieronder over rate limiting)
4. Zet alle benodigde environment variables uit de tabel hierboven in het Render-dashboard, met in elk geval:
   - `LLM_PROVIDER` en de bijbehorende API-key (`ANTHROPIC_API_KEY` of `GOOGLE_API_KEY`) — Ollama is op Render niet bruikbaar, dat vereist een lokaal model
   - `APP_ACCESS_CODE` als je de app niet voor iedereen toegankelijk wilt maken
   - `FLASK_DEBUG` **niet** zetten, of expliciet op `false`
5. Render geeft de eigen poort door via de env var `PORT`; `app.py` leest die al automatisch uit.

**Belangrijk over meerdere workers/instances:** de rate limiter (`flask-limiter`) gebruikt in-memory opslag. Met één gunicorn-worker werkt dat prima; met meerdere workers of meerdere Render-instances telt de limiet niet gedeeld mee, en kan de effectieve limiet dus hoger uitvallen dan bedoeld. Wil je meerdere workers/instances, zet dan een gedeelde store (bv. Redis) achter de limiter.

---

# Beveiliging en kosten

Deze app roept bij `LLM_PROVIDER=anthropic` of `google` een **betaalde** externe API aan met een serverzijdige key. Zonder bescherming kan iedereen die de URL kent dat tegoed verbruiken. Neem daarom bij een publieke deploy altijd het volgende mee:

- **CORS staat bewust uit** — de frontend wordt door dezelfde Flask-app geserveerd (same-origin), dus een andere website kan niet zomaar vanuit de browser van een bezoeker requests sturen.
- **Rate limiting per IP** is standaard aan (`RATE_LIMIT_LLM`, `RATE_LIMIT_SPARQL`); pas de limieten aan naar je verwachte gebruik.
- **`APP_ACCESS_CODE`** is optioneel maar aan te raden zodra de app publiek bereikbaar is: zonder toegangscode kan iedereen met de URL de app gebruiken (en dus het API-tegoed verbruiken), ook al is dat door de rate limits begrensd per IP.
- **`FLASK_DEBUG` moet `false` blijven** in elke publiek bereikbare omgeving — de Werkzeug-debugger geeft dan een interactieve Python-console aan wie een fout weet te veroorzaken.
- Alleen `SELECT`- en `ASK`-queries worden toegestaan op het SPARQL endpoint ([sparql/executor.py](sparql/executor.py)); schrijfoperaties worden geweigerd.
- `.env` staat in `.gitignore` en hoort nooit gecommit te worden — de API-keys staan daarin.
- **`SENTRY_DSN`** is optioneel maar aan te raden zodra de app publiek draait: zonder foutregistratie zijn onverwachte 500's alleen zichtbaar als je zelf in de Render-logs kijkt. Alle drie de `except Exception`-blokken in `app.py` sturen de fout expliciet door naar Sentry (`sentry_sdk.capture_exception`) — die worden namelijk al binnen de route afgevangen en geven een nette JSON-foutmelding terug, dus Sentry's automatische onverwerkte-uitzondering-detectie zou ze anders missen.

---

# Datamodelregels

De applicatie gebruikt een centrale kennisbasis in:

`sparql/prompts/datamodel_rules.txt`

Dit bestand bevat:

- CEO classes
- property paths
- archeologische patronen
- geometrische relaties
- BAG/BRK-structuren en de officiële `ceo:heeftGemeente`/OWMS-route voor gemeenten
- gezichten
- werelderfgoed
- ActorEnRol patronen
- functie- en typepaden

`lijst.txt` en `telling.txt` bevatten alleen gedragsregels voor respectievelijk lijst- en tellingqueries.

Naast de statische promptregels valideert `sparql/semantic_validator.py` elke gegenereerde query ook programmatisch: het controleert of opgeloste gemeente-/provincie-URI's daadwerkelijk gebruikt zijn, en of onderdelen die letterlijk in de vraag staan (bv. een functie als "kerk" of een "gezicht") ook echt in de query terugkomen. Bij een gemiste onderdeel volgt automatisch één correctie-aanroep naar het LLM.

---

# Kaartfunctionaliteit

De applicatie ondersteunt automatische kaartweergave via Leaflet.

Wanneer een query WKT-geometrie teruggeeft, toont de frontend automatisch:

- punten
- lijnen
- polygonen
- multipolygonen

Ondersteunde WKT-velden:

- `?wkt`
- `?rmWkt`
- `?gezichtWkt`
- `?gebiedWkt`

Bij ruimtelijke queries kunnen meerdere geometrieën tegelijk worden weergegeven, bijvoorbeeld:

- beschermd gezicht als vlak
- rijksmonumenten als punten

---

# Ruimtelijke queries en foutafhandeling

Ruimtelijke joins (`geof:sfWithin`, `geof:sfIntersects`) draaien op het Virtuoso-endpoint van de RCE en kunnen daar falen met een `TopologyException` (bv. bij zelf-overlappende polygonen) of een timeout, vooral zodra een naamfilter meerdere kandidaat-gebieden oplevert (bijvoorbeeld twee gezichten die allebei "Dordrecht" in de naam hebben).

`sparql/executor.py` vangt dit automatisch af: bij zo'n fout wordt de query herhaald zonder de ruimtelijke filter, en wordt de ruimtelijke relatie lokaal opnieuw berekend met [Shapely](https://shapely.readthedocs.io/) (`sparql/spatial.py`). Geometrie die ook lokaal niet geldig te maken is, wordt overgeslagen (gelogd) in plaats van de hele aanvraag te laten mislukken. Blijft de fallback zelf ook falen, dan krijgt de gebruiker een begrijpelijke Nederlandse foutmelding in plaats van een ruwe endpointfout, met de suggestie de vraag specifieker te maken (bijvoorbeeld één gezicht of gemeente tegelijk).

---

# Bekende beperkingen

- Grote ruimtelijke queries kunnen nog steeds traag zijn, ook met de lokale fallback (die kost zelf ook rekentijd bij veel kandidaten).
- Sommige geometrieën ontbreken of zijn onherstelbaar ongeldig in de brondata; die rijen worden overgeslagen in plaats van getoond.
- Sommige objecttypen gebruiken inconsistente CEO-structuren.
- Grote prompts kunnen bij kleinere lokale LLM's (via Ollama) incomplete of licht afwijkende SPARQL opleveren; de app corrigeert een aantal bekende afwijkingen automatisch, maar niet alles.
- Plaats/woonplaats (bv. "Zeist" als plaats binnen een andere gemeente) wordt nog niet als aparte kandidaat herkend naast gemeente/provincie — alleen de gemeente-/provincie-ambiguïteit levert een verduidelijkingsvraag op.
- De `?rm`/`?complex`/... hoofdobject-URI eindigt op het interne CHO-nummer (cultuurhistorisch objectnummer), niet op het officiële rijksmonumentnummer — de frontend toont dat veld daarom als kale "bron"-link in plaats van als getal, om verwarring met het echte nummer te voorkomen.
- De rate limiter gebruikt in-memory opslag; bij meerdere gunicorn-workers of Render-instances telt de limiet niet gedeeld mee (zie Deployment met Render hierboven).

---

# Licentie

MIT — zie [LICENSE](LICENSE).
