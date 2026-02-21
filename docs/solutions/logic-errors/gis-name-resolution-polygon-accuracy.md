---
title: "Thompson Orchard polygon mismatch — hallucinated road name, off-center reference point, unused compass metadata"
date: 2026-02-20
category: logic-errors
component: scripts/community_polygon.py
tags:
  - gis
  - polygon-generation
  - llm-hallucination
  - geocoding
  - shapely
  - arcgis
  - compass-filtering
  - boundary-resolution
  - toronto-gis
  - data-quality
severity: high
time_to_resolve: "8h"
status: resolved
resolution_type: architecture-change
symptoms:
  - "Output polygon did not match the reference map (examples/ThompsonOrchard.png)"
  - "Wrong north boundary — The Kingsway curves far north instead of a straight Bloor line"
  - "Reference point (9 Ashton Manor) located outside the generated polygon"
  - "IoU between Approach A (boundary lines) and Approach B (zoning union) was 0.000"
root_causes:
  - "LLM hallucinated road name substitution in input JSON — replaced 'Bloor' with 'The Kingsway' based on incorrect assumption they are the same road"
  - "Reference point '9 Ashton Manor' is east of Royal York Rd while Thompson Orchard is west"
  - "compass_direction metadata present in JSON but never used for geometry filtering"
related_issues:
  - ../integration-issues/community-boundary-polygon-generation.md
---

# GIS Name Resolution & Polygon Accuracy Fix

## Problem Summary

GeoScribe's Thompson Orchard polygon output was completely wrong — it didn't match the reference map. The community description is clear: *"Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek."* The expected shape is a triangle with Royal York on the east, Bloor at the top (straight line), and Mimico Creek curving along the west and south.

Three compounding errors produced a polygon that bore no resemblance to this description.

## Investigation Steps

1. Compared the output polygon against the reference map (`examples/ThompsonOrchard.png`) — shape was completely wrong.
2. Analyzed the GIS data: "The Kingsway" is a curving residential road, not the straight E-W boundary described. An LLM had fabricated the substitution in the input JSON.
3. Geocoded the reference point "9 Ashton Manor" — it's east of Royal York Rd, placing it outside the Thompson Orchard community.
4. Discovered `compass_direction` was written to GeoJSON output but never consumed during geometry processing.
5. Found Nominatim is order-sensitive: "Royal York Rd & Bloor St W" failed but "Bloor St W & Royal York Rd" succeeded.
6. Found Google Maps returned wrong locations for road-waterway intersections ("Bloor St W & Mimico Creek" geocoded to downtown Toronto, 10km away).
7. Found resolved-name geocodes ("Royal York Rd & The Kingsway") overrode correct original-name results at the far north convergence of both roads (1km from community).
8. Measured The Kingsway segment between corners: 1940m along the road vs 665m straight-line — the road curves far north through a ravine.

## Root Cause

Three compounding errors:

### 1. Hallucinated road name in input JSON

The north boundary was specified as "The Kingsway" instead of "Bloor". An LLM authored the JSON and fabricated a note: "Bloor St W becomes The Kingsway in this area." This is wrong — they are distinct roads in Toronto's GIS. The system required GIS-exact names (`LINEAR_NAME_FULL`) but the JSON author didn't know GIS internals.

### 2. Reference point outside the community

The reference address "9 Ashton Manor, Etobicoke, ON" geocodes to a location east of Royal York Rd. Thompson Orchard is *west* of Royal York. This shifted the entire search envelope to the wrong side.

### 3. Unused compass_direction metadata

Each boundary carried a `compass_direction` field (`"north"`, `"east"`, `"west_and_south"`) but the code ignored it completely. Segments on the wrong side of the reference point were included without filtering.

## Solution

### A. GIS Name Resolution Pipeline

Added `_resolve_all_boundary_names()` and `resolve_gis_name()` to automatically map user-facing names to GIS-internal names. The original name is preserved in `_original_name` for geocoding priority.

Resolution strategy (in order):
1. Normalize with `normalize_road_name()` (handles "Bloor" → "Bloor St W")
2. Exact match query against ArcGIS (`LINEAR_NAME_FULL = 'Bloor St W'`)
3. LIKE query (`UPPER(LINEAR_NAME_FULL) LIKE '%BLOOR%'`)
4. Intersection-based: geocode the intersection with an adjacent boundary, search for road segments at that location

```python
# Key insight: "Bloor" resolves to "The Kingsway" via intersection-based lookup
# 1. Geocode "Bloor St W & Royal York Rd" → (43.647, -79.511)
# 2. Query ArcGIS for roads near that point
# 3. Find "The Kingsway" segments there → resolved name
```

### B. Multi-Geocoder Corner-Finding with Geometry Validation

Rewrote `_find_corner()` with four strategies tried in order:

1. **Geocode+snap**: `_geocode_intersection_all()` tries both Google and Nominatim, both name orderings ("A & B" and "B & A"), plus "at" format. All candidates validated by snapping to line geometries:
   - "geocoded+snapped": both snaps < 500m → average the two snap points
   - "geocoded+partial": min snap < 200m → use the closer snap point (handles ArcGIS centreline gaps at bridges/underpasses)
2. **Geometric intersection**: Buffer both lines by 30m, intersect, take centroid
3. **Line extrapolation**: `_extrapolate_corner()` extends endpoints along their trajectory
4. **Nearest points**: `nearest_points()` fallback

