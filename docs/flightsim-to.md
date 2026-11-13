<!-- Title for the upload form (title case, per flightsim.to rules): Onward: Solo GA Travel Career for MSFS 2024 -->
# Onward: Solo GA Travel Career for MSFS 2024

*Every flight starts where the last one ended. Free, external, browser-based.*

Onward is a companion app for GA pilots who want a reason to fly without running an airline. You're a travelling pilot with your own airplane, hopping from one airfield to the next and seeing the world on the way. There are no clients, no money and no subscriptions, just a journey that keeps going.

## How it works

Pick a distance range and a mood (relaxed or tough, grass strips, weather, night) and Onward suggests about a dozen flights from where you are now, each with a reason to fly it: sights along the way, a short or high runway, a crosswind, a new country, a photogrammetry city at the end. Or pick any airfield yourself, or plan a multi-day trip with fuel and overnight stops.

Take one, fly it in the sim, and Onward follows along through SimConnect. When you shut down, it writes a journal entry, and your next flight starts from there.

## Planning

- Touring routes with real VORs and NDBs as checkpoints, and detours to sights worth seeing, including MSFS World Update POIs and photogrammetry cities
- Edit the route on the map: drag points, add sights, navaids or airfields, and see what each change costs in miles, minutes and fuel
- Route shapes: direct, scenic, low and slow along the lower ground, or navaid to navaid
- A side view of the route: terrain, your altitude, minimum safe altitude, forecast cloud and freezing level, with ways out when something clashes
- Weather along the route from the hourly forecast at each waypoint, plus the destination TAF for your arrival time
- Takeoff and landing distances corrected for density altitude, wind and surface
- An arrival briefing: runway for the wind, circuit altitude, how to join, terrain and frequencies
- Weather overlays: radar, live satellite clouds, forecast flying conditions (VFR to LIFR) for the next six hours, METAR flight categories, SIGMETs and AIRMETs, winds aloft and low cloud
- A .pln for MSFS, a Garmin .gfp for the TDS GTNXi, a printable navlog and a SimBrief prefill for IFR days

## In flight

- Guidance to the next waypoint, with heads-ups before each sight
- A stable approach check at 500 ft and a landing grade from A to D
- Optional spoken callouts, and a cockpit mode for a tablet or second screen
- Screenshots you take are saved to the leg and placed on the map

## Consequences and continuity

Onward watches how you treat the airplane: hard landings, overspeed, flap and gear limits, overrevving, shock cooling. Wear builds up across the engine, airframe, gear and prop; worn parts can fail for real in the sim, and repairs take real time. A crash doesn't end the journey. Every leg goes into the journal, with your photos and the track you actually flew on a world map.

## Requirements

- **Microsoft Flight Simulator 2024** on Windows. Onward is made for MSFS 2024 and hasn't been tested with MSFS 2020.
- No other add-ons needed. Onward.exe includes everything, so there's no Python to install.
- An internet connection for weather, terrain and landmarks (all free, no accounts)
- Any browser: it also runs on a tablet or a Mac on the same network

## Installation

1. Unzip the download anywhere, for example in Documents.
2. Double-click **Onward.exe**. Onward opens in its own window.
3. If Windows asks whether to allow Onward through the firewall, allow it. It's only needed to use Onward from a tablet or another computer.
4. Pick a name and a home airfield, and you're off. The first start downloads the airfield database (about 15 MB), so it takes a minute.

Keep Onward open while you fly (closing its window stops it). Onward connects to MSFS by itself, before or after the sim starts.

**Updating:** Onward tells you when a new version is out here on flightsim.to. Download it and replace the program folder. Your career, journal, photos and settings are kept separately in `%LOCALAPPDATA%\Onward`, so nothing is lost.

## Known issues and limitations

- **Weather is the real-world forecast**, not the sim's live weather, so the two can differ. The side view says which forecast it used.
- **Airfield and runway data come from real-world sources**, so small strips can be missing or placed differently in the sim.
- **Study-level aircraft** with their own systems may ignore the sim's failure events.
- **Flight plans are loaded by you:** download the .pln and load it in the MSFS world map or the EFB. MSFS 2024 only takes part of a plan sent from outside, so Onward doesn't send it by itself.
- Charts and data are for flight simulation only.

## Source code

Onward is free software under the AGPL-3.0. The complete source code is included in the download, in the `Source code` folder.

## Credits

See CREDITS.md in the download.
