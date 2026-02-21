"""
Convert community boundary descriptions to geographic polygons.

Takes a JSON boundary description (street names, waterways, compass directions)
and constructs a real polygon using Toronto's ArcGIS REST API geometries.

Two approaches:
  A) Boundary Lines -> Polygon: fetch road/waterway geometries, clip, polygonize
  B) Zoning Exception Union: union all zoning parcels with a given exception number

Usage:
    python community_polygon.py ../examples/thompson_orchard.json
    python community_polygon.py ../examples/thompson_orchard.json --approach both
    python community_polygon.py ../examples/thompson_orchard.json --approach zoning
    python community_polygon.py ../examples/thompson_orchard.json --address "9 Ashton Manor, Etobicoke, ON"
"""

import json
import argparse
import os
import sys
from datetime import datetime

import numpy as np
from shapely.geometry import (
    LineString, MultiLineString, Polygon, MultiPolygon, Point, box, mapping,
)
from shapely.ops import linemerge, unary_union, nearest_points

from toronto_gis import _query_where, query_exception_zone
from geocoder import geocode
from config import LAYER_ROAD_CENTRELINE, LAYER_WATERLINE, OUTPUT_DIR


# ---------------------------------------------------------------------------
# Road name normalization
# ---------------------------------------------------------------------------

ROAD_SUFFIX_MAP = {
    "Road": "Rd", "Street": "St", "Avenue": "Ave", "Boulevard": "Blvd",
    "Drive": "Dr", "Crescent": "Cres", "Court": "Ct", "Place": "Pl",
    "Trail": "Tr", "Circle": "Cir", "Gardens": "Gdns", "Terrace": "Terr",
}

ROAD_DIRECTION_MAP = {
    "West": "W", "East": "E", "North": "N", "South": "S",
}

ROAD_NAME_ALIASES = {
    "Royal York Road": "Royal York Rd",
    "Royal York": "Royal York Rd",
    "Bloor Street West": "Bloor St W",
    "Bloor Street": "Bloor St W",
    "Bloor": "Bloor St W",
    "The Kingsway": "The Kingsway",
    "Kingsway": "The Kingsway",
}


def normalize_road_name(name):
    """Normalize a road name to match Toronto ArcGIS LINEAR_NAME_FULL format."""
    if name in ROAD_NAME_ALIASES:
        return ROAD_NAME_ALIASES[name]
    words = name.split()
    normalized = []
    for word in words:
        if word in ROAD_SUFFIX_MAP:
            normalized.append(ROAD_SUFFIX_MAP[word])
        elif word in ROAD_DIRECTION_MAP:
            normalized.append(ROAD_DIRECTION_MAP[word])
        else:
            normalized.append(word)
    return " ".join(normalized)


# ---------------------------------------------------------------------------
# Geometry fetching
# ---------------------------------------------------------------------------

