"""Small playback-only Streamlit component for archived session audio."""
from pathlib import Path
from streamlit.components.v1 import declare_component

session_player = declare_component("session_player", path=str(
    Path(__file__).resolve().parent.parent / "assets" / "session_player"))
