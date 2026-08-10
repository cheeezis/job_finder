# Job Agent

Ein kleiner, lokal betriebener Job-Agent fuer konfigurierbare IT-Rollen,
einen persoenlichen Suchradius oder vollstaendig remote angebotene Stellen.

## Aktueller Stand

Der Agent kann:

- Arbeitsagentur-Suchergebnisse fuer definierte Suchbegriffe abrufen
- StepStone-Suchergebnisse fuer definierte Suchbegriffe abrufen
- StepStone-Detailseiten lokal cachen und Zugriffsgrenzen respektieren
- bekannte Detailseiten aller Quellen sieben Tage lokal wiederverwenden
- get-in-IT-Suchergebnisse ueber die oeffentliche JSON-API abrufen
- Arbeitnow-Stellen ueber die kostenlose oeffentliche API abrufen
- direkte Karriereseiten von JUMO, EDAG, CSS, Proemion, NETHINKS, Compose IT, bytewerk und RhönEnergie abrufen
- Jobdetail-Seiten importieren
- alle Quellen in ein verbindliches Jobmodell ueberfuehren
- Rohbeschreibung, Klartext sowie Veroeffentlichungs- und Abrufdaten speichern
- Jobs kostenlos vorfiltern und passende neue Jobs per OpenAI bewerten
- persoenliche Match-Scores von 0 bis 100 nach klaren Jobsuch-Prioritaeten erzeugen
- quellenuebergreifende Duplikate zusammenfuehren
- bereits gesehene Jobs intern merken
- nach drei erfolgreichen, vergeblichen Quellensuchen nicht mehr gefundene Jobs
  als inaktiv markieren
- kompakte KI-Empfehlungen als JSON und Markdown ausgeben
- Fehler einer Quelle isolieren und die uebrigen Quellen weiterverarbeiten
- jeden Lauf protokollieren und wichtige interne Daten rotierend sichern

Die persoenliche Bewertung priorisiert zuerst einen realistischen Berufseinstieg
(50 Punkte), danach Standort und Homeoffice (30 Punkte). Die grobe fachliche
Richtung (15 Punkte) und bereits vorhandene Technologien (5 Punkte) sind
nachgeordnet. Nur eine klar einstiegsfreundliche Stelle kann `strong_match`
werden; ein unsicherer Einstieg oder eine vage Anzeige bleibt hoechstens
`borderline`.

Die kostenlose Vorfilterung verwirft nur klare Konflikte wie unpassenden
Standort, ausgeschlossene Beschaeftigungsarten, eindeutige Fuehrungs-/Seniorrollen,
mehr als drei geforderte Erfahrungsjahre oder hohe Reisetaetigkeit. Ungewohnte
IT-Richtungen und Technologien erreichen dagegen die persoenliche KI-Bewertung.
Ein genanntes Gehalt unter dem konfigurierten Minimum wird als Warnung angezeigt, aber nicht
vorab ausgeschlossen.

## Nutzung

Abhaengigkeiten installieren:

```powershell
python -m pip install -r requirements.txt
```

Persoenliche Konfiguration einmalig aus den oeffentlichen Beispielen anlegen:

```powershell
Copy-Item user_settings.example.yaml user_settings.local.yaml
Copy-Item profile.example.yaml profile.local.yaml
```

`user_settings.local.yaml`, `profile.local.yaml` und alle Dateien unter `data/`
bleiben lokal und werden nicht von Git erfasst. Ohne lokale Dateien verwendet
der Agent die anonymisierten Beispiele.

Die beiden lokalen Dateien danach an die eigene Suche und das belegbare Profil
anpassen. Insbesondere duerfen Profilangaben keine nicht vorhandenen Kenntnisse,
Erfahrungen oder Abschluesse enthalten.

## Lokale Daten und Datenschutz

Das Repository enthaelt nur anonymisierte Konfigurationsbeispiele. Folgende
Inhalte bleiben bewusst ausserhalb von Git:

- persoenliche Profil- und Sucheinstellungen
- gefundene Stellen, Detail-Caches und manuelle Review-Notizen
- LLM-Ergebnisse und Benachrichtigungsstatus
- `OPENAI_API_KEY` und `DISCORD_WEBHOOK_URL`

