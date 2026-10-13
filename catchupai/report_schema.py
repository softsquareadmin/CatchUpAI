"""Generate only supported schemas and validate every model report locally."""

import copy
import json

from jsonschema import Draft202012Validator

from .models import validate_report_sections
from .record_check import STATUSES, is_record_check, record_check_schema


TOOL_NAME = "update_conversation_report"


def build_report_schema(report_sections, topics=(), *, is_final=True):
    validate_report_sections(report_sections)
    properties = {}
    required = []
    for section in report_sections:
        if section.get("final_only", False) and not is_final:
            continue
        kind = section["type"]
        field = {"description": section["instruction"]}
        if is_record_check(section):
            field = record_check_schema()
        elif kind == "text":
            field["type"] = "string"
        elif kind == "list":
            field.update(type="array", items={"type": "string"})
            if "max_items" in section:
                field["maxItems"] = section["max_items"]
        elif kind == "status":
            field.update(type=["string", "null"], enum=section["options"] + [None])
        elif kind == "boolean":
            field["type"] = ["boolean", "null"]
        elif kind == "score":
            field.update(type=["number", "null"], minimum=section["min"], maximum=section["max"])
        if kind in ("status", "boolean", "score"):
            field["description"] += " Return null when evidence is insufficient."
        properties[section["id"]] = field
        if section.get("required", True):
            required.append(section["id"])
    if topics:
        topic_fields = {}
        for topic in topics:
            criteria = {str(i): {"type": "object", "properties": {
                "status": {"type": "string", "enum": ["covered", "partial", "unanswered"]},
                "evidence": {"type": "string", "description": "Brief supporting conversation excerpt. Empty when unanswered."},
                "missing": {"type": "string", "description": "Specific missing information; empty when covered."},
            }, "required": ["status", "evidence", "missing"], "additionalProperties": False,
                "description": criterion} for i, criterion in enumerate(topic["criteria"])}
            topic_fields[topic["id"]] = {"type": "object", "properties": {
                "criteria": {"type": "object", "properties": criteria, "required": list(criteria), "additionalProperties": False,
                             "description": "Only numbered criterion entries belong here. asked, summary and suggested_question are siblings of criteria."},
                "asked": {"type": "boolean", "description": "Was a question seeking this information asked?"},
                "summary": {"type": "string", "description": "Summary of information actually obtained; empty if none."},
                "suggested_question": {"type": "string", "description": "One targeted question addressing missing criteria; empty if fully covered."},
            }, "required": ["criteria", "asked", "summary", "suggested_question"], "additionalProperties": False,
                "description": f"Coverage of {topic['label']}. Other topic IDs belong directly under topic_coverage, never inside this topic."}
        properties["topic_coverage"] = {"type": "object", "properties": topic_fields, "required": list(topic_fields), "additionalProperties": False,
                                      "description": "Every configured topic ID is a direct sibling key in this object."}
        required.append("topic_coverage")
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    Draft202012Validator.check_schema(schema)
    return schema


def build_analysis_tool(report_config, topics=(), *, is_final=True):
    return {
        "type": "function",
        "name": TOOL_NAME,
        "description": "Return the current structured conversation analysis using the configured report fields.",
        "parameters": build_report_schema(report_config["sections"], topics, is_final=is_final),
    }


def normalize_report_structure(report, schema):
    """Relocate unambiguous coverage fields without inventing or changing data.

    Realtime tool arguments are not schema-constrained. Recover the known
    misplaced-sibling errors before full schema and evidence validation.
    Duplicate topics or conflicting metadata remain invalid for the retry.
    """
    normalized = copy.deepcopy(report)
    if not isinstance(normalized, dict):
        return normalized
    coverage = normalized.get("topic_coverage")
    coverage_schema = schema.get("properties", {}).get("topic_coverage", {})
    if not isinstance(coverage, dict) or coverage_schema.get("type") != "object":
        return normalized
    topic_ids = set(coverage_schema.get("properties", {}))
    metadata_fields = {"asked", "summary", "suggested_question"}
    collected = {}

    def collect(topic_id, topic):
        if topic_id in collected:
            raise ValueError("Multiple values for the same topic cannot be safely relocated.")
        collected[topic_id] = topic
        if not isinstance(topic, dict):
            return
        criteria = topic.get("criteria")
        if isinstance(criteria, dict):
            for key in metadata_fields:
                if key not in criteria:
                    continue
                if key in topic and topic[key] != criteria[key]:
                    raise ValueError("Conflicting topic metadata cannot be safely relocated.")
                topic[key] = criteria.pop(key)
        for container in (topic, criteria):
            if not isinstance(container, dict):
                continue
            for key in list(container):
                # Reserved field names are not eligible for topic relocation.
                if key in topic_ids and key not in metadata_fields | {"criteria"}:
                    collect(key, container.pop(key))

    try:
        for topic_id in list(coverage):
            if topic_id in topic_ids:
                collect(topic_id, coverage[topic_id])
    except ValueError:
        return copy.deepcopy(report)
    coverage.update(collected)
    return normalized


def validate_report(report, schema):
    # Reject NaN/Infinity even though Python's JSON decoder permits them by default.
    try:
        json.dumps(report, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("Report contains a non-JSON value.") from error
    errors = sorted(Draft202012Validator(schema).iter_errors(report), key=lambda e: str(e.path))
    if errors:
        details = "; ".join(f"{'.'.join(map(str, error.path)) or 'report'}: {error.message}" for error in errors)
        raise ValueError(f"Invalid report: {details}")
    for key, field in schema.get("properties", {}).items():
        item_schema = field.get("items", {})
        if item_schema.get("properties", {}).get("status", {}).get("enum") != list(STATUSES):
            continue
        for comparison in report.get(key, []):
            if not comparison["field"].strip() or not comparison["explanation"].strip():
                raise ValueError("Record comparisons require a field name and explanation.")
            if comparison["status"] != "unconfirmed" and not comparison["evidence"].strip():
                raise ValueError("Consistent or differing record comparisons require conversation evidence.")
    coverage = report.get("topic_coverage", {}) if schema.get("properties", {}).get("topic_coverage", {}).get("type") == "object" else {}
    for topic in coverage.values():
        for criterion in topic["criteria"].values():
            status = criterion["status"]
            if status != "unanswered" and not criterion["evidence"].strip():
                raise ValueError("Covered or partial criteria require supporting evidence.")
            if status != "covered" and not criterion["missing"].strip():
                raise ValueError("Incomplete criteria require a description of missing information.")
        complete = all(c["status"] == "covered" for c in topic["criteria"].values())
        if not complete and not topic["suggested_question"].strip():
            raise ValueError("Incomplete topics require a suggested follow-up question.")
        if any(c["status"] != "unanswered" for c in topic["criteria"].values()) and not topic["summary"].strip():
            raise ValueError("Topics with answers require an answer summary.")
    return report
