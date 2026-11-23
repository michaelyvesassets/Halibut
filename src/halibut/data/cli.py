"""``halibut data`` commands: init, ingest, status, events.

    halibut data init
    halibut data ingest [--dataset ID ...]      # fetches from the publisher (network)
    halibut data events FILE                    # load a sourced policy/event file
    halibut data status
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..config import ConfigError

DEFAULT_DIR = Path(os.environ.get("HALIBUT_DATA_DIR", "data"))
DEFAULT_CATALOGUE = "configs/data/catalogue.toml"


def add_data_commands(sub) -> None:
    p = sub.add_parser("data", help="real-world data: catalogue, ingestion, provenance store")
    p.add_argument("--db", default=str(DEFAULT_DIR / "halibut.sqlite"))
    p.add_argument("--raw", default=str(DEFAULT_DIR / "raw"), help="raw snapshot directory")
    p.add_argument("--catalogue", default=DEFAULT_CATALOGUE)
    dsub = p.add_subparsers(dest="data_command", required=True)
    dsub.add_parser("init", help="create/migrate the store and register the catalogue")
    i = dsub.add_parser("ingest", help="fetch, snapshot and load datasets (network access)")
    i.add_argument("--dataset", action="append", help="dataset id (repeatable; default: all)")
    e = dsub.add_parser("events", help="load a sourced event file into the store")
    e.add_argument("file")
    dsub.add_parser("status", help="summarise datasets, snapshots and coverage")
    p.set_defaults(handler=_handle)


def _open(args):
    from .catalogue import load_catalogue  # noqa: PLC0415
    from .store import Store  # noqa: PLC0415
    cat = load_catalogue(args.catalogue)
    store = Store(args.db)
    store.register_catalogue(cat)
    return cat, store


def _handle(args) -> int:
    try:
        cat, store = _open(args)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.data_command == "init":
            print(f"store ready: {args.db} ({len(cat.datasets)} datasets registered)")
        elif args.data_command == "ingest":
            from .ssb import FetchError, Fetcher, ingest  # noqa: PLC0415
            ids = args.dataset or list(cat.datasets)
            fetcher = Fetcher()
            print(f"ingesting {len(ids)} dataset(s); polite rate limit => at least {2.5 * len(ids):.0f}s")
            failed = 0
            for ds in ids:
                try:
                    r = ingest(store, cat, ds, args.raw, fetcher)
                    print(f"  {ds}: {r['series']} series, {r['cells']} cells; new={r['new']} "
                          f"revised={r['revised']} unchanged={r['unchanged']} "
                          f"(snapshot {r['snapshot_id']}{'' if r['new_snapshot'] else ', identical bytes'}; "
                          f"publisher updated {r['publisher_updated']})")
                except (FetchError, ValueError) as exc:
                    failed += 1
                    print(f"  {ds}: FAILED: {exc}", file=sys.stderr)
            return 1 if failed else 0
        elif args.data_command == "events":
            from .events import load_events  # noqa: PLC0415
            events = load_events(args.file)
            store.upsert_events(events)
            print(f"loaded {len(events)} events from {args.file}")
        elif args.data_command == "status":
            from .quality import status_rows  # noqa: PLC0415
            for row in status_rows(store):
                print("  " + "  ".join(f"{k}={v}" for k, v in row.items()))
        return 0
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
