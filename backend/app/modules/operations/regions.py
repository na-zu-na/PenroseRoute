"""Assign delivery destinations to the same five map regions used by the UI."""

import gzip
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _regions():
    with gzip.open(Path(__file__).with_name("regions.json.gz"), "rt", encoding="utf-8") as source:
        coordinates = json.load(source)
    return [
        (name, [
            [(ring, (
                min(point[0] for point in ring), min(point[1] for point in ring),
                max(point[0] for point in ring), max(point[1] for point in ring),
            )) for ring in polygon]
            for polygon in polygons
        ])
        for name, polygons in coordinates.items()
    ]


def _inside(longitude: float, latitude: float, ring, bounds) -> bool:
    if not (bounds[0] <= longitude <= bounds[2] and bounds[1] <= latitude <= bounds[3]):
        return False
    inside = False
    for index in range(len(ring)):
        first = ring[index - 1]
        second = ring[index]
        if ((first[1] > latitude) != (second[1] > latitude)
                and longitude < (second[0] - first[0]) * (latitude - first[1])
                / (second[1] - first[1]) + first[0]):
            inside = not inside
    return inside


@lru_cache(maxsize=4096)
def destination_region(longitude: float, latitude: float) -> str | None:
    for name, polygons in _regions():
        for rings in polygons:
            if (_inside(longitude, latitude, *rings[0])
                    and not any(_inside(longitude, latitude, *hole) for hole in rings[1:])):
                return name
    return None
