# Entwicklung am Job Finder

Diese Anleitung beschreibt die gemeinsame Anwendung. Einrichtung und Bedienung
stehen in der [README](../README.md). Docker- und Azure-Betrieb sind seit der
vollständigen Umstellung auf PostgreSQL und Azure Teil von `main`
(`infrastructure/`, [PostgreSQL-Anleitung](postgresql.md),
[Netzwerkpfade](networking.md)); ein separater Cloud-Branch existiert nicht mehr.

## Arbeitsumgebung und Prüfungen

Python 3.11 oder neuer wird benötigt. Die Befehle laufen im Repository-Stamm.
Unter Windows muss die virtuelle Umgebung nicht aktiviert werden:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe scripts/test_postgres.py
node --test tests/frontend.test.cjs
```

Unter Linux/macOS lautet der Interpreterpfad `.venv/bin/python`. Node.js wird
nur für die Frontend-Tests benötigt; die CI verwendet Node.js 24. Für den
Python-Betrieb reicht `requirements.txt`. `requirements-dev.txt` installiert
zusätzlich die festgelegte Ruff-Version, damit lokale Prüfung und CI dieselben
Formatierungsregeln verwenden.

Der [GitHub-Workflow](../.github/workflows/checks.yml) prüft Python auf Linux mit PostgreSQL sowie Python 3.11 und 3.13.
Lokal ist derselbe Teststarter auch unter Windows nutzbar. Ein weiterer Job prüft Stil und Frontend.
Die Tests verwenden lokale Fixtures, temporäre Datenpfade, eine separate
PostgreSQL-Testdatenbank und ersetzte Netzwerkzugriffe. Die Datenbank wird gemäß
[PostgreSQL-Anleitung](postgresql.md) eingerichtet; der Teststarter schützt den
Produktivbestand vor Testschreibzugriffen. Ein vollständiger Finder-Lauf gehört nicht zur Testsuite.
GitHub-Checks werden damit ausgeführt; ob sie einen Merge blockieren, wird
separat in den Repository-Regeln eingestellt.

Neue allgemeine Änderungen beginnen auf einem aktuellen `main`, zum Beispiel
auf `docs/...`, `fix/...` oder `feat/...`. Inhaltliche und große rein mechanische
Änderungen getrennt committen. Die [PR-Vorlage](../.github/PULL_REQUEST_TEMPLATE.md)
beschreibt Titel und Beschreibung.

## Orientierung im Code

| Bereich | Zuständigkeit |
| --- | --- |
| `run_finder.py` | CLI, Quellenkoordination und Reihenfolge der Pipeline |
| `job_finder/workflow/main.py` | Vorhandene Jobs bewerten und Ergebnisse zusammenstellen |
| `job_finder/sources/` | Quellen abrufen und in `Job`/`JobSource` umwandeln |
| `job_finder/models.py` | Datenmodell, Statuswerte und Serialisierung |
| `job_finder/matching/deduplication.py` | Gleiche Anzeigen verschiedener Quellen zusammenführen |
| `job_finder/matching/scoring.py`, `matching_rules.py`, `remote.py` | Bewertungsablauf, Erkennungsregeln und Remote-Erkennung |
| `job_finder/matching/experience.py`, `location_rules.py`, `salary.py`, `matching_text.py` | Zusammenhängende Analysen und normalisierte Textvergleiche |
| `job_finder/workflow/memory.py` | PostgreSQL-Zustand, stabile IDs und frühere Entscheidungen |
| `job_finder/workflow/availability.py` | Fehlende interessante Stellen auf bestätigte Schließung prüfen |
| `job_finder/review.py`, `job_finder/workflow/review_data.py`, `review_actions.py` | HTTP-Server, Review-Datenaufbereitung und transaktionale Aktionen |
| `job_finder/workflow/applications.py`; `job_finder/persistence/application_documents.py`, `document_store.py`, `state_compat.py` | Bewerbungsverlauf, Unterlagen (lokal oder im Blob Storage) und unterstützte Speicherformate |
| `job_finder/app.js`, `landing.js`, `review.js`, `applications.js` und zugehörige HTML-Dateien | Gemeinsame Browser-Helfer, Seitenskripte und Arbeitsansichten |
| `job_finder/workflow/reporting.py`, `notifications.py` | Review-Ausgabe und Discord-Warteschlange |
| `job_finder/matching/user_settings.py`, `config.py`; `job_finder/paths.py` | Konfiguration, Suche und lokale Dateipfade |

### Datenfluss eines Finder-Laufs

1. Lokal wird zuerst der Datenbestand gesichert; in Containern entfällt
   dieses Backup. Danach liefern Quellen Treffer und Abdeckungsangaben; URLs
   und quellenübergreifende Duplikate werden zusammengeführt. Sind mehr als
   die Hälfte der Quellen unbrauchbar, stoppt der Lauf vor dem Ersetzen von
   Job-Snapshot und Review-Ausgabe.
2. Ein erster Vorfilter bestimmt die Kandidaten für optionale Detailabrufe.
   Quellen dürfen deren Job-Objekte ersetzen oder bestätigte geschlossene
   Anzeigen aus der Liste entfernen.
3. Die angereicherten Jobs werden endgültig bewertet. Diese Ergebnisse
   bleiben mit den Job-Objekten verbunden, während das Gedächtnis anschließend
   IDs, Erstfund-Merkmale und bestehende Workflow-Entscheidungen zuordnet.
4. Die Offline-Prüfung betrachtet fehlende interessante Stellen ohne
   Bewerbungsverlauf und nur bei vollständig erfolgreichen bekannten Quellen.
   Netzwerkabrufe erfolgen außerhalb der PostgreSQL-Schreibtransaktionen;
   vor einer Statusänderung wird der aktuelle Nutzerentscheid erneut geprüft.
5. Job-Snapshot und Empfehlungen werden geschrieben. Die Discord-Warteschlange
   wird aktualisiert und bei jedem Lauf direkt versendet.

`is_new` beschreibt einen Erstfund im Suchlauf. `workflow_status="new"`
bedeutet dagegen, dass die Stelle noch nicht bearbeitet wurde. Der Review-
Filter „Neu“ richtet sich nach dem Workflow-Status und seinen Sichtbarkeitsfiltern.
Diese Merkmale dürfen bei Änderungen nicht gleichgesetzt werden.

### Manueller Import

`manual_import.import_manual_url` verarbeitet genau die eingereichte URL und
behält die übrigen Empfehlungen. Ein Vorfilterkonflikt bleibt als Warnung
sichtbar; er verhindert die manuelle Sichtung nicht. Im Standardbetrieb lädt
sie die Seite vor jeder Sperre und schreibt danach manuelle Quelle, Gedächtnis,
Job-Snapshot und Empfehlungen in einer gemeinsamen PostgreSQL-Transaktion.
Mit ausdrücklich anderen Dateipfaden, etwa in Tests, laufen diese
Schreibvorgänge nacheinander ohne gemeinsame Transaktion.

## Eine Quelle ergänzen

Eine Quelle liegt unter `job_finder/sources/<name>.py` und liefert Instanzen
des gemeinsamen Modells statt eigener Job-Dictionaries. Zunächst eine vorhandene
ähnliche Quelle prüfen. Eine Karriereseite, deren Detailseiten JSON-LD liefern
und einem gemeinsamen URL-Muster folgen, braucht kein eigenes Modul: Dafür
genügt ein `CareerPage`-Eintrag in `sources/company_careers.py`, bei
nummerierten Seiten `PaginatedCareerPage`.

Der vom Runner erwartete Vertrag:

| Schnittstelle | Verhalten |
| --- | --- |
| `SOURCE_NAME` | Stabiler Quellenname für IDs, Cache und Laufdiagnose |
| `fetch_jobs()` | Liefert eine Liste von `Job`; bei einem vollständigen Quellenfehler darf eine Exception propagieren |
| `enrich_candidate_jobs(jobs, candidate_ids)` (optional) | Verändert die übergebene Liste beziehungsweise ihre Jobs und liefert die Anzahl betroffener Anzeigen; nicht ladbare Kandidatendetails meldet sie über `record_candidate_failure()` |

Eine Quelle mit mehreren Suchen meldet ihre Abdeckung während `fetch_jobs()`;
der Runner bildet daraus Status und Details:

```python
from job_finder.sources.common import record_partial_failure, record_total_segments

