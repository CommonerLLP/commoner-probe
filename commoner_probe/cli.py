# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import argparse
from pathlib import Path

from .answers import extract_answers
from .atr_linkage import extract_atr_linkages
from .committees import CommitteeProbe, resolve_committees
from .example_topics import list_example_topics, load_example_topic_text
from .neva import StateAssemblyCrawler
from .sansad import SansadProbe
from .stats import compute_stats, print_stats
from .topics import load_topic
from .validate import validate_corpus


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_session_range(value: str) -> list[int]:
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = [int(x.strip()) for x in part.split("-", 1)]
            out.extend(range(start, end + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def _build_resolver_if_requested(out_dir: Path, with_entities: bool, log):
    """Lazy-import to keep the CLI cold-start cheap when --with-entities is off."""
    if not with_entities:
        return None
    from .entities import EntityStore, populate_entity_store_from_mp_roster
    from .members import MPRoster
    from .resolver import Resolver
    store = EntityStore(out_dir)
    store.load()
    if not store.people:
        log("Entity store empty — fetching MP roster from sansad.in...")
        roster = MPRoster()
        try:
            roster.load_ls()
            roster.load_rs()
        except Exception as exc:  # noqa: BLE001
            log(f"Warning: MP roster fetch failed: {exc}; resolver will return 'unknown' for askers.")
        people_added, memberships_added = populate_entity_store_from_mp_roster(roster, store)
        log(f"Populated entity store: {people_added} people, {memberships_added} memberships.")
        store.save()
    else:
        log(f"Loaded existing entity store: {len(store.people)} people.")
    return Resolver(store)


def sansad_cmd(args: argparse.Namespace) -> None:
    topic = load_topic(args.topic)
    out = Path(args.out)
    if args.reset and (out / "manifest.jsonl").exists():
        (out / "manifest.jsonl").unlink()
    if args.reset and (out / "probe.log").exists():
        (out / "probe.log").unlink()
    out.mkdir(parents=True, exist_ok=True)
    resolver = _build_resolver_if_requested(out, getattr(args, "with_entities", False), print)
    probe = SansadProbe(
        topic,
        out,
        sleep=args.sleep,
        topic_path=args.topic,
        resolver=resolver,
    )
    seen = probe.load_seen()
    probe.log(f"resume seen={len(seen)} topic={topic.name} download={not args.no_download}")
    added = 0
    if args.house in ("both", "ls"):
        added += probe.probe_ls(
            seen,
            from_date=args.from_date,
            to_date=args.to_date,
            qtype_filter=None if args.qtype == "both" else args.qtype,
            limit=args.limit,
            max_buckets=args.max_buckets,
            max_records=args.max_records,
            download=not args.no_download,
        )
    if args.house in ("both", "rs"):
        added += probe.probe_rs(
            seen,
            sessions=parse_session_range(args.sessions),
            from_date=args.from_date,
            to_date=args.to_date,
            qtype_filter=None if args.qtype == "both" else args.qtype,
            limit=args.limit,
            max_buckets=args.max_buckets,
            max_records=args.max_records,
            download=not args.no_download,
        )
    probe.log(f"DONE added={added} total={len(seen)}")


def committees_cmd(args: argparse.Namespace) -> None:
    topic = load_topic(args.topic)
    out = Path(args.out)
    if args.reset and (out / "manifest.jsonl").exists():
        (out / "manifest.jsonl").unlink()
    if args.reset and (out / "probe.log").exists():
        (out / "probe.log").unlink()
    probe = CommitteeProbe(
        topic,
        out,
        sleep=args.sleep,
        lok_sabha_no=args.lok_sabha_no,
        topic_path=args.topic,
    )
    seen = probe.load_seen()
    requested = _split_csv(args.committees)
    probe.log(
        f"resume seen={len(seen)} topic={topic.name} ls={args.lok_sabha_no} "
        f"download={not args.no_download}"
    )
    added = 0
    if args.house in ("both", "ls"):
        added += probe.probe_ls(
            seen,
            committees=resolve_committees("ls", requested),
            from_date=args.from_date,
            to_date=args.to_date,
            max_records=args.max_records,
            download=not args.no_download,
        )
    if args.house in ("both", "rs"):
        added += probe.probe_rs(
            seen,
            committees=resolve_committees("rs", requested),
            from_date=args.from_date,
            to_date=args.to_date,
            max_records=args.max_records,
            download=not args.no_download,
        )
    probe.log(f"DONE added={added} total={len(seen)}")


def state_assembly_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    probe = StateAssemblyCrawler(
        portal_code=args.portal,
        state_code=args.state,
        out_dir=out,
        sleep=args.sleep,
    )
    summary = probe.run(
        assembly_nos=parse_session_range(args.assemblies),
        download=not args.no_download,
        fetch_member_details=not args.no_member_details,
        sessions_limit=args.sessions_limit,
    )
    print(summary)


def extract_answers_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if not (out / "manifest.jsonl").exists():
        raise SystemExit(f"no manifest at {out}/manifest.jsonl — run 'sansad' first")
    extract_answers(out, refresh=args.refresh, log_fn=print)


def extract_atr_linkage_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if not (out / "manifest.jsonl").exists():
        raise SystemExit(f"no manifest at {out}/manifest.jsonl — run 'committees' first")
    extract_atr_linkages(out, log_fn=print)


def stats_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if not out.is_dir():
        raise SystemExit(f"directory not found: {out}")
    stats = compute_stats(out)
    print_stats(stats, json_output=args.json)


def validate_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if not out.is_dir():
        raise SystemExit(f"directory not found: {out}")
    ok = validate_corpus(out, log=print, max_errors=args.max_errors)
    if not ok:
        raise SystemExit(1)


def init_topic_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if out.exists() and not args.force:
        raise SystemExit(f"output already exists: {out} (pass --force to overwrite)")
    try:
        topic_text = load_example_topic_text(args.name)
    except KeyError:
        available = ", ".join(list_example_topics())
        raise SystemExit(
            f"unknown built-in topic '{args.name}'. available topics: {available}"
        ) from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(topic_text, encoding="utf-8")
    print(f"wrote built-in topic '{args.name}' to {out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="commoner-probe")
    sub = parser.add_subparsers(dest="command", required=True)

    sansad = sub.add_parser("sansad", help="Probe Lok Sabha / Rajya Sabha parliamentary questions")
    sansad.add_argument("--topic", required=True, help="Path to topic profile JSON")
    sansad.add_argument("--out", required=True, help="Output corpus directory")
    sansad.add_argument("--house", choices=["both", "ls", "rs"], default="both")
    sansad.add_argument("--from-date")
    sansad.add_argument("--to-date")
    sansad.add_argument(
        "--qtype",
        choices=["both", "starred", "unstarred"],
        default="both",
        help="Filter to starred or unstarred questions at crawl time.",
    )
    sansad.add_argument("--sessions", default="1-267", help="Rajya Sabha sessions, e.g. 230-267")
    sansad.add_argument("--limit", type=int, help="Max raw API records per bucket")
    sansad.add_argument("--max-buckets", type=int, help="First N search/ministry buckets (smoke-test brake)")
    sansad.add_argument("--max-records", type=int, help="Stop after N new records per house crawl (smoke-test brake)")
    sansad.add_argument("--sleep", type=float, default=0.25)
    sansad.add_argument("--no-download", action="store_true")
    sansad.add_argument("--reset", action="store_true")
    sansad.add_argument(
        "--with-entities",
        action="store_true",
        help=(
            "Resolve asker names to stable entity_ids. First run fetches MP "
            "rosters from sansad.in and populates entities/; subsequent runs "
            "reuse the local store."
        ),
    )
    sansad.set_defaults(func=sansad_cmd)

    cc = sub.add_parser("committees", help="Probe standing-committee reports")
    cc.add_argument("--topic", required=True, help="Path to topic profile JSON")
    cc.add_argument("--out", required=True, help="Output corpus directory")
    cc.add_argument("--house", choices=["both", "ls", "rs"], default="both")
    cc.add_argument("--committees", help="Comma-separated committee slugs; default = all for the chosen house(s)")
    cc.add_argument("--lok-sabha-no", type=int, default=18, help="Lok Sabha number for LS reports (default 18)")
    cc.add_argument("--from-date")
    cc.add_argument("--to-date")
    cc.add_argument("--max-records", type=int, help="Stop after N new records per house crawl (smoke-test brake)")
    cc.add_argument("--sleep", type=float, default=0.25)
    cc.add_argument("--no-download", action="store_true")
    cc.add_argument("--reset", action="store_true")
    cc.set_defaults(func=committees_cmd)

    extract = sub.add_parser(
        "extract-answers",
        help="Extract structured question/answer and recommendation/response pairs from PDFs into answers.jsonl",
    )
    extract.add_argument("--out", required=True, help="Corpus directory containing manifest.jsonl + downloaded PDFs")
    extract.add_argument("--refresh", action="store_true", help="Force re-extraction even if answers.jsonl exists")
    extract.set_defaults(func=extract_answers_cmd)

    state_assembly = sub.add_parser(
        "state-assembly",
        help="Probe a NeVA state assembly portal (questions, members, papers to be laid).",
    )
    state_assembly.add_argument("--portal", required=True, help="Portal subdomain, e.g. gujarat")
    state_assembly.add_argument("--state", required=True, help="CMS state code, e.g. GJ")
    state_assembly.add_argument("--out", required=True, help="Output corpus directory")
    state_assembly.add_argument("--assemblies", default="15", help="Assembly numbers, e.g. 15 or 14-15")
    state_assembly.add_argument("--sleep", type=float, default=0.5)
    state_assembly.add_argument("--no-download", action="store_true")
    state_assembly.add_argument("--no-member-details", action="store_true", help="Skip per-member detail pages")
    state_assembly.add_argument("--sessions-limit", type=int, help="Stop after N sessions per assembly (smoke-test)")
    state_assembly.set_defaults(func=state_assembly_cmd)

    atr_link = sub.add_parser(
        "atr-linkage",
        help=(
            "For every Action Taken Report (ATR) in manifest.jsonl, parse the title "
            "to find the original committee report it responds to. "
            "ATR = the government's formal written response to a committee recommendation. "
            "Writes atr_linkage.jsonl."
        ),
    )
    atr_link.add_argument("--out", required=True, help="Corpus directory containing manifest.jsonl")
    atr_link.set_defaults(func=extract_atr_linkage_cmd)

    st = sub.add_parser(
        "stats",
        help="Print corpus health statistics (record counts, coverage, top ministries).",
    )
    st.add_argument("--out", required=True, help="Corpus directory")
    st.add_argument(
        "--json",
        action="store_true",
        help="Emit stats as a single JSON object",
    )
    st.set_defaults(func=stats_cmd)

    val = sub.add_parser(
        "validate",
        help=(
            "Validate all JSONL files in a corpus directory against their "
            "JSON Schemas. Requires pip install commoner-probe[dev]."
        ),
    )
    val.add_argument("--out", required=True, help="Corpus directory to validate")
    val.add_argument(
        "--max-errors",
        type=int,
        default=10,
        help="Maximum validation errors to print per file (default: 10)",
    )
    val.set_defaults(func=validate_cmd)

    init_topic = sub.add_parser(
        "init-topic",
        help="Write a built-in topic profile JSON to a local path.",
    )
    init_topic.add_argument(
        "--name",
        required=True,
        help="Built-in topic name (e.g., libraries, home_affairs_starred, affirmative_action).",
    )
    init_topic.add_argument("--out", required=True, help="Destination path for the topic JSON file")
    init_topic.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination file if it already exists.",
    )
    init_topic.set_defaults(func=init_topic_cmd)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
