"""
Generate structured validation reports in Markdown and JSON formats.
"""

import json
import os
from datetime import datetime
from config import OUTPUT_DIR


def generate_report(address, geocode_result, spatial_results, boundary_results=None):
    """
    Assemble all validation results into a structured report dict.
    """
    report = {
        "meta": {
            "address_input": address,
            "timestamp": datetime.now().isoformat(),
            "geocoding_provider": geocode_result.get("source", "unknown"),
        },
        "location": {
            "lat": geocode_result["lat"],
            "lon": geocode_result["lon"],
            "display_name": geocode_result.get("display_name", ""),
            "neighbourhood_from_geocoder": geocode_result.get("neighbourhood", ""),
            "city": geocode_result.get("city", ""),
            "province": geocode_result.get("province", ""),
            "postcode": geocode_result.get("postcode", ""),
        },
        "zoning": spatial_results.get("zoning"),
        "former_bylaw": spatial_results.get("former_bylaw"),
        "mtsa": spatial_results.get("mtsa"),
        "neighbourhood": spatial_results.get("neighbourhood"),
        "ward": spatial_results.get("ward"),
        "community_planning": spatial_results.get("community_planning"),
    }
    if boundary_results:
        report["boundary_validation"] = boundary_results
    return report


def _section(title, content_lines):
    """Helper to format a markdown section."""
    lines = [f"## {title}", ""]
    lines.extend(content_lines)
    lines.append("")
    return "\n".join(lines)


