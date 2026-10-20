## Web Lookups
`web_search` (Perplexity) is available. If it ever errors with `missing_perplexity_api_key` or similar, do NOT tell the user "I can't search the web" and stop — fall back to the relevant skill (e.g. `flight-search` for flights), the `browser` tool for a specific page, or `web_fetch` for a known URL, and flag the broken key.
