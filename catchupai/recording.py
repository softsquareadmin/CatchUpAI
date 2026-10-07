"""Feed uploaded recordings through the existing Realtime client; no mic changes."""

import io

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import CHUNK_MS, OPENAI_SAMPLE_RATE
from .realtime_client import RealtimeConversationAnalyzer


def recording_info(recording_bytes):
    try:
        info = sf.info(io.BytesIO(recording_bytes))
    except (sf.LibsndfileError, ValueError) as error:
        raise ValueError("This recording could not be read. Choose a valid WAV, MP3, FLAC or OGG file.") from error
    if info.duration < 0.1:
        raise ValueError("The recording must contain at least 0.1 seconds of audio.")
    return info


def analyze_recording(config, recording_bytes, stop_event, on_report=None, on_error=None, on_status=None, on_analysis=None):
    recording_info(recording_bytes)
    status = on_status or (lambda message: None)
    client = RealtimeConversationAnalyzer(config, on_report, on_error, on_analysis, require_full_context=True)
    elapsed = 0.0
    try:
        status("Connecting")
        client.connect()
        if stop_event.is_set():
            status("Cancelled")
            return
        with sf.SoundFile(io.BytesIO(recording_bytes)) as source:
            status("Processing recording")
            block_size = max(1, int(source.samplerate * CHUNK_MS / 1000))
            frames_sent = 0
            while True:
                if stop_event.is_set():
                    status("Cancelled")
                    return
                if client.connection_failed.is_set():
                    raise RuntimeError("Realtime connection failed; restart the recording analysis.")
                audio = source.read(block_size, dtype="float32", always_2d=True)
                if not len(audio):
                    break
                # Downmix stereo/multichannel uploads; retain the microphone path as-is.
                mono = audio.mean(axis=1)
                if source.samplerate != OPENAI_SAMPLE_RATE:
                    mono = resample_poly(mono, OPENAI_SAMPLE_RATE, source.samplerate)
                client.send_audio(np.asarray(mono, dtype=np.float32))
                frames_sent += len(audio)
                elapsed = frames_sent / source.samplerate
                # Transport chunks are not analysis checkpoints. Send without playback delays.
        if stop_event.is_set():
            status("Cancelled")
            return
        status("Finishing report")
        if client.connection_failed.is_set():
            raise RuntimeError("Realtime connection failed; restart the recording analysis.")
        # One commit and one analysis after the entire file has arrived.
        client.request_analysis(timestamp=elapsed)
        finished = client.wait_for_responses(timeout=120, stop_event=stop_event)
        if stop_event.is_set():
            status("Cancelled")
            return
        if client.connection_failed.is_set():
            raise RuntimeError("Realtime connection failed while analyzing the recording.")
        if not finished:
            raise TimeoutError("Timed out waiting for the complete recording report.")
    finally:
        client.close()
        status("Stopped")
