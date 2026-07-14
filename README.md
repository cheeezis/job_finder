# Job Agent

Ein kleiner Job-Agent fuer Junior Python/Data/AI-Rollen im 30-km-Radius um
Exampletown oder 100 Prozent remote aus Deutschland.

## Aktueller Stand

Der Agent kann:

- Arbeitsagentur-Suchergebnisse fuer definierte Suchbegriffe abrufen
- StepStone-Suchergebnisse fuer definierte Suchbegriffe abrufen
- get-in-IT-Suchergebnisse ueber die oeffentliche JSON-API abrufen
- Jobdetail-Seiten importieren
- Jobs nach deinen Regeln bewerten
- feste, laufuebergreifend vergleichbare Match-Scores von 0 bis 100 erzeugen
- quellenuebergreifende Duplikate fuer das Review zusammenfuehren
- bereits gesehene Jobs in `data/seen_jobs.json` merken
- kompakte Review-Dateien fuer manuelles Feintuning erzeugen

## Nutzung

Kompletter Agentenlauf fuer alle angebundenen Quellen:

```powershell
python run_agent.py
```

Nur vorhandene importierte Jobs bewerten:

```powershell
python -m job_agent.main
```

Sample-Daten bewerten:

```powershell
python -m job_agent.main data/jobs_sample.json
```

Scoring-, Filter- und Deduplizierungsregeln testen:

```powershell
python -m unittest discover -s tests -v
```

## Struktur

```text
job_agent/                  Code
job_agent/config.py         Suchbegriffe und Suchorte
job_agent/console.py        gemeinsame Konsolenkonfiguration
job_agent/http.py           gemeinsame HTTP-Helfer
job_agent/profile.py        Profil-, Skill- und Scoring-Regeln
job_agent/deduplication.py  quellenuebergreifende Job-Deduplizierung
job_agent/remote.py         gemeinsame Remote-Erkennung
job_agent/reporting.py      JSON- und Markdown-Ausgaben
job_agent/search_plan.py    gemeinsame Suchplan-Helfer
job_agent/structured_data.py gemeinsame JSON-LD-Auswertung
job_agent/text.py           gemeinsame Text-/HTML-Helfer
job_agent/sources/          Quellenadapter
data/jobs_sample.json       Testdaten
data/jobs_imported.json     importierte Jobdetails
data/seen_jobs.json         lokales Job-Gedaechtnis
data/jobs_scored.json       generierte Scoring-Ergebnisse
data/jobs_review.md         generierte Review-Datei
data/job_feedback.json      gespeicherte manuelle Job-Bewertungen
tests/                      automatisierte Scoring- und Filtertests
run_agent.py                kompletter Agentenlauf
```
