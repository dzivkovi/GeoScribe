# GeoScribe

Turn plain-English community descriptions into real GIS polygons.

> *"Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek"*

GeoScribe takes that sentence, queries live GIS APIs, and produces an interactive map, GeoJSON, and KML -- no GIS expertise required. Add a zoning exception number and it will also union the city's own parcel data into an authoritative polygon.

![Thompson Orchard -- boundary polygon overlaid on OpenStreetMap](images/thompson_orchard_map.png)

**Works outside Toronto too.** Communities anywhere with OpenStreetMap coverage use the Overpass API instead of ArcGIS.

> *"North Richvale is bounded by Major MacKenzie Dr on the north, Yonge St on the east, Carrville Rd on the south, and Bathurst St on the west"*

![North Richvale, Richmond Hill -- 6 km² polygon from OpenStreetMap](images/north_richvale_map.png)

## Usage

### 1. Install and run

```bash
pip install -r requirements.txt

cd scripts

# Toronto community (uses ArcGIS — fast, ~10s)
python community_polygon.py ../examples/thompson_orchard.json --approach both

# Non-Toronto community (uses OpenStreetMap — ~60s due to API throttling)
python community_polygon.py ../examples/north_richvale.json --approach lines
```

Three files appear in `output/`:

| File | What you get |
| ---- | ----------- |
| `*.html` | Interactive Leaflet map — open in any browser |
| `*.geojson` | Standard GeoJSON polygon |
| `*.kml` | Google Earth / Google My Maps format |

Pre-generated outputs for both examples are in `examples/sample_output/`.

### 2. Describe your community

You start with plain English — a sentence like the one at the top of this page. GeoScribe needs that description converted into a structured JSON file before it can query the GIS APIs.

**Option A: Use the intake prompt (recommended).** Paste the prompt from [`prompts/boundary_intake.md`](prompts/boundary_intake.md) into any LLM (Claude, ChatGPT, etc.), followed by your plain-English description. The LLM will either produce the JSON directly or ask you clarifying questions about missing boundaries, directions, or reference points.

**Option B: Write the JSON by hand.** Follow the schema below. Use the names people actually say — GeoScribe resolves them to official GIS names automatically.

Either way, save the result as a `.json` file in `examples/`.

