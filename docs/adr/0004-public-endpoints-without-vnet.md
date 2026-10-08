# 0004: Public endpoints protected by sign-in, RBAC and TLS

- Status: accepted
- Date: 2026-09-30, recorded 2026-10-08

## Context and problem

The worker, the review and the GitHub pipeline need the database, Blob Storage,
Key Vault and the model. The Container Apps environment runs in the Consumption
profile and has no fixed outbound IP. A network boundary would need private
endpoints, and that only works in an environment created with VNet integration.
The data is the personal data of one user; there is no third-party data and no
compliance requirement.

## Considered options

1. A new VNet-integrated environment with private endpoints for database,
   storage and Key Vault (about 6.30 € per endpoint and month), maybe a NAT
   gateway for a fixed outbound IP (about 31 € a month).
2. Keep public endpoints and put the protection into identity: sign-in for the
   review, RBAC without account keys for the data services, TLS with certificate
   verification and restricted database roles.

## Decision

Option 2. The review accepts only the owner's Entra ID account and is made
public only after its sign-in exists; every deploy checks that it serves nothing
without sign-in. Storage and the model accept only Entra ID, with account keys
switched off. Each runtime has its own identity and only the secrets and rights
it needs ([runtime access](../runtime-access.md)). PostgreSQL requires TLS,
clients verify with `verify-full`, and the firewall allows Azure services and
the owner's current IP.

## Consequences

- No fixed costs for networking, and the local hybrid run and admin access keep
  working without a VPN.
- The firewall rule for Azure services allows every Azure subscription, so the
  database login and TLS carry the protection; Trivy reports this, and the
  trade-off is documented in `infrastructure/.trivyignore.yaml`.
- The home IP changes often; the hybrid script updates its firewall rule before
  every run.

## Revisit when

- More users or third-party applicant data come in.
- A compliance requirement asks for private networking.
- The environment has to be recreated anyway; then VNet integration costs little
  extra effort.
