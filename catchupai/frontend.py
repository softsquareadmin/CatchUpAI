"""Reusable Streamlit configuration controls and report renderer."""

import uuid
from html import escape
from pathlib import Path
from math import cos, sin, radians

import streamlit as st

from .models import FIELD_TYPES, normalize_section_ids


def editable_row(value):
    return {"key": uuid.uuid4().hex, "value": value}


def workspace_styles():
    css = (Path(__file__).resolve().parent.parent / "assets" / "workspace.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style><div class='top-brand'>{icon('wave')}<span>CatchUpAI</span></div>", unsafe_allow_html=True)


def icon(name):
    # Eight evenly spaced teeth form a closed outline without crossing edges.
    gear_points = [
        (12 + radius * cos(radians(tooth * 45 + angle)),
         12 + radius * sin(radians(tooth * 45 + angle)))
        for tooth in range(8)
        for angle, radius in [(-18, 7.5), (-10, 10), (10, 10), (18, 7.5)]
    ]
    gear_outline = "M" + "L".join(f"{x:.2f},{y:.2f}" for x, y in gear_points) + "Z"
    paths = {
        "wave": '<path d="M3 10v4m4-7v10m5-15v20m5-15v10m4-7v4"/>',
        "settings": f'<path d="{gear_outline}"/><circle cx="12" cy="12" r="3.2"/>',
        "chart": '<rect x="3" y="12" width="4" height="9" rx="1"/><rect x="10" y="3" width="4" height="18" rx="1"/><rect x="17" y="8" width="4" height="13" rx="1"/>',
        "bulb": '<path d="M9 18h6m-6 3h6m-6-6c-5-4-3-12 3-12s8 8 3 12v3H9Z"/>',
        "mic": '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
        "file": '<path d="M14 2H5v20h14V7Zm0 0v5h5M8 11h8m-8 4h8m-8 4h5"/>',
        "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
        "users": '<circle cx="9" cy="7" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4Zm13-17a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5v3"/>',
    }
    return f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths[name]}</svg>'


def panel_heading(title, symbol, description="", strong=False):
    st.markdown(f'<div class="panel-heading"><span class="icon-tile {"solid" if strong else ""}">{icon(symbol)}</span><div><h2>{escape(title)}</h2>{f"<p>{escape(description)}</p>" if description else ""}</div></div>', unsafe_allow_html=True)


def page_heading(title, description):
    st.markdown(f'<div class="page-heading {"studio-heading" if title == "Conversation Studio" else ""}"><div class="eyebrow">CatchUpAI</div><h1>{escape(title)}</h1><p>{escape(description)}</p></div>', unsafe_allow_html=True)


def welcome_card():
    steps = [("mic", "Capture", "Record live or upload audio."), ("file", "Focus", "Set what matters in Configurations."), ("chart", "Understand", "See your report update as analysis arrives.")]
    items = "".join(f'<div class="how-step"><span class="step-number">{i}</span><span class="icon-tile">{icon(symbol)}</span><div><h3>{title}</h3><p>{description}</p></div></div>' for i, (symbol, title, description) in enumerate(steps, 1))
    st.markdown(f'<div class="how-card"><div class="panel-heading"><span class="icon-tile">{icon("bulb")}</span><div><h2>How CatchUpAI Works</h2><p>Turn your next conversation into clarity. Record a conversation or upload a recording. CatchUpAI follows your focus areas and builds the report you designed.</p></div></div><div class="how-steps">{items}</div></div>', unsafe_allow_html=True)


