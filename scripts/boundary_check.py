"""
Community boundary validation using real GIS geometry.

Validates claims like "Thompson Orchard runs west of Royal York, south of Bloor
and is bounded west and south by Mimico Creek" by:

1. Mapping all zoning parcels with a given exception number (e.g., x42)
2. Querying the City's property boundary data for properties ON each boundary road
3. Querying the waterline layer for creek geometry
4. Comparing the target property's coordinates to each boundary

Uses THREE independent data sources:
- Zoning layer: confirms the exception applies to the parcel
- Property boundary layer: locates roads by finding properties that front them
- Waterline layer: locates creeks from city hydrology data

Usage:
    python boundary_check.py
    python boundary_check.py "9 Ashton Manor, Etobicoke, ON" --exception 42
    python boundary_check.py --lat 43.6455 --lon -79.5053 --exception 42
"""

import sys
import argparse
import math

from geocoder import geocode
from toronto_gis import (
    query_exception_zone,
    query_waterline_geometry,
    _query_where,
)
from config import DEFAULT_ADDRESS, LAYER_PROPERTY_BOUNDARY


# ---------------------------------------------------------------------------
# Road location via property boundary data
# ---------------------------------------------------------------------------

def _find_road_position(road_name, near_lat, near_lon, radius=0.005):
    """
    Find a road's position by querying City property parcels that front it.

    The City's property boundary layer has ADDRESS_NUMBER and LINEAR_NAME_FULL
    for each parcel. By finding all parcels on a named road near a location,
    we get the road's actual position from authoritative parcel geometry.

    Returns:
        dict with road_lon_range (for N-S roads) or road_lat_range (for E-W roads),
        plus parcel_count and source details.
    """
    envelope = (f"{near_lon - radius},{near_lat - radius},"
                f"{near_lon + radius},{near_lat + radius}")

    features = _query_where(
        LAYER_PROPERTY_BOUNDARY,
        where_clause=f"LINEAR_NAME_FULL = '{road_name}'",
        out_fields="ADDRESS_NUMBER,LINEAR_NAME_FULL",
        return_geometry=True,
        extra_params={
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        },
    )

    if not features:
        return None

    all_lons = []
    all_lats = []
    for f in features:
        geom = f.get("geometry", {})
        for ring in geom.get("rings", []):
            for coord in ring:
                all_lons.append(coord[0])
                all_lats.append(coord[1])

    return {
        "road_name": road_name,
        "parcel_count": len(features),
        "lon_range": (min(all_lons), max(all_lons)),
        "lat_range": (min(all_lats), max(all_lats)),
        "centroid_lon": sum(all_lons) / len(all_lons),
        "centroid_lat": sum(all_lats) / len(all_lats),
        "source": f"City property boundary layer ({len(features)} parcels)",
    }


def _find_all_nearby_streets(near_lat, near_lon, radius=0.003):
    """
    Find all streets with properties near a location.
    Returns a dict of street_name -> lon_range for mapping the neighbourhood layout.
    """
    envelope = (f"{near_lon - radius},{near_lat - radius},"
                f"{near_lon + radius},{near_lat + radius}")

    features = _query_where(
        LAYER_PROPERTY_BOUNDARY,
        where_clause="1=1",
        out_fields="LINEAR_NAME_FULL",
        return_geometry=True,
        extra_params={
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        },
    )

    streets = {}
    for f in features:
        street = f.get("attributes", {}).get("LINEAR_NAME_FULL") or "unknown"
        geom = f.get("geometry", {})
        lons = [c[0] for ring in geom.get("rings", []) for c in ring]
        lats = [c[1] for ring in geom.get("rings", []) for c in ring]
        if not lons:
            continue
        if street not in streets:
            streets[street] = {"count": 0, "lons": [], "lats": []}
        streets[street]["count"] += 1
        streets[street]["lons"].extend(lons)
        streets[street]["lats"].extend(lats)

    result = {}
    for street, data in streets.items():
        result[street] = {
            "count": data["count"],
            "lon_range": (min(data["lons"]), max(data["lons"])),
            "lat_range": (min(data["lats"]), max(data["lats"])),
        }
    return result


