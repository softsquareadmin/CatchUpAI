import queue
import time
import copy
import threading

import numpy as np
try:
    import sounddevice as sd
except (ImportError, OSError):
    # Hosted browser capture and uploaded recordings do not need PortAudio.
    sd = None

from scipy.signal import resample_poly
from .config import (OPENAI_SAMPLE_RATE, CHUNK_MS, ANALYSIS_INTERVAL,)
from .realtime_client import RealtimeConversationAnalyzer
from .models import default_config, validate_config

audio_queue = queue.Queue()
microphone_lock = threading.Lock()

def microphone_callback(indata, frames, time_info, status,):
    if status:
        print("\nMIC STATUS:", status,)

    # sounddevice reuses its buffer,
    # therefore copy the data.
    
    audio_queue.put(indata[:, 0].copy())

def main(config=None, stop_event=None, on_report=None, on_error=None, on_status=None, on_analysis=None):
    config = copy.deepcopy(config if config is not None else default_config())
    validate_config(config)
    stop_event = stop_event if stop_event is not None else threading.Event()
    status = on_status or (lambda message: None)
    if not microphone_lock.acquire(blocking=False):
        raise RuntimeError("The microphone is already in use by another live session.")
    try:
        # Remove leftover chunks only while this session owns the microphone.
        while True:
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break
        return _run_audio_session(config, stop_event, on_report, on_error, status, on_analysis)
    finally:
        microphone_lock.release()


def _run_audio_session(config, stop_event, on_report, on_error, status, on_analysis):
    analysis_interval = config.get("analysis_interval", ANALYSIS_INTERVAL)
    if sd is None:
        raise RuntimeError("Server microphone capture requires sounddevice and PortAudio. Use the browser recorder in app.py instead.")
    input_device = sd.query_devices(kind="input")
    mic_sample_rate = int(input_device["default_samplerate"])
    mic_block_size = int(mic_sample_rate * CHUNK_MS / 1000)

    print("\n====================================")
    print("REALTIME CONVERSATION ANALYSIS")
    print("====================================")
    print("\nMicrophone:", input_device["name"],)
    print("Microphone sample rate:", mic_sample_rate,)
    print("OpenAI sample rate:", OPENAI_SAMPLE_RATE,)
    print("Chunk size:", CHUNK_MS, "ms",)
    print("Analysis interval:", analysis_interval,"seconds",)

    copilot = RealtimeConversationAnalyzer(config, on_report=on_report, on_error=on_error, on_analysis=on_analysis)
    audio_since_commit = False
    start_time = None
    
    try:
        status("Connecting")
        copilot.connect()
        if stop_event.is_set():
            return
        start_time = time.monotonic()
        last_analysis_time = (start_time)
        last_progress_second = -1
        audio_since_commit = False
        print("\n====================================")
        print("LIVE CONVERSATION STARTED")
        print("Press Ctrl+C to stop.")
        print("====================================\n")
        
        with sd.InputStream(
            samplerate=mic_sample_rate,
            channels=1,
            dtype="float32",
            blocksize=mic_block_size,
            callback=microphone_callback,
        ):
            status("Listening")

            while not stop_event.is_set():
                if copilot.connection_failed.is_set():
                    raise RuntimeError("Realtime connection failed; stop and restart the session.")
                try:
                    chunk = audio_queue.get(timeout=1)
                except queue.Empty:
                    continue

                if (mic_sample_rate != OPENAI_SAMPLE_RATE):
                    chunk = resample_poly(chunk, OPENAI_SAMPLE_RATE,mic_sample_rate,)

                chunk = np.asarray(chunk, dtype=np.float32,)
                copilot.send_audio(chunk)
                audio_since_commit = True
                now = time.monotonic()
                elapsed = (now - start_time)

                if (now - last_analysis_time >= analysis_interval):
                    print("\n\n" "************************************")
                    print(f"CHECKPOINT: " f"{elapsed:.0f} seconds")
                    print("************************************")

                    if audio_since_commit:
                        copilot.request_analysis(timestamp=elapsed)
                        audio_since_commit = False

                    last_analysis_time = now

                elapsed_second = int(elapsed)

                if (elapsed_second > 0 and elapsed_second % 10 == 0 and elapsed_second != last_progress_second):
                    print(f"\n[MIC LIVE: " f"{elapsed_second}s]")
                    last_progress_second = (elapsed_second)

    except KeyboardInterrupt:
        print("\n\n====================================")
        print("CONVERSATION STOPPED")
        print("====================================")

    except Exception as error:
        print("\nAPPLICATION ERROR:")
        print(repr(error))
        if on_error and not copilot.connection_failed.is_set():
            on_error(str(error))
        audio_since_commit = False

    finally:
        status("Stopping")
        if audio_since_commit and not copilot.connection_failed.is_set():
            try:
                print("\nRequesting final analysis...")
                copilot.request_analysis(timestamp=time.monotonic() - start_time)
            except Exception as error:
                if on_error and not copilot.connection_failed.is_set():
                    on_error(f"Final analysis request failed: {error}")
        if copilot.pending_responses and not copilot.connection_failed.is_set():
            copilot.wait_for_responses(timeout=30)
            if copilot.pending_responses and not copilot.connection_failed.is_set() and on_error:
                on_error("Timed out waiting for the final report.")
        copilot.print_results()
        print("\nServer errors:", len(copilot.server_errors),)

        if copilot.server_errors:
            for error in copilot.server_errors:
                print(error)
        copilot.close()
        status("Stopped")


class LiveSession:
    """Streamlit reruns retain this worker; workers never access Streamlit state."""

    def __init__(self, config, recording_bytes=None):
        validate_config(config)
        self.config = copy.deepcopy(config)
        self.recording_bytes = recording_bytes
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    @property
    def running(self):
        return self.thread.is_alive()

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        try:
            runner = main
            extra = {}
            if self.recording_bytes is not None:
                from .recording import analyze_recording
                runner = analyze_recording
                extra["recording_bytes"] = self.recording_bytes
            runner(
                self.config,
                stop_event=self.stop_event,
                on_report=lambda result: self.events.put(("report", result)),
                on_error=lambda message: self.events.put(("error", message)),
                on_status=lambda message: self.events.put(("status", message)),
                on_analysis=lambda loading: self.events.put(("analysis", loading)),
                **extra,
            )
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.recording_bytes = None
            self.events.put(("finished", None))

if __name__ == "__main__":
    main()
