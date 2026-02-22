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

def normalize_road_name(name):
    """Normalize a road name to match Toronto ArcGIS LINEAR_NAME_FULL format."""
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
# GIS name resolution
# ---------------------------------------------------------------------------

def _road_orientation(segments):
    """Determine if a set of segments runs predominantly E-W or N-S."""
    total_dx = 0
    total_dy = 0
    for seg in segments:
        coords = list(seg.coords)
        total_dx += abs(coords[-1][0] - coords[0][0])
        total_dy += abs(coords[-1][1] - coords[0][1])
    return "ew" if total_dx > total_dy else "ns"


def _compass_match_score(linestrings, ref_lat, ref_lon, compass_direction):
    """
    Score a candidate road for being the boundary in a given compass direction.

    A good boundary road is:
      - In the correct direction from the reference point (required)
      - Close to the reference point (nearest major road, not farthest)
      - Long (boundaries are major roads/features, not residential streets)
      - Oriented correctly (E-W for north/south boundaries, N-S for east/west)

    Returns a numeric score (higher = better match). Returns -999 if the road
    is in the wrong direction entirely.
    """
    if not linestrings:
        return -999

    combined = MultiLineString(linestrings) if len(linestrings) > 1 else linestrings[0]
    centroid = combined.centroid
    total_length_m = combined.length * 111320

    dlat = centroid.y - ref_lat
    dlon = centroid.x - ref_lon

    # Gate: road must be in the correct compass direction
    in_direction = False
    if compass_direction == "north":
        in_direction = dlat > -0.001
    elif compass_direction == "south":
        in_direction = dlat < 0.001
    elif compass_direction == "east":
        in_direction = dlon > -0.001
    elif compass_direction == "west":
        in_direction = dlon < 0.001
    elif compass_direction in ("west_and_south", "south_and_west"):
        in_direction = (dlat < 0.001 or dlon < 0.001)
    else:
        in_direction = True

    if not in_direction:
        return -999

    # Distance from reference point (rough meters)
    cos_lat = 0.7  # cos(43.6°) ≈ 0.72
    dist_m = ((dlat * 111320) ** 2 + (dlon * 111320 * cos_lat) ** 2) ** 0.5

    # Orientation bonus: E-W roads for N/S boundaries, N-S roads for E/W boundaries
    orient = _road_orientation(linestrings)
    expected_orient = ("ew" if compass_direction in ("north", "south")
                       else "ns" if compass_direction in ("east", "west")
                       else None)
    orientation_bonus = 500 if (expected_orient and orient == expected_orient) else 0

    # Cap length bonus — any road >2km is "substantial enough";
    # prevents long trails from dominating over closer named roads
    length_bonus = min(total_length_m, 2000)

    # Score: reward length (capped) + orientation, penalize distance
    score = length_bonus + orientation_bonus - dist_m

    return score


def resolve_gis_name(approximate_name, feature_type, ref_lat, ref_lon,
                     compass_direction=None, search_radius=0.02):
    """
    Resolve an approximate/colloquial boundary name to the exact GIS field value.

    The JSON feature_name should use the name from the community description
    (e.g. "Bloor", "Royal York") rather than the exact GIS field value.
    This function resolves user-facing names to what the GIS layer actually uses.

    Strategy:
      1. Normalize with road aliases/suffixes, try exact match
      2. If no results, try LIKE query with the base name
      3. If multiple matches, use compass_direction to pick the best one
    """
    if feature_type == "street":
        field = "LINEAR_NAME_FULL"
        layer = LAYER_ROAD_CENTRELINE
        normalized = normalize_road_name(approximate_name)
    elif feature_type == "waterway":
        field = "WATERLINE_NAME"
        layer = LAYER_WATERLINE
        normalized = approximate_name
    else:
        return approximate_name

    envelope = _make_envelope_params(ref_lat, ref_lon, search_radius)

    # Step 1: Try exact match with normalized name
    features = _query_where(
        layer,
        where_clause=f"{field} = '{normalized}'",
        out_fields=field,
        return_geometry=False,
        extra_params=envelope,
    )
    if features:
        if normalized != approximate_name:
            print(f"    Resolved '{approximate_name}' -> '{normalized}'")
        return normalized

    # Step 2: LIKE match — use the first word as the distinctive part
    base = approximate_name.split()[0]
    where_like = f"UPPER({field}) LIKE '%{base.upper()}%'"
    features = _query_where(
        layer,
        where_clause=where_like,
        out_fields=field,
        return_geometry=True,
        extra_params=envelope,
    )

    if not features:
        # Step 3: Compass-based fallback — search ALL roads in the area
        # and pick the one in the expected compass direction.
        # This handles cases where the GIS uses a completely different name
        # (e.g. user says "Bloor" but GIS calls it "The Kingsway").
        if compass_direction:
            print(f"    No name match for '{approximate_name}'. "
                  f"Trying compass-based fallback ({compass_direction})...")
            features = _query_where(
                layer,
                where_clause="1=1",
                out_fields=field,
                return_geometry=True,
                extra_params=envelope,
            )
            if features:
                # Collect by name and score by compass + orientation
                fallback_names = {}
                for f in features:
                    attrs = f.get("attributes", {})
                    name = attrs.get(field) or f.get(field)
                    if not name:
                        continue
                    if name not in fallback_names:
                        fallback_names[name] = []
                    for path in f.get("geometry", {}).get("paths", []):
                        if len(path) >= 2:
                            fallback_names[name].append(LineString(path))

                # Score each candidate by compass direction, proximity,
                # length, and orientation (all handled in _compass_match_score)
                best_name = None
                best_score = -999

                for name, segments in fallback_names.items():
                    if not segments:
                        continue
                    score = _compass_match_score(segments, ref_lat, ref_lon,
                                                compass_direction)
                    if score > best_score:
                        best_score = score
                        best_name = name

                if best_name and best_score > 0:
                    print(f"    Resolved '{approximate_name}' -> '{best_name}' "
                          f"(compass fallback: best '{compass_direction}' match "
                          f"from {list(fallback_names.keys())})")
                    return best_name

        print(f"    WARNING: No GIS features matching '{approximate_name}' "
              f"(tried exact '{normalized}', LIKE '%{base.upper()}%', "
              f"and compass fallback)")
        return normalized

    # Collect unique names with their geometries
    name_segments = {}
    for f in features:
        attrs = f.get("attributes", {})
        name = attrs.get(field) or f.get(field)
        if not name:
            continue
        if name not in name_segments:
            name_segments[name] = []
        for path in f.get("geometry", {}).get("paths", []):
            if len(path) >= 2:
                name_segments[name].append(LineString(path))

    if not name_segments:
        return normalized

    if len(name_segments) == 1:
        resolved = list(name_segments.keys())[0]
        if resolved != approximate_name:
            print(f"    Resolved '{approximate_name}' -> '{resolved}' (LIKE match)")
        return resolved

    # Multiple matches — use compass direction to pick the best candidate
    if compass_direction:
        best_name = None
        best_score = -999
        for name, segments in name_segments.items():
            score = _compass_match_score(segments, ref_lat, ref_lon, compass_direction)
            if score > best_score:
                best_score = score
                best_name = name
        print(f"    Resolved '{approximate_name}' -> '{best_name}' "
              f"(best compass match for '{compass_direction}' from "
              f"{list(name_segments.keys())})")
        return best_name

    # No compass direction — pick the name with most segments nearby
    best = max(name_segments.keys(), key=lambda n: len(name_segments[n]))
    print(f"    Resolved '{approximate_name}' -> '{best}' "
          f"(most segments from {list(name_segments.keys())})")
    return best


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

