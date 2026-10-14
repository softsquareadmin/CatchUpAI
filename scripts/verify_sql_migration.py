"""Exercise migration invariants in an isolated temporary local PostgreSQL cluster."""
from pathlib import Path
import os
import shutil
import socket
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    executable = shutil.which("psql")
    if not executable:
        raise SystemExit("Install PostgreSQL and put psql/initdb/pg_ctl on PATH to run local SQL checks.")
    binaries = Path(executable).parent
    suffix = ".exe" if os.name == "nt" else ""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="catchupai-sql-") as directory:
        root = Path(directory).resolve()
        if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("catchupai-sql-"):
            raise RuntimeError("Unexpected temporary database path.")
        cluster = root / "data"
        def run(args, **kwargs):
            # Windows server children inherit pipe handles; log to a file so
            # starting postgres cannot leave communicate() waiting for pipe EOF.
            with (root / "command.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(args, stdout=log, stderr=log, text=True,
                                        creationflags=flags, timeout=60, **kwargs)
            output = (root / "command.log").read_text(encoding="utf-8", errors="replace")
            if result.returncode:
                raise RuntimeError(output)
            return output
        run([str(binaries / ("initdb" + suffix)), "-D", str(cluster), "-U", "postgres", "-A", "trust",
             "--no-locale", "-E", "UTF8"])
        started = False
        control = str(binaries / ("pg_ctl" + suffix))
        try:
            run([control, "-D", str(cluster), "-l", str(root / "server.log"), "-o",
                 f"-h 127.0.0.1 -p {port}", "-w", "start"])
            started = True
            command = [executable, "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-d", "postgres",
                       "-v", "ON_ERROR_STOP=1"]
            run(command, input="create role anon; create role authenticated; create role service_role bypassrls; "
                               "create schema storage; create table storage.buckets(id text primary key, name text, public boolean);")
            for migration in sorted((ROOT / "supabase/migrations").glob("*.sql")):
                run(command + ["-f", str(migration)])
                run(command + ["-f", str(migration)])
            for checks in sorted((ROOT / "supabase/tests").glob("*.sql")):
                run(command + ["-f", str(checks)])
            print("Local SQL checks passed: migration repeatability, soft deletion, snapshots, reports, costs and grants.")
        finally:
            if started:
                run([control, "-D", str(cluster), "-m", "fast", "-w", "stop"])


if __name__ == "__main__":
    main()
