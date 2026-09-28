import asyncio
from datetime import datetime
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, QObject, QSettings
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtPositioning import QGeoPositionInfo, QGeoPositionInfoSource
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget
)

GREEN = QColor(72, 255, 115)
BRIGHT = QColor(165, 255, 181)
DIM = QColor(27, 125, 53)
FAINT = QColor(12, 62, 27)
BLACK = QColor(1, 7, 3)

FALLBACK_LAT = 42.1168
FALLBACK_LON = -71.8648

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org"
USER_AGENT = "NAV-COM-2006/0.4.3 (personal navigation proof-of-concept)"

BASE_DIR = Path(__file__).resolve().parent
OFFLINE_MAP_DIR = BASE_DIR / "offline_maps"

MAP_RADIUS_M = 4200
MIN_MAP_RADIUS_M = 2500
MAX_MAP_RADIUS_M = 24000
MAP_PREFETCH_MARGIN = 1.25
MAP_FETCH_DEBOUNCE_MS = 350
MAP_CACHE_LIMIT = 8
OFFLINE_RETRY_S = 60
POI_MIN_ZOOM = 0.55
POI_MAX_DRAW = 36
POI_ICONS = {
    "FUEL": "G",
    "PARKING": "P",
    "FOOD": "F",
    "MEDICAL": "M",
    "LODGING": "B",
    "LANDMARK": "L",
}
LOCATION_POLL_MS = 4000
GPS_SCAN_TIMEOUT_S = 2.0
GPS_DEFAULT_BAUDS = (9600, 4800)
NETWORK_GPS_TIMEOUT_S = 2.0
SYSTEM_LOCATION_TIMEOUT_MS = 3500
OFF_ROUTE_METERS = 80
REROUTE_COOLDOWN_S = 15


@dataclass
class Road:
    points: list
    name: str = ""
    highway: str = ""


@dataclass
class POI:
    lat: float
    lon: float
    category: str
    name: str = ""
    kind: str = ""


@dataclass
class OfflineMapRegion:
    path: Path
    name: str
    coverage: list

    def contains(self, lat, lon):
        return any(
            min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
            for min_lat, max_lat, min_lon, max_lon in self.coverage
        )


@dataclass
class MapCacheEntry:
    center: tuple
    radius_m: float
    detail: int
    roads: list
    pois: list
    source: str = "ONLINE"
    last_used: float = field(default_factory=time.time)


@dataclass
class Maneuver:
    instruction: str
    road: str
    distance_m: float
    location: tuple
    modifier: str = ""


@dataclass
class NavState:
    lat: float = FALLBACK_LAT
    lon: float = FALLBACK_LON
    accuracy_m: float = 0.0
    source: str = "FALLBACK"
    heading_deg: float = 0.0
    speed_mps: float = 0.0
    roads: list = field(default_factory=list)
    pois: list = field(default_factory=list)
    destination: tuple | None = None
    destination_name: str = ""
    route: list = field(default_factory=list)
    maneuvers: list = field(default_factory=list)
    route_distance_m: float = 0.0
    route_duration_s: float = 0.0
    loading: str = ""
    error: str = ""
    last_position: tuple | None = None
    last_position_time: float = 0.0
    last_map_center: tuple | None = None
    last_reroute: float = 0.0


class Bridge(QObject):
    location = Signal(float, float, float, str)
    system_location_request = Signal(str)
    map_preview = Signal(object, object, float, float, float, int, str)
    roads = Signal(object, object, float, float, float, int, str)
    map_error = Signal(str)
    destination = Signal(float, float, str)
    route = Signal(object, object, float, float)
    error = Signal(str)


def request_json(url, params=None, data=None, timeout=30):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def nmea_coord(value, hemisphere):
    if not value:
        raise ValueError("empty NMEA coordinate")
    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    result = degrees + minutes / 60.0
    if hemisphere in ("S", "W"):
        result = -result
    return result


def parse_nmea_sentence(line):
    if not line.startswith("$"):
        return None

    body = line.split("*", 1)[0]
    fields = body.split(",")
    sentence = fields[0][-3:]

    if sentence == "RMC" and len(fields) >= 9:
        if fields[2] != "A":
            return None
        return {
            "lat": nmea_coord(fields[3], fields[4]),
            "lon": nmea_coord(fields[5], fields[6]),
            "speed_mps": float(fields[7] or 0.0) * 0.514444,
            "heading": float(fields[8] or 0.0),
            "accuracy_m": 0.0,
        }

    if sentence == "GGA" and len(fields) >= 9:
        if int(fields[6] or 0) <= 0:
            return None
        hdop = float(fields[8] or 0.0)
        return {
            "lat": nmea_coord(fields[2], fields[3]),
            "lon": nmea_coord(fields[4], fields[5]),
            "speed_mps": 0.0,
            "heading": 0.0,
            "accuracy_m": hdop * 5.0 if hdop > 0 else 0.0,
        }

    return None


def parse_network_gps_endpoint(value):
    value = (value or "").strip()
    if not value:
        return None

    if "://" not in value:
        value = "tcp://" + value

    parsed = urllib.parse.urlparse(value)
    protocol = parsed.scheme.lower()
    if protocol not in ("tcp", "udp"):
        raise ValueError("network GPS protocol must be tcp or udp")
    if not parsed.hostname or not parsed.port:
        raise ValueError("network GPS must be host:port")

    return protocol, parsed.hostname, int(parsed.port)


def merge_nmea_fix(best, fix):
    if best is None:
        return dict(fix)

    best["lat"] = fix["lat"]
    best["lon"] = fix["lon"]
    if fix["accuracy_m"] > 0:
        best["accuracy_m"] = fix["accuracy_m"]
    if fix["speed_mps"] > 0:
        best["speed_mps"] = fix["speed_mps"]
    if fix["heading"] > 0:
        best["heading"] = fix["heading"]
    return best


def get_network_nmea_location(endpoint, timeout_s=NETWORK_GPS_TIMEOUT_S):
    parsed = parse_network_gps_endpoint(endpoint)
    if parsed is None:
        raise RuntimeError("network GPS not configured")

    protocol, host, port = parsed
    deadline = time.monotonic() + timeout_s
    best = None
    buffer = ""

    if protocol == "tcp":
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.settimeout(0.35)
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break

                buffer += chunk.decode("ascii", errors="ignore")
                lines = buffer.replace("\r", "\n").split("\n")
                buffer = lines.pop()

                for line in lines:
                    fix = parse_nmea_sentence(line.strip())
                    if fix:
                        best = merge_nmea_fix(best, fix)
                        if best is not None:
                            return (
                                best["lat"],
                                best["lon"],
                                best["accuracy_m"],
                                best["heading"],
                                best["speed_mps"],
                                f"{protocol}://{host}:{port}",
                            )

    else:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.35)

            # Most NMEA-over-UDP senders transmit to the configured port.
            # Bind locally when possible; if the endpoint is a remote unicast
            # target, connect() still gives us a clean receive path.
            try:
                sock.bind(("", port))
            except OSError:
                sock.connect((host, port))

            while time.monotonic() < deadline:
                try:
                    chunk, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue

                buffer += chunk.decode("ascii", errors="ignore")
                lines = buffer.replace("\r", "\n").split("\n")
                buffer = lines.pop()

                for line in lines:
                    fix = parse_nmea_sentence(line.strip())
                    if fix:
                        best = merge_nmea_fix(best, fix)
                        if best is not None:
                            return (
                                best["lat"],
                                best["lon"],
                                best["accuracy_m"],
                                best["heading"],
                                best["speed_mps"],
                                f"{protocol}://{host}:{port}",
                            )

    raise RuntimeError(
        f"network GPS {protocol}://{host}:{port} produced no valid NMEA fix"
    )


def gps_candidate_ports(preferred_port=None):
    import serial.tools.list_ports

    configured = os.environ.get("NAVCOM_GPS_PORT", "").strip()
    ports = list(serial.tools.list_ports.comports())
    ordered = []

    for name in (configured, preferred_port):
        if name and name not in ordered:
            ordered.append(name)

    def gps_score(port):
        text = " ".join(
            str(value or "")
            for value in (
                port.device,
                port.description,
                port.manufacturer,
                port.product,
            )
        ).lower()
        return 0 if any(
            token in text
            for token in ("gps", "gnss", "u-blox", "ublox", "garmin")
        ) else 1

    for port in sorted(ports, key=gps_score):
        if port.device not in ordered:
            ordered.append(port.device)

    return ordered


