"""JSON-serializable application configuration, independent of UI/runtime settings."""

import copy
import json
import math
import re
import unicodedata
from typing import TypedDict, NotRequired


FIELD_TYPES = ("text", "list", "status", "boolean", "score")


class Speaker(TypedDict):
    id: str
    role: str


class ConversationConfig(TypedDict):
    purpose: str
    pay_attention_to: list[str]
    speakers: list[Speaker]
    topics_to_cover: NotRequired[list[dict]]
    crm_content: NotRequired[dict]


class ReportSection(TypedDict):
    id: str
    label: str
    type: str
    instruction: str
    required: bool
    final_only: NotRequired[bool]
    options: NotRequired[list[str]]
    min: NotRequired[float]
    max: NotRequired[float]
    max_items: NotRequired[int]


class ReportConfig(TypedDict):
    sections: list[ReportSection]


class AnalysisConfig(TypedDict):
    conversation: ConversationConfig
    report: ReportConfig
    analysis_interval: NotRequired[int]


def make_section_id(label, used_ids=()):
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", ascii_label.lower()).strip("_") or "section"
    if base[0].isdigit():
        base = "section_" + base
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def normalize_section_ids(sections):
    """Generate unique IDs in display order without modifying the input."""
    normalized = copy.deepcopy(sections)
    used = set()
    for section in normalized:
        if is_record_check_section(section):
            section["label"] = RECORD_CHECK_LABEL
            section["id"] = make_section_id("Record Check", used)
        else:
            section["id"] = make_section_id(section["label"], used)
        used.add(section["id"])
    return normalized


MAX_CRM_CHARS = 20_000
RECORD_CHECK_LABEL = "CRM Verification"


def is_record_check_section(section):
    return (section.get("label", "").strip().casefold() in ("record check", "crm comparison", RECORD_CHECK_LABEL.casefold())
            or section.get("id") == "record_check") and section.get("type") in ("list", "text")


RECORD_CHECK_INSTRUCTION = (
    "Compare the conversation with the CRM record in the configuration. For every "
    "field in the record, and for each claim in its description, return a comparison "
    "object with field, status, explanation and evidence. Use exact CRM field names; "
    "name description claims as 'description (claim being compared)'. Never omit a "
    "field. status must be consistent, differs or unconfirmed. consistent means a "
    "conversation statement agrees with the CRM value; it does not establish truth. "
    "differs requires a conversation statement conflicting with the CRM value. "
    "unconfirmed covers missing mentions, partial confirmation, unclear speakers "
    "and insufficient evidence. A record being read aloud is not confirmation. "
    "Explain the comparison and retain qualifications and disputed accounts. Provide "
    "a short verbatim conversation excerpt in evidence for consistent and differs; "
    "use an empty evidence string when nothing relevant was spoken. Do not invent quotes."
)


def _reject_constant(name):
    raise ValueError(f"CRM content contains the non-JSON value {name}.")