def preview_cards(config):
    conversation = config["conversation"]
    focuses = "".join(f"<li>{escape(focus)}</li>" for focus in conversation["pay_attention_to"])
    speakers = "".join(f'<div class="speaker-preview"><span>{escape(s["id"])}</span><span>{escape(s["role"])}</span></div>' for s in conversation["speakers"])
    cards = [("target", "Purpose", f'<p>{escape(conversation["purpose"])}</p>'), ("bulb", "Pay attention to", f"<ul>{focuses}</ul>"), ("users", "Participant Roles", speakers)]
    top = "".join(f'<section class="preview-card"><div class="preview-heading"><span class="icon-tile">{icon(symbol)}</span><h3>{title}</h3></div>{body}</section>' for symbol, title, body in cards)
    rows = []
    for index, section in enumerate(config["report"]["sections"], 1):
        detail = section["instruction"]
        if section["type"] == "status":
            detail += " Options: " + " / ".join(section["options"])
        elif section["type"] == "score":
            detail += f" Range: {section['min']:g}?{section['max']:g}."
        elif "max_items" in section:
            detail += f" Maximum items: {section['max_items']}."
        required = section.get("required", True)
        rows.append(f'<div class="preview-report-row"><span class="step-number">{index}</span><div><div class="preview-row-title"><strong>{escape(section["label"])}</strong><span class="type-badge">{section["type"].title()}</span><span class="required-badge {"optional" if not required else ""}">{"Required" if required else "Optional"}</span></div><p>{escape(detail)}</p></div></div>')
    st.markdown(f'<div class="preview-grid">{top}</div><div class="preview-report-heading"><h2>Report Sections</h2><p>These sections define what CatchUpAI will include in the generated report and how each section should be formatted.</p></div>{"".join(rows)}', unsafe_allow_html=True)


def conversation_editor(config, disabled=False):
    panel_heading("Conversation Configuration", "settings")
    st.markdown('<div class="field-heading"><h3>Purpose</h3><p>Describe what you want to achieve with the conversation analysis. This helps guide the AI in capturing the right information.</p></div>', unsafe_allow_html=True)
    purpose = st.text_area("Purpose", value=config["purpose"], key="_purpose", height=100, max_chars=1000, label_visibility="collapsed", disabled=disabled)
    st.markdown(f'<div class="field-count">{len(purpose):,}/1,000</div>', unsafe_allow_html=True)
    st.markdown('<div class="field-heading"><h3>Pay attention to</h3><p>Specify the key areas to focus on during analysis.</p></div>', unsafe_allow_html=True)
    focuses = st.text_area("Pay attention to", value="\n".join(config["pay_attention_to"]), key="_focus_text", height=110, max_chars=1000, label_visibility="collapsed", help="Enter one focus area per line. Blank lines are ignored.", disabled=disabled)
    st.markdown(f'<div class="field-count">{len(focuses):,}/1,000</div>', unsafe_allow_html=True)
    st.markdown('<div class="field-heading participants"><h3>Participant Roles</h3><p>Define expected roles. Participants are not automatically identified.</p></div>', unsafe_allow_html=True)
    with st.container(border=False):
        for index, row in enumerate(st.session_state.speaker_rows, 1):
            value, remove = st.columns([5, 1], vertical_alignment="bottom")
            row["value"] = value.text_input(f"Participant {index} role / name", value=row["value"], key=f"speaker_{row['key']}", disabled=disabled)
            if remove.button("Remove participant", icon=":material/delete:", help=f"Remove participant {index}", key=f"remove_speaker_{row['key']}", disabled=disabled):
                st.session_state.speaker_rows.remove(row)
                st.rerun()
    if st.button("Add Participant", icon=":material/add:", disabled=disabled):
        st.session_state.speaker_rows.append(editable_row(""))
        st.rerun()
    return {
        "purpose": purpose.strip(),
        "pay_attention_to": [line.strip() for line in focuses.splitlines() if line.strip()],
        "speakers": [{"id": f"speaker_{index}", "role": row["value"].strip()} for index, row in enumerate(st.session_state.speaker_rows, 1)],
    }


