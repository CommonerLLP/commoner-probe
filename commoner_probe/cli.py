# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .academia import AcademicJobsProbe
from .answers import extract_answers
from .atr_linkage import extract_atr_linkages
from .bills import BILLS_API, BillsProbe
from .budget import RBI_STATE_FINANCES_URL, BudgetProbe
from .committees import CommitteeProbe, resolve_committees
from .csr.dpe import DpeCsrProbe
from .csr.mca import McaCsrProbe
from .debates import LS_DEBATE_API, DebateProbe
from .dmft.mines import MinesDmftProbe
from .evidence import build_dmft_evidence_bundle
from .example_topics import list_example_topics, load_example_topic_text
from .extract_debates import extract_debates
from .indiacode import STATE_HANDLES, IndiaCodeProbe
from .neva import StateAssemblyCrawler
from .neva_portals import NevaPortal, iter_portals
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
    topic = None
    if args.topic:
        topic = load_topic(args.topic)
    elif not args.member and not args.entity_id:
        raise SystemExit("--topic is required unless --member or --entity-id is provided.")

    out = Path(args.out)
    if args.reset and (out / "manifest.jsonl").exists():
        (out / "manifest.jsonl").unlink()
    if args.reset and (out / "probe.log").exists():
        (out / "probe.log").unlink()
    out.mkdir(parents=True, exist_ok=True)

    # We must enable entity resolution internally if --entity-id is used so we can look up the name.
    needs_entities = getattr(args, "with_entities", False) or bool(args.entity_id)
    resolver = _build_resolver_if_requested(out, needs_entities, print)

    member_name = args.member
    if args.entity_id:
        if not resolver:
            raise SystemExit("Resolver could not be initialized, cannot map entity-id to name.")
        person = resolver.store.people.get(args.entity_id)
        if not person:
            raise SystemExit(f"Entity ID {args.entity_id} not found in local store.")
        member_name = person.canonical_name

    probe = SansadProbe(
        topic,
        out,
        sleep=args.sleep,
        topic_path=args.topic,
        resolver=resolver,
        member_name=member_name,
    )
    seen = probe.load_seen()
    probe.log(f"resume seen={len(seen)} topic={probe.topic.name} download={not args.no_download}")
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
    if args.list_portals:
        for p in iter_portals():
            print(f"{p.portal_code}\t{p.state_code}\t{p.chamber}\t{p.state_name}")
        return

    if not args.out:
        raise SystemExit("--out is required unless --list-portals is given")
    out = Path(args.out)
    portals = iter_portals(chamber="assembly") if args.all else None
    if portals is None:
        if not args.portal or not args.state:
            raise SystemExit("--portal and --state are required unless --all or --list-portals is given")
        portals = (NevaPortal(args.portal, args.state, args.portal, "assembly"),)

    for p in portals:
        portal_out = out / p.portal_code if args.all else out
        portal_out.mkdir(parents=True, exist_ok=True)
        probe = StateAssemblyCrawler(
            portal_code=p.portal_code,
            state_code=p.state_code,
            out_dir=portal_out,
            sleep=args.sleep,
        )
        summary = probe.run(
            assembly_nos=parse_session_range(args.assemblies),
            download=not args.no_download,
            fetch_member_details=not args.no_member_details,
            sessions_limit=args.sessions_limit,
        )
        print(f"{p.portal_code}: {summary}")


