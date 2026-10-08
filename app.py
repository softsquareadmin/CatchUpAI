"""Run with: python -m streamlit run app.py"""
from html import escape
from pathlib import Path
import copy
import queue
import time
import uuid

import streamlit as st

from catchupai.frontend import conversation_editor, editable_row, render_report, report_editor, workspace_styles, page_heading, panel_heading, preview_cards, welcome_card
from catchupai.session import LiveSession
from catchupai.browser_audio import BrowserAudioSession, browser_recorder
from catchupai.models import default_config, normalize_section_ids, validate_config
from catchupai.recording import recording_info
from catchupai.report_export import build_report_document
from catchupai.templates import list_templates, load_template, save_template, delete_template
from catchupai.frontend import icon, inline_error

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

if st.session_state.get("page") not in ("Conversation Studio", "Configurations"):
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
    normalized["conversation"].setdefault("topics_to_cover", [])
    for index, speaker in enumerate(normalized["conversation"]["speakers"], 1):
        speaker["id"] = f"speaker_{index}"
    normalized["report"]["sections"] = normalize_section_ids(normalized["report"]["sections"])
    for section in normalized["report"]["sections"]:
        section.setdefault("required", True)
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


def perform_template_deletion(template_id, name):
    try:
        delete_template(template_id)
        if st.session_state.get("loaded_template_id") == template_id:
            st.session_state.loaded_template_id = None
        if st.session_state.get("template_selection") == template_id:
            st.session_state.template_selection = None
        st.session_state.template_notice = ("success", f"Deleted {name}.")
    except (OSError, ValueError) as error:
        st.session_state.template_notice = ("error", str(error))


@st.dialog("Delete template")
def confirm_template_deletion(template_id, name):
    st.write(f'Delete "{name}"? This removes the saved template. Your current configuration will stay in the editors.')
    cancel, confirm = st.columns(2)
    if cancel.button("Cancel", use_container_width=True):
        st.rerun()
    if confirm.button("Delete template", type="primary", use_container_width=True,
                      on_click=perform_template_deletion, args=(template_id, name)):
        st.rerun()


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
        title, loader, saver = st.columns([1.35, 2.7, 4.2], gap="small", vertical_alignment="top")
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
                        if remove.button("", icon=":material/close:", key=f"remove_template_{template['id']}", help=f"Delete {template['name']}", disabled=disabled, use_container_width=True):
                            confirm_template_deletion(template["id"], template["name"])
            load.button("Load", key="load_template", disabled=disabled or selection is None, on_click=load_selected_template, use_container_width=True)
            if errors or notice and notice[0] == "error":
                inline_error(" ".join(errors + ([notice[1]] if notice and notice[0] == "error" else [])))
        with saver, st.container(key="template_save_group"):
            name, save, secondary = st.columns([2.9, 1.8, 1.9] if loaded_id else [3.6, 1.8, 1.1], gap="small", vertical_alignment="bottom")
            name.text_input("Template name", key="template_name", placeholder="e.g. Technical Interview", disabled=disabled)
            if loaded_id:
                update_clicked = save.button("Save changes", key="update_template", type="primary", disabled=disabled or not changed, use_container_width=True)
                save_clicked = secondary.button("Save as new", key="save_new_template", disabled=disabled, use_container_width=True)
            else:
                update_clicked = False
                save_clicked = save.button("Save template", key="save_new_template", type="primary", disabled=disabled, use_container_width=True)
                secondary.button("Reset", key="reset_configuration", on_click=reset_configuration, disabled=disabled, use_container_width=True)
            save_error = st.empty()
        return save_clicked, update_clicked, save_error


with st.sidebar:
    for destination, symbol in [("Conversation Studio", "graphic_eq"), ("Configurations", "settings")]:
        st.button(destination, icon=f":material/{symbol}:", key=f"nav_{symbol}", type="primary" if st.session_state.page == destination else "secondary", use_container_width=True, disabled=st.session_state.live is not None and st.session_state.live.running, on_click=navigate, args=(destination,))
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
    st.session_state.source_name = source_name
    st.session_state.runtime_status = "Connecting"
    st.session_state.live = session
    session.start()


