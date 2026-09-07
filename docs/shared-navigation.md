# Gedeelde navigatie

## Gebruik

1. Tik op een waypoint of operator en start een directe lijn of berekende route.
2. Kies **SHARE WITH TEAM** zodra een positie/route beschikbaar is. Een mislukte routeberekening wordt niet als een berekende route gedeeld.
3. **Ops → Shared routes** toont de plannen. Tik op een route om deze op de kaart te bekijken, inclusief de richtingen. **FOLLOW & SHARE** volgt dezelfde geometrie en maakt jouw navigatie zichtbaar voor het team.
4. Command ziet routes in de zijbalk, op de kaart en via een operator-popup. All Teams houdt routes gescheiden per team.

Een volger krijgt wijzigingen van de oorspronkelijke deler. Als die de route beëindigt of vervangt, eindigt het volgen; een nieuwe route wordt niet zonder keuze geactiveerd. **STOP SHARING** maakt navigatie privé. **END** stopt navigatie en publiceert een stop. Een gesloten of gesuspendeerde WebView verwijdert een gedeeld plan niet: open het in Ops om te hervatten of het delen te stoppen.

Routes zijn gestippelde plannen met eigenaar, bestemming en tijdstip. De GPS-positie en bewegingstrace zijn afzonderlijke gegevens. De app claimt geen live voortgang of gesproken turn-by-turn navigatie wanneer alleen een route beschikbaar is.

## Netwerk en privacy

- Navigatie is standaard privé; delen is expliciet. Volgen deelt jouw gekozen plan, niet een nieuwe persoonlijke berekening.
- `navigation.updated` en `navigation.stopped` zijn ondertekende, teamgebonden events over geauthenticeerde, versleutelde Reticulum Links. De afzender komt uit de geverifieerde envelope, nooit uit een requestveld.
- Eén huidig plan per afzender. Revisions zijn oplopende, persistente tellers, onafhankelijk van telefoonklokken. Een stop blijft als tombstone bewaard zodat oude events of oudere snapshots de route niet herstellen.
- De bestaande duurzame Field-outbox bundelt opeenvolgende updates tot de nieuwste gewenste toestand. **Queued** betekent nog niet afgeleverd. Mission sync bewaart de nieuwste navigatiestatus buiten de beperkte chatgeschiedenis.
- Een community transportnode vervangt de teamhost niet. Delen volgt de bestaande teamhost/handover/synchronisatiepaden.
- De bestaande online routeberekening gebruikt OSRM en stuurt daarvoor begin- en eindcoördinaten naar de routingdienst. Gedeelde routes bekijken/volgen doet geen nieuwe OSRM-aanvraag. Offline kan bestaande routegeometrie gebruikt worden; nieuwe offline routeberekening is niet toegevoegd.

## Ontwikkeling

`GET /api/navigation` retourneert `{plans, sender_hash}`. Plannen bevatten `active`, `route_id`, `revision`, `callsign`, `network` en voor actieve plannen uitgepakte `[longitude, latitude]`-coördinaten.

`PUT /api/navigation` accepteert `label`, `mode` (`direct`/`route`), `origin`, `target`, `target_kind`, `target_id`, `coordinates`, `distance_m`, `duration_s`, optionele `following: {sender_hash, route_id}` en `directions: [{text, distance_m}]`. Route-ID/revision worden door de lokale server bepaald. `DELETE /api/navigation` stopt uitsluitend het eigen plan.

Geometrie wordt op vijf decimalen gecodeerd als polyline, zonder punten weg te laten. Grenzen: 4.000 punten, 12 KB voor het event en maximaal 64 richtingen / 4 KB. Een te grote route wordt expliciet geweigerd, niet stilzwijgend vereenvoudigd. API-writes worden geserialiseerd zodat een vertraagde update niet achter een stop terechtkomt; de UI houdt ook rekening met een gewijzigde deelkeuze tijdens lopende requests.

Gerichte tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_navigation.py
node --test tests/shared-navigation-model.test.mjs tests/shared-navigation-ui.test.mjs
$env:RETICOM_NETWORK_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -s tests/test_navigation_network.py
```

De netwerktest gebruikt geïsoleerde echte Reticulum-processen: 1.000 routepunten plus richtingen, een late deelnemer na 110 nieuwere berichten, afzenderisolatie, herstart met wachtrij en aflevering van een stop nadat de host terugkomt. Testdata raakt geen gebruikersteam of communitynode.
