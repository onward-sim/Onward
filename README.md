# Onward

A solo travelling pilot's career for Microsoft Flight Simulator 2024. You pick a home airfield, and every flight begins where the last one ended. Onward picks interesting destinations around you, tells you what's worth seeing on the way, watches how you treat the airplane, and keeps a travel journal.

Everything it uses is free: airfields, navaids and frequencies from OurAirports, live weather from aviationweather.gov, forecasts from Open-Meteo and MET Norway, terrain from the open AWS Terrain Tiles, landmarks from OpenStreetMap, and the sim itself through SimConnect. See CREDITS.md. Onward is free software under the AGPL-3.0 (LICENSE.txt).

## Install (sim PC)

**The easy way: the Windows app.** Download Onward from flightsim.to, unzip it anywhere and double-click **Onward.exe**. It opens in its own window (no Python, no browser tab); close the window to stop Onward. Settings › Updates can also open it in your normal browser. Your career and settings live in `%LOCALAPPDATA%\Onward`, so you can replace the program folder with a new version at any time. The zip also holds the complete source code.

**From source** (gets updates from inside the app). You get the newest changes as soon as they're made, so it can be a little rougher. The Windows app on flightsim.to only gets versions that have been tried in the sim first.

1. Install **Python 3.11 or newer** from https://www.python.org/downloads/ . During the install, tick **"Add python.exe to PATH"**.
2. Unzip Onward anywhere, for example `Documents\Onward`.
3. Double-click **start.bat**. The first run installs the Python packages and downloads the airfield database (about 15 MB).

Either way, a browser tab opens at http://localhost:8642. Pick your name and home airfield: that's where the journey starts. Keep the black window open while you fly, and close it when you're done. Onward connects to MSFS by itself, before or after the sim starts.

To try everything without the sim, double-click **start-demo.bat**. It uses a pretend airplane and a separate demo career, so your real journey stays clean.

To use an iPad or the Mac as the screen, open `http://<sim PC's IP address>:8642` in its browser (allow Python through the Windows firewall when asked).

## Updates

Onward checks for a new version when it starts. When you run it from source, a note appears at the top when there is one: press **Update now** and it downloads the new version, installs it and restarts by itself. The Windows app only tells you about versions that are on flightsim.to, and you download those from there. You can also check from **Settings › Updates**, where you'll see what's new and can go back to the previous version if something isn't right.

Updates only replace the program files. Your career, journal, photos and settings live in the `data` folder, which is never touched (the packaged app keeps them in `%LOCALAPPDATA%\Onward`). If something goes wrong, `data\onward.log` has the details.

## How a leg works

1. In **Fly**, set the distance and mood (relaxed or tough, grass strips, weather, darkness) and press **Find adventures**. You get about a dozen flights spread across your distance range, shortest first. Click one to see the details.
2. Take one, press **Download .pln for MSFS** and load it in the MSFS world map or the EFB (Load from PC). With a TDS GTNXi, **Save for the GTNXi** puts the plan straight into the GTN.
3. In MSFS, start a **Free Flight** at the departure airfield with the aircraft you want. Onward detects common GA aircraft by name (C172, C182, Comanche, SR22, Bonanza, Cubs, Beaver, Kodiak and more).
4. Fly. Onward tracks the flight, marks sights you pass within 2.5 nm, and logs every exceedance.
5. Land, taxi in and shut down the engine. The debrief and journal entry appear on their own (or press **Finish leg**).

Land somewhere else and that's where you are now. Put it in a field and the plane gets trucked to the nearest airfield.

## Suggested flight plans