def _make_envelope_params(ref_lat, ref_lon, radius):
    """Create ArcGIS spatial envelope parameters for bounding box queries."""
    return {
        "geometry": f"{ref_lon - radius},{ref_lat - radius},{ref_lon + radius},{ref_lat + radius}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def fetch_road_linestrings(road_name, ref_lat, ref_lon, radius=0.015):
    """
    Fetch road centreline as individual LineString objects per path segment.

    Unlike query_road_geometry() which flattens all coords into one list,
    this preserves segment boundaries so linemerge() can work correctly.
    """
    road_name = normalize_road_name(road_name)
    where = f"LINEAR_NAME_FULL = '{road_name}'"
    extra = _make_envelope_params(ref_lat, ref_lon, radius)

    features = _query_where(
        LAYER_ROAD_CENTRELINE,
        where_clause=where,
        out_fields="LINEAR_NAME_FULL",
        return_geometry=True,
        extra_params=extra,
    )

    if not features:
        raise ValueError(f"No road segments found for: {road_name} "
                         f"near ({ref_lat:.4f}, {ref_lon:.4f})")

    lines = []
    for f in features:
        for path in f.get("geometry", {}).get("paths", []):
            if len(path) >= 2:
                lines.append(LineString(path))
    return lines


def fetch_waterline_linestrings(waterline_name, ref_lat=None, ref_lon=None, radius=0.02):
    """
    Fetch waterline geometry as individual LineString objects per path segment.
    """
    where = f"WATERLINE_NAME = '{waterline_name}'"
    extra = None
    if ref_lat is not None and ref_lon is not None:
        extra = _make_envelope_params(ref_lat, ref_lon, radius)

    features = _query_where(
        LAYER_WATERLINE,
        where_clause=where,
        out_fields="WATERLINE_NAME",
        return_geometry=True,
        extra_params=extra,
    )

    if not features:
        raise ValueError(f"No waterline segments found for: {waterline_name}")

    lines = []
    for f in features:
        for path in f.get("geometry", {}).get("paths", []):
            if len(path) >= 2:
                lines.append(LineString(path))
    return lines


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def fetch_waterline_overpass(waterline_name, ref_lat, ref_lon, radius=0.02):
    """
    Fetch waterway geometry from OpenStreetMap via the Overpass API.
    Used as fallback when Toronto ArcGIS waterline data is too sparse.

    Free, no API key required. Returns much more complete creek/river geometry.
    Tries multiple Overpass endpoints for reliability.
    """
    import requests
    import time

    bbox = f"{ref_lat - radius},{ref_lon - radius},{ref_lat + radius},{ref_lon + radius}"
    query = f"""[out:json][timeout:60];
(
  way["name"~"{waterline_name}"]["waterway"]({bbox});
);
out geom;"""

    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = requests.get(endpoint, params={"data": query}, timeout=45)
            resp.raise_for_status()
            data = resp.json()

            lines = []
            for element in data.get("elements", []):
                if element.get("type") == "way" and "geometry" in element:
                    coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
                    if len(coords) >= 2:
                        lines.append(LineString(coords))
            return lines
        except Exception as e:
            last_error = e
            print(f"    Overpass endpoint {endpoint.split('/')[2]} failed: {e}")
            time.sleep(2)

    raise last_error or ValueError("All Overpass endpoints failed")


SPARSE_THRESHOLD_M = 200  # boundaries shorter than this trigger Overpass fallback


def fetch_boundary_geometry(boundary, ref_lat, ref_lon, search_radius=0.015):
    """
    Fetch real GIS geometry for a single boundary edge.

    For waterways: tries Toronto ArcGIS first, falls back to Overpass API
    if the ArcGIS data is too sparse (< 200m total geometry).

    Returns:
        list of shapely.geometry.LineString
    """
    ftype = boundary["feature_type"]
    fname = boundary["feature_name"]

    if ftype == "street":
        return fetch_road_linestrings(fname, ref_lat, ref_lon, radius=search_radius)
    elif ftype == "waterway":
        # Try ArcGIS first
        try:
            lines = fetch_waterline_linestrings(fname, ref_lat, ref_lon, radius=search_radius + 0.01)
        except ValueError:
            lines = []

        # Check if ArcGIS data is sufficient
        total_length = sum(l.length for l in lines) * 111320 if lines else 0
        if total_length < SPARSE_THRESHOLD_M:
            print(f"    ArcGIS waterline sparse ({total_length:.0f}m). "
                  f"Trying Overpass API...")
            try:
                osm_lines = fetch_waterline_overpass(fname, ref_lat, ref_lon, radius=search_radius + 0.01)
                if osm_lines:
                    osm_length = sum(l.length for l in osm_lines) * 111320
                    print(f"    Overpass returned {len(osm_lines)} segments, ~{osm_length:.0f}m")
                    return osm_lines
            except Exception as e:
                print(f"    Overpass fallback failed: {e}")

        if not lines:
            raise ValueError(f"No waterline segments found for: {fname}")
        return lines
    else:
        raise ValueError(f"Unsupported feature type: {ftype}")


# ---------------------------------------------------------------------------
# Line processing helpers
# ---------------------------------------------------------------------------

def _line_substring(line, start_dist, end_dist, num_points=200):
    """Extract a sub-segment of a LineString between two distances along it."""
    if start_dist > end_dist:
        start_dist, end_dist = end_dist, start_dist
    distances = np.linspace(start_dist, end_dist, num_points)
    points = [line.interpolate(d) for d in distances]
    coords = [(p.x, p.y) for p in points]
    return LineString(coords)


def _merge_and_select(linestrings, clip_box=None):
    """
    Merge line segments and select the components most relevant to the area.

    Args:
        linestrings: list of LineString objects
        ref_point: shapely Point (reference location)
        clip_box: optional shapely Polygon to clip to

    Returns:
        LineString or MultiLineString of relevant segments
    """
    if not linestrings:
        return None

    # Clip to bounding box if provided
    if clip_box:
        clipped = []
        for ls in linestrings:
            intersection = ls.intersection(clip_box)
            if not intersection.is_empty:
                if intersection.geom_type == "LineString":
                    clipped.append(intersection)
                elif intersection.geom_type == "MultiLineString":
                    clipped.extend(intersection.geoms)
        linestrings = clipped if clipped else linestrings

    # Merge connected segments
    merged = linemerge(MultiLineString(linestrings))

    # If still MultiLineString, keep all components (don't discard any)
    return merged


def _geocode_intersection(road_a, road_b, city="Toronto, ON"):
    """
    Geocode a road intersection to get a reliable corner point.
    Reuses the approach from boundary_check.py.

    Returns:
        shapely.geometry.Point (lon, lat) or None
    """
    from geocoder import geocode_nominatim

    queries = [
        f"{road_a} & {road_b}, {city}",
        f"{road_a} at {road_b}, {city}",
    ]
    for query in queries:
        try:
            result = geocode_nominatim(query)
            return Point(result["lon"], result["lat"])
        except Exception:
            continue
    return None


def _find_corner(boundary_i, boundary_j, line_i, line_j):
    """
    Find the corner point between two adjacent boundaries.

    Strategy depends on the boundary types:
    - street x street: geocode the intersection (most reliable)
    - street x waterway: actual geometric intersection, or nearest points
    - waterway x waterway: nearest points

    Returns:
        (Point, distance_m, method) or (None, None, None)
    """
    type_i = boundary_i["feature_type"]
    type_j = boundary_j["feature_type"]
    name_i = boundary_i["feature_name"]
    name_j = boundary_j["feature_name"]

    # For street x street: geocode the intersection
    if type_i == "street" and type_j == "street":
        pt = _geocode_intersection(name_i, name_j)
        if pt:
            return pt, 0, "geocoded"

    # Try actual geometric intersection
    ix = line_i.intersection(line_j)
    if not ix.is_empty:
        if ix.geom_type == "Point":
            return ix, 0, "intersection"
        elif hasattr(ix, 'centroid'):
            return ix.centroid, 0, "intersection"

    # Fallback: nearest points
    p1, p2 = nearest_points(line_i, line_j)
    dist_deg = p1.distance(p2)
    dist_m = dist_deg * 111320
    if dist_deg <= 0.01:  # ~1.1km tolerance
        midpoint = Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)
        return midpoint, dist_m, "nearest"

    return None, None, None


