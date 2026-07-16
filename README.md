# Job Agent

Ein kleiner Job-Agent fuer Junior Python/Data/AI-Rollen im 30-km-Radius um
Exampletown oder 100 Prozent remote aus Deutschland.

## Aktueller Stand

Der Agent kann:

- Arbeitsagentur-Suchergebnisse fuer definierte Suchbegriffe abrufen
- StepStone-Suchergebnisse fuer definierte Suchbegriffe abrufen
- StepStone-Detailseiten lokal cachen und Zugriffsgrenzen respektieren
- get-in-IT-Suchergebnisse ueber die oeffentliche JSON-API abrufen
- Jobdetail-Seiten importieren
- alle Quellen in ein verbindliches Jobmodell ueberfuehren
- Rohbeschreibung, Klartext sowie Veroeffentlichungs- und Abrufdaten speichern
- Jobs kostenlos vorfiltern und passende neue Jobs per OpenAI bewerten
- feste, laufuebergreifend vergleichbare Match-Scores von 0 bis 100 erzeugen
- quellenuebergreifende Duplikate zusammenfuehren
- bereits gesehene Jobs intern merken
- kompakte KI-Empfehlungen als JSON und Markdown ausgeben

## Nutzung

Abhaengigkeiten installieren:

```powershell
python -m pip install -r requirements.txt
```

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

Nur vorhandene interne Jobs regelbasiert pruefen, ohne Dateien zu erzeugen:

```powershell
python -m job_agent.main
```

Scoring-, Filter- und Deduplizierungsregeln testen:

```powershell
python -m unittest discover -s tests -v
```

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
data/internal/llm_cache.json LLM-Ergebnisse und ausstehende Analysen
data/output/recommendations.json kompakte finale KI-Ergebnisse
data/output/recommendations.md lesbare KI-Empfehlungen
profile.yaml                persoenliche Faktenbasis fuer das LLM
llm_evaluation/             getrenntes Labor fuer LLM-Vergleiche
llm_evaluation/fixtures/    blinde Testeingaben und menschliche Bewertungen
llm_evaluation/results/     lokale, nicht versionierte Modellergebnisse
requirements.txt            Python-Abhaengigkeiten
tests/                      automatisierte Scoring- und Filtertests
run_agent.py                kompletter Agentenlauf
review_jobs.bat             anklickbarer Start der Review-Oberflaeche
```
