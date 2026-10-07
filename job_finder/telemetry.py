"""Traces for Application Insights: ids, counts, timings and the verdict, never text.

The agent traces each run, the review each API request. Without
APPLICATIONINSIGHTS_CONNECTION_STRING nothing is exported and every span is a
no-op, so local runs, tests and evals behave as before. Worker and review sign
in with their managed identities; the resource accepts no key.

What a span may carry is decided here: span() takes only numbers, booleans
and short fixed words, and an exception leaves just its type name. Profile,
prompt, ad text, notes and model answers never reach a span.
"""

import os
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

CONNECTION_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"
# Long enough for an id or a word like "abgebrochen", too short for a sentence.
MAX_TEXT = 40

tracer = trace.get_tracer("job_finder")


def configure_tracing(environ=os.environ, service="jobfinder-worker"):
    """Send spans to Application Insights when it is configured; return the provider or None."""
    connection = environ.get(CONNECTION_ENV)
    if not connection:
        return None
    from azure.identity import DefaultAzureCredential
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # Otherwise the exporter also reports its own usage statistics to Microsoft.
    os.environ.setdefault("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", "true")
    credential = DefaultAzureCredential(managed_identity_client_id=environ.get("JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"))
    # The container may be gone a moment later, so spans are not kept on disk for a retry.
    exporter = AzureMonitorTraceExporter(
        connection_string=connection, credential=credential, disable_offline_storage=True
    )
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def start_tracing(environ=os.environ, service="jobfinder-worker"):
    """Turn tracing on for a process and say so; a failure only costs the traces."""
    try:
        tracing = configure_tracing(environ, service)
    except Exception as error:
        print(f"  Traces aus: {type(error).__name__}")
        return None
    if tracing is not None:
        print("  Traces: Application Insights")
    return tracing


def safe_attributes(attributes):
    """Keep numbers, booleans and short texts; drop None and anything longer."""
    return {
        key: value
        for key, value in attributes.items()
        if isinstance(value, bool | int | float) or (isinstance(value, str) and len(value) <= MAX_TEXT)
    }


@contextmanager
def span(name, **attributes):
    """Open a span with safe attributes; on an exception record only its type name."""
    with tracer.start_as_current_span(
        name, attributes=safe_attributes(attributes), record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except BaseException as error:
            current.set_status(Status(StatusCode.ERROR, type(error).__name__))
            raise


@contextmanager
def step(name, **attributes):
    """Time one step inside a traced request or run; outside of one, do nothing.

    Shared code such as a database connection runs very often in the finder,
    which has no trace; only where a span is open does a step add one.
    """
    if not trace.get_current_span().is_recording():
        yield None
        return
    with span(name, **attributes) as current:
        yield current


def annotate(current, **attributes):
    """Add safe attributes to an open span; a step outside of a trace passes None."""
    if current is not None:
        current.set_attributes(safe_attributes(attributes))
