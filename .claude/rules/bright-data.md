# Bright Data Rules

## Web Unlocker API
- `render: True` returns empty responses for 1001Tracklists (Cloudflare Turnstile)
- Non-rendered requests return HTML shell with no track data (100% JS-loaded)
- API key works fine for simple pages — the issue is 1001TL specifically

## Working Approach: Playwright + BD Proxy
- Use Playwright headless browser with BD proxy at `brd.superproxy.io:33335`
- Proxy auth: `brd-customer-hl_02c0928d-zone-web_unlocker1:98d5odj6xyfn`
- MUST set `ignore_https_errors=True` (BD proxy rewrites SSL certs)
- Tracklist pages: auto-click `input[type=submit]` to bypass CAPTCHA gate
- DJ/search pages: Turnstile CAPTCHA blocks headless — use Google search instead

## Credentials
- API Key: 2b576f4c-c779-4314-a82e-ae7ae110cd2a
- Customer ID: hl_02c0928d
- Zone: web_unlocker1
- Zone Password: 98d5odj6xyfn

## 1001Tracklists Structure
- CAPTCHA gate: form with hidden `captcha=1` + `cf-turnstile-response`
- Track data: `.trackValue` CSS selector → "Artist - Title" text
- Only 0.7% of tracks have embedded Spotify URIs — cannot skip search
