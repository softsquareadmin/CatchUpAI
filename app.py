"""Run with: python -m streamlit run app.py"""
from html import escape
from pathlib import Path
import copy
import queue
import time
import uuid
import logging

import streamlit as st

from catchupai.frontend import conversation_editor, editable_row, render_report, report_editor, workspace_styles, page_heading, panel_heading
from catchupai.config import ANALYSIS_INTERVAL
from catchupai.session import LiveSession
from catchupai.browser_audio import BrowserAudioSession, browser_recorder
from catchupai.models import default_config, normalize_section_ids, validate_config
from catchupai.recording import recording_info
from catchupai.report_export import build_report_document
from catchupai.templates import list_templates, load_template, save_template, delete_template
from catchupai.frontend import icon, inline_error, dismiss_row_confirmation
from catchupai.topic_coverage import topic_coverage_html
from catchupai.cost_estimation import render_cost_estimation
from catchupai.session_notices import notice_text, session_notice
from catchupai.notifications import notify_once

_LOGGER = logging.getLogger(__name__)

st.set_page_config(page_title="CatchUpAI", page_icon=str(Path(__file__).resolve().parent / "assets" / "icon.png"), layout="wide", initial_sidebar_state="expanded")
workspace_styles()

if "draft_config" not in st.session_state:
    initial = default_config()
    if "section_rows" in st.session_state:
        initial["report"]["sections"] = normalize_section_ids([row["value"] for row in st.session_state.section_rows])
        initial["conversation"]["purpose"] = st.session_state.get("purpose", initial["conversation"]["purpose"])
        if "focus_rows" in st.session_state:
            initial["conversation"]["pay_attention_to"] = [row["value"] for row in st.session_state.focus_rows]
    st.session_state.draft_config = initial
    if "speaker_rows" not in st.session_state:
        st.session_state.speaker_rows = [editable_row(s["role"]) for s in initial["conversation"]["speakers"]]
    if "section_rows" not in st.session_state:
        st.session_state.section_rows = [editable_row(s) for s in initial["report"]["sections"]]

for key, value in {"live": None, "latest": None, "errors": [], "runtime_status": "Ready", "active_config": None, "pending_report": None, "reveal_at": 0.0, "analysis_loading": False, "source_name": None, "browser_started_ids": set()}.items():
    if key not in st.session_state:
        st.session_state[key] = value

if st.session_state.get("page") not in ("Conversation Studio", "Configurations", "Cost Estimation"):
    st.session_state.page = "Conversation Studio"


def navigate(destination):
    st.session_state.page = destination


def apply_configuration(config):
    st.session_state.draft_config = copy.deepcopy(config)
    # Assign widget state explicitly; removing keys can restore the old browser values.
    st.session_state._purpose = config["conversation"]["purpose"]
    st.session_state._focus_text = "\n".join(config["conversation"]["pay_attention_to"])
    st.session_state.speaker_rows = [editable_row(s["role"]) for s in config["conversation"]["speakers"]]
    st.session_state.topic_rows = [editable_row(t) for t in config["conversation"].get("topics_to_cover", [])]
    st.session_state.crm_content = copy.deepcopy(config["conversation"].get("crm_content"))
    st.session_state.section_rows = [editable_row(s) for s in config["report"]["sections"]]


def reset_configuration():
    apply_configuration({"conversation": {"purpose": "", "pay_attention_to": [], "topics_to_cover": [], "speakers": []}, "report": {"sections": []}})
    st.session_state.template_name = ""
    st.session_state.loaded_template_id = None
    st.session_state.template_selection = None
    st.session_state.template_picker_epoch = st.session_state.get("template_picker_epoch", 0) + 1


def select_template(template_id):
    st.session_state.template_selection = template_id
    st.session_state.template_picker_epoch = st.session_state.get("template_picker_epoch", 0) + 1


def comparable_configuration(config):
    normalized = copy.deepcopy(config)
    normalized.setdefault("analysis_interval", ANALYSIS_INTERVAL)
    normalized["conversation"].setdefault("topics_to_cover", [])
    for index, speaker in enumerate(normalized["conversation"]["speakers"], 1):
        speaker["id"] = f"speaker_{index}"
    normalized["report"]["sections"] = normalize_section_ids(normalized["report"]["sections"])
    for section in normalized["report"]["sections"]:
        section.setdefault("required", True)
        section.setdefault("final_only", False)
    return normalized


