# GeoScribe

Turn plain-English community descriptions into real GIS polygons.

> *"Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek"*

GeoScribe takes that sentence, queries Toronto's live ArcGIS and OpenStreetMap APIs, and produces an interactive map, GeoJSON, and KML — no GIS expertise required.

## Usage

### 1. Install and run

```bash
pip install -r requirements.txt

cd scripts
python community_polygon.py ../examples/thompson_orchard.json --approach both
```

Three files appear in `output/`:

| File | What you get |
|------|-------------|
| `thompson_orchard_*.html` | Interactive Leaflet map — open in any browser |
| `thompson_orchard_*.geojson` | Standard GeoJSON polygon |
| `thompson_orchard_*.kml` | Google Earth / Google My Maps format |

Or skip straight to the pre-generated output: open `examples/sample_output/thompson_orchard.html` in a browser.

### 2. Describe your community

Create a JSON file. Use the names people actually say — GeoScribe resolves them to official GIS names automatically:

```json
{
  "community_name": "Thompson Orchard",
  "description": "Thompson Orchard runs west of Royal York, south of Bloor ...",
  "reference_point": {
    "address": "25 Thompson Ave, Etobicoke, ON"
  },
  "boundaries": [
    { "feature_name": "Royal York",   "feature_type": "street",   "compass_direction": "east" },
    { "feature_name": "Bloor",        "feature_type": "street",   "compass_direction": "north" },
    { "feature_name": "Mimico Creek", "feature_type": "waterway", "compass_direction": "west_and_south" }
  ],
  "zoning_exception": {
    "exception_number": 42,
    "zone_type": "RD"
  }
}
```

| Field | Required | Description |
| --- | --- | --- |
| `community_name` | Yes | Display name |
| `description` | No | Human-readable boundary description |
| `reference_point.address` | Yes* | An address *inside* the community (used for orientation and validation) |
| `reference_point.lat/lon` | Yes* | Coordinates (alternative to address) |
| `boundaries[]` | Yes | Boundary features in **perimeter order** (each shares a corner with the next) |
| `boundaries[].feature_name` | Yes | Everyday name — "Bloor", "Royal York", "Mimico Creek". GeoScribe resolves to GIS names. |
| `boundaries[].feature_type` | Yes | `street`, `waterway`, or `railway` |
| `boundaries[].compass_direction` | Yes | Where this boundary sits: `north`, `south`, `east`, `west`, `west_and_south`, etc. |
| `zoning_exception` | No | Enables Approach B (zoning parcel union) |

*One of `address` or `lat/lon` required.

### 3. View the results

**HTML map** — double-click the `.html` file. Pan, zoom, toggle between street and satellite layers.

**KML** — open in any of these:

