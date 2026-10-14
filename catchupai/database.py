"""Server-only Supabase REST/Storage access; never expose credentials to the UI."""
import json
from pathlib import Path
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import uuid

from . import config


class DatabaseError(OSError):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def enabled():
    return bool(config.SUPABASE_ENABLED and config.SUPABASE_URL and config.SUPABASE_SECRET_KEY)


def canonical_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid saved record ID.") from None


class SupabaseStore:
    def __init__(self):
        if not enabled():
            raise DatabaseError("Supabase URL and server secret key must be configured.")
        self.url = config.SUPABASE_URL
        self.key = config.SUPABASE_SECRET_KEY
        parsed = urlparse(self.url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment
                or parsed.path.strip("/")):
            raise DatabaseError("SUPABASE_URL must be the HTTPS project URL.")

    def request(self, method, path, *, params=None, body=None, headers=None, timeout=30, data=None):
        request_headers = {"apikey": self.key, "Accept": "application/json"}
        # Modern sb_secret keys are not JWTs. Legacy service-role JWTs need Bearer auth.
        if not self.key.startswith("sb_secret_"):
            request_headers["Authorization"] = "Bearer " + self.key
        if body is not None:
            data = json.dumps(body, allow_nan=False, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        target = self.url + path
        if params:
            target += "?" + urlencode(params)
        try:
            with urlopen(Request(target, data=data, headers=request_headers, method=method), timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            code = None
            try:
                code = json.loads(error.read()).get("code")
            except (ValueError, AttributeError):
                pass
            if error.code in (401, 403):
                message = "Supabase rejected the server credentials or access permissions."
            elif code == "23505":
                message = "A record with that name or identity already exists (deleted names remain reserved)."
            elif error.code == 404 or code in ("PGRST205", "42P01"):
                migration = ("202610140002_session_costs.sql" if path.endswith("/session_costs") else
                             "202610140001_audio_persistence.sql")
                message = f"Supabase setup is incomplete. Run supabase/migrations/{migration} in SQL Editor."
            elif error.code == 413:
                message = "Audio exceeds the Supabase Storage file-size limit. Adjust the project's Storage limit and retry."
            else:
                message = f"Supabase could not save or load this record (HTTP {error.code})."
            raise DatabaseError(message, error.code, code) from None
        except (URLError, TimeoutError, ConnectionError, OSError):
            raise DatabaseError("Could not reach Supabase. Pending session data remains in the local retry folder.") from None

    def rows(self, table, **filters):
        result, offset = [], 0
        while True:
            page = self.request("GET", "/rest/v1/" + table,
                                params={"select": "*", **filters, "limit": 500, "offset": offset})
            result.extend(page)
            if len(page) < 500:
                return result
            offset += len(page)

    def insert(self, table, row, conflict="id"):
        return self.request("POST", "/rest/v1/" + table, params={"on_conflict": conflict}, body=row,
                            headers={"Prefer": "resolution=ignore-duplicates,return=representation"})

    def patch(self, table, values, **filters):
        return self.request("PATCH", "/rest/v1/" + table, params=filters, body=values,
                            headers={"Prefer": "return=representation"})

    def check_schema(self):
        for table in ("configurations", "audio_sessions", "session_reports", "session_costs"):
            self.request("GET", "/rest/v1/" + table, params={"select": "id", "limit": 0})

    def create_session(self, row):
        inserted = self.insert("audio_sessions", row)
        saved = self.rows("audio_sessions", id="eq." + row["id"])
        if not saved or saved[0]["configuration_snapshot"] != row["configuration_snapshot"]:
            raise DatabaseError("Audio session identity conflicts with an existing recording.")
        if not inserted:
            raise DatabaseError("This recording already has a saved session. Open it under Recent Sessions or start a new recording.")

    def save_report(self, row):
        self.insert("session_reports", row, "audio_session_id,report_type,checkpoint")
        saved = self.rows("session_reports", audio_session_id="eq." + row["audio_session_id"],
                          report_type="eq." + row["report_type"], checkpoint="eq." + str(row["checkpoint"]))
        if not saved or saved[0]["report"] != row["report"]:
            raise DatabaseError("A saved checkpoint has different content. The new snapshot remains locally for review.")

    def save_cost(self, row):
        conflict = "api,response_id" if row["event_type"] == "api_response" else "id"
        self.insert("session_costs", row, conflict)
        filters = ({"api": "eq." + row["api"], "response_id": "eq." + row["response_id"]}
                   if row["event_type"] == "api_response" else {"id": "eq." + row["id"]})
        saved = self.rows("session_costs", **filters)
        if (not saved or saved[0]["audio_session_id"] != row["audio_session_id"] or
                saved[0]["usage"] != row.get("usage")):
            raise DatabaseError("Saved API usage conflicts with this session. The event remains locally for review.")

    def upload_audio(self, session_id, file_path, filename, mime_type):
        audio_path = session_id + "/audio" + Path(filename).suffix.lower()
        size = Path(file_path).stat().st_size
        # Stream one complete object from disk. HTTP transport buffers are not stored chunk objects.
        def content():
            with open(file_path, "rb") as source:
                while chunk := source.read(1024 * 1024):
                    yield chunk
        self.request("POST", "/storage/v1/object/session-audio/" + quote(audio_path, safe="/"),
                     data=content(), timeout=180, headers={"Content-Type": mime_type,
                     "Content-Length": str(size), "x-upsert": "true"})
        return audio_path, size

    def signed_audio_url(self, audio_path, expires_in=900):
        result = self.request("POST", "/storage/v1/object/sign/session-audio/" + quote(audio_path, safe="/"),
                              body={"expiresIn": expires_in})
        path = result["signedURL"]
        # Return only URLs belonging to this project.
        if path.startswith("/"):
            return self.url + "/storage/v1" + path
        raise DatabaseError("Supabase returned an unexpected audio download URL.")


def audio_format(filename):
    extension = Path(filename or "audio.wav").suffix.lower()
    types = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg"}
    if extension not in types:
        raise ValueError("Audio must be WAV, MP3, FLAC or OGG.")
    return extension, types[extension]
