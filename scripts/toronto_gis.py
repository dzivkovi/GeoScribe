"""
Client for Toronto's ArcGIS REST Feature Services.
Performs point-in-polygon spatial queries against city data layers.

All spatial operations (point-in-polygon) are performed server-side by ArcGIS.
This client only sends coordinates and receives attribute results.
"""

import requests
from config import (
    LAYER_ZONING_AREA, LAYER_ZONING_FORMER_MUNIC, LAYER_MTSA,
    LAYER_NEIGHBOURHOOD, LAYER_WARD, LAYER_COMMUNITY_PLANNING,
    LAYER_ROAD_CENTRELINE, LAYER_WATERLINE,
)


def _query_layer(layer_config, lat, lon, out_fields="*", extra_params=None):
    """
    Execute a point-in-polygon spatial query against a Toronto ArcGIS layer.

    Args:
        layer_config: dict with "url" and "name" keys from config.py
        lat: Latitude (WGS84)
        lon: Longitude (WGS84)
        out_fields: Comma-separated field names or "*"
        extra_params: Additional query parameters

    Returns:
        List of feature attribute dicts (empty if point outside all polygons)
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }
    if extra_params:
        params.update(extra_params)

    response = requests.get(layer_config["url"], params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        err = data["error"]
        raise ValueError(
            f"ArcGIS error on {layer_config['name']}: "
            f"[{err.get('code', '?')}] {err.get('message', 'unknown')}"
        )

    features = data.get("features", [])
    return [f["attributes"] for f in features]


def _query_where(layer_config, where_clause, out_fields="*", return_geometry=False,
                 out_sr="4326", extra_params=None):
    """
    Query a layer by attribute WHERE clause (not spatial).
    Optionally returns geometry in the specified spatial reference.

    Returns:
        List of dicts. Each dict has 'attributes' and optionally 'geometry'.
    """
    params = {
        "where": where_clause,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "f": "json",
    }
    if return_geometry:
        params["outSR"] = out_sr
    if extra_params:
        params.update(extra_params)

    response = requests.get(layer_config["url"], params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        err = data["error"]
        raise ValueError(
            f"ArcGIS error on {layer_config['name']}: "
            f"[{err.get('code', '?')}] {err.get('message', 'unknown')}"
        )

    features = data.get("features", [])
    if return_geometry:
        return [{"attributes": f["attributes"], "geometry": f.get("geometry", {})}
                for f in features]
    return [f["attributes"] for f in features]


def query_exception_zone(exception_number, zone_type=None, near_lat=None, near_lon=None,
                          radius=0.015):
    """
    Query zoning parcels with a given exception number.

    Args:
        exception_number: e.g. 42
        zone_type: Filter by zone type e.g. "RD" (important: exception numbers
                   are reused across zone types city-wide)
        near_lat, near_lon: Center point for spatial filter
        radius: Bounding box half-width in degrees (~1.7km per 0.015 deg)

    Returns:
        dict with parcel_count, zone_types, bounding_box, and raw features.
    """
    where = f"ZN_EXCPTN_NO = {exception_number}"
    if zone_type:
        where += f" AND ZN_ZONE = '{zone_type}'"

    extra_params = {}
    if near_lat is not None and near_lon is not None:
        envelope = f"{near_lon - radius},{near_lat - radius},{near_lon + radius},{near_lat + radius}"
        extra_params = {
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }

    features = _query_where(
        LAYER_ZONING_AREA,
        where_clause=where,
        out_fields="ZN_ZONE,ZN_STRING,ZN_EXCPTN_NO,ZBL_EXCPTN",
        return_geometry=True,
        extra_params=extra_params if extra_params else None,
    )

    if not features:
        return {"error": f"No zoning parcels found with exception {exception_number}"}

    # Compute bounding box from all polygon rings
    all_lons = []
    all_lats = []
    for f in features:
        geom = f.get("geometry", {})
        for ring in geom.get("rings", []):
            for coord in ring:
                all_lons.append(coord[0])
                all_lats.append(coord[1])

    zone_types = sorted(set(f["attributes"].get("ZN_ZONE", "") for f in features))
    zoning_strings = [f["attributes"].get("ZN_STRING", "") for f in features]

    return {
        "exception_number": exception_number,
        "parcel_count": len(features),
        "zone_types": zone_types,
        "zoning_strings": zoning_strings,
        "bounding_box": {
            "min_lat": min(all_lats) if all_lats else None,
            "max_lat": max(all_lats) if all_lats else None,
            "min_lon": min(all_lons) if all_lons else None,
            "max_lon": max(all_lons) if all_lons else None,
        },
        "features": features,
    }


def query_road_geometry(road_name, near_lat=None, near_lon=None, radius=0.01):
    """
    Get the centreline geometry of a named road.

    Args:
        road_name: e.g. "Royal York Rd", "Bloor St W"
        near_lat, near_lon: Center point -- limits query to nearby segments
        radius: Bounding box half-width in degrees (~1.1km per 0.01 deg)

    Returns:
        List of [lon, lat] coordinate pairs forming the road line.
    """
    where = f"LINEAR_NAME_FULL = '{road_name}'"

    # Use spatial envelope to get only segments near the property
    extra_params = {}
    if near_lat is not None and near_lon is not None:
        envelope = f"{near_lon - radius},{near_lat - radius},{near_lon + radius},{near_lat + radius}"
        extra_params = {
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }

    features = _query_where(
        LAYER_ROAD_CENTRELINE,
        where_clause=where,
        out_fields="LINEAR_NAME_FULL",
        return_geometry=True,
        extra_params=extra_params if extra_params else None,
    )

    if not features:
        raise ValueError(f"No road segments found for: {road_name}"
                         + (f" near ({near_lat}, {near_lon})" if near_lat else ""))

    # Extract all coordinates from all path segments
    all_coords = []
    for f in features:
        geom = f.get("geometry", {})
        for path in geom.get("paths", []):
            for coord in path:
                all_coords.append(coord)  # [lon, lat]

    return all_coords


def query_waterline_geometry(waterline_name):
    """
    Get the geometry of a named waterline (creek, river).

    Returns:
        List of [lon, lat] coordinate pairs forming the waterline.
    """
    features = _query_where(
        LAYER_WATERLINE,
        where_clause=f"WATERLINE_NAME = '{waterline_name}'",
        out_fields="WATERLINE_NAME",
        return_geometry=True,
    )

    if not features:
        raise ValueError(f"No waterline segments found for: {waterline_name}")

    all_coords = []
    for f in features:
        geom = f.get("geometry", {})
        for path in geom.get("paths", []):
            for coord in path:
                all_coords.append(coord)

    return all_coords


def query_zoning(lat, lon):
    """
    Get zoning designation for a point under By-law 569-2013.

    Returns:
        dict with zone info, or error dict if no zoning found.
    """
    results = _query_layer(LAYER_ZONING_AREA, lat, lon)
    if not results:
        return {"error": "No zoning data found (property may be outside Toronto or on a boundary)"}

    attr = results[0]
    has_exception = attr.get("ZN_EXCPTN", "N") == "Y"

    return {
        "zone": attr.get("ZN_ZONE", ""),
        "zoning_string": attr.get("ZN_STRING", ""),
        "has_exception": has_exception,
        "exception_number": attr.get("ZN_EXCPTN_NO") if has_exception else None,
        "min_frontage_m": attr.get("ZN_FRONTAGE"),
        "min_area_sqm": attr.get("ZN_AREA"),
        "fsi_density": attr.get("ZN_FSI_DENSITY"),
        "bylaw_exception_link": attr.get("BYLAW_EXCPTNLINK", ""),
        "bylaw_section": attr.get("ZBL_EXCPTN", ""),
        "raw": attr,
    }


def query_former_municipality_bylaw(lat, lon):
    """
    Check if property is still under a former municipality by-law (not yet under 569-2013).
    Returns None if the property IS under 569-2013 (no former bylaw applies).
    """
    results = _query_layer(LAYER_ZONING_FORMER_MUNIC, lat, lon)
    if not results:
        return None  # Property is under 569-2013

    attr = results[0]
    return {
        "bylaw_name": attr.get("BL_NAME", ""),
        "bylaw_number": attr.get("BL_NO", ""),
        "district": attr.get("DISTRICT", ""),
        "raw": attr,
    }


def query_mtsa(lat, lon):
    """
    Check if property falls within a Major Transit Station Area (MTSA/PMTSA).
    Returns None if outside all MTSA boundaries.
    """
    results = _query_layer(LAYER_MTSA, lat, lon)
    if not results:
        return None

    attr = results[0]
    return {
        "station_name": attr.get("STATION_NAME", ""),
        "mtsa_type": attr.get("MTSA_TYPE", ""),
        "sasp_number": attr.get("SASP_NUMBER", ""),
        "raw": attr,
    }


def query_neighbourhood(lat, lon):
    """
    Get the City of Toronto neighbourhood (2022 boundaries).
    Note: This returns the official City neighbourhood, NOT community associations
    like Thompson Orchard (which are informal boundaries).
    """
    results = _query_layer(LAYER_NEIGHBOURHOOD, lat, lon)
    if not results:
        return {"error": "No neighbourhood data found"}

    attr = results[0]
    return {
        "name": attr.get("AREA_NAME", ""),
        "number": attr.get("AREA_SHORT_CODE", attr.get("AREA_S_CD", "")),
        "description": attr.get("AREA_DESC", ""),
        "classification": attr.get("CLASSIFICATION", ""),
        "raw": attr,
    }


def query_ward(lat, lon):
    """Get the City of Toronto municipal ward."""
    results = _query_layer(LAYER_WARD, lat, lon)
    if not results:
        return {"error": "No ward data found"}

    attr = results[0]
    return {
        "name": attr.get("AREA_NAME", ""),
        "number": attr.get("AREA_SHORT_CODE", attr.get("AREA_S_CD", "")),
        "description": attr.get("AREA_DESC", ""),
        "raw": attr,
    }


def query_community_planning(lat, lon):
    """Get the Community Planning Boundary area."""
    results = _query_layer(LAYER_COMMUNITY_PLANNING, lat, lon)
    if not results:
        return {"error": "No community planning data found"}

    attr = results[0]
    return {
        "name": attr.get("AREA_NAME", ""),
        "district": attr.get("DISTRICT", ""),
        "raw": attr,
    }


def query_all(lat, lon):
    """
    Run all spatial queries and return combined results.

    Returns:
        dict with keys: zoning, former_bylaw, mtsa, neighbourhood, ward, community_planning
        Each value is the result dict from the corresponding query function.
    """
    results = {}

    queries = [
        ("zoning", query_zoning),
        ("former_bylaw", query_former_municipality_bylaw),
        ("mtsa", query_mtsa),
        ("neighbourhood", query_neighbourhood),
        ("ward", query_ward),
        ("community_planning", query_community_planning),
    ]

    for name, func in queries:
        try:
            results[name] = func(lat, lon)
        except Exception as e:
            results[name] = {"error": str(e)}

    return results
