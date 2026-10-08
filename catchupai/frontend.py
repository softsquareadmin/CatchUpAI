"""Reusable Streamlit configuration controls and report renderer."""

import uuid
import copy
import hashlib
from contextlib import contextmanager
from html import escape
from pathlib import Path
from math import cos, sin, radians

import streamlit as st

from .models import FIELD_TYPES, normalize_section_ids, validate_report_sections
from .topic_coverage import COVERAGE_CSS, topic_coverage_html
from .document_topics import DOCUMENT_TYPES, extract_document_topics, merge_imported_topics


def editable_row(value):
    return {"key": uuid.uuid4().hex, "value": value}


def inline_error(message, placeholder=None):
    target = placeholder if placeholder is not None else st
    target.markdown(f'<div class="configuration-error" role="alert">{escape(message)}</div>', unsafe_allow_html=True)


def workspace_styles():
    css = (Path(__file__).resolve().parent.parent / "assets" / "workspace.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}\n{COVERAGE_CSS}</style><div class='top-brand'>{icon('wave')}<span>CatchUpAI</span></div>", unsafe_allow_html=True)


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


def help_icon(description):
    text = escape(description, quote=True)
    return f'<span class="heading-help" tabindex="0" role="img" aria-label="Help: {text}" title="{text}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7" r="1" fill="currentColor" stroke="none"/></svg></span>' if description else ""


def field_heading(title, description, extra_class=""):
    st.markdown(f'<div class="field-heading {extra_class}"><h3>{escape(title)} {help_icon(description)}</h3></div>', unsafe_allow_html=True)


def panel_heading(title, symbol, description="", strong=False, tooltip=False):
    title_help = help_icon(description) if tooltip else ""
    body = f'<p>{escape(description)}</p>' if description and not tooltip else ""
    st.markdown(f'<div class="panel-heading"><span class="icon-tile {"solid" if strong else ""}">{icon(symbol)}</span><div><h2>{escape(title)} {title_help}</h2>{body}</div></div>', unsafe_allow_html=True)


def page_heading(title, description, tooltip=False):
    title_help = help_icon(description) if tooltip else ""
    body = f'<p>{escape(description)}</p>' if not tooltip else ""
    st.markdown(f'<div class="page-heading {"studio-heading" if title == "Conversation Studio" else ""}"><div class="eyebrow">CatchUpAI</div><h1>{escape(title)} {title_help}</h1>{body}</div>', unsafe_allow_html=True)


def welcome_card():
    steps = [("mic", "Capture", "Record live or upload audio."), ("file", "Focus", "Set what matters in Configurations."), ("chart", "Understand", "See your report update as analysis arrives.")]
    items = "".join(f'<div class="how-step"><span class="step-number">{i}</span><span class="icon-tile">{icon(symbol)}</span><div><h3>{title}</h3><p>{description}</p></div></div>' for i, (symbol, title, description) in enumerate(steps, 1))
    st.markdown(f'<div class="how-card"><div class="panel-heading"><span class="icon-tile">{icon("bulb")}</span><div><h2>How CatchUpAI Works</h2><p>Turn your next conversation into clarity. Record a conversation or upload a recording. CatchUpAI follows your focus areas and builds the report you designed.</p></div></div><div class="how-steps">{items}</div></div>', unsafe_allow_html=True)


def preview_cards(config):
    conversation = config["conversation"]
    focuses = "".join(f"<li>{escape(focus)}</li>" for focus in conversation["pay_attention_to"])
    speakers = "".join(f'<div class="speaker-preview"><span>{escape(s["id"])}</span><span>{escape(s["role"])}</span></div>' for s in conversation["speakers"])
    cards = [("target", "Purpose", f'<p>{escape(conversation["purpose"])}</p>'), ("bulb", "Pay attention to", f"<ul>{focuses}</ul>"), ("users", "Participant Roles", speakers)]
    topics = conversation.get("topics_to_cover", [])
    if topics:
        topic_list = ''.join(f'<li><strong>{escape(t["label"])}</strong><ul>' + ''.join(f'<li>{escape(c)}</li>' for c in t["criteria"]) + '</ul></li>' for t in topics)
        cards.append(("target", "Topics to cover", f'<ul>{topic_list}</ul>'))
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


def remove_editor_row(state_key, row_key):
    st.session_state[state_key] = [r for r in st.session_state[state_key] if r["key"] != row_key]


def dismiss_row_confirmation(row_key):
    key = f"remove_confirmation_epoch_{row_key}"
    st.session_state[key] = st.session_state.get(key, 0) + 1


