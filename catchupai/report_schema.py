"""Generate only supported schemas and validate every model report locally."""

import json

from jsonschema import Draft202012Validator

from .models import validate_report_sections


TOOL_NAME = "update_conversation_report"


def build_report_schema(report_sections, topics=()):
    validate_report_sections(report_sections)
    properties = {}
    required = []
    for section in report_sections:
        kind = section["type"]
        field = {"description": section["instruction"]}
        if kind == "text":
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
                "criteria": {"type": "object", "properties": criteria, "required": list(criteria), "additionalProperties": False},
                "asked": {"type": "boolean", "description": "Was a question seeking this information asked?"},
                "summary": {"type": "string", "description": "Summary of information actually obtained; empty if none."},
                "suggested_question": {"type": "string", "description": "One targeted question addressing missing criteria; empty if fully covered."},
            }, "required": ["criteria", "asked", "summary", "suggested_question"], "additionalProperties": False}
        properties["topic_coverage"] = {"type": "object", "properties": topic_fields, "required": list(topic_fields), "additionalProperties": False}
        required.append("topic_coverage")
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    Draft202012Validator.check_schema(schema)
    return schema


def build_analysis_tool(report_config, topics=()):
    return {
        "type": "function",
        "name": TOOL_NAME,
        "description": "Return the current structured conversation analysis using the configured report fields.",
        "parameters": build_report_schema(report_config["sections"], topics),
    }


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