# Module-level Overpass throttle to respect API rate limits
_last_overpass_time = 0


def _overpass_throttle():
    """Wait if needed to respect Overpass API rate limits (~12s between requests)."""
    global _last_overpass_time
    import time
    now = time.time()
    elapsed = now - _last_overpass_time
    if elapsed < 12:
        wait = 12 - elapsed
        print(f"    (Overpass throttle: waiting {wait:.0f}s...)")
        time.sleep(wait)
    _last_overpass_time = time.time()


def fetch_waterline_overpass(waterline_name, ref_lat, ref_lon, radius=0.02):
    """
    Fetch waterway geometry from OpenStreetMap via the Overpass API.
    Used as fallback when Toronto ArcGIS waterline data is too sparse.

    Free, no API key required. Returns much more complete creek/river geometry.
    Tries multiple Overpass endpoints for reliability.
    """
    import requests
    import time
    _overpass_throttle()

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


def fetch_road_overpass(road_name, ref_lat, ref_lon, radius=0.02):
    """
    Fetch road geometry from OpenStreetMap via the Overpass API.
    Used as fallback when ArcGIS road data is not available (non-Toronto areas).

    Uses case-insensitive regex so colloquial names like "Major MacKenzie"
    match full OSM names like "Major Mackenzie Drive West".

    Groups results by OSM road name and returns only the road with the most
    total geometry. This filters out unrelated roads that match the regex
    (e.g. "North Yonge Boulevard" when searching for "Yonge").
    """
    import requests
    import time
    _overpass_throttle()

    bbox = f"{ref_lat - radius},{ref_lon - radius},{ref_lat + radius},{ref_lon + radius}"
    query = f"""[out:json][timeout:60];
(
  way["name"~"{road_name}",i]["highway"]({bbox});
);
out geom;"""

    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = requests.get(endpoint, params={"data": query}, timeout=45)
            resp.raise_for_status()
            data = resp.json()

            # Group segments by exact OSM road name
            roads = {}  # name -> [LineStrings]
            for element in data.get("elements", []):
                if element.get("type") == "way" and "geometry" in element:
                    coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
                    osm_name = element.get("tags", {}).get("name", "")
                    if len(coords) >= 2:
                        roads.setdefault(osm_name, []).append(LineString(coords))

            if not roads:
                return []

            # Pick the road with the most total geometry — the actual
            # boundary road has more segments than a short side street
            best_name = max(roads.keys(),
                            key=lambda n: sum(l.length for l in roads[n]))
            print(f"    OSM: picked '{best_name}' "
                  f"({len(roads[best_name])} segments from "
                  f"{len(roads)} road names)")
            return roads[best_name]
        except Exception as e:
            last_error = e
            print(f"    Overpass endpoint {endpoint.split('/')[2]} failed: {e}")
            time.sleep(2)

    raise last_error or ValueError("All Overpass endpoints failed")


