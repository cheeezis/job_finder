# Datenbank und Bewerbungsdokumente gemeinsam wiederherstellen

Ein älterer Bewerbungsstatus ist erst dann wiederhergestellt, wenn auch die
dazugehörigen Dokumente geöffnet werden können. Eine längere Backup-Frist
allein belegt das nicht.

## Stand und nächste Schritte

Die derzeit im Terraform-Code festgelegten Fristen sind:

| Bestandteil | Aufbewahrung | Wiederherstellung |
| --- | --- | --- |
| PostgreSQL | sieben Tage | Point-in-Time-Restore (PITR) in einen neuen Server |
| Blob-Versionen | alte Versionen nach 30 Tagen zur Löschung vorgesehen | ausgewählte Version lesen und in ein eigenes Ziel kopieren |
| Blob- und Container-Soft-Delete | 14 Tage | gelöschte Versionen bzw. Container wieder verfügbar machen |
| Lokales Anwendungsbackup | sieben ZIP-Archive, keine feste Tagesfrist | leere Datenbank und leerer Dokumentordner |

Die drei Schritte für die gemeinsame Wiederherstellung sind:

1. **Lokal vorbereiten:** Verfahren dokumentieren, mit erfundenen Daten und
   Dateien üben, Prüfsummen und Dokumentverweise kontrollieren. Die unten
   beschriebene Probe erstellt nur eigene lokale Testdatenbanken.
2. **Aufbewahrung und Dokumentversionen abstimmen:** gemeinsames Zeitfenster
   wählen, Terraform-Änderungen und Kostenwirkung prüfen; historische
   Dokumentversionen eindeutig zuordnen. Vorschlag: 35 Tage. Dieser Vorschlag
   ist noch keine Änderung der produktiven Infrastruktur.
3. **In Azure nachweisen:** einen gewählten Datenbankzeitpunkt zusammen mit
   mindestens einem damaligen Dokument in isolierten Zielen wiederherstellen.
   Anmeldung, Dokumentzugriff, Referenzen und Dauer prüfen. Erst dieser
   Nachweis schließt die Azure-Restore-Abnahme ab.

## Vorschlag für die Aufbewahrung

PostgreSQL unterstützt sieben bis 35 Tage PITR. Für spät bemerkte Fehler im
Bewerbungsverlauf sind **35 Tage** ein sinnvoller Vorschlag. Blob- und
Container-Soft-Delete sollen mindestens dieses Fenster abdecken. Eine
Verlängerung erzeugt keine bereits abgelaufenen Daten neu; für zuvor gelöschte
Blobs gilt weiterhin deren ursprüngliche Soft-Delete-Frist.

Die aktuelle Lifecycle-Regel gilt für alle Block-Blobs im Account, also auch
für Bewerbungsdokumente. Ihre Frist zählt ab **Erstellung der Version**, nicht
ab dem Zeitpunkt, an dem sie durch eine neue Version ersetzt wurde. Eine
monatelang unveränderte Datei kann deshalb unmittelbar nach einer Änderung
zur Löschung vorgesehen sein. Einfach überall die Zahl 35 einzutragen ist
kein vollständiges Aufbewahrungsverfahren.

Vor einer Umstellung sollen Bewerbungsdokumente aus dieser pauschalen
Versionslöschung ausgenommen werden; für Terraform-State kann die bisherige
Regel auf den State-Container begrenzt bleiben. Das bewahrt zunächst mehr
Dokumentversionen auf. Ein späteres Aufräumen braucht den Nachweis, dass die
Version weder aktuell referenziert noch für das vereinbarte Restore-Fenster
benötigt wird. Dafür entsteht in diesem Vorbereitungsschritt kein eigener
Backup-Dienst.

Außerdem speichert die Anwendung heute Dokument-ID, Namen und Speicherpfad,
aber keine unveränderliche Blob-Version-ID oder Inhaltsprüfsumme in der
Dokumentreferenz. Ein Speicherpfad kann überschrieben werden. Für neue
Uploads sind deshalb feste Versionreferenzen mit Inhaltsprüfung vorzubereiten.
Für vorhandene Daten muss eine nachvollziehbare Zuordnung separat geprüft
werden; eine Version anhand ihres Zeitstempels auszuwählen reicht bei
mehrdeutigen Uploads nicht als Integritätsnachweis.

