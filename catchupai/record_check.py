"""Present existing CRM comparisons without inferring or changing their findings."""

import json
import re
from html import escape
from urllib.parse import quote

from .models import RECORD_CHECK_INSTRUCTION, is_record_check_section
from .topic_coverage import COVERAGE_SYMBOLS


STATUSES = {
    "differs": ("Differs", COVERAGE_SYMBOLS["unanswered"]),
    "unconfirmed": ("Unconfirmed", COVERAGE_SYMBOLS["partial"]),
    "consistent": ("Consistent", COVERAGE_SYMBOLS["covered"]),
}
STATUS_STYLE = {"differs": "unanswered", "unconfirmed": "partial", "consistent": "covered"}
PREFIXES = (
    ("differs", r"(?:not confirmed and contradicted|differs|contradicted|inconsistent)\b"),
    ("unconfirmed", r"(?:partially confirmed|partial confirmation|partially consistent|not confirmed|unconfirmed|not independently confirmed|not mentioned|not addressed)\b"),
    ("consistent", r"(?:consistent|confirmed)\b"),
)


def is_record_check(section):
    return is_record_check_section(section)


def record_check_schema():
    return {"type": "array", "description": RECORD_CHECK_INSTRUCTION,
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {
                          "field": {"type": "string", "minLength": 1, "description": "Exact CRM field name, or description (specific claim)."},
                          "status": {"type": "string", "enum": list(STATUSES)},
                          "explanation": {"type": "string", "minLength": 1, "description": "Comparison based on statements heard, preserving uncertainties and disputes."},
                          "evidence": {"type": "string", "description": "Short verbatim conversation excerpt; empty if none. Required for consistent or differs."},
                      }, "required": ["field", "status", "explanation", "evidence"]}}


