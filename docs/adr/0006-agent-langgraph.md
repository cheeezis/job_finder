# 0006: A LangGraph agent per job, without a checkpointer

- Status: accepted
- Date: 2026-09-30, recorded 2026-10-08

## Context and problem

The first agent was a hand-written loop around the OpenAI client. It worked,
but every new tool or rule meant more custom control flow, and the structure of
a fact sheet was defined twice (prompt format and validation). The agent writes
one fact sheet per job, may search the web and look up earlier decisions, and
must stay within the cost guard ([0002](0002-layered-cost-limits.md)).

## Considered options

1. Keep the hand-written loop.
2. A single structured model call per job, without tools.
3. A LangGraph graph per job with a LangChain model, one Pydantic model for the
   fact sheet, and no checkpointer.

## Decision

Option 3. The graph has two nodes: `model` (cost guard, model call, booking) and
`tools` (`past_decisions`); web search runs as a built-in tool inside the model
call. The Pydantic model defines the structured output and checks the answer.
A spike compared the options before the port; option 2 stays as the baseline in
the evals.

There is no checkpointer: a fact sheet takes one short graph run, and an
interrupted job simply starts again in the next run. Persistence is the fact
sheet table, not the graph state.

## Consequences

- Adding a tool means adding a node or a tool function, not more loop logic.
- The evals compare the agent with the one-call baseline on the same cases, so
  the graph has to earn its extra calls.
- LangChain does not pass on the number of billed web searches, so the model
  wrapper reads it from the HTTP response.
- Without a checkpointer an aborted job costs its calls again; the cost guard
  limits that.

## Revisit when

- A fact sheet needs several steps that are worth resuming after a failure.
- Human approval inside a run becomes necessary.
- The baseline matches the agent's results at lower cost over several eval runs.