def load_selected_template():
    try:
        template = load_template(st.session_state.template_selection)
        apply_configuration(template["config"])
        st.session_state.loaded_template_id = template["id"]
        st.session_state.template_name = template["name"]
        st.session_state.template_notice = ("success", f"Loaded {template['name']}.")
    except (OSError, ValueError, TypeError) as error:
        st.session_state.template_notice = ("error", str(error))


def cancel_template_changes():
    try:
        template = load_template(st.session_state.loaded_template_id)
        apply_configuration(template["config"])
        st.session_state.template_name = template["name"]
        st.session_state.template_selection = template["id"]
        st.session_state.template_picker_epoch = st.session_state.get("template_picker_epoch", 0) + 1
    except (OSError, ValueError, TypeError) as error:
        st.session_state.template_notice = ("error", str(error))


def perform_template_deletion(template_id, name):
    try:
        delete_template(template_id)
        if st.session_state.get("loaded_template_id") == template_id:
            st.session_state.loaded_template_id = None
        if st.session_state.get("template_selection") == template_id:
            st.session_state.template_selection = None
        st.session_state.template_picker_epoch = st.session_state.get("template_picker_epoch", 0) + 1
        st.session_state.template_notice = ("success", f"Deleted {name}.")
    except (OSError, ValueError) as error:
        st.session_state.template_notice = ("error", str(error))


def confirm_template_deletion(template_id, name):
    with st.container(key=f"confirm_remove_template_{template_id}"):
        st.write(f'Delete "{name}"?')
        cancel, confirm = st.columns(2)
        cancel.button("Cancel", key=f"cancel_delete_template_{template_id}",
                      on_click=dismiss_row_confirmation, args=(template_id,))
        confirm.button("Delete template", key=f"confirm_delete_template_{template_id}", type="primary",
                       on_click=perform_template_deletion, args=(template_id, name))


def template_controls(disabled=False):
    with st.container(border=False, key="configuration_templates"):
        templates, errors = list_templates()
        by_id = {t["id"]: t for t in templates}
        if st.session_state.get("template_selection") not in by_id:
            st.session_state.template_selection = None
        if st.session_state.get("loaded_template_id") not in by_id:
            st.session_state.loaded_template_id = None
        notice = st.session_state.pop("template_notice", None)
        if notice:
            if notice[0] == "success":
                st.toast(notice[1])
        loaded_id = st.session_state.get("loaded_template_id")
        loaded = by_id.get(loaded_id)
        changed = loaded is not None and (
            comparable_configuration(st.session_state.draft_config) != comparable_configuration(loaded["config"])
            or st.session_state.get("template_name", "").strip() != loaded["name"]
        )
        title, loader, saver = st.columns([1.35, 2.5, 4.8] if loaded_id else [1.35, 2.7, 4.2], gap="small", vertical_alignment="top")
        with title:
            st.markdown(f'<div class="template-toolbar-title"><span class="icon-tile">{icon("file")}</span><strong>Templates</strong></div>', unsafe_allow_html=True)
        with loader, st.container(key="template_load_group"):
            select, load = st.columns([4, 1.2], gap="small", vertical_alignment="bottom")
            selection = st.session_state.get("template_selection")
            with select:
                st.markdown('<div class="template-field-label">Loaded template</div>', unsafe_allow_html=True)
                with st.popover(by_id[selection]["name"] if selection else "Select a saved template", disabled=disabled, use_container_width=True, key=f"template_picker_{st.session_state.get('template_picker_epoch', 0)}"):
                    st.button("Create new template", icon=":material/add:", key="create_new_template", on_click=reset_configuration, disabled=disabled, use_container_width=True)
                    if not templates:
                        st.caption("No saved templates yet.")
                    for template in templates:
                        choose, remove = st.columns([6, 1], vertical_alignment="center")
                        choose.button(template["name"], key=f"choose_template_{template['id']}", on_click=select_template, args=(template["id"],), use_container_width=True, disabled=disabled)
                        epoch = st.session_state.get(f"remove_confirmation_epoch_{template['id']}", 0)
                        with remove.popover("Delete template", icon=":material/close:", key=f"remove_template_{template['id']}_{epoch}", help=f"Delete {template['name']}", disabled=disabled):
                            confirm_template_deletion(template["id"], template["name"])
            load.button("Load", key="load_template", disabled=disabled or selection is None, on_click=load_selected_template, use_container_width=True)
            if errors or notice and notice[0] == "error":
                inline_error(" ".join(errors + ([notice[1]] if notice and notice[0] == "error" else [])))
        with saver, st.container(key="template_save_group"):
            if loaded_id:
                name, cancel, save, secondary = st.columns([2.8, 1.2, 1.9, 1.9], gap="small", vertical_alignment="bottom")
            else:
                name, save, secondary = st.columns([3.6, 1.8, 1.1], gap="small", vertical_alignment="bottom")
            name.text_input("Template name", key="template_name", placeholder="e.g. Technical Interview", disabled=disabled)
            if loaded_id:
                cancel.button("Cancel", key="cancel_template_changes", on_click=cancel_template_changes, disabled=disabled or not changed, use_container_width=True)
                update_clicked = save.button("Save changes", key="update_template", type="primary", disabled=disabled or not changed, use_container_width=True)
                save_clicked = secondary.button("Save as new", key="save_new_template", disabled=disabled, use_container_width=True)
            else:
                update_clicked = False
                save_clicked = save.button("Save template", key="save_new_template", type="primary", disabled=disabled, use_container_width=True)
                secondary.button("Reset", key="reset_configuration", on_click=reset_configuration, disabled=disabled, use_container_width=True)
            save_error = st.empty()
        return save_clicked, update_clicked, save_error