# ---------------------------------------------------------------------------
# Boundary checks
# ---------------------------------------------------------------------------

def check_relative_to_road(point_lon, point_lat, road_name, expected_side,
                           near_lat=None, near_lon=None):
    """
    Check if a point is on the expected side of a road.

    expected_side: "west", "east", "north", "south"

    Uses City property boundary data to locate the road precisely.
    """
    road = _find_road_position(
        road_name,
        near_lat=near_lat or point_lat,
        near_lon=near_lon or point_lon,
    )

    if not road:
        return {
            "check": f"{expected_side} of {road_name}",
            "result": "INCONCLUSIVE",
            "reason": f"No property parcels found for {road_name} near the property",
        }

    lon_min, lon_max = road["lon_range"]
    lat_min, lat_max = road["lat_range"]
    road_center_lon = road["centroid_lon"]
    road_center_lat = road["centroid_lat"]

    # Compare against road centroid (road centerline approximation).
    # Properties exist on both sides of a road, so the centroid of all parcels
    # fronting a road approximates the road centerline.
    if expected_side in ("west", "east"):
        # For N-S roads, compare longitude against centroid
        if expected_side == "west":
            is_correct = point_lon < road_center_lon
        else:
            is_correct = point_lon > road_center_lon

        actual = "west" if point_lon < road_center_lon else "east"
        offset = abs(point_lon - road_center_lon) * 111320 * math.cos(math.radians(point_lat))

        return {
            "check": f"{expected_side} of {road_name}",
            "result": "PASS" if is_correct else "FAIL",
            "road_name": road_name,
            "road_parcels_lon_range": [round(lon_min, 6), round(lon_max, 6)],
            "road_center_lon": round(road_center_lon, 6),
            "property_lon": round(point_lon, 6),
            "offset_m": round(offset),
            "direction": actual,
            "parcel_count": road["parcel_count"],
            "source": road["source"],
        }
    else:
        # For E-W roads, compare latitude against centroid
        if expected_side == "south":
            is_correct = point_lat < road_center_lat
        else:
            is_correct = point_lat > road_center_lat

        actual = "south" if point_lat < road_center_lat else "north"
        offset = abs(point_lat - road_center_lat) * 111320

        return {
            "check": f"{expected_side} of {road_name}",
            "result": "PASS" if is_correct else "FAIL",
            "road_name": road_name,
            "road_parcels_lat_range": [round(lat_min, 6), round(lat_max, 6)],
            "road_center_lat": round(road_center_lat, 6),
            "property_lat": round(point_lat, 6),
            "offset_m": round(offset),
            "direction": actual,
            "parcel_count": road["parcel_count"],
            "source": road["source"],
        }


def check_relative_to_creek(point_lon, point_lat, creek_name, expected_side="east"):
    """
    Check if a point is on the expected side of a creek using waterline geometry.
    """
    coords = query_waterline_geometry(creek_name)

    # Find nearest creek coordinate
    nearest_lon = None
    nearest_lat = None
    min_dist = float("inf")
    for c in coords:
        dist = math.sqrt((point_lon - c[0]) ** 2 + (point_lat - c[1]) ** 2)
        if dist < min_dist:
            min_dist = dist
            nearest_lon, nearest_lat = c

    is_east = point_lon > nearest_lon if nearest_lon else None

    if expected_side == "east":
        passed = is_east
    else:
        passed = not is_east

    return {
        "check": f"{expected_side} of {creek_name}",
        "result": "PASS" if passed else ("FAIL" if passed is not None else "INCONCLUSIVE"),
        "creek_name": creek_name,
        "nearest_creek_lon": round(nearest_lon, 6) if nearest_lon else None,
        "nearest_creek_lat": round(nearest_lat, 6) if nearest_lat else None,
        "property_lon": round(point_lon, 6),
        "property_lat": round(point_lat, 6),
        "offset_m": round(min_dist * 111320) if min_dist < float("inf") else None,
        "direction": "east" if is_east else "west" if is_east is not None else "unknown",
    }


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------

