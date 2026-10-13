import queue
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from catchupai.notifications import notify_once
from catchupai.usage import UsageTracker


class NotificationTests(unittest.TestCase):
    def test_same_notice_is_emitted_once_and_stays_until_dismissed(self):
        streamlit = SimpleNamespace(session_state={}, toast=Mock())
        with patch("catchupai.notifications.st", streamlit):
            self.assertTrue(notify_once("Cost is incomplete", key="cost", scope="recording-1"))
            self.assertFalse(notify_once("Cost is incomplete", key="cost", scope="recording-1"))
        streamlit.toast.assert_called_once_with("Cost is incomplete", icon=":material/warning:", duration="infinite")

    def test_new_recording_and_distinct_notices_can_warn_again(self):
        streamlit = SimpleNamespace(session_state={}, toast=Mock())
        with patch("catchupai.notifications.st", streamlit):
            notify_once("Cost is incomplete", key="cost", scope="recording-1")
            self.assertTrue(notify_once("Report unavailable", key="report", scope="recording-1"))
            self.assertTrue(notify_once("Cost is incomplete", key="cost", scope="recording-2"))
        self.assertEqual(streamlit.toast.call_count, 3)

    def test_cost_warning_does_not_reopen_on_refresh_or_navigation(self):
        def session():
            tracker = UsageTracker()
            with patch.object(tracker, "_write"):
                tracker.mark_gap("connection_lost")
            return SimpleNamespace(running=False, events=queue.Queue(), usage_tracker=tracker)

        app_path = Path(__file__).resolve().parent.parent / "app.py"
        app = AppTest.from_file(app_path, default_timeout=45).run()
        app.session_state.live = session()
        with patch("catchupai.notifications.st.toast", wraps=st.toast) as toast:
            app.button(key="nav_payments").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.warning), 0)
            self.assertEqual(len(app.error), 0)
            self.assertEqual(toast.call_count, 1)
            app.run()
            app.button(key="nav_graphic_eq").click().run()
            app.button(key="nav_payments").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(toast.call_count, 1)
            app.session_state.live = session()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(toast.call_count, 2)


if __name__ == "__main__":
    unittest.main()
