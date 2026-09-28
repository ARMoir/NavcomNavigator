# NAV-COM 2006 — Navigator POC v0.4.4

NAV-COM 2006 is a retro-styled Python navigation proof of concept inspired by
1980s monochrome vector/CRT displays. It uses a custom PySide6 renderer rather
than normal map tiles, so the roads, route, instruments, labels, and CRT effects
are drawn directly by the application.

## Features

- configurable TCP/UDP network NMEA GPS input
- USB/serial NMEA GPS fallback
- in-app network GPS host/port settings with persistent preferences
- cross-platform system-location fallback through Qt Positioning
- direct Windows Location Services fallback when needed
- real nearby OpenStreetMap road geometry
- online-preferred map loading with fast offline preview/fallback
- destination entry and explicit search on Enter
- OSRM driving route calculation
- full route geometry with bright-green active route
- turn/maneuver list
- heading-up moving map
- speed and heading from Windows when available
- movement-derived heading/speed fallback
- periodic position refresh
- live GPS latitude/longitude readout
- off-route detection and automatic rerouting
- live phosphor digital clock and date
- scrolling compass tape with N / E / S / W, degree ticks, and fixed lubber line
- interactive zoom and map panning
- viewport-aware map loading with prefetch
- in-memory road-area cache
- adaptive road detail at wide zoom levels
- optional Pip-Boy-style boxed POI/landmark layer
- independent keyboard toggle for every POI category
- live on-map POI legend showing each category ON/OFF
- FOLLOW and FREE PAN modes
- manual recentering
- automatic speed/maneuver-based zoom
- fullscreen mode
- OpenStreetMap attribution

## Install

Enable Windows Location Services:

```text
Settings -> Privacy & security -> Location
```

Then:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Enter an address or place in the destination box and press **Enter**.

## Controls

| Control | Action |
| --- | --- |
| Mouse wheel | Zoom in/out |
| Click + drag | Pan map and enter FREE PAN mode |
| Double-click map | Recenter on vehicle |
| `+` / `-` | Zoom in/out |
| `A` | Toggle AUTO ZOOM; enabling it recenters |
| `G` | Toggle gas/fuel POIs |
| `P` | Toggle parking POIs |
| `F` | Toggle food POIs |
| `M` | Toggle medical/pharmacy POIs |
| `B` | Toggle lodging POIs |
| `L` | Toggle landmark/attraction/historic POIs |
| `Home` | Recenter |
| `R` | Recenter while panned; reroute while following |
| `F11` | Toggle fullscreen |
| `Esc` | Leave fullscreen |

Manual zoom disables AUTO ZOOM so the navigator does not fight the driver.
AUTO ZOOM moves farther out at road/highway speeds and progressively closer as
the next maneuver approaches.

The current zoom range is **0.30x to 5.0x**.

## Map and routing services

This proof of concept intentionally uses public/community services only for
light personal testing:

- **OpenStreetMap / Overpass** for nearby road geometry and selected POIs
- **Nominatim** for explicit, user-triggered destination searches
- **OSRM public demo server** for driving routes and maneuver steps

There is no autocomplete. Destination lookup only occurs when Enter is pressed.

NAV-COM now prefers live Overpass road/POI data whenever it is available.
If a local `.navmap` covers the current area, that database can be displayed
immediately as a fast preview while the online request is still running. The
online result replaces it as soon as it arrives. If the online request fails,
the offline data remains as the fallback.

Destination search and driving routes still use the public Nominatim and OSRM
services in this version.

## GPS and location priority

NAV-COM accepts standard NMEA data from either a configured network stream or
a serial/USB GNSS receiver. A configured network endpoint is tried first; if it
is disabled or unavailable, NAV-COM automatically continues down the fallback
chain.

The location priority is:

```text
configured TCP/UDP network NMEA GPS
        ↓ disabled / unavailable
USB / serial NMEA GPS
        ↓ no valid fix
platform system location
        ↓ unavailable
Windows direct Location Services fallback
        ↓ unavailable
last known position / configured fallback
```

The main window includes a compact **NET GPS** settings row with:

- enable / disable
- TCP or UDP
- host / IP address
- port
- Connect / Save

