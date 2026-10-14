import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import wave

from catchupai import database, templates
from catchupai.database import DatabaseError, SupabaseStore
from catchupai.models import default_config
from catchupai.persistence import SessionPersistence, resume_pending
from catchupai.usage import UsageTracker, saved_cost_snapshot


def report(final=False, checkpoint=1):
    return {"checkpoint": checkpoint, "timestamp": 60.25, "is_final": final,
            "generated_at": "2026-10-14T10:00:00+00:00", "report": {"summary": "Accepted findings"}}


class MemoryStore:
    def __init__(self):
        self.session = {"audio_storage_status": "pending"}
        self.reports = []
        self.objects = {}
        self.costs = []

    def rows(self, table, **filters):
        return [copy.deepcopy(self.session)] if table == "audio_sessions" else copy.deepcopy(self.reports)

    def patch(self, table, values, **filters):
        self.session.update(values)
        return [copy.deepcopy(self.session)]

    def save_report(self, row):
        self.reports.append(copy.deepcopy(row))

    def save_cost(self, row):
        if not any(existing["id"] == row["id"] for existing in self.costs):
            self.costs.append(copy.deepcopy(row))

    def upload_audio(self, session_id, path, filename, mime):
        name = session_id + "/" + filename
        self.objects[name] = Path(path).read_bytes()
        return name, len(self.objects[name])


class PersistenceTests(unittest.TestCase):
    def test_full_pcm_is_one_valid_wav_and_all_snapshots_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            pcm = b"\x01\x00" * 24000
            writer.report(report())
            writer.archive_pcm(io.BytesIO(pcm), len(pcm))
            writer.report(report(final=True))
            writer.finish()
            writer.start()
            writer.thread.join(3)
            self.assertFalse(writer.thread.is_alive())
            self.assertEqual([r["report_type"] for r in store.reports], ["intermediate", "final"])
            self.assertEqual(len(store.objects), 1)
            with wave.open(io.BytesIO(next(iter(store.objects.values()))), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 24000))
                self.assertEqual(audio.readframes(audio.getnframes()), pcm)
            self.assertEqual(store.session["status"], "completed")
            self.assertEqual(store.session["duration_seconds"], 1)
            self.assertFalse(writer.directory.exists())

    def test_audio_is_retained_when_final_analysis_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            writer.report(report())
            writer.archive_upload(b"original whole file", "recording.mp3", 15)
            writer.finish()
            writer.start()
            writer.thread.join(3)
            self.assertEqual(store.session["status"], "completed_with_errors")
            self.assertEqual(list(store.objects.values()), [b"original whole file"])

    def test_failed_report_write_retries_same_identity_before_finishing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            writer.report(report(final=True))
            writer.archive_upload(b"original", "recording.ogg", 5)
            writer.finish()
            calls = []
            def save(row):
                calls.append(row["id"])
                if len(calls) == 1:
                    raise DatabaseError("Temporary outage")
                store.reports.append(copy.deepcopy(row))
            with patch.object(store, "save_report", side_effect=save), patch.object(writer, "_retry_delay", return_value=0):
                writer.start()
                writer.thread.join(3)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(store.session["status"], "completed")

    def test_failed_audio_upload_does_not_block_new_final_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            writer.archive_upload(b"original", "recording.wav", 1)
            upload = store.upload_audio
            calls = []
            def archive(*args):
                calls.append(True)
                if len(calls) == 1:
                    writer.report(report(final=True))
                    writer.finish()
                    raise DatabaseError("Upload interrupted")
                self.assertEqual(store.reports[0]["report_type"], "final")
                return upload(*args)
            with patch.object(store, "upload_audio", side_effect=archive), patch.object(writer, "_retry_delay", return_value=0):
                writer.start()
                writer.thread.join(3)
            self.assertFalse(writer.thread.is_alive())
            self.assertEqual(store.session["status"], "completed")
            self.assertEqual(len(store.objects), 1)

    def test_restart_replays_closed_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            previous = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            previous.archive_upload(b"whole original", "recording.flac", 3)
            previous.report(report(final=True))
            previous.finish()
            with patch("catchupai.persistence.OUTBOX", Path(directory)), \
                 patch("catchupai.persistence.SupabaseStore", return_value=store):
                resume_pending()
                from catchupai.persistence import _ACTIVE, _ACTIVE_LOCK
                with _ACTIVE_LOCK:
                    resumed = _ACTIVE.get(previous.session_id)
                if resumed:
                    resumed.thread.join(3)
            self.assertEqual(store.session["status"], "completed")
            self.assertFalse(previous.directory.exists())

    def test_final_metadata_reconstructs_existing_report_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=MemoryStore())
            original = report(final=True)
            original.update(analysis_interval=60, interval_start=60, interval_end=120)
            writer.report(original)
            task = json.loads(next(writer.directory.glob("000*.json")).read_text())
            row = task["row"]
            self.assertEqual(row["result_metadata"]["interval_end"], 120)
            self.assertEqual(row["audio_position_seconds"], 60.25)
            self.assertEqual(row["report"], original["report"])
            self.assertNotIn("report", row["result_metadata"])