```json
{
  "community_name": "Thompson Orchard",
  "description": "Thompson Orchard runs west of Royal York, south of Bloor ...",
  "reference_point": {
    "address": "25 Thompson Ave, Etobicoke, ON"
  },
  "boundaries": [
    { "feature_name": "Royal York",   "feature_type": "street",   "compass_direction": "east",  "gis_hint": "Royal York Rd" },
    { "feature_name": "Bloor",        "feature_type": "street",   "compass_direction": "north", "gis_hint": "Bloor St W" },
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
| `description` | No | Original plain-English boundary description (display only — not parsed by GeoScribe) |
| `reference_point.address` | Yes* | An address *inside* the community (not on a boundary street) |
| `reference_point.lat/lon` | Yes* | Coordinates (alternative to address) |
| `boundaries[]` | Yes | Boundary features in **perimeter order** (each shares a corner with the next) |
| `boundaries[].feature_name` | Yes | Everyday name — "Bloor", "Royal York", "Mimico Creek". GeoScribe resolves to GIS names. |
| `boundaries[].feature_type` | Yes | `street`, `waterway`, or `railway` |
| `boundaries[].compass_direction` | Yes | Which side of the community this boundary sits on (see pitfalls below) |
| `boundaries[].gis_hint` | No | Official road name (e.g., "Royal York Rd"). Speeds up name resolution. For non-Toronto areas, used directly as the Overpass query name. |
| `zoning_exception` | No | Enables Approach B (zoning parcel union) |

*One of `address` or `lat/lon` required.

#### Common pitfalls

**The compass direction flip.** This is the #1 mistake. When a description says "west of Royal York," that means Royal York is the **east** boundary — the community sits to its west, so Royal York is on the east edge. Always ask: "which side of the community is this feature on?" not "which direction does the description say?"

| Description says | compass_direction should be |
| --- | --- |
| "west of Royal York" | `east` (Royal York is on the east side) |
| "south of Bloor" | `north` (Bloor is on the north side) |
| "north of the lake" | `south` (the lake is on the south side) |

**Don't split wrapping features.** If a creek forms both the west and south boundary, that's ONE entry with `"west_and_south"` — not two separate entries. Splitting it creates a phantom corner where the creek meets itself.

**Use the names people say, not GIS names.** Write "Bloor" not "Bloor St W". Write "Royal York" not "Royal York Rd". GeoScribe has a name resolution pipeline that maps colloquial names to the exact GIS field values. Using official abbreviations can actually cause worse results because they bypass the resolution logic.

**Reference point must be interior.** An address on a boundary street confuses the compass filtering. Pick a house on a residential street clearly inside the community.

### 3. View the results

**HTML map** — double-click the `.html` file. Pan, zoom, toggle between street and satellite layers.

**KML** — open in any of these:

| Tool | How to load |
| ---- | ---------- |
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
| ---- | ------------- | ------------ |
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

1. **Resolve names** — maps everyday names to GIS names (e.g., "Bloor" → "The Kingsway") using exact match, fuzzy LIKE query, or intersection-based lookup. Non-Toronto areas skip this step and use `gis_hint` names directly.
2. **Fetch geometry** — Toronto: road centrelines from ArcGIS, waterways from ArcGIS with OpenStreetMap fallback. Non-Toronto: all geometry from OpenStreetMap via Overpass API (with automatic 12s throttle between requests).
3. **Merge segments** — linemerge joins connected segments. When OSM data fragments badly (divided roads with offset intersection nodes), a spatial chaining fallback sorts and concatenates segments along the road direction.
4. **Filter by compass** — keeps only segments consistent with each boundary's direction relative to the community centre
5. **Find corners** — multi-strategy pipeline: geocode intersection (Google + Nominatim) with geometry snapping validation → geometric intersection → line extrapolation → nearest points fallback
6. **Clip & assemble** — extracts each boundary between its corners using linear referencing, detects and straightens detour segments, assembles into a closed polygon ring

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

```text
GeoScribe/
├── examples/
│   ├── thompson_orchard.json       Toronto example (input)
│   ├── north_richvale.json         Non-Toronto example (input)
│   ├── ThompsonOrchard.png         Reference map for validation
│   └── sample_output/              Pre-generated outputs
│       ├── thompson_orchard.{html,geojson,kml}
│       ├── north_richvale.{html,geojson,kml}
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

| Source | What It Provides | Used When | API Key |
| --- | --- | --- | --- |
| Toronto ArcGIS REST API | Road centrelines, waterlines, zoning parcels, property boundaries | Toronto communities | None |
| Overpass API (OpenStreetMap) | Road and waterway geometry | Non-Toronto communities; Toronto waterway fallback | None |
| Nominatim | Free geocoding (default) | All communities | None |
| Google Maps Geocoding | Higher-accuracy intersection geocoding | All communities (optional) | `GOOGLE_MAPS_API_KEY` env var |

**Overpass API throttling:** The Overpass API enforces per-IP rate limits. GeoScribe automatically waits 12 seconds between requests. A non-Toronto community with 4 street boundaries takes ~60 seconds due to this throttle.

## Configuration

All endpoints and constants are in `scripts/config.py`. Output goes to `output/` (auto-created, gitignored).

| Env Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | No | Improves intersection geocoding accuracy. Nominatim is always used as fallback. |

## Adding a New Community

The starting point is always a plain-English description of the community's boundaries — the kind of sentence a resident would say. GeoScribe itself does not parse natural language; it needs structured JSON. The conversion happens in a separate step, either by you or by an LLM.

**Step 1: Write down the boundaries in plain English.**
Something like: *"Lakeview Village is bounded by Lakeshore on the north, Etobicoke Creek on the west, Lake Ontario on the south, and Dwight Avenue on the east."*

**Step 2: Convert to JSON.**
Paste the [intake prompt](prompts/boundary_intake.md) into any LLM (Claude, ChatGPT, etc.), followed by your description. The LLM produces the JSON or asks clarifying questions. Alternatively, write the JSON by hand following the schema in section 2 above.

Review the output for the pitfalls in section 2 — especially the compass direction flip ("west of X" means X is the east boundary) and wrapping features (one entry with a compound direction, not two entries).

