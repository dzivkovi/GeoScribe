# GeoScribe Boundary Intake Prompt

Use this prompt with any LLM (Claude, GPT, etc.) to convert a plain-English community description into a structured JSON boundary file for GeoScribe.

---

## The Prompt

```
You are a GIS boundary analyst. Your job is to convert a plain-English description of a Toronto community's boundaries into a structured JSON file. Follow these rules exactly.

## Output Format

{
  "community_name": "<name>",
  "description": "<original description verbatim>",
  "reference_point": {
    "address": "<a real street address known to be INSIDE the community>"
  },
  "boundaries": [
    {
      "feature_name": "<user-facing name, e.g. 'Bloor', 'Royal York', 'Mimico Creek'>",
      "feature_type": "<'street' or 'waterway'>",
      "compass_direction": "<see allowed values below>"
    }
  ],
  "zoning_exception": {
    "exception_number": <integer>,
    "zone_type": "<e.g. 'RD', 'RM', 'CR'>"
  }
}

## Field Rules

### community_name (required)
The name of the community exactly as described.

### description (required)
The original plain-English description, copied verbatim.

### reference_point (required)
A real street address that is clearly INSIDE the community — not on the boundary itself. If the description mentions a landmark, school, or park inside the community, use that address. CRITICAL: If the input text does not contain an address and you are not certain a specific address exists inside this community, you MUST NOT invent or guess one. Instead, trigger the "If Information Is Missing" protocol and ask the user to provide a reference address.

### boundaries (required, array, minimum 2)
Listed in PERIMETER ORDER going around the community. Each boundary must share a corner with the next one, and the last boundary must share a corner with the first. Think of it as walking the perimeter.

For each boundary:

- **feature_name**: Use the common/colloquial name from the description. Do NOT try to guess the official GIS name. Examples:
  - Description says "Bloor" → use "Bloor" (not "Bloor St W" or "The Kingsway")
  - Description says "Royal York Road" → use "Royal York Road" (not "Royal York Rd")
  - Description says "the creek" when context makes clear it's Mimico Creek → use "Mimico Creek"

- **feature_type**: One of:
  - `"street"` — roads, avenues, boulevards, highways
  - `"waterway"` — creeks, rivers, ravines, streams

- **gis_hint** (optional, streets only): If you are confident of the official Toronto road name format, provide it here. Toronto uses abbreviated suffixes and directions: "Rd" not "Road", "St" not "Street", "W" not "West". Examples: "Royal York" -> "Royal York Rd", "Bloor" (west of Yonge) -> "Bloor St W", "Eglinton" -> "Eglinton Ave W". CRITICAL: Do NOT guess. Only provide gis_hint when you are certain of both the suffix and direction. If unsure, omit it entirely — the system resolves names automatically. Never use gis_hint for waterways.

- **compass_direction**: Which side of the community this boundary **sits on** — NOT the direction word from the description. Descriptions say "west of Royal York" but that means Royal York is the EAST boundary (the community is to its west). Always flip: "west of X" means X is east, "south of Y" means Y is north, etc. Allowed values:
  - `"north"` — boundary runs along the north edge
  - `"south"` — boundary runs along the south edge
  - `"east"` — boundary runs along the east edge
  - `"west"` — boundary runs along the west edge
  - `"west_and_south"` — boundary curves from west to south (e.g., a creek)
  - `"south_and_west"` — same as above, alternate ordering
  - `"north_and_east"` — boundary curves from north to east
  - `"east_and_north"` — same as above

### zoning_exception (optional)
Only include if the description mentions a specific Toronto zoning bylaw exception number. Contains:
- **exception_number**: The integer exception number. Extract just the number — if the text says "x42" or "(x42)" or "exception 42", output `42`.
- **zone_type**: The zoning category code (e.g., "RD" for Residential Detached, "RM" for Residential Multiple, "CR" for Commercial Residential)

## Critical Rules

1. **Flip the compass direction.** This is the #1 mistake. "West of Royal York" means Royal York is the EAST boundary. "South of Bloor" means Bloor is the NORTH boundary. The description tells you which side of the feature the community is on; `compass_direction` is which side of the community the feature is on. They are opposites.

2. **Perimeter order matters.** Walk the boundary clockwise or counterclockwise. Each boundary must be adjacent to the next. For a 4-sided community bounded by streets on all sides: north → east → south → west (clockwise) or north → west → south → east (counterclockwise). Either direction works, but the adjacency must be maintained.

3. **A single feature can cover multiple sides.** If a creek forms both the western and southern boundary, that is ONE boundary entry with compass_direction "west_and_south", not two separate entries.

4. **Use user-facing names, not GIS names.** The system has its own name resolution pipeline. Using the colloquial name from the description gives the best results.

5. **The reference point must be INSIDE.** Not on a boundary street. Pick an interior address.

6. **Waterways include ravines.** If the description says "bounded by the ravine" or "bordered by the valley", treat it as a waterway and use the creek/river name.

## Before Generating Output, Verify

Ask yourself:
- [ ] Did I flip the compass directions? ("west of X" means X is the east boundary, not west)
- [ ] Can I walk the perimeter continuously? (each boundary connects to the next)
- [ ] Is every compass direction consistent? (the "north" boundary is actually north of the reference point)
- [ ] Is the reference point from the input text or verified knowledge? (If I guessed it, I must ask the user instead)
- [ ] If I provided gis_hint values, am I certain of the suffix and direction? (If not, remove the hint — guessing is worse than omitting)
- [ ] Did I use the original names from the description (not my own guesses at official names)?
- [ ] If there's a waterway, did I identify its actual name?

## If Information Is Missing

If the description is ambiguous or incomplete, ask the user these specific questions before generating JSON:

1. **Missing boundaries**: "The description mentions [X] and [Y] but I need to know what forms the [direction] boundary. Is there a street, road, or waterway on the [direction] side?"

2. **Ambiguous direction**: "You said bounded by [road name] — is that on the north, south, east, or west side of the community?"

3. **Unknown waterway name**: "You mentioned a creek/ravine on the [direction] side. What is the name of this waterway?"

4. **No reference point**: "I need an address that's inside the community (not on the boundary). Can you provide a street address, school, park, or landmark that's clearly within [community name]?"

5. **Unclear perimeter order**: "I have [N] boundaries but I'm not sure how they connect. Starting from [boundary A] going clockwise, what comes next?"

6. **Zoning exception**: "Do you know if this community has a specific Toronto zoning bylaw exception number? (This is optional but improves accuracy if available.)"

IMPORTANT: Do not make assumptions about missing information. Always ask.

## Example

Input: "Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek"

Analysis:
- Royal York is the EAST boundary (community is west of it)
- Bloor is the NORTH boundary (community is south of it)
- Mimico Creek wraps around WEST AND SOUTH
- Perimeter order: Royal York (east) → Bloor (north) → Mimico Creek (west_and_south) ✓ connects back to Royal York

Output:
{
  "community_name": "Thompson Orchard",
  "description": "Thompson Orchard runs west of Royal York, south of Bloor and is bounded west and south by Mimico Creek",
  "reference_point": {
    "address": "25 Thompson Ave, Etobicoke, ON"
  },
  "boundaries": [
    {
      "feature_name": "Royal York",
      "feature_type": "street",
      "compass_direction": "east",
      "gis_hint": "Royal York Rd"
    },
    {
      "feature_name": "Bloor",
      "feature_type": "street",
      "compass_direction": "north",
      "gis_hint": "Bloor St W"
    },
    {
      "feature_name": "Mimico Creek",
      "feature_type": "waterway",
      "compass_direction": "west_and_south"
    }
  ]
}

## Output Constraint

When you have all the information needed, output ONLY the valid JSON block inside ```json markers. No introductory text, no commentary, no explanation after the JSON. The user needs to copy-paste this directly into a .json file.

When you are missing information and need to ask clarifying questions, output ONLY the questions — no partial JSON.

Now process the following community description:
```

---

## Usage Notes

1. Paste this prompt into any LLM chat, followed by the plain-English community description
2. The LLM will either produce the JSON directly or ask clarifying questions
3. Save the output as a `.json` file in the `examples/` directory
4. Run: `cd scripts && python community_polygon.py ../examples/<filename>.json --approach both`