def fetch_corridor_road_osm(corridor_poly, ref_lat, ref_lon):
    """
    Fetch the boundary road within a corridor polygon (name-free).

    Queries OSM for ALL highway geometry in the corridor's bounding box,
    groups segments by road name, and picks the road that spans the longest
    distance within the corridor (boundary roads run the full length; cross-
    streets only cross briefly). Among similarly-long roads (>50% of max
    span), picks the one closest to the reference point — this naturally
    selects the community-side lane of a dual-carriageway.
    """
    import requests
    import time
    _overpass_throttle()

    bounds = corridor_poly.bounds  # (minx, miny, maxx, maxy)
    bbox = f"{bounds[1]},{bounds[0]},{bounds[3]},{bounds[2]}"
    query = f"""[out:json][timeout:30];
way["highway"~"primary|secondary|tertiary|residential|trunk"]({bbox});
out geom;"""

    ref = Point(ref_lon, ref_lat)
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = requests.get(endpoint, params={"data": query}, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            # Group segments by road name, merge each road's segments
            roads = {}  # name -> list of LineStrings
            for element in data.get("elements", []):
                if element.get("type") == "way" and "geometry" in element:
                    coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
                    name = element.get("tags", {}).get("name", f"unnamed_{element['id']}")
                    if len(coords) >= 2:
                        roads.setdefault(name, []).append(LineString(coords))

            candidates = []
            for name, segments in roads.items():
                merged = linemerge(MultiLineString(segments)) if len(segments) > 1 else segments[0]
                clipped = merged.intersection(corridor_poly)
                if not clipped.is_empty and clipped.length > 0:
                    span = clipped.length * 111320
                    dist = clipped.distance(ref) * 111320
                    candidates.append((clipped, span, dist, name))

            if not candidates:
                return None

            # Pick the road that spans the longest distance in the corridor.
            # Among similarly-long roads (>50% of max), pick closest to ref.
            max_span = max(c[1] for c in candidates)
            long_roads = [c for c in candidates if c[1] > max_span * 0.5]
            best = min(long_roads, key=lambda x: x[2])
            print(f"    OSM corridor: picked '{best[3]}' "
                  f"(span={best[1]:.0f}m, dist={best[2]:.0f}m)")
            result = best[0]
            if result.geom_type == "MultiLineString":
                result = linemerge(result)
            # If still MultiLineString (disconnected segments), pick longest piece
            if result.geom_type == "MultiLineString":
                pieces = list(result.geoms)
                result = max(pieces, key=lambda g: g.length)
            return result if result.geom_type == "LineString" else None

        except Exception as e:
            last_error = e
            print(f"    Overpass endpoint {endpoint.split('/')[2]} failed: {e}")
            time.sleep(2)

    if last_error:
        print(f"    OSM corridor road fetch failed: {last_error}")
    return None


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
        # Try ArcGIS first
        try:
            return fetch_road_linestrings(fname, ref_lat, ref_lon, radius=search_radius)
        except ValueError:
            pass

        # Fallback: OpenStreetMap via Overpass API
        print(f"    ArcGIS has no data for '{fname}'. Trying Overpass API...")
        try:
            osm_lines = fetch_road_overpass(fname, ref_lat, ref_lon,
                                            radius=search_radius + 0.01)
            if osm_lines:
                osm_length = sum(l.length for l in osm_lines) * 111320
                print(f"    Overpass returned {len(osm_lines)} segments, "
                      f"~{osm_length:.0f}m")
                return osm_lines
        except Exception as e:
            print(f"    Overpass fallback failed: {e}")

        raise ValueError(f"No road segments found for: {fname} "
                         f"(ArcGIS + Overpass)")
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


def _apply_corridor_clip(clipped, prev_corner, next_corner, boundary,
                         ring_segments, source="ArcGIS"):
    """Apply corridor-clipped geometry as a boundary segment.
    Returns True if successfully applied, False otherwise."""
    if clipped.geom_type == "MultiLineString":
        clipped = linemerge(clipped)
    if clipped.geom_type != "LineString":
        return False
    # Orient from prev_corner to next_corner
    if (Point(clipped.coords[0]).distance(next_corner)
            < Point(clipped.coords[0]).distance(prev_corner)):
        clipped = LineString(list(reversed(clipped.coords)))
    cc = list(clipped.coords)
    cc[0] = (prev_corner.x, prev_corner.y)
    cc[-1] = (next_corner.x, next_corner.y)
    segment = LineString(cc)
    ring_segments.append(segment)
    print(f"    {boundary['feature_name']}: corridor-clipped from {source} "
          f"({len(cc)} pts, ~{segment.length * 111320:.0f}m)")
    return True


def _chain_segments_spatially(linestrings, compass_direction):
    """
    Chain line segments spatially when linemerge produces too many fragments.

    OSM data has gaps at intersection nodes (10-22m) where divided road lanes
    have separate nodes. linemerge requires exact endpoint matches and fails.
    This fallback sorts segments along the road direction and concatenates
    them, bridging the gaps.

    For divided roads, segments from both directions get interleaved, creating
    a small zigzag (~15m oscillation) that's acceptable for polygon construction.
    """
    if not linestrings:
        return None
    if len(linestrings) == 1:
        return linestrings[0]

    # Determine sort axis from compass direction
    # North/south boundaries run east-west -> sort by centroid longitude (x)
    # East/west boundaries run north-south -> sort by centroid latitude (y)
    sort_by_x = compass_direction in ("north", "south")
    if not sort_by_x and compass_direction not in ("east", "west"):
        # Compound directions: check which axis dominates
        sort_by_x = "north" in compass_direction or "south" in compass_direction

    if sort_by_x:
        sorted_segs = sorted(linestrings, key=lambda ls: ls.centroid.x)
    else:
        sorted_segs = sorted(linestrings, key=lambda ls: ls.centroid.y)

    # Chain: append each segment in the correct orientation
    chain_coords = list(sorted_segs[0].coords)
    for seg in sorted_segs[1:]:
        seg_coords = list(seg.coords)
        chain_end = chain_coords[-1]
        d_start = ((chain_end[0] - seg_coords[0][0])**2 +
                   (chain_end[1] - seg_coords[0][1])**2)**0.5
        d_end = ((chain_end[0] - seg_coords[-1][0])**2 +
                 (chain_end[1] - seg_coords[-1][1])**2)**0.5
        if d_end < d_start:
            seg_coords = list(reversed(seg_coords))
        chain_coords.extend(seg_coords)

    return LineString(chain_coords)


def _merge_and_select(linestrings, clip_box=None, compass_direction=None,
                      ref_lat=None, ref_lon=None):
    """
    Merge line segments and select the components most relevant to the area.

    Args:
        linestrings: list of LineString objects
        clip_box: optional shapely Polygon to clip to
        compass_direction: e.g. "north", "east", "west_and_south" — used to
            filter out segments on the wrong side of the reference point
        ref_lat, ref_lon: reference point coordinates for compass filtering

    Returns:
        LineString or MultiLineString of relevant segments
    """
    if not linestrings:
        return None

    total_raw = sum(ls.length for ls in linestrings) * 111320
    print(f"      [merge] input: {len(linestrings)} segments, ~{total_raw:.0f}m")

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
        total_clip = sum(ls.length for ls in linestrings) * 111320
        print(f"      [merge] after clip_box: {len(linestrings)} segments, ~{total_clip:.0f}m")

    # Filter by compass direction — discard segments on the wrong side
    if compass_direction and ref_lat is not None and ref_lon is not None:
        filtered = _filter_by_compass(linestrings, ref_lat, ref_lon,
                                      compass_direction)
        if filtered:
            total_compass = sum(ls.length for ls in filtered) * 111320
            print(f"      [merge] after compass({compass_direction}): "
                  f"{len(filtered)} segments, ~{total_compass:.0f}m")
            linestrings = filtered
        else:
            print(f"      [merge] compass({compass_direction}): ALL filtered out, keeping original")

    # Merge connected segments
    merged = linemerge(MultiLineString(linestrings))

    # If MultiLineString, select the most relevant component:
    # the longest one within max_dist of the reference point.
    # This drops disconnected far-away segments of the same-named road.
    if (merged.geom_type == "MultiLineString"
            and ref_lat is not None and ref_lon is not None):
        ref = Point(ref_lon, ref_lat)
        # Use clip_box diagonal as max distance (adapts to community size)
        if clip_box:
            bx = clip_box.bounds
            max_dist = max(bx[2] - bx[0], bx[3] - bx[1]) * 111320
        else:
            max_dist = 2000
        candidates = [(g, g.length, g.distance(ref) * 111320)
                      for g in merged.geoms]
        total_merged = sum(g.length for g in merged.geoms)
        max_component = max(g.length for g in merged.geoms)

        print(f"      [merge] after linemerge: {len(candidates)} components, "
              f"max_dist={max_dist:.0f}m")
        # Show top 5 components by length
        by_len = sorted(candidates, key=lambda x: x[1], reverse=True)[:5]
        for g, length, dist in by_len:
            print(f"        ~{length * 111320:.0f}m, dist={dist:.0f}m")

        # If linemerge is badly fragmented (longest < 40% of total),
        # fall back to spatial chaining — common with OSM data where
        # intersection nodes don't share exact coordinates
        if max_component < total_merged * 0.4 and compass_direction:
            print(f"      [merge] fragmented ({max_component/total_merged:.0%} "
                  f"in longest) — using spatial chain")
            merged = _chain_segments_spatially(linestrings, compass_direction)
        else:
            nearby = [(g, length) for g, length, dist in candidates
                      if dist < max_dist]
            if nearby:
                merged = max(nearby, key=lambda x: x[1])[0]
            else:
                # All far away — take the closest
                merged = min(candidates, key=lambda x: x[2])[0]

    return merged


def _filter_by_compass(linestrings, ref_lat, ref_lon, compass_direction):
    """
    Keep only linestrings whose centroid is in the expected compass direction
    relative to the reference point.

    A "north" boundary should have segments north of (higher lat than) the
    reference point. Uses a small margin to avoid cutting segments that
    straddle the boundary.
    """
    margin = 0.003  # ~330m margin

    kept = []
    for ls in linestrings:
        c = ls.centroid

        if compass_direction == "north":
            if c.y >= ref_lat - margin:
                kept.append(ls)
        elif compass_direction == "south":
            if c.y <= ref_lat + margin:
                kept.append(ls)
        elif compass_direction == "east":
            if c.x >= ref_lon - margin:
                kept.append(ls)
        elif compass_direction == "west":
            if c.x <= ref_lon + margin:
                kept.append(ls)
        elif compass_direction in ("west_and_south", "south_and_west"):
            # Keep segments west OR south of reference
            if c.x <= ref_lon + margin or c.y <= ref_lat + margin:
                kept.append(ls)
        else:
            kept.append(ls)  # Unknown direction — keep everything

    return kept if kept else None  # Return None if everything filtered out


def _geocode_intersection_all(road_a, road_b, city="Toronto, ON"):
    """
    Geocode a road intersection using all available geocoders.
    Returns a list of candidate Points (may be empty).
    Deduplicates points within 50m of each other.
    """
    from geocoder import geocode_nominatim, geocode_google
    from config import GOOGLE_MAPS_API_KEY

    candidates = []
    queries = [
        f"{road_a} & {road_b}, {city}",
        f"{road_b} & {road_a}, {city}",
    ]

    for query in queries:
        # Try Google
        if GOOGLE_MAPS_API_KEY:
            try:
                result = geocode_google(query)
                candidates.append(Point(result["lon"], result["lat"]))
            except Exception:
                pass
        # Try Nominatim
        try:
            result = geocode_nominatim(query)
            candidates.append(Point(result["lon"], result["lat"]))
        except Exception:
            pass

    # Also try "at" format for Nominatim
    for query in [f"{road_a} at {road_b}, {city}", f"{road_b} at {road_a}, {city}"]:
        try:
            result = geocode_nominatim(query)
            candidates.append(Point(result["lon"], result["lat"]))
        except Exception:
            pass

    # Deduplicate — keep only points >50m apart
    unique = []
    for pt in candidates:
        is_dup = False
        for existing in unique:
            if pt.distance(existing) * 111320 < 50:
                is_dup = True
                break
        if not is_dup:
            unique.append(pt)

    return unique


def _find_corner(boundary_i, boundary_j, line_i, line_j, city="Toronto, ON"):
    """
    Find the corner point between two adjacent boundaries.

    Strategy (tried in order):
    1. Geocode the intersection, then VALIDATE by snapping to both geometries.
    2. Actual geometric intersection of the two line geometries.
    3. Extrapolate endpoints: extend each line along its trajectory and
       intersect the extensions. Handles bridges/valleys where GIS centrelines
       diverge at different elevations.
    4. Nearest points fallback (with gap tolerance).

    Returns:
        (Point, distance_m, method) or (None, None, None)
    """
    type_i = boundary_i["feature_type"]
    type_j = boundary_j["feature_type"]
    name_i = boundary_i["feature_name"]
    name_j = boundary_j["feature_name"]
    # Original user-facing names (before GIS resolution) — better for geocoding
    orig_i = boundary_i.get("_original_name", name_i)
    orig_j = boundary_j.get("_original_name", name_j)

    # Strategy 1: Geocode with original user-facing names first.
    # User-facing names (e.g. "Bloor St W") geocode to the correct
    # intersection, while GIS-internal names (e.g. "The Kingsway") can
    # geocode to a different location on the same road.
    name_groups = []
    if orig_i != name_i or orig_j != name_j:
        name_groups.append(("original", orig_i, orig_j))
    name_groups.append(("resolved", name_i, name_j))

    for group_label, gi, gj in name_groups:
        points = _geocode_intersection_all(gi, gj, city=city)

        for pt in points:
            snap_i = line_i.interpolate(line_i.project(pt))
            snap_j = line_j.interpolate(line_j.project(pt))
            di = pt.distance(snap_i) * 111320
            dj = pt.distance(snap_j) * 111320

            if max(di, dj) < 500:
                corner = Point((snap_i.x + snap_j.x) / 2,
                               (snap_i.y + snap_j.y) / 2)
                gap_m = snap_i.distance(snap_j) * 111320
                return corner, gap_m, "geocoded+snapped"

            if min(di, dj) < 200:
                closer = snap_i if di < dj else snap_j
                gap_m = max(di, dj)
                return closer, gap_m, "geocoded+partial"

        # If original names gave any geocode results (even bad ones),
        # don't fall through to resolved names — the original names
        # represent the user's intent and resolved-name geocodes can
        # return a different (wrong) intersection entirely.
        if points and group_label == "original":
            break

    # Strategy 2: Actual geometric intersection
    ix = line_i.intersection(line_j)
    if not ix.is_empty:
        if ix.geom_type == "Point":
            return ix, 0, "intersection"
        elif hasattr(ix, 'centroid'):
            return ix.centroid, 0, "intersection"

    # Strategy 3: Extrapolate endpoints to find projected intersection
    # Handles cases where roads cross at different elevations (bridge/valley)
    # or where GIS centrelines have large gaps at intersections.
    corner = _extrapolate_corner(line_i, line_j)
    if corner:
        # Compute gap: distance from the projected corner to both original lines
        d_i = line_i.distance(corner) * 111320
        d_j = line_j.distance(corner) * 111320
        gap = max(d_i, d_j)
        if gap < 2000:  # Reasonable extrapolation
            return corner, gap, "extrapolated"

    # Strategy 4: Nearest points fallback
    best_pt = None
    best_gap = float('inf')

    for line_a, line_b in [(line_i, line_j), (line_j, line_i)]:
        for ep in _get_endpoints(line_a):
            snap = line_b.interpolate(line_b.project(ep))
            gap = ep.distance(snap) * 111320
            if gap < best_gap:
                best_gap = gap
                best_pt = Point((ep.x + snap.x) / 2, (ep.y + snap.y) / 2)

    p1, p2 = nearest_points(line_i, line_j)
    gap_np = p1.distance(p2) * 111320
    if gap_np < best_gap:
        best_gap = gap_np
        best_pt = Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)

    if best_pt and best_gap <= 1200:
        return best_pt, best_gap, "nearest"

    return None, None, None


