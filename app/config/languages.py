"""Languages a job can be delivered in: its scenarios and its Excel report.

The language is chosen when the job is created (`POST /jobs`, field `language`) and stored
on the job. It decides two things:
- what language the Designer writes titles, steps and expected results in;
- what language the report uses for its sheets, labels, explanations and value names.

Machine values (priority `critical`, category `security`, page type `login`) stay in English
in the database and the API, so filters and SQL never depend on the language; only what a
person reads is translated.
"""
from typing import Literal, get_args

Language = Literal["en", "es"]
SUPPORTED_LANGUAGES: tuple[str, ...] = get_args(Language)
DEFAULT_LANGUAGE: Language = "en"

# How the language is named inside the Designer prompt
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish (neutral, understandable in Spain and Latin America)",
}
