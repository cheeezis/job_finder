# Entwicklung am Job Finder

Diese Anleitung beschreibt die gemeinsame Anwendung. Einrichtung und Bedienung
stehen in der [README](../README.md). Docker- und Azure-Arbeit wird separat auf
`cloud/azure-job-finder` gepflegt.

## Arbeitsumgebung und Prüfungen

Python 3.11 oder neuer wird benötigt. Die Befehle laufen im Repository-Stamm.
Unter Windows muss die virtuelle Umgebung nicht aktiviert werden:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check job_finder tests run_finder.py
.\.venv\Scripts\python.exe -m ruff format --check job_finder tests run_finder.py
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/frontend.test.cjs
```

Unter Linux/macOS lautet der Interpreterpfad `.venv/bin/python`. Node.js wird
nur für die Frontend-Tests benötigt; die CI verwendet Node.js 24. Für den
Python-Betrieb reicht `requirements.txt`. `requirements-dev.txt` installiert
zusätzlich die festgelegte Ruff-Version, damit lokale Prüfung und CI dieselben
Formatierungsregeln verwenden.

Der [GitHub-Workflow](../.github/workflows/checks.yml) prüft Python auf Windows
und Linux mit Python 3.11 und 3.13. Ein weiterer Job prüft Stil und Frontend.
Die Tests verwenden lokale Fixtures, temporäre Datenpfade und ersetzte
Netzwerkzugriffe. Ein vollständiger Finder-Lauf gehört nicht zur Testsuite.
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
| `job_finder/main.py` | Vorhandene Jobs bewerten und Ergebnisse zusammenstellen |
| `job_finder/sources/` | Quellen abrufen und in `Job`/`JobSource` umwandeln |
| `job_finder/models.py` | Datenmodell, Statuswerte und Serialisierung |
| `job_finder/deduplication.py` | Gleiche Anzeigen verschiedener Quellen zusammenführen |
| `job_finder/scoring.py`, `profile.py`, `remote.py` | Filter, Punkte, Profilregeln und Remote-Erkennung |
| `job_finder/memory.py` | SQLite-Zustand, stabile IDs und frühere Entscheidungen |
| `job_finder/availability.py` | Fehlende interessante Stellen auf bestätigte Schließung prüfen |
| `job_finder/review.py`, `applications.py`, `application_documents.py` | Review, Bewerbungsverlauf und lokale Unterlagen |
| `job_finder/app.js`, `review.html`, `applications.html` | Gemeinsame Browser-Helfer und die beiden Arbeitsansichten |
| `job_finder/reporting.py`, `notifications.py` | Review-Ausgabe und Discord-Warteschlange |
| `job_finder/user_settings.py`, `config.py`, `paths.py` | Konfiguration, Suche und lokale Dateipfade |

### Datenfluss eines Finder-Laufs

1. Die vorhandenen Zustandsdateien werden gesichert. Danach liefern Quellen
   Treffer und Abdeckungsberichte; URLs und quellenübergreifende Duplikate
   werden zusammengeführt. Sind mehr als die Hälfte der Quellen unbrauchbar,
   stoppt der Lauf vor dem Ersetzen von Job-Snapshot und Review-Ausgabe.
2. Ein erster Vorfilter bestimmt die Kandidaten für optionale Detailabrufe.
   Quellen dürfen deren Job-Objekte ersetzen oder bestätigte geschlossene
   Anzeigen aus der Liste entfernen.
3. Die angereicherten Jobs werden endgültig bewertet. Diese Ergebnisse
   bleiben mit den Job-Objekten verbunden, während das Gedächtnis anschließend
   IDs, Erstfund-Merkmale und bestehende Workflow-Entscheidungen zuordnet.
4. Die Offline-Prüfung betrachtet fehlende interessante Stellen ohne
   Bewerbungsverlauf und nur bei vollständig erfolgreichen bekannten Quellen.
   Netzwerkabrufe erfolgen außerhalb der SQLite-Schreibtransaktionen;
   vor einer Statusänderung wird der aktuelle Nutzerentscheid erneut geprüft.
5. Job-Snapshot und Empfehlungen werden geschrieben. Die Discord-Warteschlange
   wird aktualisiert; versendet wird nur mit `--notify`.

`is_new` beschreibt einen Erstfund im Suchlauf. `workflow_status="new"`
bedeutet dagegen, dass die Stelle noch nicht bearbeitet wurde. Der Review-
Filter „Neu“ richtet sich nach dem Workflow-Status und seinen Sichtbarkeitsfiltern.
Diese Merkmale dürfen bei Änderungen nicht gleichgesetzt werden.

### Manueller Import

`manual_import.import_manual_url` verarbeitet genau die eingereichte URL und
behält die übrigen Empfehlungen. Ein Vorfilterkonflikt bleibt als Warnung
sichtbar; er verhindert die manuelle Sichtung nicht. Die Funktion schreibt
Cache, SQLite-Zustand, Job-Snapshot und Empfehlungen nacheinander. Diese
Speicheroperationen bilden keine gemeinsame Transaktion über alle Dateien.

## Eine Quelle ergänzen

Eine Quelle liegt unter `job_finder/sources/<name>.py` und liefert Instanzen
des gemeinsamen Modells statt eigener Job-Dictionaries. Zunächst eine vorhandene
ähnliche Quelle prüfen; direkte JSON-LD-Karriereseiten können viele Aufgaben
an `sources/company_careers.py` delegieren.

Der vom Runner erwartete Vertrag:

| Schnittstelle | Verhalten |
| --- | --- |
| `SOURCE_NAME` | Stabiler Quellenname für IDs, Cache und Laufdiagnose |
| `fetch_jobs()` | Liefert eine Liste von `Job`; bei einem vollständigen Quellenfehler darf eine Exception propagieren |
| `fetch_jobs_with_report()` (optional) | Wird anstelle von `fetch_jobs()` verwendet und liefert `jobs`, `status`, optional `details` |
| `enrich_candidate_jobs(jobs, candidate_ids)` (optional) | Verändert die übergebene Liste beziehungsweise ihre Jobs und liefert die Anzahl betroffener Anzeigen |

Ein Abdeckungsbericht sieht beispielsweise so aus:

```python
from job_finder.sources.common import build_fetch_report