def _extrapolate_corner(line_i, line_j, extension_m=2000):
    """
    Find where two non-intersecting lines would meet if extended.

    For each line, takes the endpoint closest to the other line and
    extends it along its trajectory. Returns the intersection of the
    two extended lines if found.

    This handles cases where:
    - Roads cross at different elevations (bridge/underpass)
    - GIS centrelines have large gaps at intersections (100-1500m)
    - A waterway goes underground near a road crossing
    """
    ext_deg = extension_m / 111320  # rough conversion

    # Find the pair of "facing" endpoints — each line's endpoint closest to the other
    best_pairs = []
    for ep_i in _get_endpoints(line_i):
        d = line_j.distance(ep_i)
        best_pairs.append((d, ep_i, 'i'))
    for ep_j in _get_endpoints(line_j):
        d = line_i.distance(ep_j)
        best_pairs.append((d, ep_j, 'j'))

    # Get the closest endpoint from each line
    facing_i = min((d, ep) for d, ep, which in best_pairs if which == 'i')
    facing_j = min((d, ep) for d, ep, which in best_pairs if which == 'j')
    ep_i = facing_i[1]
    ep_j = facing_j[1]

    # Get direction vectors at these endpoints (average of last N segments)
    dir_i = _endpoint_direction(line_i, ep_i, n_points=5)
    dir_j = _endpoint_direction(line_j, ep_j, n_points=5)

    if dir_i is None or dir_j is None:
        return None

    # Extend both endpoints along their direction
    ext_end_i = Point(ep_i.x + dir_i[0] * ext_deg, ep_i.y + dir_i[1] * ext_deg)
    ext_end_j = Point(ep_j.x + dir_j[0] * ext_deg, ep_j.y + dir_j[1] * ext_deg)

    ext_line_i = LineString([(ep_i.x, ep_i.y), (ext_end_i.x, ext_end_i.y)])
    ext_line_j = LineString([(ep_j.x, ep_j.y), (ext_end_j.x, ext_end_j.y)])

    ix = ext_line_i.intersection(ext_line_j)
    if not ix.is_empty:
        pt = ix if ix.geom_type == "Point" else ix.centroid
        print(f"      Extrapolated: extended {ep_i.y:.4f},{ep_i.x:.4f} "
              f"and {ep_j.y:.4f},{ep_j.x:.4f} -> ({pt.y:.6f}, {pt.x:.6f})")
        return pt

    # Extensions didn't cross — try snapping one extension to the other line
    snap_ext_i_on_j = line_j.interpolate(line_j.project(ext_end_i))
    snap_ext_j_on_i = line_i.interpolate(line_i.project(ext_end_j))

    # Use the extension that gets closer to the other line
    d1 = ext_end_i.distance(snap_ext_i_on_j)
    d2 = ext_end_j.distance(snap_ext_j_on_i)

    if d1 < d2 and d1 * 111320 < 500:
        pt = Point((ext_end_i.x + snap_ext_i_on_j.x) / 2,
                    (ext_end_i.y + snap_ext_i_on_j.y) / 2)
        return pt
    elif d2 * 111320 < 500:
        pt = Point((ext_end_j.x + snap_ext_j_on_i.x) / 2,
                    (ext_end_j.y + snap_ext_j_on_i.y) / 2)
        return pt

    return None