def get_nmea_location(preferred_port=None, timeout_s=GPS_SCAN_TIMEOUT_S):
    import serial

    ports = gps_candidate_ports(preferred_port)
    if not ports:
        raise RuntimeError("no serial GPS ports found")

    configured_baud = os.environ.get("NAVCOM_GPS_BAUD", "").strip()
    bauds = []
    if configured_baud:
        try:
            bauds.append(int(configured_baud))
        except ValueError:
            pass
    for baud in GPS_DEFAULT_BAUDS:
        if baud not in bauds:
            bauds.append(baud)

    deadline = time.monotonic() + timeout_s
    last_error = None

    for port in ports:
        for baud in bauds:
            if time.monotonic() >= deadline:
                break
            try:
                with serial.Serial(port, baudrate=baud, timeout=0.25) as gps:
                    best = None
                    port_deadline = min(deadline, time.monotonic() + 0.9)

                    while time.monotonic() < port_deadline:
                        raw = gps.readline()
                        if not raw:
                            continue
                        fix = parse_nmea_sentence(
                            raw.decode("ascii", errors="ignore").strip()
                        )
                        if not fix:
                            continue

                        best = merge_nmea_fix(best, fix)

                        # One valid NMEA fix is enough to establish GPS as the
                        # preferred source. Additional motion/accuracy values
                        # will arrive during later polls.
                        if best is not None:
                            return (
                                best["lat"],
                                best["lon"],
                                best["accuracy_m"],
                                best["heading"],
                                best["speed_mps"],
                                port,
                            )
            except Exception as exc:
                last_error = exc

        if time.monotonic() >= deadline:
            break

    if last_error:
        raise RuntimeError(f"GPS unavailable: {last_error}")
    raise RuntimeError("GPS found but no valid NMEA fix")


def get_windows_location():
    from winsdk.windows.devices.geolocation import Geolocator, GeolocationAccessStatus

    async def locate():
        access = await Geolocator.request_access_async()
        if access != GeolocationAccessStatus.ALLOWED:
            raise RuntimeError("Windows location permission not granted")
        locator = Geolocator()
        locator.desired_accuracy_in_meters = 10
        pos = await locator.get_geoposition_async()
        c = pos.coordinate
        heading = float(c.heading or 0.0)
        speed = float(c.speed or 0.0)
        accuracy = float(c.accuracy or 0.0)

        # WinRT commonly reports NaN for heading/speed when the location
        # provider cannot determine them.  NaN is truthy in Python, and
        # passing it to QPainter.rotate() invalidates the map transform.
        if not math.isfinite(heading):
            heading = 0.0
        if not math.isfinite(speed):
            speed = 0.0
        if not math.isfinite(accuracy):
            accuracy = 0.0

        return c.latitude, c.longitude, accuracy, heading, speed

    return asyncio.run(locate())


def map_detail_for_radius(radius_m):
    """Return 0=local/all drivable roads, 1=regional, 2=major roads only."""
    if radius_m <= 7000:
        return 0
    if radius_m <= 15000:
        return 1
    return 2


def poi_patterns_for_detail(detail):
    if detail == 0:
        return (
            "fuel|parking|restaurant|cafe|fast_food|hospital|clinic|pharmacy",
            "attraction|museum|viewpoint|hotel|motel"
        )
    if detail == 1:
        return (
            "fuel|parking|restaurant|fast_food|hospital|clinic|pharmacy",
            "attraction|museum|viewpoint|hotel|motel"
        )
    return (
        "fuel|hospital|clinic|pharmacy",
        "attraction|museum|viewpoint"
    )


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


