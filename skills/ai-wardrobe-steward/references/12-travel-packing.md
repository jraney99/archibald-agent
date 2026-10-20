## Travel Packing

A capsule wardrobe, not a full outfit per day — the point of packing light is maximizing outfit combinations from the fewest pieces, which means prioritizing versatile items over ones that only work for one occasion.

1. **Gather destination, season/weather, trip length, and which occasion tiers the trip actually needs** (e.g. "3 business days + 1 casual dinner"). No calendar/travel-itinerary hook is wired up yet, so ask directly rather than guessing.
2. **Get the combined real candidate pool across every occasion needed:**
   ```
   python3 scripts/travel_packing.py --occasions <tier1,tier2,...> --season <season> [--days N]
   ```
   Each returned item lists which occasion(s) it covers in `matches_occasions`. If it returns warnings, say so plainly rather than inventing something to fill the gap.
3. **Build the capsule from that pool, prioritizing items whose `matches_occasions` covers the most requested occasions** — those are what let a small number of packed pieces produce many different outfits across the trip. Use the style profile to break ties among equally-versatile options.
4. **Present it as two things**: a packing checklist and a handful of example outfit combinations across the trip's days, composed the same way as the Outfit Engine (including the layering rule) — not just a flat item list.
5. **Packing does not change `lifecycle_state`.** `packed-away` means seasonal storage, not travel — a packed suitcase's contents stay `active`. If an outfit actually gets worn on the trip, log it the normal way.