def parse_crm_content(text):
    """Turn pasted CRM text into a JSON object, or raise a message fit for the UI."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Paste the CRM JSON.")
    if len(text) > MAX_CRM_CHARS:
        raise ValueError(f"CRM content is too long; the limit is {MAX_CRM_CHARS:,} characters.")
    try:
        content = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        raise ValueError(f"CRM content is not valid JSON (line {error.lineno}, column {error.colno}): {error.msg}.") from error
    validate_crm_content(content)
    return content


def validate_crm_content(content):
    if not isinstance(content, dict) or not content:
        raise ValueError("CRM content must be a non-empty JSON object.")
    try:
        encoded = json.dumps(content, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("CRM content must contain only JSON values.") from error
    if len(encoded) > MAX_CRM_CHARS:
        raise ValueError(f"CRM content is too long; the limit is {MAX_CRM_CHARS:,} characters.")


def record_check_section():
    return {"label": RECORD_CHECK_LABEL, "type": "list", "instruction": RECORD_CHECK_INSTRUCTION,
            "required": True, "final_only": False}


def ensure_record_check_section(sections):
    """Rename legacy sections and append CRM verification only if none exists."""
    sections = copy.deepcopy(list(sections))
    if any(is_record_check_section(s) for s in sections):
        for section in sections:
            if is_record_check_section(section):
                section["label"] = RECORD_CHECK_LABEL
        return sections
    return sections + [record_check_section()]


def default_config() -> AnalysisConfig:
    sections = [
        {"label": "Summary", "type": "text", "instruction": "Summarize the conversation heard so far.", "required": True},
        {"label": "Important Facts", "type": "list", "instruction": "List important facts explicitly learned during the conversation.", "required": True},
        {"label": "Questions Not Yet Asked", "type": "list", "instruction": "Compare the important questions and information to cover in the configured Pay attention to focus areas against the conversation so far. List questions that have not yet been asked and whose answers have not otherwise been provided. Treat information volunteered by a participant as covered. Do not invent a required question checklist when none is configured; return an empty list when no uncovered configured questions remain.", "required": True},
        {"label": "Questions Needing Clarification", "type": "list", "instruction": "List important questions that were asked but received no answer, a vague answer, or an incomplete answer, including questions the interviewer moved away from before obtaining a clear response. For each item, explain what remains unclear and suggest a specific follow-up question. Consider later answers in the conversation so far and exclude questions that have since been clearly answered. Return an empty list when none need clarification.", "required": True},
        {"label": "Recommended Next Action", "type": "text", "instruction": "Suggest a useful next action grounded in the conversation.", "required": True},
    ]
    return {
        "conversation": {
            "purpose": "Track the conversation against its intended goals, capture key information, and identify important questions that remain unasked or need clearer answers. Suggest follow-up questions to close these gaps.",
            "pay_attention_to": ["Important facts", "Questions", "Decisions", "Unresolved items"],
            "topics_to_cover": [],
            "speakers": [{"id": "speaker_1", "role": "Participant 1"}, {"id": "speaker_2", "role": "Participant 2"}],
        },
        "report": {"sections": normalize_section_ids(sections)},
    }


def _object(value, allowed, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object.")
    if set(value) - set(allowed):
        raise ValueError(f"{location} contains unsupported fields: {', '.join(sorted(set(value) - set(allowed)))}.")


def _text(value, location):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must not be empty.")


def validate_report_sections(sections):
    if not isinstance(sections, list) or not sections:
        raise ValueError("Add at least one report section.")
    ids = set()
    common = {"id", "label", "type", "instruction", "required", "final_only"}
    for index, section in enumerate(sections, 1):
        where = f"Report section {index}"
        _object(section, common | {"options", "min", "max", "max_items"}, where)
        for key in ("id", "label", "type", "instruction"):
            _text(section.get(key), f"{where}: {key}")
        section_id = section["id"]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", section_id):
            raise ValueError(f"{where}: ID must be lowercase letters, digits and underscores, starting with a letter.")
        if section_id in ids:
            raise ValueError(f"Duplicate section ID: {section_id}.")
        ids.add(section_id)
        kind = section["type"]
        if kind not in FIELD_TYPES:
            raise ValueError(f"{where}: unsupported type {kind}.")
        if type(section.get("required", True)) is not bool:
            raise ValueError(f"{where}: required must be a boolean.")
        if type(section.get("final_only", False)) is not bool:
            raise ValueError(f"{where}: final_only must be a boolean.")
        extra = {"status": {"options"}, "score": {"min", "max"}, "list": {"max_items"}}.get(kind, set())
        _object(section, common | extra, where)
        if kind == "status":
            options = section.get("options")
            if not isinstance(options, list) or len(options) < 2:
                raise ValueError(f"{where}: status needs at least two options.")
            for option in options:
                _text(option, f"{where}: status option")
            if len(set(options)) != len(options):
                raise ValueError(f"{where}: status options must be unique.")
        if kind == "score":
            for key in ("min", "max"):
                value = section.get(key)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"{where}: {key} must be a finite number.")
            if section["min"] >= section["max"]:
                raise ValueError(f"{where}: minimum must be smaller than maximum.")
        if kind == "list" and "max_items" in section:
            if type(section["max_items"]) is not int or section["max_items"] < 1:
                raise ValueError(f"{where}: maximum items must be a positive integer.")


def validate_config(config):
    _object(config, {"conversation", "report", "analysis_interval"}, "Configuration")
    if "analysis_interval" in config:
        interval = config["analysis_interval"]
        if type(interval) is not int or interval < 30:
            raise ValueError("Analysis interval must be a whole number of at least 30 seconds.")
    conversation = config.get("conversation")
    _object(conversation, {"purpose", "pay_attention_to", "speakers", "topics_to_cover", "crm_content"}, "Conversation")
    if "crm_content" in conversation:
        validate_crm_content(conversation["crm_content"])
    topics = conversation.get("topics_to_cover", [])
    if not isinstance(topics, list):
        raise ValueError("Topics to cover must be a list.")
    topic_ids = set()
    for topic in topics:
        _object(topic, {"id", "label", "criteria"}, "Topic")
        for key in ("id", "label"):
            _text(topic.get(key), f"Topic {key}")
        if topic["id"] in topic_ids:
            raise ValueError("Topic IDs must be unique.")
        topic_ids.add(topic["id"])
        criteria = topic.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ValueError("Each topic needs at least one coverage criterion.")
        for criterion in criteria:
            _text(criterion, "Coverage criterion")
    _text(conversation.get("purpose"), "Purpose")
    focuses = conversation.get("pay_attention_to", [])
    if not isinstance(focuses, list):
        raise ValueError("Focus areas must be a list.")
    for focus in focuses:
        _text(focus, "Focus area")
    speakers = conversation.get("speakers", [])
    if not isinstance(speakers, list):
        raise ValueError("Speakers must be a list.")
    speaker_ids = set()
    for speaker in speakers:
        _object(speaker, {"id", "role"}, "Speaker")
        _text(speaker.get("id"), "Speaker ID")
        _text(speaker.get("role"), "Speaker role")
        if speaker["id"] in speaker_ids:
            raise ValueError("Speaker IDs must be unique.")
        speaker_ids.add(speaker["id"])
    report = config.get("report")
    _object(report, {"sections"}, "Report")
    validate_report_sections(report.get("sections"))
    if topics and any(s["id"] == "topic_coverage" for s in report["sections"]):
        raise ValueError("The report section ID topic_coverage is reserved for topic tracking.")