# ---------------------------------------------------------------------------
# Approach A: Boundary Lines -> Polygon
# ---------------------------------------------------------------------------

def construct_from_boundaries(description, ref_lat, ref_lon):
    """
    Approach A: Construct polygon from boundary line geometries.

    Algorithm:
    1. Fetch all boundary geometries from ArcGIS
    2. Merge segments per boundary
    3. For each adjacent pair of boundaries, find their "corner" point
       (actual intersection or nearest-point midpoint)
    4. Clip each boundary to the segment between its two corners
    5. Assemble clipped segments into a closed polygon ring

    Returns:
        (polygon, boundary_lines) where boundary_lines is list of
        (LineString, meta_dict) tuples for visualization
    """
    boundaries = description["boundaries"]
    reference_point = Point(ref_lon, ref_lat)

    print("\n  [Approach A: Boundary Lines -> Polygon]")

    # Create a working bounding box (generous: 2km radius)
    work_box = box(ref_lon - 0.02, ref_lat - 0.02, ref_lon + 0.02, ref_lat + 0.02)

    # Fetch and merge geometries
    merged_lines = []
    viz_lines = []
    for b in boundaries:
        print(f"  Fetching {b['feature_type']}: {b['feature_name']}...")
        try:
            linestrings = fetch_boundary_geometry(b, ref_lat, ref_lon, search_radius=0.02)
            print(f"    Got {len(linestrings)} segments")
            merged = _merge_and_select(linestrings, clip_box=work_box)
            total_length_m = merged.length * 111320 if merged else 0
            print(f"    Merged: {merged.geom_type}, ~{total_length_m:.0f}m")
            merged_lines.append(merged)
            viz_lines.append((merged, b))
        except Exception as e:
            print(f"    WARNING: {e}")
            merged_lines.append(None)
            viz_lines.append(None)

    # Check which boundaries have usable geometry
    usable = [(i, ml) for i, ml in enumerate(merged_lines) if ml is not None and ml.length > 0.0001]
    if len(usable) < 2:
        raise ValueError(f"Only {len(usable)} boundaries have usable geometry. Need at least 2.")

    sparse_threshold = 0.001  # ~111m - boundaries shorter than this are "sparse"
    for i, ml in enumerate(merged_lines):
        if ml and ml.length < sparse_threshold:
            print(f"    WARNING: {boundaries[i]['feature_name']} has sparse geometry "
                  f"({ml.length * 111320:.0f}m). Will use available points.")

    # Find "corner" points between adjacent boundary pairs
    # Boundaries are ordered around the perimeter, so boundary[i] and boundary[i+1] share a corner
    n = len(boundaries)
    corners = []
    print("\n  Finding boundary corners...")
    for i in range(n):
        j = (i + 1) % n
        line_i = merged_lines[i]
        line_j = merged_lines[j]

        if line_i is None or line_j is None:
            corners.append(None)
            print(f"    Corner {boundaries[i]['feature_name']} x "
                  f"{boundaries[j]['feature_name']}: MISSING (no geometry)")
            continue

        corner, dist_m, method = _find_corner(
            boundaries[i], boundaries[j], line_i, line_j
        )

        if corner:
            corners.append(corner)
            print(f"    Corner {boundaries[i]['feature_name']} x "
                  f"{boundaries[j]['feature_name']}: "
                  f"({corner.y:.6f}, {corner.x:.6f}) "
                  f"[{method}] gap={dist_m:.0f}m")
        else:
            corners.append(None)
            print(f"    Corner {boundaries[i]['feature_name']} x "
                  f"{boundaries[j]['feature_name']}: NOT FOUND")

    # Build polygon ring: for each boundary, extract the segment between its two corners
    # corners[i] is the corner between boundary[i] and boundary[i+1]
    # So boundary[i] goes from corners[i-1] to corners[i]
    print("\n  Building polygon ring...")
    ring_segments = []

    for i in range(n):
        prev_corner = corners[(i - 1) % n]
        next_corner = corners[i]
        line = merged_lines[i]

        if prev_corner is None or next_corner is None:
            if prev_corner and next_corner:
                # One corner missing, draw straight line
                ring_segments.append(LineString([
                    (prev_corner.x, prev_corner.y),
                    (next_corner.x, next_corner.y),
                ]))
            print(f"    {boundaries[i]['feature_name']}: skipped (missing corner)")
            continue

        if line is None or line.length < 0.0001:
            # Sparse or missing geometry: straight line between corners
            ring_segments.append(LineString([
                (prev_corner.x, prev_corner.y),
                (next_corner.x, next_corner.y),
            ]))
            print(f"    {boundaries[i]['feature_name']}: straight line (sparse geometry)")
            continue

        # Project corners onto the boundary line and extract sub-segment
        d_start = line.project(prev_corner)
        d_end = line.project(next_corner)

        if abs(d_start - d_end) < 0.00001:
            # Corners project to same point - use straight line
            ring_segments.append(LineString([
                (prev_corner.x, prev_corner.y),
                (next_corner.x, next_corner.y),
            ]))
            print(f"    {boundaries[i]['feature_name']}: straight line (corners too close on line)")
            continue

        segment = _line_substring(line, d_start, d_end, num_points=100)

        # Ensure segment starts at prev_corner and ends at next_corner
        # (the substring might go in wrong direction along the line)
        seg_start = Point(segment.coords[0])
        if seg_start.distance(next_corner) < seg_start.distance(prev_corner):
            # Segment is reversed - flip it
            segment = LineString(list(reversed(segment.coords)))

        # Force-connect to exact corner points
        coords = list(segment.coords)
        coords[0] = (prev_corner.x, prev_corner.y)
        coords[-1] = (next_corner.x, next_corner.y)
        segment = LineString(coords)

        ring_segments.append(segment)
        seg_len = segment.length * 111320
        print(f"    {boundaries[i]['feature_name']}: {len(coords)} pts, ~{seg_len:.0f}m")

    if not ring_segments:
        raise ValueError("No ring segments could be constructed")

    # Assemble ring from segments
    all_coords = []
    for seg in ring_segments:
        coords = list(seg.coords)
        if all_coords:
            # Skip first point if it's the same as the last (they share a corner)
            all_coords.extend(coords[1:])
        else:
            all_coords.extend(coords)

    # Close the ring
    if all_coords[0] != all_coords[-1]:
        all_coords.append(all_coords[0])

    try:
        polygon = Polygon(all_coords)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        # buffer(0) can produce MultiPolygon - take largest
        if isinstance(polygon, MultiPolygon):
            polygon = max(polygon.geoms, key=lambda p: p.area)
    except Exception as e:
        raise ValueError(f"Failed to create polygon: {e}")

    contains_ref = polygon.contains(reference_point)
    area_km2 = polygon.area * (111.32 ** 2)
    print(f"\n  Polygon area: ~{area_km2:.3f} km^2")
    print(f"  Reference point inside: {'YES' if contains_ref else 'NO'}")

    # Filter viz_lines to only usable ones
    viz_lines = [(ml, b) for ml, b in zip(merged_lines, boundaries) if ml is not None]

    return polygon, viz_lines


