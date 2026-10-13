import copy
import csv
import io
import queue
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from catchupai.cost_estimation import analysis_groups, costs_csv, summarize_records
from catchupai.models import default_config
from catchupai.realtime_client import RealtimeConversationAnalyzer
from catchupai.usage import UsageTracker, calculate_cost, calculate_cost_breakdown

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

def example_usage():
    return {
        "input_tokens": 3000, "output_tokens": 500,
        "input_token_details": {
            "text_tokens": 1000, "audio_tokens": 2000, "cached_tokens": 1200,
            "cached_tokens_details": {"text_tokens": 400, "audio_tokens": 800},
        },
        "output_token_details": {"text_tokens": 500, "audio_tokens": 0, "reasoning_tokens": 100},
    }


class CostAccountingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tracker = UsageTracker(log_path=Path(self.directory.name) / "usage.jsonl")
        self.addCleanup(patch.stopall)
        patch("builtins.print").start()

    def record(self, response_id, checkpoint=1, final=False, usage=None, model="gpt-realtime-2.1"):
        return self.tracker.record(response_id, model, example_usage() if usage is None else usage,
                                   purpose=f"analysis_{checkpoint}", status="completed",
                                   metadata={"checkpoint": checkpoint, "timestamp": checkpoint * 60, "is_final": final})

    def test_cached_and_reasoning_charges_are_disjoint_and_sum_to_total(self):
        breakdown = calculate_cost_breakdown("gpt-realtime-2.1", example_usage())
        expected = {
            "text_input": (600, "0.0024"), "text_cached": (400, "0.00016"),
            "audio_input": (1200, "0.0384"), "audio_cached": (800, "0.00032"),
            "report": (400, "0.0096"), "reasoning": (100, "0.0024"),
        }
        for key, (tokens, cost) in expected.items():
            self.assertEqual(breakdown[key]["tokens"], tokens)
            self.assertEqual(Decimal(breakdown[key]["cost_usd"]), Decimal(cost))
        self.assertEqual(calculate_cost("gpt-realtime-2.1", example_usage()), Decimal("0.05328"))

    def test_missing_or_invalid_reasoning_split_preserves_total(self):
        for reasoning in (None, -1, 501, True):
            usage = example_usage()
            usage["output_token_details"]["reasoning_tokens"] = reasoning
            breakdown = calculate_cost_breakdown("gpt-realtime-2.1", usage)
            self.assertIsNone(breakdown["reasoning"]["tokens"])
            self.assertEqual(breakdown["report"]["tokens"], 500)
            self.assertEqual(calculate_cost("gpt-realtime-2.1", usage), Decimal("0.05328"))

    def test_audio_and_images_remain_accounted_for(self):
        usage = example_usage()
        usage["input_tokens"] += 10
        usage["input_token_details"]["image_tokens"] = 10
        usage["output_tokens"] += 20
        usage["output_token_details"]["audio_tokens"] = 20
        breakdown = calculate_cost_breakdown("gpt-realtime-2.1", usage)
        self.assertEqual(breakdown["image_input"]["tokens"], 10)
        self.assertEqual(breakdown["audio_output"]["tokens"], 20)
        self.assertEqual(calculate_cost("gpt-realtime-2.1", usage), Decimal("0.05461"))

    def test_responses_api_cost_remains_compatible(self):
        usage = {"input_tokens": 1000, "output_tokens": 500,
                 "input_tokens_details": {"cached_tokens": 200},
                 "output_tokens_details": {"reasoning_tokens": 100}}
        self.assertEqual(calculate_cost("gpt-5.4-mini", usage, "responses"), Decimal("0.002865"))

    def test_retries_and_final_analysis_group_without_duplicate_charges(self):
        self.record("retry")
        self.record("success")
        self.record("success")
        self.record("final", checkpoint=2, final=True)
        records = self.tracker.snapshot()["records"]
        groups = analysis_groups(records)
        self.assertEqual([group["label"] for group in groups], ["Interval 1", "Final report"])
        self.assertEqual(groups[0]["summary"]["total"], Decimal("0.10656"))
        self.assertEqual(groups[0]["summary"]["responses"], 2)
        summary = summarize_records(records)
        self.assertEqual(summary["total"], Decimal("0.15984"))
        self.assertEqual(summary["savings"], Decimal("0.08016"))
        self.assertEqual(sum(item["cost"] for item in summary["categories"].values()), summary["total"])
        upload = analysis_groups(records, uploaded=True)
        self.assertEqual(len(upload), 1)
        self.assertEqual(upload[0]["summary"]["total"], summary["total"])

    def test_snapshot_is_isolated_and_unknown_usage_marks_partial_total(self):
        self.record("known")
        self.record("unknown", model="unknown-model")
        self.tracker.mark_gap("connection_lost")
        snapshot = self.tracker.snapshot()
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["unpriced_responses"], 1)
        self.assertEqual(snapshot["usage_gaps"], 1)
        snapshot["records"][0]["breakdown"]["audio_input"]["tokens"] = 0
        self.assertEqual(self.tracker.snapshot()["records"][0]["breakdown"]["audio_input"]["tokens"], 1200)
        summary = summarize_records(snapshot["records"])
        self.assertEqual(summary["total"], Decimal("0.05328"))
        self.assertEqual(summary["unpriced"], 1)

    def test_inconsistent_cache_details_are_unpriceable(self):
        usage = example_usage()
        usage["input_token_details"]["cached_tokens"] = 1100
        self.assertIsNone(calculate_cost("gpt-realtime-2.1", usage))

    def test_csv_keeps_upload_single_and_marks_unreported_reasoning(self):
        usage = example_usage()
        del usage["output_token_details"]["reasoning_tokens"]
        self.record("upload", final=True, usage=usage)
        rows = list(csv.DictReader(io.StringIO(costs_csv(analysis_groups(self.tracker.snapshot()["records"], uploaded=True)))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0]["Observed total (USD)"]), Decimal("0.05328"))
        self.assertEqual(rows[0]["Reasoning (USD)"], "")
        self.assertEqual(rows[0]["Reasoning included in report generation"], "True")

    def test_terminal_response_retains_analysis_metadata_even_on_failure(self):
        client = RealtimeConversationAnalyzer(default_config(), usage_tracker=self.tracker)
        metadata = {"checkpoint": 3, "timestamp": 183, "is_final": True}
        client.requests["analysis_3"] = metadata
        client._handle_event({"type": "response.done", "response": {
            "id": "failed-response", "metadata": {"purpose": "analysis_3"},
            "status": "failed", "usage": example_usage(),
        }})
        record = self.tracker.snapshot()["records"][0]
        self.assertEqual(record["analysis"], metadata)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["cost_usd"], "0.05328")


