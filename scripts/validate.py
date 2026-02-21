"""
Main validation script. Geocodes an address, runs all Toronto GIS spatial
queries, and generates a structured validation report.

Usage:
    python validate.py
    python validate.py "123 Some Street, Toronto, ON"
    python validate.py --provider google "123 Some Street, Toronto, ON"
    python validate.py --json-only "123 Some Street, Toronto, ON"
"""

import sys
import argparse
from geocoder import geocode
from toronto_gis import query_all
from boundary_check import validate_thompson_orchard
from report_generator import generate_report, format_markdown, format_json, save_report
from config import DEFAULT_ADDRESS


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate real estate property report claims using Toronto GIS data"
    )
    parser.add_argument(
        "address", nargs="?", default=DEFAULT_ADDRESS,
        help=f"Address to validate (default: {DEFAULT_ADDRESS})"
    )
    parser.add_argument(
        "--provider", choices=["nominatim", "google"], default="nominatim",
        help="Geocoding provider (default: nominatim, free)"
    )
    parser.add_argument(
        "--json-only", action="store_true",
        help="Output JSON instead of Markdown"
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Print to stdout only, don't save files"
    )
    parser.add_argument(
        "--skip-boundary", action="store_true",
        help="Skip community boundary validation even if zoning exception found"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print(f"{'=' * 60}")
    print(f"  Property Report GIS Validation")
    print(f"  Address: {args.address}")
    print(f"{'=' * 60}")

    # Step 1: Geocode
    print("\n[1/3] Geocoding address...")
    try:
        geo = geocode(args.address, provider=args.provider)
        print(f"  Coordinates: {geo['lat']}, {geo['lon']}")
        print(f"  Resolved:    {geo['display_name']}")
        print(f"  Provider:    {geo['source']}")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    # Step 2: Run all spatial queries
    print("\n[2/3] Querying Toronto GIS layers...")
    spatial = query_all(geo["lat"], geo["lon"])

    for name, result in spatial.items():
        if result is None:
            print(f"  {name}: not in this zone")
        elif isinstance(result, dict) and "error" in result:
            print(f"  {name}: ERROR - {result['error']}")
        else:
            # Show a summary field for each layer
            summary_keys = {
                "zoning": "zoning_string",
                "former_bylaw": "bylaw_name",
                "mtsa": "station_name",
                "neighbourhood": "name",
                "ward": "name",
                "community_planning": "district",
            }
            key = summary_keys.get(name, "")
            val = result.get(key, "OK") if key else "OK"
            print(f"  {name}: {val}")

    # Step 3: Community boundary validation (auto-runs if zoning exception found)
    boundary = None
    zoning = spatial.get("zoning")
    has_exception = (zoning and isinstance(zoning, dict)
                     and zoning.get("has_exception")
                     and zoning.get("exception_number"))

    if has_exception and not args.skip_boundary:
        exc_num = zoning["exception_number"]
        print(f"\n[3/4] Zoning exception x{exc_num} detected -- running boundary validation...")
        try:
            boundary = validate_thompson_orchard(
                geo["lat"], geo["lon"], exception_number=exc_num
            )
            print(f"\n  Boundary verdict: {boundary.get('verdict', 'UNKNOWN')}")
        except Exception as e:
            print(f"  Boundary check failed: {e}")
    elif has_exception:
        print(f"\n[3/4] Skipping boundary validation (--skip-boundary)")
    else:
        print(f"\n[3/4] No zoning exception found -- skipping boundary validation")

    # Step 4: Generate report
    step = "4/4" if has_exception else "3/3"
    print(f"\n[{step}] Generating report...")
    report = generate_report(args.address, geo, spatial, boundary_results=boundary)

    print("\n" + "=" * 60)

    if args.json_only:
        print(format_json(report))
    else:
        print(format_markdown(report))

    if not args.no_save:
        try:
            md_path, json_path = save_report(report)
            print(f"\nSaved: {md_path}")
            print(f"Saved: {json_path}")
        except Exception as e:
            print(f"\nWARNING: Could not save report: {e}")


if __name__ == "__main__":
    main()
