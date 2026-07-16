"""Evidence-based matching of extracted job requirements to a profile."""

import json
import re

from jsonschema import ValidationError, validate

from job_agent.llm.validation import build_validation_retry_messages


PROFILE_MATCH_SCHEMA_VERSION = 2
PROFILE_MATCH_PROMPT_VERSION = 2
MATCH_STATUSES = ["met", "partially_met", "not_met", "unknown"]

PROFILE_MATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches", "uncertainties"],
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "requirement_id",
                    "status",
                    "profile_evidence_paths",
                    "explanation",
                    "missing_or_uncertain",
                ],
                "properties": {
                    "requirement_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 100,
                    },
                    "status": {"type": "string", "enum": MATCH_STATUSES},
                    "profile_evidence_paths": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                    },
                    "explanation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "missing_or_uncertain": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                    },
                },
            },
        },
        "uncertainties": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    },
}

SYSTEM_PROMPT = """Gleiche eine Liste bereits extrahierter Anforderungen mit
einem belegten Bewerberprofil ab. Interpretiere die Stellenanzeige nicht neu.

Verbindliche Regeln:
- Erzeuge weder Gesamtscore noch Bewerbungsempfehlung.
- Bewerte jeden Eintrag aus requirements genau einmal und uebernimm seine id
  unveraendert als requirement_id. Ergaenze keine eigenen Anforderungen.
- Nutze ausschliesslich Fakten aus profile und gib fuer jeden verwendeten Beleg
  dessen exakten Pfad aus allowed_profile_evidence_paths zurueck.
- met bedeutet, dass die Anforderung auf dem geforderten Niveau voll belegt ist.
- partially_met bedeutet, dass ein relevanter Beleg existiert, aber Niveau,
  Umfang oder Kontext hinter der Anforderung zurueckbleibt.
- not_met bedeutet, dass eine klare Anforderung im Profil nicht belegt ist.
- unknown ist nur fuer eine unklare Anforderung oder widerspruechliche
  Profilangaben gedacht, nicht fuer schlicht fehlende Kenntnisse.
- met und partially_met benoetigen mindestens einen Profilbeleg.
- Projekte, Studium, Praktikum und Kurse duerfen first_exposure und
  practical_experience belegen, sind aber keine regulaere Berufserfahrung.
- Laufende oder abgeschlossene Kurse belegen nur explizit genannte Lerninhalte.
- Berufliche Interessen und Praeferenzen sind keine Kenntnisbelege.
- Native Sprachkenntnisse erfuellen Forderungen bis einschliesslich C1.
- Technologieeintraege werden einzeln bewertet. selection und minimum_count
  werden erst spaeter von Python fuer die Gruppe aggregiert.
- Prioritaet beeinflusst spaeter die Gewichtung, nicht den Erfuellungsstatus.
- Nenne fehlende oder unsichere Teile konkret in missing_or_uncertain.
- Fuehre widerspruechliche oder schwer belegbare Punkte unter uncertainties auf.

Antworte ausschliesslich im vorgegebenen JSON-Schema."""


class ProfileMatchValidationError(ValueError):
    """Raised when a profile match violates its structured contract."""


def flatten_job_requirements(job_analysis):
    """Create stable, individually matchable requirements from job facts."""
    requirements = [
        {
            "id": "role",
            "kind": "role",
            "summary": job_analysis["role_summary"],
            "primary_role_family": job_analysis["primary_role_family"],
            "secondary_role_families": job_analysis["secondary_role_families"],
            "seniority": job_analysis["seniority"],
        },
        {
            "id": "tasks",
            "kind": "tasks",
            "tasks": job_analysis["tasks"],
        },
        {
            "id": "experience",
            "kind": "experience",
            **job_analysis["experience_requirement"],
        },
    ]

    for group_index, group in enumerate(job_analysis["technology_requirements"]):
        for technology_index, technology in enumerate(group["technologies"]):
            requirements.append(
                {
                    "id": f"technology:{group_index}:{technology_index}",
                    "kind": "technology",
                    "technology": technology,
                    "priority": group["priority"],
                    "selection": group["selection"],
                    "minimum_count": group["minimum_count"],
                    "group_size": len(group["technologies"]),
                }
            )

    for index, requirement in enumerate(job_analysis["other_requirements"]):
        requirements.append(
            {
                "id": f"other:{index}",
                "kind": "other",
                **requirement,
            }
        )
    return requirements


def build_profile_match_messages(profile, job_analysis):
    """Build a prompt containing validated profile and flattened job facts."""
    prompt_data = {
        "profile": profile.data,
        "profile_version": profile.version,
        "allowed_profile_evidence_paths": sorted(
            collect_profile_evidence_paths(profile.data)
        ),
        "requirements": flatten_job_requirements(job_analysis),
        "output_schema": PROFILE_MATCH_SCHEMA,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(prompt_data, ensure_ascii=False),
        },
    ]


def collect_profile_evidence_paths(value, path=""):
    """Return paths to all scalar facts that an LLM may cite as evidence."""
    paths = set()
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else key
            paths.update(collect_profile_evidence_paths(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.update(collect_profile_evidence_paths(item, f"{path}[{index}]"))
    elif value is not None:
        paths.add(path)
    return paths


def validate_profile_match(match, job_analysis, profile):
    """Validate response shape, requirement coverage, and evidence paths."""
    try:
        validate(instance=match, schema=PROFILE_MATCH_SCHEMA)
    except ValidationError as error:
        raise ProfileMatchValidationError(error.message) from error

    expected_ids = [
        requirement["id"] for requirement in flatten_job_requirements(job_analysis)
    ]
    actual_ids = [item["requirement_id"] for item in match["matches"]]
    if sorted(actual_ids) != sorted(expected_ids):
        raise ProfileMatchValidationError(
            "Alle Anforderungs-IDs muessen genau einmal bewertet werden"
        )

    allowed_paths = collect_profile_evidence_paths(profile.data)
    for item in match["matches"]:
        evidence_paths = item["profile_evidence_paths"]
        if item["status"] in {"met", "partially_met"} and not evidence_paths:
            raise ProfileMatchValidationError(
                "Erfuellte oder teilweise erfuellte Anforderung braucht Profilbeleg"
            )
        unknown_paths = set(evidence_paths) - allowed_paths
        if unknown_paths:
            names = ", ".join(sorted(unknown_paths))
            raise ProfileMatchValidationError(f"Unbekannte Profilbelege: {names}")
    return match


def resolve_profile_path(profile_data, path):
    """Resolve one validated dotted/list path for display and later auditing."""
    current = profile_data
    for key, index in re.findall(r"(?:^|\.)([^.\[]+)|\[(\d+)\]", path):
        current = current[int(index)] if index else current[key]
    return current


def match_job_to_profile(
    job_analysis,
    profile,
    model,
    client,
    validation_retries=1,
):
    """Match validated job facts to a profile through an injected LLM client."""
    messages = build_profile_match_messages(profile, job_analysis)

    for attempt in range(validation_retries + 1):
        match, metadata = client.chat(
            model=model,
            messages=messages,
            output_schema=PROFILE_MATCH_SCHEMA,
        )
        try:
            validated = validate_profile_match(match, job_analysis, profile)
            metadata = dict(metadata)
            metadata["validation_retries"] = attempt
            return {
                "profile_version": profile.version,
                "match": validated,
            }, metadata
        except ProfileMatchValidationError as error:
            if attempt == validation_retries:
                error.raw_match = match
                raise
            messages = build_validation_retry_messages(messages, match, error)
