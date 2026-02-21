"""
Configuration for Property Report Validation scripts.
API keys loaded from environment variables; all Toronto GIS endpoints hardcoded.
"""

import os

# --- Address to validate (override via command-line) ---
DEFAULT_ADDRESS = "9 Ashton Manor, Etobicoke, ON, Canada"

# --- Geocoding ---
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "PropertyReportValidator/1.0"

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# --- Toronto ArcGIS REST API ---
ARCGIS_BASE = "https://gis.toronto.ca/arcgis/rest/services"

LAYER_ZONING_AREA = {
    "url": f"{ARCGIS_BASE}/cot_geospatial11/FeatureServer/3/query",
    "name": "Zoning Area (By-law 569-2013)",
}
LAYER_ZONING_FORMER_MUNIC = {
    "url": f"{ARCGIS_BASE}/cot_geospatial11/FeatureServer/8/query",
    "name": "Zoning Former Municipality Bylaws",
}
LAYER_MTSA = {
    "url": f"{ARCGIS_BASE}/cot_geospatial11/FeatureServer/65/query",
    "name": "Major Transit Station Area (MTSA/PMTSA)",
}
LAYER_NEIGHBOURHOOD = {
    "url": f"{ARCGIS_BASE}/cot_geospatial26/FeatureServer/71/query",
    "name": "Neighbourhood (2022 boundaries)",
}
LAYER_WARD = {
    "url": f"{ARCGIS_BASE}/cot_geospatial27/FeatureServer/5/query",
    "name": "City Ward (2022)",
}
LAYER_COMMUNITY_PLANNING = {
    "url": f"{ARCGIS_BASE}/cot_geospatial11/FeatureServer/47/query",
    "name": "Community Planning Boundary",
}

# --- Boundary validation layers ---
LAYER_ROAD_CENTRELINE = {
    "url": f"{ARCGIS_BASE}/cot_geospatial2/FeatureServer/2/query",
    "name": "Road Centreline (Toronto Centreline)",
}
LAYER_WATERLINE = {
    "url": f"{ARCGIS_BASE}/cot_geospatial3/FeatureServer/15/query",
    "name": "Water Line (Watercourses)",
}
LAYER_PROPERTY_BOUNDARY = {
    "url": f"{ARCGIS_BASE}/cot_geospatial27/FeatureServer/36/query",
    "name": "Property Boundary",
}

# --- Output ---
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
