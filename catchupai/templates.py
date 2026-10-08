"""Persistent configuration templates stored as individual JSON documents."""
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .models import validate_config


TEMPLATE_DIRECTORY = Path(__file__).resolve().parent.parent / "configuration_templates"


def _path(template_id):
    if not isinstance(template_id, str) or not re.fullmatch(r"[a-f0-9]{32}", template_id):
        raise ValueError("Invalid template ID.")
    path = TEMPLATE_DIRECTORY / f"{template_id}.json"
    if path.resolve().parent != TEMPLATE_DIRECTORY.resolve():
        raise ValueError("Template path must stay inside configuration_templates.")
    return path


def load_template(template_id):
    document = json.loads(_path(template_id).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported template format.")
    if document.get("id") != template_id:
        raise ValueError("Template ID does not match its file.")
    if not isinstance(document.get("name"), str) or not document["name"].strip():
        raise ValueError("Template name is missing.")
    validate_config(document.get("config"))
    return document


def list_templates():
    templates, errors = [], []
    if TEMPLATE_DIRECTORY.exists():
        for path in sorted(TEMPLATE_DIRECTORY.glob("*.json")):
            try:
                templates.append(load_template(path.stem))
            except (OSError, ValueError, TypeError) as error:
                errors.append(f"Could not read {path.name}: {error}")
    return sorted(templates, key=lambda t: t["name"].casefold()), errors


def save_template(name, config, template_id=None):
    name = name.strip()
    if not name:
        raise ValueError("Enter a template name.")
    validate_config(config)
    templates, _ = list_templates()
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
    _path(template_id).unlink()
