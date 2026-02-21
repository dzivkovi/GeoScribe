---
title: "Name-free OSM corridor query for boundary road geometry when ArcGIS names are misleading"
date: 2026-02-20
category: integration-issues
component: scripts/community_polygon.py
tags:
  - gis
  - polygon-generation
  - openstreetmap
  - overpass-api
  - arcgis
  - corridor-clipping
  - detour-detection
  - road-geometry
  - name-resolution
  - shapely
  - toronto-gis
severity: medium
time_to_resolve: "4h"
status: resolved
resolution_type: feature-enhancement
symptoms:
  - "North boundary of Thompson Orchard polygon rendered as straight line instead of following road"
  - "ArcGIS road geometry ('The Kingsway') curves through ravine 300m north of community boundary"
  - "OSM has correct geometry ('Bloor Street West') but name-based queries fail on abbreviation mismatch"
  - "Closest-to-reference-point selection picks internal residential streets instead of boundary road"
root_causes:
  - "ArcGIS 'The Kingsway' geometry is in a ravine (lat 43.650+), not along the community boundary (lat 43.647)"
  - "OSM name 'Bloor Street West' does not match normalized query 'Bloor St W'"
  - "Detour detection correctly fired (ratio 2.92x) but only had straight-line fallback"
related_issues:
  - ./community-boundary-polygon-generation.md
  - ../logic-errors/gis-name-resolution-polygon-accuracy.md
---

# Name-Free OSM Corridor Query for Boundary Road Geometry

## Problem Summary

Thompson Orchard's north boundary was a straight line instead of following the actual road. Detour detection correctly identified that "The Kingsway" (the ArcGIS name) curves through a ravine (1940m vs 665m direct, ratio 2.92x), but the only fallback was a straight line between corners.

The actual boundary road exists in OSM as "Bloor Street West" at lat 43.645-43.648 -- right where needed. ArcGIS doesn't have this road here; it calls the nearby road "The Kingsway" but its geometry runs through the ravine at lat 43.650+.

## Investigation Steps

1. **ArcGIS corridor clip -- empty.** Buffered the straight line between corners by 220m, intersected with The Kingsway geometry. Empty result -- the ravine geometry is entirely north of the corridor.

2. **OSM name-based query -- name mismatch.** Queried Overpass for `way["name"="Bloor St W"]`. No results -- OSM uses "Bloor Street West" (unabbreviated). This is not a bug; it's basic English variation. Regex substring matching ("Bloor") was considered and rejected -- risks capturing unrelated streets ("Bloormount Lane").

3. **Name-free OSM query -- wrong road selected.** Queried all highways in corridor, picked closest to reference point. Selected an internal residential street (~50m from reference) instead of the boundary road (~93m from reference). Internal streets are closer to the community center by definition.

4. **Longest-span selection -- correct road selected.** Grouped OSM roads by name, measured each road's span within the corridor. Boundary road "Bloor Street West" spans 1564m (full corridor length). Cross-streets span 20-50m. Longest-span selection reliably identifies the boundary road.

5. **MultiLineString handling -- disconnected segments.** The clipped OSM geometry had disconnected segments that `linemerge()` couldn't join. Added fallback to pick the longest piece.

## Root Cause

The ArcGIS road centreline layer stores "The Kingsway" with geometry that physically curves through a ravine (reaching lat 43.650+), while the community boundary follows a conceptual east-west line at lat 43.647. The detour detection threshold (2.5x) correctly identified this as a detour, but the only option was a straight line -- no mechanism existed to fetch alternative road geometry from OSM.

## Solution

### `fetch_corridor_road_osm()` -- Name-free corridor query

When detour detection fires and ArcGIS geometry doesn't pass through the corridor:

1. **Build corridor**: buffer straight line between corners by ~220m
2. **Query OSM for ALL highways** in the corridor bounding box (no name filter)
3. **Group segments by road name**, merge each road's pieces with `linemerge()`
4. **Pick longest-spanning road** (boundary roads run full length; cross-streets only cross briefly)
5. **Proximity tiebreaker** among similarly-long roads (>50% of max span) -- selects community-side lane of dual-carriageway

