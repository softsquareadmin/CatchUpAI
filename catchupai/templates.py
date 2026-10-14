"""Configuration repository backed by Supabase, with local development fallback."""
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from datetime import datetime, timezone

from .models import validate_config, is_record_check_section, RECORD_CHECK_LABEL
from .database import SupabaseStore, enabled, canonical_id


TEMPLATE_DIRECTORY = Path(__file__).resolve().parent.parent / "configuration_templates"


def _path(template_id):
    if not isinstance(template_id, str) or not re.fullmatch(r"[a-f0-9]{32}", template_id):
        raise ValueError("Invalid template ID.")
    path = TEMPLATE_DIRECTORY / f"{template_id}.json"
    if path.resolve().parent != TEMPLATE_DIRECTORY.resolve():
        raise ValueError("Template path must stay inside configuration_templates.")
    return path


def load_template(template_id):
    if enabled():
        rows = SupabaseStore().rows("configurations", id="eq." + canonical_id(template_id), deleted_at="is.null")
        if not rows:
            raise ValueError("Configuration was deleted or is no longer available.")
        return _document(rows[0])
    document = json.loads(_path(template_id).read_text(encoding="utf-8"))
    if document.get("deleted_at"):
        raise ValueError("Configuration was deleted or is no longer available.")
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported template format.")
    if document.get("id") != template_id:
        raise ValueError("Template ID does not match its file.")
    if not isinstance(document.get("name"), str) or not document["name"].strip():
        raise ValueError("Template name is missing.")
    validate_config(document.get("config"))
    for section in document["config"]["report"]["sections"]:
        if is_record_check_section(section):
            section["label"] = RECORD_CHECK_LABEL
    return document


def _document(row):
    validate_config(row["config"])
    for section in row["config"]["report"]["sections"]:
        if is_record_check_section(section):
            section["label"] = RECORD_CHECK_LABEL
    return {"version": row["schema_version"], "id": uuid.UUID(row["id"]).hex,
            "name": row["name"], "config": row["config"], "revision": row["revision"]}


def list_templates():
    templates, errors = [], []
    if enabled():
        try:
            templates = [_document(row) for row in SupabaseStore().rows("configurations", deleted_at="is.null")]
        except (OSError, ValueError, TypeError) as error:
            errors.append(str(error))
        return sorted(templates, key=lambda t: t["name"].casefold()), errors
    if TEMPLATE_DIRECTORY.exists():
        for path in sorted(TEMPLATE_DIRECTORY.glob("*.json")):
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("deleted_at"):
                    continue
                templates.append(load_template(path.stem))
            except (OSError, ValueError, TypeError) as error:
                errors.append(f"Could not read {path.name}: {error}")
    return sorted(templates, key=lambda t: t["name"].casefold()), errors


def save_template(name, config, template_id=None, expected_revision=None):
    name = name.strip()
    if not name:
        raise ValueError("Enter a template name.")
    validate_config(config)
    if enabled():
        store = SupabaseStore()
        values = {"name": name, "normalized_name": name.casefold(), "config": config}
        if template_id is None:
            rows = store.request("POST", "/rest/v1/configurations", body={"id": str(uuid.uuid4()), **values},
                                 headers={"Prefer": "return=representation"})
        else:
            current = load_template(template_id)
            revision = expected_revision if expected_revision is not None else current["revision"]
            rows = store.patch("configurations", values, id="eq." + canonical_id(template_id),
                               deleted_at="is.null", revision="eq." + str(revision))
            if not rows:
                raise ValueError("Configuration changed or was deleted. Reload it before saving your edits.")
        return _document(rows[0])
    templates, _ = list_templates()
    # Soft-deleted local names remain reserved too.
    deleted = [json.loads(p.read_text(encoding="utf-8")) for p in TEMPLATE_DIRECTORY.glob("*.json")
               if json.loads(p.read_text(encoding="utf-8")).get("deleted_at")]
    templates += deleted
    if any(t["name"].casefold() == name.casefold() and t["id"] != template_id for t in templates):
        raise ValueError("A template with that name already exists. Choose another name or load it to update it.")
    if template_id is not None:
        load_template(template_id)
    template_id = template_id or uuid.uuid4().hex
    path = _path(template_id)
    document = {"version": 1, "id": template_id, "name": name, "config": config}
    data = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    TEMPLATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=TEMPLATE_DIRECTORY, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return document


def delete_template(template_id):
    deleted_at = datetime.now(timezone.utc).isoformat()
    if enabled():
        SupabaseStore().patch("configurations", {"deleted_at": deleted_at},
                              id="eq." + canonical_id(template_id), deleted_at="is.null")
        return
    path = _path(template_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    if not document.get("deleted_at"):
        document["deleted_at"] = deleted_at
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