@contextmanager
def removable_expander(title, row_key, state_key, kind="topic", disabled=False):
    with st.container(key=f"edit_row_{row_key}", border=False):
        with st.expander(title, expanded=False):
            yield
        epoch = st.session_state.get(f"remove_confirmation_epoch_{row_key}", 0)
        with st.popover(f"Remove {kind}", icon=":material/close:", disabled=disabled,
                        key=f"row_close_{row_key}_{epoch}"):
            with st.container(key=f"confirm_remove_{row_key}"):
                st.write(f'Remove "{title}"?')
                cancel, confirm = st.columns(2)
                cancel.button("Cancel", key=f"cancel_remove_{row_key}", on_click=dismiss_row_confirmation, args=(row_key,))
                confirm.button(f"Remove {kind}", key=f"confirm_remove_button_{row_key}", type="primary",
                               on_click=remove_editor_row, args=(state_key, row_key), disabled=disabled)


def confirm_imported_topics():
    imported = []
    for row in st.session_state.topic_import_review:
        topic = row["value"]
        key = row["key"]
        label = st.session_state.get(f"import_label_{key}", topic["label"])
        criteria = st.session_state.get(f"import_criteria_{key}", "\n".join(topic["criteria"]))
        imported.append({"label": label.strip(), "criteria": [c.strip() for c in criteria.splitlines() if c.strip()]})
    rows = st.session_state.topic_rows
    try:
        merged = merge_imported_topics([row["value"] for row in rows], imported)
    except ValueError as error:
        st.session_state.topic_import_review_error = str(error)
        return
    by_id = {row["value"]["id"]: row for row in rows}
    for topic in merged:
        if topic["id"] in by_id:
            row = by_id[topic["id"]]
            if row["value"] != topic:
                row["value"] = topic
                st.session_state[f"topic_criteria_{row['key']}"] = "\n".join(topic["criteria"])
        else:
            rows.append(editable_row(topic))
    st.session_state.draft_config["conversation"]["topics_to_cover"] = copy.deepcopy(merged)
    st.session_state.pop("topic_import_review", None)
    st.session_state.topic_import_notice = "Added the selected document topics to your configuration."
    st.session_state.topic_import_confirmed = True