The host, port, and protocol remain editable even when NET GPS is disabled, so
you can configure the endpoint first and then enable it. Pressing Enter in the
host field or clicking **CONNECT** applies the current values immediately.

The status text now reports the real connection state, including
`CONNECTING`, `NET GPS ACTIVE`, or `NET GPS FAILED - USING FALLBACK`.
Changes take effect without restarting NAV-COM and are saved using Qt's native
settings store. The environment variable `NAVCOM_GPS_NETWORK` remains
available as an optional initial default.

For example, the NMEA stream:

```text
192.168.1.87:8080
```

can be entered directly as host `192.168.1.87`, port `8080`, protocol
`TCP`, with **NET GPS** enabled.

The platform system-location layer uses Qt Positioning. Depending on the
operating system and available services, that can use native/network-derived
location such as Wi-Fi positioning:

- Windows: system location backend, with the existing direct WinRT path retained
  as an additional fallback
- macOS: the native Core Location backend when permission is available
- Linux: the installed Qt/GeoClue positioning backend when available

The header identifies the active source as `NET GPS`, `GPS LOCK`,
`WIN LOC`, `MAC LOC`, `LINUX LOC`, or `LOC FALLBACK`.

Most NMEA receivers use 9600 or 4800 baud, both of which NAV-COM tries
automatically. You can force a serial device, baud rate, or network endpoint
with:

```text
NAVCOM_GPS_PORT
NAVCOM_GPS_BAUD
NAVCOM_GPS_NETWORK
```

Network endpoint examples:

```text
192.168.1.87:8080
tcp://192.168.1.87:8080
udp://192.168.1.87:8080
```

For example on Windows:

```powershell
$env:NAVCOM_GPS_PORT = "COM4"
$env:NAVCOM_GPS_BAUD = "9600"
python main.py
```

On macOS, native location access in a packaged application requires the normal
location usage description/permission in the application bundle. Linux system
location depends on the desktop/distribution providing an available positioning
service such as GeoClue.

## Current map behavior

Map data now follows the **visible viewport**, not only the vehicle position.

When you pan or zoom, NAV-COM calculates the geographic area currently visible,
checks whether an in-memory map cache already covers it, and loads additional
OpenStreetMap road geometry only when necessary. Requests include extra
prefetch coverage beyond the screen edges, so small pans do not immediately
trigger another network request.

The cache currently keeps the eight most recently used map areas in memory.
Returning to a recently viewed area can therefore reuse its road geometry
without downloading it again.

At wider zoom levels the map intentionally reduces road detail:

- **LOCAL** — full drivable-road detail
- **REGIONAL** — primary through tertiary/unclassified roads
- **MAJOR** — major road network only

This keeps large-area Overpass requests reasonable while preserving useful
navigation context. The bottom-left map status reports the loaded radius,
detail tier, and cache occupancy.

Online Overpass requests remain capped at a 24 km radius. When a matching
offline `.navmap` is installed, road and POI queries are read directly from
SQLite instead.

### Offline maps

NAV-COM scans `offline_maps/*.navmap` when it starts. Online map data is the
preferred source, but installed offline coverage is used in two situations:

- as an immediate preview while the online map request is still loading
- as the fallback if the online request fails

If no offline map covers the area, NAV-COM simply waits for the normal online
Overpass result.

The footer identifies the active source, for example:

```text
OFFLINE PREVIEW NEW ENGLAND
```

while live data is loading, then:

```text
ONLINE
```

when the preferred source arrives. During an outage it shows:

```text
OFFLINE FALLBACK NEW ENGLAND
```

The normal NAV-COM installation does not need any extra packages to **use**
offline maps. The optional builder uses `osmium` only on the computer where
you create the database.

For New England, the simplest setup from the repository root is:

```powershell
python -m pip install -r tools/requirements.txt
python tools/download_new_england.py --build
```

That downloads the Connecticut, Maine, Massachusetts, New Hampshire, Rhode
Island, and Vermont OSM extracts and produces:

```text
offline_maps/new-england.navmap
```

For another location, download one or more `.osm.pbf` files and run:

