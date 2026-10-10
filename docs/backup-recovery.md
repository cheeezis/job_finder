# Restoring database and application documents together

An older application status is only restored once the documents that belong to
it can be opened as well. A longer backup period alone does not prove that.

## Status and next steps

The starting point before the change was:

| Part | Retention | Restore |
| --- | --- | --- |
| PostgreSQL | seven days | point-in-time restore (PITR) into a new server |
| Blob versions | old versions scheduled for deletion after 30 days | read a chosen version and copy it to a target of its own |
| Blob and container soft delete | 14 days | make deleted versions or containers available again |
| Local application backup | seven ZIP archives, no fixed number of days | empty database and empty document folder |

The three steps for the joint restore are:

1. **Prepare locally:** document the procedure, practise with made-up data and
   files, check checksums and document references. The drill described below
   creates only its own local test databases.
2. **Align retention and document versions:** prepare the chosen window of 14
   days, check the Terraform changes and their cost; assign document versions
   unambiguously. Preparing the code does not yet change the production
   infrastructure.
3. **Prove it in Azure:** restore a chosen database point in time together
   with at least one document of that time into isolated targets. Check
   sign-in, document access, references and duration. Only this proof completes
   the acceptance of the Azure restore.

All three steps are done; the Azure drill passed on 05.10.2026 (see
[Result of the Azure drill](#result-of-the-azure-drill)).

## Chosen backup window: 14 days

Database and document delete protection share a window of **14 days**.
Terraform extends PostgreSQL PITR from seven to 14 days; blob and container soft
delete keep their earlier 14 days. The window limits the way back to an earlier
state, not the age of the stored applications or documents. An extension does
not recreate data that has already expired; for blobs deleted before, their
original soft-delete period still applies.

The earlier lifecycle rule applied to all block blobs in the account, so to
application documents as well. Its period counts from the **creation of the
version**, not from the moment it was replaced by a newer version. A file
unchanged for months can therefore be scheduled for deletion right after a
change. Simply entering the same number of days everywhere is no complete
retention procedure.

The prepared change excludes application documents from this blanket version
deletion; for the Terraform state the 30-day rule stays limited to the prefix
`tfstate/`. That keeps more document versions at first. A later cleanup needs
proof that the version is neither referenced now nor needed for the agreed
restore window. No separate backup service is created in this preparation step.

New uploads get a readable folder group of their own, so their keys do not
collide with earlier application documents. The original file names are kept.
Files are created only under keys still free; a concurrent upload must not be
overwritten either. The document reference contains a content checksum and, in
Blob Storage, the immutable version ID. Download and backup read this exact
version and check the bytes. With a missing version or a deviating checksum
there is no silent fallback to the current file.

Existing references without these fields stay readable. The deploy does not
rewrite them automatically; for an older restore their historical assignment has
to be checked separately. Choosing a version by its timestamp is not enough as
proof of integrity for ambiguous uploads.

The ZIP format stays backward compatible at version 1. On restore, the version
IDs of the source container are bound to the new target version for every
reference, or removed for a local file target. Document ID, display name and an
existing content checksum are kept. Several references to identical bytes of the
same key are possible. Different referenced contents at the same key explicitly
abort the backup, because ZIP v1 maps one content per key. Regular new uploads
avoid this collision through their own folder group.

ZIP v1 enthält zusätzlich `runs.json` mit der Laufhistorie und ihrer
Manifest-Prüfsumme. Alte ZIP-v1-Archive ohne diesen Eintrag bleiben lesbar und
stellen keine Läufe wieder her. Importierte `running`-Einträge werden als
`failed` gespeichert: Ein archivierter Lauf ist kein aktiver Worker. Seine
unbekannte Endzeit bleibt `NULL`; abgeschlossene Läufe und Kennzahlen bleiben
unverändert. Die Prüfung der importierten Zeilen erfolgt vor dieser Umstellung.

Das Restore-Ziel muss auch hinsichtlich `runs` leer sein. Geprüft werden die
Datenwurzeln `job_state`, `datasets`, `agent_usage`, `agent_fact_sheets` und
`runs`; ihre abhängigen Tabellen sind durch Foreign Keys abgedeckt.
`schema_version`, `alembic_version` und das historische `migration_runs` sind
Verwaltungsmarker und werden nicht als Anwendungsdaten importiert. Unbekannte
Manifest-Einträge werden abgewiesen; bei einem Restore-Fehler werden die
DB-Transaktion und die von diesem Restore neu geschriebenen Dokumente
zurückgenommen.

After lost upload responses, an unreferenced file version can remain although
the database transaction was rolled back. Such an upload must not overwrite
older referenced documents. A later cleanup needs the proof of references and
restore window.

## Local drill without real application data

It needs a running local PostgreSQL test container and an ignored
`.env.postgres` with its test administrator. The script accepts only
`localhost` or `127.0.0.1`, pins the actual target address to loopback and
refuses connections through a libpq service.

```powershell
.\.venv\Scripts\python.exe scripts/restore_drill.py
```

A separate working copy can name the local access configuration explicitly:

```powershell
python scripts/restore_drill.py --env-file "PATH_TO_LOCAL_ENV.postgres" --report "tmp/recovery-drill.json"
```

The script creates two new databases with random names and the suffix `_test`.
It opens no existing application database. In the source database it creates a
made-up application with a document, timeline, review card and an already sent
notification. After the ZIP backup it changes the application status and the
document content. The restore into the second database must deliver the earlier
state and the original file bytes; the changed source stays as it is. A restore
into the already filled source must be refused.

Inherited cloud, model, PostgreSQL and Discord settings are removed or set
locally for the drill. No worker is started and no model or webhook is called.
Afterwards only the test databases created by this call and the temporary files
are removed. The report contains check results and the measured local restore
time, no credentials or document contents.

The additional integration tests check damaged file bytes, missing referenced
documents and the protection of an already filled document target. A failed
restore must not leave application data or documents written before behind.
Empty local subfolders created on the way are removed too, so another attempt at
the same document target is possible; folders that are not empty and the target
folder itself stay.

This drill proves the existing application ZIP procedure. It uses no Azure
server backup and restores no historical cloud file versions. Its duration is
not a measured Azure recovery time.

## Course of the Azure drill

Before creating Azure test resources, their names, the chosen restore point in
time, the cost frame and the cleanup plan must be approved concretely.
Production server and runtime configuration are not switched to a restore
target for the drill.

1. **Record the starting point.** Check the current backup periods, the
   earliest available database restore time and the document versions needed.
   Choose a UTC point in time at which the sample application with its document
   was already saved. A current backup does not prove a restore of an older
   point in time.
2. **Restore a new database.** PITR into a newly named test server; no restore
   over the production server. Set up only targeted test access. Firewall
   rules, server parameters, identities and necessary permissions are checked
   separately, because not everything is taken over from the source server.
3. **Assign historical documents.** Read the references from the restored
   database and assign them to the documented versions of that time. Copy files
   under the same keys into an empty test container of their own. Do not simply
   use all of today's documents for that. Origin, version ID, checksum and
   restore time belong in a private check log; real file names and application
   data do not go into Git.
4. **Handle soft delete separately.** A soft-deleted version cannot be read
   directly. `Undelete Blob` changes the source data and makes all soft-deleted
   versions of that blob available again. Such an intervention needs its own
   concrete approval. A drill without production write access first uses a
   historical version that is still readable; the restore of soft-deleted data
   then stays explicitly open. The same applies to deleted containers.
5. **Check access and references.** Configure a separate review only with the
   test database and the test document container. Check personal sign-in,
   restored status and document download; version and checksum must match the
   log. Check all referenced documents for existence. Trigger no regular worker
   or notifications. The test review keeps at least one running container and
   becomes publicly reachable only once its revision runs healthily: a cold
   start from zero containers took about 27 seconds when measured and made a
   check with a 20-second wait fail. The separate test app registration needs
   the delegated permission `User.Read` (Microsoft Graph), otherwise Microsoft
   Entra refuses the sign-in with `AADSTS650056`.
6. **Acceptance and cleanup.** Log times, check results and remaining
   limitations. Remove only the test resources created for the drill, by their
   complete resource IDs. The production delete locks stay. Publish no private
   documents or credentials.

## Result of the Azure drill

On 05.10.2026 a deliberately older state (the day before) was restored into
separate test resources: a new PostgreSQL server through PITR, a private
document container of its own, a review of its own with its own sign-in only
for the project owner. Production server, production documents and runtime
configuration stayed unchanged. Measured from the first creation:

| Step | Duration |
| --- | --- |
| Database restored | 6:29 min |
| Application state and all referenced documents copied and checked by version and checksum | 7:06 min |
| Test review runs, anonymous access is refused | 8:35 min |
| Personal sign-in and document download confirmed | 21:33 min |
| All test resources removed and their absence checked | 24:08 min |

The time until the confirmed sign-in includes adding the missing `User.Read`
permission to the test app. The RTO target of 60 minutes is therefore met. The
deliberately older point in time measures no RPO. Two earlier attempts were
cleaned up completely: the first was interrupted, in the second database and
documents passed, the check of the review failed at the cold start. Both lessons
are in the procedure above.

## Recovery targets and costs

For the acceptance: with a current restore at most **five minutes of data loss
(RPO)**, and within **60 minutes a usable review with document access again
(RTO)**. Microsoft describes a WAL backup delay of generally up to five minutes;
this is not a value measured in the project. The RTO target is proven in the
Azure drill (see above), the RPO target for a current restore not yet. With a
deliberately chosen older restore, the distance to today's state is a
deliberate reset, not a measured backup delay.

The log records the start, the requested and the actually restored point in
time, database availability, completion of the document copy, successful review
access and the end of the cleanup. It distinguishes measured duration, agreed
target and assumptions not yet proven.

The extension is no promise of unchanged costs: PostgreSQL backup storage up to
the size of the provisioned server is included, more is charged. Blob versions
and soft-deleted data take up paid storage. The additional restore server and
document copies also cost money during the Azure drill. Before the approval,
measure the existing backup use and document volume; after the change, check the
actual consumption.

## Sources

- [Microsoft: PostgreSQL backup and restore](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore)
- [Microsoft: Blob versioning](https://learn.microsoft.com/en-us/azure/storage/blobs/versioning-overview)
- [Microsoft: Blob soft delete](https://learn.microsoft.com/en-us/azure/storage/blobs/soft-delete-blob-overview)
- [Microsoft: lifecycle rules and their time conditions](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-policy-structure)
