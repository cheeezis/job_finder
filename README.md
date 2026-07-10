# Job Agent

Ein kleiner Job-Agent fuer Junior Python/Data/AI-Rollen rund um Fulda oder remote.

## Aktueller Stand

Der Agent kann:

- Arbeitsagentur-Suchergebnisse fuer definierte Suchbegriffe abrufen
- Jobdetail-Seiten importieren
- Jobs nach deinen Regeln bewerten
- bereits gesehene Jobs in `data/seen_jobs.json` merken

## Nutzung

Kompletter Agentenlauf fuer alle angebundenen Quellen:

```powershell
python run_agent.py
```

Arbeitsagentur-Lauf als alter Alias:

```powershell
python run_arbeitsagentur.py
```

Nur vorhandene importierte Jobs bewerten:

```powershell
python main.py
```

Sample-Daten bewerten:

```powershell
python main.py data/jobs_sample.json
```

Nur Arbeitsagentur-Links suchen:

```powershell
python -m job_agent.arbeitsagentur_search
```

Nur vorhandene Links importieren:

```powershell
python -m job_agent.arbeitsagentur_import
```

## Struktur

```text
job_agent/                  Code
job_agent/sources/          Quellenadapter
data/jobs_sample.json       Testdaten
data/job_links.txt          generierte Arbeitsagentur-Links
data/jobs_imported.json     importierte Jobdetails
data/seen_jobs.json         lokales Job-Gedaechtnis
run_agent.py                kompletter Agentenlauf
run_arbeitsagentur.py       Alias fuer run_agent.py
main.py                     Bewertungs-Einstieg
```
