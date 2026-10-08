# Architecture decisions

Short records of the decisions that shape the project, in the
[MADR](https://adr.github.io/madr/) style: the situation, the options that were
considered, the decision, its consequences and what would make it worth
revisiting. They describe why things are the way they are; the other documents
describe how they work.

| No. | Decision | Status |
| --- | --- | --- |
| [0001](0001-container-apps-modular-monolith.md) | One Python codebase on Azure Container Apps: a scheduled job and a review app | accepted |
| [0002](0002-layered-cost-limits.md) | Layered cost limits for the AI agent | accepted |
| [0003](0003-data-model-transition.md) | Move from JSON documents to tables in stages (expand and contract) | accepted, in progress |
| [0004](0004-public-endpoints-without-vnet.md) | Public endpoints protected by sign-in, RBAC and TLS instead of a VNet | accepted |
| [0005](0005-hybrid-sources.md) | Run the two sources that block Azure locally against the shared database | accepted |
| [0006](0006-agent-langgraph.md) | A LangGraph agent per job, without a checkpointer | accepted |
| [0007](0007-deployment-responsibility.md) | Plan in CI, approve by hand, migrate separately, roll back by image | accepted |

A new record gets the next number and the status `proposed` until it is
accepted. A decision that is replaced keeps its file, with the status
`superseded by NNNN`.
