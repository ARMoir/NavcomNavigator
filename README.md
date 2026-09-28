# NAV-COM 2006 — Navigator POC v0.3.5

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

- **OpenStreetMap / Overpass** for nearby road geometry
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

When heading data is unavailable while stationary, the display safely remains
north-up until a usable heading can be obtained or derived from movement.

## Version history

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