**Step 3: Run GeoScribe.**

```bash
cd scripts && python community_polygon.py ../examples/your_community.json --approach both
```

**Step 4: Review and iterate.**
Open the HTML map. If a boundary looks wrong, check: is the compass direction correct? Is the reference point inside the polygon? Are boundaries in perimeter order? Adjust the JSON and re-run.

## Toronto vs. Non-Toronto Behavior

| | Toronto | Outside Toronto |
| --- | --- | --- |
| **Road data** | ArcGIS centrelines (fast, single request per road) | OpenStreetMap via Overpass (12s throttle between requests) |
| **Waterways** | ArcGIS with OSM fallback | OpenStreetMap via Overpass |
| **Name resolution** | Multi-strategy pipeline (exact, fuzzy, intersection-based) | Uses `gis_hint` directly as Overpass query |
| **Zoning (Approach B)** | Available (Toronto zoning layer) | Not available |
| **Typical run time** | ~10 seconds | ~60 seconds (4 boundaries) |
| **Detection** | Automatic — based on whether reference point falls within Toronto's ArcGIS coverage | |

## Known Limitations

- **ArcGIS road centreline gaps** (Toronto): 100-1500m gaps at intersections, especially at bridges/underpasses. The geocode+snap pipeline handles most cases, but complex road geometries (ravines, highway interchanges) may need manual reference point tuning.
- **OSM divided road zigzag** (non-Toronto): Divided roads (e.g., 4-lane arterials) have separate geometry for each direction. The spatial chaining merge produces a small zigzag (~15m) at the polygon edge. Acceptable for neighbourhood-scale polygons.
- **Road name changes**: Some roads change name mid-block (e.g., "Rutherford Rd" becomes "Carrville Rd" east of Bathurst). Use the name that applies along the community's boundary segment, not the name at the far end of the road.
- **Sparse waterway data**: ArcGIS waterline layer can be thin. GeoScribe automatically falls back to OpenStreetMap when ArcGIS returns less than 200m of geometry.
- **Curving boundary roads**: When a road curves significantly between corners (detour ratio >2.5x), GeoScribe tries to fetch the actual road geometry from OpenStreetMap within a corridor between the corners. Falls back to a straight line only when no suitable road geometry is found.

## Glossary

| Term | What it is |
| --- | --- |
| **ArcGIS** | [Esri's](https://www.esri.com/) geographic information system platform. Toronto publishes road centrelines, waterways, and zoning parcels through an [ArcGIS REST API](https://developers.arcgis.com/rest/) that GeoScribe queries directly -- no API key required. |
| **GeoJSON** | An [open standard](https://geojson.org/) for encoding geographic features (points, lines, polygons) in JSON. Works with virtually every mapping tool. |
| **GIS** | Geographic Information System -- software and data for capturing, storing, and analyzing spatial information. |
| **KML** | [Keyhole Markup Language](https://developers.google.com/kml) -- an XML format for geographic data, used by Google Earth and Google My Maps. |
| **Nominatim** | [OpenStreetMap's free geocoding service](https://nominatim.openstreetmap.org/). Converts addresses and intersection names to coordinates. GeoScribe's default geocoder. |
| **OSM** | [OpenStreetMap](https://www.openstreetmap.org/) -- a collaborative, open-data map of the world. GeoScribe uses OSM data via the Overpass API for areas outside Toronto and as a fallback for Toronto waterways. |
| **Overpass API** | A [read-only API](https://wiki.openstreetmap.org/wiki/Overpass_API) for querying OpenStreetMap data by location and feature type. Free, no API key, but rate-limited per IP. |

## Dependencies

- **requests** — HTTP client for ArcGIS and Overpass API calls
- **numpy** — numerical operations for linear referencing
- **shapely** — computational geometry (polygon construction, line merging, spatial operations)
- **folium** — interactive HTML map generation on OpenStreetMap tiles
- **simplekml** — KML export for Google Earth

No heavy GIS installations required (no GDAL, no PostGIS, no desktop GIS software).

## License

[MIT](LICENSE) -- free to use, modify, and distribute. Just keep the copyright notice.

Built by [MAGMA INC.](https://magmainc.ca)
