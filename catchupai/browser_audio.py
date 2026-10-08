"""Streamlit bridge and resilient worker for browser-owned microphone audio."""

import base64
import copy
from pathlib import Path
import queue
import tempfile
import threading
import time

from streamlit.components.v1 import declare_component

from .config import ANALYSIS_INTERVAL, OPENAI_SAMPLE_RATE
from .models import validate_config
from .realtime_client import RealtimeConversationAnalyzer


browser_recorder = declare_component("browser_recorder", path=str(Path(__file__).resolve().parent.parent / "assets" / "browser_recorder"))


class BrowserAudioSession:
    """Acknowledged input survives OpenAI failures; each new connection replays it."""

    def __init__(self, config, session_id, client_factory=RealtimeConversationAnalyzer):
        validate_config(config)
        self.config = copy.deepcopy(config)
        self.analysis_interval = self.config.get("analysis_interval", ANALYSIS_INTERVAL)
        self.session_id = session_id
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.input_finished = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.audio = tempfile.TemporaryFile()
        self.offsets = []
        self.byte_count = 0
        self.highest_sequence = -1
        self.boundaries = []
        self.checkpoint = 0
        self.client_factory = client_factory
        self.thread = threading.Thread(target=self._run, daemon=True)

    @property
    def running(self):
        return self.thread.is_alive()

    def start(self):
        self.thread.start()

    def stop(self):
        # The browser sends its tail before input_finished is set.
        self.stop_event.set()
        self.wake.set()

    def cancel(self):
        self.cancel_event.set()
        self.stop_event.set()
        self.wake.set()

    def ingest(self, message):
        if self.cancel_event.is_set():
            return
        if message.get("session_id") != self.session_id:
            raise ValueError("Audio belongs to a different recording.")
        with self.lock:
            for chunk in message.get("chunks", []):
                sequence = chunk["sequence"]
                if type(sequence) is not int or sequence < 0:
                    raise ValueError("Invalid audio sequence.")
                if sequence <= self.highest_sequence:
                    continue
                if self.input_finished.is_set():
                    raise ValueError("Recording input is already complete.")
                if sequence != self.highest_sequence + 1:
                    raise ValueError("Audio segment missing; waiting for browser retransmission.")
                pcm = base64.b64decode(chunk["audio"], validate=True)
                if not pcm or len(pcm) % 2 or len(pcm) > OPENAI_SAMPLE_RATE * 2 * 2:
                    raise ValueError("Invalid PCM audio segment.")
                self.audio.seek(self.byte_count)
                self.audio.write(pcm)
                self.offsets.append((self.byte_count, len(pcm)))
                self.byte_count += len(pcm)
                self.highest_sequence = sequence
            if message.get("stopped") and message.get("last_sequence") == self.highest_sequence:
                self.input_finished.set()
                self.stop_event.set()
        self.wake.set()

    def _snapshot(self):
        with self.lock:
            return len(self.offsets), self.byte_count / (OPENAI_SAMPLE_RATE * 2)

    def _chunk(self, index):
        with self.lock:
            offset, length = self.offsets[index]
            self.audio.seek(offset)
            return self.audio.read(length)

    def _report(self, report):
        if not self.cancel_event.is_set():
            self.events.put(("report", report))

    def _new_client(self):
        def on_error(message):
            if client.connection_failed.is_set():
                self.events.put(("status", "Reconnecting — recording is saved in your browser"))
            else:
                self.events.put(("error", message))

        client = self.client_factory(
            self.config, on_report=self._report,
            on_error=on_error,
            on_analysis=lambda active: self.events.put(("analysis", active)),
        )
        return client

    def _run(self):
        client = None
        sent = committed = 0
        analyzed_at = 0.0
        attempts = 0
        final_deadline = None
        recovered = False
        try:
            while not self.cancel_event.is_set():
                if self.input_finished.is_set() and final_deadline is None:
                    final_deadline = time.monotonic() + 90
                if final_deadline is not None and time.monotonic() >= final_deadline:
                    self.events.put(("error", "Could not finish analysis. Your recording is still saved in the browser; download it and upload it later."))
                    break
                try:
                    if client is None:
                        self.events.put(("status", "Connecting" if attempts == 0 else "Reconnecting — recording is saved in your browser"))
                        client = self._new_client()
                        client.analysis_number = self.checkpoint
                        client.connect()
                        sent = committed = 0
                        self.events.put(("status", "Restoring conversation" if attempts else "Listening"))
                        # Recreate previous conversation items without generating old reports.
                        for boundary in self.boundaries:
                            while sent < boundary and not self.cancel_event.is_set():
                                client.send_pcm(self._chunk(sent))
                                sent += 1
                            if self.cancel_event.is_set():
                                break
                            client.commit_audio()
                            committed = sent
                        recovered = attempts > 0
                    if client.connection_failed.is_set():
                        raise ConnectionError("Realtime connection lost")
                    count, elapsed = self._snapshot()
                    # Bound each pass so recording input and stop handling remain responsive.
                    target = min(count, sent + 50)
                    while sent < target and not self.cancel_event.is_set():
                        client.send_pcm(self._chunk(sent))
                        sent += 1
                    if self.cancel_event.is_set():
                        break
                    caught_up = sent == count
                    ready = not client.pending_responses
                    final = self.input_finished.is_set() and caught_up
                    due = elapsed - analyzed_at >= self.analysis_interval
                    if ready and caught_up and count and (due or recovered or final):
                        self.checkpoint += 1
                        client.analysis_number = self.checkpoint - 1
                        client.request_analysis(timestamp=elapsed, commit=sent > committed)
                        if sent > committed:
                            self.boundaries.append(sent)
                            committed = sent
                        analyzed_at = elapsed
                        recovered = False
                        self.events.put(("status", "Finishing report" if final else "Listening"))
                        if final:
                            client.wait_for_responses(timeout=30, stop_event=self.cancel_event)
                            if self.cancel_event.is_set():
                                break
                            if client.connection_failed.is_set():
                                raise ConnectionError("Connection lost during final report")
                            if client.pending_responses:
                                raise TimeoutError("Final report did not complete")
                            break
                    elif final and not count:
                        break
                    self.wake.wait(0.2)
                    self.wake.clear()
                except Exception as error:
                    if self.cancel_event.is_set():
                        break
                    if client is not None:
                        self.checkpoint = max(self.checkpoint, client.analysis_number)
                        client.close()
                        client = None
                    attempts += 1
                    self.events.put(("analysis", False))
                    self.events.put(("status", f"Reconnecting (attempt {attempts}) — recording continues"))
                    # Missing credentials cannot recover; capture still runs in the browser.
                    if "OPENAI_API_KEY" in str(error) and attempts == 1:
                        self.events.put(("error", "OPENAI_API_KEY is missing. Recording remains saved in the browser while analysis retries."))
                    # Incoming audio must not shorten retry backoff.
                    self.cancel_event.wait(min(2 ** min(attempts, 4), 15))
        finally:
            if client is not None:
                client.close()
            with self.lock:
                self.audio.close()
            self.events.put(("analysis", False))
            self.events.put(("finished", None))
