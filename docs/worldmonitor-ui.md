# World Monitor, as a UI note

World Monitor (`koala73/worldmonitor`, AGPL-3.0) is a situational-awareness dashboard. Watch Tower does not copy its code and does not ship a UI. The feed **names and URLs** are data in `config/feeds/worldmonitor.yaml`, and that catalog is off unless `worldmonitor.enabled` is set.

What is worth borrowing later, if a UI is built:

- **One digest, many feeds.** A server-side digest groups items by variant (`full`, `tech`, `finance`, `happy`, `commodity`) and by category (politics, US, Europe, tech, intel, and the rest). The screen is a briefing, not a pile of raw feeds.
- **Variants are audiences.** The same pipeline can emphasize world news, technology, markets, or a lighter set without a second app.
- **Publisher first, relay second.** Items are fetched from the publisher when that works, with a relay when a feed blocks datacenter traffic. Failures stay on that feed.
- **Language stays on the feed.** A French source is not mixed into an English briefing unless it is marked as a strategic default.
- **Items carry place and severity.** A map and an alert flag sit on the item. The list can filter to "what changed" without re-reading every headline.
- **Scheduling is not ranking.** Some feeds are fetched earlier on a cold start. That order is not a score and should not be shown as importance.

Watch Tower's equivalent of the briefing today is `wt digest` plus push alerts. A future UI could show the digest by source category, the explanation already stored on each match, and thumbs up/down. It should keep treating article text as untrusted data.