return build_fetch_report(jobs, failed_segments, total_segments)
```

Bei einem abgefangenen Teilfehler muss die Quelle diesen melden, etwa über
`record_partial_failure()` oder einen passenden Bericht. Ein stilles `[]`
könnte sonst als vollständig erfolgreiche Suche ohne Treffer interpretiert
werden. Der Runner verwendet die Zustände `success`, `empty`, `partial` und
`failed`, um fehlende Treffer richtig zu behandeln.

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
3. Das Modul in `run_finder.py` importieren und in `SOURCES` registrieren.
   Optionale Zugangsdaten nur über Umgebungsvariablen beziehen; bei Bedarf
   die Quelle nur bei vorhandener Konfiguration aktivieren.
4. Parser und Quellenausfälle mit kleinen Fixtures testen: reguläre Anzeige,
   fehlende optionale Felder, Teilfehler und Cache-/Schließungsfälle. Keine
   kompletten fremden Webseiten mit Trackingdaten als Fixtures übernehmen.
5. Quelle und besondere Einschränkungen in der README ergänzen.

Adapter erhalten weder Datenbankverantwortung noch persönliche
Workflow-Entscheidungen. Sie liefern Anzeigen und ihre Herkunft; Filter und
Speicherung bleiben in den gemeinsamen Modulen.

## Zustandsänderungen und Konfiguration

`paths.py` legt alle Pfade relativ zum Projekt fest. Es gibt keinen allgemeinen
`DATA_DIR`-Umgebungsvariablen-Schalter. Tests reichen abweichende Pfade über
Funktionsparameter oder gezielte Patches ein.

- `data/internal/job_finder.sqlite3` ist der dauerhafte Stellen- und
  Bewerbungszustand. Für Änderungen `edit_memory` verwenden, damit Lesen,
  Ändern und Speichern gemeinsam gesperrt sind. `update_memory` verändert die
  übergebenen Objekte, schreibt allein aber nicht in die Datenbank.
- `jobs.json` und `recommendations.json` sind neu erzeugbare Ausgaben;
  `*_cache.json` enthält wiederverwendbare Quelldetails.
- `notifications.json` enthält die Discord-Warteschlange und den Versandstatus.
  Auch `process_notifications(send=False)` verändert diese Datei.
- Bewerbungsunterlagen liegen separat unter `application_documents`.
  Die rotierenden Zustandsbackups enthalten diese Dateien nicht. Für eine
  vollständige Sicherung den gesamten Datenordner bei beendeter Anwendung
  sichern, wie in der README beschrieben.

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

Die Review-API ordnet POST-Routen kurzen Aktionsmethoden zu. Host-/Origin-Prüfung,
Größenlimit und JSON-Objektprüfung erfolgen gemeinsam vor dem Aufruf der Aktion;
Fehlerantworten und Antwortheader bleiben zentral. Im Browser verwenden die
Bewerbungsformulare denselben Speicherablauf, der ihre Aktionsbuttons auch nach
einem Fehler wieder freigibt.

## Python-Stil und hilfreiche Dokumentation

Orientierung geben [PEP 8](https://peps.python.org/pep-0008/) und
[PEP 257](https://peps.python.org/pep-0257/). Die konkrete, reproduzierbare
Konfiguration steht in [pyproject.toml](../pyproject.toml).

- Vier Leerzeichen einrücken; englische Bezeichner, Kommentare und Docstrings
  verwenden. Nutzertexte und Projektanleitungen bleiben deutsch.
- Ruff formatiert mit 88 Zeichen als Richtwert. Lange URLs, Regex-Ausdrücke
  oder Testdaten können länger bleiben, wenn Aufteilen die Lesbarkeit
  verschlechtert. `E501` wird deshalb nicht pauschal erzwungen; lange
  Kommentar- und Docstring-Absätze von Hand auf etwa 72 Zeichen umbrechen.
- Imports nach Standardbibliothek, Fremdpaketen und Projektcode gruppieren.
  Änderungen an Importreihenfolgen bei Modulen mit Initialisierungseffekten
  zusätzlich inhaltlich prüfen.
- Module, öffentliche Klassen, Funktionen und Methoden erhalten einen
  aussagekräftigen Docstring. Er beginnt mit einer kurzen Handlungsbeschreibung
  und einem Punkt. Weitere Absätze folgen nach einer Leerzeile.
- Bei komplexen Funktionen Eingaben, Rückgaben, Zustandsänderungen und relevante
  Fehler beschreiben. Insbesondere `None`, leere Werte und das Verändern
  übergebener Objekte erklären. Selbstverständliche Parameter nicht nur unter
  anderen Worten wiederholen. Ein festes Google-/NumPy-Abschnittsschema ist
  nicht vorgeschrieben.
- Kommentare begründen Sonderfälle oder Voraussetzungen. Sie sollen nicht
  jede Schleife und Zuweisung nacherzählen. Veraltete Kommentare beim Ändern
  des Verhaltens gleichzeitig aktualisieren.
- Testfälle erhalten sprechende Namen. Ruff verlangt dort keine zusätzlichen
  Docstrings. Konstruktoren und Standard-Protokollmethoden brauchen keine
  bloße Wiederholung; abweichendes Verhalten gehört trotzdem dokumentiert.
- Typannotationen sind bei Datenmodellen und neuen klaren Schnittstellen
  hilfreich. Eine flächendeckende Typmigration ist keine Voraussetzung für
  eine Dokumentationsänderung.

Automatisch formatieren und anschließend prüfen:

```powershell
.\.venv\Scripts\python.exe -m ruff check --select I --fix job_finder tests run_finder.py
.\.venv\Scripts\python.exe -m ruff format job_finder tests run_finder.py
.\.venv\Scripts\python.exe -m ruff check job_finder tests run_finder.py
```

Der Linter prüft Form und häufige Fehler. Ob ein Docstring das tatsächliche
Verhalten erklärt und ob eine Fachregel sinnvoll ist, bleibt Teil des Reviews.
