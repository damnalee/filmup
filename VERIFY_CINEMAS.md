# Cinema data — what to verify

Researched in October 2026 from public listings, news and OpenStreetMap. Cinemas in Pakistan open, close and rebrand often, so **phone each one before launch** and update `src/data/cinemas.json`.

`"verified": true` means all three of these hold:

1. a 2026 listing names the cinema as operating;
2. it is not on the March 2025 Express Tribune list of closed Lahore cinemas;
3. its coordinates come from an OpenStreetMap cinema or mall feature at the stated address (checked with Nominatim, and all inside Lahore).

Phone numbers and screen counts come from blog listings or official sites and are less reliable than locations.

## Included but uncertain (`"verified": false`)

| Cinema | What's uncertain | How to check |
| --- | --- | --- |
| **Chalo Cinema**, Fortress Square | Older sources (Wikipedia, Graana) list a 3-screen **Cinepax** here. 2022–2026 sources list **Chalo Cinema**. It probably replaced Cinepax, but I couldn't confirm that. No phone number found. Tribune (Mar 2025) also reported screens there as temporarily shut. | Visit or call Fortress Square management |
| **CUE Cinemas**, Boulevard 57 Gulberg | The building isn't in OpenStreetMap, so the coordinates (31.5192, 74.3463) are an **estimate** on Main Boulevard Gulberg. The cinema itself is well documented. | Drop a pin on Boulevard 57 in Google Maps and update lat/lng |
| **DHA Cinema**, Phase 2 | Single screen run by DHA. Location is from OpenStreetMap. Cinema Treasures marks it "open" but is undated. It closed in 2011 and later reopened. No phone number found. | Call DHA Phase 2 administration |

## Included, with notes

- **Cinepax Packages Mall** and **Cinepax Amanah Mall**: the Tribune (Mar 2025) said some screens at both were *temporarily* shut. Both are listed as operating in July 2026. Packages Mall has 10 screens (from its 2017 launch); the Amanah screen count is unknown.
- **CineStar Township**: formerly CineStar IMAX. The IMAX licence wasn't renewed in 2024, so it now runs regular screens.
- **Raiha CineGold Plex**: the address is Sector F, but the OSM cinema node sits near the Winter Land entrance. I used the OSM node.

## Excluded — reported closed

The Express Tribune, *"Pakistani cinemas shutting down at an alarming rate"* (March 2025), lists these Lahore cinemas as having halted operations:

- CineStar – ATC (Ali Trade Centre, Gulberg)
- PAF Cinema (Lahore Cantt)
- Imperial Cinemas (Imperial Mall, Paragon City)
- Super Cinema – Royal Palm
- Super Cinema – Vogue Tower
- Bahria CineGold Orchard (probably the venue other sites call "The Arena, Bahria Orchard")
- Cinepax Lake City
- Sozo World
- Luxus Grand
- Shabistan, Prince, Plaza and Gulistan (single screens)

## Excluded — couldn't confirm they exist or operate

- **CineStar Xinhua Mall**: named by one undated listing only.
- **Regal, Capital, Odeon and Ratan** (single screens around Mall Road, Abbott Road and McLeod Road): only old or undated mentions. If any are still running, add them.

## Sources

- Express Tribune, closures report (Mar 2025): https://tribune.com.pk/story/2534873/pakistani-cinemas-shutting-down-at-an-alarming-rate
- Eastern Housing, *Best Cinema in Lahore* (Jul 2026): https://easternhousing.com.pk/best-cinema-in-lahore/
- Graana, *Best Cinemas in Lahore* (updated Mar 2024; phone numbers, addresses): https://www.graana.com/blog/best-cinemas-in-lahore/
- Zameen, *Best Cinemas in Lahore* (2022): https://www.zameen.com/blog/best-cinemas-lahore.html
- CineStar DHA Raya (official): https://cinestar.pk/Browsing/Cinemas/Details/0002
- CineStar (Pakistan), Wikipedia: https://en.wikipedia.org/wiki/CineStar_(Pakistan)
- Universal Cinemas (official): https://universalcinemas.com/browsing/Cinemas/Details/8888
- Raiha CineGold Plex, Cinema Treasures: https://www.cinematreasures.org/theaters/62449
- DHA Cinema, Wikipedia: https://en.wikipedia.org/wiki/DHA_Cinema
- Visit Lahore, *Top Cinemas in Lahore*: https://visitlahore.com/top-cinemas-in-lahore/
- Coordinates: OpenStreetMap via Nominatim (https://nominatim.openstreetmap.org)