@st.dialog("Import topics from a document", width="large")
def topic_import_dialog():
    # The confirmation callback consumes the review before this fragment renders.
    # Close immediately so disabling the now-empty confirm button cannot hide its click.
    if st.session_state.pop("topic_import_confirmed", False):
        st.rerun()
    context = st.session_state.topic_import_context
    with st.container(key="topic_import_content"):
        st.markdown('<p class="import-description">Extract topics that match your purpose, then review them before adding them.</p>', unsafe_allow_html=True)
        st.markdown(
            '<div class="import-purpose"><svg viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="12" cy="12" r="11" fill="currentColor"/>'
            '<path d="M12 10v7" stroke="white" stroke-width="2" stroke-linecap="round"/>'
            '<circle cx="12" cy="6.5" r="1.25" fill="white"/></svg>'
            f'<p><strong>Purpose:</strong> {escape(context["purpose"])}</p></div>',
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader("Upload a document", type=list(DOCUMENT_TYPES),
                                    key=f"topic_import_file_{context['key']}", max_upload_size=20)
        if uploaded is not None:
            data = uploaded.getvalue()
            fingerprint = hashlib.sha256(data + uploaded.name.encode("utf-8")).hexdigest()
            changed_file = fingerprint != st.session_state.get("topic_import_fingerprint")
            retry = False
            if not changed_file and st.session_state.get("topic_import_error"):
                inline_error(st.session_state.topic_import_error)
                retry = st.button("Try extraction again", key="retry_topic_import")
            if changed_file or retry:
                st.session_state.topic_import_fingerprint = fingerprint
                st.session_state.topic_import_review = []
                st.session_state.topic_import_error = ""
                try:
                    with st.spinner("Reading the document and extracting relevant topics…"):
                        topics = extract_document_topics(uploaded.name, data, context["purpose"], context["existing_topics"])
                    st.session_state.topic_import_review = [editable_row(t) for t in topics]
                except ValueError as error:
                    st.session_state.topic_import_error = str(error)
                    inline_error(str(error))
            review = st.session_state.get("topic_import_review", [])
            if not st.session_state.get("topic_import_error"):
                if not review:
                    st.caption("No topics to add. The document may have no matching guidance, its topics may already be covered, or all suggestions were removed.")
                else:
                    st.markdown(f"**Review {len(review)} extracted topic{'s' if len(review) != 1 else ''}**")
            invalid = False
            with st.container(key="import_topics_scroll", border=False):
                for row in review:
                    topic = row["value"]
                    key = row["key"]
                    with removable_expander(topic["label"] or "Untitled topic", key, "topic_import_review"):
                        label = st.text_input("Topic", value=topic["label"], key=f"import_label_{key}")
                        criteria = st.text_area("Enough coverage means", value="\n".join(topic["criteria"]), key=f"import_criteria_{key}", height=110)
                        row["value"] = {**topic, "label": label.strip(), "criteria": [c.strip() for c in criteria.splitlines() if c.strip()]}
                        if not label.strip() or not row["value"]["criteria"]:
                            invalid = True
                            inline_error("Enter a topic name and at least one coverage criterion.")
                        if topic["source_section"]:
                            st.caption(f"Source: {topic['source_section']}")
                        st.markdown(f'<blockquote class="import-source">{escape(topic["source_excerpt"])}</blockquote>', unsafe_allow_html=True)
        else:
            st.session_state.topic_import_review = []
            st.session_state.topic_import_fingerprint = None
            st.session_state.topic_import_error = ""
            review, invalid = [], False
    with st.container(key="topic_import_footer"):
        if st.session_state.get("topic_import_review_error"):
            inline_error(st.session_state.topic_import_review_error)
        cancel, _, confirm = st.columns([1, 2.25, 1.4], gap="small")
        if cancel.button("Cancel", key="cancel_topic_import", use_container_width=True):
            st.session_state.pop("topic_import_review", None)
            st.rerun()
        confirm_clicked = confirm.button("Add selected topics", key="confirm_topic_import", type="primary", use_container_width=True,
                          disabled=not review or invalid or bool(st.session_state.get("topic_import_error")),
                          on_click=confirm_imported_topics)
        if confirm_clicked and st.session_state.get("topic_import_confirmed"):
            st.rerun()


def conversation_editor(config, disabled=False):
    panel_heading("Conversation Configuration", "settings")
    field_heading("Purpose", "Describe what you want to achieve with the conversation analysis. This helps guide the AI in capturing the right information.")
    purpose_error = st.empty()
    if "_purpose" not in st.session_state:
        st.session_state._purpose = config["purpose"]
    purpose = st.text_area("Purpose", key="_purpose", height=100, max_chars=1000, label_visibility="collapsed", disabled=disabled)
    if not purpose.strip():
        inline_error("Enter a purpose for the conversation.", purpose_error)
    st.markdown(f'<div class="field-count">{len(purpose):,}/1,000</div>', unsafe_allow_html=True)
    field_heading("Pay attention to", "Specify the key areas to focus on during analysis. Enter one focus area per line; blank lines are ignored.")
    if "_focus_text" not in st.session_state:
        st.session_state._focus_text = "\n".join(config["pay_attention_to"])
    focuses = st.text_area("Pay attention to", key="_focus_text", height=110, max_chars=1000, label_visibility="collapsed", disabled=disabled)
    st.markdown(f'<div class="field-count">{len(focuses):,}/1,000</div>', unsafe_allow_html=True)
    field_heading("Topics to cover", "Add topics you need to cover. The report tracks whether each has a complete, partial, or missing answer.")
    topics_error = st.empty()
    if "topic_rows" not in st.session_state:
        st.session_state.topic_rows = [editable_row(t) for t in config.get("topics_to_cover", [])]
    topics = []
    topic_errors = []
    with st.container(key="configured_topics_scroll", border=False):
        for row in st.session_state.topic_rows:
            key = row["key"]
            topic = row["value"]
            with removable_expander(topic.get("label") or "New topic", key, "topic_rows", disabled=disabled):
                label = st.text_input("Topic", value=topic.get("label", ""), key=f"topic_label_{key}", placeholder="Project ownership", disabled=disabled)
                criteria = st.text_area("Enough coverage means", value="\n".join(topic.get("criteria", [])), key=f"topic_criteria_{key}", placeholder="Their responsibility\nTheir personal contribution\nThe outcome", help="One required detail per line. Green requires all details; yellow means some information exists; red means no usable answer.", disabled=disabled)
                row["value"] = {"id": topic.get("id", f"topic_{key}"), "label": label.strip(), "criteria": [c.strip() for c in criteria.splitlines() if c.strip()]}
                topics.append(row["value"])
                if not label.strip():
                    topic_errors.append(f"Topic {len(topics)} needs a name.")
                if not row["value"]["criteria"]:
                    topic_errors.append(f"{label.strip() or 'Topic ' + str(len(topics))} needs at least one coverage criterion.")
    if st.session_state.get("topic_import_notice"):
        st.toast(st.session_state.pop("topic_import_notice"))
    with st.container(key="topic_actions"):
        add_topic, upload_topics, _ = st.columns([1.1, 1, 2.5], gap="small")
        add_clicked = add_topic.button("Add Topic", icon=":material/add:", disabled=disabled, use_container_width=True)
        upload_clicked = upload_topics.button("Upload", icon=":material/upload_file:", key="upload_topics", disabled=disabled, use_container_width=True)
    if add_clicked:
        st.session_state.topic_rows.append(editable_row({"label": "", "criteria": []}))
        st.rerun()
    if upload_clicked:
        if not purpose.strip():
            inline_error("Enter a purpose before uploading a document.", topics_error)
        else:
            st.session_state.topic_import_context = {"key": uuid.uuid4().hex, "purpose": purpose.strip(), "existing_topics": copy.deepcopy(topics)}
            st.session_state.topic_import_review = []
            st.session_state.topic_import_fingerprint = None
            st.session_state.topic_import_error = ""
            st.session_state.topic_import_review_error = ""
            st.session_state.topic_import_confirmed = False
            topic_import_dialog()
    if topic_errors:
        inline_error(" ".join(topic_errors), topics_error)
    field_heading("Participant Roles", "Define participant roles to guide analysis. Speakers aren’t automatically identified.", "participants")
    participants_error = st.empty()
    with st.container(border=False):
        for index, row in enumerate(st.session_state.speaker_rows, 1):
            value, remove = st.columns([5, 1], vertical_alignment="bottom")
            row["value"] = value.text_input(f"Participant {index} role", value=row["value"], key=f"speaker_{row['key']}", disabled=disabled)
            if remove.button("Remove participant", icon=":material/delete:", help=f"Remove participant {index}", key=f"remove_speaker_{row['key']}", disabled=disabled):
                st.session_state.speaker_rows.remove(row)
                st.rerun()
    if st.button("Add Participant", icon=":material/add:", disabled=disabled):
        st.session_state.speaker_rows.append(editable_row(""))
        st.rerun()
    if any(not row["value"].strip() for row in st.session_state.speaker_rows):
        inline_error("Enter a role for each participant.", participants_error)
    return {
        "purpose": purpose.strip(),
        "pay_attention_to": [line.strip() for line in focuses.splitlines() if line.strip()],
        "topics_to_cover": topics,
        "speakers": [{"id": f"speaker_{index}", "role": row["value"].strip()} for index, row in enumerate(st.session_state.speaker_rows, 1)],
    }


def report_editor(disabled=False, tracking_topics=False):
    panel_heading("Custom Report", "chart")
    report_error = st.empty()
    sections = []
    rows = st.session_state.section_rows
    for index, row in enumerate(rows):
        section = row["value"]
        key = row["key"]
        with removable_expander(f"{index + 1}. {section['label'] or 'Untitled section'}", key, "section_rows", kind="section", disabled=disabled):
            section_error = st.empty()
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
            try:
                validated = normalize_section_ids([updated])
                validate_report_sections(validated)
                if tracking_topics and validated[0]["id"] == "topic_coverage":
                    raise ValueError("Choose another label; Topic coverage is reserved for topic tracking.")
            except ValueError as error:
                inline_error(str(error).removeprefix("Report section 1: "), section_error)
            up, down = st.columns(2)
            if up.button("Move up", icon=":material/arrow_upward:", key=f"up_{key}", disabled=disabled or index == 0):
                rows[index - 1], rows[index] = rows[index], rows[index - 1]
                st.rerun()
            if down.button("Move down", icon=":material/arrow_downward:", key=f"down_{key}", disabled=disabled or index == len(rows) - 1):
                rows[index + 1], rows[index] = rows[index], rows[index + 1]
                st.rerun()
    if st.button("Add Report Section", icon=":material/add:", disabled=disabled):
        rows.append(editable_row({"label": "New Section", "type": "text", "instruction": "", "required": True}))
        st.rerun()
    if not sections:
        inline_error("Add at least one report section.", report_error)
    return {"sections": normalize_section_ids(sections)}


def render_report(sections, report, topics=()):
    coverage = topic_coverage_html(topics, report)
    if coverage:
        st.markdown(coverage, unsafe_allow_html=True)
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
