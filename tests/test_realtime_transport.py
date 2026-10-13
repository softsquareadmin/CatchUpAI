import json
import queue
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from websocket import ABNF, WebSocketTimeoutException

from catchupai.models import default_config
from catchupai.realtime_client import RealtimeConversationAnalyzer
from catchupai.usage import UsageTracker


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tracker = UsageTracker()
        self.log = []
        self.addCleanup(patch.stopall)
        patch.object(self.tracker, "_write", side_effect=self.log.append).start()
        patch("builtins.print").start()
        self.error = Mock()
        self.client = RealtimeConversationAnalyzer(default_config(), on_error=self.error, usage_tracker=self.tracker)
        self.client.ws = Mock()
        self.client.connection_id = "test-connection"

    def test_peer_close_records_code_reason_and_unavailable_pending_usage_once(self):
        self.client.request_analysis(timestamp=240, commit=False, checkpoint=4)
        self.client.ws.recv_data_frame.return_value = (
            ABNF.OPCODE_CLOSE, SimpleNamespace(data=struct.pack("!H", 1011) + b"keepalive ping timeout"))
        self.client._receiver_loop()
        self.client._fail_connection("Duplicate failure")
        disconnected = next(row for row in self.log if row.get("kind") == "disconnected")
        self.assertEqual(disconnected["close_code"], 1011)
        self.assertEqual(disconnected["close_reason"], "keepalive ping timeout")
        self.assertEqual(disconnected["pending_responses"], 1)
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 1)
        self.error.assert_called_once()

    def test_heartbeat_checks_matching_pong_and_times_out_only_when_unanswered(self):
        self.client._heartbeat_tick(10)
        self.client.ws.ping.assert_called_once()
        payload = self.client.pending_ping[0]
        def receive(**kwargs):
            self.client.stop_receiver.set()
            return ABNF.OPCODE_PONG, SimpleNamespace(data=payload)
        self.client.ws.recv_data_frame.side_effect = receive
        self.client._receiver_loop()
        self.assertIsNone(self.client.pending_ping)
        self.client.stop_receiver.clear()
        self.client.request_analysis(timestamp=60, commit=False, checkpoint=1)
        self.client._heartbeat_tick(20)
        self.client._heartbeat_tick(64)
        self.assertFalse(self.client.connection_failed.is_set())
        self.client._heartbeat_tick(65)
        self.assertTrue(self.client.connection_failed.is_set())
        self.assertEqual(next(row for row in self.log if row.get("kind") == "disconnected")["reason"], "heartbeat_timeout")

    def test_wrong_pong_does_not_confirm_heartbeat(self):
        self.client._heartbeat_tick(10)
        def receive(**kwargs):
            self.client.stop_receiver.set()
            return ABNF.OPCODE_PONG, SimpleNamespace(data=b"unrelated-pong")
        self.client.ws.recv_data_frame.side_effect = receive
        self.client._receiver_loop()
        self.assertIsNotNone(self.client.pending_ping)

    def test_request_rejection_preserves_socket_and_releases_the_request(self):
        self.client.request_analysis(timestamp=60, commit=False, checkpoint=1)
        self.client._handle_event({"type": "error", "error": {
            "event_id": "request_analysis_1", "code": "invalid_request_error", "message": "Request rejected"}})
        self.assertFalse(self.client.connection_failed.is_set())
        self.assertEqual(self.client.pending_responses, 0)
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 0)
        self.error.assert_called_once()

    def test_rejected_retry_is_also_associated_with_its_request(self):
        self.client.request_analysis(timestamp=60, commit=False, checkpoint=1)
        self.client._send_analysis_request("analysis_1", validation_error="Bad report")
        self.client._handle_event({"type": "error", "error": {
            "event_id": "request_analysis_1_retry", "code": "rate_limit_exceeded", "message": "Rate limit reached"}})
        self.assertFalse(self.client.connection_failed.is_set())
        self.assertEqual(self.client.pending_responses, 0)
        self.assertEqual(self.client.request_events, {})

    def test_local_handler_failure_does_not_become_a_connection_failure(self):
        self.client.request_analysis(timestamp=60, commit=False, checkpoint=1)
        self.client.event_queue.put({"type": "response.done", "response": {"metadata": {"purpose": "analysis_1"}}})
        self.client.event_queue.put({"type": "next"})
        def handle(event):
            if event["type"] == "next":
                self.client.stop_receiver.set()
            else:
                raise ValueError("Local callback failed")
        with patch.object(self.client, "_handle_event", side_effect=handle):
            self.client._event_loop()
        self.assertFalse(self.client.connection_failed.is_set())
        self.assertEqual(self.client.pending_responses, 0)
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 0)

    def test_transport_receives_pong_while_report_processing_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        frames = queue.Queue()
        def receive(**kwargs):
            try:
                return frames.get(timeout=0.05)
            except queue.Empty:
                raise WebSocketTimeoutException()
        def handle(event):
            entered.set()
            release.wait(timeout=2)
        self.client.ws.recv_data_frame.side_effect = receive
        with patch.object(self.client, "_handle_event", side_effect=handle):
            self.client._start_receiver()
            try:
                frames.put((ABNF.OPCODE_TEXT, SimpleNamespace(data=json.dumps({"type": "response.done"}))))
                self.assertTrue(entered.wait(timeout=1))
                self.client._heartbeat_tick(10)
                frames.put((ABNF.OPCODE_PONG, SimpleNamespace(data=self.client.pending_ping[0])))
                deadline = time.monotonic() + 1
                while self.client.pending_ping is not None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNone(self.client.pending_ping)
                self.assertFalse(self.client.connection_failed.is_set())
            finally:
                release.set()
                self.client.close()
        self.assertFalse(any(worker.is_alive() for worker in (
            self.client.receiver_thread, self.client.event_thread, self.client.heartbeat_thread)))

    def test_close_has_one_reader_and_shutdown_runs_even_if_close_frame_fails(self):
        self.client.ws.send_close.side_effect = OSError("Socket already closed")
        self.client.close()
        self.client.ws.close.assert_not_called()
        self.client.ws.shutdown.assert_called_once()
        self.assertFalse(self.client.connection_failed.is_set())

    def test_connection_loss_with_no_pending_response_does_not_invent_missing_charges(self):
        self.client._fail_connection("Lost between reports")
        self.assertEqual(self.tracker.snapshot()["usage_gaps"], 0)
        self.assertTrue(self.tracker.snapshot()["complete"])

    def test_queued_terminal_usage_is_saved_after_shutdown_without_publishing_a_report(self):
        self.client.request_analysis(timestamp=240, commit=False, checkpoint=4)
        metadata = dict(self.client.requests["analysis_1"])
        self.client._fail_connection("Lost after response arrived")
        self.client.stop_receiver.set()
        self.client.event_queue.put({"type": "response.done", "response": {
            "id": "queued-response", "metadata": {"purpose": "analysis_1"}, "status": "completed",
            "usage": {"input_tokens": 0, "output_tokens": 1}}})
        self.client._event_loop()
        record = self.tracker.snapshot()["records"][0]
        self.assertEqual(record["analysis"], metadata)
        self.assertEqual(record["cost_usd"], "0.000024")


if __name__ == "__main__":
    unittest.main()
