import copy
import io
import json
import queue
import threading
import unittest
import urllib.error
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from catchupai.cost_estimation import analysis_groups, costs_csv, summarize_records
from catchupai.models import default_config
from catchupai.realtime_client import RealtimeConversationAnalyzer
from catchupai.report_repair import apply_repair_moves, has_repair_candidates, load_report_json, repair_report
from catchupai.report_schema import TOOL_NAME, build_report_schema, normalize_report_structure
from catchupai.session_notices import notice_text, session_notice
from catchupai.session import LiveSession
from catchupai.usage import UsageTracker


APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def config():
    result = default_config()
    result["conversation"]["topics_to_cover"] = [
        {"id": "topic_a", "label": "Experience", "criteria": ["Role", "Outcome"]},
        {"id": "topic_b", "label": "Teamwork", "criteria": ["Team role"]},
    ]
    result["report"]["sections"] = [{"id": "notes", "label": "Notes", "instruction": "Summarize.", "type": "text", "required": True}]
    return result


def report():
    return {"notes": "Worked on a release.", "topic_coverage": {
        "topic_a": {"criteria": {
            "0": {"status": "covered", "evidence": "I led the release.", "missing": ""},
            "1": {"status": "covered", "evidence": "We shipped on time.", "missing": ""},
        }, "asked": True, "summary": "Led a release that shipped on time.", "suggested_question": ""},
        "topic_b": {"criteria": {
            "0": {"status": "unanswered", "evidence": "", "missing": "The team role."},
        }, "asked": False, "summary": "", "suggested_question": "What was your role in the team?"},
    }}


def broken():
    result = report()
    topic = result["topic_coverage"]["topic_a"]
    topic["criteria"]["0"]["asked"] = topic.pop("asked")
    return result


def plan():
    return {"repairable": True, "moves": [{"from": "/topic_coverage/topic_a/criteria/0/asked", "path": "/topic_coverage/topic_a/asked"}],
            "reason": "Move asked back to its topic."}


def mini_response(move_plan=None, **changes):
    return {"id": "mini_response", "model": "gpt-5.4-mini", "status": "completed",
            "usage": {"input_tokens": 1000, "output_tokens": 100,
                      "input_tokens_details": {"cached_tokens": 200}, "output_tokens_details": {"reasoning_tokens": 0}},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(move_plan or plan())}]}], **changes}


class MovePreservationTests(unittest.TestCase):
    def setUp(self):
        configuration = config()
        self.schema = build_report_schema(configuration["report"]["sections"], configuration["conversation"]["topics_to_cover"])

    def test_deeply_misplaced_field_is_recovered_with_exact_values(self):
        original = broken()
        self.assertEqual(normalize_report_structure(original, self.schema), original)
        self.assertTrue(has_repair_candidates(original, self.schema))
        repaired = apply_repair_moves(original, plan(), self.schema)
        self.assertEqual(repaired, report())
        self.assertEqual(original, broken())

    def test_overwrites_renames_and_replacement_values_are_rejected(self):
        for defect in ("overwrite", "rename", "replace"):
            with self.subTest(defect=defect):
                original = broken()
                moves = plan()
                if defect == "overwrite":
                    original["topic_coverage"]["topic_a"]["asked"] = False
                elif defect == "rename":
                    moves["moves"][0]["path"] = "/topic_coverage/topic_a/summary"
                else:
                    moves["moves"][0]["value"] = False
                with self.assertRaises(Exception):
                    apply_repair_moves(original, moves, self.schema)

    def test_cross_topic_or_criterion_assignment_is_rejected(self):
        original = broken()
        del original["topic_coverage"]["topic_b"]["asked"]
        moves = plan()
        moves["moves"][0]["path"] = "/topic_coverage/topic_b/asked"
        with self.assertRaisesRegex(ValueError, "assignment"):
            apply_repair_moves(original, moves, self.schema)
        original = report()
        del original["topic_coverage"]["topic_a"]["criteria"]["1"]["evidence"]
        moves["moves"][0] = {"from": "/topic_coverage/topic_a/criteria/0/evidence", "path": "/topic_coverage/topic_a/criteria/1/evidence"}
        with self.assertRaisesRegex(ValueError, "assignment"):
            apply_repair_moves(original, moves, self.schema)

    def test_empty_extra_wrapper_is_pruned_without_dropping_data(self):
        original = report()
        original["wrapper"] = {"notes": original.pop("notes")}
        moves = {"repairable": True, "moves": [{"from": "/wrapper/notes", "path": "/notes"}], "reason": "Unwrap notes."}
        self.assertEqual(apply_repair_moves(original, moves, self.schema), report())

    def test_missing_data_and_semantic_errors_do_not_request_structural_repair(self):
        for defect in ("missing_topic", "missing_evidence", "missing_criterion"):
            original = report()
            if defect == "missing_topic":
                del original["topic_coverage"]["topic_b"]
            elif defect == "missing_evidence":
                original["topic_coverage"]["topic_a"]["criteria"]["0"]["evidence"] = ""
            else:
                del original["topic_coverage"]["topic_a"]["criteria"]["1"]
            self.assertFalse(has_repair_candidates(original, self.schema))

    def test_invalid_json_duplicate_keys_and_nonfinite_values_are_rejected(self):
        for text in ('{"topic_coverage":', '{"notes":"first","notes":"second"}', '{"notes":NaN}'):
            with self.assertRaises(ValueError):
                load_report_json(text)

    def test_bad_pointers_and_unbounded_plans_are_rejected(self):
        for source in ("topic_a", "/missing/asked", "/bad~2escape/asked"):
            moves = plan()
            moves["moves"][0]["from"] = source
            with self.assertRaises(ValueError):
                apply_repair_moves(broken(), moves, self.schema)
        moves = plan()
        moves["moves"] *= 129
        with self.assertRaises(ValueError):
            apply_repair_moves(broken(), moves, self.schema)


