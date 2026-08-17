# Job Finder

Ein kleiner, lokal betriebener Job Finder für konfigurierbare IT-Rollen,
einen persönlichen Suchradius oder vollständig remote angebotene Stellen.

## Aktueller Stand

Der Job Finder kann:

- Arbeitsagentur-Suchergebnisse für definierte Suchbegriffe abrufen
- StepStone-Suchergebnisse für definierte Suchbegriffe abrufen
- StepStone-Detailseiten lokal cachen und Zugriffsgrenzen respektieren
- bekannte Detailseiten aller Quellen sieben Tage lokal wiederverwenden
- get-in-IT-Suchergebnisse über die öffentliche JSON-API abrufen
- Arbeitnow-Stellen über die kostenlose öffentliche API abrufen
- Entry-Level-Remote-Stellen über die öffentliche Himalayas-API abrufen
- aktuelle Engineering-Stellen über die offizielle Startup-Jobs-API abrufen
- Remote-IT-Stellen über die öffentliche Jobicy-API abrufen
- direkte Karriereseiten von JUMO, EDAG, CSS, Proemion, NETHINKS, Compose IT, bytewerk und RhönEnergie abrufen
- Jobdetail-Seiten importieren
- alle Quellen in ein verbindliches Jobmodell überführen
- Rohbeschreibung, Klartext sowie Veröffentlichungs- und Abrufdaten speichern
- Jobs kostenlos vorfiltern und passende neue Jobs per OpenAI bewerten
- persönliche Match-Scores von 0 bis 100 nach klaren Jobsuch-Prioritäten erzeugen
- quellenübergreifende Duplikate zusammenführen
- bereits gesehene Jobs intern merken
- nach drei erfolgreichen, vergeblichen Quellensuchen nicht mehr gefundene Jobs
  als inaktiv markieren
- kompakte KI-Empfehlungen als JSON ausgeben
- Fehler einer Quelle isolieren und die übrigen Quellen weiterverarbeiten
- jeden Lauf protokollieren und wichtige interne Daten rotierend sichern

Die persönliche Bewertung priorisiert zuerst einen realistischen Berufseinstieg
(50 Punkte), danach Standort und Homeoffice (30 Punkte). Die grobe fachliche
Richtung (15 Punkte) und bereits vorhandene Technologien (5 Punkte) sind
nachgeordnet. Nur eine klar einstiegsfreundliche Stelle kann `strong_match`
werden; ein unsicherer Einstieg oder eine vage Anzeige bleibt höchstens
`borderline`.

Die kostenlose Vorfilterung verwirft nur klare Konflikte wie unpassenden
Standort, ausgeschlossene Beschäftigungsarten, eindeutige Führungs-/Seniorrollen,
mehr als drei geforderte Erfahrungsjahre oder hohe Reisetätigkeit. Ungewohnte
IT-Richtungen und Technologien erreichen dagegen die persönliche KI-Bewertung.
Ein genanntes Gehalt unter dem konfigurierten Minimum wird als Warnung angezeigt, aber nicht
vorab ausgeschlossen.

## Nutzung

Abhängigkeiten installieren:

```powershell
python -m pip install -r requirements.txt
```

Persönliche Konfiguration einmalig aus den öffentlichen Beispielen anlegen:

```powershell
Copy-Item user_settings.example.yaml user_settings.local.yaml
Copy-Item profile.example.yaml profile.local.yaml
```

`user_settings.local.yaml`, `profile.local.yaml` und alle Dateien unter `data/`
bleiben lokal und werden nicht von Git erfasst. Ohne lokale Dateien verwendet
der Job Finder die anonymisierten Beispiele.

Die beiden lokalen Dateien danach an die eigene Suche und das belegbare Profil
anpassen. Insbesondere dürfen Profilangaben keine nicht vorhandenen Kenntnisse,
Erfahrungen oder Abschlüsse enthalten.

## Lokale Daten und Datenschutz

Das Repository enthält nur anonymisierte Konfigurationsbeispiele. Folgende
Inhalte bleiben bewusst ausserhalb von Git:

- persönliche Profil- und Sucheinstellungen
- gefundene Stellen, Detail-Caches und manuelle Review-Notizen
- LLM-Ergebnisse und Benachrichtigungsstatus
- `OPENAI_API_KEY` und `DISCORD_WEBHOOK_URL`
- der optionale `STARTUP_JOBS_API_KEY`

Vor einem Fork oder einer Veröffentlichung sollte `git status --ignored`
kontrolliert werden. Lokale Konfigurationen, der Ordner `data/` und echte
Zugangsdaten dürfen nicht erzwungen zu Git hinzugefügt werden.