class ConfigurationTests(unittest.TestCase):
    def test_local_soft_delete_changes_only_deleted_at_and_keeps_name_reserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(templates, "TEMPLATE_DIRECTORY", Path(directory)), \
             patch.object(templates, "enabled", return_value=False):
            saved = templates.save_template("Example", default_config())
            path = Path(directory) / (saved["id"] + ".json")
            before = json.loads(path.read_text())
            templates.delete_template(saved["id"])
            after = json.loads(path.read_text())
            deleted_at = after.pop("deleted_at")
            self.assertEqual(after, before)
            templates.delete_template(saved["id"])
            self.assertEqual(json.loads(path.read_text())["deleted_at"], deleted_at)
            self.assertEqual(templates.list_templates(), ([], []))
            with self.assertRaises(ValueError):
                templates.load_template(saved["id"])
            with self.assertRaises(ValueError):
                templates.save_template("example", default_config())

    def test_remote_delete_updates_only_deleted_at_for_active_row(self):
        with patch.object(templates, "enabled", return_value=True), patch.object(templates, "SupabaseStore") as factory:
            identity = str(uuid.uuid4())
            templates.delete_template(identity)
            args, filters = factory.return_value.patch.call_args
            self.assertEqual(args[0], "configurations")
            self.assertEqual(set(args[1]), {"deleted_at"})
            self.assertEqual(filters, {"id": "eq." + identity, "deleted_at": "is.null"})

    def test_stale_editor_revision_does_not_overwrite_changes(self):
        with patch.object(templates, "enabled", return_value=True), patch.object(templates, "SupabaseStore") as factory, \
             patch.object(templates, "load_template", return_value={"revision": 4}):
            factory.return_value.patch.return_value = []
            identity = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, "Reload"):
                templates.save_template("Example", default_config(), identity, expected_revision=2)
            self.assertEqual(factory.return_value.patch.call_args.kwargs["revision"], "eq.2")


class RestTests(unittest.TestCase):
    def store(self):
        with patch.object(database.config, "SUPABASE_ENABLED", True), \
             patch.object(database.config, "SUPABASE_URL", "https://example.supabase.co"), \
             patch.object(database.config, "SUPABASE_SECRET_KEY", "sb_secret_test"):
            return SupabaseStore()

    def test_modern_secret_is_apikey_not_bearer(self):
        store = self.store()
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b"[]"
        with patch.object(database, "urlopen", return_value=Response()) as request:
            store.request("GET", "/rest/v1/configurations")
        headers = {k.lower(): v for k, v in request.call_args.args[0].header_items()}
        self.assertEqual(headers["apikey"], "sb_secret_test")
        self.assertNotIn("authorization", headers)

    def test_duplicate_report_cannot_silently_replace_saved_content(self):
        store = self.store()
        row = {"audio_session_id": str(uuid.uuid4()), "report_type": "final", "checkpoint": 1,
               "report": {"summary": "New value"}}
        with patch.object(store, "insert", return_value=[]), \
             patch.object(store, "rows", return_value=[{"report": {"summary": "Saved value"}}]):
            with self.assertRaisesRegex(DatabaseError, "different content"):
                store.save_report(row)

    def test_network_error_does_not_include_request_credentials(self):
        store = self.store()
        with patch.object(database, "urlopen", side_effect=OSError("sb_secret_test internal details")):
            with self.assertRaises(DatabaseError) as caught:
                store.request("GET", "/rest/v1/configurations")
        self.assertNotIn("sb_secret_test", str(caught.exception))


