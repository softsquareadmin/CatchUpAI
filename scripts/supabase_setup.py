"""Check the remote schema and explicitly import existing local configurations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catchupai.database import SupabaseStore, canonical_id
from catchupai.models import validate_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-configurations", action="store_true")
    args = parser.parse_args()
    store = SupabaseStore()
    store.check_schema()
    bucket = store.request("GET", "/storage/v1/bucket/session-audio")
    if bucket.get("public"):
        raise ValueError("session-audio must be a private Storage bucket.")
    print("Supabase: four tables available (including session_costs); audio bucket is private.")
    if args.import_configurations:
        imported = skipped = 0
        directory = Path(__file__).resolve().parent.parent / "configuration_templates"
        for path in sorted(directory.glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("version") != 1 or document.get("id") != path.stem:
                raise ValueError("A local configuration has an unsupported format or identity.")
            validate_config(document["config"])
            identity = canonical_id(document["id"])
            if store.rows("configurations", id="eq." + identity):
                skipped += 1
                continue
            name = document["name"].strip()
            if not name:
                raise ValueError("A local configuration is missing its name.")
            store.insert("configurations", {"id": identity, "name": name,
                "normalized_name": name.casefold(), "config": document["config"],
                "deleted_at": document.get("deleted_at")})
            imported += 1
        print(f"Configurations: {imported} imported; {skipped} already present. Local files retained.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