def discover_offline_maps():
    regions = []
    if not OFFLINE_MAP_DIR.exists():
        return regions

    for path in sorted(OFFLINE_MAP_DIR.glob("*.navmap")):
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                meta = dict(conn.execute("SELECT key, value FROM metadata"))
                coverage = [
                    (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
                    for row in conn.execute(
                        "SELECT min_lat, max_lat, min_lon, max_lon FROM coverage"
                    )
                ]
            if coverage:
                regions.append(OfflineMapRegion(
                    path=path,
                    name=meta.get("name", path.stem),
                    coverage=coverage
                ))
        except Exception:
            # A bad or half-copied file should never prevent NAV-COM starting.
            continue
    return regions


def find_offline_map(regions, lat, lon):
    for region in regions:
        if region.contains(lat, lon):
            return region
    return None


def map_bbox(lat, lon, radius_m):
    dlat = radius_m / 111320.0
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    dlon = radius_m / (111320.0 * cos_lat)
    return lat-dlat, lat+dlat, lon-dlon, lon+dlon


def query_offline_map(region, lat, lon, radius_m, detail):
    min_lat, max_lat, min_lon, max_lon = map_bbox(lat, lon, radius_m)
    uri = region.path.resolve().as_uri() + "?mode=ro"

    if detail == 0:
        road_types = None
        poi_categories = None
    elif detail == 1:
        road_types = (
            "motorway", "motorway_link", "trunk", "trunk_link",
            "primary", "primary_link", "secondary", "secondary_link",
            "tertiary", "tertiary_link", "unclassified"
        )
        poi_categories = None
    else:
        road_types = (
            "motorway", "motorway_link", "trunk", "trunk_link",
            "primary", "primary_link", "secondary", "secondary_link"
        )
        poi_categories = ("FUEL", "MEDICAL", "LANDMARK")

    with sqlite3.connect(uri, uri=True) as conn:
        road_sql = """
            SELECT r.name, r.highway, r.geometry
            FROM road_index i
            JOIN roads r ON r.id = i.id
            WHERE i.max_lat >= ? AND i.min_lat <= ?
              AND i.max_lon >= ? AND i.min_lon <= ?
        """
        road_params = [min_lat, max_lat, min_lon, max_lon]
        if road_types:
            road_sql += " AND r.highway IN (" + ",".join("?" * len(road_types)) + ")"
            road_params.extend(road_types)

        roads = []
        for name, highway, geometry in conn.execute(road_sql, road_params):
            points = [tuple(point) for point in json.loads(geometry)]
            if len(points) > 1:
                roads.append(Road(points, name or "", highway or ""))

        poi_sql = """
            SELECT p.lat, p.lon, p.category, p.name, p.kind
            FROM poi_index i
            JOIN pois p ON p.id = i.id
            WHERE i.max_lat >= ? AND i.min_lat <= ?
              AND i.max_lon >= ? AND i.min_lon <= ?
        """
        poi_params = [min_lat, max_lat, min_lon, max_lon]
        if poi_categories:
            poi_sql += " AND p.category IN (" + ",".join("?" * len(poi_categories)) + ")"
            poi_params.extend(poi_categories)

        pois = [
            POI(
                lat=float(plat),
                lon=float(plon),
                category=category,
                name=name or "",
                kind=kind or ""
            )
            for plat, plon, category, name, kind in conn.execute(poi_sql, poi_params)
        ]

    return roads, pois


def fetch_map_data(lat, lon, radius_m=MAP_RADIUS_M, detail=0):
    # Roads and selected POIs share one Overpass request so the optional POI
    # layer does not double map-service traffic.
    if detail == 0:
        road_selector = (
            '["highway"]'
            '["highway"!~"footway|path|steps|cycleway|bridleway|corridor|'
            'construction|proposed"]'
        )
    elif detail == 1:
        road_selector = (
            '["highway"~"motorway|motorway_link|trunk|trunk_link|primary|'
            'primary_link|secondary|secondary_link|tertiary|tertiary_link|'
            'unclassified"]'
        )
    else:
        road_selector = (
            '["highway"~"motorway|motorway_link|trunk|trunk_link|primary|'
            'primary_link|secondary|secondary_link"]'
        )

    amenity_pattern, tourism_pattern = poi_patterns_for_detail(detail)
    radius = int(radius_m)
    query = f"""
    [out:json][timeout:25];
    way(around:{radius},{lat},{lon}){road_selector}->.roads;
    (
      nwr(around:{radius},{lat},{lon})["amenity"~"{amenity_pattern}"];
      nwr(around:{radius},{lat},{lon})["tourism"~"{tourism_pattern}"];
      nwr(around:{radius},{lat},{lon})["historic"];
    )->.pois;
    .roads out tags geom;
    .pois out tags center;
    """

    data = urllib.parse.urlencode({"data": query}).encode()
    obj = request_json(OVERPASS_URL, data=data, timeout=35)

    roads = []
    pois = []
    seen_pois = set()

    for e in obj.get("elements", []):
        tags = e.get("tags", {})
        geom = e.get("geometry", [])

        if tags.get("highway") and len(geom) > 1:
            roads.append(Road(
                [(p["lat"], p["lon"]) for p in geom],
                tags.get("name", ""),
                tags.get("highway", "")
            ))
            continue

        category, kind = classify_poi(tags)
        if not category:
            continue

        if "lat" in e and "lon" in e:
            plat, plon = float(e["lat"]), float(e["lon"])
        else:
            center = e.get("center") or {}
            if "lat" not in center or "lon" not in center:
                continue
            plat, plon = float(center["lat"]), float(center["lon"])

        # De-duplicate features that can appear through more than one selector.
        key = (e.get("type"), e.get("id"), category)
        if key in seen_pois:
            continue
        seen_pois.add(key)

        pois.append(POI(
            lat=plat,
            lon=plon,
            category=category,
            name=tags.get("name", ""),
            kind=kind
        ))

    return roads, pois


def geocode(query):
    # User-triggered only; no autocomplete. One result keeps public-service use tiny.
    obj = request_json(NOMINATIM_URL, {
        "q": query, "format": "jsonv2", "limit": 1,
        "addressdetails": 0
    }, timeout=20)
    if not obj:
        raise RuntimeError("Destination not found")
    return float(obj[0]["lat"]), float(obj[0]["lon"]), obj[0]["display_name"]


def route_osrm(start_lat, start_lon, dest_lat, dest_lon):
    coords = f"{start_lon},{start_lat};{dest_lon},{dest_lat}"
    url = f"{OSRM_URL}/route/v1/driving/{coords}"
    obj = request_json(url, {
        "steps": "true", "geometries": "geojson",
        "overview": "full", "alternatives": "false"
    }, timeout=30)
    if obj.get("code") != "Ok" or not obj.get("routes"):
        raise RuntimeError("No driving route found")

    route = obj["routes"][0]
    # GeoJSON uses lon,lat.
    points = [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]
    maneuvers = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            m = step.get("maneuver", {})
            loc = m.get("location", [0, 0])
            typ = m.get("type", "continue").replace("_", " ")
            modifier = m.get("modifier", "")
            name = step.get("name") or "UNNAMED ROAD"
            if typ == "depart":
                instruction = "DEPART"
            elif typ == "arrive":
                instruction = "ARRIVE"
            elif typ == "roundabout":
                instruction = "ROUNDABOUT"
            else:
                instruction = (modifier + " " + typ).strip().upper()
            maneuvers.append(Maneuver(
                instruction=instruction,
                road=name,
                distance_m=float(step.get("distance", 0)),
                location=(float(loc[1]), float(loc[0])),
                modifier=modifier
            ))
    return points, maneuvers, float(route["distance"]), float(route["duration"])


def haversine_m(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(h))


def bearing_deg(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dl = lon2-lon1
    x = math.sin(dl)*math.cos(lat2)
    y = math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def point_segment_distance_m(p, a, b):
    # Local planar approximation for off-route checks.
    lat0 = p[0]
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 111320
    px, py = 0.0, 0.0
    ax, ay = (a[1]-p[1])*kx, (a[0]-p[0])*ky
    bx, by = (b[1]-p[1])*kx, (b[0]-p[0])*ky
    dx, dy = bx-ax, by-ay
    if dx == dy == 0:
        return math.hypot(ax, ay)
    t = max(0, min(1, (-(ax*dx + ay*dy)) / (dx*dx + dy*dy)))
    return math.hypot(ax+t*dx, ay+t*dy)


def distance_to_route_m(p, route):
    if len(route) < 2:
        return 0.0
    return min(point_segment_distance_m(p, a, b) for a, b in zip(route, route[1:]))


def local_xy(lat, lon, origin_lat, origin_lon):
    x = (lon-origin_lon) * 111320.0 * math.cos(math.radians(origin_lat))
    y = -(lat-origin_lat) * 111320.0
    return x, y


def local_latlon(x_m, y_m, origin_lat, origin_lon):
    lat = origin_lat - (y_m / 111320.0)
    cos_lat = max(0.01, math.cos(math.radians(origin_lat)))
    lon = origin_lon + (x_m / (111320.0 * cos_lat))
    return lat, lon


class NavDisplay(QWidget):
    destination_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.s = NavState()
        self.bridge = Bridge()
        self.bridge.location.connect(self.on_location)
        self.bridge.system_location_request.connect(self.request_system_location)
        self.bridge.map_preview.connect(self.on_map_preview)
        self.bridge.roads.connect(self.on_roads)
        self.bridge.map_error.connect(self.on_map_error)
        self.bridge.destination.connect(self.on_destination)
        self.bridge.route.connect(self.on_route)
        self.bridge.error.connect(self.on_error)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        # Interactive map state.
        self.zoom = 1.0
        self.auto_zoom = True
        self.follow_vehicle = True
        self.pan_px = QPointF(0, 0)
        self.dragging = False
        self.drag_last = QPointF()
        self.map_rect = QRectF()
        self.poi_legend_rect = QRectF()
        self.poi_legend_collapsed = False
        self.poi_enabled = {
            "FUEL": True,
            "PARKING": True,
            "FOOD": True,
            "MEDICAL": True,
            "LODGING": True,
            "LANDMARK": True,
        }

        # Viewport-aware map cache. A cached circle can satisfy future pans or
        # zooms without another network request when it fully covers the view.
        self.road_cache = []
        self.map_request_inflight = False
        self.map_refresh_pending = False
        self.loaded_map_radius_m = 0.0
        self.loaded_map_detail = 0
        self.loaded_map_center = None
        self.loaded_map_source = "ONLINE"
        self.offline_maps = discover_offline_maps()
        self._gps_port = os.environ.get("NAVCOM_GPS_PORT", "").strip() or None
        self._network_gps = os.environ.get(
            "NAVCOM_GPS_NETWORK", ""
        ).strip() or None
        self._gps_error = ""
        self._system_location_pending = False

        self.system_position_source = QGeoPositionInfoSource.createDefaultSource(self)
        if self.system_position_source is not None:
            try:
                self.system_position_source.setPreferredPositioningMethods(
                    QGeoPositionInfoSource.PositioningMethod.NonSatellitePositioningMethods
                )
            except Exception:
                pass
            self.system_position_source.positionUpdated.connect(
                self.on_system_position
            )
            self.system_position_source.errorOccurred.connect(
                self.on_system_location_error
            )

        self.map_refresh_timer = QTimer(self)
        self.map_refresh_timer.setSingleShot(True)
        self.map_refresh_timer.timeout.connect(self.ensure_map_coverage)

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.acquire_location)
        self.poll.start(LOCATION_POLL_MS)

        self.instrument_timer = QTimer(self)
        self.instrument_timer.timeout.connect(self.update)
        self.instrument_timer.start(250)

        QTimer.singleShot(500, self.acquire_location)

    def set_network_gps(self, enabled, host="", port=8080, protocol="tcp"):
        if enabled:
            host = str(host).strip()
            protocol = str(protocol or "tcp").strip().lower()
            if not host:
                raise ValueError("Network GPS host is required")
            if protocol not in ("tcp", "udp"):
                raise ValueError("Network GPS protocol must be TCP or UDP")
            port = int(port)
            if not 1 <= port <= 65535:
                raise ValueError("Network GPS port must be 1-65535")
            self._network_gps = f"{protocol}://{host}:{port}"
        else:
            self._network_gps = None

        # Re-evaluate immediately so a newly entered endpoint can take over
        # without requiring an application restart.
        self.acquire_location()

    def acquire_location(self):
        threading.Thread(target=self._location_worker, daemon=True).start()

    def _location_worker(self):
        errors = []

        if self._network_gps:
            try:
                lat, lon, acc, heading, speed, endpoint = (
                    get_network_nmea_location(self._network_gps)
                )
                self._pending_heading = heading
                self._pending_speed = speed
                self.bridge.location.emit(lat, lon, acc, "NETGPS")
                return
            except Exception as exc:
                errors.append("NETWORK GPS: " + str(exc))

        try:
            lat, lon, acc, heading, speed, port = get_nmea_location(
                self._gps_port
            )
            self._gps_port = port
            self._pending_heading = heading
            self._pending_speed = speed
            self.bridge.location.emit(lat, lon, acc, "GPS")
            return
        except Exception as exc:
            errors.append("SERIAL GPS: " + str(exc))

        self.bridge.system_location_request.emit("; ".join(errors))

    def request_system_location(self, gps_error=""):
        self._gps_error = gps_error

        if self.system_position_source is not None:
            if not self._system_location_pending:
                self._system_location_pending = True
                timeout_ms = max(
                    SYSTEM_LOCATION_TIMEOUT_MS,
                    int(self.system_position_source.minimumUpdateInterval())
                )
                self.system_position_source.requestUpdate(timeout_ms)
            return

        if sys.platform.startswith("win"):
            threading.Thread(
                target=self._windows_location_worker,
                daemon=True
            ).start()
            return

        self._location_fallback(
            f"GPS: {gps_error}; no system location source"
        )

    def _windows_location_worker(self):
        try:
            lat, lon, acc, heading, speed = get_windows_location()
            self._pending_heading = heading
            self._pending_speed = speed
            self.bridge.location.emit(lat, lon, acc, "WINDOWS")
        except Exception as exc:
            self._location_fallback(
                f"GPS: {self._gps_error}; WINDOWS: {exc}"
            )

    def on_system_position(self, info):
        self._system_location_pending = False
        if not info or not info.isValid():
            self._location_fallback(
                f"GPS: {self._gps_error}; invalid system location"
            )
            return

        coord = info.coordinate()
        if not coord.isValid():
            self._location_fallback(
                f"GPS: {self._gps_error}; invalid system coordinate"
            )
            return

        attr = QGeoPositionInfo.Attribute
        accuracy = (
            float(info.attribute(attr.HorizontalAccuracy))
            if info.hasAttribute(attr.HorizontalAccuracy) else 0.0
        )
        heading = (
            float(info.attribute(attr.Direction))
            if info.hasAttribute(attr.Direction) else 0.0
        )
        speed = (
            float(info.attribute(attr.GroundSpeed))
            if info.hasAttribute(attr.GroundSpeed) else 0.0
        )

        for value_name, value in (
            ("accuracy", accuracy),
            ("heading", heading),
            ("speed", speed),
        ):
            if not math.isfinite(value):
                if value_name == "accuracy":
                    accuracy = 0.0
                elif value_name == "heading":
                    heading = 0.0
                else:
                    speed = 0.0

        if sys.platform.startswith("win"):
            source = "WINDOWS"
        elif sys.platform == "darwin":
            source = "MACOS"
        elif sys.platform.startswith("linux"):
            source = "LINUX"
        else:
            source = "SYSTEM"

        self._pending_heading = heading
        self._pending_speed = speed
        self.bridge.location.emit(
            float(coord.latitude()),
            float(coord.longitude()),
            accuracy,
            source
        )

    def on_system_location_error(self, _error):
        if not self._system_location_pending:
            return
        self._system_location_pending = False

        # Windows gets one extra direct WinRT attempt because that was the
        # original NAV-COM fallback and can work even when a Qt positioning
        # plugin is unavailable or misconfigured.
        if sys.platform.startswith("win"):
            threading.Thread(
                target=self._windows_location_worker,
                daemon=True
            ).start()
        else:
            self._location_fallback(
                f"GPS: {self._gps_error}; system location unavailable"
            )

    def _location_fallback(self, message):
        if self.s.last_position is None:
            self.bridge.location.emit(
                FALLBACK_LAT, FALLBACK_LON, 0, "FALLBACK"
            )
        self.bridge.error.emit("LOCATION: " + message)

    def on_location(self, lat, lon, accuracy, source):
        now = time.time()
        old = self.s.last_position
        new = (lat, lon)
        self.s.lat, self.s.lon = lat, lon
        self.s.accuracy_m, self.s.source = accuracy, source
        if self.s.error.startswith("LOCATION:"):
            self.s.error = ""

        api_heading = getattr(self, "_pending_heading", 0.0)
        api_speed = getattr(self, "_pending_speed", 0.0)

        if math.isfinite(api_heading) and 0.0 < api_heading <= 360.0:
            self.s.heading_deg = api_heading
        elif old and haversine_m(old, new) > 5:
            derived = bearing_deg(old, new)
            if math.isfinite(derived):
                self.s.heading_deg = derived
        elif not math.isfinite(self.s.heading_deg):
            self.s.heading_deg = 0.0

        if math.isfinite(api_speed) and api_speed > 0.0:
            self.s.speed_mps = api_speed
        elif old and self.s.last_position_time:
            dt = max(0.1, now-self.s.last_position_time)
            derived_speed = haversine_m(old, new)/dt
            self.s.speed_mps = derived_speed if math.isfinite(derived_speed) else 0.0
        elif not math.isfinite(self.s.speed_mps):
            self.s.speed_mps = 0.0

        self.s.last_position = new
        self.s.last_position_time = now

        if self.auto_zoom and self.follow_vehicle:
            mph = self.s.speed_mps * 2.23694
            turns = self.next_maneuvers()
            turn_distance = haversine_m(new, turns[0].location) if turns else 99999
            if turn_distance < 180:
                target_zoom = 2.8
            elif turn_distance < 500:
                target_zoom = 2.0
            elif mph > 55:
                target_zoom = 0.65
            elif mph > 35:
                target_zoom = 0.85
            else:
                target_zoom = 1.15
            self.zoom += (target_zoom - self.zoom) * 0.25

        # Map coverage follows the visible viewport, not just the vehicle.
        self.ensure_map_coverage()

        if self.s.destination and self.s.route:
            off = distance_to_route_m(new, self.s.route)
            if off > OFF_ROUTE_METERS and now-self.s.last_reroute > REROUTE_COOLDOWN_S:
                self.s.last_reroute = now
                self.calculate_route()

        self.update()

    def map_view_request(self):
        """Return viewport center, required prefetch radius, and detail tier."""
        if self.map_rect.width() < 100 or self.map_rect.height() < 100:
            center = (self.s.lat, self.s.lon)
            radius = MAP_RADIUS_M
            return center, radius, map_detail_for_radius(radius)

        ppm = (min(self.map_rect.width(), self.map_rect.height()) / 7000.0) * max(0.01, self.zoom)
        safe_heading = self.s.heading_deg if math.isfinite(self.s.heading_deg) else 0.0

        pan = self.pan_px if not self.follow_vehicle else QPointF(0, 0)
        sx = -pan.x()
        sy = -pan.y()
        a = math.radians(safe_heading)
        world_px_x = math.cos(a) * sx - math.sin(a) * sy
        world_px_y = math.sin(a) * sx + math.cos(a) * sy

        center = local_latlon(
            world_px_x / ppm,
            world_px_y / ppm,
            self.s.lat,
            self.s.lon
        )

        half_diagonal_px = 0.5 * math.hypot(
            self.map_rect.width(), self.map_rect.height()
        )
        # Radius needed for the actual visible viewport. The network fetch
        # adds a larger prefetch margin so small pans do not cause new calls.
        radius = half_diagonal_px / ppm
        radius = max(MIN_MAP_RADIUS_M, min(MAX_MAP_RADIUS_M, radius))
        return center, radius, map_detail_for_radius(radius)

    def _find_cached_map(self, center, radius, detail):
        candidates = []
        now = time.time()
        for entry in self.road_cache:
            # A more detailed cache can satisfy a less-detailed request, but
            # not the reverse.
            if entry.detail > detail:
                continue

            # Online data is preferred. Offline fallback entries remain useful
            # during an outage, but are retried periodically so NAV-COM can
            # return to fresh online data when connectivity comes back.
            if entry.source.startswith("OFFLINE"):
                if now - entry.last_used > OFFLINE_RETRY_S:
                    continue

            if haversine_m(entry.center, center) + radius <= entry.radius_m:
                candidates.append(entry)

        if not candidates:
            return None

        online = [entry for entry in candidates if entry.source.startswith("ONLINE")]
        if online:
            candidates = online

        return min(candidates, key=lambda e: e.radius_m)

    def _activate_map_entry(self, entry):
        entry.last_used = time.time()
        self.s.roads = entry.roads
        self.s.pois = entry.pois
        self.s.last_map_center = entry.center
        self.loaded_map_center = entry.center
        self.loaded_map_radius_m = entry.radius_m
        self.loaded_map_detail = entry.detail
        self.loaded_map_source = entry.source
        self.s.loading = ""
        self.update()

    def schedule_map_refresh(self, delay_ms=MAP_FETCH_DEBOUNCE_MS):
        if hasattr(self, "map_refresh_timer"):
            self.map_refresh_timer.start(max(0, int(delay_ms)))

    def ensure_map_coverage(self):
        center, radius, detail = self.map_view_request()
        cached = self._find_cached_map(center, radius, detail)
        if cached is not None:
            self._activate_map_entry(cached)
            return

        if self.map_request_inflight:
            self.map_refresh_pending = True
            return

        self.map_request_inflight = True
        self.map_refresh_pending = False
        self.s.loading = "LOADING MAP"

        # Fetch beyond the visible edge. The extra coverage is what lets the
        # user pan a meaningful distance before another request is needed.
        fetch_radius = min(MAX_MAP_RADIUS_M, radius * MAP_PREFETCH_MARGIN)
        threading.Thread(
            target=self._road_worker,
            args=(center[0], center[1], fetch_radius, detail),
            daemon=True
        ).start()
        self.update()

    def _road_worker(self, lat, lon, radius, detail):
        region = find_offline_map(self.offline_maps, lat, lon)
        offline_data = None

        # If local coverage exists, use it as a fast startup/viewport preview
        # while the preferred online request runs in the same worker.
        if region is not None:
            try:
                offline_roads, offline_pois = query_offline_map(
                    region, lat, lon, radius, detail
                )
                if offline_roads:
                    offline_data = (offline_roads, offline_pois)
                    self.bridge.map_preview.emit(
                        offline_roads,
                        offline_pois,
                        lat,
                        lon,
                        radius,
                        detail,
                        "OFFLINE PREVIEW " + region.name.upper()
                    )
            except Exception:
                offline_data = None

        try:
            roads, pois = fetch_map_data(lat, lon, radius, detail)
            self.bridge.roads.emit(
                roads, pois, lat, lon, radius, detail, "ONLINE"
            )
        except Exception as exc:
            if offline_data is not None:
                roads, pois = offline_data
                self.bridge.roads.emit(
                    roads,
                    pois,
                    lat,
                    lon,
                    radius,
                    detail,
                    "OFFLINE FALLBACK " + region.name.upper()
                )
            else:
                self.bridge.map_error.emit("MAP: " + str(exc))

    def on_map_preview(self, roads, pois, lat, lon, radius, detail, source):
        # Preview data is intentionally not added to the long-lived cache.
        # It keeps the screen useful while the preferred online request is
        # still in flight, then gets replaced by the online result.
        self.s.roads = roads
        self.s.pois = pois
        self.s.last_map_center = (lat, lon)
        self.loaded_map_center = (lat, lon)
        self.loaded_map_radius_m = radius
        self.loaded_map_detail = detail
        self.loaded_map_source = source
        self.s.loading = "LOADING ONLINE"
        self.update()

    def on_roads(self, roads, pois, lat, lon, radius, detail, source):
        self.map_request_inflight = False
        entry = MapCacheEntry(
            center=(lat, lon),
            radius_m=radius,
            detail=detail,
            roads=roads,
            pois=pois,
            source=source
        )
        self.road_cache.append(entry)
        self.road_cache.sort(key=lambda e: e.last_used, reverse=True)
        del self.road_cache[MAP_CACHE_LIMIT:]

        # Re-evaluate the current viewport. If the user moved while the query
        # was running, the result is retained in cache and a second request is
        # made only if the new viewport is not covered.
        self.s.loading = ""
        pending = self.map_refresh_pending
        self.map_refresh_pending = False
        self.ensure_map_coverage()
        if pending:
            self.schedule_map_refresh(50)

    def on_map_error(self, message):
        self.map_request_inflight = False
        self.map_refresh_pending = False
        self.s.error = message
        self.s.loading = ""
        self.update()

    def search_destination(self, text):
        text = text.strip()
        if not text:
            return
        self.s.loading = "SEARCHING"
        threading.Thread(target=self._search_worker, args=(text,), daemon=True).start()

    def _search_worker(self, text):
        try:
            self.bridge.destination.emit(*geocode(text))
        except Exception as exc:
            self.bridge.error.emit("SEARCH: " + str(exc))

    def on_destination(self, lat, lon, name):
        self.s.destination = (lat, lon)
        self.s.destination_name = name
        self.calculate_route()

    def calculate_route(self):
        if not self.s.destination:
            return
        self.s.loading = "CALCULATING ROUTE"
        threading.Thread(target=self._route_worker, daemon=True).start()

    def _route_worker(self):
        try:
            dlat, dlon = self.s.destination
            self.bridge.route.emit(*route_osrm(self.s.lat, self.s.lon, dlat, dlon))
        except Exception as exc:
            self.bridge.error.emit("ROUTE: " + str(exc))

    def on_route(self, route, maneuvers, distance, duration):
        self.s.route = route
        self.s.maneuvers = maneuvers
        self.s.route_distance_m = distance
        self.s.route_duration_s = duration
        self.s.loading = ""
        self.s.error = ""
        self.update()

    def on_error(self, message):
        self.s.error = message
        self.s.loading = ""
        self.update()

    def mono(self, size, bold=False):
        f = QFont("DejaVu Sans Mono", size)
        f.setStyleHint(QFont.Monospace)
        f.setBold(bold)
        return f

    def road_pen(self, h):
        if h in ("motorway", "trunk"): return QPen(GREEN, 3)
        if h in ("primary", "secondary"): return QPen(DIM, 2)
        return QPen(FAINT, 1)

    def poi_is_enabled(self, category):
        return self.poi_enabled.get(category, False)

    def toggle_poi_category(self, category):
        if category in self.poi_enabled:
            self.poi_enabled[category] = not self.poi_enabled[category]
            self.update()

    def draw_poi_legend(self, p, r):
        """Small clickable Pip-Boy-style POI legend."""
        items = [
            ("G", "FUEL", "FUEL"),
            ("P", "PARK", "PARKING"),
            ("F", "FOOD", "FOOD"),
            ("M", "MED", "MEDICAL"),
            ("B", "BED", "LODGING"),
            ("L", "LAND", "LANDMARK"),
        ]

        enabled_count = sum(1 for value in self.poi_enabled.values() if value)
        x_margin = 8
        y_margin = 8

        if self.poi_legend_collapsed:
            box_w = 92
            box_h = 20
            x = r.right() - box_w - x_margin
            y = r.top() + y_margin
            box = QRectF(x, y, box_w, box_h)
            self.poi_legend_rect = box

            p.save()
            p.fillRect(box, QColor(1, 7, 3, 225))
            p.setPen(QPen(DIM, 1))
            p.drawRect(box)
            p.setPen(GREEN)
            p.setFont(self.mono(7, True))
            p.drawText(int(x+6), int(y+13), f"POI {enabled_count}/6 [+]")
            p.restore()
            return

        box_w = 118
        row_h = 14
        header_h = 18
        box_h = header_h + row_h * len(items) + 4
        x = r.right() - box_w - x_margin
        y = r.top() + y_margin
        box = QRectF(x, y, box_w, box_h)
        self.poi_legend_rect = box

        p.save()
        p.fillRect(box, QColor(1, 7, 3, 225))
        p.setPen(QPen(DIM, 1))
        p.drawRect(box)

        p.setPen(GREEN)
        p.setFont(self.mono(7, True))
        p.drawText(int(x+6), int(y+12), f"POI {enabled_count}/6  [-]")

        yy = y + header_h + 9
        for glyph, label, category in items:
            enabled = self.poi_enabled.get(category, False)

            icon = QRectF(x+6, yy-8, 12, 12)
            p.setPen(QPen(BRIGHT if enabled else FAINT, 1))
            p.drawRect(icon)
            p.setFont(self.mono(6, True))
            p.drawText(icon, Qt.AlignCenter, glyph)

            p.setPen(GREEN if enabled else FAINT)
            p.setFont(self.mono(6, True))
            p.drawText(int(x+23), int(yy+1), label)

            state = "ON" if enabled else "OFF"
            p.setPen(BRIGHT if enabled else DIM)
            p.drawText(int(x+91), int(yy+1), state)
            yy += row_h

        p.restore()

    def draw_poi_markers(self, p, r, markers):
        if not markers or not any(self.poi_enabled.values()) or self.zoom < POI_MIN_ZOOM:
            return

        if self.zoom >= 1.50:
            limit = POI_MAX_DRAW
        elif self.zoom >= 1.00:
            limit = 28
        elif self.zoom >= 0.75:
            limit = 18
        else:
            limit = 10

        priority = {
            "FUEL": 0,
            "MEDICAL": 1,
            "LANDMARK": 2,
            "PARKING": 3,
            "FOOD": 4,
            "LODGING": 5,
        }
        center = r.center()
        markers = sorted(
            markers,
            key=lambda item: (
                priority.get(item[1].category, 9),
                abs(item[0].x() - center.x()) + abs(item[0].y() - center.y())
            )
        )

        p.save()
        p.setClipRect(r.adjusted(2, 2, -2, -34))
        occupied = set()
        drawn = 0
        labels = 0

        for pos, poi in markers:
            cell = (int(pos.x() // 34), int(pos.y() // 28))
            if cell in occupied:
                continue
            occupied.add(cell)

            box = QRectF(pos.x()-9, pos.y()-9, 18, 18)
            strong = poi.category in ("FUEL", "MEDICAL", "LANDMARK")
            p.fillRect(box, BLACK)
            p.setPen(QPen(BRIGHT if strong else GREEN, 1.5))
            p.drawRect(box)
            p.setFont(self.mono(9, True))
            p.drawText(box, Qt.AlignCenter, POI_ICONS.get(poi.category, "?"))

            if self.zoom >= 1.15 and poi.name and labels < 12:
                p.setFont(self.mono(7, True))
                p.setPen(DIM)
                p.drawText(
                    QPointF(pos.x()+13, pos.y()+4),
                    poi.name.upper()[:18]
                )
                labels += 1

            drawn += 1
            if drawn >= limit:
                break

        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), BLACK)
        margin, header, gap = 18, 58, 10
        rw = max(310, int(self.width()*.31))
        mr = QRectF(margin, margin+header,
                    self.width()-2*margin-rw-gap,
                    self.height()-2*margin-header)
        pr = QRectF(mr.right()+gap, mr.top(), rw, mr.height())
        self.map_rect = mr
        p.setPen(QPen(GREEN, 2)); p.drawRect(mr); p.drawRect(pr)
        self.draw_header(p, margin)
        self.draw_map(p, mr)
        self.draw_panel(p, pr)
        self.scanlines(p)

    def draw_header(self, p, m):
        safe_speed = self.s.speed_mps if math.isfinite(self.s.speed_mps) else 0.0
        safe_heading = self.s.heading_deg if math.isfinite(self.s.heading_deg) else 0.0
        safe_heading %= 360.0
        now = datetime.now()

        p.setPen(GREEN)
        p.setFont(self.mono(18, True))
        p.drawText(m+7, m+29, "NAV-COM 2006")

        status = {
            "NETGPS": "NET GPS",
            "GPS": "GPS LOCK",
            "WINDOWS": "WIN LOC",
            "MACOS": "MAC LOC",
            "LINUX": "LINUX LOC",
            "SYSTEM": "SYS LOC",
            "FALLBACK": "LOC FALLBACK",
        }.get(self.s.source, self.s.source[:12])
        p.setFont(self.mono(9, True))
        p.drawText(int(self.width()*.205), m+27, status)

        # Scrolling compass tape: fixed lubber line, moving degree scale.
        tape_w = min(470, int(self.width()*.30))
        tape_h = 38
        tape_x = int(self.width()*.34)
        tape_y = m+1
        tape = QRectF(tape_x, tape_y, tape_w, tape_h)
        p.setPen(QPen(DIM, 1))
        p.drawRect(tape)
        cx = tape.center().x()
        ppd = tape_w / 90.0
        anchor = round(safe_heading/5.0)*5

        for delta in range(-60, 65, 5):
            raw = anchor + delta
            deg = int(raw % 360)
            x = cx + (raw-safe_heading)*ppd
            if x < tape.left()+4 or x > tape.right()-4:
                continue
            major = deg % 15 == 0
            cardinal = deg % 90 == 0
            top = tape.bottom()-16 if major else tape.bottom()-9
            p.setPen(QPen(GREEN if major else DIM, 1))
            p.drawLine(int(x), int(top), int(x), int(tape.bottom()-3))
            if major:
                label = {0:"N",90:"E",180:"S",270:"W"}[deg] if cardinal else f"{deg:03d}"
                p.setFont(self.mono(10 if cardinal else 7, cardinal))
                tw=p.fontMetrics().horizontalAdvance(label)
                p.drawText(int(x-tw/2), int(tape.top()+12), label)

        p.setPen(QPen(BRIGHT,2))
        p.drawLine(int(cx),int(tape.top()),int(cx),int(tape.bottom()))
        p.setBrush(BRIGHT)
        p.drawPolygon(QPolygonF([QPointF(cx-6,tape.top()+1),
                                 QPointF(cx+6,tape.top()+1),
                                 QPointF(cx,tape.top()+8)]))
        p.setBrush(Qt.NoBrush)
        p.setFont(self.mono(8,True))
        hdg=f"{safe_heading:03.0f}°"
        tw=p.fontMetrics().horizontalAdvance(hdg)
        p.drawText(int(cx-tw/2),int(tape.bottom()+11),hdg)

        clock_x=self.width()-190
        speed_x=clock_x-125
        p.setPen(GREEN); p.setFont(self.mono(7,True))
        p.drawText(speed_x,m+9,"GROUND SPEED")
        p.setFont(self.mono(14,True))
        p.drawText(speed_x,m+29,f"{safe_speed*2.23694:03.0f} MPH")

        p.setPen(BRIGHT); p.setFont(self.mono(17,True))
        p.drawText(clock_x,m+21,now.strftime("%H:%M:%S"))
        p.setPen(DIM); p.setFont(self.mono(7,True))
        p.drawText(clock_x+2,m+36,now.strftime("%a %d %b %Y").upper())
        p.drawText(self.width()-54,m+36,f"RD{len(self.s.roads):04d}")

    def draw_map(self, p, r):
        p.save()
        p.setClipRect(r.adjusted(2,2,-2,-2))
        p.translate(r.center())
        p.translate(self.pan_px)

        # Heading-up while following. Free-pan keeps the last orientation but
        # allows the user to inspect geometry away from the truck.
        safe_heading = self.s.heading_deg if math.isfinite(self.s.heading_deg) else 0.0
        p.rotate(-safe_heading)
        ppm = (min(r.width(), r.height()) / 7000.0) * self.zoom

        labels = []
        poi_markers = []
        for road in self.s.roads:
            pts = []
            for lat, lon in road.points:
                x,y = local_xy(lat,lon,self.s.lat,self.s.lon)
                pts.append(QPointF(x*ppm,y*ppm))
            if len(pts)>1:
                p.setPen(self.road_pen(road.highway))
                p.drawPolyline(QPolygonF(pts))
                if road.name and len(labels)<25:
                    labels.append((pts[len(pts)//2], road.name))

        if len(self.s.route)>1:
            pts=[]
            for lat,lon in self.s.route:
                x,y=local_xy(lat,lon,self.s.lat,self.s.lon)
                pts.append(QPointF(x*ppm,y*ppm))
            p.setPen(QPen(FAINT, 11)); p.drawPolyline(QPolygonF(pts))
            p.setPen(QPen(BRIGHT, 4)); p.drawPolyline(QPolygonF(pts))

        if self.s.destination:
            x,y=local_xy(*self.s.destination,self.s.lat,self.s.lon)
            p.setPen(QPen(BRIGHT,2))
            p.drawEllipse(QPointF(x*ppm,y*ppm),10,10)
            p.drawLine(QPointF(x*ppm-14,y*ppm),QPointF(x*ppm+14,y*ppm))
            p.drawLine(QPointF(x*ppm,y*ppm-14),QPointF(x*ppm,y*ppm+14))

        if any(self.poi_enabled.values()) and self.zoom >= POI_MIN_ZOOM:
            for poi in self.s.pois:
                if not self.poi_is_enabled(poi.category):
                    continue
                x, y = local_xy(poi.lat, poi.lon, self.s.lat, self.s.lon)
                screen_pos = p.transform().map(QPointF(x*ppm, y*ppm))
                if r.adjusted(8, 8, -8, -38).contains(screen_pos):
                    poi_markers.append((screen_pos, poi))

        # Rotate road labels with map; deliberately feels like old hardware.
        p.setFont(self.mono(8, True)); p.setPen(DIM)
        used=set()
        for pos,name in labels:
            if name not in used:
                used.add(name); p.drawText(pos+QPointF(3,-3),name.upper()[:18])
        p.restore()
        self.draw_poi_markers(p, r, poi_markers)
        self.draw_poi_legend(p, r)

        # Truck stays centered in FOLLOW mode; in FREE PAN it moves with the
        # panned world so the user can see where the vehicle is relative to
        # the inspected area.
        c = r.center() if self.follow_vehicle else r.center() + self.pan_px
        p.setBrush(BLACK); p.setPen(QPen(BRIGHT,3))
        p.drawPolygon(QPolygonF([
            QPointF(c.x(),c.y()-24), QPointF(c.x()-16,c.y()+18),
            QPointF(c.x(),c.y()+9), QPointF(c.x()+16,c.y()+18)
        ]))
        p.setBrush(Qt.NoBrush)

        p.setPen(GREEN); p.setFont(self.mono(9,True))
        mode = "FOLLOW" if self.follow_vehicle else "FREE PAN"
        az = "AUTO" if self.auto_zoom else "MANUAL"
        p.drawText(int(r.left()+14),int(r.bottom()-14),
                   f"{mode}   ZOOM {self.zoom:0.2f}X {az}   +/- ZOOM   A AUTO")
        p.setPen(DIM); p.setFont(self.mono(7,True))
        detail_name = ("LOCAL", "REGIONAL", "MAJOR")[min(2, self.loaded_map_detail)]
        poi_on = sum(1 for value in self.poi_enabled.values() if value)
        source = self.loaded_map_source[:24]
        p.drawText(int(r.left()+14),int(r.bottom()-29),
                   f"{source}   MAP {self.loaded_map_radius_m/1000:0.1f}KM {detail_name}   CACHE {len(self.road_cache)}/{MAP_CACHE_LIMIT}   POI {poi_on}/6")
        if not self.follow_vehicle:
            label = "[ RECENTER ]"
            tw = p.fontMetrics().horizontalAdvance(label)
            p.setPen(BRIGHT)
            p.drawText(int(r.right()-tw-14),int(r.bottom()-14),label)

        if self.s.loading and not self.s.roads:
            p.setFont(self.mono(17,True))
            p.drawText(r,Qt.AlignCenter,self.s.loading+"...")
        elif self.s.loading in ("LOADING MAP", "LOADING ONLINE"):
            p.setPen(BRIGHT); p.setFont(self.mono(8,True))
            label = "[ ONLINE ]" if self.s.loading == "LOADING ONLINE" else "[ MAP LOAD ]"
            p.drawText(int(r.right()-105), int(r.top()+18), label)
        elif not self.s.roads:
            p.setFont(self.mono(15,True))
            p.drawText(r,Qt.AlignCenter,"NO ROAD GEOMETRY - CHECK MAP SERVICE")

    def next_maneuvers(self):
        if not self.s.maneuvers:
            return []
        # Pick first maneuver not clearly behind/at current position.
        scored=[]
        here=(self.s.lat,self.s.lon)
        for m in self.s.maneuvers:
            scored.append((haversine_m(here,m.location),m))
        idx=min(range(len(scored)), key=lambda i: scored[i][0])
        # If closest step is extremely close, advance to next.
        if scored[idx][0] < 25 and idx+1 < len(scored):
            idx += 1
        return self.s.maneuvers[idx:idx+4]

    def draw_panel(self,p,r):
        x,y,w=r.left(),r.top(),r.width()
        turns=self.next_maneuvers()
        nxt=turns[0] if turns else None

        p.setPen(GREEN); p.setFont(self.mono(15,True))
        p.drawText(int(x+18),int(y+30),"NEXT MANEUVER")
        if nxt:
            d=haversine_m((self.s.lat,self.s.lon),nxt.location)
            p.setFont(self.mono(18,True))
            p.drawText(int(x+18),int(y+68),nxt.instruction[:25])
            p.setFont(self.mono(15,True))
            p.drawText(int(x+18),int(y+99),nxt.road.upper()[:27])
            p.setFont(self.mono(24,True))
            p.drawText(int(x+18),int(y+139),format_distance(d))
        else:
            p.setFont(self.mono(15))
            p.drawText(int(x+18),int(y+70),"ENTER DESTINATION ABOVE")

        p.setPen(QPen(DIM,1))
        p.drawLine(int(x+15),int(y+160),int(x+w-15),int(y+160))
        p.setPen(GREEN); p.setFont(self.mono(12,True))
        p.drawText(int(x+18),int(y+188),"UPCOMING")
        p.setFont(self.mono(11))
        yy=y+218
        for m in turns[1:]:
            p.drawText(int(x+18),int(yy),m.instruction[:14])
            p.drawText(int(x+145),int(yy),m.road.upper()[:16])
            yy+=30

        p.setPen(QPen(DIM,1))
        p.drawLine(int(x+15),int(y+315),int(x+w-15),int(y+315))
        p.setPen(GREEN); p.setFont(self.mono(12,True))
        p.drawText(int(x+18),int(y+344),"ROUTE")
        p.setFont(self.mono(13))
        p.drawText(int(x+18),int(y+376),
                   f"DIST {self.s.route_distance_m/1609.344:0.1f} MI")
        p.drawText(int(x+18),int(y+404),
                   f"ETA  {self.s.route_duration_s/60:0.0f} MIN")
        p.drawText(int(x+18),int(y+432),
                   f"ACC  {self.s.accuracy_m:0.0f} M")

        # Live GPS coordinates directly beneath the accuracy readout.
        p.setPen(DIM)
        p.setFont(self.mono(10, True))
        p.drawText(int(x+18),int(y+456),f"LAT  {self.s.lat:+.6f}")
        p.drawText(int(x+18),int(y+476),f"LON  {self.s.lon:+.6f}")

        if self.s.destination_name:
            p.setPen(GREEN)
            p.setFont(self.mono(8))
            name=self.s.destination_name.upper()
            p.drawText(int(x+18),int(y+510),name[:42])
            p.drawText(int(x+18),int(y+526),name[42:84])

        if self.s.error:
            p.setPen(DIM); p.setFont(self.mono(9))
            msg=self.s.error.upper()
            p.drawText(int(x+18),int(r.bottom()-38),msg[:42])

        p.setPen(GREEN); p.setFont(self.mono(8))
        p.drawText(int(x+18),int(r.bottom()-16),"© OPENSTREETMAP CONTRIBUTORS")

    def scanlines(self,p):
        p.save(); p.setOpacity(.20); p.setPen(QPen(QColor(0,0,0),1))
        for y in range(0,self.height(),4): p.drawLine(0,y,self.width(),y)
        p.restore()

    def set_manual_zoom(self, factor):
        self.auto_zoom = False
        self.zoom = max(0.30, min(5.0, self.zoom * factor))
        self.schedule_map_refresh()
        self.update()

    def recenter(self):
        self.follow_vehicle = True
        self.pan_px = QPointF(0, 0)
        self.schedule_map_refresh(0)
        self.update()

    def wheelEvent(self, e):
        if self.map_rect.contains(e.position()):
            self.set_manual_zoom(1.18 if e.angleDelta().y() > 0 else 1/1.18)
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.poi_legend_rect.contains(e.position()):
            self.poi_legend_collapsed = not self.poi_legend_collapsed
            self.update()
            e.accept()
            return
        if e.button() == Qt.LeftButton and self.map_rect.contains(e.position()):
            self.dragging = True
            self.drag_last = e.position()
            self.follow_vehicle = False
            self.auto_zoom = False
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.dragging:
            delta = e.position() - self.drag_last
            self.pan_px += delta
            self.drag_last = e.position()
            self.schedule_map_refresh()
            self.update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            self.unsetCursor()
            self.schedule_map_refresh(0)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.map_rect.contains(e.position()):
            self.recenter()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.schedule_map_refresh()

    def keyPressEvent(self,e):
        if e.key()==Qt.Key_F11:
            self.window().setWindowState(self.window().windowState() ^ Qt.WindowFullScreen)
        elif e.key()==Qt.Key_Escape:
            self.window().showNormal()
        elif e.key()==Qt.Key_R:
            if self.follow_vehicle:
                self.calculate_route()
            else:
                self.recenter()
        elif e.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.set_manual_zoom(1.18)
        elif e.key()==Qt.Key_Minus:
            self.set_manual_zoom(1/1.18)
        elif e.key()==Qt.Key_A:
            self.auto_zoom = not self.auto_zoom
            if self.auto_zoom:
                self.recenter()
            self.update()
        elif e.key()==Qt.Key_G:
            self.toggle_poi_category("FUEL")
        elif e.key()==Qt.Key_P:
            self.toggle_poi_category("PARKING")
        elif e.key()==Qt.Key_F:
            self.toggle_poi_category("FOOD")
        elif e.key()==Qt.Key_M:
            self.toggle_poi_category("MEDICAL")
        elif e.key()==Qt.Key_B:
            self.toggle_poi_category("LODGING")
        elif e.key()==Qt.Key_L:
            self.toggle_poi_category("LANDMARK")
        elif e.key()==Qt.Key_Home:
            self.recenter()
        super().keyPressEvent(e)


def format_distance(m):
    if m < 160:
        return f"{max(10,round(m/10)*10):.0f} M"
    miles=m/1609.344
    return f"{miles:.1f} MI"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NAV-COM 2006 — Navigator")
        self.resize(1280,760)
        self.settings = QSettings("NAV-COM", "NAV-COM 2006")

        shell=QWidget(); layout=QVBoxLayout(shell)
        layout.setContentsMargins(12,10,12,10); layout.setSpacing(6)

        self.search=QLineEdit()
        self.search.setPlaceholderText("DESTINATION > type address/place and press ENTER")
        self.search.setStyleSheet("""
            QLineEdit { background:#010703; color:#48ff73; border:2px solid #48ff73;
                        padding:8px; font: bold 15px 'DejaVu Sans Mono'; }
            QLineEdit:focus { border:2px solid #a5ffb5; }
        """)

        gps_row = QWidget()
        gps_layout = QHBoxLayout(gps_row)
        gps_layout.setContentsMargins(0,0,0,0)
        gps_layout.setSpacing(6)

        self.net_gps_enabled = QCheckBox("NET GPS")
        self.net_gps_enabled.setChecked(
            self.settings.value("network_gps/enabled", False, type=bool)
        )

        self.net_gps_protocol = QComboBox()
        self.net_gps_protocol.addItems(["TCP", "UDP"])
        protocol = str(
            self.settings.value("network_gps/protocol", "TCP")
        ).upper()
        self.net_gps_protocol.setCurrentText(
            protocol if protocol in ("TCP", "UDP") else "TCP"
        )

        self.net_gps_host = QLineEdit()
        env_endpoint = os.environ.get("NAVCOM_GPS_NETWORK", "").strip()
        env_host = ""
        env_port = 8080
        env_protocol = "TCP"
        if env_endpoint:
            try:
                parsed = parse_network_gps_endpoint(env_endpoint)
                if parsed:
                    env_protocol, env_host, env_port = (
                        parsed[0].upper(), parsed[1], parsed[2]
                    )
            except Exception:
                pass

        saved_host = self.settings.value(
            "network_gps/host", env_host or "192.168.1.87"
        )
        saved_port = self.settings.value(
            "network_gps/port", env_port, type=int
        )
        if not self.settings.contains("network_gps/protocol") and env_endpoint:
            self.net_gps_protocol.setCurrentText(env_protocol)

        self.net_gps_host.setText(str(saved_host))
        self.net_gps_host.setPlaceholderText("GPS HOST / IP")
        self.net_gps_host.setMaximumWidth(185)

        self.net_gps_port = QSpinBox()
        self.net_gps_port.setRange(1, 65535)
        self.net_gps_port.setValue(int(saved_port or 8080))
        self.net_gps_port.setMaximumWidth(90)

        self.net_gps_apply = QPushButton("APPLY")
        self.net_gps_status = QLabel("")

        gps_layout.addWidget(self.net_gps_enabled)
        gps_layout.addWidget(self.net_gps_protocol)
        gps_layout.addWidget(self.net_gps_host)
        gps_layout.addWidget(self.net_gps_port)
        gps_layout.addWidget(self.net_gps_apply)
        gps_layout.addWidget(self.net_gps_status, 1)

        gps_row.setStyleSheet("""
            QCheckBox, QLabel {
                color:#48ff73;
                font: bold 11px 'DejaVu Sans Mono';
            }
            QLineEdit, QSpinBox, QComboBox {
                background:#010703;
                color:#48ff73;
                border:1px solid #1b7d35;
                padding:4px;
                font: bold 11px 'DejaVu Sans Mono';
            }
            QPushButton {
                background:#010703;
                color:#48ff73;
                border:1px solid #48ff73;
                padding:4px 10px;
                font: bold 11px 'DejaVu Sans Mono';
            }
            QPushButton:hover {
                border:1px solid #a5ffb5;
                color:#a5ffb5;
            }
        """)

        self.display=NavDisplay()
        self.search.returnPressed.connect(
            lambda: self.display.search_destination(self.search.text())
        )
        self.net_gps_apply.clicked.connect(self.apply_network_gps_settings)
        self.net_gps_enabled.toggled.connect(self.update_network_gps_controls)

        layout.addWidget(self.search)
        layout.addWidget(gps_row)
        layout.addWidget(self.display,1)
        self.setCentralWidget(shell)
        shell.setStyleSheet("background:#010703;")

        self.update_network_gps_controls()
        QTimer.singleShot(0, self.apply_network_gps_settings)

    def update_network_gps_controls(self):
        enabled = self.net_gps_enabled.isChecked()
        self.net_gps_protocol.setEnabled(enabled)
        self.net_gps_host.setEnabled(enabled)
        self.net_gps_port.setEnabled(enabled)
        self.net_gps_status.setText(
            "NETWORK NMEA ENABLED" if enabled else "NETWORK NMEA DISABLED"
        )

    def apply_network_gps_settings(self):
        enabled = self.net_gps_enabled.isChecked()
        host = self.net_gps_host.text().strip()
        port = self.net_gps_port.value()
        protocol = self.net_gps_protocol.currentText().lower()

        try:
            self.display.set_network_gps(
                enabled, host, port, protocol
            )
        except Exception as exc:
            self.net_gps_status.setText("ERROR: " + str(exc).upper())
            return

        self.settings.setValue("network_gps/enabled", enabled)
        self.settings.setValue("network_gps/protocol", protocol.upper())
        self.settings.setValue("network_gps/host", host)
        self.settings.setValue("network_gps/port", port)
        self.settings.sync()

        if enabled:
            self.net_gps_status.setText(
                f"{protocol.upper()} {host}:{port}"
            )
        else:
            self.net_gps_status.setText("NETWORK NMEA DISABLED")


def main():
    app=QApplication(sys.argv); app.setStyle("Fusion")
    w=MainWindow(); w.show()
    sys.exit(app.exec())


if __name__=="__main__":
    main()