def _endpoint_direction(line, endpoint, n_points=5):
    """
    Get the direction vector at an endpoint of a line.
    Uses the last n_points to compute an averaged heading.

    Returns:
        (dx, dy) normalized direction vector pointing OUTWARD from the line,
        or None if can't determine.
    """
    if line.geom_type == "MultiLineString":
        # Find which sub-line contains this endpoint
        for part in line.geoms:
            coords = list(part.coords)
            if (abs(coords[0][0] - endpoint.x) < 1e-8 and
                    abs(coords[0][1] - endpoint.y) < 1e-8):
                return _endpoint_direction(part, endpoint, n_points)
            if (abs(coords[-1][0] - endpoint.x) < 1e-8 and
                    abs(coords[-1][1] - endpoint.y) < 1e-8):
                return _endpoint_direction(part, endpoint, n_points)
        return None

    coords = list(line.coords)
    if len(coords) < 2:
        return None

    # Determine if endpoint is at start or end
    is_start = (abs(coords[0][0] - endpoint.x) < 1e-8 and
                abs(coords[0][1] - endpoint.y) < 1e-8)
    is_end = (abs(coords[-1][0] - endpoint.x) < 1e-8 and
              abs(coords[-1][1] - endpoint.y) < 1e-8)

    if is_start:
        # Direction = from interior toward start (outward)
        n = min(n_points, len(coords))
        dx = coords[0][0] - coords[n - 1][0]
        dy = coords[0][1] - coords[n - 1][1]
    elif is_end:
        # Direction = from interior toward end (outward)
        n = min(n_points, len(coords))
        dx = coords[-1][0] - coords[-n][0]
        dy = coords[-1][1] - coords[-n][1]
    else:
        return None

    # Normalize
    mag = (dx ** 2 + dy ** 2) ** 0.5
    if mag < 1e-10:
        return None
    return (dx / mag, dy / mag)


