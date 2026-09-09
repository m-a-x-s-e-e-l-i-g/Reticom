# Aircraft and ship tracks

Click an aircraft or vessel to center its marker at the current zoom and load its available travelled track. Loading history and further position updates preserve the camera; the map never automatically fits the whole route. Closing the popup or selecting another target clears the previous line.

Manually zooming below the live feed's regional limits keeps the last accepted regional subscription active. It does not request worldwide traffic. The selection survives viewport changes, empty responses and normal cache expiry; old reports fade and show their last-seen age.

Traffic observations merge by identity and report time across map regions, including empty or partial responses. Older responses cannot rewind a target. Browser caches retain up to 5,000 targets per traffic layer for 30 minutes; the selected target is pinned separately. The backend shares a bounded 30-minute regional observation cache. Moving a map replaces its previous aircraft request region, while concurrent maps still share the provider budget fairly. In-flight traffic requests finish into the cache rather than restarting on every pan. Cached display coordinates are never written into observed history.

Aircraft selections request the provider's full trace file and show available positions from the last 24 hours. This can reveal the path before the map was opened. ADSB.lol history is cached briefly, validated against the aircraft identity, and merged with locally observed fixes. The trace feed can have gaps and is not a guaranteed complete flight itinerary.

Vessel selections show AIS positions recorded locally during the last 24 hours. History persists across browser refreshes and application restarts. AISStream does not durably replay earlier reports, so a newly opened area cannot show an entire previous voyage. Recording occurs while the enabled live layer is receiving reports; it does not create a worldwide background subscription.

The popup labels the available coverage. Reception gaps, impossible jumps and separate flight legs stay disconnected. Date-line crossings render without a line across the world. Only observed positions enter history; animated estimates do not. Local records are sampled at most once per target every 30 seconds and bounded in time and storage.

These lines show past observations, not planned routes to an airport or port. Public traffic remains separate from signed team reports.

Sources: https://www.adsb.lol/docs/open-data/historical/ and https://aisstream.io/documentation .
