# Bedienung, Regeln und Betrieb

Die deutsche Anleitung zum Job Finder. Eine englische Übersicht mit Architektur
steht in der [README](../README.md), Betrieb und Entwicklung beschreiben
[Betrieb](operations.md) und die [Entwickleranleitung](development.md).

Ein Python-Job-Finder für IT-Einstiegsstellen. Er sammelt Anzeigen aus mehreren
Quellen, führt gleiche Stellen zusammen, filtert klare Fehlgriffe regelbasiert
heraus und begleitet die Sichtung bis zur Bewerbung. Er läuft lokal oder in
einer eigenen Azure-Subscription (siehe [Betrieb](#betrieb)).

Nach außen gehen nur kompakte Stellenkarten und Laufstatistiken an Discord,
wenn ein Webhook gesetzt ist. Mit eingeschaltetem KI-Agenten gehen außerdem
Anzeige, persönliches Profil und frühere Entscheidungen an das Sprachmodell in
der eigenen Azure-Subscription und dessen Suchanfragen an die Bing-Suche.

## Funktionen

- Jobportale, offene Feeds und ausgewählte Karriereseiten; manueller Import
  einer einzelnen Anzeige per URL
- ein einheitliches Jobmodell, quellenübergreifende Deduplizierung und ein
  Gedächtnis für bekannte, entschiedene und inaktive Stellen
- regelbasierter Vorfilter für Standort, Remote-Anteil, Erfahrung,
  Beschäftigungsart, Reisetätigkeit und IT-Eignung; Junior-Hybrid-Sonderfälle
  und internationale Stellen lassen sich im Review zuschalten
- Review mit Interessant, Rückfrage, Warteliste, Ignorieren und Bewerben sowie
  eigener Notiz; läuft bei derselben Firma schon eine Bewerbung, nennt die Karte
  sie mit Status
- Warteliste für Stellen bei einer Firma, bei der eine andere Bewerbung noch
  offen ist: Die Stelle bleibt im Statusfilter „Warteliste“ sichtbar, und sobald
  jene Bewerbung abgeschlossen ist (Absage, keine Rückmeldung, selbst abgesagt),
  heißt es auf der Karte „jetzt entscheiden“
- optionaler KI-Agent (LangGraph und LangChain auf Azure OpenAI), der
  vorgefilterten Stellen einen Steckbrief mit Ampeln, Fazit und Kurzgrund
  schreibt, begrenzt durch einen mehrstufigen Kostenschutz
- Bewerbungsübersicht mit Verlauf, Gesprächsterminen, Gehaltsvorstellung (pro
  Monat oder Jahr eingegeben, als Jahresbrutto gespeichert) und Statistik
- Discord-Karten für neue Stellen und eine Laufstatistik; Quellenfehler bleiben
  isoliert und werden gemeldet

Der Vorfilter-Score ist eine Sortierhilfe, keine Eignungsprognose. Die
Entscheidung bleibt beim Nutzer.

## Quellen

| Gruppe | Quellen |
| --- | --- |
| Jobportale | Arbeitsagentur, StepStone, get-in-IT |
| Feeds und Aggregatoren | Arbeitnow, GermanTechJobs, Himalayas, Jobicy, Remotely, Startup Jobs, StudySmarter |
| Direkte Karriereseiten | Compose IT, bytewerk, RhönEnergie, JUMO, EDAG, CSS, Proemion, NETHINKS |
| Eigene Einträge | manueller Import einer öffentlichen Stellen-URL |

Startup Jobs läuft nur mit `STARTUP_JOBS_API_KEY`. Liefert eine Quelle nur
Teilergebnisse, etwa wegen Rate-Limits, meldet der Lauf das in Konsole, Log und
Discord. Sind mehr als die Hälfte der Quellen unbrauchbar, bricht er ab und
lässt den bisherigen Stand unverändert.

### Faire Abrufe

Die Quellen fragt ein erkennbarer Client (`job-finder/0.1`) ab. StepStone ruft
nur Adressen auf, die die robots.txt erlaubt: Suchseiten ohne Parameter, also
nur die erste Ergebnisseite je Suchbegriff und Ort, ohne Radius. Dafür sucht
StepStone mit allen allgemeinen Suchbegriffen. Zwischen zwei Abrufen liegen
1,5 Sekunden; bei HTTP 403 oder 429 stoppt die Quelle und nutzt den letzten
Cache-Stand, statt die Sperre zu umgehen. Ausnahme: Remotely prüft verlinkte
LinkedIn-Originale auf geschlossene Stellen mit einer Browser-Kennung, höchstens
einmal am Tag je Stelle.

## Einrichtung

```powershell
uv sync
Copy-Item user_settings.example.yaml user_settings.local.yaml
```

Benötigt werden Python 3.11 oder neuer, [uv](https://docs.astral.sh/uv/) (etwa
per `python -m pip install --user uv`) und PostgreSQL
([Einrichtung der Datenbank](operations.md#lokale-datenbank)). `uv sync` legt
`.venv` an und installiert genau die Versionen aus `uv.lock`.

`user_settings.local.yaml` (von Git ignoriert) enthält Suchort, Radius,
Pendlerorte, fachliche Stichwörter und die Grenzen des Agenten. Optional
ersetzen `search.terms`, `search.stepstone_terms` und `search.commuter_terms`
die eingebauten Suchbegriffe, und `sources.companies` wählt die Karriereseiten
einzelner Firmen; die Beispieldatei zeigt beides. Ohne die Datei gilt die
anonymisierte Beispielkonfiguration. Ein Finder-Lauf prüft die Einstellungen,
bevor er eine Quelle abfragt, und bricht bei einem Fehler mit dem Namen des
Feldes ab. Eine laufende Review übernimmt Änderungen erst nach einem Neustart. In Azure kommt der Inhalt aus dem
Key-Vault-Secret `JobfinderUserSettings`, das nach Änderungen neu gesetzt wird:

```powershell
& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe" -X utf8 -IBm azure.cli keyvault secret set --vault-name <key-vault> --name JobfinderUserSettings --file user_settings.local.yaml --encoding utf-8 --output none
```

Unter Windows liest `az keyvault secret set --file` die Datei sonst mit der
Windows-Kodierung (cp1252) statt UTF-8, auch mit `--encoding utf-8`: Aus
„München“ wird im Secret „MÃ¼nchen“. Der Aufruf oben startet die Python-Umgebung
der Azure CLI im UTF-8-Modus; `az.cmd` startet sie isoliert und übergeht
deshalb eine Umgebungsvariable wie `PYTHONUTF8`. Unter Linux und macOS genügt
`az keyvault secret set` mit denselben Argumenten. `--output none` verhindert,
dass die CLI den gespeicherten Inhalt ausgibt.

Den Namen des Key Vaults nennt `az keyvault list -g rg-jobfinder --query [].name -o tsv`.
Jeder Finder-Lauf nennt zu Beginn, woher seine Einstellungen stammen.

Optional sind `DISCORD_WEBHOOK_URL` für Discord und `JOBFINDER_REVIEW_HOST`
(Hostname der Review) für Direktlinks in den Karten. Geheimnisse gehören nicht
in YAML-Dateien oder ins Repository.

## Nutzung

```powershell
.\.venv\Scripts\python.exe run_finder.py
review_jobs.bat
```

`run_finder.py` sucht, bewertet und schickt neue Treffer an Discord, sofern
konfiguriert. `review_jobs.bat` (oder `python -m job_finder.review`) startet die
Oberfläche unter `http://127.0.0.1:8765`: Startseite mit manuellem Import,
`/review` für die Sichtung und `/applications` für Bewerbungen und Statistik.
Geänderte Bewertungsregeln wirken ab dem nächsten Finder-Lauf.

- **Review:** Der Filter „Neu“ zeigt alle noch nicht eingestuften Stellen,
  unabhängig vom Fundlauf. Internationale und Junior-Hybrid-Stellen sind eigene,
  standardmäßig ausgeschaltete Filter. Sortiert wird nach dem Fazit des Agenten,
  bei gleichem Fazit nach Vorfilter-Score.
- **Manueller Import:** Explizit hinzugefügte Anzeigen bleiben auch als
  internationale oder Junior-Hybrid-Sonderfälle sichtbar. Nach dem Import
  öffnet sich die passende Karte direkt.
- **Zusätzliche Anzeige zur Bewerbung:** Gehört eine Karte zu einer bereits
  gespeicherten Bewerbung, wähle „Bestehender Bewerbung zuordnen“ und die
  passende Bewerbung. Das funktioniert auch bei abweichenden Firmennamen,
  etwa bei Vermittlern. Die Bewerbung behält ihren Arbeitgeber, Verlauf,
  Gehalt und ihre Unterlagen; die zusätzliche Anzeige und ihre Sichtungsnotiz
  erscheinen dort unter „Zugeordnete Anzeigen“.
- **Notiz:** „Meine Notiz“ hält den Grund einer Entscheidung fest (bis 2.000
  Zeichen) und wird beim Verlassen der Karte gespeichert. Der Agent liest bei
  ähnlichen Stellen die ersten 300 Zeichen mit.
- **Eine Karte je Stelle:** Anzeigen mit gleichem Titel und gleicher Firma
  bilden eine Karte mit allen Orten und Links, über Portale und Läufe hinweg;
  eine Entscheidung gilt für alle. Eine schon entschiedene Stelle übernimmt eine
  neue Anzeige nur ohne neuen Ort oder wenn beide komplett remote sind.
- **Gespräche:** Die Karte hebt das nächste Gespräch hervor; ist es vorbei,
  zeigt sie „Letztes Gespräch“, bis ein neues Ereignis eingetragen wird. Mit
  „Gespräch absagen“ endet die Bewerbung als „Selbst abgesagt“; das zählt weder
  als Absage noch als „Keine Rückmeldung“.
- **Reihenfolge der Bewerbungen:** Zuerst kommen bevorstehende Gespräche, der
  nächste Termin ganz oben. Danach folgen vergangene Gespräche, das jüngste
  zuerst. Die übrigen offenen Bewerbungen stehen darunter, weiterhin nach
  Bewerbungsdatum mit der neuesten zuerst.
- **Links:** Die Karte zeigt alle Anzeigen, die die Review derselben Stelle
  zuordnet, auch von anderen Portalen.
- **Keine Rückmeldung:** Kommt 14 Tage nach Bewerbung, Rückmeldung oder letztem
  Gesprächstermin nichts Neues, zeigt die Übersicht „Keine Rückmeldung“; ein
  späteres Ereignis öffnet die Bewerbung wieder. Der Status lässt sich auch
  selbst eintragen. Die Antwortquote zählt nur abgeschlossene Bewerbungen.

## Betrieb

- **Lokal:** Finder und Review laufen auf dem eigenen Rechner, PostgreSQL in
  Docker Compose, Dokumente unter `data/internal/application_documents`. Vor
  jedem Finder-Lauf entsteht ein rotierendes Backup.
- **Azure:** Der Finder läuft als Container-Apps-Job um 06:00 und 16:00 UTC,
  ohne StepStone und Remotely. Die Review ist eine Container App hinter einer
  Entra-ID-Anmeldung für das eigene Konto; jeder Deploy prüft, dass sie ohne
  Anmeldung nichts ausliefert. Daten liegen in Azure PostgreSQL,
  Dokumente im Blob Storage, Geheimnisse im Key Vault; statt ZIP-Backups
  sichern Point-in-Time-Restore und Blob-Versionierung.
- **Hybrid:** StepStone und Remotely liefern aus Azure keine Treffer. Ein
  lokaler Windows-Task startet sie täglich in Docker, mit dem Image des
  Azure-Workers und gegen dieselbe Datenbank (`scripts/run_local_hybrid.py`);
  danach schreibt der Agent die Steckbriefe dieser Stellen. Jeder Lauf ersetzt
  nur die Anzeigen seiner eigenen Portale. Das Skript startet Docker bei Bedarf,
  schreibt ein Log und meldet Fehlschläge in Discord.

Einrichtung, Backups, Azure und Zugriffswege beschreibt der
[Betrieb](operations.md).

## KI-Agent und Kostenschutz

Nach jedem Finder-Lauf in Azure und im Hybrid-Lauf schreibt der Agent
(`job_finder/agent/`, ein LangGraph-Graph mit einem LangChain-Modell auf dem
eigenen Azure OpenAI) für die besten wartenden Stellen einen Steckbrief: sieben
Ampelzeilen (Status, Berufseinstieg, Fachlicher Fit, Lücken, Homeoffice /
Standort, Reiseanteil, Gehalt), bis zu zwei Zusatzzeilen, Fazit und Kurzgrund.
Er nimmt unentschiedene, im Standard-Review sichtbare Stellen ohne Steckbrief,
die Einstiegsstellen sind oder mehr als 50 Punkte haben, die besten zuerst. Er
darf im Web suchen und frühere Entscheidungen samt Notizen nachschlagen, aber
nichts ändern. Anzeigen und Webseiten sind für ihn Material, keine Anweisungen,
und als Quellen bleiben nur Links, die er tatsächlich gesehen hat. Seine
Maßstäbe stehen in `job_finder/agent/instructions.py`.

Er läuft nur, wenn `agent.enabled: true` gesetzt ist, der Lauf die
Modell-Adresse kennt und ein Profil (`profile.local.yaml`) vorhanden ist. In
Azure setzt Terraform die Adresse und das Profil kommt aus dem Key-Vault-Secret
`JobfinderProfile`; der Hybrid-Lauf nimmt beides aus lokalen Dateien
(`.env.docker-local`, `profile.local.yaml`). Nach Profiländerungen das Secret
neu setzen:

```powershell
& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe" -X utf8 -IBm azure.cli keyvault secret set --vault-name <key-vault> --name JobfinderProfile --file profile.local.yaml --encoding utf-8 --output none
```

Zum UTF-8-Aufruf unter Windows siehe die Einstellungen oben.

Endet ein Steckbrief mit einer unvollständigen oder unbrauchbaren Antwort,
versucht der nächste Lauf diese Stelle genau noch einmal; die Review nennt das.
Eine abgelehnte Anfrage oder eine erreichte Grenze je Stelle wird nicht
wiederholt, weil sie nur erneut Geld kosten würde.

Jeder Steckbrief merkt sich, auf welcher Grundlage er entstand: Profil (nach
Inhalt, Kommentare zählen nicht), Regeln, Anzeige, Modell samt Denkaufwand und
Ablauf des Agenten. Hat sich davon seitdem etwas geändert, zeigt die Review
„Veraltet“ mit den geänderten Teilen; neu bewertet wird dadurch nichts. Der
Knopf „Neu bewerten“ am Steckbrief markiert die Stelle, und der nächste
Agent-Lauf schreibt ihren Steckbrief vor allen anderen neu, auch wenn sie schon
entschieden ist. Die Review selbst ruft dabei kein Modell auf.

Jeder Lauf nennt im Abschnitt „Steckbriefe (Agent)“, warum der Agent nicht lief
oder wie viele Steckbriefe fertig, abgebrochen, offen oder veraltet sind und was
der Tag gekostet hat. Bricht der Agent ab oder stoppt er vor der letzten Stelle, etwa an
der Tagesgrenze, meldet das zusätzlich eine Warnung in Discord.
`agent.reasoning_effort` stellt den Denkaufwand ein (Standard `medium`).

**Kostenschutz:** Vor jedem Modell- und Werkzeugaufruf prüft der Kostenwächter
(`job_finder/agent/cost_guard.py`) den Schalter, den hinterlegten Preis, das
Kostenbuch `agent_usage` mit Tages- und Monatsgrenze (deutsche Zeit, alle Läufe
zusammen) sowie die Grenzen der Stelle für Kosten, Aufrufe und bezahlte
Websuchen (`job_max_web_searches`, Standard 3). Eine erreichte Stellengrenze
beendet nur diese Stelle, Tages- oder Monatsgrenze den Agenten für den Lauf;
eine Grenze kann um höchstens einen Aufruf überschritten werden. Bei HTTP 429
wartet er 5 bis 65 Sekunden und versucht es bis zu dreimal neu. Die Grenzen
stehen im Abschnitt `agent` der Einstellungen, Standardwerte in
`user_settings.example.yaml`. Ungültige Werte schalten den Agenten ab, und feste
Obergrenzen im Code (5 € pro Tag, 50 € pro Monat) fangen Tippfehler ab.

In Azure wirken zusätzlich eine Drossel der Modell-Bereitstellung
(`infrastructure/openai.tf`), ein Token-Alarm und ein Monatsbudget mit
E-Mail-Warnungen (`infrastructure/monitoring.tf`). Das Modell-Konto hat keine
API-Schlüssel. Aufrufen dürfen es nur Worker, Hybrid-Lauf und das eigene Konto,
jeweils mit einer Rolle, die an der Bereitstellung nichts ändern kann.

## Regeln im Überblick

Den Ablauf eines Laufs beschreibt die
[Entwickleranleitung](development.md#datenfluss-eines-finder-laufs).

- **Alter:** Automatisch gefundene Anzeigen, deren bekanntes
  Veröffentlichungsdatum mehr als 60 Tage zurückliegt, fallen heraus; ein
  fehlendes Datum allein nicht. Manuell importierte alte Anzeigen bleiben mit
  Warnung prüfbar.
- **Benachrichtigungen:** Neue Stellen können an Discord gehen. Spätere
  Textänderungen lösen weder eine neue Nachricht noch ein erneutes „Neu“ aus.
- **Offline-Prüfung:** Interessante und wartende Stellen bleiben auch ohne
  Suchtreffer vorgemerkt. Fehlen sie in einem Lauf, dessen Quellen alle vollständig waren,
  prüft der Lauf ihre URLs. Erst wenn alle eindeutig geschlossen sind (HTTP
  404/410 oder Schließungshinweis), wechselt die Stelle mit Datum und Grund auf
  „Nicht interessant“; Bewerbungen bleiben unberührt. Diese Karten lädt die
  Review erst, wenn der Filter „Alle Status“ oder „Nicht interessant“ gewählt
  ist oder ein Link auf eine solche Stelle zeigt; die übliche Liste bleibt so klein.
- **Caches:** Detaildaten gelten sieben Tage als frisch. Bei einem
  Netzwerkfehler darf ein höchstens 14 Tage alter Eintrag als markierter
  Fallback erscheinen. Ein teilweise fehlgeschlagenes Suchsegment setzt keine
  Stellen inaktiv.
- **Quellen:** Arbeitnow lädt nur beim bekannten Platzhaltertext die
  Originalanzeige nach, deren URL Review und Discord dann bevorzugen. Remotely
  übernimmt nur Anzeigen der letzten sieben Tage und lässt LinkedIn-Originale
  weg, die keine Bewerbungen mehr annehmen. get-in-IT und StudySmarter laden
  Detailseiten erst nach dem ersten, großzügigen Vorfilter; StudySmarter sucht
  im Radius und deutschlandweit nach Remote-Stellen. Gehaltsspannen von
  GermanTechJobs gelten als Euro brutto pro Jahr.

## Tests und Entwicklung

```powershell
uv sync
uv run python scripts/test_postgres.py
uv run ruff check .
node --test tests/frontend.test.cjs
```

Die Python-Tests brauchen eine eigene Testdatenbank
([Betrieb](operations.md#lokale-datenbank)), Node.js ab Version 18 nur
die Frontend-Tests. Die [Entwickleranleitung](development.md) erklärt
Aufbau, Datenfluss, neue Quellen und Stilregeln. GitHub Actions prüfen jeden
Pull Request; nach einem Merge auf `main` rollen sie Infrastruktur und Image
erst nach manueller Freigabe aus.

```text
job_finder/            Kernlogik, Review und Bewerbungen
job_finder/sources/    Quellenadapter
job_finder/agent/      KI-Agent mit Kostenschutz
scripts/               Einrichtung, Tests und lokaler Hybrid-Lauf
infrastructure/        Terraform für Azure
docs/                  Bedienung, Betrieb und Entwicklung (deutsch)
tests/                 automatisierte Tests
run_finder.py          Einstieg für einen Finder-Lauf
review_jobs.bat        Start der lokalen Oberfläche
data/                  lokale Laufdaten (nicht versioniert)
```

## Lizenz

MIT, siehe [LICENSE](../LICENSE).