with st.sidebar:
    for destination, symbol in [("Conversation Studio", "graphic_eq"), ("Configurations", "settings"), ("Cost Estimation", "payments")]:
        st.button(destination, icon=f":material/{symbol}:", key=f"nav_{symbol}", type="primary" if st.session_state.page == destination else "secondary", use_container_width=True, disabled=destination == "Configurations" and st.session_state.live is not None and st.session_state.live.running, on_click=navigate, args=(destination,))
page = st.session_state.page

live = st.session_state.live
running = live is not None and live.running


def configuration_error(config):
    try:
        validate_config(config)
    except ValueError as error:
        return str(error)
    return None


def start_session(config, recording_bytes=None, source_name=None, browser_session_id=None):
    validate_config(config)
    session = BrowserAudioSession(config, browser_session_id) if browser_session_id else LiveSession(config, recording_bytes=recording_bytes)
    st.session_state.latest = None
    st.session_state.pending_report = None
    st.session_state.analysis_loading = False
    st.session_state.errors = []
    st.session_state.active_config = copy.deepcopy(config)
    st.session_state.report_navigation = "coverage"
    st.session_state.source_name = source_name
    st.session_state.runtime_status = "Connecting"
    st.session_state.live = session
    session.start()


def interval_label(seconds):
    return f"{seconds} sec" if seconds < 60 or seconds % 60 else f"{seconds // 60} min"


def dismiss_analysis_interval():
    st.session_state.analysis_interval_open = False


@st.dialog("Select Analysis Interval", width="small", icon=":material/schedule:", on_dismiss=dismiss_analysis_interval)
def analysis_interval_dialog():
    with st.container(border=False, key="analysis_interval_content"):
        selected = st.session_state._analysis_interval_selection
        st.markdown(f'<div class="analysis-interval-value">{interval_label(selected)}</div>', unsafe_allow_html=True)
        st.select_slider(
            "Analysis interval", options=st.session_state._analysis_interval_options,
            key="_analysis_interval_selection", format_func=interval_label,
            label_visibility="collapsed",
        )
        labels = ''.join(f'<span>{interval_label(value)}</span>' for value in st.session_state._analysis_interval_options)
        st.markdown(f'<div class="analysis-interval-ticks">{labels}</div>', unsafe_allow_html=True)
        st.caption("How often CatchUpAI requests an updated report during live recording. Minimum: 30 seconds. Shorter intervals give more frequent updates; longer intervals make fewer analysis requests. Changes apply to your next recording.")
    with st.container(key="analysis_interval_footer"):
        _, cancel, apply = st.columns([2, 1, 1], gap="small")
        if cancel.button("Cancel", key="cancel_analysis_interval", use_container_width=True):
            dismiss_analysis_interval()
            st.rerun()
        if apply.button("Apply", key="apply_analysis_interval", type="primary", use_container_width=True):
            st.session_state.draft_config["analysis_interval"] = st.session_state._analysis_interval_selection
            dismiss_analysis_interval()
            st.rerun()