def state_assembly_probe_cmd(args: argparse.Namespace) -> None:
    portals = iter_portals(chamber=None if args.include_councils else "assembly")
    codes = _split_csv(args.portals)
    if codes:
        portals = tuple(p for p in portals if p.portal_code in codes)

    out_path = Path(args.out) if args.out else None
    log_root = (out_path.parent if out_path else Path(".")) / "_probe_logs"
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    for p in portals:
        crawler = StateAssemblyCrawler(
            portal_code=p.portal_code,
            state_code=p.state_code,
            out_dir=log_root / p.portal_code,
            sleep=args.sleep,
        )
        try:
            result = crawler.probe_depth(max_assembly=args.max_assembly)
        except Exception as exc:  # noqa: BLE001
            result = {
                "portal_code": p.portal_code,
                "state_code": p.state_code,
                "reachable": False,
                "error": str(exc),
            }
        result["state_name"] = p.state_name
        result["chamber"] = p.chamber
        line = json.dumps(result, ensure_ascii=False)
        print(line)
        if out_path:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def indiacode_crawl_cmd(args: argparse.Namespace) -> None:
    if args.list_states:
        for name in sorted(STATE_HANDLES):
            print(f"{STATE_HANDLES[name]}\t{name}")
        return

    if not args.out:
        raise SystemExit("--out is required unless --list-states is given")
    out = Path(args.out)
    states = _split_csv(args.states) or (sorted(STATE_HANDLES) if args.all_states else [])
    if not states:
        raise SystemExit("--states, --all-states, or --list-states is required")
    probe = IndiaCodeProbe(out, sleep=args.sleep, rpp=args.rpp)
    records = probe.probe_states(
        states,
        download=not args.no_download,
        dry_run=args.dry_run,
        max_acts=args.max_acts,
    )
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def indiacode_query_cmd(args: argparse.Namespace) -> None:
    import re

    from .indiacode import PUBLIC_LIBRARIES_HANDLES

    if not args.out:
        raise SystemExit("--out is required")

    out = Path(args.out)
    states = _split_csv(args.states) or (sorted(STATE_HANDLES) if args.all_states else [])
    if not states:
        raise SystemExit("--states or --all-states is required")

    query_re = None
    exclude_re = None
    known_handles = None
    classify = False

    if args.public_libraries:
        query_re = re.compile(r"publ\w{0,2}c\s+librar|सार्वजनिक पुस्तकालय", re.IGNORECASE)
        exclude_re = re.compile(r"sachidanand|khuda\s+bakhsh|raza\s+library|rampuri|harekrushna|gautam\s+buddha", re.IGNORECASE)
        known_handles = PUBLIC_LIBRARIES_HANDLES
        classify = True
    else:
        if not args.query:
            raise SystemExit("--query is required unless --public-libraries is used")
        query_re = re.compile(args.query, re.IGNORECASE)
        if args.exclude:
            exclude_re = re.compile(args.exclude, re.IGNORECASE)

    probe = IndiaCodeProbe(out, sleep=args.sleep, rpp=args.rpp)
    records = probe.probe_states(
        states,
        download=not args.no_download,
        dry_run=args.dry_run,
        max_acts=args.max_acts,
        query_re=query_re,
        exclude_re=exclude_re,
        known_handles=known_handles,
        classify_availability=classify,
    )
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def dpe_csr_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    probe = DpeCsrProbe(out, sleep=args.sleep)
    records = probe.probe(search=args.search, dry_run=args.dry_run, max_pages=args.max_pages)
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def mca_csr_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    years = _split_csv(args.years) or []
    if not years:
        raise SystemExit("--years must contain at least one financial year, e.g. 2022-23")
    probe = McaCsrProbe(out, sleep=args.sleep)
    records = probe.probe_years(years, dry_run=args.dry_run)
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def mines_dmft_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    sources = _split_csv(args.sources) or []
    if not sources:
        raise SystemExit("--sources must contain at least one source, e.g. mines-gov-in")
    probe = MinesDmftProbe(
        out,
        sleep=args.sleep,
        ministry_endpoints=_split_csv(args.ministry_endpoints),
        odisha_endpoints=_split_csv(args.odisha_endpoints),
    )
    records = probe.probe_sources(sources, dry_run=args.dry_run)
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def budget_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    sources = _split_csv(args.sources) or ["union-budget"]
    demands = _split_csv(args.demands) or ["101"]
    probe = BudgetProbe(
        out,
        sleep=args.sleep,
        demands=demands,
        rbi_url=args.rbi_url,
    )
    records = probe.probe_sources(sources, dry_run=args.dry_run)
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def bills_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    houses = [args.house] if args.house != "both" else ["ls", "rs"]
    probe = BillsProbe(
        out,
        sleep=args.sleep,
        houses=houses,
        bill_type=args.bill_type,
        api_url=args.api_url,
    )
    records = probe.probe(max_records=args.max_records, dry_run=args.dry_run)
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def _add_indiacode_crawl_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", help="Output corpus directory; required unless --list-states")
    parser.add_argument("--states", help="Comma-separated state names, e.g. 'West Bengal,Sikkim'")
    parser.add_argument("--all-states", action="store_true", help="Probe every registered state (see --list-states)")
    parser.add_argument("--list-states", action="store_true", help="Print the registered state -> parent-handle table and exit")
    parser.add_argument("--max-acts", type=int, help="Stop after N Acts per state (smoke-test brake)")
    parser.add_argument("--no-download", action="store_true", help="Record instruments without downloading PDFs")
    parser.add_argument("--rpp", type=int, default=100, help="Results per browse page (India Code enumeration)")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit one planning record per state without fetching.",
    )