Press **Flight plan** on any offer (it's built automatically when you take a leg). Onward works out:

- **A touring route**, not a straight line. It detours to sights that are worth it (MSFS POIs and photogrammetry cities get the biggest detours, castles and lighthouses smaller ones) within a budget of about 30% extra distance, and adds VORs and NDBs close to the route as checkpoints with their frequencies.
- **A terrain-safe cruise altitude.** It samples the terrain along a 5 nm wide corridor, adds 1,000 ft of clearance (2,000 ft over mountains), and picks the VFR semicircular altitude for your magnetic course.
- **Winds aloft** at that altitude from the Open-Meteo forecast, with magnetic headings, ground speeds and times per leg, including top of climb and top of descent for your aircraft's climb and descent rates.
- **Fuel**: taxi, trip and a 30 minute reserve (45 at night), checked against usable fuel.
- **The departure runway** for the current wind, plus the arrival runway, airfield frequencies and navaids near the route.

**Download .pln for MSFS** gives you the plan for the world map (departure and arrival runways included), and **Printable navlog** opens a kneeboard page you can print or keep on an iPad.

## Before you go: weather and runway checks

Every flight plan starts with **Weather along the route**: a verdict (looks good, flyable with care, or think twice) and the reasons. It checks the hourly forecast at each waypoint for the time you'll be there (cloud base, visibility, rain, thunderstorms, gusts and the freezing level) and the destination TAF for your arrival time. The cloud base is estimated from temperature and dew point, so treat it as a guide.

Below the navlog you'll find the **takeoff and landing distances** over a 50 ft obstacle, worked out from the aircraft's book numbers and corrected for density altitude, wind and grass. Green means plenty of runway, amber means tight, red means it doesn't fit.

## Getting the plan into the sim

Press **Download .pln for MSFS** and load the file in the world map before the flight, or in the EFB with Load from PC. Onward doesn't send plans to the sim by itself: MSFS 2024 only takes part of a plan sent from outside (the waypoints, not the airports or the EFB), so loading it yourself is the reliable way.

## Landing scorecard and stable approach

At 500 ft above the field on the way down, Onward checks whether you're stable: speed within the approach band, sink under 1,000 fpm, wings nearly level, lined up with a runway and, in retractables, gear down. You hear about it either way.

Every landing gets a grade from A to D, based on sink rate, speed against the approach speed, how far past the threshold you touched down, distance from the centerline, bank and crab, with points off for bounces. The debrief shows the breakdown, the journal keeps the grade, and Onward remembers your best score at each airfield. Runway geometry comes from OurAirports, so strips without runway coordinates are scored on sink, speed and bank only.

## Spoken callouts and cockpit mode

Press the speaker button at the top to have Onward read its messages aloud: sights coming up, the next waypoint, the arrival briefing, the 500 ft check, weather warnings and the landing grade. It uses your browser's built-in voice.

**Cockpit** opens a big, simple screen for a tablet, phone or second monitor: the next waypoint with bearing, distance and time, the arrival runway and circuit altitude when you get close, the key instruments, the latest messages and your landing grade. It keeps the screen awake where the browser allows it. When the cockpit screen has its voice on, the main window goes quiet so you don't hear everything twice. On the iPad, open `http://<sim PC's IP address>:8642/cockpit.html`.

## IFR in bad weather

If your aircraft is IFR equipped (SR22, Comanche, Bonanza, 172, 182, PA-28, DA40, Kodiak, Caravan) and the weather is IFR or worse, Onward turns legs into IFR flights. It only picks destinations that are likely to have an instrument approach and are above typical minimums. It also plans an alternate with good weather, and adds alternate fuel and a 45 minute reserve. In a VFR-only aircraft (Cubs, Beaver) it tells you to wait the weather out.

The IFR plan goes navaid to navaid at a terrain-safe altitude (1,000 ft clearance within 4 nm, 2,000 ft over mountains) on IFR semicircular altitudes. For real airways, departures, arrivals and approaches, press **Plan in SimBrief**. It opens SimBrief already filled in (airports, aircraft type, altitude, runways, alternate), and MSFS 2024 can import it.

In flight, Onward checks the destination weather every 10 minutes and warns you if it drops below minimums. It also spots go-arounds. Ending up at your alternate is logged as part of the story, not as a failure.

Free data has no instrument procedures, so "likely to have an approach" is a guess based on airport size, scheduled service and nearby navaids. Check the approaches in the sim before you go. You can turn IFR offers off in Settings.

## Planning your own way

Open the plan on any flight (or on the leg you've taken, before takeoff) and it becomes editable:

- **Route shapes:** Direct, Scenic (Onward's tour of the sights), Low and slow (follows the lowest ground it can find, at the lowest safe altitude) and Navaids (VOR and NDB to VOR and NDB). Each shows its distance and time before you pick it.
- **Edit on the map:** drag any point to move it, drag the small + between two points to bend the route, click the line to add a point, and right-click a point to remove it. You can also drag rows in the waypoint list to reorder them, remove them with ×, or add an airfield, VOR, NDB or sight by name. Clicking a sight on the map gives you "Add to route".
- **What a change costs:** after every edit the plan is recalculated (altitude, winds, times, fuel, weather) and a dark bar shows the difference, like "+6 nm · +4 min · +0.6 gal". Undo and Reset are right there.
- **Detours worth it:** sights near your route that aren't in it yet, with how many miles each would add.
- **Side view:** under the map, the terrain along the route, your altitude, the minimum safe altitude, forecast cloud layers (and a band along the top when the overcast is higher up) and the freezing level. Drag the line up or down to change the cruise altitude. If it clips terrain or cloud, Onward says where and offers ways out: a different altitude, the lower ground, or leaving an hour or two later when the forecast improves.
- **Hazards on the side view:** icing (moist air between 0 and -15 °C), turbulence (wind changing fast with height, gusts near the ground, strong wind over high ground) and storms, each drawn where and at what height the forecast puts them, for the time you'll pass each point. Hover to read them. MSFS live weather can differ from the forecast.

The .pln you download follows your edited route.

## GTN flight plans (TDS GTNXi)

In the plan, **Save for the GTNXi** writes the leg as a Garmin .gfp file to `C:\ProgramData\TDS\GTNXi\FPL` (change the folder in Settings). Airports go in by their code and every other waypoint by its coordinates, so nothing imports as a locked waypoint; the GTN shows them as user waypoints. To load it on the GTN: Flight Plan, Menu, Catalog, Menu, Import. Tick **Save the flight plan for the GTN automatically** in Settings to save one whenever you take a leg (and again if you change its plan). If Windows won't let Onward write there, give your user full control of that folder.

## Plan a trip

The **Plan a trip** tab starts with the map around you: one ring for a comfortable leg in your aircraft and one for a day of flying with a fuel stop. Photogrammetry cities, handcrafted airports and MSFS points of interest show at any zoom; zoom in and every airfield appears too (dark dots are paved, brown are grass or dirt), with the places you've been faded. Click any of them, or anywhere on the map for the nearest airfield, to get a card with **Plan a trip here** or **Fly there now**. Searching by name or code works as before. The trip map has the same layer menu as the Fly map: base maps, radar, satellite clouds, forecast flying conditions, METARs, SIGMETs, winds and the VFR chart.

The side panel shows **your map so far** (airfields, countries, miles, MSFS POIs seen and the nearest country you haven't landed in), **trip ideas** made from where you are (a World Update's sights, a photogrammetry city, a new country, a handcrafted airport, a longer journey and a loop back home; hover one to see it on the map), and your **past trips** to plan again.

Once you've picked a destination, add **via stops**: airfields to land at, or sights the route flies over on the way. Tick **Come back home afterwards** for a round trip. Onward splits the way into legs at airfields worth stopping at, following the route through your sights, marks fuel stops and overnight stops, and groups them into days. Shorter legs and Longer legs re-plan it. When the straight line crosses open water, Onward goes around it, airfield to airfield along the coast.

Once a trip is running, its next leg shows up first in Fly whenever you're at its start, with the live weather of the day. Land at the leg's end and the trip moves on. You can skip a stop or end the trip at any time. If you end up somewhere else, Replan from here picks it up again.

## Arrival briefings, sights and in-flight guidance

- **Arrival briefing:** every flight plan includes one for the destination: field elevation and circuit altitude, the runway that suits the wind (magnetic heading, length, surface, lighting, head and crosswind), how to join from the direction you'll arrive, which side the terrain rises on, water nearby, radio frequencies, sunset time, the latest weather and a few practical tips. In flight it pops up on its own when you're 20 nm out.
- **About each sight:** click any sight (in the lists, the route or on the map) for a short Wikipedia summary and photo.
- **Your past flights:** every leg you fly is drawn on the map as a thin moss-green line along the path you actually flew, with a dot at each airfield. Turn it off with "My past flights" in the layers menu.
- **Guidance:** while flying, the panel shows the next waypoint with magnetic bearing, distance and time, how far off course you are, and what comes after. A dashed line on the map points to it. You also get a heads-up a couple of minutes before each sight, saying which side it'll be on.

## Photos and the World map

Take screenshots as you fly and Onward adds them to the leg on its own. It watches the usual folders: the Xbox Game Bar captures folder (Win+Alt+PrtScn), Pictures\Screenshots (Win+PrtScn), and Steam's MSFS screenshot folder (F12). If you save screenshots somewhere else, add that folder in Settings, where you can also see which folders it found.

Each photo is copied into `data\photos`, placed on the map where you were when you took it, and labeled with what you were near ("over the Golden Gate Bridge", "near Sausalito", "at Half Moon Bay Airport"). They show up in the flight panel, the debrief and the journal entry. Click one to see it full size and use the arrow keys to browse. To remove one, open it and press **Delete photo**: it disappears from the leg, the journal and the World map, and Onward's copy is deleted (the original screenshot stays where the sim saved it).

The **World** tab shows the whole journey: every track you've flown, every airfield, and every photo where you took it, with your totals in the corner.

## Charts on the map

Use the layers button at the top right of the map:

- **FAA VFR sectional** and **FAA IFR low enroute** (United States), as base maps.
- **VFR chart (open flightmaps)**, an overlay with airspace, frequencies and airfields for the European countries open flightmaps covers. Portugal isn't one of them.
- **Airspace & navaids (openAIP)**, a worldwide overlay. It needs a free openAIP API key: create an account at openaip.net, add an API client in your account settings, and paste its token in Onward's Settings.

Weather overlays, in the same layers menu:

- **Weather radar (RainViewer):** the latest precipitation radar, refreshed every 10 minutes.
- **Satellite clouds (live):** the latest satellite picture, every 10 to 15 minutes: Meteosat (EUMETSAT) over Europe, Africa and the Atlantic, GOES over the Americas and Himawari over Asia and the Pacific (NASA GIBS). Only the cloud is shown, blended over your map. A satellite sees cloud from above, so it can't tell low cloud from high.
- **Flying conditions (forecast):** VFR, MVFR, IFR and LIFR shading from the forecast cloud ceiling and visibility, for now and the next six hours (use the slider at the top of the map). Good for spotting where the low cloud and poor visibility will be and planning around it.
- **Airport weather (METAR):** every reporting station in view as a dot colored by flight category (green VFR, blue MVFR, red IFR, purple LIFR), with a short line pointing into the wind. Hover for the wind, ceiling, visibility and the raw METAR. Shown from zoom 5 in.
- **SIGMETs and AIRMETs:** active hazard areas worldwide (red for thunderstorms, blue for icing, orange for turbulence, purple for volcanic ash). Hover for levels and validity, click for the full text.
- **Winds aloft and low cloud:** a grid of wind arrows with speeds at your planned cruise altitude (5,000 ft when no plan is open), plus an estimated low cloud base where there is cloud, from the Open-Meteo forecast.

Onward remembers which overlays you had on.

These are for the sim only, not for real-world navigation.

## Wear, damage and repairs

Each aircraft has four systems (engine, airframe, landing gear, propeller). They wear down from hard landings and bounces, G loads, overspeed, flaps or gear out too fast, overrevving, high cylinder head temperatures, shock cooling, runway excursions, and normal use.

- **Worn** (under 80%): parts can fail for real in the sim (vacuum, electrical, pitot/static, brakes; a badly neglected engine can quit).
- **Damaged** (under 60%): only ferry flights to an airfield with a maintenance shop are offered.
- **Grounded** (under 35%): a field repair is needed before flying.
- **Crash**: the airframe is written off. The journey continues from the nearest airfield once a replacement arrives.

Repairs take real time (adjustable in Settings, or set them to 0).

### A2A aircraft (AccuSim)

Fly an A2A aircraft such as the A2A Comanche and Onward reads its real condition from AccuSim, the same values A2A's tablet shows: every cylinder, spark plug, magneto, filter, pump, tire, brake and control surface, plus failures like a blocked static port or a dead antenna. The four systems in the Hangar are worked out from those parts, and the Hangar lists the ones that need attention.

With AccuSim in charge, Onward doesn't add wear or failures of its own. It still notes hard landings, exceedances and the rest in the journal. Do the maintenance in the A2A tablet: Onward picks up the result by itself. A damaged airplane still only gets ferry flights to a shop, which is a good place to do that work. Onward only reads AccuSim and never changes anything in it.

## MSFS points of interest

Onward includes the community "Microsoft Flight Simulator Map" of MSFS's own points of interest: every World Update and City Update POI, the photogrammetry cities and the handcrafted airports. That's about 2,600 places. They show as amber dots on the map, are favored when routes are picked, and handcrafted airports get a bonus as destinations. Onward checks for an updated copy of the map once a week.

On top of that, it downloads general landmarks from OpenStreetMap, shown as slate-blue dots. It only keeps the ones worth seeing in the sim: anything inside a photogrammetry city (castles, churches, towers and so on, which the scan shows as they really are), and elsewhere only major geographic features that the satellite-based scenery shows well: volcanoes and glaciers, peaks over 2,000 m (or over 1,000 m with a Wikipedia article), and notable gorges, capes, bays and cliffs. The sim's own POIs in amber are always shown.

## The look

Onward uses a "field journal" style: warm paper, serif headlines, small monospaced labels and a dark panel for the numbers that matter (the next waypoint, the before-you-go check). The map is toned down so your routes and photos stand out; pick Satellite in the layers menu for full color. Cockpit mode uses a dark version of the same style for night flying.

## Good to know

- Real-world data doesn't always match the sim. Small strips may be missing or placed differently, runway lighting in MSFS isn't always accurate, and strips without a code the sim knows are marked "no sim code" with their coordinates.
- Some study-level aircraft use their own systems and ignore the sim's failure events.
- Aircraft limits live in `aircraft.json`. Edit them or add your own aircraft.
- Your journey is saved in `data\career.json`. Back it up if you care about it; delete it to start over somewhere new.
