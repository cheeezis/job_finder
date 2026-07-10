# Job Agent

Ein kleiner Job-Agent fuer Junior Python/Data/AI-Rollen rund um Fulda oder remote.

## Aktueller Stand

Der Agent kann:

- Arbeitsagentur-Suchergebnisse fuer definierte Suchbegriffe abrufen
- StepStone-Suchergebnisse fuer definierte Suchbegriffe abrufen
- get-in-IT-Suchergebnisse abrufen
- Jobdetail-Seiten importieren
- Jobs nach deinen Regeln bewerten
- bereits gesehene Jobs in `data/seen_jobs.json` merken

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

## Struktur

```text
job_agent/                  Code
job_agent/config.py         Suchbegriffe und Quellenlimits
job_agent/profile.py        Profil-, Skill- und Scoring-Regeln
job_agent/sources/          Quellenadapter
data/jobs_sample.json       Testdaten
data/jobs_imported.json     importierte Jobdetails
data/seen_jobs.json         lokales Job-Gedaechtnis
run_agent.py                kompletter Agentenlauf
```