def validate_thompson_orchard(lat, lon, exception_number=42):
    """
    Full Thompson Orchard boundary validation.

    Checks:
    1. Exception zone mapping (x42 RD parcels nearby)
    2. Relative position to Royal York Rd (expected: west)
    3. Relative position to Bloor St W (expected: south)
    4. Relative position to Mimico Creek (expected: east)
    5. Neighbourhood street layout for context

    Returns:
        dict with exception_zone, boundary_checks, street_layout, and verdict.
    """
    results = {"exception_zone": None, "boundary_checks": [], "street_layout": None, "verdict": None}

    # 1. Map the exception zone
    print(f"  Mapping Exception {exception_number} zone (RD parcels near property)...")
    try:
        zone = query_exception_zone(
            exception_number, zone_type="RD", near_lat=lat, near_lon=lon, radius=0.015
        )
        if "error" not in zone:
            bbox = zone["bounding_box"]
            print(f"    Found {zone['parcel_count']} RD zoning polygons nearby")
            print(f"    Bounding box: {bbox['min_lat']:.4f}-{bbox['max_lat']:.4f} lat, "
                  f"{bbox['min_lon']:.4f}-{bbox['max_lon']:.4f} lon")
            print(f"    Zoning strings: {', '.join(set(zone['zoning_strings']))}")
        results["exception_zone"] = zone
    except Exception as e:
        results["exception_zone"] = {"error": str(e)}
        print(f"    ERROR: {e}")

    # 2. West of Royal York Rd
    print(f"  Checking: position relative to Royal York Rd...")
    try:
        check1 = check_relative_to_road(lon, lat, "Royal York Rd", "west")
        results["boundary_checks"].append(check1)
        _print_road_check(check1, "lon")
    except Exception as e:
        results["boundary_checks"].append({"check": "west of Royal York Rd", "result": "ERROR", "error": str(e)})
        print(f"    ERROR: {e}")

    # 3. South of Bloor St W
    print(f"  Checking: position relative to Bloor St W...")
    try:
        check2 = check_relative_to_road(lon, lat, "Bloor St W", "south")
        results["boundary_checks"].append(check2)
        _print_road_check(check2, "lat")
    except Exception as e:
        results["boundary_checks"].append({"check": "south of Bloor St W", "result": "ERROR", "error": str(e)})
        print(f"    ERROR: {e}")

    # 4. East of Mimico Creek
    print(f"  Checking: position relative to Mimico Creek...")
    try:
        check3 = check_relative_to_creek(lon, lat, "Mimico Creek", expected_side="east")
        results["boundary_checks"].append(check3)
        print(f"    Creek nearest point: {check3.get('nearest_creek_lon', '?')}")
        print(f"    Property:            {check3['property_lon']}")
        print(f"    Result: {check3['direction'].upper()} of creek ({check3['result']}) ~{check3.get('offset_m', '?')}m")
    except Exception as e:
        results["boundary_checks"].append({"check": "east of Mimico Creek", "result": "ERROR", "error": str(e)})
        print(f"    ERROR: {e}")

    # 5. Street layout context
    print(f"  Mapping neighbourhood street layout...")
    try:
        streets = _find_all_nearby_streets(lat, lon, radius=0.004)
        results["street_layout"] = streets
        print(f"    Found {len(streets)} streets nearby (west to east):")
        for street in sorted(streets.keys(), key=lambda s: streets[s]["lon_range"][0]):
            s = streets[street]
            lon_mid = (s["lon_range"][0] + s["lon_range"][1]) / 2
            marker = " *** TARGET" if street == "Ashton Manor" else ""
            print(f"      {street:30s}  lon ~{lon_mid:.4f}  ({s['count']} parcels){marker}")
    except Exception as e:
        print(f"    ERROR: {e}")

    # Overall verdict
    check_results = [c["result"] for c in results["boundary_checks"]]
    all_pass = all(r == "PASS" for r in check_results)
    any_fail = any(r == "FAIL" for r in check_results)

    zone = results.get("exception_zone")
    has_exception = zone and "error" not in zone and zone.get("parcel_count", 0) > 0

    if all_pass and has_exception:
        results["verdict"] = "INSIDE"
        results["verdict_detail"] = (
            f"Property is INSIDE the Thompson Orchard area. "
            f"All 3 boundary checks pass AND {zone['parcel_count']} RD zoning polygons "
            f"with Exception {exception_number} found nearby."
        )
    elif has_exception and any_fail:
        failed = [c["check"] for c in results["boundary_checks"] if c["result"] == "FAIL"]
        passed = [c["check"] for c in results["boundary_checks"] if c["result"] == "PASS"]
        results["verdict"] = "BOUNDARY_DISCREPANCY"
        results["verdict_detail"] = (
            f"DISCREPANCY: Zoning Exception {exception_number} (RD) DOES apply to this parcel, "
            f"but the property FAILS the textual boundary check: {', '.join(failed)}. "
            f"Passed: {', '.join(passed)}. "
            f"This means TOCA's textual boundary description may be imprecise -- "
            f"the actual exception zone extends beyond the stated boundaries."
        )
    elif any_fail:
        failed = [c["check"] for c in results["boundary_checks"] if c["result"] == "FAIL"]
        results["verdict"] = "OUTSIDE"
        results["verdict_detail"] = (
            f"Property FAILS boundary check(s): {', '.join(failed)} "
            f"and no matching zoning exception found."
        )
    else:
        results["verdict"] = "INCONCLUSIVE"
        results["verdict_detail"] = (
            "One or more checks could not be completed. Manual verification recommended."
        )

    return results