def clear_uploaded_recording():
    st.session_state.upload_generation = st.session_state.get("upload_generation", 0) + 1


def reset_workspace():
    session = st.session_state.live
    if session is not None and session.running:
        return
    clear_uploaded_recording()
    st.session_state.live = None
    st.session_state.latest = None
    st.session_state.pending_report = None
    st.session_state.analysis_loading = False
    st.session_state.errors = []
    st.session_state.runtime_status = "Ready"
    st.session_state.active_config = None
    st.session_state.source_name = None
    st.session_state.reveal_at = 0.0
    st.session_state.recorder_reset_token = uuid.uuid4().hex
    st.session_state.capture_mode = "Record Live"
    st.session_state.report_navigation = "coverage"


def load_capture_template():
    template_id = st.session_state.capture_template
    if template_id:
        select_template(template_id)
        load_selected_template()


def upload_recording(config, disabled=False):
    upload_key = f"recording_upload_{st.session_state.get('upload_generation', 0)}"
    selected = st.session_state.get(upload_key) is not None
    with st.container(key="upload_picker_selected" if selected else "upload_picker_empty"):
        uploaded = st.file_uploader("Choose an audio recording", type=["wav", "mp3", "flac", "ogg"], key=upload_key, disabled=disabled, label_visibility="collapsed")
    if uploaded is None:
        return
    recording_bytes = uploaded.getvalue()
    try:
        info = recording_info(recording_bytes)
    except ValueError as error:
        notify_once(str(error), key=("invalid_upload", upload_key, str(error)))
        st.button("Remove uploaded audio", icon=":material/close:", disabled=disabled, on_click=clear_uploaded_recording)
        return
    minutes, seconds = divmod(int(info.duration), 60)
    size = len(recording_bytes) / (1024 * 1024)
    size_label = f"{size:.1f} MB" if size >= 1 else f"{len(recording_bytes) / 1024:.1f} KB"
    with st.container(key="upload_file_card", gap=None):
        details, remove = st.columns([12, 1], gap="small", vertical_alignment="center")
        with details:
            st.markdown(f'<div class="uploaded-file"><svg viewBox="0 0 32 40" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 2h16l8 8v28H4zM20 2v10h8M13 29V18l9-2v11"/><ellipse cx="10" cy="30" rx="3" ry="2" fill="currentColor"/><ellipse cx="19" cy="28" rx="3" ry="2" fill="currentColor"/></svg><div><strong title="{escape(uploaded.name, quote=True)}">{escape(uploaded.name)}</strong><div class="uploaded-metadata"><span>{size_label}</span><span class="uploaded-duration"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>{minutes}:{seconds:02d}</span></div></div></div>', unsafe_allow_html=True)
        remove.button("Remove uploaded audio", icon=":material/close:", key="remove_uploaded_audio", help="Remove uploaded audio", disabled=disabled, on_click=clear_uploaded_recording)
    session = st.session_state.live
    if session is not None and session.running and not isinstance(session, BrowserAudioSession):
        if st.button("Cancel recording analysis", key="cancel_recording_analysis", disabled=session.stop_event.is_set(), width="stretch"):
            session.stop()
        return
    if st.button("Analyze recording", icon=":material/graphic_eq:", type="primary", disabled=disabled, width="stretch"):
        if st.session_state.live is not None and st.session_state.live.running:
            notify_once("Stop the current session before analyzing another recording.",
                        key="session_busy", scope=st.session_state.live.usage_tracker.session_id)
            return
        start_session(config, recording_bytes, uploaded.name)
        st.rerun()