def _get_endpoints(line):
    """Get the start and end points of a LineString or MultiLineString."""
    endpoints = []
    if line.geom_type == "LineString":
        coords = list(line.coords)
        endpoints.append(Point(coords[0]))
        endpoints.append(Point(coords[-1]))
    elif line.geom_type == "MultiLineString":
        for part in line.geoms:
            coords = list(part.coords)
            endpoints.append(Point(coords[0]))
            endpoints.append(Point(coords[-1]))
    return endpoints


# ---------------------------------------------------------------------------
# Boundary name resolution (pre-pass)
# ---------------------------------------------------------------------------

def _resolve_all_boundary_names(boundaries, ref_lat, ref_lon, city="Toronto, ON"):
    """
    Resolve all boundary names to exact GIS field values.

    Uses a two-pass approach:
      Pass 1: Try direct name resolution (exact match, LIKE, compass fallback)
      Pass 2: For unresolved streets, geocode their intersection with an
              adjacent resolved boundary to find the actual GIS road name
              at that specific corner location.
    """
    n = len(boundaries)
    resolved = [dict(b) for b in boundaries]

    # Store original user-facing names — these geocode better than GIS names
    for b in resolved:
        if b["feature_type"] == "street":
            b["_original_name"] = b.get("gis_hint") or normalize_road_name(b["feature_name"])
        else:
            b["_original_name"] = b["feature_name"]

    print("  Resolving boundary names...")

    # Pass 1: Direct resolution
    for b in resolved:
        ftype = b["feature_type"]
        fname = b["feature_name"]
        compass = b.get("compass_direction")

        if ftype == "street":
            normalized = b.get("gis_hint") or normalize_road_name(fname)
            field = "LINEAR_NAME_FULL"
            layer = LAYER_ROAD_CENTRELINE
        elif ftype == "waterway":
            normalized = fname
            field = "WATERLINE_NAME"
            layer = LAYER_WATERLINE
        else:
            b["_resolved"] = True
            continue

        # Check if exact/LIKE match works
        envelope = _make_envelope_params(ref_lat, ref_lon, 0.02)
        features = _query_where(
            layer, where_clause=f"{field} = '{normalized}'",
            out_fields=field, return_geometry=False, extra_params=envelope,
        )
        if features:
            if normalized != fname:
                print(f"    '{fname}' -> '{normalized}' (exact match)")
            b["feature_name"] = normalized
            b["_resolved"] = True
        else:
            # Try LIKE
            base = fname.split()[0]
            features = _query_where(
                layer, where_clause=f"UPPER({field}) LIKE '%{base.upper()}%'",
                out_fields=field, return_geometry=False, extra_params=envelope,
            )
            if features:
                # Pick first unique name
                names = set()
                for f in features:
                    name = (f.get("attributes", {}).get(field)
                            or f.get(field))
                    if name:
                        names.add(name)
                if len(names) == 1:
                    resolved_name = names.pop()
                    print(f"    '{fname}' -> '{resolved_name}' (LIKE match)")
                    b["feature_name"] = resolved_name
                    b["_resolved"] = True
                else:
                    b["_resolved"] = False
                    b["_like_names"] = names
            else:
                b["_resolved"] = False

    # Pass 2: Intersection-based resolution for unresolved boundaries
    # Skip if no boundaries resolved at all (non-Toronto area — ArcGIS has no data)
    any_resolved = any(b.get("_resolved") for b in resolved)
    if not any_resolved:
        print("    No boundaries found in ArcGIS -- using original names "
              "(will try Overpass API for geometry)")

    for i in range(n):
        if not any_resolved:
            break
        if resolved[i].get("_resolved"):
            continue

        b = resolved[i]
        approx = b.get("gis_hint") or normalize_road_name(b["feature_name"])
        compass = b.get("compass_direction")

        print(f"    '{b['feature_name']}' not found in GIS. "
              f"Trying intersection-based resolution...")

        # Try geocoding intersection with each adjacent boundary
        for di in [-1, 1]:
            j = (i + di) % n
            adj_name = resolved[j]["feature_name"]

            pts = _geocode_intersection_all(approx, adj_name, city=city)
            if not pts:
                continue
            pt = pts[0]  # Use first result

            print(f"    Geocoded '{approx} & {adj_name}' -> "
                  f"({pt.y:.6f}, {pt.x:.6f})")

            # Search for what roads exist at this intersection
            # Use a generous radius — geocoded points can be offset
            # from the actual GIS geometry by 500-800m
            local_env = _make_envelope_params(pt.y, pt.x, 0.008)
            features = _query_where(
                LAYER_ROAD_CENTRELINE,
                where_clause="1=1",
                out_fields="LINEAR_NAME_FULL",
                return_geometry=True,
                extra_params=local_env,
            )

            if not features:
                continue

            # Collect unique names (excluding the adjacent boundary's name)
            name_segments = {}
            for f in features:
                name = (f.get("attributes", {}).get("LINEAR_NAME_FULL")
                        or f.get("LINEAR_NAME_FULL"))
                if not name or name == adj_name:
                    continue
                if name not in name_segments:
                    name_segments[name] = []
                for path in f.get("geometry", {}).get("paths", []):
                    if len(path) >= 2:
                        name_segments[name].append(LineString(path))

            if not name_segments:
                continue

            # Pick the best match by compass direction
            if compass and len(name_segments) > 1:
                best = max(name_segments.keys(),
                           key=lambda nm: _compass_match_score(
                               name_segments[nm], ref_lat, ref_lon, compass))
            else:
                best = max(name_segments.keys(),
                           key=lambda nm: len(name_segments[nm]))

            print(f"    Resolved '{b['feature_name']}' -> '{best}' "
                  f"(found at intersection with {adj_name})")
            b["feature_name"] = best
            b["_resolved"] = True
            break

    # Clean up temp fields
    for b in resolved:
        b.pop("_resolved", None)
        b.pop("_like_names", None)

    return resolved


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

    # Extract city from reference address for geocoding outside Toronto
    ref_address = description.get("reference_point", {}).get("address", "")
    city = ", ".join(p.strip() for p in ref_address.split(",")[1:]) or "Toronto, ON"

    print("\n  [Approach A: Boundary Lines -> Polygon]")

    # Pre-resolve boundary names to exact GIS field values
    boundaries = _resolve_all_boundary_names(boundaries, ref_lat, ref_lon, city=city)

    # Pass 1: Fetch all boundary geometries
    raw_lines = []
    for b in boundaries:
        print(f"  Fetching {b['feature_type']}: {b['feature_name']}...")
        try:
            linestrings = fetch_boundary_geometry(b, ref_lat, ref_lon,
                                                  search_radius=0.03)
            print(f"    Got {len(linestrings)} segments")
            raw_lines.append(linestrings)
        except Exception as e:
            print(f"    WARNING: {e}")
            raw_lines.append(None)

    # Compute work_box from actual geometry bounds (data-driven)
    all_segments = [ls for group in raw_lines if group for ls in group]
    if all_segments:
        all_geom = MultiLineString(all_segments)
        bx = all_geom.bounds  # (minx, miny, maxx, maxy)
        padding = 0.005  # ~550m
        work_box = box(bx[0] - padding, bx[1] - padding,
                       bx[2] + padding, bx[3] + padding)
    else:
        work_box = box(ref_lon - 0.02, ref_lat - 0.02,
                       ref_lon + 0.02, ref_lat + 0.02)

    # Pass 2: Merge with data-driven clip
    merged_lines = []
    viz_lines = []
    for i, b in enumerate(boundaries):
        if raw_lines[i] is None:
            merged_lines.append(None)
            viz_lines.append(None)
            continue
        merged = _merge_and_select(raw_lines[i], clip_box=work_box,
                                   compass_direction=b.get("compass_direction"),
                                   ref_lat=ref_lat, ref_lon=ref_lon)
        total_length_m = merged.length * 111320 if merged else 0
        print(f"    {b['feature_name']}: merged {merged.geom_type}, "
              f"~{total_length_m:.0f}m")
        merged_lines.append(merged)
        viz_lines.append((merged, b))

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
            boundaries[i], boundaries[j], line_i, line_j, city=city
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

        # Detour detection: if a road boundary curves far away from the
        # straight line between corners (>2.5x the direct distance), try
        # corridor clipping. First try ArcGIS geometry, then fetch the
        # closest road from OSM within the corridor (name-free).
        seg_len = segment.length * 111320
        straight_len = prev_corner.distance(next_corner) * 111320
        if (straight_len > 0 and seg_len > straight_len * 2.5
                and boundaries[i]["feature_type"] == "street"):
            straight = LineString([
                (prev_corner.x, prev_corner.y),
                (next_corner.x, next_corner.y),
            ])
            corridor = straight.buffer(0.002)  # ~220m at Toronto latitude
            used_corridor = False

            # Try corridor clip on ArcGIS geometry first
            clipped = line.intersection(corridor)
            if not clipped.is_empty and clipped.length * 111320 > straight_len * 0.5:
                used_corridor = _apply_corridor_clip(
                    clipped, prev_corner, next_corner, boundaries[i], ring_segments)

            # If ArcGIS geometry doesn't pass through corridor, fetch the
            # closest road from OSM within the corridor (name-free query)
            if not used_corridor:
                try:
                    osm_road = fetch_corridor_road_osm(corridor, ref_lat, ref_lon)
                    if osm_road is not None:
                        used_corridor = _apply_corridor_clip(
                            osm_road, prev_corner, next_corner,
                            boundaries[i], ring_segments, source="OSM")
                except Exception as e:
                    print(f"    OSM corridor fallback failed: {e}")

            if used_corridor:
                continue
            # Corridor clip failed — use straight line
            segment = straight
            ring_segments.append(segment)
            print(f"    {boundaries[i]['feature_name']}: straight line "
                  f"(road detours: {seg_len:.0f}m vs {straight_len:.0f}m direct)")
            continue

        ring_segments.append(segment)
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