Kompletter Finder-Lauf mit Vorfilter und KI-Bewertung:

```powershell
python run_finder.py
```

Empfehlungen lokal im Browser durchsehen und ihren Status speichern:

```text
review_jobs.bat doppelklicken
```

Die Oberfläche läuft nur auf dem eigenen Computer. Beim Start führt eine
schlichte Auswahl entweder zu `Stellen prüfen` oder zu `Bewerbungen verwalten`.
Im Stellen-Review werden Empfehlungen als interessant, nicht interessant oder
beworben markiert. Internationale Treffer sind dort standardmäßig ausgeblendet
und lassen sich bei Bedarf zuschalten. Auch Stellen ohne gültige KI-Bewertung
bleiben für die manuelle Prüfung erhalten. Alle späteren Bewerbungsschritte
werden ausschließlich in der Bewerbungsübersicht gepflegt. Beide Bereiche
verwenden das bestehende Job-Gedächtnis unter `data/internal/seen_jobs.json`.

Die Bewerbungsübersicht speichert datierte Statuswechsel, zeigt auch nicht mehr
aktive Stellen und berechnet daraus lokale Kennzahlen. Für Gespräche kann
zusätzlich ein optionaler Termin mit Datum und Uhrzeit hinterlegt werden. Bei
älteren Bewerbungen
ohne gespeichertes Datum bleibt das Datum bewusst unbekannt. Der finale Status
`Keine Rückmeldung` wird manuell gesetzt; eine nicht mehr aktive Anzeige allein
löst ihn nicht automatisch aus. Die Kartenliste zeigt standardmäßig nur
laufende Bewerbungen. Abgeschlossene Bewerbungen bleiben in der Statistik und
können bei Bedarf zur Korrektur eingeblendet werden; Status und Datum einzelner
Verlaufsereignisse lassen sich dort ändern oder löschen.

Ein kostenbegrenzter Testlauf analysiert höchstens einen neuen passenden Job:

```powershell
python run_finder.py --llm-limit 1
```

Passende und grenzwertige neue oder aktualisierte Empfehlungen für Discord
vormerken, ohne sie zu senden, geschieht automatisch bei jedem Lauf. Die
Nachrichten enthalten Kurzbeschreibung, Erfahrungslevel sowie Pro und Contra.
Für den echten Versand muss
der Webhook als `DISCORD_WEBHOOK_URL` gesetzt und der Versand explizit aktiviert
werden:

```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python run_finder.py --notify
```

Der Webhook bleibt ausserhalb der gespeicherten Dateien. Erfolgreich gesendete
Jobversionen werden nicht erneut gemeldet; Fehler bleiben für den nächsten
Lauf vorgemerkt. Nach jedem Lauf mit `--notify` folgt eine kompakte
Laufstatistik mit Laufzeit, Vorfilter- und KI-Zahlen, Modellaufrufen und
Tokenverbrauch sowie einer Aufschlüsselung der gefundenen Stellen nach Quelle.
Endgültige lokale Validierungsfehler werden für unveränderte Stellen pausiert,
damit sie nicht täglich erneut kostenpflichtig analysiert werden. Änderungen an
Stelle, Profil oder KI-Konfiguration geben die Analyse wieder frei; technische
Provider- und Netzwerkfehler bleiben erneut versuchbar.

Startup Jobs ist eine optionale Quelle. Nach dem Erstellen eines kostenlosen
API-Keys unter `https://startup.jobs/account/api_keys` wird sie durch die
Umgebungsvariable `STARTUP_JOBS_API_KEY` automatisch aktiviert:

```powershell
$env:STARTUP_JOBS_API_KEY = "sj_..."
python run_finder.py
```

Jeder Lauf schreibt seine kompakte Terminalausgabe zusätzlich nach
`data/logs/`. Vor dem Verändern persistenter Daten werden Job-Gedächtnis,
LLM-Cache und Discord-Versandstatus als ZIP unter `data/backups/` gesichert.
Es bleiben höchstens die sieben neuesten Sicherungen erhalten.

Scheitert eine komplette Quelle unerwartet, wird der Fehler protokolliert und
der Lauf mit den übrigen Quellen fortgesetzt. Eine Stelle gilt erst dann als
inaktiv, wenn sie in drei erfolgreichen Läufen ihrer bekannten Quellen nicht
mehr gefunden wurde. Fehlgeschlagene oder leere Quellensuchen zählen dabei
nicht als Verschwinden.

## Optionaler automatischer Betrieb