def report_editor(disabled=False):
    panel_heading("Custom Report", "chart", "Add the fields you want at each checkpoint. Move sections to set their display order.")
    sections = []
    rows = st.session_state.section_rows
    for index, row in enumerate(rows):
        section = row["value"]
        key = row["key"]
        with st.expander(f"{index + 1}. {section['label'] or 'Untitled section'}", expanded=index == 0):
            label = st.text_input("Section Label", value=section["label"], key=f"label_{key}", disabled=disabled)
            left, right = st.columns(2)
            kind = left.selectbox("Section Type", FIELD_TYPES, index=FIELD_TYPES.index(section["type"]), key=f"type_{key}", format_func=str.title, disabled=disabled)
            required = right.checkbox("Required", value=section.get("required", True), key=f"required_{key}", disabled=disabled)
            instruction = st.text_area("Instruction", value=section["instruction"], key=f"instruction_{key}", height=85, disabled=disabled)
            updated = {"label": label.strip(), "type": kind, "instruction": instruction.strip(), "required": required}
            if kind == "status":
                options_key = f"option_rows_{key}"
                if options_key not in st.session_state:
                    st.session_state[options_key] = [editable_row(option) for option in section.get("options", ["known", "partial", "unknown"])]
                st.caption("Status options")
                with st.container(height=160, border=False):
                    for option in st.session_state[options_key]:
                        value, remove = st.columns([5, 1])
                        option["value"] = value.text_input("Option", value=option["value"], key=f"option_{option['key']}", label_visibility="collapsed", disabled=disabled)
                        if remove.button("Remove", key=f"remove_option_{option['key']}", disabled=disabled):
                            st.session_state[options_key].remove(option)
                            st.rerun()
                if st.button("+ Add Status Option", key=f"add_option_{key}", disabled=disabled):
                    st.session_state[options_key].append(editable_row(""))
                    st.rerun()
                updated["options"] = [option["value"].strip() for option in st.session_state[options_key]]
            elif kind == "score":
                left, right = st.columns(2)
                updated["min"] = left.number_input("Minimum", value=float(section.get("min", 1)), key=f"min_{key}", disabled=disabled)
                updated["max"] = right.number_input("Maximum", value=float(section.get("max", 5)), key=f"max_{key}", disabled=disabled)
            elif kind == "list":
                limited = st.checkbox("Limit number of items", value="max_items" in section, key=f"limited_{key}", disabled=disabled)
                if limited:
                    updated["max_items"] = st.number_input("Maximum items", min_value=1, value=section.get("max_items", 10), step=1, key=f"items_{key}", disabled=disabled)
            row["value"] = updated
            sections.append(updated)
            up, down, delete = st.columns(3)
            if up.button("Move up", icon=":material/arrow_upward:", key=f"up_{key}", disabled=disabled or index == 0):
                rows[index - 1], rows[index] = rows[index], rows[index - 1]
                st.rerun()
            if down.button("Move down", icon=":material/arrow_downward:", key=f"down_{key}", disabled=disabled or index == len(rows) - 1):
                rows[index + 1], rows[index] = rows[index], rows[index + 1]
                st.rerun()
            if delete.button("Delete section", icon=":material/delete:", key=f"delete_{key}", disabled=disabled):
                rows.pop(index)
                st.rerun()
    if st.button("Add Report Section", icon=":material/add:", disabled=disabled):
        rows.append(editable_row({"label": "New Section", "type": "text", "instruction": "", "required": True}))
        st.rerun()
    return {"sections": normalize_section_ids(sections)}


def render_report(sections, report):
    cards = []
    for index, section in enumerate(sections, 1):
        value = report.get(section["id"])
        kind = section["type"]
        symbol = "file" if kind == "text" else "target" if kind == "list" else "chart"
        is_question = "question" in section["id"].lower()
        if is_question:
            symbol = "bulb"
        if value is None:
            message = "Unknown / not yet established" if section["id"] in report else "Not provided"
            body = f'<p class="report-empty">{message}</p>'
        elif kind == "list":
            if value:
                body = '<ul class="report-items">' + ''.join(f'<li>{escape(str(item))}</li>' for item in value) + '</ul>'
            else:
                message = "No outstanding questions identified" if is_question else "Nothing established yet"
                body = f'<p class="report-empty">{message}</p>'
        elif kind in ("boolean", "status"):
            text = ("Yes" if value else "No") if kind == "boolean" else str(value)
            body = f'<span class="report-value-badge">{escape(text)}</span>'
        elif kind == "score":
            body = f'<div class="report-score">{value:g}<span> / {section["max"]:g}</span></div><p class="report-empty">Configured range: {section["min"]:g} to {section["max"]:g}</p>'
        else:
            body = f'<p class="report-prose">{escape(str(value))}</p>' if value else '<p class="report-empty">Nothing established yet</p>'
        cards.append(f'<section class="report-section {"question-section" if is_question else ""}"><div class="report-section-heading"><span class="icon-tile">{icon(symbol)}</span><h3>{escape(section["label"])}</h3><span class="report-section-number">{index:02d}</span></div><div class="report-section-content">{body}</div></section>')
    st.markdown('<div class="report-sections">' + ''.join(cards) + '</div>', unsafe_allow_html=True)
