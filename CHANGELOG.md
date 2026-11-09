# What's new

## 0.27.4
- Fixed: plans sent to MSFS 2024 arrived without the departure and destination airports. The flight plan file now uses the MSFS 2024 format, with both airports and their runways.
- Fixed: a waypoint coordinate could be written with 60 seconds instead of rolling over to the next minute.

## 0.27.3
- Strips the sim may not have are marked OFF THE MAP in the list of suggested flights (hover the tag for what it means).
- Fixed: strips with no elevation in the airfield database were treated as sea level, so they showed "elev 0 ft" and the hills around them were called a mountain approach. Onward now takes their height from the terrain.
- "Runway: unknown" now reads "Runway details unknown", with an explanation when you hover it.

## 0.27.2
- The option for strips with no sim code or unknown runway is now called "Off the map", with a line explaining that the sim may show a field, a farm or nothing at all there.

## 0.27.1
- Each suggested flight now shows the key numbers at a glance: airfield code, flying time, runway and length (amber when it's short for your airplane), field elevation, weather category, wind and crosswind, and difficulty.
- If Onward is already running, starting it a second time says so and opens the running one, instead of quietly starting a second copy.
- The page reloads itself when the Onward behind it changes version, so it never shows an old screen after an update.
- Fixed: grass and gravel strips no longer read as "Gre strip" or "Unk strip".

## 0.27.0
- A2A aircraft: Onward reads the real condition from AccuSim (cylinders, spark plugs, magnetos, filters, tires, brakes, flaps and more) instead of keeping its own, and the Hangar shows the parts that need attention.
- Fouled spark plugs are shown on their own and don't count against the engine: a leaned run-up or Clean spark plugs in the tablet clears them.
- With AccuSim, Onward no longer adds its own wear or failures on top of A2A's. Maintenance is done in the A2A tablet, and Onward picks it up.

## 0.26.6
- Fixed: a daytime landing could be written up as a night landing when the flight had been picked hours earlier. Onward now checks the sun at the moment you land, using the sim's own clock.
- A well-flared landing that touches down near the stall no longer loses points or gets a "close to the stall" note: the approach speed is for over the fence, not for touchdown.

## 0.26.5
- Near airports, the side view now takes the low cloud from their latest weather reports (METARs) for the next few hours, like MSFS live weather does, and uses the forecast elsewhere.
- The side view says which airports' reports it used.

## 0.26.4
- Finding flights in a new area is about twice as fast: terrain downloads several map tiles at a time, while Onward looks for landmarks.
- While it searches, Onward says what it's doing (with a count for the terrain) and that the first search in a new area takes longer.
- When the landmark servers are busy, Onward no longer waits for them and uses the landmarks it already has.

## 0.26.3
- The side view now shows scattered low cloud, like afternoon cumulus over the hills, which the forecast's height levels are too coarse to catch.
- Flying at the height of scattered cloud is no longer reported as clear: the side view says you'll pass near or through some.
- Busy OpenStreetMap servers are mentioned in the log every 10 minutes instead of every few minutes.

## 0.26.2
- Fixed: right after loading into the cockpit, Onward could show errors and briefly stop tracking until all the sim's values had arrived.
- The log file no longer shows your Windows user name, so it's safe to share in a bug report.

## 0.26.1
- Onward now lives at github.com/onward-sim/Onward, and updates come from there.
- The demo starts at Half Moon Bay, California.

## 0.26.0
- Far fewer weather requests: terrain now comes from free elevation map tiles (cached on disk), forecasts are cached, the winds grid reuses points when you pan, and a daily budget switches to MET Norway well before Open-Meteo's free limit.
- Send to sim: Onward now waits until you're in the cockpit (MSFS ignores flight plans sent from the main menu) and sends the plan again when you load a new flight.
- Satellite map: now EOxCloudless (Sentinel-2), which is free to use in a free app; the previous satellite map needed a paid license.
- Settings › About and credits lists every data source and the license (AGPL-3.0).
- A Windows app (Onward.exe, no Python needed) with its own icon, built and tested automatically.
- More room for the side panel and pages on large screens.

## 0.25.2
- Fixed the backup forecast on Windows: MET Norway's certificate failed to verify, so the side view still had no weather. Onward now also trusts the certifi certificate bundle (installed automatically by start.bat).

## 0.25.1
- When Open-Meteo refuses (it has a daily request limit) the route forecast now comes from MET Norway instead, so the side view still shows cloud, icing and storms.
- If no forecast is available at all, the side view says so instead of reporting the route as clear.

## 0.25.0
- The Plan a trip map has the same layers as the Fly map: base maps, radar, satellite clouds, forecast flying conditions, METARs, SIGMETs, winds and the VFR chart, with its own legend.
- Trips no longer cross open water in one long leg: when the straight line crosses a bay or a sea, Onward goes around it, airfield to airfield.
- Fixed: a trip idea's route could stay on the map after you started a trip.

## 0.24.3
- Taking a screenshot no longer gets a spoken callout. The photo is still noted in the flight log.

## 0.24.2
- One callout per sight, about a minute before you reach it, instead of a "coming up", a "next waypoint" and a "passing" for each.
- Sights close together, like a city's landmarks, are grouped into one callout.
- A sight listed twice (along the route and as a waypoint) is only announced once.

## 0.24.1
- The side view stays up while you fly: your aircraft and the altitude you've actually flown, over the planned profile, terrain, cloud and hazards (read-only once you're airborne).

