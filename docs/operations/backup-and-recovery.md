# Backup and recovery

Status: proposed 2026-10-06. These targets were selected for the maintenance
proposal; no production backup schedule or storage destination has been activated.
Historical migration backups and verified rollback archives remain separate
evidence and must not be deleted by routine retention.

## Proposed targets

| Item | Target |
| --- | --- |
| Owner | Project operator; confirm a named owner before activation |
| Frequency | Daily, including when registrar polling is stopped |
| Retention | 30 days of successful daily backups |
| Maximum data loss (RPO) | 24 hours |
| Recovery time (RTO) | 4 hours from incident declaration |
| Coverage | Every configured enrollment database and the separate bot database |

Keep backups outside the VM's failure domain in private encrypted storage.
Credentials in `.env` need a separate protected recovery process. Database backups
contain private bot state and must never enter Git, CI artifacts, or public output.

## Backup procedure

Use SQLite's online backup API for each database. Copying the main file of a live
database can omit committed WAL data. Use unique dated filenames, restrict file
permissions to the operator, and validate `integrity_check` and
`foreign_key_check` before publishing the result. The enrollment command is
documented in [DATABASE.md](../../DATABASE.md).

Record completion time, source identity, destination identity, file size, SHA-256,
and validation results without logging credentials or database contents. Upload
only validated files. Delete expired routine backups only after a new backup has
been verified off-host; retain migration rollback archives independently.

Alert the operator when the last successful backup is older than 24 hours or a
validation/upload fails. Alert transport and its recipient require approval.

## Restore rehearsal and incident recovery

1. Download a selected backup into an isolated temporary directory. Verify its
   recorded hash, SQLite integrity, foreign keys, and expected schema.
2. Read the latest enrollment snapshot and reporting position for every restored
   semester. Check bot-store schema and aggregate counts without printing user
   identifiers. Generate the dashboard locally from the restored enrollment copies.
3. Record elapsed restoration time and newest recoverable observation. Rehearse
   before activation and quarterly thereafter; measure the proposed RPO/RTO.
4. For an actual incident, obtain authorization to stop affected writers and
   replace their databases. Preserve the damaged files, restore under the runtime
   owner's permissions, and verify state before an authorized restart.
5. Bot recovery can replay deliveries or lose newly created watches after the
   backup point. Reconcile delivery state before resuming the bot. Pages upload
   remains a separately authorized action.

## Activation requirements

Before requesting production activation, select the storage location, encryption
and access policy, named owner, daily execution time, failure-alert destination,
and cost. Prepare and test the scheduled job, upload verification, retention, and
restoration against disposable data. Then request explicit approval for the
specific production resources and service changes. This proposal alone does not
establish a measured recovery guarantee.