Vor einem Fork oder einer Veroeffentlichung sollte `git status --ignored`
kontrolliert werden. Lokale Konfigurationen, der Ordner `data/` und echte
Zugangsdaten duerfen nicht erzwungen zu Git hinzugefuegt werden.

Kompletter Agentenlauf mit Vorfilter und KI-Bewertung:

```powershell
python run_agent.py
```

Empfehlungen lokal im Browser durchsehen und ihren Status speichern:

```text
review_jobs.bat doppelklicken
```

Die Review-Oberflaeche laeuft nur auf dem eigenen Computer und verwendet das
bestehende Job-Gedaechtnis unter `data/internal/seen_jobs.json`.

Ein kostenbegrenzter Testlauf analysiert hoechstens einen neuen passenden Job:

```powershell
python run_agent.py --llm-limit 1
```

Passende und grenzwertige neue oder aktualisierte Empfehlungen fuer Discord
vormerken, ohne sie zu senden, geschieht automatisch bei jedem Lauf. Die
Nachrichten enthalten Kurzbeschreibung, Erfahrungslevel sowie Pro und Contra.
Fuer den echten Versand muss
der Webhook als `DISCORD_WEBHOOK_URL` gesetzt und der Versand explizit aktiviert
werden:

```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python run_agent.py --notify
```

Der Webhook bleibt ausserhalb der gespeicherten Dateien. Erfolgreich gesendete
Jobversionen werden nicht erneut gemeldet; Fehler bleiben fuer den naechsten
Lauf vorgemerkt. Nach jedem Lauf mit `--notify` folgt eine kompakte
Laufstatistik mit Laufzeit, Vorfilter- und KI-Zahlen sowie einer Aufschluesselung
der gefundenen Stellen nach Quelle.

Jeder Lauf schreibt seine vollstaendige Terminalausgabe zusaetzlich nach
`data/logs/`. Vor dem Veraendern persistenter Daten werden Job-Gedaechtnis,
LLM-Cache und Discord-Versandstatus als ZIP unter `data/backups/` gesichert.
Es bleiben hoechstens die sieben neuesten Sicherungen erhalten.

Scheitert eine komplette Quelle unerwartet, wird der Fehler protokolliert und
der Lauf mit den uebrigen Quellen fortgesetzt. Eine Stelle gilt erst dann als
inaktiv, wenn sie in drei erfolgreichen Laeufen ihrer bekannten Quellen nicht
mehr gefunden wurde. Fehlgeschlagene oder leere Quellensuchen zaehlen dabei
nicht als Verschwinden.

## Optionaler automatischer Betrieb

`python run_agent.py --notify` kann beispielsweise ueber die Windows-
Aufgabenplanung taeglich gestartet werden. Die Aufgabe selbst ist nicht Teil
des Repositorys und muss lokal eingerichtet werden. `OPENAI_API_KEY` und
`DISCORD_WEBHOOK_URL` sollten dabei als lokale Umgebungsvariablen gesetzt und
nicht in Skripten oder versionierten Dateien gespeichert werden.

Nur vorhandene interne Jobs regelbasiert pruefen, ohne Dateien zu erzeugen:

```powershell
python -m job_agent.main
```

Scoring-, Filter- und Deduplizierungsregeln testen:

```powershell
python -m unittest discover -s tests -v
```

Die automatisierten Tests benoetigen keine echten Zugangsdaten und fuehren
keine kostenpflichtigen OpenAI-Aufrufe aus. Das LLM-Benchmark verwendet 26
vollstaendig synthetische, manuell bewertete Stellenanzeigen. Firmen, URLs und
Anzeigentexte darin sind erfunden.

`gpt-5.4-mini` ist nach Development-, Holdout- und Reserve-Vergleich das
Standardmodell. Die OpenAI-Anbindung verwendet `OPENAI_API_KEY`:

```powershell
python -m llm_evaluation.compare_models --limit 1
```

Die vollstaendige Zwei-Stufen-Pipeline gegen den Development-Split testen:

```powershell
python -m llm_evaluation.compare_models --split development
```

## Struktur

```text
job_agent/                  produktiver Agentencode
job_agent/config.py         Suchbegriffe und Suchorte
job_agent/console.py        gemeinsame Konsolenkonfiguration
job_agent/http.py           gemeinsame HTTP-Helfer
job_agent/llm/              wiederverwendbare LLM-Komponenten
job_agent/llm/contract.py   Rubrik und strukturierter Antwortvertrag
job_agent/llm/fit_score.py  Scoring fuer validierte Zwei-Stufen-Ergebnisse
job_agent/llm/profile_loader.py Laden und Validieren des LLM-Profils
job_agent/llm/openai.py     Client fuer strukturierte OpenAI-Antworten
job_agent/llm/service.py    produktive Zwei-Stufen-Analyse und LLM-Cache
job_agent/models.py         einheitliches Job- und Statusmodell
job_agent/operations.py     Laufprotokolle und rotierende Datensicherungen
job_agent/paths.py          gemeinsame interne und externe Datenpfade
job_agent/profile.py        Profil-, Skill- und Scoring-Regeln
job_agent/deduplication.py  quellenuebergreifende Job-Deduplizierung
job_agent/remote.py         gemeinsame Remote-Erkennung
job_agent/reporting.py      JSON- und Markdown-Ausgaben
job_agent/review.py         lokaler Webserver fuer den Review-Workflow
job_agent/review.html       lokale Browseroberflaeche fuer Entscheidungen
job_agent/search_plan.py    gemeinsame Suchplan-Helfer
job_agent/structured_data.py gemeinsame JSON-LD-Auswertung
job_agent/text.py           gemeinsame Text-/HTML-Helfer
job_agent/sources/          Quellenadapter
data/internal/jobs.json     vollstaendige kanonische Jobdaten
data/internal/seen_jobs.json lokales Job-Gedaechtnis
data/internal/stepstone_cache.json technischer StepStone-Cache
data/internal/arbeitsagentur_cache.json Detailcache der Arbeitsagentur
data/internal/get_in_it_cache.json Detailcache von get-in-IT
data/internal/arbeitnow_cache.json Detailcache von Arbeitnow
data/internal/jumo_cache.json       Detailcache von JUMO
data/internal/edag_cache.json       Detailcache von EDAG
data/internal/css_cache.json        Detailcache von CSS
data/internal/proemion_cache.json   Detailcache von Proemion
data/internal/nethinks_cache.json   Detailcache von NETHINKS
data/internal/compose_it_cache.json Detailcache von Compose IT
data/internal/bytewerk_cache.json   Detailcache von bytewerk
data/internal/rhoenenergie_cache.json Detailcache der RhönEnergie-Gruppe
data/internal/llm_cache.json LLM-Ergebnisse und ausstehende Analysen
data/internal/notifications.json Versand- und Wiederholungsstatus
data/output/recommendations.json kompakte finale KI-Ergebnisse
data/output/recommendations.md lesbare KI-Empfehlungen
data/logs/                  vollstaendige Protokolle einzelner Laeufe
data/backups/               sieben neueste Sicherungen persistenter Zustandsdaten
profile.example.yaml        anonymisierte Vorlage fuer das LLM-Profil
profile.local.yaml          lokale persoenliche Faktenbasis (nicht versioniert)
user_settings.example.yaml  anonymisierte Vorlage fuer Suche und Vorfilter
user_settings.local.yaml    lokale Such- und Filterwerte (nicht versioniert)
llm_evaluation/             getrenntes Labor fuer LLM-Vergleiche
llm_evaluation/fixtures/    blinde Testeingaben und menschliche Bewertungen
llm_evaluation/results/     lokale, nicht versionierte Modellergebnisse
requirements.txt            Python-Abhaengigkeiten
tests/                      automatisierte Scoring- und Filtertests
run_agent.py                kompletter Agentenlauf
review_jobs.bat             anklickbarer Start der Review-Oberflaeche
```

## Lizenz

Der Quellcode steht unter der [MIT-Lizenz](LICENSE).
