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
- Jobs nach deinen Regeln bewerten
- feste, laufuebergreifend vergleichbare Match-Scores von 0 bis 100 erzeugen
- quellenuebergreifende Duplikate fuer das Review zusammenfuehren
- bereits gesehene Jobs in `data/seen_jobs.json` merken
- kompakte Review-Dateien fuer manuelles Feintuning erzeugen

## Nutzung

Abhaengigkeiten installieren:

```powershell
python -m pip install -r requirements.txt
```

Kompletter Agentenlauf fuer alle angebundenen Quellen:

```powershell
python run_agent.py
```

Der neue Modellstand ist absichtlich nicht mit alten Laufdaten kompatibel.
Vor dem ersten Lauf nach dieser Umstellung werden die bisherigen generierten
Job-, Memory-, Review- und Cache-Dateien unter `data/` entfernt. Es findet
keine Altdatenmigration statt.

Nur vorhandene importierte Jobs bewerten:

```powershell
python -m job_agent.main
```

Scoring-, Filter- und Deduplizierungsregeln testen:

```powershell
python -m unittest discover -s tests -v
```

Lokale LLMs gegen den getrennten Blindtestsatz vergleichen:

```powershell
python -m llm_evaluation.compare_models gemma3:12b
```

## Struktur

```text
job_agent/                  produktiver Agentencode
job_agent/config.py         Suchbegriffe und Suchorte
job_agent/console.py        gemeinsame Konsolenkonfiguration
job_agent/http.py           gemeinsame HTTP-Helfer
job_agent/llm/              wiederverwendbare lokale LLM-Komponenten
job_agent/llm/contract.py   Rubrik und strukturierter Antwortvertrag
job_agent/llm/profile_loader.py Laden und Validieren des LLM-Profils
job_agent/llm/ollama.py     Client fuer die lokale Ollama-API
job_agent/models.py         einheitliches Job- und Statusmodell
job_agent/profile.py        Profil-, Skill- und Scoring-Regeln
job_agent/deduplication.py  quellenuebergreifende Job-Deduplizierung
job_agent/remote.py         gemeinsame Remote-Erkennung
job_agent/reporting.py      JSON- und Markdown-Ausgaben
job_agent/search_plan.py    gemeinsame Suchplan-Helfer
job_agent/structured_data.py gemeinsame JSON-LD-Auswertung
job_agent/text.py           gemeinsame Text-/HTML-Helfer
job_agent/sources/          Quellenadapter
data/jobs_imported.json     importierte Jobdetails
data/seen_jobs.json         lokales Job-Gedaechtnis
data/jobs_scored.json       generierte Scoring-Ergebnisse
data/jobs_review.md         generierte Review-Datei
data/stepstone_cache.json   gecachte StepStone-Jobdetails und letzte Linkliste
profile.yaml                persoenliche Faktenbasis fuer das LLM
llm_evaluation/             getrenntes Labor fuer lokale LLM-Vergleiche
llm_evaluation/fixtures/    blinde Testeingaben und menschliche Bewertungen
llm_evaluation/results/     lokale, nicht versionierte Modellergebnisse
requirements.txt            Python-Abhaengigkeiten
tests/                      automatisierte Scoring- und Filtertests
run_agent.py                kompletter Agentenlauf
```