def drain_session_events():
    session = st.session_state.live
    if session is None:
        return
    while True:
        try:
            kind, value = session.events.get_nowait()
        except queue.Empty:
            break
        if kind == "report" and not (isinstance(session, BrowserAudioSession) and session.cancel_event.is_set()):
            previous = st.session_state.pending_report or st.session_state.latest
            if previous is None or (value["timestamp"], value.get("is_final", False)) > (previous["timestamp"], previous.get("is_final", False)):
                st.session_state.pending_report = value
                st.session_state.reveal_at = time.monotonic() + 0.7
                st.session_state.errors = [notice for notice in map(session_notice, st.session_state.errors)
                                           if notice["checkpoint"] is not None and notice["checkpoint"] > value["checkpoint"]]
        elif kind == "analysis":
            st.session_state.analysis_loading = value
        elif kind == "error":
            if not isinstance(value, dict):
                _LOGGER.warning("Audio analysis failed: %s", value)
            notice = session_notice(value)
            if notice not in st.session_state.errors:
                st.session_state.errors.append(notice)
            st.session_state.errors = st.session_state.errors[-5:]
        elif kind == "status":
            st.session_state.runtime_status = value
            if value == "Listening":
                st.session_state.errors = [notice for notice in map(session_notice, st.session_state.errors)
                                           if notice["kind"] != "connection"]
        elif kind == "finished":
            st.session_state.runtime_status = "Stopped"
            st.session_state.analysis_loading = False
    if st.session_state.pending_report is not None and time.monotonic() >= st.session_state.reveal_at:
        st.session_state.latest = st.session_state.pending_report
        st.session_state.pending_report = None


def render_session_notice():
    if not st.session_state.errors:
        return
    session = st.session_state.live
    recording = (isinstance(session, BrowserAudioSession) and session.running
                 and not session.input_finished.is_set() and not session.cancel_event.is_set())
    for error in st.session_state.errors:
        notice = session_notice(error)
        notify_once(notice_text(notice, recording=recording,
                               has_report=st.session_state.latest is not None or st.session_state.pending_report is not None,
                               uploaded=st.session_state.source_name is not None),
                    key=("analysis", notice["kind"], notice["checkpoint"], notice["is_final"]),
                    scope=session.usage_tracker.session_id if session else "workspace")


@st.fragment
def recorder_controls():
    session = st.session_state.live
    active = session is not None and session.running
    config = st.session_state.draft_config
    error = configuration_error(config)
    with st.container(key="capture_card"):
        with st.container(key="capture_header"):
            heading, template = st.columns([2.2, 1], vertical_alignment="center")
            with heading:
                st.markdown(
                    '<div class="panel-heading">'
                    f'<span class="icon-tile solid">{icon("wave")}</span>'
                    '<div><h2>Record or Upload a Conversation</h2></div></div>', unsafe_allow_html=True,
                )
            with template:
                templates, template_errors = list_templates()
                names = {item["id"]: item["name"] for item in templates}
                loaded_id = st.session_state.get("loaded_template_id")
                options = [None, *names]
                st.selectbox("Template", options, index=options.index(loaded_id) if loaded_id in names else 0,
                             format_func=lambda value: names.get(value, "Current configuration"),
                             key="capture_template", disabled=active, on_change=load_capture_template,
                             label_visibility="collapsed")
                if template_errors:
                    inline_error(" ".join(template_errors))
            if st.button("Reset", key="reset_workspace", disabled=active, on_click=reset_workspace, help="Reset recording, uploaded file, and report. Keep your configuration."):
                st.rerun()
        with st.container(key="capture_body"):
            mode = st.session_state.get("capture_mode", "Record Live")
            with st.container(key="record_action"):
                browser_session = session if isinstance(session, BrowserAudioSession) else None
                message = browser_recorder(
                    session_id=browser_session.session_id if browser_session else None,
                    ack=browser_session.highest_sequence if browser_session else -1,
                    busy=active, disabled=error is not None,
                    status=st.session_state.runtime_status,
                    mode=mode,
                    reset_token=st.session_state.get("recorder_reset_token"),
                    key="live_browser_recorder", default=None,
                )
                if message and message.get("mode") in ("Record Live", "Upload Audio") and not active:
                    if message["mode"] != mode:
                        st.session_state.capture_mode = message["mode"]
                        st.rerun()
                if message and message.get("cancelled"):
                    st.session_state.browser_started_ids.add(message["session_id"])
                    if isinstance(session, BrowserAudioSession) and session.session_id == message["session_id"]:
                        session.cancel()
                        st.session_state.latest = st.session_state.pending_report = None
                        st.session_state.analysis_loading = False
                        st.session_state.runtime_status = "Ready"
                elif message and message.get("started"):
                    if message["session_id"] not in st.session_state.browser_started_ids and not active:
                        st.session_state.browser_started_ids.add(message["session_id"])
                        start_session(config, browser_session_id=message["session_id"])
                        session = st.session_state.live
                    if isinstance(session, BrowserAudioSession) and session.session_id == message["session_id"] and session.running:
                        try:
                            session.ingest(message)
                        except (ValueError, KeyError) as audio_error:
                            _LOGGER.warning("Audio recording input failed: %s", audio_error)
                            notify_once("Some recorded audio couldn't be processed. Try recording again if analysis doesn't resume.",
                                        key="audio_input", scope=message["session_id"])
                    if message.get("error"):
                        notify_once(message["error"], key=("recorder", message["error"]), scope=message["session_id"])
            if mode == "Upload Audio":
                with st.container(key="upload_action"):
                    upload_recording(config, disabled=active or error is not None)
    if error:
        notify_once(f"Update Configurations before starting: {error}", key=("configuration", error))
    # Refresh navigation and reports only when the session changes running state.
    current_session = st.session_state.live
    if (current_session is not None and current_session.running) != running:
        st.rerun()


