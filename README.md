# GeoScribe

Scribe geographic polygons from natural language boundary descriptions.

GeoScribe takes structured community boundary descriptions — street names, waterways, compass directions — and constructs real geographic polygons using live GIS data from Toronto's ArcGIS REST API and OpenStreetMap. Outputs include interactive HTML maps (Folium/Leaflet), GeoJSON, and KML.

**Example input**: *"Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek"*

**Example output**: A polygon of ~330 houses overlaid on an interactive OpenStreetMap, exportable to Google Earth.

## Quick Start

**See it first** — open `examples/sample_output/thompson_orchard.html` in a browser to see what GeoScribe produces.

**Run it yourself:**

```bash
# Install dependencies
pip install -r requirements.txt

# Generate the Thompson Orchard community polygon
cd scripts
python community_polygon.py ../examples/thompson_orchard.json --approach both

# Open the HTML map in your browser
# (output path printed to console, in ../output/)
```

This fetches live geometry from Toronto's ArcGIS servers, constructs the polygon, and exports three files to `output/`:
- `.html` — interactive map (open in any browser)
- `.geojson` — standard GIS format (open at [geojson.io](https://geojson.io))
- `.kml` — Google Earth format

## How It Works

GeoScribe implements two complementary polygon construction approaches:

### Approach A: Boundary Lines to Polygon

1. **Fetch** road/waterway line geometries from Toronto ArcGIS REST API
2. **Merge** segments with Shapely's `linemerge()` (ArcGIS returns roads as disconnected segments)
3. **Find corners** where adjacent boundaries meet (geocoding for street intersections, geometric intersection for street/waterway pairs)
4. **Clip** each boundary line to the relevant segment using linear referencing
5. **Assemble** clipped segments into a closed ring and create the polygon

Best for communities where you know the boundary streets/waterways but don't have a zoning exception number.

### Approach B: Zoning Exception Union

1. **Query** all parcels with a specific zoning exception number from Toronto's zoning layer
2. **Convert** ArcGIS ring geometry to Shapely Polygons
3. **Union** all parcels with `unary_union()` to produce the exact community boundary

Authoritative and precise — produces the official community boundary when a zoning exception number is available.

When both approaches run (`--approach both`), the script overlays both polygons on the same map and computes an Intersection-over-Union score for comparison.

## JSON Input Format

Community boundaries are described in a JSON file:

```json
{
  "community_name": "Thompson Orchard",
  "description": "Thompson Orchard runs west of Royal York, south of Bloor...",
  "reference_point": {
    "address": "9 Ashton Manor, Etobicoke, ON"
  },
  "boundaries": [
    {
      "feature_name": "Royal York Rd",
      "feature_type": "street",
      "compass_direction": "east"
    },
    {
      "feature_name": "The Kingsway",
      "feature_type": "street",
      "compass_direction": "north",
      "note": "Bloor St W becomes The Kingsway in this area"
    },
    {
      "feature_name": "Mimico Creek",
      "feature_type": "waterway",
      "compass_direction": "west_and_south"
    }
  ],
  "zoning_exception": {
    "exception_number": 42,
    "zone_type": "RD"
  }
}
```

| Field | Required | Description |
| --- | --- | --- |
| `community_name` | Yes | Display name for the community |
| `description` | No | Human-readable boundary description |
| `reference_point.address` | Yes* | Address known to be inside the community |
| `reference_point.lat/lon` | Yes* | Coordinates (alternative to address) |
| `boundaries[]` | Yes | Array of boundary features in perimeter order |
| `boundaries[].feature_name` | Yes | Official GIS name (e.g., "Royal York Rd", not "Royal York Road") |
| `boundaries[].feature_type` | Yes | `street`, `waterway`, or `railway` |
| `boundaries[].compass_direction` | Yes | Which side of the community: `north`, `south`, `east`, `west`, `west_and_south`, etc. |
| `zoning_exception` | No | Required for Approach B |

*One of `address` or `lat/lon` is required.

Boundaries must be listed in **perimeter order** — each entry shares a corner with the next, and the last shares a corner with the first.

## CLI Usage

### Polygon Generation

```bash
# Both approaches (default)
python community_polygon.py ../examples/thompson_orchard.json

# Only boundary lines approach
python community_polygon.py ../examples/thompson_orchard.json --approach lines

# Only zoning exception approach
python community_polygon.py ../examples/thompson_orchard.json --approach zoning

# Override reference point
python community_polygon.py ../examples/thompson_orchard.json --address "123 Other St, Toronto"
python community_polygon.py ../examples/thompson_orchard.json --lat 43.6388 --lon -79.5108

# Skip HTML map
python community_polygon.py ../examples/thompson_orchard.json --no-map

# Custom output directory
python community_polygon.py ../examples/thompson_orchard.json --output-dir ./my_output

# Use Google geocoding (requires GOOGLE_MAPS_API_KEY env var)
python community_polygon.py ../examples/thompson_orchard.json --provider google
```

### Boundary Validation

Validate that an address falls within a community's described boundaries:

```bash
# Validate the default address (9 Ashton Manor)
python validate.py

# Validate a specific address
python validate.py "9 Ashton Manor, Etobicoke, ON"

# JSON-only output
python validate.py --json-only "9 Ashton Manor, Etobicoke, ON"

# Standalone boundary check
python boundary_check.py "9 Ashton Manor, Etobicoke, ON" --exception 42
```

## Output Formats

| Format | Extension | Use Case |
| --- | --- | --- |
| HTML Map | `.html` | Interactive visualization — open in any browser, pan/zoom, toggle layers |
| GeoJSON | `.geojson` | Web standard — import into [geojson.io](https://geojson.io), Mapbox, Leaflet, QGIS |
| KML | `.kml` | Google Earth — 3D visualization, sharing with non-technical users |
| Markdown Report | `.md` | Boundary validation results (human-readable) |
| JSON Report | `.json` | Boundary validation results (machine-readable) |

All outputs are saved to `output/` with timestamps: `thompson_orchard_20260220_143000.html`

## Architecture

```
GeoScribe/
├── examples/
│   ├── thompson_orchard.json       Sample boundary description (input)
│   └── sample_output/              Pre-generated reference outputs
│       ├── thompson_orchard.html
│       ├── thompson_orchard.geojson
│       ├── thompson_orchard.kml
│       ├── validation_9_ashton_manor.md
│       └── validation_9_ashton_manor.json
├── scripts/
│   ├── community_polygon.py        Main polygon generation pipeline
│   ├── community_visualize.py      Folium/Leaflet interactive map rendering
│   ├── boundary_check.py           Boundary validation (is address inside community?)
│   ├── validate.py                 Full validation orchestrator
│   ├── toronto_gis.py              Toronto ArcGIS REST API client
│   ├── geocoder.py                 Address geocoding (Nominatim + Google)
│   ├── report_generator.py         Markdown/JSON validation report formatting
│   └── config.py                   API endpoints, layer definitions, constants
└── output/                          Runtime outputs (gitignored)
```

### Dependency Graph

```
community_polygon.py ──→ toronto_gis.py ──→ config.py
                     ──→ geocoder.py ────→ config.py
                     ──→ config.py
                     ──→ community_visualize.py (optional, for HTML maps)

validate.py ──→ geocoder.py ────→ config.py
            ──→ toronto_gis.py ──→ config.py
            ──→ boundary_check.py ──→ geocoder.py, toronto_gis.py, config.py
            ──→ report_generator.py ──→ config.py
```

### Data Sources

| Source | What It Provides | API Key |
| --- | --- | --- |
| Toronto ArcGIS REST API | Road centrelines, waterlines, zoning parcels, property boundaries | None required |
| Overpass API (OpenStreetMap) | Waterway fallback when ArcGIS data is sparse | None required |
| Nominatim | Free geocoding (address → lat/lon) | None required |
| Google Maps Geocoding | Optional higher-accuracy geocoding | `GOOGLE_MAPS_API_KEY` env var |

## Configuration

All configuration is in `scripts/config.py`:

- **`DEFAULT_ADDRESS`** — default address for validation (currently "9 Ashton Manor, Etobicoke, ON, Canada")
- **`ARCGIS_BASE`** — Toronto ArcGIS REST API base URL
- **`LAYER_*`** — individual GIS layer endpoints (zoning, roads, waterlines, property boundaries, etc.)
- **`NOMINATIM_URL`** — OpenStreetMap geocoding endpoint
- **`OUTPUT_DIR`** — output directory (auto-resolved relative to script location)

### Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | No | Google Maps geocoding API key (only if using `--provider google`) |

## Known Limitations

- **Approach A accuracy**: Road centreline data from ArcGIS has 100-200m gaps at intersections. Roads that curve significantly (e.g., through ravines) may produce shifted polygons. Use Approach B when a zoning exception number is available.
- **Road name aliases**: Toronto road names change locally (e.g., Bloor St W becomes The Kingsway). The JSON input must use the official GIS name. Check the `note` field for documenting aliases.
- **Sparse waterway data**: ArcGIS waterline layer can be extremely sparse. GeoScribe automatically falls back to the Overpass API (OpenStreetMap) when ArcGIS returns less than 200m of geometry.
- **Nominatim geocoding**: Returns points on road centrelines, not at intersections. This affects corner detection in Approach A.
- **Toronto-specific**: Currently configured for Toronto's ArcGIS REST API. Adapting to other cities requires updating the layer endpoints in `config.py` and the road name normalization in `community_polygon.py`.

## Adding a New Community

1. Create a JSON file in `examples/` following the schema above
2. Use official GIS road names (check Toronto's open data portal if unsure)
3. List boundaries in perimeter order (clockwise or counter-clockwise)
4. Include a `reference_point` address known to be inside the community
5. If available, include the `zoning_exception` number for authoritative Approach B
6. Run: `cd scripts && python community_polygon.py ../examples/your_community.json --approach both`

## Dependencies

- **requests** — HTTP client for ArcGIS and Overpass API calls
- **numpy** — numerical operations for linear referencing
- **shapely** — computational geometry (polygon construction, line merging, spatial operations)
- **folium** — interactive HTML map generation on OpenStreetMap tiles
- **simplekml** — KML export for Google Earth

No heavy GIS installations required (no GDAL, no PostGIS, no desktop GIS software).
