# Getting ready for flightsim.to

Done:
- [x] No personal defaults: first-run setup asks for the pilot name and home airfield
- [x] The community MSFS POI map is downloaded at first run, not shipped in the package
- [x] Versioning, changelog and in-app updates from GitHub (public repo onward-sim/Onward)
- [x] User data separate from the program; log file for bug reports (onward.log)
- [x] License: AGPL-3.0-or-later (required in practice by Python-SimConnect, AGPL-3.0)
- [x] Credits for every data source and library (CREDITS.md, and Settings › About and credits)
- [x] Map and data terms checked: satellite map moved from Esri (needs an ArcGIS license) to EOxCloudless (free non-commercial, credited)
- [x] Weather requests kept under Open-Meteo's free limit (daily budget, caching), MET Norway as backup, terrain from AWS Terrain Tiles
- [x] Icon (assets/onward.ico) for the exe and the browser tab
- [x] Windows build tested on GitHub's Windows runners: Onward.exe builds, starts in demo mode, makes offers and plans
- [x] Automated checks: `python tests/smoke.py` (calculations plus every endpoint and a full demo leg)
- [x] Listing screenshots

- [x] Permission from the creator of the community "Microsoft Flight Simulator Map" to use it in Onward (the map is still downloaded at first run, never shipped)
- [x] The packaged app checks stable.json and points to flightsim.to for new versions, never to GitHub
- [x] The release zip includes the complete source code (AGPL-3.0), so the listing needs no outside link

Still to do before publishing:
- [ ] A closed beta in the real sim: send-to-sim, landing grades, failures, reconnects, screenshots, GTNXi files, first run of Onward.exe on a gaming PC (Windows firewall prompt)
- [ ] A neutral flightsim.to account (not linked to any personal account)
- [ ] VirusTotal scan of the release zip: it must show 0 detections

## Two channels

- **GitHub `main`: the fast lane.** Every push to `main` with a new version in version.json is offered to everyone running from source, through the in-app updater. Use it to test and iterate.
- **flightsim.to: picked versions only.** The packaged Onward.exe reads `stable.json` on `main` and only announces versions listed there, with a link to the listing. It never downloads anything itself.

## Publishing a version on flightsim.to

1. Pick a version from `main` that has been tried in the sim. Run `python tests/smoke.py` (ALL OK).
2. Tag it: `git tag v0.x.y && git push origin v0.x.y`. GitHub builds, tests and zips Onward.exe (`Onward-0.x.y-windows.zip`, with the source code inside) and attaches it to a GitHub release.
3. Scan the zip on VirusTotal (0 detections), then upload it to the flightsim.to listing as a new version, with the CHANGELOG entry as the notes.
4. Once it's live there, mark it stable: copy `version`, `date` and `notes` from version.json into `stable.json`, set `url` to the listing address, and push to `main`. Packaged apps then show "Get it on flightsim.to".
