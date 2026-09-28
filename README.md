# NAV-COM 2006 — Navigator POC v0.3.8

NAV-COM 2006 is a retro-styled Python navigation proof of concept inspired by
1980s monochrome vector/CRT displays. It uses a custom PySide6 renderer rather
than normal map tiles, so the roads, route, instruments, labels, and CRT effects
are drawn directly by the application.

## Features

- Windows laptop geolocation
- real nearby OpenStreetMap road geometry
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

These public endpoints should not be treated as the production backend for an
always-on vehicle navigator. A truck-ready build should use an appropriate
hosted provider or, preferably for this project, local/offline OSM data and a
local routing engine.

## GPS reality

A Windows laptop without dedicated GNSS hardware may report location using
Wi-Fi or other network-derived positioning. That is useful for proving the
software but is not the ideal source for navigation in a moving vehicle.

For an in-vehicle build, a USB GNSS receiver outputting NMEA is the preferred
input. The renderer/navigation state is intentionally separated so an NMEA
adapter can replace Windows Location Services without redesigning the UI.

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

The current fetch radius is capped at 24 km because this POC still uses the
public Overpass service. An offline map backend will remove that practical
limit.

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
- local/offline map extract
- local Valhalla or OSRM routing
- offline address search
- route-progress-aware maneuver selection
- spoken turn prompts
- intersection-focused zoom
- route simulation/replay mode for desk testing