| Tool | How to load |
|------|------------|
| [Google Earth Web](https://earth.google.com/web) | Menu (top-left) → Projects → New project → Import KML file |
| [Google My Maps](https://www.google.com/mymaps) | Create a new map → Import → upload the `.kml` |
| [geojson.io](https://geojson.io) | Drag and drop the `.kml` or `.geojson` onto the map |
| [QGIS](https://qgis.org) (desktop, free) | Layer → Add Vector Layer → select the file |

Google Earth Web is the most satisfying — you get satellite imagery underneath the polygon and can compare it to the actual neighbourhood.

**GeoJSON** — works with any GIS tool or mapping library (Leaflet, Mapbox, Deck.gl). Paste directly into [geojson.io](https://geojson.io) for instant visualization and editing.

### 4. Validate an address

Check whether a specific address falls within community boundaries:

```bash
python validate.py "9 Ashton Manor, Etobicoke, ON"

# JSON-only output
python validate.py --json-only "9 Ashton Manor, Etobicoke, ON"

# Standalone boundary check
python boundary_check.py "9 Ashton Manor, Etobicoke, ON" --exception 42
```

### 5. CLI options

**Which `--approach` should I use?**

| Flag | When to use it | What happens |
|------|---------------|--------------|
| *(no flag)* | **Most of the time.** Your JSON has both boundaries and a `zoning_exception`. | Runs both approaches, overlays them, shows an IoU agreement score. This is the default. |
| `--approach lines` | Your JSON has **no** `zoning_exception`, OR you're iterating on boundary accuracy. | Traces road/waterway geometry only. This is what most communities will use (not every area has a unique zoning exception). |
| `--approach zoning` | You **only** want the zoning parcel union and don't care about road-tracing. | Faster — skips all geometry work. Only works if your JSON has a `zoning_exception`. |

```bash
# Both approaches (default — recommended when zoning_exception is available)
python community_polygon.py ../examples/thompson_orchard.json

# Only boundary lines (use when no zoning exception exists)
python community_polygon.py ../examples/thompson_orchard.json --approach lines

# Only zoning exception (fast, authoritative when available)
python community_polygon.py ../examples/thompson_orchard.json --approach zoning

# Override reference point
python community_polygon.py ../examples/thompson_orchard.json --address "123 Other St, Toronto"
python community_polygon.py ../examples/thompson_orchard.json --lat 43.6388 --lon -79.5108

# Skip HTML map / custom output dir
python community_polygon.py ../examples/thompson_orchard.json --no-map
python community_polygon.py ../examples/thompson_orchard.json --output-dir ./my_output

# Use Google geocoding (requires GOOGLE_MAPS_API_KEY env var)
python community_polygon.py ../examples/thompson_orchard.json --provider google
```

## How It Works

GeoScribe builds polygons two independent ways:

### Approach A: Boundary Lines → Polygon

1. **Resolve names** — maps everyday names to GIS names (e.g., "Bloor" → "The Kingsway") using exact match, fuzzy LIKE query, or intersection-based lookup
2. **Fetch geometry** — road centrelines and waterways from ArcGIS; falls back to OpenStreetMap for sparse waterway data
3. **Filter by compass** — keeps only segments consistent with each boundary's direction relative to the community centre
4. **Find corners** — multi-strategy pipeline: geocode intersection (Google + Nominatim) with geometry snapping validation → geometric intersection → line extrapolation → nearest points fallback
5. **Clip & assemble** — extracts each boundary between its corners using linear referencing, detects and straightens detour segments, assembles into a closed polygon ring

### Approach B: Zoning Exception Union

1. **Query** all parcels with a specific zoning exception number from Toronto's zoning layer
2. **Union** parcels into a single polygon with `unary_union()`

Authoritative when a zoning exception number is available. When `--approach both` is used, both results are overlaid and compared (IoU score).

## Zoning: What the Exception Number Means

Toronto properties have a zoning label like `RD (f13.5; a510; d0.45) (x42)`. The letters and numbers encode what you can build: zone type (RD = detached houses only), minimum lot size, and maximum house size. The `(x42)` at the end is the important part — it means **site-specific exception rules** override the defaults for this property.

GeoScribe uses these exception numbers in two ways:

- **Drawing community boundaries (Approach B):** All ~330 homes in Thompson Orchard share exception x42. GeoScribe queries the city's database for every property with x42, then merges them into one polygon. This is authoritative — it uses the city's own parcel data.
- **Validating addresses:** `validate.py` automatically retrieves the zoning label, exception number, and boundary checks for any Toronto address.

**Where to find your community's exception number:** Search the property on the [Toronto Interactive Zoning Map](https://map.toronto.ca/maps/map.jsp?app=ZBL_CONSULT), click it, and look for `(x__)` in the zoning label. If all homes in the area share the same exception, that's your input for the `zoning_exception` field.

For a full explanation of Toronto zoning (FSI, setbacks, GFA, how to read the by-law, and why exceptions change everything), see [docs/toronto-zoning.md](docs/toronto-zoning.md).

## Architecture

```
GeoScribe/
├── examples/
│   ├── thompson_orchard.json       Sample boundary description (input)
│   ├── ThompsonOrchard.png         Reference map for validation
│   └── sample_output/              Pre-generated outputs
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

### Data Sources

| Source | What It Provides | API Key |
| --- | --- | --- |
| Toronto ArcGIS REST API | Road centrelines, waterlines, zoning parcels, property boundaries | None |
| Overpass API (OpenStreetMap) | Waterway fallback when ArcGIS data is sparse | None |
| Nominatim | Free geocoding (default) | None |
| Google Maps Geocoding | Higher-accuracy intersection geocoding | Optional: `GOOGLE_MAPS_API_KEY` env var |

## Configuration

All endpoints and constants are in `scripts/config.py`. Output goes to `output/` (auto-created, gitignored).

| Env Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | No | Improves intersection geocoding accuracy. Nominatim is always used as fallback. |

## Adding a New Community

1. Create a JSON file in `examples/` following the schema above
2. Use everyday road/waterway names — GeoScribe resolves them to GIS names
3. List boundaries in perimeter order (clockwise or counter-clockwise)
4. Set `reference_point` to an address known to be *inside* the community
5. If available, include the `zoning_exception` number for Approach B
6. Run: `cd scripts && python community_polygon.py ../examples/your_community.json --approach both`

## Known Limitations

- **Toronto-specific**: Currently configured for Toronto's ArcGIS REST API. Adapting to other cities requires updating `config.py` endpoints and road name normalization.
- **ArcGIS road centreline gaps**: 100-1500m gaps at intersections, especially at bridges/underpasses. The geocode+snap pipeline handles most cases, but complex road geometries (ravines, highway interchanges) may need manual reference point tuning.
- **Sparse waterway data**: ArcGIS waterline layer can be thin. GeoScribe automatically falls back to OpenStreetMap when ArcGIS returns less than 200m of geometry.
- **Curving boundary roads**: When a road curves significantly between corners (detour ratio >2.5x), GeoScribe tries to fetch the actual road geometry from OpenStreetMap within a corridor between the corners. This usually produces a road-following edge. Falls back to a straight line only when no suitable road geometry is found.

## Dependencies

- **requests** — HTTP client for ArcGIS and Overpass API calls
- **numpy** — numerical operations for linear referencing
- **shapely** — computational geometry (polygon construction, line merging, spatial operations)
- **folium** — interactive HTML map generation on OpenStreetMap tiles
- **simplekml** — KML export for Google Earth

No heavy GIS installations required (no GDAL, no PostGIS, no desktop GIS software).
