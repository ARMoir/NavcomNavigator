# NAV-COM 2006 — Navigator POC v0.3.3

This is now an actual navigation proof of concept rather than only a map viewer.

## Features

- Windows laptop geolocation
- real nearby OpenStreetMap road geometry
- destination entry
- explicit destination search on Enter
- OSRM driving route calculation
- full route geometry
- bright-green active route
- turn/maneuver list
- heading-up moving map
- speed and heading from Windows when available
- movement-derived heading/speed fallback
- periodic position refresh
- off-route detection
- automatic rerouting
- manual reroute (`R`)
- fullscreen (`F11`)
- OSM attribution

## Install

Enable Windows Location Services:

Settings -> Privacy & security -> Location

Then:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Enter an address/place in the destination box and press Enter.

## Important POC notes

This build intentionally uses public/community services only for light personal
testing:

- OpenStreetMap/Overpass for nearby road geometry
- Nominatim for explicit, user-triggered destination search
- OSRM's public demo routing server for route calculation

There is no autocomplete. Destination search only occurs when you press Enter.

Do not treat the public endpoints as the production backend for an always-on
vehicle navigator. The truck version should use a suitable hosted provider or,
preferably for this project, local/offline OSM + routing data.

## GPS reality

A Windows laptop without GNSS hardware may report a location derived from
Wi-Fi/network information. That can be good enough to prove the software but
is not the GPS source I would use in a moving vehicle.

For the Ranger build, a USB GNSS receiver outputting NMEA is the better input.
The renderer/navigation state is separated enough that an NMEA adapter can
replace Windows Location Services without changing the UI.

## Next logical upgrade

- USB GPS/NMEA support
- local map extract
- local Valhalla/OSRM routing
- offline address search
- route-progress-aware maneuver selection
- spoken turn prompts
- zoom based on speed / distance to maneuver
- route simulation/replay mode for desk testing


## v0.3.3 fix

Windows Location Services can return IEEE NaN for speed and heading when those
values are unavailable. v0.3 passed that NaN heading into QPainter.rotate(),
which invalidated the world transform and made all map/route vectors disappear.

v0.3.3 sanitizes all location values, uses north-up when no valid heading is
available, derives heading only after meaningful movement, and displays the
loaded road count as `RD ####` in the header. If the road service fails, the
map pane now says `NO ROAD GEOMETRY - CHECK MAP SERVICE`.


## v0.3.3
Adds a live phosphor clock/date and scrolling compass tape with cardinal points, degree ticks, fixed lubber line, numeric heading, and a cleaner ground-speed readout. Instruments refresh at 4 Hz without increasing GPS/network polling.


## v0.3.3 interactive moving map

- mouse wheel zoom
- `+` / `-` keyboard zoom
- click-and-drag map panning
- automatic switch from FOLLOW to FREE PAN when dragged
- double-click map to recenter
- `Home` to recenter
- `R` recenters when panned; reroutes while following
- visible FOLLOW / FREE PAN state
- visible zoom multiplier
- AUTO / MANUAL zoom state
- `A` toggles automatic zoom and recenters when enabled
- auto zooms out at road/highway speeds
- auto zooms progressively closer near the next maneuver
- manual zoom disables AUTO so the navigator does not fight the driver
- zoom range is clamped from 0.30x to 5.0x

The loaded OSM road radius is still finite. Extreme panning can therefore move
beyond the currently downloaded road geometry; recentering returns to the
vehicle, and road geometry is refreshed as the vehicle travels.