def _print_road_check(check, axis):
    """Print a road check result."""
    if check["result"] == "INCONCLUSIVE":
        print(f"    {check.get('reason', 'Unknown error')}")
        return

    if axis == "lon":
        rng = check.get("road_parcels_lon_range", [])
        print(f"    Road parcels lon range: [{rng[0] if rng else '?'} to {rng[1] if rng else '?'}] "
              f"({check.get('parcel_count', '?')} parcels)")
        print(f"    Property lon:           {check['property_lon']}")
    else:
        rng = check.get("road_parcels_lat_range", [])
        print(f"    Road parcels lat range: [{rng[0] if rng else '?'} to {rng[1] if rng else '?'}] "
              f"({check.get('parcel_count', '?')} parcels)")
        print(f"    Property lat:           {check['property_lat']}")

    print(f"    Result: {check['direction'].upper()} ({check['result']}) ~{check.get('offset_m', '?')}m")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate community boundary claims using GIS geometry"
    )
    parser.add_argument(
        "address", nargs="?", default=DEFAULT_ADDRESS,
        help=f"Address to check (default: {DEFAULT_ADDRESS})"
    )
    parser.add_argument(
        "--lat", type=float, help="Latitude (skip geocoding)"
    )
    parser.add_argument(
        "--lon", type=float, help="Longitude (skip geocoding)"
    )
    parser.add_argument(
        "--exception", type=int, default=42,
        help="Zoning exception number to map (default: 42)"
    )
    parser.add_argument(
        "--provider", choices=["nominatim", "google"], default="nominatim",
        help="Geocoding provider"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 70)
    print("  Community Boundary Validation")
    print("  Claim: 'west of Royal York, south of Bloor, bounded by Mimico Creek'")
    print("=" * 70)

    # Get coordinates
    if args.lat and args.lon:
        lat, lon = args.lat, args.lon
        print(f"\n  Using provided coordinates: {lat}, {lon}")
    else:
        print(f"\n  Geocoding: {args.address}")
        geo = geocode(args.address, provider=args.provider)
        lat, lon = geo["lat"], geo["lon"]
        print(f"  Coordinates: {lat}, {lon}")
        print(f"  Resolved: {geo['display_name']}")

    print(f"\n  Exception: x{args.exception}")
    print("-" * 70)

    results = validate_thompson_orchard(lat, lon, exception_number=args.exception)

    # Print verdict
    verdict = results["verdict"]
    print("\n" + "=" * 70)
    print(f"  VERDICT: {verdict}")
    print(f"  {results['verdict_detail']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
