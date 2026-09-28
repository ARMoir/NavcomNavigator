#!/usr/bin/env python3
"""Build a NAV-COM .navmap SQLite database from one or more OSM PBF files."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

try:
    import osmium
except ImportError:
    print(
        "Missing dependency: osmium\n"
        "Install the map-building tools with:\n"
        "  python -m pip install -r tools/requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)


EXCLUDED_HIGHWAYS = {
    "footway", "path", "steps", "cycleway", "bridleway",
    "corridor", "construction", "proposed",
}


def classify_poi(tags):
    amenity = tags.get("amenity", "")
    tourism = tags.get("tourism", "")

    if amenity == "fuel":
        return "FUEL", amenity
    if amenity == "parking":
        return "PARKING", amenity
    if amenity in ("restaurant", "cafe", "fast_food"):
        return "FOOD", amenity
    if amenity in ("hospital", "clinic", "pharmacy"):
        return "MEDICAL", amenity
    if tourism in ("hotel", "motel"):
        return "LODGING", tourism
    if tourism in ("attraction", "museum", "viewpoint") or tags.get("historic"):
        return "LANDMARK", tourism or tags.get("historic", "historic")
    return None, ""


def create_schema(conn):
    conn.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE coverage (
            source TEXT PRIMARY KEY,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL
        );

        CREATE TABLE roads (
            id INTEGER PRIMARY KEY,
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            name TEXT,
            highway TEXT NOT NULL,
            maxspeed TEXT,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL,
            geometry TEXT NOT NULL,
            UNIQUE(osm_type, osm_id)
        );

        CREATE VIRTUAL TABLE road_index USING rtree(
            id, min_lat, max_lat, min_lon, max_lon
        );

        CREATE TABLE pois (
            id INTEGER PRIMARY KEY,
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            name TEXT,
            kind TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            UNIQUE(osm_type, osm_id, category)
        );

        CREATE VIRTUAL TABLE poi_index USING rtree(
            id, min_lat, max_lat, min_lon, max_lon
        );

        CREATE INDEX idx_roads_highway ON roads(highway);
        CREATE INDEX idx_pois_category ON pois(category);
        """
    )


class NavMapHandler(osmium.SimpleHandler):
    def __init__(self, conn, source_name):
        super().__init__()
        self.conn = conn
        self.source_name = source_name
        self.min_lat = None
        self.max_lat = None
        self.min_lon = None
        self.max_lon = None
        self.road_count = 0
        self.poi_count = 0

    def update_bounds(self, lat, lon):
        self.min_lat = lat if self.min_lat is None else min(self.min_lat, lat)
        self.max_lat = lat if self.max_lat is None else max(self.max_lat, lat)
        self.min_lon = lon if self.min_lon is None else min(self.min_lon, lon)
        self.max_lon = lon if self.max_lon is None else max(self.max_lon, lon)

    def insert_poi(self, osm_type, osm_id, lat, lon, tags):
        category, kind = classify_poi(tags)
        if not category:
            return

        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO pois
                (osm_type, osm_id, category, name, kind, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                osm_type,
                int(osm_id),
                category,
                tags.get("name", ""),
                kind,
                lat,
                lon,
            ),
        )
        if cur.rowcount:
            poi_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO poi_index VALUES (?, ?, ?, ?, ?)",
                (poi_id, lat, lat, lon, lon),
            )
            self.poi_count += 1

    def node(self, node):
        if not node.location.valid():
            return

        lat = float(node.location.lat)
        lon = float(node.location.lon)
        self.update_bounds(lat, lon)
        self.insert_poi("node", node.id, lat, lon, node.tags)

    def way(self, way):
        highway = way.tags.get("highway", "")
        category, _ = classify_poi(way.tags)
        is_road = bool(highway and highway not in EXCLUDED_HIGHWAYS)

        if not is_road and not category:
            return

        points = []
        for node_ref in way.nodes:
            if node_ref.location.valid():
                points.append(
                    (float(node_ref.location.lat), float(node_ref.location.lon))
                )

        if not points:
            return

        if is_road and len(points) > 1:
            lats = [point[0] for point in points]
            lons = [point[1] for point in points]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)

            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO roads
                    (osm_type, osm_id, name, highway, maxspeed,
                     min_lat, max_lat, min_lon, max_lon, geometry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "way",
                    int(way.id),
                    way.tags.get("name", ""),
                    highway,
                    way.tags.get("maxspeed", ""),
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                    json.dumps(points, separators=(",", ":")),
                ),
            )
            if cur.rowcount:
                road_id = cur.lastrowid
                self.conn.execute(
                    "INSERT INTO road_index VALUES (?, ?, ?, ?, ?)",
                    (road_id, min_lat, max_lat, min_lon, max_lon),
                )
                self.road_count += 1

        if category:
            lat = sum(point[0] for point in points) / len(points)
            lon = sum(point[1] for point in points) / len(points)
            self.insert_poi("way", way.id, lat, lon, way.tags)


def resolve_inputs(values):
    files = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            files.extend(sorted(path.glob("*.osm.pbf")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(value)

    unique = []
    seen = set()
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def build_navmap(name, output, inputs, overwrite=False):
    output = Path(output)
    inputs = resolve_inputs(inputs)

    if not inputs:
        raise RuntimeError("No .osm.pbf input files were found")

    if output.exists():
        if not overwrite:
            raise RuntimeError(
                f"{output} already exists; use --overwrite to replace it"
            )
        output.unlink()

    output.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(output)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        create_schema(conn)

        conn.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [
                ("schema_version", "1"),
                ("name", name),
                ("created_utc", datetime.now(timezone.utc).isoformat()),
            ],
        )
        conn.commit()

        total_roads = 0
        total_pois = 0

        for index, pbf in enumerate(inputs, start=1):
            print(f"[{index}/{len(inputs)}] Reading {pbf.name} ...")
            handler = NavMapHandler(conn, pbf.name)

            conn.execute("BEGIN")
            try:
                handler.apply_file(str(pbf), locations=True)

                if handler.min_lat is None:
                    raise RuntimeError(f"No usable coordinates found in {pbf}")

                conn.execute(
                    """
                    INSERT OR REPLACE INTO coverage
                        (source, min_lat, max_lat, min_lon, max_lon)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        pbf.name,
                        handler.min_lat,
                        handler.max_lat,
                        handler.min_lon,
                        handler.max_lon,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            total_roads += handler.road_count
            total_pois += handler.poi_count
            print(
                f"    added {handler.road_count:,} roads and "
                f"{handler.poi_count:,} POIs"
            )

        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("input_files", str(len(inputs))),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("road_count", str(conn.execute("SELECT COUNT(*) FROM roads").fetchone()[0])),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("poi_count", str(conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0])),
        )
        conn.commit()

        print("Optimizing database ...")
        conn.execute("ANALYZE")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    size_mb = output.stat().st_size / (1024 * 1024)
    print()
    print(f"Created: {output}")
    print(f"Region:  {name}")
    print(f"Size:    {size_mb:,.1f} MB")
    print("NAV-COM will discover this file automatically on its next start.")


def main():
    parser = argparse.ArgumentParser(
        description="Build an offline NAV-COM .navmap database from OSM PBF files."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="One or more .osm.pbf files or directories containing them.",
    )
    parser.add_argument("--name", required=True, help="Display name for the region.")
    parser.add_argument("--output", required=True, help="Output .navmap path.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output file if it already exists.",
    )
    args = parser.parse_args()

    try:
        build_navmap(args.name, args.output, args.inputs, args.overwrite)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
