# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

GeoScribe converts structured JSON descriptions of community boundaries (street names, waterways, compass directions) into geographic polygons using live GIS data from Toronto's ArcGIS REST API and OpenStreetMap. Outputs: interactive HTML maps (Folium/Leaflet), GeoJSON, KML.

## Commands

All scripts run from the `scripts/` directory:

```bash
# Install dependencies
pip install -r requirements.txt

# Generate polygon from boundary description
cd scripts
python community_polygon.py ../examples/thompson_orchard.json --approach both
python community_polygon.py ../examples/thompson_orchard.json --approach lines
python community_polygon.py ../examples/thompson_orchard.json --approach zoning

# Validate an address against community boundaries
python validate.py "9 Ashton Manor, Etobicoke, ON"
python validate.py --json-only "9 Ashton Manor, Etobicoke, ON"

# Standalone boundary check
python boundary_check.py "9 Ashton Manor, Etobicoke, ON" --exception 42
```

There are no tests, linter, or build steps configured.

## Architecture

### Two Polygon Construction Approaches

**Approach A (Boundary Lines):** Resolves user-facing boundary names to GIS-internal names (`_resolve_all_boundary_names()`), fetches road/waterway line geometries from ArcGIS, filters by compass direction, selects the longest nearby component from MultiLineString results, finds corners between adjacent boundaries using a multi-strategy pipeline (geocode+snap, geometric intersection, line extrapolation, nearest points), clips each boundary to the segment between its two corners using linear referencing, applies detour detection for street boundaries, then assembles into a closed polygon ring.

**Approach B (Zoning Exception Union):** Queries all parcels with a specific zoning exception number from Toronto's zoning layer, converts ArcGIS ring geometry to Shapely Polygons, unions with `unary_union()`. Authoritative when a zoning exception number is available.

### Data Flow

`community_polygon.py` is the main entry point. It loads a JSON boundary description, resolves user-facing names to GIS-internal names (e.g., "Bloor" → "The Kingsway"), resolves a reference point via `geocoder.py`, fetches geometry via `toronto_gis.py` (which queries ArcGIS endpoints defined in `config.py`), constructs polygons with Shapely, and exports to GeoJSON/KML/HTML (using `community_visualize.py` for maps).

`validate.py` orchestrates the validation pipeline: geocodes an address, runs all spatial queries via `toronto_gis.query_all()`, then delegates boundary checking to `boundary_check.py` and report formatting to `report_generator.py`.

### Key Data Sources and Fallbacks

- Toronto ArcGIS REST API: roads, waterways, zoning parcels, property boundaries (no API key)
- Overpass API (OpenStreetMap): waterway fallback when ArcGIS data is sparse (<200m total geometry)
- Nominatim: free geocoding (default); Google Maps geocoding optional via `GOOGLE_MAPS_API_KEY` env var (improves intersection geocoding accuracy)

### Critical Implementation Details

- **JSON `feature_name` should use user-facing names** from the community description (e.g., "Bloor", "Royal York", "Mimico Creek"). The GIS name resolution pipeline (`_resolve_all_boundary_names()` → `resolve_gis_name()`) automatically maps these to exact GIS field values from `LINEAR_NAME_FULL`. Resolution strategies: exact match, LIKE query, then intersection-based resolution (geocode intersection with adjacent boundary, search for road segments at that location).
- **`compass_direction` is actively used** for geometry filtering. `_filter_by_compass()` keeps only segments consistent with the boundary's compass direction relative to the reference point (e.g., "north" keeps segments north of reference). `_merge_and_select()` selects the longest nearby component from MultiLineString results within 2km of the reference point.
- **Corner-finding uses a multi-strategy pipeline** in `_find_corner()`: (1) geocode intersection with both Google and Nominatim via `_geocode_intersection_all()`, validate by snapping to line geometries — "geocoded+snapped" if both snaps < 500m, "geocoded+partial" if min snap < 200m; (2) geometric intersection of buffered lines; (3) line extrapolation via `_extrapolate_corner()` for non-intersecting geometries (bridge/valley gaps); (4) `nearest_points()` fallback. Original user-facing names are tried first and take priority over resolved GIS names for geocoding.
- **ArcGIS road centrelines have 100-1500m gaps** at intersections, especially where roads cross at different elevations (bridges/underpasses). The "geocoded+partial" strategy handles this: if one geometry confirms the geocoded location (snap < 200m), trust it despite the other geometry having a gap.
- **Detour detection** in ring building: if a street boundary's extracted segment is >2.5x the straight-line distance between its corners, the road geometry curves far from the community boundary. The segment is replaced with a straight line between corners.
- **Individual LineString segments must be preserved** when fetching from ArcGIS. Do not flatten path arrays into a single coordinate list -- `linemerge()` requires separate LineString objects. `fetch_road_linestrings()` replaces the older `query_road_geometry()` for this reason.
- **Always handle MultiPolygon** after `buffer(0)` or `unary_union()`. Check `isinstance(polygon, MultiPolygon)` before accessing `.exterior`.
- **JSON boundary input requires perimeter order** -- each boundary shares a corner with the next, and the last shares a corner with the first.
- **All scripts use relative imports** from within the `scripts/` directory. Run from `scripts/` or adjust `sys.path`.

## Configuration

All endpoints and constants are in `scripts/config.py`. Output goes to `output/` (auto-created, gitignored). Optional env var: `GOOGLE_MAPS_API_KEY` for Google Maps geocoding (improves intersection geocoding accuracy; Nominatim is used as fallback regardless).
