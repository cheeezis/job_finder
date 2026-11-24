# uv installs exactly the versions in uv.lock, without the project itself: the
# image runs the code from /app. uv is only mounted for that step and stays out
# of the image. Both images are pinned by digest; Dependabot
# proposes updates.
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

WORKDIR /app

# Dates and interview times are local German time, as entered in the review.
ENV TZ=Europe/Berlin \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --no-cache --no-install-project

# Nothing runs pip, and its vendored copies of msgpack and setuptools are old
# enough for image scans to report them. PATH already starts with the venv,
# which has no pip, so this calls the base image's interpreter directly.
RUN /usr/local/bin/python -m pip uninstall --yes pip

# Only what the finder and the review run; tests, docs and scripts stay out.
COPY job_finder ./job_finder
COPY run_finder.py user_settings.example.yaml ./

# An unprivileged user; the run log under data/ is all the container writes.
RUN useradd --create-home --uid 10001 jobfinder \
    && mkdir data \
    && chown jobfinder data
USER jobfinder

CMD ["python", "run_finder.py"]
