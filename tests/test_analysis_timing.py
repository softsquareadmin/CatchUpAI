import copy
import queue
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from catchupai.analysis_timing import audio_checkpoint
from catchupai.browser_audio import BrowserAudioSession
from catchupai.cost_estimation import analysis_groups, costs_csv, _history_html
from catchupai.models import default_config
from catchupai.realtime_client import RealtimeConversationAnalyzer
from catchupai.usage import UsageTracker


class TimingTests(unittest.TestCase):
    def test_number_comes_from_elapsed_audio_and_configured_interval(self):
        for seconds, expected in ((0, 0), (8.4, 0), (59.9, 0), (60, 1), (180, 3), (264.4, 4), (300, 5)):
            self.assertEqual(audio_checkpoint(seconds, 60), expected)
        self.assertEqual(audio_checkpoint(91, 30), 3)
        self.assertEqual(audio_checkpoint(65, 60, is_final=True), 2)
        self.assertEqual(audio_checkpoint(60, 60, is_final=True), 1)

    def test_reconnection_attempts_keep_same_interval_and_next_request_stays_on_boundary(self):
        session = BrowserAudioSession(default_config(), "timing-test")
        session.checkpoint = session.completed_checkpoint = session.request_number = 3
        session.boundaries = [30, 60, 90]
        state = {"elapsed": 240.0}
        requests, clients = [], []
        class Client:
            def __init__(self, config, on_report, **kwargs):
                self.connection_failed = threading.Event()
                self.pending_responses = 0
                self.has_intermediate_report = True
                self.report = on_report
                self.index = len(clients)
                clients.append(self)
            def connect(self):
                state["elapsed"] = (240, 250, 264.4)[self.index]
            def send_pcm(self, chunk):
                pass
            def commit_audio(self):
                pass
            def request_analysis(self, timestamp, commit, is_final, checkpoint):
                self.analysis_number += 1
                requests.append((checkpoint, timestamp, is_final, self.analysis_number))
                if self.index < 2:
                    raise ConnectionError("Disconnected during request")
                self.report({"checkpoint": checkpoint, "timestamp": timestamp, "is_final": is_final, "report": {}})
                if checkpoint == 4:
                    state["elapsed"] = 300
                elif checkpoint == 5:
                    state["elapsed"] = 373.2
                    session.input_finished.set()
            def wait_for_responses(self, **kwargs):
                return True
            def close(self):
                pass
        session.client_factory = Client
        with patch.object(session, "_snapshot", side_effect=lambda: (int(state["elapsed"] / 2), state["elapsed"])), \
             patch.object(session, "_chunk", return_value=b"\x00\x00"), \
             patch.object(session.cancel_event, "wait", return_value=False), \
             patch.object(session.wake, "wait", return_value=False):
            session._run()
        self.assertEqual([row[0] for row in requests], [4, 4, 4, 5, 7])
        self.assertEqual([row[3] for row in requests], [4, 5, 6, 7, 8])
        self.assertEqual(requests[3][1], 300)
        self.assertTrue(requests[-1][2])

    def test_reconnecting_before_first_minute_does_not_generate_an_early_report(self):
        session = BrowserAudioSession(default_config(), "early-test")
        state = {"elapsed": 8.4, "connections": 0}
        requests = []
        class Client:
            pending_responses = 0
            has_intermediate_report = True
            connection_failed = threading.Event()
            def __init__(self, config, on_report, **kwargs):
                self.report = on_report
            def connect(self):
                state["connections"] += 1
                if state["connections"] == 1:
                    raise ConnectionError("Connect failed")
            def send_pcm(self, chunk):
                pass
            def commit_audio(self):
                pass
            def request_analysis(self, timestamp, commit, is_final, checkpoint):
                self.analysis_number += 1
                requests.append((timestamp, checkpoint, is_final))
                self.report({"checkpoint": checkpoint, "timestamp": timestamp, "is_final": is_final, "report": {}})
                state["elapsed"] = 65
                session.input_finished.set()
            def wait_for_responses(self, **kwargs):
                return True
            def close(self):
                pass
        session.client_factory = Client
        def refresh(*args):
            if state["elapsed"] == 8.4:
                state["elapsed"] = 60
        with patch.object(session, "_snapshot", side_effect=lambda: (int(state["elapsed"] / 2), state["elapsed"])), \
             patch.object(session, "_chunk", return_value=b"\x00\x00"), \
             patch.object(session.cancel_event, "wait", return_value=False), \
             patch.object(session.wake, "wait", side_effect=refresh):
            session._run()
        self.assertEqual(requests, [(60, 1, False), (65, 2, True)])

    def test_reconnection_responses_group_by_time_and_final_stays_separate(self):
        tracker = UsageTracker()
        client = RealtimeConversationAnalyzer(default_config(), usage_tracker=tracker)
        client.ws = Mock()
        client.analysis_number = 5
        with patch.object(tracker, "_write"), patch("builtins.print"):
            for position in (240, 250, 264.4):
                client.request_analysis(timestamp=position, commit=False, checkpoint=4)
                purpose = f"analysis_{client.analysis_number}"
                tracker.record(purpose, "gpt-realtime-2.1", {"input_tokens": 0, "output_tokens": 1},
                               purpose=purpose, metadata=client.requests[purpose])
            client.request_analysis(timestamp=265, commit=False, checkpoint=5, is_final=True)
            purpose = f"analysis_{client.analysis_number}"
            tracker.record(purpose, "gpt-realtime-2.1", {"input_tokens": 0, "output_tokens": 1},
                           purpose=purpose, metadata=client.requests[purpose])
        groups = analysis_groups(tracker.snapshot()["records"])
        self.assertEqual([group["label"] for group in groups], ["Interval 4", "Final report"])
        self.assertEqual(groups[0]["summary"]["responses"], 3)
        self.assertEqual(groups[0]["summary"]["total"], Decimal("0.000072"))
        self.assertIn("3:00 – 4:00", _history_html(groups, uploaded=False))
        self.assertIn("Interval 4", costs_csv(groups))
        self.assertEqual(len(analysis_groups(tracker.snapshot()["records"], uploaded=True)), 1)

    def test_later_report_in_same_interval_and_final_at_same_time_replace_previous_report(self):
        events = queue.Queue()
        session = SimpleNamespace(running=False, events=events, usage_tracker=UsageTracker())
        app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=45).run()
        app.session_state.live = session
        original = {"checkpoint": 4, "timestamp": 240, "report": {}, "is_final": False}
        app.session_state.latest = copy.deepcopy(original)
        events.put(("report", {**original, "timestamp": 264.4}))
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.pending_report["timestamp"], 264.4)
        events.put(("report", {**original, "timestamp": 264.4, "is_final": True}))
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.session_state.pending_report["is_final"])
        events.put(("report", original))
        app.run()
        self.assertTrue(app.session_state.pending_report["is_final"])

    def test_existing_counter_based_records_are_displayed_using_their_audio_times(self):
        tracker = UsageTracker()
        with patch.object(tracker, "_write"), patch("builtins.print"):
            for counter, position in ((3, 180.6), (6, 264.4), (7, 324.6)):
                tracker.record(f"response_{counter}", "gpt-realtime-2.1", {"input_tokens": 0, "output_tokens": 1},
                               purpose=f"analysis_{counter}", metadata={"checkpoint": counter, "timestamp": position, "is_final": False})
        snapshot = tracker.snapshot()
        groups = analysis_groups(snapshot["records"], analysis_interval=60)
        self.assertEqual([group["label"] for group in groups], ["Interval 3", "Interval 4", "Interval 5"])
        self.assertEqual(snapshot["records"][1]["analysis"]["checkpoint"], 6)


if __name__ == "__main__":
    unittest.main()
