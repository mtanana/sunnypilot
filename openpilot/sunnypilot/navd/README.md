# Navigation

Turn-by-turn guidance from Mapbox: routing, geocoding, and the instructions the UI, soundd
and the navigation desires consume. It is a driving aid, never a source of autonomy. The
user guide, from install to the first route, is the branch's post on the sunnypilot
community forum.

- `navigation_helpers/`: Mapbox API integration and instruction processing.
- `navigationd`: requests a route, keeps it current, and publishes the `navigationd`
  message at 3 Hz (the localizer feeds it at 20 Hz; a Ratekeeper holds the loop at 3).
- `destinationd`: the phone page and its HTTP API on the car's network.
