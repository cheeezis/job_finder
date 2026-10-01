"""Traces of the agent for Application Insights: ids, counts and the verdict, never text.

Without APPLICATIONINSIGHTS_CONNECTION_STRING nothing is exported and every
span is a no-op, so local runs, tests and evals behave as before. The worker
signs in with its managed identity; the resource accepts no key.

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

tracer = trace.get_tracer("job_finder.agent")


def configure_tracing(environ=os.environ):
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
    # The container is gone after the run, so spans are not kept on disk for a retry.
    exporter = AzureMonitorTraceExporter(
        connection_string=connection, credential=credential, disable_offline_storage=True
    )
    provider = TracerProvider(resource=Resource.create({"service.name": "jobfinder-worker"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


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


def annotate(current, **attributes):
    """Add safe attributes to an open span."""
    current.set_attributes(safe_attributes(attributes))
