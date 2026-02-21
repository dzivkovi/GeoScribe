"""
Folium map visualization for community polygons.

Creates interactive HTML maps with community polygon overlays,
boundary lines, and reference point markers on OpenStreetMap tiles.
"""

import folium
from shapely.geometry import mapping


COLOR_MAP = {
    "street": "#e74c3c",
    "waterway": "#2980b9",
    "railway": "#8e44ad",
}


def create_community_map(polygons, boundary_lines=None, metadata=None, reference_point=None):
    """
    Create a Folium map showing community polygon(s) and boundary lines.

    Args:
        polygons: list of (shapely_polygon, label, color) tuples
        boundary_lines: list of (shapely_line, boundary_meta_dict) tuples
        metadata: dict with community_name, etc.
        reference_point: optional (lat, lon) tuple for a marker

    Returns:
        folium.Map
    """
    metadata = metadata or {}
    community_name = metadata.get("community_name", "Community")

    # Center map on first polygon's centroid
    centroid = polygons[0][0].centroid
    m = folium.Map(
        location=[centroid.y, centroid.x],
        zoom_start=15,
        tiles="OpenStreetMap",
    )

    # Add each polygon
    for polygon, label, color in polygons:
        folium.GeoJson(
            mapping(polygon),
            name=label,
            style_function=lambda x, c=color: {
                "fillColor": c,
                "color": c,
                "weight": 2,
                "fillOpacity": 0.2,
            },
            tooltip=label,
        ).add_to(m)

    # Add boundary lines
    if boundary_lines:
        for line, bmeta in boundary_lines:
            color = COLOR_MAP.get(bmeta.get("feature_type", ""), "#333333")
            folium.GeoJson(
                mapping(line),
                name=bmeta["feature_name"],
                style_function=lambda x, c=color: {
                    "color": c,
                    "weight": 4,
                    "opacity": 0.8,
                },
                tooltip=f"{bmeta['feature_name']} ({bmeta.get('feature_type', '')})",
            ).add_to(m)

    # Reference point marker
    if reference_point:
        folium.Marker(
            location=[reference_point[0], reference_point[1]],
            popup=metadata.get("reference_label", "Reference Point"),
            icon=folium.Icon(color="green", icon="home"),
        ).add_to(m)

    folium.LayerControl().add_to(m)
    return m


def save_map(folium_map, output_path):
    """Save folium map to HTML file."""
    folium_map.save(output_path)
    return output_path
