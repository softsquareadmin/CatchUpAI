"""A portable, printable report document using the session's configured labels."""
from html import escape
from .topic_coverage import COVERAGE_CSS, topic_coverage_html


def build_report_document(config, result, source_name=None):
    blocks = [topic_coverage_html(config["conversation"].get("topics_to_cover", []), result["report"])]
    for section in config["report"]["sections"]:
        value = result["report"].get(section["id"])
        label = escape(section["label"])
        if value is None:
            content = "<p class='muted'>Unknown / not provided</p>"
        elif section["type"] == "list":
            content = "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in value) + "</ul>" if value else "<p class='muted'>Nothing established yet</p>"
        else:
            if section["type"] == "boolean":
                text = "Yes" if value else "No"
            elif section["type"] == "score":
                text = f"{value:g} / {section['max']:g} (range {section['min']:g}–{section['max']:g})"
            elif section["type"] == "status":
                text = value.upper()
            else:
                text = value or "Nothing established yet"
            content = f"<p>{escape(text)}</p>"
        blocks.append(f"<section><h2>{label}</h2>{content}</section>")
    purpose = escape(config["conversation"]["purpose"])
    generated = escape(str(result["generated_at"]))
    source = escape(source_name or "Live recording")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CatchUpAI Conversation Report</title><style>
{COVERAGE_CSS}
body{{font-family:Arial,sans-serif;max-width:820px;margin:48px auto;padding:0 24px;color:#24262c;line-height:1.6}}
h1{{margin-bottom:4px}}h2{{font-size:18px;margin:0 0 10px}}section{{border-top:1px solid #ddd;padding:20px 0;break-inside:avoid}}
p,li{{white-space:pre-wrap;overflow-wrap:anywhere}}.muted{{color:#666}}.meta{{font-size:13px;color:#666}}
@media print{{body{{margin:0;max-width:none}}}}
</style></head><body><h1>CatchUpAI Conversation Report</h1>
<p class="meta">Source: {source}<br>Conversation duration: {result['timestamp']:.1f} seconds<br>
Checkpoint: {result['checkpoint']}<br>Generated: {generated}</p>
<p><strong>Purpose:</strong> {purpose}</p>{''.join(blocks)}</body></html>"""
