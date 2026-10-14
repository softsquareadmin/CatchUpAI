"""Durable local outbox for accepted reports and one complete audio object/session."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid
import wave

from .database import SupabaseStore, DatabaseError, canonical_id
from .config import OPENAI_SAMPLE_RATE

OUTBOX = Path(__file__).resolve().parent.parent / ".persistence"
_ACTIVE = {}
_ACTIVE_LOCK = threading.Lock()
_RESUME_LOCK = threading.Lock()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class SessionPersistence:
    def __init__(self, session_id, *, events=None, store=None, root=OUTBOX, resume=False):
        self.session_id = canonical_id(session_id)
        self.store = store or SupabaseStore()
        self.events = events
        self.directory = Path(root) / self.session_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.closed = False
        self.retired = False
        self.failure = None
        self.counter = max((int(p.stem) for p in self.directory.glob("*.json") if p.stem.isdigit()), default=0)
        self.metadata_path = self.directory / "session.json"
        if not resume:
            _atomic_json(self.metadata_path, {"id": self.session_id, "closed": False})
        else:
            self.closed = json.loads(self.metadata_path.read_text(encoding="utf-8")).get("closed", False)
            if not self.closed:
                self.closed = any(json.loads(p.read_text(encoding="utf-8")).get("kind") == "finish"
                                  for p in self.directory.glob("*.json") if p.stem.isdigit())
        self.thread = threading.Thread(target=self._run, daemon=True)

    @classmethod
    def create(cls, config, *, configuration=None, client_session_id=None, filename=None, events=None):
        store = SupabaseStore()
        store.check_schema()
        session_id = canonical_id(client_session_id) if client_session_id else str(uuid.uuid4())
        row = {"id": session_id, "configuration_snapshot": copy.deepcopy(config),
               "configuration_id": canonical_id(configuration["id"]) if configuration else None,
               "configuration_revision": configuration.get("revision") if configuration else None,
               "client_session_id": client_session_id,
               "title": filename or "Live recording " + utcnow(),
               "original_filename": filename or "audio.wav",
               "source_type": "upload" if filename else "live",
               "status": "processing" if filename else "recording"}
        store.create_session(row)
        writer = cls(session_id, events=events, store=store)
        writer.start()
        return writer

    def start(self):
        with _ACTIVE_LOCK:
            if self.session_id in _ACTIVE and not _ACTIVE[self.session_id].retired:
                return False
            _ACTIVE[self.session_id] = self
        self.thread.start()
        return True

    def _enqueue(self, kind, *, allow_closed=False, **values):
        with self.lock:
            if self.retired and allow_closed:
                return False
            if self.retired or self.closed and not allow_closed:
                raise OSError("Session saving has already been finalized.")
            self.counter += 1
            _atomic_json(self.directory / f"{self.counter:08d}.json", {"kind": kind, **values})
        self.wake.set()
        return True

    def cost(self, record):
        """Persist terminal response usage/gaps, including late repair responses."""
        event_type = record["event"]
        if event_type not in ("api_response", "usage_gap"):
            return
        identity = (record.get("api", "") + ":" + record.get("response_id", "") if event_type == "api_response"
                    else record["logged_at"] + ":" + record["reason"])
        usage = record.get("usage")
        def token(key):
            value = usage.get(key) if isinstance(usage, dict) else None
            return value if type(value) is int and value >= 0 else None
        row = {"id": str(uuid.uuid5(uuid.UUID(self.session_id), event_type + ":" + identity)),
               "audio_session_id": self.session_id, "event_type": event_type,
               "api": record.get("api"), "response_id": record.get("response_id"), "model": record.get("model"),
               "purpose": record.get("purpose"), "response_status": record.get("status"),
               "input_tokens": token("input_tokens"), "output_tokens": token("output_tokens"),
               "total_tokens": token("total_tokens"), "usage": copy.deepcopy(usage),
               "analysis": copy.deepcopy(record.get("analysis") or {}), "breakdown": copy.deepcopy(record.get("breakdown")),
               "cost_usd": record.get("cost_usd"), "rates_usd_per_million": copy.deepcopy(record.get("rates_usd_per_million")),
               "pricing_date": record.get("pricing_date"), "pricing_source": record.get("pricing_source"),
               "reason": record.get("reason"), "logged_at": record["logged_at"]}
        try:
            with _RESUME_LOCK:
                with _ACTIVE_LOCK:
                    writer = _ACTIVE.get(self.session_id, self)
                if writer._enqueue("cost", allow_closed=True, row=row):
                    return
                # A background repair can report paid usage after the recording worker
                # has finished. Start a cost-only outbox without changing ended_at/status.
                writer = SessionPersistence(self.session_id, store=self.store, events=self.events,
                                            root=self.directory.parent)
                writer._enqueue("cost", row=row)
                with writer.lock:
                    _atomic_json(writer.metadata_path, {"id": self.session_id, "closed": True})
                    writer.closed = True
                writer.start()
        except OSError:
            if self.events is not None:
                self.events.put(("error", {"kind": "local_storage"}))
            raise

    def report(self, result):
        metadata = {k: v for k, v in result.items() if k not in ("report", "generated_at")}
        self._enqueue("report", row={"id": str(uuid.uuid4()), "audio_session_id": self.session_id,
            "report_type": "final" if result.get("is_final") else "intermediate",
            "checkpoint": result["checkpoint"], "audio_position_seconds": result["timestamp"],
            "generated_at": result["generated_at"], "report": copy.deepcopy(result["report"]),
            "result_metadata": copy.deepcopy(metadata)})

    def archive_upload(self, data, filename, duration):
        from .database import audio_format
        extension, mime = audio_format(filename)
        path = self.directory / ("audio" + extension)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        self._enqueue("audio", filename=path.name, mime=mime, duration=duration)

    def archive_pcm(self, source, byte_count):
        path = self.directory / "audio.wav"
        temporary = self.directory / "audio.wav.tmp"
        source.seek(0)
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(OPENAI_SAMPLE_RATE)
            remaining = byte_count
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("Complete recording could not be assembled; use the browser download to recover it.")
                output.writeframesraw(chunk)
                remaining -= len(chunk)
        temporary.replace(path)
        self._enqueue("audio", filename=path.name, mime="audio/wav",
                      duration=byte_count / (OPENAI_SAMPLE_RATE * 2))

    def finish(self, *, cancelled=False, interrupted=False):
        with self.lock:
            self.counter += 1
            _atomic_json(self.directory / f"{self.counter:08d}.json", {"kind": "finish",
                         "cancelled": cancelled, "interrupted": interrupted, "ended_at": utcnow()})
            _atomic_json(self.metadata_path, {"id": self.session_id, "closed": True})
            self.closed = True
        self.wake.set()

    def _apply(self, task):
        if task["kind"] == "report":
            self.store.save_report(task["row"])
        elif task["kind"] == "cost":
            self.store.save_cost(task["row"])
        elif task["kind"] == "audio":
            self.store.patch("audio_sessions", {"audio_storage_status": "uploading"}, id="eq." + self.session_id)
            path, size = self.store.upload_audio(self.session_id, self.directory / task["filename"],
                                                task["filename"], task["mime"])
            self.store.patch("audio_sessions", {"audio_storage_status": "stored", "audio_path": path,
                "audio_size_bytes": size, "audio_mime_type": task["mime"], "duration_seconds": task["duration"]},
                id="eq." + self.session_id)
        elif task["kind"] == "finish":
            sessions = self.store.rows("audio_sessions", id="eq." + self.session_id)
            reports = self.store.rows("session_reports", audio_session_id="eq." + self.session_id)
            if not sessions:
                raise DatabaseError("Saved audio session no longer exists.")
            complete = sessions[0]["audio_storage_status"] == "stored" and any(r["report_type"] == "final" for r in reports)
            status = ("cancelled" if task["cancelled"] else "interrupted" if task["interrupted"] else
                      "completed" if complete else "completed_with_errors" if reports else "failed")
            self.store.patch("audio_sessions", {"status": status, "ended_at": task["ended_at"],
                "error_summary": None if complete else "The session ended without a saved final report or complete audio."},
                id="eq." + self.session_id)

    def _run(self):
        retries = {}
        try:
            while True:
                with self.lock:
                    tasks = sorted(p for p in self.directory.glob("*.json") if p.stem.isdigit())
                    closed = self.closed
                if not tasks:
                    if closed:
                        with self.lock:
                            if any(p.stem.isdigit() for p in self.directory.glob("*.json")):
                                continue
                            for path in self.directory.iterdir():
                                path.unlink()
                            self.directory.rmdir()
                            self.retired = True
                        if self.events is not None:
                            self.events.put(("saved", None))
                        return
                    self.wake.wait(1)
                    self.wake.clear()
                    continue
                pending = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in tasks]
                # Report saving must continue even when whole-audio archival is failing.
                work = [item for item in pending if item[1]["kind"] != "finish"] or pending
                work.sort(key=lambda item: (item[1]["kind"] not in ("report", "cost"), item[0].name))
                now = time.monotonic()
                ready = [item for item in work if retries.get(item[0].name, (0, 0))[1] <= now]
                if not ready:
                    delay = min(retries[item[0].name][1] - now for item in work)
                    self.wake.wait(max(0, min(delay, 60)))
                    self.wake.clear()
                    continue
                path, task = ready[0]
                try:
                    self._apply(task)
                    path.unlink()
                    retries.pop(path.name, None)
                    if not retries:
                        self.failure = None
                except (OSError, ValueError, KeyError) as error:
                    attempts = retries.get(path.name, (0, 0))[0] + 1
                    retries[path.name] = (attempts, time.monotonic() + self._retry_delay(attempts))
                    self.failure = str(error)
                    if self.events is not None and attempts == 1:
                        self.events.put(("error", {"kind": "persistence"}))
        finally:
            with _ACTIVE_LOCK:
                if _ACTIVE.get(self.session_id) is self:
                    _ACTIVE.pop(self.session_id, None)

    def _retry_delay(self, attempts):
        return min(2 ** min(attempts, 6), 60)


def resume_pending():
    """Replay closed sessions; abandoned recordings are marked interrupted."""
    with _RESUME_LOCK:
        _resume_pending()


def _resume_pending():
    if not OUTBOX.exists():
        return
    for directory in OUTBOX.iterdir():
        if not directory.is_dir() or not (directory / "session.json").exists():
            continue
        with _ACTIVE_LOCK:
            if directory.name in _ACTIVE:
                continue
        try:
            writer = SessionPersistence(directory.name, resume=True, root=OUTBOX)
            if not writer.closed:
                writer.finish(interrupted=True)
            writer.start()
        except (OSError, ValueError):
            continue