def format_markdown(report):
    """Format the validation report as readable Markdown."""
    sections = []

    # Header
    sections.append(f"# GIS Validation Report")
    sections.append(f"**Address:** {report['meta']['address_input']}")
    sections.append(f"**Generated:** {report['meta']['timestamp']}")
    sections.append(f"**Geocoder:** {report['meta']['geocoding_provider']}")
    sections.append("")
    sections.append("---")
    sections.append("")

    # Location
    loc = report["location"]
    sections.append(_section("1. Geocoded Location", [
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Coordinates | {loc['lat']}, {loc['lon']} |",
        f"| Resolved Address | {loc['display_name']} |",
        f"| Neighbourhood (geocoder) | {loc.get('neighbourhood_from_geocoder', 'N/A')} |",
        f"| City | {loc.get('city', 'N/A')} |",
        f"| Postal Code | {loc.get('postcode', 'N/A')} |",
    ]))

    # Zoning
    z = report.get("zoning")
    if z and "error" not in z:
        exc_line = "None"
        if z.get("has_exception"):
            exc_line = f"**Yes** - Exception #{z.get('exception_number', '?')}"
            if z.get("bylaw_section"):
                exc_line += f" (Section {z['bylaw_section']})"
        sections.append(_section("2. Zoning (By-law 569-2013)", [
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Zone | **{z.get('zone', 'N/A')}** |",
            f"| Full Zoning String | `{z.get('zoning_string', 'N/A')}` |",
            f"| Site-Specific Exception | {exc_line} |",
            f"| Min Frontage | {z.get('min_frontage_m', 'N/A')} m |",
            f"| Min Lot Area | {z.get('min_area_sqm', 'N/A')} sqm |",
            f"| FSI/Density | {z.get('fsi_density', 'N/A')} |",
        ]))
    elif z:
        sections.append(_section("2. Zoning", [f"**Error:** {z.get('error', 'unknown')}"]))

    # Former Municipality By-law
    fb = report.get("former_bylaw")
    if fb is None:
        sections.append(_section("3. Former Municipality By-law", [
            "Property is governed by **By-law 569-2013** (not a former municipality code).",
            "",
            "This means the zoning data above is authoritative under the current city-wide by-law.",
        ]))
    elif "error" in fb:
        sections.append(_section("3. Former Municipality By-law", [f"**Error:** {fb['error']}"]))
    else:
        sections.append(_section("3. Former Municipality By-law", [
            f"**WARNING:** Property is still under a former municipality by-law.",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| By-law Name | {fb.get('bylaw_name', 'N/A')} |",
            f"| By-law Number | {fb.get('bylaw_number', 'N/A')} |",
            f"| District | {fb.get('district', 'N/A')} |",
        ]))

    # MTSA/PMTSA
    mtsa = report.get("mtsa")
    if mtsa is None:
        sections.append(_section("4. MTSA/PMTSA Status", [
            "Property is **not within** a mapped MTSA/PMTSA boundary in the current GIS data.",
            "",
            "**Note:** Some station boundaries may not yet be published. This does NOT",
            "definitively mean the property is outside all transit station areas.",
        ]))
    elif "error" in mtsa:
        sections.append(_section("4. MTSA/PMTSA Status", [f"**Error:** {mtsa['error']}"]))
    else:
        sections.append(_section("4. MTSA/PMTSA Status", [
            f"Property is **inside** a transit station area.",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Station | {mtsa.get('station_name', 'N/A')} |",
            f"| Type | {mtsa.get('mtsa_type', 'N/A')} |",
            f"| SASP # | {mtsa.get('sasp_number', 'N/A')} |",
        ]))

    # Neighbourhood
    n = report.get("neighbourhood")
    if n and "error" not in n:
        sections.append(_section("5. Official City Neighbourhood", [
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Name | **{n.get('name', 'N/A')}** |",
            f"| Number | {n.get('number', 'N/A')} |",
            f"| Classification | {n.get('classification', 'N/A')} |",
            f"",
            f"**Note:** Community associations (e.g., Thompson Orchard) are informal",
            f"boundaries not tracked in City GIS. The zoning exception (if present)",
            f"is the deterministic proof that a community-specific by-law applies.",
        ]))
    elif n:
        sections.append(_section("5. Neighbourhood", [f"**Error:** {n.get('error', 'unknown')}"]))

    # Ward
    w = report.get("ward")
    if w and "error" not in w:
        sections.append(_section("6. Municipal Ward", [
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Ward | **{w.get('name', 'N/A')}** |",
            f"| Number | {w.get('number', 'N/A')} |",
        ]))

    # Community Planning
    cp = report.get("community_planning")
    if cp and "error" not in cp:
        sections.append(_section("7. Community Planning District", [
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Area | {cp.get('name', 'N/A')} |",
            f"| District | {cp.get('district', 'N/A')} |",
        ]))

    # Boundary Validation (if present)
    bv = report.get("boundary_validation")
    if bv:
        bv_lines = []

        # Exception zone
        zone = bv.get("exception_zone")
        if zone and "error" not in zone:
            bbox = zone.get("bounding_box", {})
            bv_lines.extend([
                f"### Exception Zone Mapping (x{zone.get('exception_number', '?')})",
                f"",
                f"| Field | Value |",
                f"|-------|-------|",
                f"| RD Zoning Polygons | {zone.get('parcel_count', 0)} |",
                f"| Zoning Strings | {', '.join(set(zone.get('zoning_strings', [])))} |",
                f"| Bounding Box | {bbox.get('min_lat', '?'):.4f}-{bbox.get('max_lat', '?'):.4f} lat, "
                f"{bbox.get('min_lon', '?'):.4f}-{bbox.get('max_lon', '?'):.4f} lon |",
                f"",
            ])

        # Boundary checks
        checks = bv.get("boundary_checks", [])
        if checks:
            bv_lines.extend([
                f"### Boundary Checks",
                f"",
                f"Claim: *\"west of Royal York, south of Bloor, bounded by Mimico Creek\"*",
                f"",
                f"| Boundary | Expected | Actual | Offset | Result |",
                f"|----------|----------|--------|--------|--------|",
            ])
            for c in checks:
                check_name = c.get("check", "?")
                direction = c.get("direction", "?").upper()
                offset = c.get("offset_m", "?")
                result = c.get("result", "?")
                marker = "PASS" if result == "PASS" else ("**FAIL**" if result == "FAIL" else result)
                bv_lines.append(f"| {check_name} | {check_name.split(' of ')[0].upper()} | {direction} | ~{offset}m | {marker} |")
            bv_lines.append("")

        # Verdict
        verdict = bv.get("verdict", "")
        detail = bv.get("verdict_detail", "")
        if verdict:
            bv_lines.extend([
                f"### Verdict: **{verdict}**",
                f"",
                f"{detail}",
            ])

        sections.append(_section("8. Community Boundary Validation", bv_lines))

    return "\n".join(sections)


def format_json(report):
    """Format report as indented JSON string."""
    # Strip raw ArcGIS attributes to keep output clean
    clean = _strip_raw(report)
    return json.dumps(clean, indent=2, default=str)


def _strip_raw(obj):
    """Recursively remove 'raw' keys from nested dicts."""
    if isinstance(obj, dict):
        return {k: _strip_raw(v) for k, v in obj.items() if k != "raw"}
    if isinstance(obj, list):
        return [_strip_raw(item) for item in obj]
    return obj


def save_report(report, base_name=None):
    """
    Save report in both Markdown and JSON formats to the output directory.

    Returns:
        tuple: (markdown_path, json_path)
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not base_name:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        addr_slug = report["meta"]["address_input"][:30].replace(" ", "_").replace(",", "")
        base_name = f"validation_{addr_slug}_{ts}"

    md_path = os.path.join(OUTPUT_DIR, f"{base_name}.md")
    json_path = os.path.join(OUTPUT_DIR, f"{base_name}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(format_markdown(report))

    with open(json_path, "w", encoding="utf-8") as f:
        f.write(format_json(report))

    return md_path, json_path
