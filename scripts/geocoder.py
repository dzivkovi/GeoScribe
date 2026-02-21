"""
Geocode a street address to latitude/longitude coordinates.
Supports Nominatim (default, free) and Google Maps Geocoding API (optional).
"""

import requests
import time
from config import (
    NOMINATIM_URL, NOMINATIM_USER_AGENT,
    GOOGLE_GEOCODE_URL, GOOGLE_MAPS_API_KEY,
)


def geocode_nominatim(address):
    """Geocode using OpenStreetMap Nominatim (free, no API key)."""
    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": address,
            "format": "json",
            "addressdetails": 1,
            "limit": 1,
        },
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json()

    if not results:
        raise ValueError(f"Nominatim returned no results for: {address}")

    hit = results[0]
    addr = hit.get("address", {})

    return {
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "display_name": hit.get("display_name", ""),
        "source": "nominatim",
        "neighbourhood": addr.get("neighbourhood", addr.get("suburb", "")),
        "city": addr.get("city", addr.get("town", "")),
        "province": addr.get("state", ""),
        "postcode": addr.get("postcode", ""),
    }


def geocode_google(address):
    """Geocode using Google Maps Geocoding API (requires GOOGLE_MAPS_API_KEY env var)."""
    if not GOOGLE_MAPS_API_KEY:
        raise ValueError(
            "GOOGLE_MAPS_API_KEY environment variable not set. "
            "Set it or use --provider nominatim (default)."
        )

    response = requests.get(
        GOOGLE_GEOCODE_URL,
        params={"address": address, "key": GOOGLE_MAPS_API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK" or not data.get("results"):
        raise ValueError(
            f"Google Geocoding failed: {data.get('status', 'unknown')} - "
            f"{data.get('error_message', 'no results')}"
        )

    result = data["results"][0]
    loc = result["geometry"]["location"]

    # Extract neighbourhood from address components
    neighbourhood = ""
    city = ""
    province = ""
    postcode = ""
    for comp in result.get("address_components", []):
        types = comp.get("types", [])
        if "neighborhood" in types or "neighbourhood" in types:
            neighbourhood = comp["long_name"]
        elif "locality" in types:
            city = comp["long_name"]
        elif "administrative_area_level_1" in types:
            province = comp["long_name"]
        elif "postal_code" in types:
            postcode = comp["long_name"]

    return {
        "lat": loc["lat"],
        "lon": loc["lng"],
        "display_name": result.get("formatted_address", ""),
        "source": "google",
        "neighbourhood": neighbourhood,
        "city": city,
        "province": province,
        "postcode": postcode,
    }


def geocode(address, provider="nominatim"):
    """
    Geocode an address using the specified provider.

    Args:
        address: Full street address string
        provider: "nominatim" (default, free) or "google" (requires API key)

    Returns:
        dict with keys: lat, lon, display_name, source, neighbourhood, city, province, postcode
    """
    if provider == "google":
        return geocode_google(address)
    return geocode_nominatim(address)