```python
def fetch_corridor_road_osm(corridor_poly, ref_lat, ref_lon):
    # Query ALL highways in corridor (name-free)
    roads = {}  # name -> [LineStrings]
    for element in data.get("elements", []):
        name = element.get("tags", {}).get("name", f"unnamed_{element['id']}")
        roads.setdefault(name, []).append(LineString(coords))

    # Group, merge, clip, measure span
    candidates = []
    for name, segments in roads.items():
        merged = linemerge(MultiLineString(segments))
        clipped = merged.intersection(corridor_poly)
        span = clipped.length * 111320  # meters
        dist = clipped.distance(ref) * 111320
        candidates.append((clipped, span, dist, name))

    # Longest span wins; proximity breaks ties
    max_span = max(c[1] for c in candidates)
    long_roads = [c for c in candidates if c[1] > max_span * 0.5]
    best = min(long_roads, key=lambda x: x[2])
```

### Detour detection cascade

```
if seg_len > straight_len * 2.5 (street only):
  1. Try ArcGIS corridor clip (intersect with 220m buffer)
  2. If empty -> fetch_corridor_road_osm() (name-free)
  3. If OSM returns geometry -> _apply_corridor_clip()
  4. Else -> straight line fallback
```

### MultiLineString handling

After `linemerge()`, if result is still MultiLineString (disconnected segments), pick the longest piece:

```python
if result.geom_type == "MultiLineString":
    pieces = list(result.geoms)
    result = max(pieces, key=lambda g: g.length)
```

## Result

- OSM corridor picked "Bloor Street West" (span=1564m, dist=93m)
- North boundary: 22 points following actual road (was 2-point straight line)
- Reference point inside: YES
- Area: 0.392 km^2

## Why Name-Free

The JSON `feature_name` is NLP output from a natural language description. Name resolution between "Bloor", "Bloor St W", "Bloor Street West", and "The Kingsway" is an LLM-grade intelligence task, not a regex task. The corridor + span + proximity approach sidesteps name matching entirely -- it finds whatever road runs between the two corners, closest to the community.

## Files Modified

| File | Change |
|------|--------|
| `scripts/community_polygon.py` | Added `fetch_corridor_road_osm()`, `_apply_corridor_clip()`, updated detour detection cascade |

## Prevention Strategies

### For other communities with terrain issues

- **Ravines, valleys, bridges**: roads may curve far from the boundary line. Detour detection (ratio >2.5x) catches this automatically.
- **Dual carriageways**: OSM represents each direction as separate ways. Proximity tiebreaker selects the community-facing lane.
- **Cross-streets in corridor**: span-based ranking eliminates them (20-50m span vs 500m+ for boundary roads).

### For name mismatches across data sources

- **Don't regex-match road names across ArcGIS/OSM.** Name conventions differ ("St W" vs "Street West"). Corridor geometry query is more robust.
- **Preserve user-facing names for geocoding.** Original names ("Bloor St W & Royal York Rd") produce better geocode results than resolved GIS names ("The Kingsway & Royal York Rd").

## Potential Test Cases

| Test | Scenario | Expected |
|------|----------|----------|
| CR-1 | ArcGIS geometry in ravine, OSM has road at correct location | Corridor picks OSM road by span |
| CR-2 | Dual carriageway in corridor | Proximity tiebreaker picks community-side lane |
| CR-3 | Cross-streets in corridor | Span filter eliminates them (<50% of max) |
| CR-4 | OSM MultiLineString (disconnected segments) | Picks longest piece after failed linemerge |
| CR-5 | Both ArcGIS and OSM corridor clips empty | Falls back to straight line |
| CR-6 | ArcGIS geometry passes through corridor | Uses ArcGIS clip, skips OSM query |

## Related Documentation

- [Community Boundary Polygon Generation](./community-boundary-polygon-generation.md) -- original architecture, ArcGIS gaps, Overpass fallback
- [GIS Name Resolution & Polygon Accuracy](../logic-errors/gis-name-resolution-polygon-accuracy.md) -- name resolution pipeline, compass filtering, corner-finding
