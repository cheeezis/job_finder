"""Display names of all sources, in one place for the console, Discord and the review."""

SOURCE_LABELS = {
    "arbeitsagentur": "Arbeitsagentur",
    "stepstone": "StepStone",
    "get_in_it": "get-in-IT",
    "arbeitnow": "Arbeitnow",
    "himalayas": "Himalayas",
    "jobicy": "Jobicy",
    "german_tech_jobs": "GermanTechJobs",
    "remotely": "Remotely",
    "startup_jobs": "Startup Jobs",
    "studysmarter": "StudySmarter",
    "manual": "Manuell hinzugefügt",
    "compose_it": "Compose IT",
    "bytewerk": "bytewerk",
    "rhoenenergie": "RhönEnergie",
    "jumo": "JUMO",
    "edag": "EDAG",
    "css": "CSS",
    "proemion": "Proemion",
    "nethinks": "NETHINKS",
}


def source_label(name):
    """Return a source's display name; unknown names stay as they are."""
    return SOURCE_LABELS.get(name, name)