# ---------------------------------------------------------------------------
# Approach B: Zoning Exception Union
# ---------------------------------------------------------------------------

def construct_from_zoning_exception(exc_number, zone_type, ref_lat, ref_lon, radius=0.015):
    """
    Approach B: Construct polygon by unioning all zoning parcels with a given exception.
    """
    print(f"\n  [Approach B: Zoning Exception Union (x{exc_number} {zone_type})]")

    zone_data = query_exception_zone(exc_number, zone_type, ref_lat, ref_lon, radius)

    if "error" in zone_data:
        raise ValueError(f"No parcels found: {zone_data['error']}")

    parcel_polygons = []
    for feature in zone_data["features"]:
        rings = feature.get("geometry", {}).get("rings", [])
        if rings:
            exterior = rings[0]
            holes = rings[1:] if len(rings) > 1 else []
            try:
                p = Polygon(exterior, holes)
                if not p.is_valid:
                    p = p.buffer(0)
                if isinstance(p, MultiPolygon):
                    parcel_polygons.extend(p.geoms)
                elif p.area > 0:
                    parcel_polygons.append(p)
            except Exception:
                continue

    if not parcel_polygons:
        raise ValueError("No valid parcel polygons could be constructed")

    print(f"    Parcels found: {len(parcel_polygons)}")
    community_polygon = unary_union(parcel_polygons)

    if not community_polygon.is_valid:
        community_polygon = community_polygon.buffer(0)

    # Ensure we return a Polygon (not MultiPolygon)
    if isinstance(community_polygon, MultiPolygon):
        # Take the largest polygon, or the one containing the reference point
        ref = Point(ref_lon, ref_lat)
        for geom in community_polygon.geoms:
            if geom.contains(ref):
                community_polygon = geom
                break
        else:
            community_polygon = max(community_polygon.geoms, key=lambda p: p.area)

    area_km2 = community_polygon.area * (111.32 ** 2)
    print(f"    Union area: ~{area_km2:.3f} km^2")
    print(f"    Reference point inside: "
          f"{'YES' if community_polygon.contains(Point(ref_lon, ref_lat)) else 'NO'}")

    return community_polygon, len(parcel_polygons)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_geojson(polygons_data, boundary_lines, metadata, output_path):
    """Export as GeoJSON FeatureCollection."""
    features = []

    for polygon, label, source in polygons_data:
        features.append({
            "type": "Feature",
            "properties": {
                "name": label,
                "source": source,
                "area_deg2": polygon.area,
            },
            "geometry": mapping(polygon),
        })

    for line, bmeta in boundary_lines:
        features.append({
            "type": "Feature",
            "properties": {
                "name": bmeta["feature_name"],
                "feature_type": bmeta["feature_type"],
                "compass_direction": bmeta.get("compass_direction", ""),
                "layer": "boundary_line",
            },
            "geometry": mapping(line),
        })

    geojson = {"type": "FeatureCollection", "features": features}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2)

    return output_path