## Lokale Probe ohne echte Bewerbungsdaten

Voraussetzung ist ein laufender lokaler PostgreSQL-Testcontainer und eine
ignorierte `.env.postgres` mit dessen Testadministrator. Das Skript akzeptiert
ausschließlich `localhost` bzw. `127.0.0.1`, setzt die tatsächliche Zieladresse
fest auf Loopback und lehnt Verbindungen über einen libpq-Service ab.

```powershell
.\.venv\Scripts\python.exe scripts/restore_drill.py
```

Eine getrennte Arbeitskopie kann die lokale Zugangskonfiguration explizit
angeben:

```powershell
python scripts/restore_drill.py --env-file "PFAD_ZUR_LOKALEN_ENV.postgres" --report "tmp/recovery-drill.json"
```

Das Skript erstellt zwei neue Datenbanken mit zufälligen Namen und `_test`-
Suffix. Es öffnet keine bestehende Anwendungsdatenbank. In der Quelldatenbank
legt es eine erfundene Bewerbung mit Dokument, Verlauf, Review-Karte und
bereits versandter Benachrichtigung an. Nach dem ZIP-Backup verändert es
Bewerbungsstatus und Dokumentinhalt. Die Wiederherstellung in die zweite
Datenbank muss den vorherigen Stand und die ursprünglichen Dateibytes liefern;
die veränderte Quelle bleibt erhalten. Ein Restore in die bereits gefüllte
Quelle muss abgewiesen werden.

Geerbte Cloud-, Modell-, PostgreSQL- und Discord-Einstellungen werden für die
Probe entfernt bzw. lokal festgelegt. Es wird kein Worker gestartet und kein
Modell oder Webhook aufgerufen. Anschließend werden ausschließlich die von
diesem Aufruf angelegten Testdatenbanken und die temporären Dateien entfernt.
Der Bericht enthält Prüfergebnisse und die gemessene lokale Restore-Dauer,
keine Zugangsdaten oder Dokumentinhalte.

Die zusätzlichen Integrationstests prüfen beschädigte Dateibytes, fehlende
referenzierte Dokumente und den Schutz eines bereits gefüllten Dokumentziels.
Eine fehlgeschlagene Wiederherstellung darf keine Bewerbungsdaten oder zuvor
geschriebenen Dokumente zurücklassen. Leere, dabei entstandene lokale
Unterordner werden ebenfalls entfernt, damit ein erneuter Versuch am selben
Dokumentziel möglich ist; nicht leere Ordner und der Zielordner selbst bleiben
erhalten.

Diese Probe belegt das bestehende Anwendungs-ZIP-Verfahren. Sie verwendet
keinen Azure-Serverbackup und stellt keine historischen Cloud-Dateiversionen
wieder her. Ihre Dauer ist kein gemessener Azure-Wiederanlaufwert.

## Ablauf der späteren Azure-Probe

Vor dem Anlegen von Azure-Testressourcen müssen deren Namen, gewählter
Restore-Zeitpunkt, Kostenrahmen und Aufräumplan konkret freigegeben sein.
Produktionsserver und Laufzeitkonfiguration werden für die Probe nicht auf
ein Restore-Ziel umgestellt.

1. **Ausgangspunkt festhalten.** Aktuelle Sicherungsfristen, frühesten
   verfügbaren DB-Restore-Zeitpunkt und benötigte Dokumentversionen prüfen.
   Einen UTC-Zeitpunkt wählen, zu dem die Beispielbewerbung samt Dokument
   bereits gespeichert war. Eine aktuelle Sicherung belegt keinen Restore
   eines älteren Zeitpunkts.
2. **Neue DB wiederherstellen.** PITR in einen neu benannten Testserver;
   keine Wiederherstellung über den Produktionsserver. Nur gezielten
   Testzugriff einrichten. Firewallregeln, Serverparameter, Identitäten und
   nötige Berechtigungen werden separat geprüft, da nicht alles vom
   Quellserver übernommen wird.