@st.fragment(run_every=1)
def workspace_updates(show_home, show_cost=False):
    drain_session_events()
    session = st.session_state.live
    if session is not None and not session.running and running:
        st.rerun()
    if show_cost:
        config = st.session_state.active_config or st.session_state.draft_config
        result = st.session_state.pending_report or st.session_state.latest
        render_session_notice()
        render_cost_estimation(
            session.usage_tracker.snapshot() if session else None,
            active=session is not None and session.running,
            uploaded=st.session_state.source_name is not None,
            source_name=st.session_state.source_name,
            analysis_interval=config.get("analysis_interval", ANALYSIS_INTERVAL),
            final_report=bool(result and result.get("is_final")),
        )
        return
    if not show_home:
        return
    active = session is not None and session.running
    result = st.session_state.latest
    config = st.session_state.active_config or st.session_state.draft_config
    topics = config["conversation"].get("topics_to_cover", [])
    sections = {f"section:{section['id']}": section for section in config["report"]["sections"]}
    labels = {"coverage": "Topic Coverage", **{key: section["label"] for key, section in sections.items()}}
    if st.session_state.get("report_navigation") not in labels:
        st.session_state.report_navigation = "coverage"
    todo, report = st.columns([1, 2.45], gap="small")
    with todo, st.container(key="studio_todo", gap=None):
        with st.container(key="studio_todo_header"):
            panel_heading("To do", "list", strong=True)
        with st.container(height=430, border=False, key="report_navigation_scroll"):
            selected = st.radio("Report sections", options=list(labels), format_func=labels.get,
                                key="report_navigation", label_visibility="collapsed")
    with report, st.container(key="studio_report", gap=None):
        completed = result is not None and not active and st.session_state.pending_report is None and not st.session_state.analysis_loading
        with st.container(key="studio_report_header"):
            checkpoint = ""
            status_label = ""
            if st.session_state.pending_report is not None:
                status_label = "Loading new report…"
            elif st.session_state.analysis_loading:
                status_label = "Analyzing conversation…"
            elif active:
                runtime_status = st.session_state.runtime_status
                if runtime_status in ("Connecting", "Processing recording", "Finishing report", "Restoring conversation") or runtime_status.startswith("Reconnecting"):
                    status_label = runtime_status
            if status_label:
                checkpoint = f'<span class="report-checkpoint report-header-status" role="status" aria-live="polite"><span class="report-status-spinner" aria-hidden="true"></span>{escape(status_label)}</span>'
            elif result is not None:
                minutes, seconds = divmod(int(result["timestamp"]), 60)
                checkpoint = f'<span class="report-checkpoint">Checkpoint {result["checkpoint"]} | {minutes}:{seconds:02d} of conversation</span>'
            st.markdown(f'<div class="panel-heading report-header-heading"><span class="icon-tile solid">{icon("chart")}</span><h2>{escape(f"Report - {labels[selected]}")}</h2>{checkpoint}</div>', unsafe_allow_html=True)
            with st.container(key="report_header_download"):
                st.download_button(
                    "Download report",
                    data=build_report_document(config, result, st.session_state.source_name) if completed else "",
                    file_name="CatchUpAI-report.html", mime="text/html", key="download_report",
                    disabled=not completed, on_click="ignore",
                    help="Download a readable report. Open it in a browser to print or save as PDF.",
                )
        with st.container(height=430, border=False, key="report_content_scroll"):
            render_session_notice()
            if result is None:
                if selected == "coverage" and topics:
                    st.markdown(topic_coverage_html(topics, {}, show_heading=False), unsafe_allow_html=True)
                elif selected == "coverage":
                    st.caption("Add topics in Configurations to track what still needs to be covered.")
                elif sections[selected].get("final_only", False):
                    st.caption("This section will be generated after recording ends or when an uploaded recording is analyzed.")
                else:
                    st.caption("Your report will appear here once the complete recording has been analyzed." if st.session_state.source_name else "Start recording or upload audio to see this report section.")
                return
            if completed and st.session_state.errors:
                st.caption("This download contains the latest valid report; the session ended with errors.")
            if selected == "coverage":
                if topics:
                    st.markdown(topic_coverage_html(topics, result["report"], show_heading=False), unsafe_allow_html=True)
                else:
                    st.caption("Add topics in Configurations to track what still needs to be covered.")
            else:
                section = sections[selected]
                if section.get("final_only", False) and not result.get("is_final", False):
                    st.caption("This section will be generated after recording ends or when an uploaded recording is analyzed.")
                else:
                    render_report([section], result["report"], show_heading=False)


