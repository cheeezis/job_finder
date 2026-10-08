# Database sign-in with Microsoft Entra (F10)

## Benefit and current scope

**Status 06.10.2026: active.** Worker, review and hybrid run sign in with Entra
tokens. Before the switch, all three identities passed the stage 3 checks in
Azure (token sign-in, rights matrix, forbidden DDL, rolled-back write access, a
new connection with a new token). The earlier database secrets stay in Key
Vault as the way back.

Since F09, worker and review use database roles of their own with defined
rights. F10 adds sign-in through their existing Azure identities: a short-lived
Entra token replaces the stored database password when the connection is
opened. The rights on tables and rows do not change. For the hybrid run, its
existing service principal can obtain a database token; its Azure client secret
is still needed for now.

The implementation covers token sign-in, a separate setup helper, Terraform
phases and a separate hybrid switch. Password sign-in stays the default. A merge
alone activates no Entra sign-in: server preparation and the runtime switch
have to be approved explicitly.

## Connection configuration

`JOBFINDER_DATABASE_AUTH` decides only the runtime sign-in:

| Value | Sign-in | Identity |
| --- | --- | --- |
| `password` (default) | earlier DSN | existing local or Azure database role |
| `managed_identity` | PostgreSQL token | explicit `JOBFINDER_MANAGED_IDENTITY_CLIENT_ID` |
| `service_principal` | PostgreSQL token | explicit `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |

For Entra, `JOBFINDER_DATABASE_URL` must contain a DSN without a password, with
explicit host, database, role and `sslmode=verify-full`. The configured CA
bundle is kept. An example with made-up details:

```text
JOBFINDER_DATABASE_AUTH=managed_identity
JOBFINDER_MANAGED_IDENTITY_CLIENT_ID=<client ID of the own runtime identity>
JOBFINDER_DATABASE_URL=postgresql://jobfinder_worker_entra@example.postgres.database.azure.com:5432/jobfinder?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt
```

The token is passed only in memory as the driver password. Never put a token in
a DSN, runtime file, Terraform state, command line or log. `get_token` is called
for every new connection; the Azure SDK manages valid tokens and their renewal.
Existing or nested transactions and session locks keep their connection. There
is no connection pool at present; a later pool has to use the same token call
when it creates new connections.

The credential choice uses ManagedIdentityCredential or ClientSecretCredential
explicitly, without falling back to the developer's Azure CLI sign-in. A
missing identity, an unknown mode, a password DSN, missing TLS verification or a
token error abort. There is no fallback to a password sign-in and no retry of
write transactions that may already have run. Entra driver and token errors are
passed on without sensitive exception contents.

Administrative maintenance stays separate: `transaction(admin=True)` and Alembic
use only `JOBFINDER_ADMIN_DATABASE_URL`. A runtime mode does not make the
application an Entra administrator. Local Compose tests still use test
passwords.

## Terraform phases

| `database_auth_phase` | Server and administrator | Runtimes |
| --- | --- | --- |
| `password` (default) | existing configuration | earlier separate password roles and vault references |
| `prepare` | Entra active in addition; own personal Entra admin | unchanged, including database secret access |
| `entra` | Entra and administrative password access stay available | own password-free DSNs, explicit managed identity; only the worker and review database secret references and their vault roles go away |

`prepare` and `entra` require `runtime_identity_phase=split` and
`runtime_access_verified=true`. `postgres_entra_admin_name` must contain the
sign-in name of the personal Entra administrator determined by
`owner_object_id`. A worker or review managed identity or the hybrid service
principal must not be administrator. PostgreSQL limits role names to 63
characters, and Azure stores the administrator name shortened accordingly (for
example for long guest accounts with `#EXT#`). Therefore enter the name
shortened to 63 characters; signing in with the full name still works. `entra`
additionally requires `database_entra_verified=true` after the actual Azure
acceptance. The GitHub pipeline accordingly passes `DATABASE_AUTH_PHASE`
(default `password`) and `DATABASE_ENTRA_VERIFIED` (default `false`) as
repository variables and `POSTGRES_ENTRA_ADMIN_NAME` as a repository secret:
the sign-in name can contain an email address, and variables appear unmasked in
the public workflow logs. Local Terraform values and GitHub values must match
before an apply. Deploy the image with token support first.

Activating Entra restarts the PostgreSQL server. A suitable maintenance window
is needed before the first `prepare` apply. Server, database and administrator
stay protected by `prevent_destroy`. After an activation, always return to
`prepare`, not to `password`: that neither removes Entra nor deletes the admin.
Server password authentication stays on in all phases.

The outputs `entra_principals` and `entra_database_urls` contain the planned
object IDs and password-free DSNs, no tokens or passwords. In the separate F09
mode they are provided even before `prepare`; their existence does not confirm
set-up SQL principals or an Azure sign-in.

## Setting up Entra principals

`scripts/create_entra_roles.py` uses two separate administrative logins to the
same server:

- `JOBFINDER_ENTRA_ADMIN_DATABASE_URL` from `.env.entra-azure` or the
  environment: a password-free DSN of the personal Entra admin to the database
  `postgres`, with `verify-full` and a matching local CA bundle. Only this
  maintenance helper obtains the token from an explicit `AzureCliCredential` in
  the given tenant.