```powershell
python tools/build_navmap.py --name "Florida" --output offline_maps/florida.navmap downloads/florida-latest.osm.pbf
```

The builder accepts either individual PBF files or a directory containing
several PBF files. Generated PBF and `.navmap` files are ignored by Git.

Offline routing is deliberately separate for now: the map and POIs can be
fully local while route calculation continues to use OSRM online.

### POI layer

The optional POI layer is loaded with the same viewport request and stored in
the same map cache as the road geometry, so enabling POIs does not create a
second stream of map requests.

Markers use intentionally simple Pip-Boy-style boxed glyphs:

| Glyph | Category |
| --- | --- |
| `G` | Gas / fuel |
| `P` | Parking |
| `F` | Food |
| `M` | Medical / pharmacy |
| `B` | Lodging |
| `L` | Landmark / attraction / historic place |

Marker density is zoom-aware. At wide zoom levels fewer markers are shown, and
at very wide views the POI query is restricted to higher-value categories.
Names appear beside markers only at closer zoom levels to keep the map legible.

Each POI category is independently controlled by the same letter used in its
map icon: `G`, `P`, `F`, `M`, `B`, and `L`. Toggling a category
does not reload map data; it only changes what is rendered from the current
viewport cache.

A compact on-map **POI LEGEND** shows all six categories and their current
**ON/OFF** state. All categories start enabled.

The legend is intentionally small. Clicking anywhere on the expanded legend
collapses it into a tiny `POI n/6 [+]` status strip; clicking that strip
expands the full legend again. Legend clicks are handled before map dragging,
so collapsing or expanding it does not pan the map.

When heading data is unavailable while stationary, the display safely remains
north-up until a usable heading can be obtained or derived from movement.

## Version history

### v0.4.4 — Simplified network GPS setup

- host/IP, port, and protocol remain editable while NET GPS is disabled
- NET GPS checkbox now immediately enables or disables the network source
- CONNECT applies the currently displayed endpoint without restarting
- pressing Enter in the host field applies the endpoint
- settings are saved independently from whether network GPS is enabled
- status text now reports CONNECTING, ACTIVE, or fallback after failure
- disabling network GPS immediately returns to serial/system location fallback

### v0.4.3 — In-app network GPS settings

- added a compact NET GPS configuration row to the main window
- host/IP and port can be changed without restarting
- TCP/UDP can be selected from the UI
- network NMEA can be enabled or disabled independently
- settings persist through Qt's native settings store
- UI settings override the optional environment default for the current session

### v0.4.2 — Network NMEA GPS

- added configurable TCP NMEA input
- added UDP NMEA input
- configured network GPS is preferred before serial GPS
- network streams reuse the same GGA/RMC parser as USB/serial receivers
- added `NAVCOM_GPS_NETWORK` configuration
- header displays `NET GPS` while network NMEA is active

### v0.4.1 — GPS-first cross-platform location fallback

- added automatic serial/USB NMEA GPS discovery
- GPS is now the preferred location source on every supported desktop platform
- parses valid RMC and GGA NMEA fixes
- uses NMEA course/speed when available and a rough GGA HDOP-based accuracy display
- added Qt Positioning as the platform system-location fallback
- supports native/network-derived location fallback on Windows, macOS, and Linux when the platform backend is available
- retained the direct Windows Location Services path as an additional Windows fallback
- added `NAVCOM_GPS_PORT` and `NAVCOM_GPS_BAUD` overrides
- header now distinguishes GPS, Windows, macOS, Linux, and fixed fallback location sources
- added `pyserial` to runtime requirements

### v0.4.0 — Online-first hybrid map loading

- live Overpass data is now the preferred road/POI source
- installed navmaps can paint the viewport immediately while online data loads
- online data replaces the offline preview as soon as it arrives
- offline data remains in place when the online request fails
- offline fallback cache entries are retried periodically so NAV-COM can return online after connectivity recovers
- map footer distinguishes ONLINE, OFFLINE PREVIEW, and OFFLINE FALLBACK states

### v0.3.9 — Simple offline map database support