3. **Historische Dokumente zuordnen.** Referenzen aus der wiederhergestellten
   Datenbank lesen und den belegten damaligen Versionen zuordnen. Dateien
   unter denselben Schlüsseln in einen eigenen leeren Testcontainer kopieren.
   Dafür nicht einfach alle heutigen Dokumente verwenden. Herkunft,
   Version-ID, Prüfsumme und Restore-Zeitpunkt gehören in ein privates
   Prüfprotokoll; echte Dateinamen und Bewerbungsdaten nicht in Git.
4. **Soft Delete gesondert behandeln.** Eine soft-gelöschte Version kann
   nicht direkt gelesen werden. `Undelete Blob` verändert den Quellbestand
   und stellt alle soft-gelöschten Versionen dieses Blobs wieder verfügbar.
   Ein solcher Eingriff braucht eine eigene konkrete Freigabe. Eine Probe
   ohne Produktionsschreibzugriff verwendet zunächst eine noch lesbare
   historische Version; die Wiederherstellung soft-gelöschter Daten bleibt
   dann ausdrücklich separat offen. Entsprechendes gilt für gelöschte
   Container.
5. **Zugriff und Referenzen prüfen.** Eine getrennte Review ausschließlich
   mit Test-DB und Test-Dokumentcontainer konfigurieren. Persönliche Anmeldung,
   wiederhergestellten Status und Dokumentdownload prüfen; Version und
   Prüfsumme müssen zum Protokoll passen. Alle referenzierten Dokumente auf
   Existenz prüfen. Keine regulären Worker oder Benachrichtigungen auslösen.
6. **Abnahme und Aufräumen.** Zeiten, Prüfergebnisse und offene Einschränkungen
   protokollieren. Nur die eigens angelegten Testressourcen anhand ihrer
   vollständigen Ressourcen-IDs entfernen. Die produktiven Löschsperren
   bleiben bestehen. Keine privaten Dokumente oder Zugangsdaten veröffentlichen.

## Wiederherstellungsziele und Kosten

Als Vorschlag für die spätere Abnahme gilt: bei einem aktuellen Restore
höchstens **fünf Minuten Datenverlust (RPO)**, und innerhalb von **60 Minuten
wieder nutzbare Review mit Dokumentzugriff (RTO)**. Microsoft beschreibt einen
WAL-Sicherungsverzug von im Allgemeinen bis zu fünf Minuten; dies ist kein
bereits gemessener Projektwert. Die Ziele müssen in der Azure-Probe geprüft
und bei Bedarf angepasst werden. Bei absichtlich gewähltem älteren Restore
ist der Abstand zum heutigen Stand eine bewusste Rücksetzung, kein gemessener
Backup-Verzug.

Das Protokoll erfasst Beginn, gewünschten und tatsächlich wiederhergestellten
Zeitpunkt, DB-Verfügbarkeit, Abschluss der Dokumentkopie, erfolgreichen
Review-Zugriff und Ende des Aufräumens. Es unterscheidet gemessene Dauer,
vereinbartes Ziel und noch nicht belegte Annahmen.

Die Verlängerung ist keine Zusage unveränderter Kosten: PostgreSQL-Backup-
Speicher bis zur Größe des provisionierten Servers ist enthalten, Mehrverbrauch
wird berechnet. Blob-Versionen und soft-gelöschte Daten belegen kostenpflichtigen
Speicher. Der zusätzliche Restore-Server und Dokumentkopien kosten während
der Azure-Probe ebenfalls Geld. Vor der Freigabe vorhandene Backup-Nutzung und
Dokumentvolumen messen; nach der Umstellung den tatsächlichen Verbrauch prüfen.

## Quellen

- [Microsoft: PostgreSQL backup and restore](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore)
- [Microsoft: Blob versioning](https://learn.microsoft.com/en-us/azure/storage/blobs/versioning-overview)
- [Microsoft: Blob soft delete](https://learn.microsoft.com/en-us/azure/storage/blobs/soft-delete-blob-overview)
- [Microsoft: Lifecycle-Regeln und deren Zeitbedingungen](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-policy-structure)