record_total_segments(len(searches))
record_partial_failure(failed_searches)
```

Bei einem abgefangenen Teilfehler muss die Quelle diesen über
`record_partial_failure()` melden. Ein stilles `[]`
könnte sonst als vollständig erfolgreiche Suche ohne Treffer interpretiert
werden. Der Runner verwendet die Zustände `success`, `empty`, `partial` und
`failed`, um fehlende Treffer richtig zu behandeln.

Scheitert nach dem Vorfilter die Detailseite eines Kandidaten, meldet die
Quelle das über `record_candidate_failure()`. Der Quellenstatus bleibt dabei
unverändert, weil die Suche selbst vollständig war. Die Discord-Laufstatistik
und das Logereignis `enrichment_completed` weisen die fehlenden Details aus.

Für Details übernimmt `fetch_cached_details` den gemeinsamen Cache: sieben
Tage frisch, bei Abruffehlern höchstens 14 Tage als markierter Fallback.
`ListingUnavailableError` kennzeichnet eindeutig geschlossene Anzeigen und
entfernt ihren Detailcache. Andere Fehler sind kein Schließungsnachweis.
Quellenspezifische Suchfenster oder Cache-Regeln können davon abweichen und
gehören in den jeweiligen Modul-Docstring.

Beim Ergänzen einer Quelle:

1. Herkunft in `JobSource` erhalten; Anzeigen-URL und gegebenenfalls direkte
   Bewerbungs-URL unterscheiden. IDs stabil erzeugen. Unbekannte Datums- und
   Gehaltsangaben nicht schätzen. Gehälter im Modell sind EUR-Jahresbrutto.
2. Gemeinsame HTTP-, Text-, JSON-LD- und Remote-Helfer verwenden, soweit sie
   zur Seite passen. Bei manuell eingegebenen URLs auch Weiterleitungen durch
   `validate_public_url` prüfen lassen.
3. Das Modul beziehungsweise den `CareerPage`-Eintrag in `run_finder.py`
   importieren und in `SOURCES` registrieren. Optionale Zugangsdaten nur über
   Umgebungsvariablen beziehen; bei Bedarf die Quelle nur bei vorhandener
   Konfiguration aktivieren.
4. Parser und Quellenausfälle mit kleinen Fixtures testen: reguläre Anzeige,
   fehlende optionale Felder, Teilfehler und Cache-/Schließungsfälle. Keine
   kompletten fremden Webseiten mit Trackingdaten als Fixtures übernehmen.
5. Quelle und besondere Einschränkungen in der README ergänzen.

Adapter erhalten weder Datenbankverantwortung noch persönliche
Workflow-Entscheidungen. Sie liefern Anzeigen und ihre Herkunft; Filter und
Speicherung bleiben in den gemeinsamen Modulen.

## Zustandsänderungen und Konfiguration

`paths.py` legt alle Pfade relativ zum Projekt fest. Es gibt keinen allgemeinen
`DATA_DIR`-Umgebungsvariablen-Schalter; nur der Dokumentordner lässt sich über
`JOBFINDER_DOCUMENTS_DIR` verlegen. Tests reichen abweichende Pfade über
Funktionsparameter oder gezielte Patches ein.

- Der dauerhafte Stellen- und Bewerbungszustand liegt in PostgreSQL.
  `MEMORY_FILE` (`data/internal/job_finder.sqlite3`) ist nur noch der Schlüssel
  dieses Bestands; andere Pfade sind allein in isolierten Tests erlaubt. Für
  Änderungen `edit_memory` verwenden, damit Lesen, Ändern und Speichern
  gemeinsam gesperrt sind. `update_memory` verändert die übergebenen Objekte,
  schreibt allein aber nicht in die Datenbank.
- JSON-Pfade direkt unter `data/internal` und `data/output` benennen
  PostgreSQL-Datensätze (`storage.dataset_name`), keine Dateien; nur andere
  Pfade werden als JSON-Datei gelesen oder geschrieben. `jobs.json` und
  `recommendations.json` sind neu erzeugbare Ausgaben; `*_cache.json` enthält
  wiederverwendbare Quelldetails.
- `notifications.json` enthält die Discord-Warteschlange und den Versandstatus.
  Auch `process_notifications(send=False)` verändert diesen Datensatz.
- Bewerbungsunterlagen liegen je nach `JOBFINDER_DOCUMENTS_BACKEND` im
  Dokumentordner (`local`, Standard) oder im Blob Storage (`blob`); ihre
  Metadaten stehen in PostgreSQL. `python -m job_finder.db backup` und das
  automatische Backup vor lokalen Finder-Läufen enthalten die referenzierten
  Dokumente samt Prüfsummen.

`user_settings.local.yaml` wird beim Import der Konfigurationsmodule gelesen.
Ohne diese Datei wird die anonymisierte Beispielkonfiguration verwendet.
Nach Änderungen laufende Prozesse neu starten. Persönliche Konfiguration,
Dokumente, Datenbanken und Zugangsdaten bleiben außerhalb von Git.

`score_job` liefert ein Ergebnis-Dictionary, keine einzelne Prozentzahl.
Ausgeschlossene Ergebnisse enthalten einen Score von 0 und den ersten
Ausschlussgrund; nur regulär eingeschlossene Ergebnisse besitzen zusätzlich
`role_group` und `location_precheck`. `score_for_pipeline` ergänzt die
Sonderbehandlung manueller Einträge. Diese Ergebnisse sind Sortierhilfen,
keine Vorhersagen einer Einstellungschance.

Die Bewertung prüft zuerst das Anzeigenalter, danach die Anforderungen und
zuletzt den Standort. Die erste Ablehnung bleibt der sichtbare Ausschlussgrund.
Erfahrungsjahre und Standortanalyse werden anschließend für die Punktevergabe
wiederverwendet. Bei Änderungen diese Reihenfolge und die Grenzwerte erhalten.

### Eigenständiger Vorfilter und persönliche Präferenzen

`matching_rules.py` enthält Rollenbegriffe, Kontextbedingungen und
Ausschlussmerkmale ohne persönliche Werte oder Punkte. Die erste passende
Rollengruppe gewinnt; ihre Reihenfolge ist fachliche Erkennungspriorität und
wird nicht durch persönliche Vorlieben umsortiert.

`user_settings.local.yaml` steuert Standort, Gehalt
und den Bezug zu Projekten oder Weiterbildungen. Ein früheres Feld
`matching.preferred_role_groups` wird beim Laden ignoriert. `profile.local.yaml` ist davon unabhängig und wird nicht automatisch
eingelesen. Die alte Python-Datei `profile.py` wurde durch `matching_rules.py`
abgelöst; interne Imports verwenden die neuen Zuständigkeiten.

Die Bewertung bleibt eine vollständige, regelbasierte Sortierhilfe:

| Bestandteil | Punkte |
| --- | --- |
| Klare Einstiegsstelle oder erste Erfahrung ausreichend | 25 |
| Erfahrung nur wünschenswert / keine klare Anforderung | 18 / 20 |
| Ein / zwei / drei Jahre gefordert | 14 / 8 / 3 |
| Technologische Vorerfahrung / mehrjährige Erfahrung ohne Jahreszahl | 8 / 6 |
| Vollständig remote / lokal hybrid / lokal vor Ort / erlaubter Pendelort | 15 / 13 / 10 / 8 |
| Erkannte IT-Richtung | 10 bis 30, nach Rolle |
| Technologien im Titel oder Beschreibung | bis 25 insgesamt |
| Mindestens ein konfigurierter Profilbegriff im Anzeigentext | 5 insgesamt |

Die Junior-Hybrid-Ausnahme außerhalb des Suchgebiets bleibt mit null
Standortpunkten sichtbar zuschaltbar. Bestehende Präferenzabzüge folgen auf
die Summe; das Ergebnis bleibt auf 0 bis 100 begrenzt. Es gibt keinen
Mindestscore für die Aufnahme ins Review. `ranking_weights.py` enthält die
wiederhergestellten Rollen- und Technologiegewichte aus `201417f`; die
Erkennungsregeln und persönlichen Einstellungen bleiben davon getrennt.

Die 32 festen Vergleichsfälle behalten ihre Eingaben und Ausschlussentscheidungen;
die Erwartungen entsprechen wieder der Sortierung vor der Umgewichtung.
Änderungen an einzelnen Erkennungsfehlern werden separat getestet und mit
gespeicherten Entscheidungen verglichen. Persönliche Anzeigen und Bewertungen
bleiben dabei lokal außerhalb des Repositories.

Die Review-API ordnet POST-Routen kurzen Aktionsmethoden zu. Host-/Origin-Prüfung,
Größenlimit und JSON-Objektprüfung erfolgen gemeinsam vor dem Aufruf der Aktion;
Fehlerantworten und Antwortheader bleiben zentral. Im Browser verwenden die
Bewerbungsformulare denselben Speicherablauf, der ihre Aktionsbuttons auch nach
einem Fehler wieder freigibt.

Fachliche Funktionen werden aus ihrem zuständigen Modul importiert;
Kompatibilitätspfade über `job_finder.review` oder das frühere
`job_finder.scoring` gibt es nicht mehr. Standortregeln bekommen lokale
Einstellungen explizit vom Scoring-Einstiegspunkt übergeben.

## Python-Stil und hilfreiche Dokumentation

Orientierung geben [PEP 8](https://peps.python.org/pep-0008/) und
[PEP 257](https://peps.python.org/pep-0257/). Die konkrete, reproduzierbare
Konfiguration steht in [pyproject.toml](../pyproject.toml).

- Vier Leerzeichen einrücken; englische Bezeichner, Kommentare und Docstrings
  verwenden. Nutzertexte und Projektanleitungen bleiben deutsch.
- Ruff formatiert mit 100 Zeichen als Richtwert und setzt alles auf eine Zeile,
  was hineinpasst; ein Komma am Ende erzwingt keinen Umbruch. Lange URLs,
  Regex-Ausdrücke oder Testdaten können länger bleiben, wenn Aufteilen die
  Lesbarkeit verschlechtert. `E501` wird deshalb nicht pauschal erzwungen; lange
  Kommentar- und Docstring-Absätze von Hand auf etwa 72 Zeichen umbrechen.
- Imports nach Standardbibliothek, Fremdpaketen und Projektcode gruppieren.
  Änderungen an Importreihenfolgen bei Modulen mit Initialisierungseffekten
  zusätzlich inhaltlich prüfen.
- Ein Docstring steht dort, wo er mehr sagt als der Name: Zweck, Grund,
  Randfälle oder Zustandsänderungen. Einer, der nur den Namen wiederholt,
  entfällt; Ruff verlangt deshalb keine Docstrings (`D1xx` ist aus). Er beginnt
  mit einer kurzen Handlungsbeschreibung und einem Punkt; weitere Absätze folgen
  nach einer Leerzeile.
- Bei komplexen Funktionen Eingaben, Rückgaben, Zustandsänderungen und relevante
  Fehler beschreiben. Insbesondere `None`, leere Werte und das Verändern
  übergebener Objekte erklären. Selbstverständliche Parameter nicht nur unter
  anderen Worten wiederholen. Ein festes Google-/NumPy-Abschnittsschema ist
  nicht vorgeschrieben.
- Kommentare begründen Sonderfälle oder Voraussetzungen. Sie sollen nicht
  jede Schleife und Zuweisung nacherzählen. Veraltete Kommentare beim Ändern
  des Verhaltens gleichzeitig aktualisieren.
- Testfälle erhalten sprechende Namen. Konstruktoren und
  Standard-Protokollmethoden brauchen keine bloße Wiederholung; abweichendes
  Verhalten gehört trotzdem dokumentiert.
- Typannotationen sind bei Datenmodellen und neuen klaren Schnittstellen
  hilfreich. Eine flächendeckende Typmigration ist keine Voraussetzung für
  eine Dokumentationsänderung.

Automatisch formatieren und anschließend prüfen:

```powershell
.\.venv\Scripts\python.exe -m ruff check --select I --fix .
.\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe -m ruff check .
```

Der Linter prüft Form und häufige Fehler. Ob ein Docstring das tatsächliche
Verhalten erklärt und ob eine Fachregel sinnvoll ist, bleibt Teil des Reviews.


## Vereinfachungsprüfung vom 15. September 2026

Ausgangspunkt ist der saubere lokale `main` bei `9e572b6`; die Änderungen liegen
auf `refactor/reduce-codebase`. Geprüft wurden alle 107 versionierten Dateien:
Python-Definitionen, Imports und Parameter per AST und Referenzsuche, dynamische
Quellen-/HTTP-/Parser-Einstiege, HTML/JS/CSS-Verwendung, Tests, Dokumentation und
Installations-/CI-Konfiguration. Ignorierte Nutzerdaten, persönliche Einstellungen,
Caches und virtuelle Umgebungen gehören nicht zur Bilanz und wurden nicht verändert.
Eine fehlende interne Referenz gilt ausdrücklich nicht als Löschbeleg für eine API.

### Umgesetzte Kandidaten

Die Einsparungen sind physische Zeilen einschließlich Kommentaren und Leerzeilen.
Sie messen entfernte Wiederholungen; Formatierung und reine Dateiverschiebungen
wurden nicht als eigene Vereinfachung vorgenommen.

| Fundstelle | Bisheriges Verhalten | Vereinfachung | Erwartung → netto | Risiko | Validierung |
| --- | --- | --- | --- | --- | --- |
| `tests/fixtures/scoring_parity.json`, `test_scoring_parity.py` | 32 vollständige Eingaben wiederholen dieselben Basisfelder | Feste `job_defaults` plus explizite Abweichungen; jede Erwartung bleibt vollständig | ca. 750 → 779 Zeilen | Unbemerkte Änderung geerbter Eingaben | Alle 32 expandierten Eingaben und Ergebnisse exakt mit Git-Basis verglichen; Parität und Nichtmutation geprüft |
| `sources/{arbeitnow,get_in_it,himalayas,jobicy,startup_jobs,studysmarter}.py:collect_records` | Liste und ID-Menge parallel; erster Treffer gewinnt | Geordnetes Dictionary mit `setdefault`, am Ausgang weiterhin Liste | ca. 15 → 16 Zeilen | Reihenfolge, Duplikate, Teilfehler | Quellen-Tests einschließlich Pagination, Überschneidungen und Abdeckungsberichten |
| `deduplication.py:unique_sources`, `run_finder.py:build_run_summary` | Manuelle Mengen-/Listenpflege und Zähler | Geordnetes Dictionary beziehungsweise vorhandener `Counter` | ca. 8 → 8 Zeilen | Erster Quellen-Datensatz darf nicht überschrieben werden | Deduplizierungs-/Diagnosetests; zusätzlicher Identitäts- und Reihenfolgetest (+12 Testzeilen) |
| `text.py:_TextExtractor.text`, `sources/remotely.py:_RemotelyListParser` | Einmalige private Weiterleitung; `in_anchor` spiegelt stets den nichtleeren `href` | Join direkt am Aufruf; Link selbst als Capture-Zustand verwenden | ca. 6 → 6 Zeilen | HTML-Text oder Capture-Grenzen | Quellen-, Struktur- und Remotely-Tests; Parser-Callbacks für selbstschließende Tags bleiben erhalten |
| `experience.py:experience_is_optional/has_required_experience` | Schleifen liefern beim ersten Treffer True, sonst False | Kurzschließendes `any` mit gleicher Reihenfolge | ca. 2 → 2 Zeilen | Optionale und verpflichtende Anforderungen verwechseln | Scoring-Grenzfälle und alle 32 eingefrorenen Ergebnisse |
| `review.js:renderSourceLinks` | Einmalige Weiterleitung plus ungenutztes `make`-Binding | Links direkt in `render` leeren und mit vorhandenem Helfer füllen | ca. 4 → 4 Zeilen | Rendering und Navigation | Alle 16 Frontend-Tests, darunter vollständiges Laden und Entscheiden |
| `tests/test_review.py` | Elf identische JSON-Request-Konstruktionen | Lokaler Request-Helfer; Payloads, HTTP-Antworten und Assertions bleiben im Test | ca. 35 → 35 Zeilen | Ein Helfer könnte Fehlerfälle verdecken | Assertion-ASTs unverändert; alle 41 HTTP-/Review-Tests, besondere Header weiterhin explizit |
| README und dieser Abschnitt | README behauptete weiterhin Seitencode im HTML; abgeschlossene Refactoring-Planung wiederholte Verträge | Beschreibung berichtigen, alte Planung durch belegte Bestandsaufnahme ersetzen | Keine Code-Einsparung beansprucht | Hilfreiche Verträge verlieren | Datenfluss, Schnittstellen und Kompatibilitätsregeln oben beibehalten |

### Bewusst beibehaltene Kandidaten

Einsparungen hier sind grobe Löschpotenziale, keine empfohlenen Änderungen und
nicht Teil der erreichten Bilanz. Es besteht jeweils ein konkreter Gegenbeleg
oder kein Nutzen, der eine neue Abstraktion rechtfertigt.

| Fundstelle | Bisheriges Verhalten / mögliche Vereinfachung | Potenzial | Risiko und Entscheidung | Passende Validierung |
| --- | --- | --- | --- | --- |
| `scoring.py`, `review.py`, `console.py:progress_bar`, `review_data.py:memory_entry_for_job` | Alte Analyse-/Aktions-Importpfade und Helfer; Reexports und Wrapper entfernen | ca. 100 Zeilen | Dokumentierte und getestete Python-Schnittstellen; interne Nutzung beweist keine externe Nichtnutzung. Behalten | `test_public_api`, Scoring-/Review-/Console-Tests |
| `experience.py`, `location_rules.py`, `salary.py`, `matching_text.py`, `review_data.py`, `review_actions.py`, `state_compat.py` | Fachanalysen, HTTP, Transaktionen und Decoder getrennt; Module wieder zusammenführen | vor allem Verschiebung; einige Importzeilen | Zusammenlegung vergrößert zentrale Dateien; alte Importpfade müssten weiterhin funktionieren. Standort-Wrapper erhalten außerdem explizit gepatchte Einstellungen. Behalten | API-, Scoring-, Zustands- und HTTP-Tests |
| Kleine Firmenadapter und `search_plan.py` | Registrierte Quellen mit eigenem Namen, Cache und Parser; durch Registry/Factory oder Tupel ersetzen | ca. 50–100 Zeilen | `run_finder.SOURCES` und dokumentierter Quellenvertrag nutzen diese Module; Factory schafft neue Indirektion. Behalten | Firmenquellen-, Quellenmodell- und Runner-Tests |
| `models.py`, `operations.py`, `review.py`, HTML-Parser; StudySmarter-Parameter `now` | Framework-Callbacks und akzeptierte Aufrufsignaturen ohne direkten Namensaufruf/Parametergebrauch | wenige Zeilen | Dynamische Aufrufe, Protokolle und Keyword-Kompatibilität. Behalten; auch leere Parser-Callbacks verhindern das Standardverhalten | Modell-, Log-, HTTP- und Parser-Tests |
| `common.py:fetch_cached_details/enrich_cached_candidates`, StepStone, Arbeitnow, Remotely, manueller Cache | Ähnliche Abrufschleifen mit unterschiedlichen Frische-, Stopp-, Redirect- und Speicherregeln | ca. 100–200 Zeilen | Vereinheitlichung benötigt zahlreiche Optionen und könnte geschlossene Anzeigen, 403/429 oder Teilfehler anders behandeln. Behalten | Frischegrenzen, Ausfall-/Schließungsfälle und Cache-Tests |
| `remote.py:contains_any`, `matching_text.py:contains_any`, Gehalts-/Datumsparser der Quellen | Teilstring- versus Wortgrenzenvergleich; verschiedene Einheiten und Fallbacks | ca. 20–50 Zeilen | Nicht semantisch gleich: etwa Kommalisten, fehlende Jahresperioden, Unixzeit und Remote-Wörter. Keine pauschalen Aliase | Remote-, Scoring- und Quellen-Grenzfälle |
| `state_compat.py`, `application_documents.py:document_directory`, `memory.py:inferred_sources` | Alte JSON-/Notification-/Gehalts-/Dokumentformate und Quellen-IDs lesen | über 80 Zeilen | Gespeicherte Entscheidungen, Versandzustand oder Unterlagen könnten unerreichbar werden; Migrationen sind dokumentiert. Behalten | Versions-Fixtures, Wiederladen, Migration und Dokumenttests |
| `memory.py`, `applications.py`, `availability.py`, `review_actions.py` | Ähnliche Status-, Datums- und Transaktionsprüfungen | ca. 30–60 Zeilen | Erstfund versus Entscheidung, unbekanntes Datum versus heute, laufende Bewerbung und konkurrierende Änderungen sind unterschiedliche Fälle. Sperren, Rollback und Dateikompensation behalten | History-, Undo-, Parallelitäts-, Rollback- und Offline-Tests |
| `app.js`, `review.js`, `applications.js`, `reporting.py` | Ähnliche Labels, Datums- und Standortanzeige | ca. 15–30 Zeilen | Unterschiedliche Fallbacks und Seitentexte; ein gemeinsames Backend-/Frontend-Format wäre eine zusätzliche Schnittstelle. Behalten | Frontend-, Ausgabe- und HTTP-Tests |
| HTML/CSS/JS-Assets, Paketdaten | Drei Seiten laden gemeinsame und eigene Assets | keine belegte Einsparung | Alle Assets werden ausgeliefert/referenziert, alle CSS-Klassennamen sind in HTML/JS vorhanden. Keine Datei sicher ungenutzt | Referenzsuche, ausgelieferte Bytes/Content-Typen und vollständige Seitenskripte |
| Tests, `frontend_environment.cjs`, Paritäts-Fixture | Unit-, Paritäts-, HTTP- und DOM-Tests überlappen thematisch | mehrere hundert Zeilen bei Falllöschung | Unterschiedliche Prüfaussagen: Grenzen, vollständige Ergebnisse, Transport und Interaktion. Keine Fälle gelöscht; kleiner DOM-Ersatz benötigt keine Dependency | Testnamen/Assertions erhalten; vollständige Suiten |
| `requirements*.txt`, `pyproject.toml`, CI, README/Entwickleranleitung | PyYAML in zwei Installationswegen; Ruff nur Entwicklung; Anleitungen für Bedienung und Entwicklung | einzelne Zeilen | PyYAML wird tatsächlich geladen; beide Installationswege sind dokumentiert. Ruff-Pin und Plattformmatrix sichern reproduzierbare Prüfungen. Behalten | Importinventar, CI-Befehle und Referenzabgleich |

### Bilanz und Prüfgrenzen

| Bereich | Vorher | Nachher | Differenz |
| --- | ---: | ---: | ---: |
| Produktivcode (Paket inkl. Webassets, Runner und BAT) | 11.937 | 11.901 | −36 |
| Tests und Testdaten | 9.350 | 8.548 | −802 |
| Dokumentation (Markdown inkl. PR-Vorlage und LICENSE) | 591 | 622 | +31 |
| Konfiguration | 136 | 136 | 0 |
| Gesamt | 22.014 | 21.207 | −807 |

Dateizahl unverändert: 64 Produktiv-, 33 Test-, vier Dokumentations- und sechs
Konfigurationsdateien. Der vorherige Merge hatte netto 2.023 Zeilen ergänzt:
218 Produktivcode, 1.747 Tests/Testdaten und 58 Dokumentation. Die größte
vermeidbare Wiederholung lag in den Testeingaben, nicht in 2.000 neuen Codezeilen.
Die 32 Erwartungs-Dictionaries wurden nicht aus verändertem Produktivcode erzeugt.

Abschlussprüfung: Ruff-Lint und Formatprüfung, vollständige Python-Unittest-Suite,
Node-Frontend-Suite und `git diff --check`. Lokal: Windows, Python 3.13.0 und
Node 24.19.0; 314 Python-Tests und 16 Frontend-Tests. Die CI-Matrix mit Linux und
Python 3.11 wurde hier nicht ausgeführt. DOM-Tests ersetzen keinen visuellen
Browsertest. Kein vollständiger Finder-Lauf, Discord-Versand oder Zugriff auf
Live-Quellen ist Bestandteil dieser Refactoring-Validierung.