@st.dialog("Configuration Preview", width="large")
def preview_config(config):
    with st.container(border=False, key="preview_content"):
        preview_cards(config)
    with st.container(key="preview_footer"):
        spacer, close = st.columns([5, 1])
        if close.button("Close preview", use_container_width=True):
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
        st.error(str(error))
        st.button("Remove uploaded audio", icon=":material/close:", disabled=disabled, on_click=clear_uploaded_recording)
        return
    minutes, seconds = divmod(int(info.duration), 60)
    size = len(recording_bytes) / (1024 * 1024)
    size_label = f"{size:.1f} MB" if size >= 1 else f"{len(recording_bytes) / 1024:.1f} KB"
    with st.container(key="upload_file_card"):
        details, remove = st.columns([8, 1], vertical_alignment="center")
        with details:
            st.markdown(f'<div class="uploaded-file"><svg viewBox="0 0 32 40" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 2h16l8 8v28H4zM20 2v10h8M13 29V18l9-2v11"/><ellipse cx="10" cy="30" rx="3" ry="2" fill="currentColor"/><ellipse cx="19" cy="28" rx="3" ry="2" fill="currentColor"/></svg><div><strong>{escape(uploaded.name)}</strong><small>{size_label}</small></div></div>', unsafe_allow_html=True)
        remove.button("Remove uploaded audio", icon=":material/close:", key="remove_uploaded_audio", help="Remove uploaded audio", disabled=disabled, on_click=clear_uploaded_recording)
        st.markdown(f'<div class="uploaded-details"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><span>{minutes}:{seconds:02d}</span></div>', unsafe_allow_html=True)
    st.audio(recording_bytes)
    if st.button("Analyze recording", icon=":material/graphic_eq:", type="primary", disabled=disabled, width="stretch"):
        if st.session_state.live is not None and st.session_state.live.running:
            st.error("Stop the current session before analyzing another recording.")
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
            if previous is None or value["checkpoint"] > previous["checkpoint"]:
                st.session_state.pending_report = value
                st.session_state.reveal_at = time.monotonic() + 0.7
        elif kind == "analysis":
            st.session_state.analysis_loading = value
        elif kind == "error":
            st.session_state.errors.append(value)
        elif kind == "status":
            st.session_state.runtime_status = value
        elif kind == "finished":
            st.session_state.runtime_status = "Stopped"
            st.session_state.analysis_loading = False
    if st.session_state.pending_report is not None and time.monotonic() >= st.session_state.reveal_at:
        st.session_state.latest = st.session_state.pending_report
        st.session_state.pending_report = None


@st.fragment
def recorder_controls():
    session = st.session_state.live
    active = session is not None and session.running
    config = st.session_state.draft_config
    error = configuration_error(config)
    with st.container(key="capture_card"):
        with st.container(key="capture_header"):
            panel_heading("Record or Upload a Conversation", "wave", strong=True)
            if st.button("Reset", key="reset_workspace", disabled=active, on_click=reset_workspace, help="Reset recording, uploaded file, and report. Keep your configuration."):
                st.rerun()
        with st.container(key="capture_body"):
            record, upload = st.columns([1.43, 1], gap="small")
            with record, st.container(key="record_action"):
                browser_session = session if isinstance(session, BrowserAudioSession) else None
                message = browser_recorder(
                    session_id=browser_session.session_id if browser_session else None,
                    ack=browser_session.highest_sequence if browser_session else -1,
                    busy=active, disabled=error is not None,
                    status=st.session_state.runtime_status,
                    reset_token=st.session_state.get("recorder_reset_token"),
                    key="live_browser_recorder", default=None,
                )
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
                            st.error(str(audio_error))
                    if message.get("error"):
                        st.error(message["error"])
                if active and not browser_session:
                    st.caption(st.session_state.runtime_status)
                    if st.button("Cancel recording analysis", disabled=session.stop_event.is_set()):
                        session.stop()
            with upload, st.container(key="upload_action"):
                st.markdown("### Upload audio")
                st.caption("Upload an existing audio file to analyze.")
                upload_recording(config, disabled=active or error is not None)
    if error:
        st.warning(f"Update Configurations before starting: {error}")
    # Refresh navigation and reports only when the session changes running state.
    current_session = st.session_state.live
    if (current_session is not None and current_session.running) != running:
        st.rerun()


