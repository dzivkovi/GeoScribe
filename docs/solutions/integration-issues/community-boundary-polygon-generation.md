---
title: "Community Boundary NLP-to-GeoJSON Polygon Generation for Toronto Property Validation"
date: 2026-02-20
category: integration-issues
tags:
  - geospatial
  - polygon-generation
  - arcgis
  - overpass-api
  - osm
  - shapely
  - toronto
  - geocoding
  - kml
  - geojson
  - boundary-detection
severity: high
component: PropertyReportValidation/scripts
related_issues: []
status: resolved
resolution_type: architecture-change
---

# Community Boundary Polygon Generation from Natural Language Descriptions

## Problem Summary

The PropertyReportValidation project needed to convert natural language community boundary descriptions (e.g., "Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek") into precise geographic polygons exportable as GeoJSON, KML, and interactive HTML maps. Several compounding integration failures were encountered: road name aliases caused silent lookup failures, ArcGIS waterway data was critically sparse, road centreline segments had 100-200m gaps at intersections, KML export crashed on MultiPolygon geometry types, and Nominatim geocoding returned inaccurate intersection positions.

## Root Cause Analysis

### 1. ArcGIS Road Centreline Gaps at Intersections

Toronto's ArcGIS road centreline layer stores road geometry as discrete segments that terminate 100-200 metres before actual road intersections. A naive fetch of all segments for a named road returns disconnected pieces with gaps precisely where polygon corners need to be anchored. Direct geometric intersection tests on raw data fail to find where two roads actually meet.

### 2. Extremely Sparse ArcGIS Waterway Data

The `WATERLINE_NAME` layer (Layer 15) holds very limited geometry. For Mimico Creek, ArcGIS returned only ~74 metres of total polyline -- far too short to form a meaningful boundary for a community bounded "west and south" by the creek.

### 3. Road Names Change Locally

Bloor Street West physically becomes "The Kingsway" west of Royal York Road. The ArcGIS `LINEAR_NAME_FULL` field stores these as entirely different named features. A query for "Bloor Street West" returns nothing in the Thompson Orchard area. The community description says "south of Bloor" but the GIS name is "The Kingsway".

### 4. Existing Helper Flattens Segment Arrays

The pre-existing `query_road_geometry()` in `toronto_gis.py` (lines 201-208) concatenates all path segment coordinates into a single flat list, destroying individual segment boundaries. A road with segments `[[A,B,C], [D,E,F]]` becomes `[A,B,C,D,E,F]` with a false line from C to D. Shapely's `linemerge()` requires separate `LineString` objects to identify connected endpoints.

### 5. Nominatim Geocoding Inaccuracy for Intersections

Nominatim returns the closest point on a road centreline, not the physical intersection point. Because centreline segments have 100-200m gaps, the returned point can fall well inside one segment rather than at the actual corner where two roads meet.

## Working Solution

Two complementary polygon construction approaches were implemented:

### Approach A: Boundary Lines to Polygon

Five-stage algorithm: fetch, merge, corner-find, clip, assemble.

**Fetch -- preserving individual segments:**

```python
# community_polygon.py -- fetch_road_linestrings()
lines = []
for f in features:
    for path in f.get("geometry", {}).get("paths", []):
        if len(path) >= 2:
            lines.append(LineString(path))
return lines
```

This replaces `query_road_geometry()` by calling `_query_where()` directly, preserving each path as its own `LineString`.

**Overpass API fallback for sparse waterway data:**

```python
SPARSE_THRESHOLD_M = 200

# In fetch_boundary_geometry():
total_length = sum(l.length for l in lines) * 111320 if lines else 0
if total_length < SPARSE_THRESHOLD_M:
    osm_lines = fetch_waterline_overpass(fname, ref_lat, ref_lon, ...)
    if osm_lines:
        return osm_lines
```

Queries OpenStreetMap via `way["name"~"..."]["waterway"]` within a bounding box. Retry logic across two Overpass endpoints handles 504 timeouts.

**Corner finding strategy by feature-type pair:**

- **Street x Street**: Nominatim geocoding of the named intersection
- **Street x Waterway**: Shapely `intersection()`, fallback to `nearest_points()` midpoint
- **Waterway x Waterway**: `nearest_points()` midpoint with 0.01 degree (~1.1km) tolerance

**Clip with linear referencing:**

```python
def _line_substring(line, start_dist, end_dist, num_points=200):
    if start_dist > end_dist:
        start_dist, end_dist = end_dist, start_dist
    distances = np.linspace(start_dist, end_dist, num_points)
    points = [line.interpolate(d) for d in distances]
    return LineString([(p.x, p.y) for p in points])
```

Clipped segment endpoints are force-snapped to exact corner points before ring assembly.

### Approach B: Zoning Exception Union (Authoritative)

Where a community corresponds to a discrete zoning exception, query all parcels and dissolve:

```python
def construct_from_zoning_exception(exc_number, zone_type, ref_lat, ref_lon, radius=0.015):
    zone_data = query_exception_zone(exc_number, zone_type, ref_lat, ref_lon, radius)
    parcel_polygons = []
    for feature in zone_data["features"]:
        rings = feature["geometry"]["rings"]
        exterior = rings[0]
        holes = rings[1:] if len(rings) > 1 else []
        p = Polygon(exterior, holes)
        if not p.is_valid:
            p = p.buffer(0)
        parcel_polygons.append(p)
    community_polygon = unary_union(parcel_polygons)
```