class CostPersistenceTests(unittest.TestCase):
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
             "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 2}}

    def test_responses_repairs_unknown_prices_and_gaps_are_saved_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            store = MemoryStore()
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            tracker = UsageTracker(session_id=writer.session_id, log_path=Path(directory)/"usage.jsonl", on_record=writer.cost)
            tracker.record("response-one", "gpt-5.4-mini", self.usage, api="responses", metadata={"checkpoint": 1})
            tracker.record("response-one", "gpt-5.4-mini", self.usage, api="responses")
            tracker.record("repair-one", "gpt-5.4-mini", self.usage, api="responses", metadata={"operation": "report_repair"})
            tracker.record("unpriced", "unknown-model", self.usage, api="responses")
            tracker.mark_gap("closed_before_response_usage")
            tracker.log_connection("reconnected")
            tracker.log_summary()
            writer.finish()
            writer.start()
            writer.thread.join(3)
            self.assertFalse(writer.thread.is_alive())
            self.assertEqual(len(store.costs), 4)  # Three responses plus one gap; no summaries/transport charges.
            snapshot = saved_cost_snapshot(writer.session_id, store.costs)
            self.assertEqual(snapshot["observed_cost_usd"], "0.0000600")
            self.assertEqual((snapshot["responses"], snapshot["unpriced_responses"], snapshot["usage_gaps"]), (3, 1, 1))
            self.assertFalse(snapshot["complete"])
            self.assertEqual(store.costs[0]["total_tokens"], 15)
            self.assertEqual(store.costs[1]["analysis"]["operation"], "report_repair")

    def test_late_repair_cost_is_saved_without_changing_session_end_time(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            store = MemoryStore()
            writer = SessionPersistence(str(uuid.uuid4()), root=directory, store=store)
            writer.finish()
            writer.start()
            writer.thread.join(3)
            original = copy.deepcopy(store.session)
            tracker = UsageTracker(session_id=writer.session_id, log_path=Path(directory)/"usage.jsonl", on_record=writer.cost)
            tracker.record("late-repair", "gpt-5.4-mini", self.usage, api="responses", metadata={"operation": "report_repair"})
            from catchupai.persistence import _ACTIVE, _ACTIVE_LOCK
            with _ACTIVE_LOCK:
                replacement = _ACTIVE.get(writer.session_id)
            if replacement:
                replacement.thread.join(3)
            self.assertEqual(len(store.costs), 1)
            self.assertEqual(store.session, original)
            self.assertFalse(writer.directory.exists())

    def test_sink_failure_does_not_interrupt_usage_accounting(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            def fail(record):
                raise OSError("Disk full")
            tracker = UsageTracker(log_path=Path(directory)/"usage.jsonl", on_record=fail)
            with self.assertLogs("catchupai.usage", level="WARNING"):
                tracker.record("paid", "gpt-5.4-mini", self.usage, api="responses")
            self.assertEqual(tracker.snapshot()["responses"], 1)
            self.assertTrue(tracker.snapshot()["complete"])

    def test_usage_still_reaches_outbox_when_local_usage_log_fails(self):
        captured = []
        tracker = UsageTracker(on_record=captured.append)
        with patch("pathlib.Path.open", side_effect=OSError("Log file unavailable")), \
             patch("builtins.print"), self.assertLogs("catchupai.usage", level="WARNING"):
            tracker.record("paid", "gpt-5.4-mini", self.usage, api="responses")
        self.assertEqual(captured[0]["response_id"], "paid")

    def test_no_historical_events_is_not_a_complete_zero_cost(self):
        snapshot = saved_cost_snapshot("old-session", [])
        self.assertFalse(snapshot["complete"])


if __name__ == "__main__":
    unittest.main()
