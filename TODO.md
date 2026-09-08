# Backlog

Verzameling van uitgestelde ideeën en openstaande verificaties, besproken
maar bewust nog niet opgepakt.

## Prioriteit

Volgorde, met korte reden. Binnen een niveau geen vaste volgorde.

1. **CI (GitHub Actions).** Er is inmiddels een reële, groeiende testsuite
   (60+ tests) die alleen draait als iemand er lokaal aan denkt — de
   goedkoopste manier om te voorkomen dat een regressie ongemerkt naar
   productie gaat.
2. **Error tracking (Sentry of vergelijkbaar).** Eén integratie, sluit
   direct de blinde vlek "onverwachte 500's in productie zijn alleen
   zichtbaar als je zelf in de Render-logs kijkt".
3. **TopologyException-fallback reproduceren tegen een echte fout.** De
   LIMIT-verbreding (`spatial.widen_limit`) is deze sessie toegevoegd en
   unit-getest, maar nooit tegen een daadwerkelijk live falende
   ruimtelijke query bevestigd — de logische afronding van dat werk, geen
   nieuw traject.
4. **Cloudflare Turnstile.** De app staat nu live en publiek met een
   betaalde Anthropic-sleutel erachter — dit beschermt dat budget
   concreter dan de huidige rate limits alleen.
5. **Periodiek nalopen van `APP_ACCESS_CODE` en de rate limits.** Geen
   eenmalige actie, maar goedkoop om standaard mee te nemen zodra 4
   opgepakt wordt.
6. **Backlog-parity-check met talk2thegraph.** Nalopen welke van de
   bovenstaande punten (vooral CI, JS-testrunner, Redis, Turnstile) daar
   hetzelfde gat hebben — zie projectgeheugen
   `project-chat2thedata-vs-talk2thegraph`.
7. **Fase 1b — plaats/woonplaats als derde kandidaat** naast gemeente/
   provincie (de oorspronkelijke Zeist-casus). Natuurlijke vervolgstap op
   een al bewezen mechanisme (Fase 1); zie het plan-bestand voor het
   ontwerp.
8. **Cache voor veelgestelde vragen.** Bespaart LLM-kosten en latency —
   zelf al eerder aangemerkt als waarschijnlijk de hoogste ROI van de
   resterende featurelijst.
9. **Gedeelde rate-limit-store (Redis).** Pas relevant zodra er meer dan
   één gunicorn-worker of Render-instance draait — nu in-memory, dus nog
   geen acuut probleem bij één instance.
10. **JS-testrunner voor de WKT/GeoJSON-parsing.** Nog altijd alleen
    handmatig via de browserconsole getest; toen dat wél gebeurde kwam er
    direct een echte bug uit.
11. **Fase 3 — kanttekeningen in het antwoord** voor de resterende
    ambiguïteitscategorieën (vrije-tekst-fallback, vaag-woord-naar-
    verkeerde-property, schema-dekking). Groter/vager traject dan Fase 1b,
    vandaar lager.
12. **Overige features** (model-keuze, duim omhoog/omlaag, kaartlagen,
    marker clustering, querybibliotheek, tweede LLM-kritiekronde) — zie
    onder, geen betrouwbaarheids- of kostenrisico, dus laagste prioriteit.

## Betrouwbaarheid / verificatie

- ~~Nooit getest met een echte LLM-aanroep.~~ **Opgelost (2026-09-07/08):**
  live geverifieerd met zowel Ollama als Anthropic Claude, inclusief de
  volledige verduidelijkings-/beperkingsflow.
- ~~Geen live Render-deploy geverifieerd.~~ **Opgelost:** staat live op
  Render, provider-omschakeling en toegangscode bevestigd werkend.
- **De TopologyException-fallback is nog nooit tegen een echte fout op het
  live endpoint getest**, alleen tegen een gemockte HTTPError. De
  LIMIT-verbreding (`spatial.widen_limit`, `incomplete_due_to_limit`) is
  deze sessie toegevoegd en unit-getest, maar de fallback als geheel
  reproduceren tegen een daadwerkelijk live falende ruimtelijke query staat
  nog open. Zie Prioriteit #3.
- **Geen JS-testrunner.** Zie Prioriteit #10.

## Monitoring en meldingen

- Geen alerting bij herhaaldelijk geraakte rate limits, LLM-providerfouten,
  of vaak aanslaande ruimtelijke fallback — nu alleen zichtbaar in de
  Render-logs als je er zelf naar kijkt.
- Geen error tracking (bv. Sentry) voor onverwachte 500's in productie. Zie
  Prioriteit #2.
- Geen CI: de testsuite draait alleen lokaal/handmatig, niet automatisch bij
  een push (bv. GitHub Actions). Zie Prioriteit #1.

## Features — kleinere/middelgrote moeite

- Cache voor veelgestelde vragen. Zie Prioriteit #8.
- Keuze tussen snel/goedkoop/nauwkeurig model (meerdere modelpresets per
  provider, front- en backend).
- Knoppen voor goed/fout antwoord — moet ergens opgeslagen worden (bestand?
  database? alleen loggen?), nog te bepalen.
- Kaartlagen voor monumenten en gezichten los aan/uit te zetten.

## Features — grotere investering

- Markerclustering bij grote resultaten (vereist een externe
  Leaflet-plugin, moet gevendord worden net als de rest van Leaflet).
- Querybibliotheek met betrouwbare sjablonen (sla bewezen queries op i.p.v.
  steeds opnieuw te laten genereren) — apart project.
- Verdergaande automatische tweede controle van gegenereerde SPARQL. Bestaat
  al deels via `validate_semantics`/`validate_completeness`/`validate_syntax`
  (regelgebaseerd, gratis); een extra LLM-kritiekronde kost een extra
  aanroep per vraag — pas doen als de regelgebaseerde aanpak in de praktijk
  tekortschiet.

## Beveiliging (doorlopend, geen eenmalige actie)

- Cloudflare Turnstile voor de publieke deploy. Zie Prioriteit #4.
- Gedeelde rate-limit-store (bv. Redis) zodra er meer dan één
  gunicorn-worker of Render-instance draait. Zie Prioriteit #9.
- Periodiek nalopen of `APP_ACCESS_CODE` en de rate limits nog passen bij
  het daadwerkelijke gebruik zodra de app publiek staat. Zie Prioriteit #5.

## Ambiguïteitswerk (los traject, zie plan-bestand en projectgeheugen)

- Fase 1b — plaats/woonplaats als derde kandidaat. Zie Prioriteit #7.
- Fase 3 — kanttekeningen in het antwoord voor de resterende categorieën.
  Zie Prioriteit #11.
- Backlog-parity-check met talk2thegraph. Zie Prioriteit #6.