def _polygon_to_kml_coords(polygon):
    """Convert a Polygon or MultiPolygon to KML coordinate lists."""
    if isinstance(polygon, MultiPolygon):
        polys = list(polygon.geoms)
    else:
        polys = [polygon]
    return polys


def export_kml(polygons_data, metadata, output_path):
    """Export polygon(s) to KML format."""
    try:
        import simplekml
        kml = simplekml.Kml()

        for polygon, label, source in polygons_data:
            for poly in _polygon_to_kml_coords(polygon):
                coords_lonlat = list(poly.exterior.coords)
                kml_coords = [(c[0], c[1], 0) for c in coords_lonlat]
                pol = kml.newpolygon(name=label)
                pol.outerboundaryis = kml_coords
                pol.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.blue)
                pol.style.linestyle.width = 2

        kml.save(output_path)

    except ImportError:
        _export_kml_manual(polygons_data, output_path)

    return output_path


def _export_kml_manual(polygons_data, output_path):
    """Write minimal KML XML directly (no simplekml dependency)."""
    placemarks = []
    for polygon, label, source in polygons_data:
        for poly in _polygon_to_kml_coords(polygon):
            coords = " ".join(f"{c[0]},{c[1]},0" for c in poly.exterior.coords)
            placemarks.append(f"""    <Placemark>
      <name>{label}</name>
      <description>Source: {source}</description>
      <Style>
        <PolyStyle><color>64ff0000</color></PolyStyle>
        <LineStyle><color>ff0000ff</color><width>2</width></LineStyle>
      </Style>
      <Polygon>
        <outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark>""")

    kml_str = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Community Polygons</name>
{"".join(placemarks)}
  </Document>