def debates_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    loksabhas = [int(x) for x in (_split_csv(args.loksabhas) or ["18"])]
    sessions = [int(x) for x in (_split_csv(args.sessions) or [])] or None
    probe = DebateProbe(
        out,
        sleep=args.sleep,
        loksabhas=loksabhas,
        sessions=sessions,
        from_date=args.from_date,
        to_date=args.to_date,
        api_url=args.api_url,
    )
    records = probe.probe(
        max_records=args.max_records,
        download=args.download,
        dry_run=args.dry_run,
    )
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def academic_jobs_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    probe = AcademicJobsProbe(
        out,
        sleep=args.sleep,
        institutions=_split_csv(args.institutions),
        registry_path=args.registry,
    )
    records = probe.probe(download=not args.no_download, dry_run=args.dry_run)
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def extract_debates_cmd(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if not (out / "manifest.jsonl").exists():
        raise SystemExit(f"no manifest at {out}/manifest.jsonl — run 'debates' first")
    extract_debates(out, refresh=args.refresh, log_fn=print)


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


def evidence_dmft_cmd(args: argparse.Namespace) -> None:
    terms = tuple(_split_csv(args.terms) or [])
    bundle = build_dmft_evidence_bundle(
        mines_dmft_dir=args.mines_dmft_dir,
        sansad_dir=args.sansad_dir,
        ministry=args.ministry,
        terms=terms or None,
    )
    text = json.dumps(bundle, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


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
    sansad.add_argument("--topic", help="Path to topic profile JSON (required unless --member or --entity-id given)")
    sansad.add_argument("--member", help="Member name for per-member retrieval mode")
    sansad.add_argument("--entity-id", help="Stable entity ID for per-member retrieval mode")
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


    extract_deb = sub.add_parser(
        "extract-debates",
        help="Extract structured speeches from Lok Sabha debate PDFs into speeches.jsonl",
    )
    extract_deb.add_argument("--out", required=True, help="Corpus directory containing manifest.jsonl + downloaded PDFs")
    extract_deb.add_argument("--refresh", action="store_true", help="Force re-extraction even if speeches.jsonl exists")
    extract_deb.set_defaults(func=extract_debates_cmd)


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
    state_assembly.add_argument("--portal", help="Portal subdomain, e.g. gujarat")
    state_assembly.add_argument("--state", help="CMS state code, e.g. GJ")
    state_assembly.add_argument("--out", help="Output corpus directory (one subdirectory per portal when --all); required unless --list-portals")
    state_assembly.add_argument("--assemblies", default="15", help="Assembly numbers, e.g. 15 or 14-15")
    state_assembly.add_argument("--sleep", type=float, default=0.5)
    state_assembly.add_argument("--no-download", action="store_true")
    state_assembly.add_argument("--no-member-details", action="store_true", help="Skip per-member detail pages")
    state_assembly.add_argument("--sessions-limit", type=int, help="Stop after N sessions per assembly (smoke-test)")
    state_assembly.add_argument(
        "--all", action="store_true",
        help="Crawl every registered assembly portal (see --list-portals) instead of a single --portal/--state.",
    )
    state_assembly.add_argument(
        "--list-portals", action="store_true",
        help="Print the registered portal_code/state_code/chamber/state_name table and exit.",
    )
    state_assembly.set_defaults(func=state_assembly_cmd)

    state_assembly_probe = sub.add_parser(
        "state-assembly-probe",
        help="Data-depth coverage probe across registered NeVA portals (sessions/questions/papers/members counts).",
    )
    state_assembly_probe.add_argument("--out", help="Append one JSONL coverage record per portal to this file (also printed to stdout).")
    state_assembly_probe.add_argument("--portals", help="Comma-separated portal_codes to limit the probe to; default = all.")
    state_assembly_probe.add_argument("--include-councils", action="store_true", help="Include the 6 Legislative Council portals.")
    state_assembly_probe.add_argument("--max-assembly", type=int, default=20, help="Highest assembly number to scan per portal.")
    state_assembly_probe.add_argument("--sleep", type=float, default=0.5)
    state_assembly_probe.set_defaults(func=state_assembly_probe_cmd)

    dpe_csr = sub.add_parser(
        "dpe-csr",
        help="Download DPE CPSE CSR documents via WordPress REST API.",
    )
    dpe_csr.add_argument("--out", required=True, help="Output directory")
    dpe_csr.add_argument("--search", default="csr", help="Search query for the media endpoint (default: 'csr').")
    dpe_csr.add_argument("--max-pages", type=int, default=10, help="Max pages to fetch (default: 10).")
    dpe_csr.add_argument("--sleep", type=float, default=1.0)
    dpe_csr.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest records without writing manifest.jsonl.",
    )
    dpe_csr.set_defaults(func=dpe_csr_cmd)

    mca_csr = sub.add_parser(
        "mca-csr",
        help="Download MCA CDM CSR company-spend CSV exports.",
    )
    mca_csr.add_argument("--out", required=True, help="Output directory")
    mca_csr.add_argument(
        "--years",
        required=True,
        help="Comma-separated financial years, e.g. 2022-23 or FY 2022-23",
    )
    mca_csr.add_argument("--sleep", type=float, default=2.0)
    mca_csr.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest records without opening a network session or writing manifest.jsonl.",
    )
    mca_csr.set_defaults(func=mca_csr_cmd)

    mines_dmft = sub.add_parser(
        "mines-dmft",
        help="Download Ministry of Mines / Odisha DMFT raw source files.",
    )
    mines_dmft.add_argument("--out", required=True, help="Output directory")
    mines_dmft.add_argument(
        "--sources",
        default="mines-gov-in,odisha",
        help="Comma-separated sources: mines-gov-in, odisha",
    )
    mines_dmft.add_argument("--sleep", type=float, default=1.0)
    mines_dmft.add_argument(
        "--ministry-endpoints",
        help="Optional comma-separated Ministry endpoint filenames for focused runs.",
    )
    mines_dmft.add_argument(
        "--odisha-endpoints",
        help="Optional comma-separated Odisha endpoint filenames for focused runs.",
    )
    mines_dmft.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest records without opening network sessions or writing manifest.jsonl.",
    )
    mines_dmft.set_defaults(func=mines_dmft_cmd)

    budget = sub.add_parser(
        "budget",
        help="Download Union Budget SBE spreadsheets and RBI State-Finances source files.",
    )
    budget.add_argument("--out", required=True, help="Output directory")
    budget.add_argument(
        "--sources",
        default="union-budget",
        help="Comma-separated sources: union-budget, rbi-state-finances",
    )
    budget.add_argument(
        "--demands",
        default="101",
        help="Comma-separated Union Budget demand numbers, e.g. 101,1,33",
    )
    budget.add_argument(
        "--rbi-url",
        default=RBI_STATE_FINANCES_URL,
        help="RBI State-Finances publication page to discover documents from.",
    )
    budget.add_argument("--sleep", type=float, default=1.0)
    budget.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print manifest records without writing manifest.jsonl. Fully offline "
            "for union-budget; rbi-state-finances still fetches the index page to enumerate."
        ),
    )
    budget.set_defaults(func=budget_cmd)

    bills = sub.add_parser(
        "bills",
        help="Probe sansad.in bills / legislation (api_rs/legislation/getBills).",
    )
    bills.add_argument("--out", required=True, help="Output corpus directory")
    bills.add_argument("--house", choices=["both", "ls", "rs"], default="both")
    bills.add_argument(
        "--bill-type",
        default="",
        help="Filter by bill type, e.g. 'Government' or 'Private Member'; default = all types.",
    )
    bills.add_argument("--max-records", type=int, help="Stop after N new records per house (smoke-test brake)")
    bills.add_argument("--api-url", default=BILLS_API, help="Override the bills API base URL.")
    bills.add_argument("--sleep", type=float, default=0.5)
    bills.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit one planning record per house without fetching.",
    )
    bills.set_defaults(func=bills_cmd)

    indiacode = sub.add_parser(
        "indiacode",
        help="Probe India Code (indiacode.nic.in) state Acts + amendments/rules/notifications.",
    )
    _add_indiacode_crawl_args(indiacode)
    indiacode.set_defaults(func=indiacode_crawl_cmd)
    ic_sub = indiacode.add_subparsers(dest="indiacode_command")

    ic_crawl = ic_sub.add_parser("crawl", help="Full state crawl (existing behavior)")
    _add_indiacode_crawl_args(ic_crawl)
    ic_crawl.set_defaults(func=indiacode_crawl_cmd)

    ic_query = ic_sub.add_parser("query", help="Query India Code browse index for specific Acts.")
    ic_query.add_argument("--out", help="Output corpus directory")
    ic_query.add_argument("--states", help="Comma-separated state names")
    ic_query.add_argument("--all-states", action="store_true", help="Probe every registered state")
    ic_query.add_argument("--query", help="Regex to match Act short titles")
    ic_query.add_argument("--exclude", help="Regex to exclude from matched titles")
    ic_query.add_argument("--public-libraries", action="store_true", help="Use built-in Public Libraries Act registry and query fallback")
    ic_query.add_argument("--max-acts", type=int, help="Stop after N Acts per state (smoke-test brake)")
    ic_query.add_argument("--no-download", action="store_true", help="Record instruments without downloading PDFs")
    ic_query.add_argument("--rpp", type=int, default=100, help="Results per browse page (India Code enumeration)")
    ic_query.add_argument("--sleep", type=float, default=1.0)
    ic_query.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit one planning record per state without fetching.",
    )
    ic_query.set_defaults(func=indiacode_query_cmd)

    debates = sub.add_parser(
        "debates",
        help="Probe Lok Sabha per-day floor-debate transcript PDFs (api_ls/debate/text-of-debate).",
    )
    debates.add_argument("--out", required=True, help="Output corpus directory")
    debates.add_argument("--loksabhas", default="18", help="Comma-separated Lok Sabha numbers, e.g. 17,18")
    debates.add_argument("--sessions", help="Comma-separated session numbers to limit to; default = all")
    debates.add_argument("--from-date", help="ISO date lower bound (YYYY-MM-DD)")
    debates.add_argument("--to-date", help="ISO date upper bound (YYYY-MM-DD)")
    debates.add_argument("--max-records", type=int, help="Stop after N new records per Lok Sabha (smoke-test brake)")
    debates.add_argument("--download", action="store_true", help="Download each day's transcript PDF (+ sha256)")
    debates.add_argument("--api-url", default=LS_DEBATE_API, help="Override the debate API base URL.")
    debates.add_argument("--sleep", type=float, default=0.5)
    debates.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidate sitting dates (from the session catalog) without fetching per-day PDFs.",
    )
    debates.set_defaults(func=debates_cmd)

    academic_jobs = sub.add_parser(
        "academic-jobs",
        help="Crawl Indian HEI career pages for faculty-recruitment advertisements.",
    )
    academic_jobs.add_argument("--out", required=True, help="Output directory")
    academic_jobs.add_argument(
        "--institutions",
        help="Comma-separated institution ids (e.g. iit-kharagpur); default = all in registry",
    )
    academic_jobs.add_argument(
        "--registry",
        help="Path to an alternative institutions_registry.json; default = bundled registry",
    )
    academic_jobs.add_argument(
        "--no-download",
        action="store_true",
        help="Skip PDF download + text extraction (listing-page heuristics only).",
    )
    academic_jobs.add_argument("--sleep", type=float, default=1.0)
    academic_jobs.add_argument(
        "--dry-run",
        action="store_true",
        help="List which institutions would be probed (one record each) without fetching.",
    )
    academic_jobs.set_defaults(func=academic_jobs_cmd)

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

    evidence = sub.add_parser(
        "evidence",
        help="Build cross-source evidence bundles without flattening source families.",
    )
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_dmft = evidence_sub.add_parser(
        "dmft",
        help="Bundle Ministry of Mines DMFT disclosure records with Sansad Q/A oversight records.",
    )
    evidence_dmft.add_argument(
        "--mines-dmft-dir",
        required=True,
        help="Ministry of Mines / DMFT disclosure corpus directory",
    )
    evidence_dmft.add_argument("--sansad-dir", help="Sansad Q/A corpus directory")
    evidence_dmft.add_argument("--out", help="Write bundle JSON to this path; defaults to stdout")
    evidence_dmft.add_argument("--ministry", default="MINES", help="Sansad ministry filter")
    evidence_dmft.add_argument(
        "--terms",
        help="Comma-separated terms for filtering Sansad Q/A; defaults to DMFT/PMKKKY terms.",
    )
    evidence_dmft.set_defaults(func=evidence_dmft_cmd)

    init_topic = sub.add_parser(
        "init-topic",
        help="Write a built-in topic profile JSON to a local path.",
    )
    init_topic.add_argument(
        "--name",
        required=True,
        help=(
            "Built-in topic name (e.g., libraries, home_affairs_starred, "
            "affirmative_action, mines_dmft_pmkkky, narcotics_substance)."
        ),
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


if __name__ == "__main__":
    main()
