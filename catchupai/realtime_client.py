import base64
import json
import threading
import time
import copy
from datetime import datetime, timezone

import numpy as np

from websocket import (
    create_connection,
    WebSocketTimeoutException,
    WebSocketException,
)

from .config import (
    OPENAI_API_KEY,
    OPENAI_SAMPLE_RATE,
    REALTIME_URL,
)
from .models import default_config, validate_config
from .prompts import CORE_SYSTEM_INSTRUCTION, build_analysis_instructions
from .report_schema import TOOL_NAME, build_analysis_tool, validate_report


class RealtimeConversationAnalyzer:
    def __init__(self, config=None, on_report=None, on_error=None, on_analysis=None, require_full_context=False):
        self.config = copy.deepcopy(config if config is not None else default_config())
        validate_config(self.config)
        self.analysis_instructions = build_analysis_instructions(self.config)
        self.analysis_tool = build_analysis_tool(self.config["report"], self.config["conversation"].get("topics_to_cover", []))
        self.on_report = on_report
        self.on_error = on_error
        self.on_analysis = on_analysis
        self.require_full_context = require_full_context
        self.requests = {}
        self.request_events = {}
        self.completed_responses = set()
        self.connection_failed = threading.Event()
        self.started_at = None
        self.ws = None
        self.stop_receiver = threading.Event()
        self.receiver_thread = None
        self.analysis_number = 0
        self.analysis_results = {}
        self.server_errors = []
        self.pending_responses = 0
        self.response_lock = threading.Lock()
        self.responses_finished = threading.Event()
        self.responses_finished.set()

    def connect(self):
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not found in .env")
        print("Connecting to OpenAI Realtime...")
        self.ws = create_connection(
            REALTIME_URL,
            header=[f"Authorization: Bearer {OPENAI_API_KEY}"],
            timeout=20,
            enable_multithread=True,
        )

        raw = self.ws.recv()
        event = json.loads(raw)
        event_type = event.get("type")

        print("First event:", event_type)

        if event_type == "error": raise RuntimeError(json.dumps(event, indent=2))
        if event_type != "session.created": raise RuntimeError(f"Expected session.created, "f"got {event_type}")
        self._configure_session()
        self._start_receiver()
        self.started_at = time.monotonic()

    def _configure_session(self):
        self.ws.send(
            json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    **({"truncation": "disabled"} if self.require_full_context else {}),
                    "instructions": CORE_SYSTEM_INSTRUCTION,
                    "output_modalities": ["text"],
                    "audio": {
                        "input": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": OPENAI_SAMPLE_RATE,
                            },
                            # We control commits manually.
                            "turn_detection": None,
                        }
                    }
                }
            })
        )

        while True:
            raw = self.ws.recv()
            event = json.loads(raw)
            event_type = event.get("type")

            if event_type == "session.updated":
                print("OpenAI Realtime session configured.")
                return

            if event_type == "error": raise RuntimeError(json.dumps(event, indent=2))

    def _start_receiver(self):
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True,)
        self.receiver_thread.start()
        print("Background receiver started.")

    def _receiver_loop(self):
        while not self.stop_receiver.is_set():
            try:
                raw = self.ws.recv()
                if not raw:
                    if not self.stop_receiver.is_set():
                        self._fail_connection("Connection lost. The latest completed report is available; restart recording to continue.")
                    break
                event = json.loads(raw)
                self._handle_event(event)

            except WebSocketTimeoutException:
                continue

            except Exception as error:
                if not self.stop_receiver.is_set():
                    self._fail_connection(f"Connection lost: {error}. The latest completed report is available; restart recording to continue.")
                break

    def _fail_connection(self, message):
        with self.response_lock:
            if self.connection_failed.is_set():
                return
            self.connection_failed.set()
            for result in self.analysis_results.values():
                if "result" not in result and "error" not in result:
                    result["error"] = "Connection lost before this report completed."
                    result["calls"].clear()
            self.requests.clear()
            self.request_events.clear()
            self.pending_responses = 0
            self.responses_finished.set()
        if self.on_analysis:
            self.on_analysis(False)
        self._record_error(message)

    def _send(self, event):
        if self.connection_failed.is_set():
            raise RuntimeError("Realtime connection is unavailable; restart the session.")
        try:
            self.ws.send(json.dumps(event))
        except (WebSocketException, OSError) as error:
            self._fail_connection(f"Connection lost: {error}. The latest completed report is available; restart recording to continue.")
            raise RuntimeError("Realtime connection is unavailable; restart the session.") from error

    def _record_error(self, message):
        event = {"type": "error", "error": {"message": message}}
        self.server_errors.append(event)
        print("\nANALYSIS ERROR:", message)
        if self.on_error:
            self.on_error(message)

    def _finish_request(self, purpose):
        with self.response_lock:
            if self.requests.pop(purpose, None) is not None:
                self.pending_responses = max(0, self.pending_responses - 1)
            self.request_events.pop(f"request_{purpose}", None)
            if self.pending_responses == 0:
                self.responses_finished.set()
        if self.on_analysis:
            self.on_analysis(self.pending_responses > 0)

    def _handle_event(self, event):
        event_type = event.get("type")
        if event_type == "input_audio_buffer.committed":
            print("\n[AUDIO COMMITTED]", event.get("item_id"))
        elif event_type == "response.created":
            response = event.get("response", {})
            purpose = (response.get("metadata") or {}).get("purpose")
            if purpose not in self.requests:
                return
            self.analysis_results[response["id"]] = {
                "purpose": purpose, "text": [], "calls": {},
                "metadata": dict(self.requests[purpose]),
            }
        elif event_type == "response.output_text.delta":
            result = self.analysis_results.get(event.get("response_id"))
            if result is not None:
                result["text"].append(event.get("delta", ""))
        elif event_type in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
            result = self.analysis_results.get(event.get("response_id"))
            if result is None:
                return
            call = result["calls"].setdefault(event.get("item_id") or event.get("call_id"), {"arguments": ""})
            if event_type.endswith(".delta"):
                call["arguments"] += event.get("delta", "")
            else:
                call.update(name=event.get("name"), arguments=event.get("arguments", call["arguments"]), complete=True)
        elif event_type == "response.done":
            response = event.get("response", {})
            response_id = response.get("id")
            result = self.analysis_results.get(response_id)
            if result is None or response_id in self.completed_responses:
                return
            self.completed_responses.add(response_id)
            try:
                if response.get("status") != "completed":
                    raise ValueError(f"Analysis response {response.get('status', 'unknown')}: {response.get('status_details')}")
                calls = [item for item in response.get("output", []) if item.get("type") == "function_call"]
                if not calls:
                    calls = [call for call in result["calls"].values() if call.get("complete")]
                if len(calls) != 1 or calls[0].get("name") != TOOL_NAME:
                    raise ValueError("Expected one completed update_conversation_report function call.")
                report = json.loads(calls[0]["arguments"])
                validate_report(report, self.analysis_tool["parameters"])
                structured = {
                    **result["metadata"],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "report": report,
                }
                result["result"] = structured
                print(json.dumps(structured, indent=2))
                if self.on_report:
                    self.on_report(structured)
            except (ValueError, TypeError, KeyError) as error:
                result["error"] = str(error)
                self._record_error(str(error))
            finally:
                result["calls"].clear()
                self._finish_request(result["purpose"])
        elif event_type == "error":
            # A rejected audio commit or other session error cannot yield a valid checkpoint.
            self._fail_connection((event.get("error") or {}).get("message", "Unknown OpenAI error"))

    def send_audio(self, audio_float32):
        audio_float32 = np.clip(audio_float32, -1.0, 1.0,)
        pcm16 = (audio_float32 * 32767).astype("<i2").tobytes()
        encoded = base64.b64encode(pcm16).decode("ascii")
        self._send({"type": "input_audio_buffer.append", "audio": encoded})

    def send_pcm(self, pcm16):
        """Forward browser-produced 24 kHz mono PCM without re-quantizing it."""
        self._send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm16).decode("ascii")})

    def commit_audio(self):
        self._send({"type": "input_audio_buffer.commit"})

    def request_analysis(self, timestamp=None, commit=True):
        self.analysis_number += 1
        purpose = (f"analysis_{self.analysis_number}")
        if commit:
            self.commit_audio()

        with self.response_lock:
            if self.connection_failed.is_set():
                raise RuntimeError("Realtime connection is unavailable; restart the session.")
            self.requests[purpose] = {
                "checkpoint": self.analysis_number,
                "timestamp": round(timestamp if timestamp is not None else time.monotonic() - self.started_at, 3),
            }
            self.request_events[f"request_{purpose}"] = purpose
            self.pending_responses += 1
            self.responses_finished.clear()

        if self.on_analysis:
            self.on_analysis(True)

        try:
            self._send({
                "event_id": f"request_{purpose}",
                "type": "response.create",
                "response": {
                    "conversation": "none",
                    "output_modalities": ["text"],
                    "metadata": {"purpose": purpose},
                    "instructions": self.analysis_instructions,
                    "tools": [self.analysis_tool],
                    "tool_choice": {"type": "function", "name": TOOL_NAME},
                }
            })
        except Exception:
            self._finish_request(purpose)
            raise

        print(f"\n>>> " f"{purpose.upper()} REQUESTED")

    def print_results(self):
        print("\n\n" "====================================")
        print("ALL CONVERSATION REPORTS")
        print("====================================")

        for result in list(self.analysis_results.values()):
            print("\n------------------------------------")
            print(result["purpose"].upper())
            print("------------------------------------\n")
            print(json.dumps(result.get("result", {"error": result.get("error", "No completed report")}), indent=2))

    def wait_for_responses(self, timeout=30, stop_event=None):
        if stop_event is None:
            return self.responses_finished.wait(timeout=timeout)
        deadline = time.monotonic() + timeout
        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self.responses_finished.wait(timeout=min(0.25, remaining)):
                return True
        return False

    def close(self):
        print("\nClosing Realtime connection...")
        self.stop_receiver.set()
        time.sleep(0.5)

        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

        if self.receiver_thread and self.receiver_thread is not threading.current_thread():
            self.receiver_thread.join(timeout=2)

        print("Realtime connection closed.")


# Compatibility for existing imports; new code uses the generic name.
RealtimeInterviewCopilot = RealtimeConversationAnalyzer
