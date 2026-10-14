import copy
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from catchupai import templates

from catchupai.history import recent_sessions, ordered_reports
from catchupai.models import default_config
from catchupai.usage import calculate_cost, calculate_cost_breakdown

APP = Path(__file__).resolve().parent.parent / "app.py"
SESSION_ID = "00000000-0000-0000-0000-000000000001"
OTHER_ID = "00000000-0000-0000-0000-000000000002"


class FakeSessionStore:
    def __init__(self):
        config = default_config()
        config["conversation"]["purpose"] = "Historical configuration snapshot"
        self.sessions = [dict(id=identity, title=title, status="completed", source_type="upload",
            configuration_snapshot=copy.deepcopy(config), configuration_id=OTHER_ID,
            audio_storage_status="stored", audio_path=identity + "/audio.wav", duration_seconds=205,
            original_filename="old.wav", created_at="2026-10-14T10:00:00Z", started_at="2026-10-14T10:00:00Z")
            for identity, title in [(SESSION_ID, "Client interview"), (OTHER_ID, "Follow-up conversation")]]
        self.reports = [dict(id=identity, report_type=kind, audio_position_seconds=position, checkpoint=1,
            generated_at="2026-10-14T10:00:00Z", result_metadata={},
            report={"summary": summary, "important_facts": [summary], "questions_not_yet_asked": [],
                    "questions_needing_clarification": [], "recommended_next_action": "Review the findings"})
            for identity, kind, position, summary in [("early", "intermediate", 60, "Earlier findings"),
                                                     ("final", "final", 205, "Historical final findings")]]
        usage = {"input_tokens": 10, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 0}}
        self.costs = [{"event_type": "api_response", "response_id": "saved-response", "model": "gpt-5.4-mini",
            "api": "responses", "purpose": "analysis_1", "response_status": "completed", "usage": usage,
            "analysis": {"checkpoint": 1, "timestamp": 205, "is_final": True},
            "cost_usd": str(calculate_cost("gpt-5.4-mini", usage, "responses")),
            "breakdown": calculate_cost_breakdown("gpt-5.4-mini", usage, "responses"),
            "rates_usd_per_million": {"input": ".75", "cached": ".075", "output": "4.50"},
            "pricing_date": "2026-10-12",
            "logged_at": "2026-10-14T10:00:00Z"}]

    def request(self, method, path, **kwargs):
        if path.endswith("/configurations"):
            return [{"name": "Interview template"}]
        return [{k: row[k] for k in ("id", "title", "status", "created_at")} for row in self.sessions]

    def rows(self, table, **filters):
        if table == "configurations":
            return []
        if table == "audio_sessions":
            return [copy.deepcopy(row) for row in self.sessions if "eq." + row["id"] == filters["id"]]
        return copy.deepcopy(self.reports if table == "session_reports" else self.costs)

    def signed_audio_url(self, path, expires_in=900):
        return "https://example.invalid/private/" + path

    def patch(self, table, values, **filters):
        row = next(row for row in self.sessions if "eq." + row["id"] == filters["id"])
        row.update(values)
        return [copy.deepcopy(row)]


class SessionNavigationTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeSessionStore()
        recent_sessions.clear()
        for target, value in [("catchupai.database.enabled", lambda: True),
                              ("catchupai.templates.enabled", lambda: False),
                              ("catchupai.database.SupabaseStore", lambda: self.store),
                              ("catchupai.history.SupabaseStore", lambda: self.store),
                              ("catchupai.persistence.resume_pending", lambda: None)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(recent_sessions.clear)

    def app(self):
        return AppTest.from_file(APP, default_timeout=45).run()

    def content(self, app):
        return "\n".join(item.value for item in app.markdown)

    def test_recent_session_opens_studio_and_preserves_session_on_cost_tab(self):
        app = self.app()
        original_draft = copy.deepcopy(app.session_state.draft_config)
        self.assertFalse(any(button.label == "Saved Sessions" for button in app.button))
        app.button(key="open_session_" + SESSION_ID).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.page, "Conversation Studio")
        self.assertIn("Client interview", self.content(app))
        self.assertIn("Configuration used", self.content(app))
        self.assertIn("Interview template", self.content(app))
        self.assertNotIn("Record or Upload a Conversation", self.content(app))
        self.assertEqual(app.session_state.saved_report_id, "final")
        self.assertEqual(app.session_state.draft_config, original_draft)
        app.radio(key="report_navigation").set_value("section:summary").run()
        self.assertIn("Historical final findings", self.content(app))
        app.button(key="nav_payments").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.saved_session_id, SESSION_ID)
        self.assertIn("Client interview", self.content(app))
        self.assertIn("$0.000030", self.content(app))
        self.assertNotIn("Record or Upload a Conversation", self.content(app))
        app.button(key="nav_graphic_eq").click().run()
        self.assertIn("Historical final findings", self.content(app))

    def test_new_session_restores_recorder_and_clears_historical_costs(self):
        app = self.app()
        app.button(key="open_session_" + SESSION_ID).click().run()
        app.button(key="nav_payments").click().run()
        app.button(key="new_session").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.page, "Conversation Studio")
        self.assertIsNone(app.session_state.saved_session_id)
        self.assertIn("Record or Upload a Conversation", self.content(app))
        app.button(key="nav_payments").click().run()
        self.assertIn("Record or Upload a Conversation", self.content(app))
        self.assertNotIn("$0.000030", self.content(app))

    def test_intermediate_snapshot_and_second_session_selection(self):
        app = self.app()
        app.button(key="open_session_" + SESSION_ID).click().run()
        self.assertFalse(any(box.label == "Report snapshot" for box in app.selectbox))
        app.session_state.saved_report_id = "early"
        app.run()
        app.radio(key="report_navigation").set_value("section:summary").run()
        self.assertIn("Earlier findings", self.content(app))
        app.button(key="nav_payments").click().run()
        app.button(key="open_session_" + OTHER_ID).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.page, "Conversation Studio")
        self.assertEqual(app.session_state.saved_session_id, OTHER_ID)
        self.assertEqual(app.session_state.saved_report_id, "final")

    def test_navigation_away_from_active_recording_is_disabled(self):
        app = self.app()
        app.session_state.live = SimpleNamespace(running=True, events=queue.Queue())
        app.run()
        self.assertTrue(app.button(key="new_session").disabled)
        self.assertTrue(app.button(key="open_session_" + SESSION_ID).disabled)
        self.assertFalse(app.button(key="nav_payments").disabled)

    def test_missing_audio_reports_and_costs_keep_session_context(self):
        self.store.sessions[0].update(audio_storage_status="failed", audio_path=None, status="failed")
        self.store.reports = []
        self.store.costs = []
        app = self.app()
        app.button(key="open_session_" + SESSION_ID).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Complete audio is not available" in item.value for item in app.caption))
        app.button(key="nav_payments").click().run()
        self.assertTrue(any("No cost data" in item.value for item in app.info))
        self.assertNotIn("Record or Upload a Conversation", self.content(app))

    def test_final_report_wins_over_late_intermediate_position(self):
        rows = copy.deepcopy(self.store.reports)
        rows[0]["audio_position_seconds"] = 999
        self.assertEqual(ordered_reports(rows)[0]["report_type"], "final")

    def rename_menu(self, app, identity=SESSION_ID):
        return next(menu for menu in app.get("menu_button") if menu.key == "session_options_" + identity)

    def test_rename_is_only_menu_option_and_opens_a_dialog(self):
        app = self.app()
        menu = self.rename_menu(app)
        self.assertEqual(menu.label, ":material/more_horiz:")
        self.assertEqual(menu.options, ["Rename"])
        self.assertFalse(any(field.label == "Session name" for field in app.text_input))
        menu.click("Rename").run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.text_input(key="rename_session_input").value, "Client interview")
        app.button(key="cancel_session_rename").click().run()
        self.assertIsNone(app.session_state.rename_session_target)
        self.assertEqual(self.store.sessions[0]["title"], "Client interview")

    def test_rename_updates_sidebar_and_saved_header_without_changing_cost_page(self):
        app = self.app()
        app.button(key="open_session_" + SESSION_ID).click().run()
        app.button(key="nav_payments").click().run()
        self.rename_menu(app).click("Rename").run()
        app.text_input(key="rename_session_input").set_value("  Renamed interview  ").run()
        app.button(key="save_session_rename").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(self.store.sessions[0]["title"], "Renamed interview")
        self.assertEqual(app.session_state.page, "Cost Estimation")
        self.assertEqual(app.button(key="open_session_" + SESSION_ID).label, "Renamed interview")
        self.assertIn("Renamed interview", self.content(app))
        self.assertIsNone(app.session_state.rename_session_target)

    def test_empty_name_keeps_dialog_open_without_saving(self):
        app = self.app()
        self.rename_menu(app).click("Rename").run()
        app.text_input(key="rename_session_input").set_value("   ").run()
        app.button(key="save_session_rename").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.rename_session_target, SESSION_ID)
        self.assertIn("Enter a session name", self.content(app))
        self.assertEqual(self.store.sessions[0]["title"], "Client interview")


if __name__ == "__main__":
    unittest.main()