@st.fragment(run_every=1)
def workspace_updates(show_home):
    drain_session_events()
    session = st.session_state.live
    if session is not None and not session.running and running:
        st.rerun()
    if not show_home:
        return
    active = session is not None and session.running
    for message in st.session_state.errors[-5:]:
        st.error(message)
    if st.session_state.analysis_loading or st.session_state.pending_report is not None:
        label = "Loading new report…" if st.session_state.pending_report is not None else "Analyzing conversation…"
        st.markdown(f'<div class="report-loader" role="status" aria-live="polite"><span aria-hidden="true"></span>{label}</div>', unsafe_allow_html=True)
    result = st.session_state.latest
    if result is not None:
        completed = not active and st.session_state.pending_report is None and not st.session_state.analysis_loading
        with st.container(key="analysis_report"):
            with st.container(key="analysis_report_header"):
                heading, download = st.columns([4, 1], vertical_alignment="center")
                minutes, seconds = divmod(int(result["timestamp"]), 60)
                with heading:
                    panel_heading("Final report" if completed and not st.session_state.errors else "Conversation report", "chart", f"Checkpoint {result['checkpoint']} | {minutes}:{seconds:02d} of conversation", strong=True)
                if completed:
                    download.download_button(
                        "Download report", icon=":material/download:",
                        data=build_report_document(st.session_state.active_config, result, st.session_state.source_name),
                        file_name="CatchUpAI-report.html", mime="text/html", key="download_report",
                        on_click="ignore", help="Download a readable report. Open it in a browser to print or save as PDF.",
                    )
            with st.container(key="analysis_report_body"):
                if completed and st.session_state.errors:
                    st.caption("This download contains the latest valid report; the session ended with errors.")
                render_report(st.session_state.active_config["report"]["sections"], result["report"], st.session_state.active_config["conversation"].get("topics_to_cover", []))
    elif not active and st.session_state.pending_report is None:
        welcome_card()
    elif result is None and st.session_state.pending_report is None:
        st.caption("Your report will appear here once the complete recording has been analyzed." if st.session_state.source_name else "Your first report will appear here after the next analysis checkpoint.")


if page == "Conversation Studio":
    page_heading("Conversation Studio", "Listen closely. Keep what matters. Start recording or upload a conversation below.")
    recorder_controls()
    workspace_updates(True)
else:
    heading, preview_button = st.columns([4, 1], vertical_alignment="top")
    with heading:
        page_heading("Configurations", "Set up how your conversations are analyzed and define the structure of your reports.")
    with preview_button, st.container(key="preview_button"):
        open_preview = st.button("Preview Config", icon=":material/visibility:", use_container_width=True)
    # Reserve the toolbar position, then render it using this run's editor values.
    template_toolbar = st.container()
    left, right = st.columns(2, gap="small")
    with left, st.container(border=False, key="conversation_panel"):
        conversation = conversation_editor(st.session_state.draft_config["conversation"], disabled=running)
    with right, st.container(border=False, key="report_editor_panel"):
        report_definition = report_editor(disabled=running, tracking_topics=bool(conversation.get("topics_to_cover")))
    st.session_state.draft_config = {"conversation": conversation, "report": report_definition}
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
    if open_preview and not error:
        preview_config(copy.deepcopy(st.session_state.draft_config))
    workspace_updates(False)
