# 0007: Plan in CI, approve by hand, migrate separately, roll back by image

- Status: accepted
- Date: 2026-10-06, recorded 2026-10-08

## Context and problem

Every merge to `main` changes code that runs unattended in production, and some
merges change the infrastructure. A deploy must not apply something that was
not reviewed, must not leave the review open without sign-in, and must have a
quick way back. Schema changes are the hard part: an image can be swapped back
in a minute, a dropped column cannot.

## Considered options

1. Apply automatically on every merge.
2. Deploy by hand from the owner's computer.
3. CI builds and plans, a person approves exactly that plan, and migrations run
   separately and only add.

## Decision

Option 3:

- After a merge, CI builds the image, scans it and plans Terraform against the
  real Azure state. The plan is stored privately, and the run's summary shows
  only resources and actions.
- The approval in the GitHub environment `production` applies exactly this
  plan, checked by its checksum. An outdated plan or a commit that is no longer
  the current `main` aborts.
- The image rolls out by digest; the deploy checks that the review refuses
  access without sign-in and waits for a healthy revision. The previous image is
  noted as the way back, and the workflow `Rollback` restores it.
- Database migrations run separately and before the release that needs them,
  with an explicit approval. They only add tables and columns, so the previous
  image keeps working; cleanup migrations come later, deliberately.
- Drei Identitäten: Der PR-Plan nutzt die Read-only-Plan-Identität, der Build
  darf Images pushen. Der Release-Plan auf `main` nutzt bewusst die
  Apply-Identität im Environment `production-plan`: Sein Refresh benötigt
  unter anderem `listSecrets`. Dieses Environment ist auf `main` beschränkt;
  die manuelle Freigabe erfolgt anschließend für den gespeicherten Plan in
  `production`. Der Owner akzeptiert diese Aufteilung; eine vierte Identität
  wird nicht eingeführt.

## Consequences

- Nothing reaches production without a reviewed plan, and a rollback needs no
  Terraform or database change.
- A deploy needs a person to approve it; several merges in a row produce several
  waiting deploys, of which only the latest one is rolled out.
- Contract migrations (see [0003](0003-data-model-transition.md)) remove the way
  back to older images and need a backup and their own plan.
- Seit Contract 0007 prüft `Rollback` den zum Image-Digest gehörenden
  `sha-<12>`-Commit gegen `c55e17787bbe6e95cc6666771f9f890f3c4c4cf6`.
  Unbekannte oder mehrdeutige Herkunft bricht ab. Ein bekanntes älteres Image
  benötigt eine ausdrückliche Ausnahme mit Begründung und einen separat
  geprüften DB-Rückweg. Diese Prüfung liest die Datenbank nicht.

## Revisit when

- Deploys become so frequent that the manual approval slows work down; then a
  staging environment with automatic promotion is the next step.
- More than one person works on the project.
