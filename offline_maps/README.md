# Offline maps

NAV-COM automatically scans this directory for files ending in `.navmap`.

A `.navmap` file is a normal SQLite database containing the road geometry,
POIs, coverage bounds, and a small metadata table needed by the navigator.

Generated map databases are intentionally ignored by Git because they can be
large. Copy any finished `.navmap` file into this directory and restart
NAV-COM.

## New England

From the repository root:

```powershell
python -m pip install -r tools/requirements.txt
python tools/download_new_england.py --build
```

That downloads the six New England state extracts and builds:

```text
offline_maps/new-england.navmap
```

## Any other region

Download one or more `.osm.pbf` files, then run:

```powershell
python tools/build_navmap.py --name "Florida" --output offline_maps/florida.navmap downloads/florida-latest.osm.pbf
```

The builder also accepts a directory containing multiple `.osm.pbf` files.