class CostPageTests(unittest.TestCase):
    def test_empty_page_and_navigation(self):
        app = AppTest.from_file(APP_PATH, default_timeout=45).run()
        self.assertEqual(len(app.exception), 0)
        app.button(key="nav_payments").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.page, "Cost Estimation")
        self.assertTrue(any("Your audio costs will appear here" in item.value for item in app.markdown))
        app.button(key="nav_settings").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state.page, "Configurations")

    def test_active_session_navigation_preserves_recorder_and_shows_interval_costs(self):
        tracker = UsageTracker()
        with patch.object(tracker, "_write"), patch("builtins.print"):
            tracker.record("sample", "gpt-realtime-2.1", example_usage(), purpose="analysis_1", status="completed",
                           metadata={"checkpoint": 1, "timestamp": 60, "is_final": False})
        session = SimpleNamespace(running=True, events=queue.Queue(), usage_tracker=tracker)
        app = AppTest.from_file(APP_PATH, default_timeout=45).run()
        app.session_state.live = session
        app.session_state.active_config = copy.deepcopy(app.session_state.draft_config)
        app.run()
        recorder_id = app.get("component_instance")[0].proto.id
        self.assertTrue(app.button(key="nav_settings").disabled)
        self.assertFalse(app.button(key="nav_payments").disabled)
        app.button(key="nav_payments").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.get("component_instance")[0].proto.id, recorder_id)
        self.assertTrue(any("$0.053280" in item.value for item in app.markdown))
        self.assertTrue(any("Interval 1" in item.value for item in app.markdown))
        app.selectbox(key=f"cost_scope_{tracker.session_id}").select("interval_1").run()
        self.assertEqual(len(app.exception), 0)
        app.button(key="nav_graphic_eq").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.get("component_instance")[0].proto.id, recorder_id)

    def test_final_cost_refresh_and_reset_clear_session_costs(self):
        tracker = UsageTracker()
        with patch.object(tracker, "_write"), patch("builtins.print"):
            tracker.record("interval", "gpt-realtime-2.1", example_usage(), purpose="analysis_1", status="completed",
                           metadata={"checkpoint": 1, "timestamp": 60, "is_final": False})
        session = SimpleNamespace(running=True, events=queue.Queue(), usage_tracker=tracker)
        app = AppTest.from_file(APP_PATH, default_timeout=45).run()
        app.session_state.live = session
        app.button(key="nav_payments").click().run()
        self.assertEqual(len(app.exception), 0)
        with patch.object(tracker, "_write"), patch("builtins.print"):
            tracker.record("final", "gpt-realtime-2.1", example_usage(), purpose="analysis_2", status="completed",
                           metadata={"checkpoint": 2, "timestamp": 82, "is_final": True})
        session.running = False
        session.events.put(("report", {"checkpoint": 2, "timestamp": 82, "is_final": True, "report": {}}))
        session.events.put(("finished", None))
        app.run()
        self.assertEqual(len(app.exception), 0)
        content = "\n".join(item.value for item in app.markdown)
        self.assertIn("Final estimated cost", content)
        self.assertIn("$0.106560", content)
        self.assertIn("Final report", content)
        app.button(key="reset_workspace").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertIsNone(app.session_state.live)
        content = "\n".join(item.value for item in app.markdown)
        self.assertIn("Your audio costs will appear here", content)
        self.assertNotIn("$0.106560", content)

    def test_unknown_usage_shows_partial_estimate_and_unavailable_amounts(self):
        tracker = UsageTracker()
        with patch.object(tracker, "_write"), patch("builtins.print"):
            tracker.record("unknown", "unknown-model", None, purpose="analysis_1", status="failed",
                           metadata={"checkpoint": 1, "timestamp": 60, "is_final": True})
            tracker.mark_gap("connection_lost")
        app = AppTest.from_file(APP_PATH, default_timeout=45).run()
        app.session_state.live = SimpleNamespace(running=False, events=queue.Queue(), usage_tracker=tracker)
        app.button(key="nav_payments").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.warning), 0)
        self.assertTrue(any("known costs only" in item.value for item in app.toast))
        self.assertEqual(app.toast[0].proto.duration, 0)
        content = "\n".join(item.value for item in app.markdown)
        self.assertIn("Partial estimate", content)
        self.assertIn("Unavailable", content)
        self.assertNotIn("Final estimated cost", content)

    def test_uploaded_audio_has_one_row_and_no_interval_picker(self):
        tracker = UsageTracker()
        with patch.object(tracker, "_write"), patch("builtins.print"):
            for response_id in ("retry", "success"):
                tracker.record(response_id, "gpt-realtime-2.1", example_usage(), purpose="analysis_1", status="completed",
                               metadata={"checkpoint": 1, "timestamp": 91, "is_final": True})
        app = AppTest.from_file(APP_PATH, default_timeout=45).run()
        app.session_state.live = SimpleNamespace(running=False, events=queue.Queue(), usage_tracker=tracker)
        app.session_state.source_name = '<test & recording>.wav'
        app.session_state.latest = {"checkpoint": 1, "timestamp": 91, "is_final": True, "report": {}}
        app.button(key="nav_payments").click().run()
        self.assertEqual(len(app.exception), 0)
        content = "\n".join(item.value for item in app.markdown)
        self.assertIn("$0.106560", content)
        self.assertIn("&lt;test &amp; recording&gt;.wav", content)
        self.assertIn("Uploaded audio", content)
        self.assertIn("Final estimated cost", content)
        self.assertNotIn("Running total", content)
        self.assertFalse(any(box.key.startswith("cost_scope_") for box in app.selectbox))


if __name__ == "__main__":
    unittest.main()