</kml>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(kml_str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert community boundary descriptions to geographic polygons"
    )
    parser.add_argument(
        "input",
        help="Path to boundary description JSON file"
    )
    parser.add_argument(
        "--approach", choices=["lines", "zoning", "both"], default="both",
        help="Polygon construction approach (default: both)"
    )
    parser.add_argument(
        "--address",
        help="Reference address inside the community (overrides JSON reference_point)"
    )
    parser.add_argument(
        "--lat", type=float,
        help="Reference point latitude (skip geocoding)"
    )
    parser.add_argument(
        "--lon", type=float,
        help="Reference point longitude (skip geocoding)"
    )
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--no-map", action="store_true",
        help="Skip HTML map generation"
    )
    parser.add_argument(
        "--provider", choices=["nominatim", "google"], default="nominatim",
        help="Geocoding provider for reference point"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Load description
    with open(args.input, "r", encoding="utf-8") as f:
        description = json.load(f)

    community_name = description["community_name"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = community_name.lower().replace(" ", "_")

    print("=" * 60)
    print(f"  Community Polygon Builder")
    print("=" * 60)
    print(f"\n  Community: {community_name}")
    print(f"  Description: {description.get('description', 'N/A')}")
    print(f"  Boundaries: {len(description.get('boundaries', []))}")
    print(f"  Approach: {args.approach}")

    # Resolve reference point
    if args.lat and args.lon:
        ref_lat, ref_lon = args.lat, args.lon
        print(f"  Reference point: {ref_lat}, {ref_lon} (provided)")
    elif args.address:
        geo = geocode(args.address, provider=args.provider)
        ref_lat, ref_lon = geo["lat"], geo["lon"]
        print(f"  Reference point: {ref_lat}, {ref_lon} (geocoded from: {args.address})")
    elif "reference_point" in description:
        rp = description["reference_point"]
        if "lat" in rp and "lon" in rp:
            ref_lat, ref_lon = rp["lat"], rp["lon"]
            print(f"  Reference point: {ref_lat}, {ref_lon} (from JSON)")
        elif "address" in rp:
            geo = geocode(rp["address"], provider=args.provider)
            ref_lat, ref_lon = geo["lat"], geo["lon"]
            print(f"  Reference point: {ref_lat}, {ref_lon} (geocoded from: {rp['address']})")
        else:
            print("  ERROR: No reference point provided")
            sys.exit(1)
    else:
        print("  ERROR: No reference point. Use --lat/--lon, --address, or include in JSON.")
        sys.exit(1)

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    polygons_for_export = []
    boundary_lines_for_export = []
    polygon_a = None
    polygon_b = None

    # Approach A: Boundary Lines
    if args.approach in ("lines", "both"):
        try:
            polygon_a, boundary_lines = construct_from_boundaries(description, ref_lat, ref_lon)
            polygons_for_export.append(
                (polygon_a, f"{community_name} (boundary lines)", "approach_a_lines")
            )
            boundary_lines_for_export = boundary_lines
        except Exception as e:
            print(f"\n  Approach A FAILED: {e}")
            import traceback
            traceback.print_exc()
            if args.approach == "lines":
                sys.exit(1)

    # Approach B: Zoning Exception Union
    if args.approach in ("zoning", "both") and "zoning_exception" in description:
        ze = description["zoning_exception"]
        try:
            polygon_b, parcel_count = construct_from_zoning_exception(
                ze["exception_number"], ze["zone_type"], ref_lat, ref_lon
            )
            polygons_for_export.append(
                (polygon_b, f"{community_name} (zoning x{ze['exception_number']})", "approach_b_zoning")
            )
        except Exception as e:
            print(f"\n  Approach B FAILED: {e}")
            if args.approach == "zoning":
                sys.exit(1)

    if not polygons_for_export:
        print("\n  No polygons were constructed. Exiting.")
        sys.exit(1)

    # Compare approaches if both succeeded
    if polygon_a and polygon_b:
        print("\n  [Comparison]")
        try:
            intersection = polygon_a.intersection(polygon_b)
            union_poly = polygon_a.union(polygon_b)
            iou = intersection.area / union_poly.area if union_poly.area > 0 else 0
            print(f"    Approach A area: {polygon_a.area * 111.32**2:.3f} km^2")
            print(f"    Approach B area: {polygon_b.area * 111.32**2:.3f} km^2")
            print(f"    IoU (Intersection/Union): {iou:.3f}")
        except Exception as e:
            print(f"    Comparison failed: {e}")

    # Export GeoJSON
    geojson_path = os.path.join(args.output_dir, f"{base_name}_{timestamp}.geojson")
    export_geojson(polygons_for_export, boundary_lines_for_export,
                   {"community_name": community_name}, geojson_path)
    print(f"\n  GeoJSON: {geojson_path}")

    # Export KML
    kml_path = os.path.join(args.output_dir, f"{base_name}_{timestamp}.kml")
    export_kml(polygons_for_export, {"community_name": community_name}, kml_path)
    print(f"  KML:     {kml_path}")

    # Generate HTML map
    if not args.no_map:
        from community_visualize import create_community_map, save_map

        viz_polygons = []
        colors = ["#3388ff", "#33cc33", "#ff8833"]
        for i, (poly, label, source) in enumerate(polygons_for_export):
            viz_polygons.append((poly, label, colors[i % len(colors)]))

        m = create_community_map(
            viz_polygons,
            boundary_lines=boundary_lines_for_export,
            metadata={
                "community_name": community_name,
                "reference_label": description.get("reference_point", {}).get("address", "Reference"),
            },
            reference_point=(ref_lat, ref_lon),
        )
        map_path = os.path.join(args.output_dir, f"{base_name}_{timestamp}.html")
        save_map(m, map_path)
        print(f"  HTML Map: {map_path}")

    print(f"\n{'=' * 60}")
    print(f"  Done. Open the HTML file in a browser to view the map.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
