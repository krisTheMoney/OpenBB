#!/usr/bin/env python3
"""
Lag den daglige markeds- og porteføljeoppdateringen.

Eksempler
---------
    python run_brief.py                      # hent ferske data og skriv rapporten
    python run_brief.py --validate           # sjekk bare at portfolio.toml stemmer
    python run_brief.py --offline            # bygg rapporten fra lagrede testdata
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daily_brief.config import ConfigError, load_portfolio, validate_totals  # noqa: E402
from daily_brief.offline import load_bundle  # noqa: E402
from daily_brief.pipeline import BriefError, build_brief, run_pipeline  # noqa: E402
from daily_brief.render import render_json, render_markdown  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "portfolio.toml"
DEFAULT_FIXTURE = HERE / "tests" / "fixtures" / "offline_bundle.json"
DEFAULT_OUTPUT = HERE / "reports"

logger = logging.getLogger("daily_brief")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Les kommandolinjeargumentene."""
    parser = argparse.ArgumentParser(
        description="Daglig markeds- og porteføljeoppdatering",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="sti til portfolio.toml",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="mappe rapportene skrives til",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="kontroller bare at porteføljen summerer riktig, og avslutt",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="bygg rapporten fra lagrede testdata i stedet for å hente ferske",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="testdata som brukes av --offline",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="skriv detaljert logg",
    )
    return parser.parse_args(argv)


def write_outputs(output_dir: Path, markdown: str, payload: dict) -> tuple[Path, Path]:
    """
    Skriv rapporten til disk.

    Den daterte filen er arkivet; latest.json er det Claude-rutinen leser hver morgen.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    trading_day = payload.get("trading_day") or payload["generated_at"][:10]
    dated_path = output_dir / f"{trading_day}.md"
    json_path = output_dir / "latest.json"

    dated_path.write_text(markdown, encoding="utf-8")
    (output_dir / "latest.md").write_text(markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return dated_path, json_path


def main(argv: list[str] | None = None) -> int:
    """Kjør kommandoen og returner exit-koden."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        config = load_portfolio(args.config)
    except ConfigError as error:
        logger.error("ugyldig portefølje-config: %s", error)
        return 2

    is_valid, message = validate_totals(config)

    if not is_valid:
        logger.error(message)
        return 2

    logger.info(message)

    if args.validate:
        return 0

    generated_at = datetime.now(timezone.utc)

    try:
        if args.offline:
            logger.info("bygger fra lagrede testdata: %s", args.fixture)
            result, fx = load_bundle(args.fixture, generated_at)
            brief = build_brief(config, result, fx, generated_at)
        else:
            brief = asyncio.run(run_pipeline(config, generated_at))
    except BriefError as error:
        logger.error("%s", error)
        return 1
    except FileNotFoundError as error:
        logger.error("fant ikke testdata: %s", error)
        return 2

    markdown = render_markdown(brief)
    payload = render_json(brief)
    dated_path, json_path = write_outputs(args.out, markdown, payload)

    if brief.issues:
        logger.warning("%d kilde(r) feilet, se Forbehold i rapporten", len(brief.issues))

    logger.info("skrev %s", dated_path)
    logger.info("skrev %s", json_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