For Thompson Orchard: exception 42, zone type RD returns ~330 parcels. `unary_union()` produces an authoritative community boundary. Both approaches run with `--approach both` and IoU is computed to compare them.

### Export Pipeline

- **GeoJSON**: Standard `mapping(polygon)` with boundary-line features
- **KML**: `_polygon_to_kml_coords()` decomposes MultiPolygon before export (fixes crash)
- **HTML**: Folium interactive map with polygon overlays, colored boundary lines, reference point marker, layer controls

## Investigation Steps (What Didn't Work)

1. **ArcGIS-only approach for waterways**: Mimico Creek returned 74m total -- completely insufficient. Overpass API fallback added.
2. **Using `query_road_geometry()` for line fetching**: Flattened coordinate arrays made `linemerge()` produce meaningless results. Replaced with direct `_query_where()` calls.
3. **`nearest_points()` on large MultiLineStrings**: Found globally closest points across kilometres of road, not the local intersection. Fixed by clipping to working bounding box first and adding `_find_corner()` with geocoding.
4. **Tight snap tolerance (0.001 deg)**: Many boundary pairs exceeded this. Progressively widened to 0.01 deg with gap distance logging.
5. **Direct `.exterior.coords` on union result**: Crashed when `unary_union()` produced MultiPolygon. Added `_polygon_to_kml_coords()` decomposition helper.

## Prevention Strategies

### Data Quality Checks Before Polygon Construction

- **Geometry completeness**: After fetching, verify total line length exceeds `SPARSE_THRESHOLD_M` (200m). Log and trigger fallback if sparse.
- **Name validation**: Query ArcGIS with exact name first; if zero results, check alias table and log non-exact matches.
- **Segment connectivity**: Calculate distance between consecutive segment endpoints; flag gaps > 50m.
- **Coordinate structure**: Before `linemerge()`, assert input is a collection of separate `LineString` objects, not a single flattened coordinate list.

### Geometry Type Robustness

Always check `geom_type` before accessing `.exterior`:
```python
if isinstance(polygon, MultiPolygon):
    polys = list(polygon.geoms)
else:
    polys = [polygon]
```

This pattern must be applied at every export/processing boundary (GeoJSON, KML, HTML map, area calculation).

### Road Name Normalization

Maintain a local alias table for Toronto street name variants:
- "Bloor St W" -> "The Kingsway" (west of Royal York)
- Use `note` field in JSON input to document name changes
- Query ArcGIS with each alias in sequence; log successful matches

### Fallback Chain

For any geographic feature type:
1. Toronto ArcGIS REST API (official, authoritative)
2. Overpass API / OpenStreetMap (comprehensive coverage, community-maintained)
3. Manual review flag (log warning, continue with available data)

## Test Case Suggestions

### Unit Tests

| Test | Input | Expected |
|------|-------|----------|
| Gap detection & snapping | 2 LineStrings with 150m gap | Snapping allows closure; polygon is valid |
| MultiPolygon KML export | Zoning union with 3 disconnected rings | KML has 3 `<Polygon>` elements |
| Road name alias resolution | "Bloor St W" in Kingsway area | Fallback finds "The Kingsway" |
| Flattened array detection | Concatenated coordinates vs. LineString list | `linemerge()` produces correct merge only for LineString list |
| Line substring extraction | Line with known length, clip at 25%-75% | Resulting segment length ~50% of original |

### Integration Tests

| Test | Community | Validation |
|------|-----------|------------|
| Thompson Orchard (Approach B) | Exception 42, RD zone | ~330 parcels, reference point inside polygon |
| Thompson Orchard (Approach A) | 3 boundaries | Polygon formed, IoU > 0.5 vs Approach B |
| Overpass fallback | Any community with waterway boundary | ArcGIS sparse triggers Overpass; final geometry > 1km |

## Files

| File | Purpose |
|------|---------|
| `scripts/community_polygon.py` | Main script: parse JSON, fetch GIS data, construct polygon, export |
| `scripts/community_visualize.py` | Folium map rendering with polygon overlays and boundary lines |
| `scripts/thompson_orchard.json` | Sample input with boundary descriptions and zoning exception |
| `scripts/requirements.txt` | Dependencies: requests, shapely, folium, simplekml |
| `scripts/toronto_gis.py` | Existing ArcGIS REST helpers (reused, not modified) |
| `scripts/config.py` | Layer endpoints and configuration (reused, not modified) |
| `scripts/boundary_check.py` | Existing boundary validation (independently updated to use property boundary layer) |

## Technology Stack

| Technology | Role |
|------------|------|
| Toronto ArcGIS REST API | Official road centreline, waterline, zoning exception data |
| Overpass API (OSM) | Fallback for sparse waterway geometry |
| Shapely | Geometry operations: linemerge, polygonize, unary_union, linear referencing |
| Folium | Interactive HTML map visualization on OpenStreetMap tiles |
| simplekml | KML export for Google Earth |
| Nominatim | Free geocoding for reference points and street intersections |

## Known Limitations

- **Approach A accuracy**: Royal York Rd curves through a ravine, causing the boundary-line polygon to shift. Reference point may not fall inside. Approach B is authoritative for communities with zoning exceptions.
- **Nominatim intersection geocoding**: Returns points ON roads, not AT intersections. Potential improvement: use property boundary layer parcel centroids (as done in updated `boundary_check.py`).
- **Overpass API reliability**: 504 timeouts occur; retry logic with backup endpoint mitigates but doesn't eliminate.
- **Road name aliases**: Must be manually configured per community. No automated alias discovery yet.