def _key(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _header(text):
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            return text[:index].strip(), text[index + 1:].strip()
    return None, text


def _clean(text):
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", str(text)).strip()
    if text.startswith("**") and text.endswith("**"):
        text = text[2:-2].strip()
    return text


def _source_fields(crm):
    fields = dict(crm.get("fields", {})) if isinstance(crm.get("fields"), dict) else {}
    fields.update({key: value for key, value in crm.items() if key not in ("fields", "object") and key not in fields})
    lookup = {}
    for name, value in fields.items():
        identity = _key(name)
        # Ambiguous spelling must not assign a comparison to the wrong CRM field.
        lookup[identity] = (name, _text(value)) if identity not in lookup else None
    if "description" in lookup and "crmdescription" not in lookup:
        lookup["crmdescription"] = lookup["description"]
    return lookup


def comparison_items(value, crm_content=None):
    if isinstance(value, list):
        lines = value
    elif isinstance(value, str):
        lines = re.split(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", value)
    else:
        lines = []
    fields = _source_fields(crm_content or {})
    items = []
    for index, line in enumerate(lines):
        if isinstance(line, dict) and all(isinstance(line.get(key), str) for key in ("field", "status", "explanation", "evidence")) and line["status"] in STATUSES:
            field = line["field"]
            source = fields.get(_key(re.sub(r"\s*\([^)]*\)\s*$", "", field)))
            items.append({"field": field, "body": line["explanation"], "evidence": line["evidence"],
                          "status": line["status"], "index": index, "source": source[1] if source else None,
                          "source_label": "CRM record", "reference": None})
            continue
        text = _clean(line)
        if not text:
            continue
        heading, body = _header(text)
        embedded = re.search(r"\(\s*CRM\s*:\s*(.*)\)\s*$", heading or "", flags=re.I | re.S)
        field = (heading[:embedded.start()].strip() if embedded else heading) or "Analysis note"
        field = re.sub(r"(\*\*|__)(.+?)\1", r"\2", field)
        # A descriptive qualifier on a claim remains in its displayed title.
        source_key = _key(re.sub(r"\s*\([^)]*\)\s*$", "", field))
        source = fields.get(source_key)
        status_body = re.sub(r"^(\*\*|__)(.+?)\1", r"\2", body)
        status = next((status for status, pattern in PREFIXES if heading and re.match(pattern, status_body, flags=re.I)), "unconfirmed")
        if status == "consistent" and re.match(r"^(?:consistent|confirmed)\s*(?:\?|but\b|except\b|discrepancy\b|difference\b)", status_body, flags=re.I):
            status = "unconfirmed"
        items.append({"field": field, "body": body, "status": status, "index": index,
                      "source": source[1] if source else embedded.group(1) if embedded else None,
                      "source_label": "CRM record" if source else "CRM value in analysis",
                      "reference": embedded.group(1) if source and embedded and embedded.group(1) != source[1] else None})
    return items


def record_check_navigation_icon(value, crm_content=None):
    """Show consistent findings over all findings using the report's statuses."""
    items = comparison_items(value, crm_content)
    consistent = sum(item["status"] == "consistent" for item in items)
    total = len(items)
    progress = consistent / total * 100 if total else 0
    label = f"{consistent}/{total}"
    font_size = min(14, 48 / len(label))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
           f'<title>{consistent} consistent out of {total} findings</title>'
           '<circle cx="24" cy="24" r="21" fill="none" stroke="#dbe5ec" stroke-width="5"/>'
           f'<circle cx="24" cy="24" r="21" fill="none" stroke="#128362" stroke-width="5" '
           f'pathLength="100" stroke-dasharray="{progress} 100" transform="rotate(-90 24 24)"/>'
           f'<text x="24" y="24" text-anchor="middle" dominant-baseline="central" '
           f'font-family="Arial,Helvetica,sans-serif" font-size="{font_size}" font-weight="700" fill="#15285a">{label}</text></svg>')
    return "data:image/svg+xml," + quote(svg, safe="")


def _evidence_html(text):
    parts, position = [], 0
    for match in re.finditer(r'“[^”\n]+”|"[^"\n]+"|\*\*[^*\n]+\*\*|__[^_\n]+__', text):
        token = match.group()
        highlighted = (f'<strong>{escape(token[2:-2])}</strong>' if token.startswith(("**", "__")) else
                       f'<span class="record-check-quote">{escape(token)}</span>')
        parts.extend((escape(text[position:match.start()]),
                      highlighted))
        position = match.end()
    parts.append(escape(text[position:]))
    return "".join(parts)


def _title(text):
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " ")
    return escape(text)