- `JOBFINDER_ADMIN_DATABASE_URL` from `.env.postgres-azure` or the environment:
  the existing password admin to the application database. It assigns the F09
  access groups it manages. So the Entra admin neither has to take over these
  groups nor gets additional admin rights on them.

The locally ignored files stay separate from the active runtime file. Never pass
a token, password or DSN as a command-line argument. After the approved
`prepare` apply, export the non-secret principal output:

```powershell
terraform -chdir=infrastructure output -json entra_principals > tmp/entra-principals.json
uv run python scripts/create_entra_roles.py --principals-file tmp/entra-principals.json
# Only after the check and a separate production approval:
uv run python scripts/create_entra_roles.py --principals-file tmp/entra-principals.json --apply
```

The default is a technically read-only check. `--apply` creates only the three
new non-admin logins `jobfinder_worker_entra`, `jobfinder_review_entra` and
`jobfinder_hybrid_entra` through `pgaadauth_create_principal_with_oid` and
assigns them to the existing F09 NOLOGIN groups. The assignment uses the object
IDs of the managed identities and the hybrid service principal; client IDs or
display names are unsuitable for this. The helper reads back tenant, object ID,
principal type and admin flag and checks the role attributes, memberships,
complete effective table rights and the existing review row policy. Existing
password roles, passwords, table rights, schema and active configuration are not
changed.

Missing F09 boundaries, PUBLIC table rights, foreign memberships, an object ID
already bound elsewhere or a role of the same name without the right Entra
assignment abort. The helper does not repair such states automatically and sets
no acceptance marker.

The identities are set up in `postgres`, the memberships in the application
database. These two transactions cannot commit atomically together. If the
second phase fails, new logins without access groups can remain; the active
logins are kept. Checking and running again continues safely without rotating
passwords. Before the switch, accept all effective rights separately with the
actual token connections.

## Switching the hybrid run separately

The runner reads additional markers from the existing, ignored
`.env.runtime-azure`. To prepare, add them and keep the earlier values:

```text
JOBFINDER_HYBRID_DATABASE_AUTH=password
JOBFINDER_HYBRID_ENTRA_VERIFIED=0
JOBFINDER_HYBRID_ENTRA_DATABASE_URL=postgresql://jobfinder_hybrid_entra@example.postgres.database.azure.com:5432/jobfinder?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt
```

Set `JOBFINDER_HYBRID_ENTRA_VERIFIED=1` and
`JOBFINDER_HYBRID_DATABASE_AUTH=entra` only after the Azure acceptance. The
runner also requires the existing, checked F09 mode
(`JOBFINDER_RUNTIME_ACCESS=split`, `JOBFINDER_RUNTIME_CREDENTIALS_READY=1`). It
checks role, host, port and database against the earlier hybrid login, refuses
static passwords and requires `verify-full`. In the container it uses the Linux
CA bundle and passes `JOBFINDER_DATABASE_AUTH=service_principal`. The existing
explicit service principal details from `.env.docker-local` stay required; this
database switch does not remove its client secret. Way back: set only
`JOBFINDER_HYBRID_DATABASE_AUTH=password`. A missing marker also means the
earlier password mode.

## Cloud acceptance and activation

1. After the complete F09 operational acceptance, check backup and the way back,
   check the concrete `prepare` plan for changes and resource replacements and
   approve the restart window. Only then prepare the server and the own Entra
   admin.
2. Check the principal output and the setup logins; create the roles with the
   helper after a separate approval. Keep the old logins active for now.
3. With the actual own identities of worker, review and hybrid run, accept image
   pull, token sign-in and a new connection after the token expired. Confirm the
   rights including negative write, DDL and row checks. For the checks, start no
   complete finder run, evals, model generation or Discord sends; check writing
   in a transaction rolled back at the end. Confirm the administrative password
   emergency access separately.
4. Only then approve `database_entra_verified=true`, the concrete `entra` plan
   and the hybrid switch. Accept personal review access including documents and
   the following regular cloud and hybrid runs. Keep the old database secrets
   and roles as the way back; decide on deleting them separately later.

Switching off server-wide password authentication later also affects the
administrative emergency access and needs a decision of its own. Entra does not
change the existing network firewall.

## Local checks and their limit

The auth tests simulate credential choice, token change and error cases without
an Azure sign-in. The Entra rights tests simulate only the Azure-specific
principal API; real local PostgreSQL roles check F09 groups, review RLS, table
and DDL boundaries, repetition and errors between the setup phases. Hybrid tests
use made-up configuration and replace Docker, CLI and notifications. Terraform
tests simulate all providers and check default, preparation, activation and
missing approvals. These checks do not replace an Azure acceptance.

## Sources

- [Microsoft: sign-in with a managed identity](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity)
- [Microsoft: managing Entra principals](https://learn.microsoft.com/en-us/azure/postgresql/security/security-manage-entra-users)
- [Microsoft: authentication modes, restart and token lifetime](https://learn.microsoft.com/en-us/azure/postgresql/security/security-entra-concepts)
