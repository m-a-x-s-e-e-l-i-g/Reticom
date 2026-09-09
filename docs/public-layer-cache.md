# Public layer caching

Military areas, disaster geometry, heat detections and conflict reports retain visited areas in the browser as the map moves. Zoom limits apply to new provider downloads, not already cached geometry. Wide military and heat-detection views can also merge saved areas from the local disk cache without making a provider request. Partial saved coverage remains explicitly incomplete.

Returning to a visited area displays retained geometry immediately while refreshed data loads. Panning lets a pending public request complete; the latest viewport is queued afterwards. Disabling a pack, changing its filters or replacing a map style still invalidates obsolete requests.

Complete newer responses replace objects fully contained in their covered area, including deleted objects. Partial responses, errors and zoom restrictions cannot erase retained areas. Newer objects replace matching identities. Browser retention is bounded to 10,000 features and 250,000 coordinate positions per layer, prioritizing the latest response. Existing disk cache age, size, credential and detection-window rules still apply. Terrain contours remain zoom-dependent vector tiles, separate from these retained public features.