def record_check_html(value, crm_content=None, *, expand_all=False):
    items = comparison_items(value, crm_content)
    if not items:
        return '<p class="report-empty">No record comparisons available yet.</p>'
    counts = {"differs": 0, "unconfirmed": 0, "consistent": 0}
    for item in items:
        counts[item["status"]] += 1
    stat_icons = {
        "differs": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4M12 6v5"/><circle cx="12" cy="14" r=".6"/>',
        "unconfirmed": '<path d="M10.3 3.9 2.5 18a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v5"/><circle cx="12" cy="17" r=".6"/>',
        "consistent": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    }
    stats = ''.join(
        f'<div class="record-check-stat {status}">'
        '<span class="record-check-stat-icon" aria-hidden="true">'
        f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{stat_icons[status]}</svg></span>'
        f'<strong class="record-check-stat-count">{counts[status]}</strong>'
        f'<div class="record-check-stat-description"><strong>{label}</strong><span>{description}</span></div></div>'
        for status, label, description in (("differs", "Differs", "Needs review"),
                                           ("unconfirmed", "Unconfirmed", "Needs human review"),
                                           ("consistent", "Consistent", "Matches conversation")))
    groups = []
    for group, heading in (("differs", "Differences to review"), ("unconfirmed", "Unconfirmed details"),
                           ("consistent", "Consistent with conversation")):
        selected = [item for item in items if item["status"] == group]
        if not selected:
            continue
        cards = []
        for item in selected:
            status = item["status"]
            label, symbol = STATUSES[status]
            source = ""
            if item["source"] is not None:
                value_html = f'<p>{escape(item["source"])}</p>'
                if len(item["source"]) > 180:
                    value_html = f'<details class="record-check-source-more"{" open" if expand_all else ""}><summary><span class="coverage-chevron" aria-hidden="true"></span>View CRM content</summary>{value_html}</details>'
                source = f'<div class="record-check-source"><span class="record-check-label">{item["source_label"]}</span>{value_html}</div>'
            reference = (f'<p class="record-check-reference">Analysis referenced CRM value: {escape(item["reference"])}</p>'
                         if item["reference"] is not None else "")
            opened = " open" if status == "differs" or expand_all else ""
            evidence = (f'<blockquote class="record-check-evidence"><span class="record-check-evidence-icon" aria-hidden="true">&#10077;</span><strong class="record-check-evidence-text">{escape(item["evidence"])}</strong></blockquote>'
                        if item.get("evidence") else "")
            if status == "unconfirmed" and not evidence:
                evidence = ('<div class="record-check-evidence record-check-no-evidence">'
                            '<span class="record-check-evidence-icon" aria-hidden="true">&#9432;</span>'
                            '<span>No supporting conversation quote available.</span></div>')
            cards.append(f'<details class="record-check-card {status} coverage-topic {STATUS_STYLE[status]}"{opened}><summary>'
                         '<span class="coverage-chevron" aria-hidden="true"></span>'
                         f'<span class="coverage-symbol" aria-hidden="true">{symbol}</span>'
                         f'<strong class="coverage-topic-title">{_title(item["field"])}</strong>'
                         f'<span class="coverage-label">{label}</span></summary>'
                         f'<div class="record-check-comparison coverage-body {"has-source" if source else ""}">{source}'
                         '<div class="record-check-analysis"><span class="record-check-label">Conversation analysis</span>'
                         f'<p>{_evidence_html(item["body"])}</p>{evidence}{reference}</div></div></details>')
        groups.append(f'<div class="record-check-group"><h4>{heading}</h4>{"".join(cards)}</div>')
    return ('<div class="record-check"><p class="record-check-context">Based on conversation statements. '
            'Differences and unconfirmed details need human review.</p>'
            f'<div class="record-check-stats">{stats}</div>{"".join(groups)}</div>')