if page in ("Conversation Studio", "Cost Estimation"):
    # Keep the recorder at the same position on both pages so navigation does
    # not unmount the browser microphone component during live capture.
    with st.container(key="recording_workspace"):
        heading = st.empty()
        recorder_controls()
        workspace_updates(page == "Conversation Studio", show_cost=page == "Cost Estimation")
else:
    heading, interval_button = st.columns([4, 1], vertical_alignment="top")
    with heading:
        page_heading("Configurations", "")
    with interval_button, st.container(key="analysis_interval_button"):
        open_interval = st.button("Analysis Interval", icon=":material/schedule:", use_container_width=True, disabled=running)
    # Reserve the toolbar position, then render it using this run's editor values.
    template_toolbar = st.container()
    left, right = st.columns(2, gap="small")
    with left, st.container(border=False, key="conversation_panel"):
        conversation = conversation_editor(st.session_state.draft_config["conversation"], disabled=running)
    with right, st.container(border=False, key="report_editor_panel"):
        report_definition = report_editor(disabled=running, tracking_topics=bool(conversation.get("topics_to_cover")))
    st.session_state.draft_config = {
        "conversation": conversation, "report": report_definition,
        "analysis_interval": st.session_state.draft_config.get("analysis_interval", ANALYSIS_INTERVAL),
    }
    error = configuration_error(st.session_state.draft_config)
    with template_toolbar:
        save_new, update_existing, save_error = template_controls(disabled=running)
    if save_new or update_existing:
        try:
            name = st.session_state.template_name.strip()
            if not name:
                raise ValueError("Enter a template name.")
            existing, _ = list_templates()
            if save_new and any(t["name"].casefold() == name.casefold() for t in existing):
                raise ValueError("Use a different, unique name to save a new template.")
            if error:
                raise ValueError("Review the highlighted configuration fields.")
            template = save_template(st.session_state.template_name, st.session_state.draft_config,
                                     st.session_state.get("loaded_template_id") if update_existing else None)
            st.session_state.loaded_template_id = template["id"]
            st.session_state.template_selection = template["id"]
            st.session_state.template_notice = ("success", f"{'Updated' if update_existing else 'Saved'} {template['name']}.")
            st.rerun()
        except (OSError, ValueError, TypeError) as template_error:
            inline_error(str(template_error), save_error)
    if open_interval:
        current_interval = st.session_state.draft_config.get("analysis_interval", ANALYSIS_INTERVAL)
        st.session_state._analysis_interval_selection = current_interval
        st.session_state._analysis_interval_options = sorted({30, 60, 120, 300, 600, current_interval})
        st.session_state.analysis_interval_open = True
    if st.session_state.get("analysis_interval_open"):
        analysis_interval_dialog()
    workspace_updates(False)
