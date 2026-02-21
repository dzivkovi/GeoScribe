---
title: "GeoScribe Two-Stage Pipeline: NLP Intake + Deterministic GIS Processing"
date: 2026-02-21
category: logic-errors
component: system-architecture
tags:
  - misconception-correction
  - architectural-documentation
  - boundary-intake
  - name-resolution
  - corner-finding
  - pipeline-design
severity: medium
symptoms: "User questioned source of intelligence in GeoScribe, assuming NLP parsed unstructured boundary descriptions into polygons"
root_cause: "The JSON description field is display-only; actual inputs are structured boundaries[] array. Intelligence comes from cascading fallback strategies for name resolution and corner-finding, not NLP."
related:
  - ../integration-issues/community-boundary-polygon-generation.md
  - gis-name-resolution-polygon-accuracy.md
---

# Two-Stage Pipeline: NLP Intake + Deterministic GIS Processing

## Problem Statement

The user believed GeoScribe used NLP or AI to parse plain-English community boundary descriptions into geographic polygons. Two specific concerns:

1. Is the `description` field ("Thompson Orchard runs west of Royal York...") actually parsed by any code?
2. Are there hardcoded assumptions that make this only work for Thompson Orchard?

Understanding the real architecture is critical for knowing how to extend the system to new communities and where AI actually belongs in the workflow.

## Investigation Steps

**Step 1: Trace where the `description` field is consumed.**
Found a single usage at `community_polygon.py` ~line 1574:
```python
description.get('description', 'N/A')
```
This stores the prose description as display metadata only. It is never tokenized, parsed, or used to derive boundary geometry.

**Step 2: Identify the real input consumed by the pipeline.**
The `boundaries[]` array in the JSON is the actual input. Each entry has explicit `feature_name`, `feature_type`, and `compass_direction` fields. A human must pre-decompose the prose into this structure.

**Step 3: Audit for Thompson Orchard-specific hardcoding.**
Found `ROAD_NAME_ALIASES` in `community_polygon.py` (lines 50-57):
```python
ROAD_NAME_ALIASES = {
    "Royal York Road": "Royal York Rd",
    "Royal York": "Royal York Rd",
    "Bloor Street West": "Bloor St W",
    "Bloor Street": "Bloor St W",
    "Bloor": "Bloor St W",
    "The Kingsway": "The Kingsway",
    "Kingsway": "The Kingsway",
}
```
These are a performance shortcut. Without them, the LIKE query fallback and Pass 2 intersection-based resolution handle the same mappings with one additional API call.

**Step 4: Map the full pipeline to confirm generality.**
Read `_resolve_all_boundary_names()`, `_find_corner()`, `_filter_by_compass()`, and `_merge_and_select()`. No other Thompson Orchard-specific logic exists.

## Root Cause

GeoScribe is a two-stage pipeline, not an NLP system:

```
Stage 1 (External):  Plain English  -->  [Human or LLM]  -->  Structured JSON
Stage 2 (GeoScribe): Structured JSON -->  [GIS Pipeline]  -->  Polygon + Map
```

The `description` field is never parsed. The real input is the `boundaries[]` array where a human (or LLM via `prompts/boundary_intake.md`) has already performed the NLP decomposition step.

The pipeline's apparent intelligence comes from **cascading deterministic fallback strategies**, not from any language model or statistical method:

- **Name resolution**: exact match -> LIKE query -> intersection-based geocoding
- **Corner-finding**: geocode+snap -> geometric intersection -> line extrapolation -> nearest_points
- **Compass filtering**: centroid comparison to discard segments on the wrong side

## Solution

### Part 1: The Python code is already generic

Any Toronto community can be processed by writing a new JSON file and running the existing script unchanged:

```json
{
  "community_name": "Any Neighbourhood",
  "description": "Original prose description here",
  "reference_point": {
    "address": "100 Interior Street, Toronto, ON"
  },
  "boundaries": [
    {
      "feature_name": "Eglinton",
      "feature_type": "street",
      "compass_direction": "north"
    },
    {
      "feature_name": "Dufferin",
      "feature_type": "street",
      "compass_direction": "east"
    },
    {
      "feature_name": "Lawrence",
      "feature_type": "street",
      "compass_direction": "south"
    },
    {
      "feature_name": "Allen Road",
      "feature_type": "street",
      "compass_direction": "west"
    }
  ]
}
```

Run with:
```bash
cd scripts
python community_polygon.py ../examples/my_community.json --approach both
```

No code changes required.

### Part 2: The LLM intake prompt bridges Stage 1

Created `prompts/boundary_intake.md` - a reusable prompt that any LLM can use to convert plain-English community descriptions into the required JSON schema. This is where AI enters the pipeline.

Key design decisions encoded in the prompt:

| Decision | Rationale |
|----------|-----------|
| Use colloquial names ("Bloor" not "Bloor St W") | `_find_corner()` tries `_original_name` first; colloquial names geocode better |
| Enforce perimeter order | Corner-finding needs consecutive boundary adjacency |
| Single entry for multi-sided features | Splitting a creek into two entries creates a phantom corner |
| Reference point must be interior | Compass filtering anchors all direction calculations to this point |
| zoning_exception is optional | Many communities don't have one; Approach A works universally |
| Ask clarifying questions, don't assume | 6 question templates for missing boundaries, ambiguous directions, unknown waterway names, no reference point, unclear perimeter order, zoning exceptions |

## Where the Intelligence Actually Lives

### Name Resolution (`_resolve_all_boundary_names()`)

Two passes, all using live GIS queries:

**Pass 1 - Direct Resolution:**
1. Check `ROAD_NAME_ALIASES` cache (instant, no API call)
2. Exact match query against ArcGIS `LINEAR_NAME_FULL` field
3. LIKE query (substring match) against ArcGIS
4. If LIKE returns one unique name, accept it; if multiple, defer to Pass 2

**Pass 2 - Intersection-Based Resolution:**
1. Geocode the intersection of the unresolved boundary with an adjacent resolved boundary (e.g., "Bloor & Royal York")
2. Query ALL road centrelines (`WHERE 1=1`) within 0.008 degrees of that point
3. Exclude the adjacent boundary's name from candidates
4. Pick the best remaining road using `_compass_match_score()` (direction, length, orientation, proximity)

This is how "Bloor" becomes "The Kingsway" automatically - the pipeline discovers the actual road name at that location.

### Corner-Finding (`_find_corner()`)

Four-strategy cascade:

1. **Geocode+snap**: Fire geocoding queries via Google and Nominatim using both "&" and "at" formats. Validate by snapping to both line geometries. "geocoded+snapped" if both snaps < 500m. "geocoded+partial" if min snap < 200m (handles ArcGIS centreline gaps at bridges).
2. **Geometric intersection**: Shapely intersection of buffered line geometries.
3. **Line extrapolation**: Extend both lines' endpoints 2000m along their trajectory, find the projected crossing (handles bridge/underpass gaps of 100-1500m).
4. **Nearest points fallback**: Tests all endpoint-to-line distances plus `nearest_points()`.

Original user-facing names are tried first and take priority over resolved GIS names. If original names produce any geocode results, the function does NOT fall through to resolved names.

### Compass Filtering (`_filter_by_compass()`)

Keeps only geometry segments whose centroid is in the correct compass quadrant relative to the reference point. For compound directions like "west_and_south", uses OR logic (centroid is west OR south).

### Detour Detection

After clipping a street boundary to the segment between its corners, if the segment length exceeds 2.5x the straight-line distance between corners, the road curves away from the community edge. The curved segment is replaced with a straight line.

## What Makes the System Community-Agnostic

| Component | Generic? | Detail |
|-----------|----------|--------|
| ArcGIS queries | Yes | All queries hit Toronto's public API with dynamic parameters |
| Name resolution | Yes | Cascade works for any road/waterway name |
| Corner-finding | Yes | Geocoding + geometric operations, no location-specific logic |
| Compass filtering | Yes | Pure lat/lon comparison relative to reference point |
| Waterway fallback | Yes | Triggered by data quality (< 200m geometry), not feature name |
| Detour detection | Yes | Geometry-based distance ratio check |
| `ROAD_NAME_ALIASES` | No | Contains 3 Thompson Orchard-specific entries (convenience only) |

Removing `ROAD_NAME_ALIASES` entirely would cause one extra API call per boundary but produce identical results.

## Prevention Strategies

### For JSON Authors (Human or LLM)

- **Always use colloquial names in `feature_name`**: "Bloor" not "Bloor St W". The name resolution pipeline is designed to map everyday names to GIS field values. Using official names defeats the resolution system.
- **List boundaries in strict perimeter order**: Walk the boundary clockwise or counterclockwise. Each boundary must connect to the next. The last must connect to the first.
- **Use compound compass_directions for wrapping features**: A creek forming the west and south boundary is ONE entry with "west_and_south", not two separate entries.
- **Pick an interior reference point**: A residential address inside the community, not on a boundary street.
- **Pair LLM intake output with manual verification**: Geocode every street name the LLM produces against Toronto ArcGIS to catch hallucinated names.

### For System Maintainers

- **Do not add NLP parsing of the description field**: The two-stage separation is intentional. Stage 1 (NLP) is external and replaceable; Stage 2 (GIS) is deterministic and testable.
- **Extend `ROAD_NAME_ALIASES` sparingly**: It's a cache, not a requirement. If it grows large, consider an on-disk lookup file rather than a dict in code.
- **Preserve individual LineString segments**: Do not flatten ArcGIS path arrays into a single coordinate list. `linemerge()` requires separate LineString objects.

## Validation Checklist for New Community JSON

- [ ] JSON parses without syntax errors
- [ ] `community_name` is non-empty
- [ ] `reference_point` has `address` or `lat`/`lon`
- [ ] Reference point address is verifiable on Google Maps and is inside the community
- [ ] `boundaries` array has minimum 2 entries
- [ ] Each boundary has `feature_name`, `feature_type`, and `compass_direction`
- [ ] `feature_type` values are "street" or "waterway"
- [ ] `compass_direction` values are valid (north/south/east/west or compounds like west_and_south)
- [ ] `feature_name` values use colloquial names from the community description
- [ ] Boundaries are in perimeter order (each connects to the next)
- [ ] No waterway appears twice with single directions (use compound instead)
- [ ] Compass directions are consistent with reference point location
- [ ] If `zoning_exception` included, exception number is confirmed in Toronto's zoning layer

## Cross-References

- [Community Boundary Polygon Generation](../integration-issues/community-boundary-polygon-generation.md) - Original pipeline architecture with both approaches
- [GIS Name Resolution and Polygon Accuracy](gis-name-resolution-polygon-accuracy.md) - Detailed name resolution pipeline and corner-finding strategies
- [Boundary Intake Prompt](../../../prompts/boundary_intake.md) - The LLM prompt for Stage 1 (plain English to JSON)
- [CLAUDE.md](../../../CLAUDE.md) - Project architecture reference with critical implementation details
- [README.md](../../../README.md) - User-facing documentation with JSON schema specification
