# Toronto Zoning: A Plain-English Guide

This guide is for realtors, homebuyers, and anyone who needs to understand Toronto zoning without a planning degree. Every example uses real data from Thompson Orchard (Etobicoke).

## Reading a Zoning Label

When you look up a property on the [Toronto Interactive Zoning Map](https://map.toronto.ca/maps/map.jsp?app=ZBL_CONSULT), you'll see a label like this:

```
RD (f13.5; a510; d0.45) (x42)
```

This looks like algebra, but it's just four pieces of information:

| Piece | What it means |
|-------|---------------|
| `RD` | **Zone type** — Residential Detached. Only single-family homes allowed on this lot. |
| `f13.5` | **Frontage** — The lot must be at least 13.5 metres wide (about 44 feet). |
| `a510` | **Area** — The lot must be at least 510 square metres. |
| `d0.45` | **Density (FSI)** — Your house can't be bigger than 45% of the lot area. A 510 sqm lot allows a maximum 229.5 sqm (2,470 sqft) of living space across all floors. |
| `(x42)` | **Exception** — Special rules apply to this property. Exception 42 overrides some of the base rules above. You must look up the exception text to know the real limits. |

## Zone Types

These are the most common ones you'll encounter in residential Toronto:

| Code | What it means | What can be built |
|------|---------------|-------------------|
| **RD** | Residential Detached | Single-family houses only |
| **RS** | Residential Semi-Detached | Semis and detached houses |
| **RT** | Residential Townhouse | Townhouses and lower-density homes |
| **RM** | Residential Multiple | Townhouses, duplexes, low-rise apartments |
| **RA** | Residential Apartment | High-rise apartment buildings |
| **RAC** | Residential Apartment Commercial | Apartments with shops on the ground floor |
| **CR** | Commercial Residential | Mixed commercial and residential |

If you're buying a house in a neighbourhood like Thompson Orchard, you'll almost always see **RD**.

## The Numbers That Matter When Renovating or Rebuilding

These are the numbers that determine how big a house you can build on a lot. They trip up buyers and builders constantly.

### FSI (Floor Space Index) — the big one

FSI controls the **total size of your house** relative to your lot. If your lot is 5,000 sqft and the FSI is 0.45, the maximum house size is 2,250 sqft — period. It doesn't matter how many floors you build.

This is different from **lot coverage**, which only measures the footprint (how much dirt you cover). You could have 30% lot coverage but use two floors to reach your FSI limit.

### GFA (Gross Floor Area) — what counts and what doesn't

GFA is the actual square footage that counts toward your FSI limit. Toronto's rules about what counts are full of surprises:

- **Basements** usually do NOT count toward GFA — as long as more than half the basement is underground.
- **Garages** sometimes DO count. Many zoning exceptions (like Thompson Orchard's x42) include the garage in GFA. This forces a tradeoff: bigger garage means smaller house.
- **Tall ceilings** get penalized. If any room has a ceiling 4.6 metres (15 feet) or higher, the city counts that empty air space TWICE toward your GFA. Want a grand two-storey living room? It costs you usable square footage elsewhere.

### Setbacks — the invisible borders

Setbacks are buffer zones around your property line where you cannot build anything.

- **Front yard**: typically 6 metres from the street
- **Rear yard**: typically 7.5 metres from the back
- **Side yards**: typically 0.9 to 1.2 metres from each neighbour
- **Aggregate side**: the TOTAL of both side yards must meet a minimum (e.g., 2.1m). If you build 0.9m from the left neighbour, you must be at least 1.2m from the right.

### Height — two limits, not one

Toronto imposes two height limits:

- **Maximum height**: The peak of your roof (e.g., 8.5m)
- **Main wall height**: Where the roof meets the exterior wall (e.g., 6.0m). This prevents massive flat-topped boxes. Above the main wall height, your roof must slope inward.

## Exceptions — Why the (x) Number Changes Everything

The base zoning label gives you the default rules. But if you see `(x42)` or `(x18)`, those default rules may be partially or completely overridden.

**What is an exception?** It's a set of custom rules that Toronto City Council approved for a specific area. They're permanent — written into the zoning by-law itself. Every exception is documented in **Chapter 900** of [By-law 569-2013](https://www.toronto.ca/city-government/planning-development/zoning-by-law-preliminary-zoning-reviews/zoning-by-law-569-2013-2/).

**Why they matter:** An exception might allow a bigger house, a smaller lot, different setbacks, or additional uses that the base zone prohibits. Or it might impose *stricter* limits. You cannot know without reading the actual exception text.

**Example — Thompson Orchard (Exception 42):** All ~330 houses in Thompson Orchard share zoning exception RD x42. This exception defines site-specific rules for frontage, density, and lot coverage that apply to every property in the community. It's also what makes it possible for GeoScribe to draw the community boundary — by querying all parcels with x42, we get the exact outline.

### How to look up an exception

1. Go to the [Toronto Interactive Zoning Map](https://map.toronto.ca/maps/map.jsp?app=ZBL_CONSULT)
2. Search for the address
3. Click the property — the zoning label appears (e.g., `RD (f13.5; a510; d0.45) (x42)`)
4. Note the exception number (42 in this case)
5. Look it up in [Chapter 900 of By-law 569-2013](https://www.toronto.ca/zoning/bylaw_amendments/ZBL_NewProvision_Chapter900.htm) under the matching zone type section (900.3 for RD)

Or use GeoScribe's validation tool, which retrieves the exception data automatically:

```bash
cd scripts
python validate.py "9 Ashton Manor, Etobicoke, ON"
```

## How GeoScribe Uses Zoning Data

GeoScribe queries Toronto's public ArcGIS REST API — the same database that powers the Interactive Zoning Map. No API key needed, no fees, no login.

### Drawing community boundaries (Approach B)

When your JSON input includes a `zoning_exception` field:

```json
"zoning_exception": {
    "exception_number": 42,
    "zone_type": "RD"
}
```

GeoScribe asks the city's database: *"Give me every property with exception 42 and zone type RD."* The city returns ~330 property boundary polygons. GeoScribe merges them into one shape — that's your community outline.

This is **authoritative**. It uses the city's own property boundaries, not road geometry or guesswork. When you run `--approach both`, GeoScribe draws the community both ways (road-tracing AND zoning union) and shows how well they agree.

### Validating addresses

When you run `validate.py` with an address, GeoScribe:

1. Finds the property's coordinates
2. Queries the zoning layer — gets the zone type, exception number, frontage, area, FSI
3. If an exception exists, checks whether the property is inside the community boundaries (west of Royal York? south of Bloor? east of Mimico Creek?)
4. Generates a report with all the zoning details and boundary check results

## The Golden Rule for Real Estate

**Never trust an AI's guess about zoning numbers when you can look up the real data.**

ChatGPT might see `(x18)` and guess "18 metres". A zoning tool might calculate buildable area using base RD rules while ignoring the exception that completely changes them. The exception text is the law — everything else is a guess.

GeoScribe retrieves the actual data from Toronto's official database. But for anything involving money (renovation budgets, purchase decisions, variance applications), always verify against the [official by-law text](https://www.toronto.ca/city-government/planning-development/zoning-by-law-preliminary-zoning-reviews/zoning-by-law-569-2013-2/).

## If the Zoning Rules Don't Work for You

When a property owner wants to build something that violates the zoning numbers (even slightly), they must apply for a **Minor Variance** through the [Committee of Adjustment](https://www.toronto.ca/city-government/planning-development/committee-of-adjustment/). This process:

- Costs thousands of dollars in application fees
- Takes months to schedule and decide
- Requires public notice to neighbours (who can object)
- Must pass four legal tests: the variance is minor, desirable, consistent with the zoning by-law's intent, and consistent with the Official Plan

Neighbours appealing a decision can escalate to the Toronto Local Appeal Body (TLAB), adding more time and cost. This is why understanding the actual zoning limits *before* buying is so important.

## Key Links

| Resource | URL |
|----------|-----|
| Toronto Interactive Zoning Map | https://map.toronto.ca/maps/map.jsp?app=ZBL_CONSULT |
| By-law 569-2013 (main page) | https://www.toronto.ca/city-government/planning-development/zoning-by-law-preliminary-zoning-reviews/zoning-by-law-569-2013-2/ |
| Chapter 900 — Site-Specific Exceptions | https://www.toronto.ca/zoning/bylaw_amendments/ZBL_NewProvision_Chapter900.htm |
| Toronto Open Data Portal | https://open.toronto.ca/ |
| Committee of Adjustment | https://www.toronto.ca/city-government/planning-development/committee-of-adjustment/ |
| ArcGIS Services (for developers) | https://gis.toronto.ca/arcgis/rest/services/ |

## Quick Glossary

| Term | Plain English |
|------|--------------|
| **FSI** | Floor Space Index — how big your house can be relative to your lot |
| **GFA** | Gross Floor Area — the total square footage that counts toward the FSI limit |
| **Setback** | How far from the property line you must keep the building |
| **Exception (x)** | Special rules for a specific area that override the base zoning |
| **Minor Variance** | Permission to break a zoning rule slightly (requires application + hearing) |
| **Chapter 900** | The section of the zoning by-law containing all site-specific exceptions |
| **By-law 569-2013** | Toronto's city-wide zoning law (replaced the 6 former municipality by-laws) |
| **ArcGIS** | The City's geographic information system — where all the map data lives |
| **Lot Coverage** | How much of the dirt you can cover with the building footprint |
| **Main Wall Height** | Where the roof starts — above this line, the roof must slope inward |
