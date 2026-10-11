# Rip to Watch Tower

An agent cannot reliably export an Apple `.shortcut` file. Build this once on the Mac in the Shortcuts app, then it appears in the share sheet on that Mac and, if you turn on iCloud, on the iPhone.

The desk is `http://127.0.0.1:47221`. Do not use port 8787. That port is only the old thumbs endpoint.

## Token

On the Mac, in the Watch Tower checkout:

```bash
wt rip-token
```

That prints a bearer token and stores it in `data/rip.token`. The Shortcut sends it as `Authorization: Bearer …`. A GET, and a POST without this token or the page's CSRF token, does nothing.

## Shortcut

1. Open Shortcuts → New Shortcut. Name it **Rip to Watch Tower**.
2. Click the shortcut's settings (i) and turn on **Use as Quick Action** and **Show in Share Sheet**. Accept **URLs**, **Safari web pages**, and **Text**.
3. Add **Receive** input of those types.
4. Add **Get URLs from Input**. If that returns nothing, add **Get Text from Input** and leave it as the URL the share sheet handed you.
5. Add **Choose from Menu** with two items: **Rip now** and **Subscribe**.
6. Add **Get Network Details** (or **Get Wi-Fi Network's Network Name**) if you want the home-network branch. Otherwise use the LAN branch whenever the phone is on the same Wi-Fi as the Mac.
7. **If** the current Wi-Fi name is your home network:
   - **Get Contents of URL**
   - URL: `http://<mac-lan-name>.local:47221/api/rip`
   - Method: POST
   - Headers: `Authorization` = `Bearer <token from wt rip-token>`, `Content-Type` = `application/json`
   - Request body (JSON): `{"url":"<Shortcut Input>","mode":"rip","note":""}`
   - For the Subscribe menu item, set `"mode":"subscribe"`.
   - The Mac must be running `wt desk`. Add `--lan` only if you want the API itself on the LAN. The desk UI already listens on the LAN, and `/api/rip` is proxied to the local API.
8. **Otherwise** (cellular, or a coffee-shop network):
   - **Get Contents of URL**
   - URL: `https://ntfy.sh/<your-private-topic>`
   - Method: POST
   - Body: the single URL, or the URL plus the word `subscribe`
   - On the Mac, `wt inbox` listens with `WT_NTFY_INBOX=https://ntfy.sh/<your-private-topic>/json`
   - A message with no URL, or with more than one URL, is ignored.
9. Add **Show Notification** with the response title or "Sent to Watch Tower".

Use a long, unguessable ntfy topic. Do not reuse the alert topic unless you want rips and alerts in the same stream.

## What the request does

`POST /api/rip` with the bearer token rips or subscribes. It does not bypass a paywall. If the page is thin, the rip is stored as a stub only when there is enough text; otherwise paste it on the rip desk. Subscriptions that `robots.txt` disallows are refused.
