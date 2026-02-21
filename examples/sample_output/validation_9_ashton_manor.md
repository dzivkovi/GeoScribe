# GIS Validation Report
**Address:** 9 Ashton Manor, Etobicoke, ON
**Generated:** 2026-02-20T19:52:29.182128
**Geocoder:** nominatim

---

## 1. Geocoded Location

| Field | Value |
|-------|-------|
| Coordinates | 43.6455385, -79.5052469 |
| Resolved Address | 9, Ashton Manor, Stonegate-Queensway, Etobicoke—Lakeshore, Etobicoke, Toronto, Golden Horseshoe, Ontario, M8Y 2N3, Canada |
| Neighbourhood (geocoder) | Stonegate-Queensway |
| City | Toronto |
| Postal Code | M8Y 2N3 |

## 2. Zoning (By-law 569-2013)

| Field | Value |
|-------|-------|
| Zone | **RD** |
| Full Zoning String | `RD (f13.5; a510; d0.45) (x42)` |
| Site-Specific Exception | **Yes** - Exception #42 (Section 900.3.10(42)) |
| Min Frontage | 13.5 m |
| Min Lot Area | 510 sqm |
| FSI/Density | 0.45 |

## 3. Former Municipality By-law

Property is governed by **By-law 569-2013** (not a former municipality code).

This means the zoning data above is authoritative under the current city-wide by-law.

## 4. MTSA/PMTSA Status

Property is **not within** a mapped MTSA/PMTSA boundary in the current GIS data.

**Note:** Some station boundaries may not yet be published. This does NOT
definitively mean the property is outside all transit station areas.

## 5. Official City Neighbourhood

| Field | Value |
|-------|-------|
| Name | **Stonegate-Queensway** |
| Number | 016 |
| Classification | Not an NIA or Emerging Neighbourhood |

**Note:** Community associations (e.g., Thompson Orchard) are informal
boundaries not tracked in City GIS. The zoning exception (if present)
is the deterministic proof that a community-specific by-law applies.

## 6. Municipal Ward

| Field | Value |
|-------|-------|
| Ward | **Etobicoke-Lakeshore** |
| Number | 03 |

## 7. Community Planning District

| Field | Value |
|-------|-------|
| Area | EY_C |
| District | Etobicoke York |

## 8. Community Boundary Validation

### Exception Zone Mapping (x42)

| Field | Value |
|-------|-------|
| RD Zoning Polygons | 2 |
| Zoning Strings | RD (f15.0; a555; d0.45) (x42), RD (f13.5; a510; d0.45) (x42) |
| Bounding Box | 43.6345-43.6481 lat, -79.5112--79.4916 lon |

### Boundary Checks

Claim: *"west of Royal York, south of Bloor, bounded by Mimico Creek"*

| Boundary | Expected | Actual | Offset | Result |
|----------|----------|--------|--------|--------|
| west of Royal York Rd | WEST | EAST | ~345m | **FAIL** |
| south of Bloor St W | SOUTH | SOUTH | ~300m | PASS |
| east of Mimico Creek | EAST | EAST | ~1116m | PASS |

### Verdict: **BOUNDARY_DISCREPANCY**

DISCREPANCY: Zoning Exception 42 (RD) DOES apply to this parcel, but the property FAILS the textual boundary check: west of Royal York Rd. Passed: south of Bloor St W, east of Mimico Creek. This means TOCA's textual boundary description may be imprecise -- the actual exception zone extends beyond the stated boundaries.