- NAV-COM now scans `offline_maps/*.navmap` automatically at startup
- installed SQLite maps provide roads and POIs without Overpass
- online Overpass remains the automatic fallback outside installed coverage
- map cache entries retain and display whether their source is ONLINE or OFFLINE
- added `tools/build_navmap.py` to build one navmap from one or many OSM PBF files
- added `tools/download_new_england.py --build` for the simple New England setup
- added a separate tools dependency file so normal NAV-COM runtime installs stay unchanged
- generated PBF and navmap data are excluded from Git
- routing remains online in this version

### v0.3.8 — Compact collapsible POI legend

- reduced the expanded POI legend footprint
- tightened icon, font, and row spacing
- clicking the legend collapses it into a small `POI n/6 [+]` strip
- clicking the collapsed strip restores the full legend
- legend clicks no longer fall through to map panning

### v0.3.7 — Individual POI controls and legend

- replaced preset POI modes with six independent category toggles
- `G` toggles fuel
- `P` toggles parking
- `F` toggles food
- `M` toggles medical/pharmacy
- `B` toggles lodging
- `L` toggles landmarks/attractions/historic places
- added a compact Pip-Boy-style on-map legend showing each category ON/OFF
- POI toggles operate entirely on cached data and do not cause new map requests
- map footer now summarizes how many POI categories are enabled

### v0.3.6 — Pip-Boy POI layer

- added boxed retro map markers for fuel, parking, food, medical, lodging, and landmarks
- POIs are fetched in the same Overpass request as road geometry
- POIs share the existing viewport-aware map cache
- added zoom-aware marker density and close-range POI labels
- added `P` to toggle POIs on/off
- added `Shift+P` to cycle ALL, FUEL, LANDMARKS, SERVICES, and OFF filters
- wide-area map requests automatically reduce POI categories to limit clutter and service load
- added live POI mode status to the map footer

### v0.3.5 — GPS coordinate readout

- added live latitude and longitude beneath the GPS accuracy readout
- coordinates are shown to six decimal places
- adjusted destination text placement to preserve panel spacing

### v0.3.4 — Viewport-aware map loading

- map coverage now follows the visible viewport instead of only the vehicle
- panning into a new area automatically requests additional road geometry
- zooming out requests enough geography to cover the wider view
- added a prefetch margin beyond the visible screen edges
- added an eight-area in-memory road cache
- revisiting cached areas avoids unnecessary network requests
- map loading is debounced while dragging to reduce repeated requests
- wide zoom levels automatically use REGIONAL or MAJOR road detail
- added live map radius/detail/cache diagnostics to the map footer
- existing roads remain visible while additional map data loads
- map requests are capped at 24 km while using the public Overpass endpoint

### v0.3.3 — Interactive moving map

- added mouse-wheel and keyboard zoom
- added click-and-drag map panning
- added FOLLOW and FREE PAN states
- added double-click, Home, and context-sensitive R recentering
- added visible zoom mode/status
- added AUTO ZOOM based on speed and distance to the next maneuver
- manual zoom now disables AUTO ZOOM

### v0.3.2 — Instrument panel

- added live 24-hour phosphor clock with seconds
- added compact date display
- added scrolling 90-degree compass tape
- added 5-degree ticks and 15-degree labels
- emphasized N / E / S / W cardinal points
- added fixed bright-green lubber line and numeric heading
- added dedicated ground-speed readout
- kept the loaded-road diagnostic
- instrument refresh runs independently without increasing GPS/network polling

### v0.3.1 — Location/rendering fix

Windows Location Services can return IEEE `NaN` for speed and heading when
those values are unavailable. Earlier builds could pass a `NaN` heading into
`QPainter.rotate()`, invalidating the world transform and making the map and
route vectors disappear.

v0.3.1:

- sanitizes non-finite location values
- defaults safely to north-up when heading is unavailable
- derives heading only after meaningful movement
- displays the loaded road count as `RD ####`
- shows an explicit message when no road geometry is available

## Next logical upgrades

- USB GPS / NMEA receiver support
- local Valhalla or OSRM routing
- offline address search
- route-progress-aware maneuver selection
- spoken turn prompts
- intersection-focused zoom
- route simulation/replay mode for desk testing