class RepairApiTests(unittest.TestCase):
    def setUp(self):
        configuration = config()
        self.schema = build_report_schema(configuration["report"]["sections"], configuration["conversation"]["topics_to_cover"])
        self.tracker = UsageTracker()
        self.metadata = {"checkpoint": 1, "timestamp": 60, "is_final": False}
        self.addCleanup(patch.stopall)
        patch("catchupai.report_repair.OPENAI_API_KEY", "test-key").start()
        patch.object(self.tracker, "_write").start()
        patch("builtins.print").start()

    def run_repair(self, response):
        with patch("catchupai.report_repair.urllib.request.urlopen", return_value=io.StringIO(json.dumps(response))) as api:
            repaired = repair_report(broken(), self.schema, "Invalid nesting", self.tracker,
                                     purpose="analysis_1", metadata=self.metadata)
            payload = json.loads(api.call_args[0][0].data)
        return repaired, payload

    def test_strict_move_request_and_cost_are_attached_to_current_interval(self):
        repaired, payload = self.run_repair(mini_response())
        self.assertEqual(repaired, report())
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertFalse(payload["store"])
        record = self.tracker.snapshot()["records"][0]
        self.assertEqual(record["api"], "responses")
        self.assertEqual(record["purpose"], "analysis_1")
        self.assertEqual(record["analysis"]["operation"], "report_repair")
        self.assertEqual(Decimal(record["cost_usd"]), Decimal("0.001065"))
        self.assertEqual(len(analysis_groups(self.tracker.snapshot()["records"])), 1)
        self.assertEqual(summarize_records(self.tracker.snapshot()["records"])["total"], Decimal("0.001065"))

    def test_paid_incomplete_refused_or_invalid_plans_are_still_charged(self):
        responses = [mini_response(status="incomplete"),
                     mini_response(output=[{"content": [{"type": "refusal", "refusal": "No"}]}]),
                     mini_response({"repairable": False, "moves": [], "reason": "Missing facts."})]
        for index, response in enumerate(responses):
            response["id"] = f"paid_{index}"
            with self.assertRaises(ValueError):
                self.run_repair(response)
        self.assertEqual(self.tracker.snapshot()["responses"], 3)

    def test_timeout_marks_unknown_usage_and_http_rejection_does_not(self):
        with patch("catchupai.report_repair.urllib.request.urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaises(ValueError):
                repair_report(broken(), self.schema, "Invalid", self.tracker, purpose="analysis_1", metadata=self.metadata)
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 1)
        error = urllib.error.HTTPError("https://api.openai.com/v1/responses", 429, "Quota", {}, None)
        with patch("catchupai.report_repair.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ValueError):
                repair_report(broken(), self.schema, "Invalid", self.tracker, purpose="analysis_1", metadata=self.metadata)
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 1)


