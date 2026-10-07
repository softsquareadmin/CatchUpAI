"""Generate only supported schemas and validate every model report locally."""

import json

from jsonschema import Draft202012Validator

from .models import validate_report_sections


TOOL_NAME = "update_conversation_report"


def build_report_schema(report_sections):
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
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    Draft202012Validator.check_schema(schema)
    return schema


def build_analysis_tool(report_config):
    return {
        "type": "function",
        "name": TOOL_NAME,
        "description": "Return the current structured conversation analysis using the configured report fields.",
        "parameters": build_report_schema(report_config["sections"]),
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
    return report