`python run_finder.py --notify` kann beispielsweise über die Windows-
Aufgabenplanung täglich gestartet werden. Die Aufgabe selbst ist nicht Teil
des Repositorys und muss lokal eingerichtet werden. `OPENAI_API_KEY` und
`DISCORD_WEBHOOK_URL` sollten dabei als lokale Umgebungsvariablen gesetzt und
nicht in Skripten oder versionierten Dateien gespeichert werden.

Nur vorhandene interne Jobs regelbasiert prüfen, ohne Dateien zu erzeugen:

```powershell
python -m job_finder.main
```

Scoring-, Filter- und Deduplizierungsregeln testen:

```powershell
python -m unittest discover -s tests -v
```

Die automatisierten Tests benötigen keine echten Zugangsdaten und führen
keine kostenpflichtigen OpenAI-Aufrufe aus. Das LLM-Benchmark verwendet 26
vollständig synthetische, manuell bewertete Stellenanzeigen. Firmen, URLs und
Anzeigentexte darin sind erfunden.

`gpt-5.4-mini` ist nach Development-, Holdout- und Reserve-Vergleich das
Standardmodell. Die OpenAI-Anbindung verwendet `OPENAI_API_KEY`:

```powershell
python -m llm_evaluation.compare_models --limit 1
```

Die vollständige Zwei-Stufen-Pipeline gegen den Development-Split testen:

```powershell
python -m llm_evaluation.compare_models --split development
```

## Struktur

```text
job_finder/                  produktiver Anwendungscode
job_finder/config.py         Suchbegriffe und Suchorte
job_finder/console.py        gemeinsame Konsolenkonfiguration
job_finder/http.py           gemeinsame HTTP-Helfer
job_finder/llm/              wiederverwendbare LLM-Komponenten
job_finder/llm/contract.py   Rubrik und strukturierter Antwortvertrag
job_finder/llm/fit_score.py  Scoring für validierte Zwei-Stufen-Ergebnisse
job_finder/llm/profile_loader.py Laden und Validieren des LLM-Profils
job_finder/llm/openai.py     Client für strukturierte OpenAI-Antworten
job_finder/llm/service.py    produktive Zwei-Stufen-Analyse und LLM-Cache
job_finder/models.py         einheitliches Job- und Statusmodell
job_finder/operations.py     Laufprotokolle und rotierende Datensicherungen
job_finder/paths.py          gemeinsame interne und externe Datenpfade
job_finder/profile.py        Profil-, Skill- und Scoring-Regeln
job_finder/deduplication.py  quellenübergreifende Job-Deduplizierung
job_finder/remote.py         gemeinsame Remote-Erkennung
job_finder/reporting.py      kompakte JSON-Ausgabe der Empfehlungen
job_finder/applications.py  Bewerbungsverlauf und daraus abgeleitete Kennzahlen
job_finder/applications.html getrennte lokale Bewerbungsübersicht
job_finder/landing.html      lokale Startseite der Browseroberfläche
job_finder/review.py         lokaler Webserver für die Browseroberfläche
job_finder/review.html       lokale Browseroberfläche für Entscheidungen
job_finder/search_plan.py    gemeinsame Suchplan-Helfer
job_finder/structured_data.py gemeinsame JSON-LD-Auswertung
job_finder/text.py           gemeinsame Text-/HTML-Helfer
job_finder/sources/          Quellenadapter
data/internal/jobs.json     vollständige kanonische Jobdaten
data/internal/seen_jobs.json lokales Job-Gedächtnis
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
data/logs/                  kompakte Protokolle einzelner Läufe
data/backups/               sieben neueste Sicherungen persistenter Zustandsdaten
profile.example.yaml        anonymisierte Vorlage für das LLM-Profil
profile.local.yaml          lokale persönliche Faktenbasis (nicht versioniert)
user_settings.example.yaml  anonymisierte Vorlage für Suche und Vorfilter
user_settings.local.yaml    lokale Such- und Filterwerte (nicht versioniert)
llm_evaluation/             getrenntes Labor für LLM-Vergleiche
llm_evaluation/fixtures/    blinde Testeingaben und menschliche Bewertungen
llm_evaluation/results/     lokale, nicht versionierte Modellergebnisse
requirements.txt            Python-Abhängigkeiten
tests/                      automatisierte Scoring- und Filtertests
run_finder.py                kompletter Finder-Lauf
review_jobs.bat             anklickbarer Start der Review-Oberfläche
```

## Lizenz

Der Quellcode steht unter der [MIT-Lizenz](LICENSE).