## 0.24.0
- Flight plans for the TDS GTNXi: Save for the GTNXi writes a Garmin .gfp to C:\ProgramData\TDS\GTNXi\FPL, with airports by code and every other waypoint by coordinates, so nothing imports locked.
- Optional: save it for the GTN automatically whenever the plan goes to the sim (Settings).

## 0.23.2
- Fixed: if Onward was started while MSFS was still loading, it could stay on "Waiting for Microsoft Flight Simulator" until you restarted it. It now retries and connects by itself.
- A stuck connection attempt no longer keeps a CPU core busy, and old connections are closed properly when Onward reconnects.

## 0.23.1
- Fixed the highlights list in the offer cards, squeezed into a narrow column since 0.23.0.

## 0.23.0
- Plan a trip, rebuilt: range rings for one leg and one day, photogrammetry cities, handcrafted airports and MSFS POIs on the map, and every airfield when you zoom in, all clickable.
- Trip ideas from where you are: a World Update's sights, a photogrammetry city, a new country, a handcrafted airport, a longer journey and a loop back home.
- Via stops: airfields to land at, or sights the route flies over, and a round trip option.
- Your map so far: airfields, countries, miles, MSFS POIs seen, and the nearest country you haven't landed in.
- Past trips are kept, so you can plan one again.

## 0.22.0
- Satellite clouds on the map: the latest Meteosat, GOES or Himawari picture, blended so only the cloud shows.
- Forecast flying conditions: VFR, MVFR, IFR and LIFR shading from the forecast ceiling and visibility, with a slider for the next six hours.
- Radar now draws on top of both, so rain stays visible.

## 0.21.2
- MSFS points of interest show when zoomed further out, and every dot has a light ring so it stands out on the satellite map.

## 0.21.1
- Cloud on the side view now comes from the forecast cloud cover at each height, with base and top, instead of an estimate from temperature and dew point.
- Grey days show up: when the overcast is above the chart, a band along the top says so, with its approximate base.
- Flying on top of a cloud layer is flagged too, not only flying into it.
- The freezing level warning only appears when there's cloud near that height.

## 0.21.0
- Side view hazards: icing, turbulence, mountain waves and storms, drawn where and at what height the forecast puts them.
- Forecasts are read for the time you'll pass each point, every 15 to 20 nm along the route.
- New ways out on the side view: fly below or above the icing, a smoother altitude, or leave later when storms pass.

## 0.20.1
- Updates now come from GitHub.

## 0.20.0
- Updates from inside the app: Onward checks GitHub for a new version and installs it with one click. Your data is never touched, and the previous version is kept so you can go back.
- First-run setup: pick your pilot name and home airfield.
- Your own data can live outside the program folder (the packaged app keeps it in %LOCALAPPDATA%\Onward), and there's a log file (onward.log) to send along when something goes wrong.
- Weather overlays: radar, METAR flight categories, SIGMETs/AIRMETs, winds aloft and low cloud.
- Plan your own route on the map, a side view of terrain and cloud, route shapes and detours.
- Plan a trip: multi-leg journeys with fuel and overnight stops.