RECORD_CHECK_CSS = """
.record-check { color:#0a1637; font-family:Arial,Helvetica,sans-serif; container-type:inline-size; }
.record-check * { box-sizing:border-box; }
.record-check .record-check-context { margin:0 0 12px; color:#737e98; font-size:12px; line-height:19px; white-space:normal; }
.record-check-stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-bottom:18px; }
.record-check-stat { display:flex; align-items:center; gap:14px; padding:14px; border-radius:8px; border:1px solid var(--stat-border); background:var(--stat-bg); }
.record-check-stat.differs { --stat-color:#cf3939; --stat-bg:#ffebeb; --stat-border:#f4caca; --stat-icon-bg:#ffd6d6; }
.record-check-stat.unconfirmed { --stat-color:#ad7500; --stat-bg:#fff3d9; --stat-border:#eed7a7; --stat-icon-bg:#ffe7b3; }
.record-check-stat.consistent { --stat-color:#128362; --stat-bg:#e3f5ec; --stat-border:#bfdfcf; --stat-icon-bg:#ccebdc; }
.record-check-stat-icon { display:flex; align-items:center; justify-content:center; width:48px; height:48px; flex-shrink:0; border-radius:50%; background:var(--stat-icon-bg); color:var(--stat-color); }
.record-check-stat-icon svg { width:26px; height:26px; }
.record-check-stat .record-check-stat-count { font-size:38px; line-height:1; color:var(--stat-color); }
.record-check-stat-description { min-width:0; }
.record-check-stat-description strong { display:block; font-size:13px; line-height:20px; color:#0a1637; }
.record-check-stat-description span { display:block; font-size:12px; line-height:19px; color:#647693; }
.record-check-group { margin:0 0 18px; }
.record-check .record-check-group h4 { margin:0 0 9px; padding:0; font-size:12px; font-weight:600; color:#647693; line-height:20px; }
.record-check-card { --record-accent:var(--status-color); --record-tint:var(--status-bg); }
.record-check-card.differs { --status-bg:#ffdce4; }
.record-check-card.unconfirmed { --status-bg:#ffe9be; }
.record-check-card.consistent { --status-bg:#cef0df; }
.record-check-card > summary { padding:12px 16px; }
.record-check-card > summary > .coverage-label { font-weight:600; }
.record-check-comparison { display:grid; gap:18px; padding:4px 16px 16px; }
.record-check-comparison.has-source { grid-template-columns:minmax(0,1fr) minmax(0,2fr); }
.record-check-analysis { min-width:0; }
.record-check-comparison.has-source > .record-check-analysis { border-left:1px solid #e0eafa; padding-left:18px; }
.record-check-source { border:1px solid #e0eafa; border-radius:6px; background:#f5f9ff; padding:12px 14px; align-self:start; }
.record-check-label { display:block; font-size:11px; font-weight:600; color:#7d8ca4; margin-bottom:6px; }
.record-check .record-check-comparison p { font-size:12px; line-height:1.45; margin:3px 0 0; color:#425d90; white-space:pre-wrap; overflow-wrap:anywhere; }
.record-check .record-check-analysis > p { color:#172642; }
.record-check .record-check-source p { font-size:14px; font-weight:700; color:#0a1637; }
.record-check-quote { font-weight:700; color:#213858; background:#eef4fd; border-radius:3px; padding:1px 3px; box-decoration-break:clone; }
.record-check .record-check-evidence { display:flex; align-items:flex-start; gap:12px; margin:10px 0 0; padding:9px 12px; border-left:3px solid var(--record-accent); border-radius:0 6px 6px 0; background:linear-gradient(90deg,var(--record-tint),#fafcfe); color:#213858; font-size:12px; font-weight:600; line-height:21px; white-space:pre-wrap; overflow-wrap:anywhere; }
.record-check-evidence-icon { color:var(--record-accent); font-size:26px; line-height:21px; flex-shrink:0; }
.record-check .record-check-evidence > span:last-child { color:#172642; }
.record-check blockquote.record-check-evidence > span:last-child { color:#101e38; font-weight:700; }
.record-check blockquote.record-check-evidence > .record-check-evidence-text { color:#101e38 !important; font-weight:700 !important; font-size:13px; }
.record-check .record-check-no-evidence { font-weight:400; color:#7d6a46; }
.record-check-no-evidence .record-check-evidence-icon { font-size:17px; }
.record-check-source-more > summary { padding:0; gap:8px; font-size:12px; line-height:20px; background:transparent; color:#0069ff; cursor:pointer; }
.record-check-source-more[open] > summary .coverage-chevron { transform:rotate(90deg); }
.record-check .record-check-source-more p { margin-top:8px; font-weight:400; }
.record-check .record-check-reference { color:#7d8ca4; font-size:11px; margin-top:8px; }
@container (max-width:480px) {
  .record-check-comparison.has-source { grid-template-columns:1fr; }
  .record-check-comparison.has-source > .record-check-analysis { border-left:0; border-top:1px solid #e0eafa; padding-left:0; padding-top:12px; }
  .record-check-stats { grid-template-columns:1fr; }
  .record-check-stat { gap:12px; padding:12px; }
  .record-check-card > summary { gap:8px; flex-wrap:wrap; }
  .record-check-card > summary > .coverage-topic-title { flex-basis:calc(100% - 70px); }
  .record-check-card > summary > .coverage-label { margin-left:auto; }
}
@media print {
  .record-check-card { break-inside:avoid; }
  .record-check-card > summary { cursor:default; }
}
"""