**Critical: original-name priority.** User-facing names are tried first. If they produce geocode results, don't fall through to resolved GIS names (which may geocode to a different point on the same road).

```python
# Original names ("Bloor St W & Royal York Rd") geocode to (43.647, -79.511)
#   → 2m snap to Royal York Rd geometry = VALID partial-snap
# Resolved names ("The Kingsway & Royal York Rd") geocode to (43.656, -79.514)
#   → 1km north of community = WRONG (roads converge far away)
# Solution: try originals first, return on first valid result
```

### C. Compass Direction Filtering

`_filter_by_compass()` keeps only geometry segments consistent with each boundary's declared direction relative to the reference point. `_merge_and_select()` selects the longest connected component within 2km of the reference point after `linemerge()`.

### D. Detour Detection

During ring assembly, if a street boundary's segment length exceeds 2.5x the straight-line distance between its corners, replace with a straight line. This catches roads that curve far from the community boundary.

```python
# The Kingsway: 1940m along road vs 665m direct (ratio 2.9x)
# → replaced with straight line between NE and NW corners
```

### E. Input JSON Correction

Changed to use everyday names (resolution pipeline handles the rest):

```json
{
  "community_name": "Thompson Orchard",
  "reference_point": { "address": "25 Thompson Ave, Etobicoke, ON" },
  "boundaries": [
    { "feature_name": "Royal York",   "feature_type": "street",   "compass_direction": "east" },
    { "feature_name": "Bloor",        "feature_type": "street",   "compass_direction": "north" },
    { "feature_name": "Mimico Creek", "feature_type": "waterway", "compass_direction": "west_and_south" }
  ]
}
```

## Result

- Area: 0.400 km², reference point inside: YES
- Triangle shape matching reference map
- Corners: NE at Royal York/Bloor (geocoded+partial), NW at Bloor/Mimico Creek (geocoded+partial), S at Mimico Creek/Royal York (geometric intersection)
- The Kingsway north boundary rendered as straight line (detour detection)

## Files Modified

| File | Change |
|------|--------|
| `scripts/community_polygon.py` | Added name resolution, multi-geocoder corner-finding, compass filtering, detour detection |
| `examples/thompson_orchard.json` | User-facing names, correct reference point |
| `CLAUDE.md` | Updated architecture documentation |
| `README.md` | Comprehensive rewrite with usage guide and KML viewing instructions |

## Prevention Strategies

### Prevent Hallucinated Road Names

- **Validate all road names against GIS at ingest time.** Before geometry work begins, resolve every name against ArcGIS `LINEAR_NAME_FULL`. Reject with a clear error if no features are found within the search radius.
- **Never trust LLM-authored spatial data without machine validation.** Treat LLM output the same as user input — assume it contains plausible-sounding errors.
- **Log the resolution chain.** When "Bloor" resolves to "The Kingsway", log both so debugging is straightforward.

### Prevent Off-Center Reference Points

- **Validate that the reference point falls inside the constructed polygon.** Run `point.within(polygon)` after construction. If outside, emit a warning.
- **Document in the schema that the reference address must be well inside the community**, not on a boundary road.

### Prevent Dead Metadata

- **If a field exists in the schema, it must be consumed by the code.** Dead schema fields are a maintenance hazard. Either use them or remove them.
- **Use compass_direction for both filtering and validation.** Filter candidate segments during construction; validate the final polygon edges are in the expected compass octants.

## Validation Checklist

### Input
- [ ] Every road/waterway name resolves to GIS features within the search radius
- [ ] Reference address geocodes to a point inside the expected community area
- [ ] Boundaries are in perimeter order
- [ ] All `compass_direction` values match geographic reality

### Geometry
- [ ] Polygon is valid (`polygon.is_valid`)
- [ ] Single Polygon, not MultiPolygon
- [ ] Reference point falls inside polygon
- [ ] Area is plausible (typically 0.1–10 km² for Toronto communities)

### Visual
- [ ] HTML map polygon aligns with expected road/waterway boundaries
- [ ] No unexpected protrusions or concavities
- [ ] Curving roads handled correctly (detour detection or road-following as appropriate)

## Potential Test Cases

| Test | Scenario | Expected |
|------|----------|----------|
| NR-1 | Exact GIS name ("Royal York Rd") | Resolves directly, returns geometry |
| NR-2 | Common short name ("Bloor" near Royal York) | Resolves to "The Kingsway" via intersection-based lookup |
| NR-3 | Hallucinated name ("Kingsway Boulevard") | Fails with clear error |
| RP-1 | Address inside community | Reference point within final polygon |
| RP-2 | Address on wrong side of boundary road | Warning emitted |
| CD-1 | Correct compass direction | Geometry segments accepted |
| CD-2 | Wrong compass direction | Segments filtered out, warning logged |
| GC-1 | ArcGIS gap at intersection (bridge) | Corner found via geocoded+partial strategy |
| GC-2 | Road curves through ravine (detour ratio >2.5) | Straight line substituted |
| E2E-1 | Thompson Orchard full pipeline | Polygon matches reference map, area ~0.4 km² |

## Related Documentation

- [Community Boundary Polygon Generation](../integration-issues/community-boundary-polygon-generation.md) — original implementation documenting ArcGIS gaps, Overpass fallback, and the two-approach architecture