class BackgroundRepairTests(unittest.TestCase):
    def setUp(self):
        self.tracker = UsageTracker()
        self.on_report, self.on_error = Mock(), Mock()
        self.addCleanup(patch.stopall)
        patch.object(self.tracker, "_write").start()
        patch("builtins.print").start()
        patch("catchupai.realtime_client.REPORT_REPAIR_ENABLED", True).start()
        self.client = RealtimeConversationAnalyzer(config(), on_report=self.on_report, on_error=self.on_error, usage_tracker=self.tracker)
        self.client.ws = Mock()
        self.client.request_analysis(timestamp=90, commit=False, is_final=True)

    def respond(self, raw=None, response_id="rt_1", status="completed"):
        self.client._handle_event({"type": "response.created", "response": {"id": response_id, "metadata": {"purpose": "analysis_1"}}})
        event = {"type": "response.done", "response": {
            "id": response_id, "metadata": {"purpose": "analysis_1"}, "status": status,
            "usage": {"input_tokens": 0, "output_tokens": 100},
            "output": [{"type": "function_call", "name": TOOL_NAME, "arguments": raw or json.dumps(broken())}],
        }}
        self.client._handle_event(event)
        return event

    def join_workers(self):
        for worker in self.client.repair_threads:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())

    def test_repair_does_not_block_audio_or_receiving_and_duplicate_usage_counts_once(self):
        entered, release = threading.Event(), threading.Event()
        def repair(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return apply_repair_moves(args[0], plan(), args[1])
        with patch("catchupai.realtime_client.repair_report", side_effect=repair) as fallback:
            event = self.respond()
            self.assertTrue(entered.wait(timeout=1))
            self.assertFalse(self.client.wait_for_responses(timeout=0.01))
            self.client.send_pcm(b"\x00\x00")
            self.client._handle_event(event)
            self.assertEqual(fallback.call_count, 1)
            release.set()
            self.assertTrue(self.client.wait_for_responses(timeout=2))
            self.join_workers()
        self.assertTrue(self.on_report.call_args[0][0]["is_final"])
        self.assertEqual(self.on_report.call_args[0][0]["report"], report())
        self.on_error.assert_not_called()
        self.assertEqual(self.tracker.snapshot()["responses"], 1)

    def test_failed_repair_uses_one_realtime_retry_and_does_not_repair_again(self):
        with patch("catchupai.realtime_client.repair_report", side_effect=TimeoutError("API timed out")) as fallback:
            self.respond()
            self.join_workers()
            self.assertEqual(self.client.ws.send.call_count, 2)
            self.assertEqual(self.client.pending_responses, 1)
            self.respond(response_id="rt_2")
            self.assertEqual(fallback.call_count, 1)
        self.on_report.assert_not_called()
        self.on_error.assert_called_once()
        notice = self.on_error.call_args[0][0]
        self.assertEqual(notice["kind"], "report")
        self.assertNotIn("Invalid report", str(notice))
        self.assertEqual(self.client.pending_responses, 0)

    def test_background_api_repair_includes_both_models_in_one_final_analysis_cost(self):
        with patch("catchupai.report_repair.OPENAI_API_KEY", "test-key"), patch(
                "catchupai.report_repair.urllib.request.urlopen", return_value=io.StringIO(json.dumps(mini_response()))):
            self.respond()
            self.assertTrue(self.client.wait_for_responses(timeout=2))
            self.join_workers()
        self.on_report.assert_called_once()
        self.on_error.assert_not_called()
        snapshot = self.tracker.snapshot()
        self.assertEqual(snapshot["responses"], 2)
        groups = analysis_groups(snapshot["records"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["label"], "Final report")
        self.assertEqual(groups[0]["summary"]["total"], Decimal("0.003465"))
        self.assertEqual(len(groups[0]["summary"]["categories"]["report"]["rates"]), 2)
        self.assertIn("Report repair calls", costs_csv(groups))

    def test_cancelled_or_disconnected_client_cannot_publish_late_repairs(self):
        for disconnected in (False, True):
            with self.subTest(disconnected=disconnected):
                self.setUp()
                entered, release = threading.Event(), threading.Event()
                def repair(*args, **kwargs):
                    entered.set()
                    release.wait(timeout=2)
                    return report()
                with patch("catchupai.realtime_client.repair_report", side_effect=repair):
                    self.respond()
                    self.assertTrue(entered.wait(timeout=1))
                    if disconnected:
                        self.client._fail_connection("Lost connection")
                    else:
                        self.client.stop_receiver.set()
                    release.set()
                    self.join_workers()
                self.on_report.assert_not_called()
                self.assertEqual(self.client.ws.send.call_count, 1)

    def test_unparseable_and_truncated_responses_skip_mini_and_retry(self):
        with patch("catchupai.realtime_client.repair_report") as fallback:
            self.respond(raw='{"topic_coverage":')
            fallback.assert_not_called()
            self.assertEqual(self.client.ws.send.call_count, 2)
            self.respond(response_id="rt_2", status="incomplete")
            self.assertEqual(self.client.pending_responses, 0)
        self.on_error.assert_called_once()

    def test_hard_deadline_retries_without_waiting_for_a_slow_network_call(self):
        entered, release, retried = threading.Event(), threading.Event(), threading.Event()
        def repair(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return report()
        self.client.ws.send.side_effect = lambda value: retried.set() if "request_analysis_1_retry" in value else None
        with patch("catchupai.realtime_client.REPORT_REPAIR_TIMEOUT", 0.03), patch("catchupai.realtime_client.repair_report", side_effect=repair):
            self.respond()
            self.assertTrue(entered.wait(timeout=1))
            self.assertTrue(retried.wait(timeout=1))
            self.assertEqual(self.client.ws.send.call_count, 2)
            self.respond(raw=json.dumps(report()), response_id="rt_2")
            release.set()
            self.join_workers()
        self.on_report.assert_called_once()
        self.on_error.assert_not_called()
        self.assertEqual(self.client.pending_responses, 0)
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 1)

    def test_disabled_repair_uses_existing_realtime_retry(self):
        with patch("catchupai.realtime_client.REPORT_REPAIR_ENABLED", False), patch("catchupai.realtime_client.repair_report") as fallback:
            self.respond()
            fallback.assert_not_called()
        self.assertEqual(self.client.ws.send.call_count, 2)

    def test_upload_cancellation_discards_late_reports_and_live_stop_keeps_final_reports(self):
        uploaded = LiveSession(config(), recording_bytes=b"audio")
        uploaded.stop()
        uploaded.recording_bytes = None
        uploaded._report({"is_final": True})
        self.assertTrue(uploaded.events.empty())
        live = LiveSession(config())
        live.stop()
        live._report({"is_final": True})
        self.assertEqual(live.events.get_nowait(), ("report", {"is_final": True}))


class FriendlyNoticeTests(unittest.TestCase):
    def test_raw_diagnostics_are_never_echoed_and_recording_context_is_accurate(self):
        raw = "Invalid report: topic_coverage.topic_abc.criteria: Additional properties are not allowed"
        text = notice_text(session_notice(raw), recording=True, has_report=True)
        self.assertIn("Recording continues", text)
        self.assertIn("last valid report", text)
        self.assertNotIn("topic_abc", text)
        self.assertNotIn("Additional properties", text)
        self.assertIn("uploaded audio", notice_text(session_notice(raw), uploaded=True))
        self.assertIn("API key", notice_text(session_notice("OPENAI_API_KEY missing")))
        self.assertIn("usage limits", notice_text(session_notice("HTTP 429 quota")))

    def test_ui_keeps_previous_report_and_clears_notice_on_recovery(self):
        events = queue.Queue()
        session = SimpleNamespace(running=True, events=events, usage_tracker=UsageTracker())
        app = AppTest.from_file(APP_PATH, default_timeout=45).run()
        app.session_state.live = session
        app.session_state.latest = {"checkpoint": 1, "timestamp": 60, "report": {}, "is_final": False}
        events.put(("error", "Invalid report: topic_coverage.internal_abc: Additional properties"))
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.warning), 0)
        self.assertTrue(any("Couldn't update" in item.value for item in app.toast))
        self.assertFalse(any("internal_abc" in item.value for item in app.toast))
        self.assertEqual(app.session_state.latest["checkpoint"], 1)
        events.put(("report", {"checkpoint": 2, "timestamp": 120, "report": {}, "is_final": False}))
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.errors, [])
        self.assertEqual(len(app.warning), 0)


if __name__ == "__main__":
    unittest.main()
