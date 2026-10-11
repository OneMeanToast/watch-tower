"""Hosted Jev client for the official TypeSafe System One API.

POST {base}/v1/systemone
Authorization: Bearer $JEV_API_KEY

There is no batch endpoint. Every question about one item goes in a single
request. Choice options are rotated so jev-1.13's first-option bias does not
stick to the YAML order. Dates and counts are computed here; the model only
sees the result. Manual uploads and private files stay on the local decider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from urllib.parse import urlparse

import httpx

from wt.cost import (
    estimate_tokens,
    input_cost,
    over_budget,
    record_usage,
    resolve_daily_warn,
    resolve_monthly,
    resolve_rate,
)
from wt.decider import (
    DeciderError,
    LexicalDecider,
    _parse_systemone,
    questions_payload,
)
from wt.models import Candidate, Decision, Indicator, Item
from wt.safety import excerpts, fence_untrusted, neutralize, trim_words
from wt.text import sha256_text, utcnow

log = logging.getLogger("wt.jev")

OFFICIAL_BASE = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-1.13.0"
# Reseller hosts. A copy-paste of one of these must not receive the key.
RESELLER_HOSTS = frozenset({"jevtypesafeai.com", "jevmodel.app", "jevdirectory.org"})
# Models page: 32k for state plus the longest question, 64k for the request.
STATE_TOKEN_BUDGET = 32_000
REQUEST_TOKEN_BUDGET = 64_000
CHOICE_OPTION_CAP = 255
SCORE_LEVEL_CAP = 10
FOCUSED_WORDS = 120
RETRY_STATUSES = frozenset({429, 529})
ATTEMPTS = 4
_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


class JevDecider:
    name = "jev"

    def __init__(
        self,
        base_url: str = OFFICIAL_BASE,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        store=None,
        usd_per_mtok: float = 0.042,
        monthly_usd: float = 10.0,
        daily_warn_usd: float = 1.0,
        prompt_version: str = "1",
        local=None,
        fallback: bool = True,
        allow_private: bool = False,
        sleep=None,
        run_id: str | None = None,
    ):
        self.base_url = resolve_base(base_url)
        self.api_key = (api_key or "").strip() or None
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self._transport = transport
        self.store = store
        self.usd_per_mtok = float(usd_per_mtok)
        self.monthly_usd = float(monthly_usd)
        self.daily_warn_usd = float(daily_warn_usd)
        self.prompt_version = str(prompt_version or "1")
        self.local = local if local is not None else LexicalDecider()
        self.fallback = fallback
        self.allow_private = allow_private
        self.sleep = sleep or time.sleep
        self.run_id = run_id or uuid.uuid4().hex
        self._budget_logged = False
        self._daily_logged = False

    def decide_many(self, item: Item, candidates: list[Candidate]) -> dict[str, Decision]:
        if not candidates:
            return {}
        if self._must_stay_local(item):
            return self._local_many(item, candidates, why="local_only")
        if self._budget_blocked():
            return self._local_many(item, candidates, why="budget")
        self._warn_daily()
        try:
            return self._decide_remote(item, candidates)
        except DeciderError as exc:
            if not self.fallback:
                raise
            log.warning("jev failed (%s); using the local decider", exc)
            return self._local_many(item, candidates, why="error")

    def ask(self, state: str, questions: dict) -> dict:
        """One billed System One call. Extraction uses this so the cap still applies."""
        if not self.api_key:
            raise DeciderError("JEV_API_KEY is not set")
        if self._budget_blocked():
            raise DeciderError("jev monthly cap is spent")
        self._warn_daily()
        key = cache_key(
            artifact_hash=sha256_text(state),
            indicator_id="extract",
            prompt_version=self.prompt_version,
            questions=questions,
            model=self.model,
        )
        hit = self._cache_get(key)
        if isinstance(hit, dict) and isinstance(hit.get("payload"), dict):
            self._record(0, 0, 0.0, cached=1, calls=0)
            return hit["payload"]
        payload = self._post(state, questions)
        self._bill(payload, state, questions, cached=0)
        self._cache_put(key, "extract", {"payload": payload})
        return payload

    def same_event(self, item: Item, other_title: str, other_text: str) -> bool:
        if self._must_stay_local(item) or self._budget_blocked():
            return self._local_same_event(item, other_title, other_text)
        state = jev_state(
            item,
            [],
            extra=f"OTHER ITEM TITLE: {other_title}\nOTHER ITEM BODY:\n{other_text}",
        )
        questions = {
            "same-event": {
                "type": "noul",
                "instructions": (
                    "Do these two items describe the same real-world event or announcement, "
                    "even if the wording or outlet differs?"
                ),
            }
        }
        try:
            payload = self._post(state, questions)
        except DeciderError as exc:
            log.warning("jev same-event failed (%s); grouping from the local signals", exc)
            return self._local_same_event(item, other_title, other_text)
        self._bill(payload, state, questions, cached=0)
        answer = (payload.get("answers") or {}).get("same-event") or {}
        if "noul" not in answer:
            raise DeciderError("jev same-event response missing noul")
        return float(answer["noul"]) >= 0.5

    def _decide_remote(self, item: Item, candidates: list[Candidate]) -> dict[str, Decision]:
        terms: list[str] = []
        for cand in candidates:
            terms.extend(cand.matched_terms)
        state = jev_state(item, terms)
        artifact = sha256_text(state)
        pending: list[tuple[Candidate, dict, str]] = []
        cached: dict[str, dict] = {}
        for cand in candidates:
            questions = prepared_questions(cand.indicator, artifact)
            key = cache_key(
                artifact_hash=artifact,
                indicator_id=cand.indicator.id,
                prompt_version=self.prompt_version,
                questions=questions,
                model=self.model,
            )
            hit = self._cache_get(key)
            if hit is None:
                pending.append((cand, questions, key))
            else:
                cached[cand.indicator.id] = hit
        fresh: dict[str, Decision] = {}
        if pending:
            questions: dict = {}
            for _cand, part, _key in pending:
                questions.update(part)
            payload = self._post(state, questions)
            self._bill(payload, state, questions, cached=len(cached))
            fresh = _parse_systemone(payload, [cand for cand, _q, _k in pending], backend="jev")
            for cand, _part, key in pending:
                decision = fresh.get(cand.indicator.id)
                if decision is not None:
                    self._cache_put(key, cand.indicator.id, decision.answers)
        elif cached:
            self._record(0, 0, 0.0, cached=len(cached), calls=0)
        decisions: dict[str, Decision] = {}
        for cand in candidates:
            if cand.indicator.id in fresh:
                decisions[cand.indicator.id] = fresh[cand.indicator.id]
            elif cand.indicator.id in cached:
                decisions[cand.indicator.id] = _decision_from_scoped(
                    cand, cached[cand.indicator.id]
                )
        return decisions

    def _post(self, state: str, questions: dict) -> dict:
        if not self.api_key:
            raise DeciderError("JEV_API_KEY is not set")
        state, questions = fit_request(state, questions)
        body = {"state": state, "model": self.model, "questions": questions}
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        url = systemone_url(self.base_url)
        last_error = "jev request failed"
        for attempt in range(ATTEMPTS):
            response = None
            try:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    response = client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"jev request failed: {exc}"
                if attempt + 1 >= ATTEMPTS:
                    break
                self.sleep(_backoff(attempt, None))
                continue
            if response.status_code < 400:
                try:
                    payload = response.json()
                except json.JSONDecodeError as exc:
                    raise DeciderError("jev returned non-JSON") from exc
                if not isinstance(payload, dict):
                    raise DeciderError("jev returned a non-object")
                return payload
            last_error = f"jev HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code not in RETRY_STATUSES or attempt + 1 >= ATTEMPTS:
                raise DeciderError(last_error)
            self.sleep(_backoff(attempt, response))
        raise DeciderError(last_error)

    def _bill(self, payload: dict, state: str, questions: dict, *, cached: int) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        raw_in = usage.get("input_tokens")
        if isinstance(raw_in, int) and raw_in >= 0:
            input_tokens = raw_in
        else:
            input_tokens = estimate_tokens(state, json.dumps(questions, sort_keys=True))
        raw_out = usage.get("output_tokens")
        output_tokens = int(raw_out) if isinstance(raw_out, int) and raw_out >= 0 else 0
        rate = resolve_rate(self.store, self.usd_per_mtok)
        usd = input_cost(input_tokens, rate)
        self._record(input_tokens, output_tokens, usd, cached=cached, calls=1)

    def _record(self, input_tokens: int, output_tokens: int, usd: float, *, cached: int, calls: int) -> None:
        record_usage(
            self.store,
            run_id=self.run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=usd,
            cached=cached,
            calls=calls,
        )

    def _budget_blocked(self) -> bool:
        cap = resolve_monthly(self.store, self.monthly_usd)
        if cap <= 0 or self.store is None:
            return False
        from wt.cost import summary as cost_summary

        spent = cost_summary(
            self.store,
            rate=resolve_rate(self.store, self.usd_per_mtok),
            monthly=cap,
            daily_warn=resolve_daily_warn(self.store, self.daily_warn_usd),
        )["month_usd"]
        if not over_budget(spent, cap):
            return False
        if not self._budget_logged:
            log.warning("jev monthly cap of $%s is spent; using the local decider", cap)
            self._budget_logged = True
        return True

    def _warn_daily(self) -> None:
        if self._daily_logged or self.store is None:
            return
        from wt.cost import summary as cost_summary

        report = cost_summary(
            self.store,
            rate=resolve_rate(self.store, self.usd_per_mtok),
            monthly=resolve_monthly(self.store, self.monthly_usd),
            daily_warn=resolve_daily_warn(self.store, self.daily_warn_usd),
        )
        if report["daily_warning"]:
            log.warning(
                "jev spend today is $%.4f, at or over the $%s daily warning",
                report["today_usd"],
                report["daily_warn_usd"],
            )
            self._daily_logged = True

    def _must_stay_local(self, item: Item) -> bool:
        if self.allow_private:
            return False
        return item_is_local_only(item)

    def _local_many(self, item: Item, candidates: list[Candidate], *, why: str) -> dict[str, Decision]:
        log.info("skipping jev for %s (%s)", item.id, why)
        return self.local.decide_many(item, candidates)

    def _local_same_event(self, item: Item, other_title: str, other_text: str) -> bool:
        method = getattr(self.local, "same_event", None)
        if method is None:
            return True
        try:
            return bool(method(item, other_title, other_text))
        except DeciderError:
            return True

    def _cache_get(self, key: str) -> dict | None:
        if self.store is None:
            return None
        row = self.store.conn.execute(
            "SELECT answer_json FROM jev_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            parsed = json.loads(row["answer_json"])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _cache_put(self, key: str, indicator_id: str, answers: dict) -> None:
        if self.store is None:
            return
        self.store.conn.execute(
            """
            INSERT INTO jev_cache(key, indicator_id, answer_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              answer_json = excluded.answer_json,
              created_at = excluded.created_at
            """,
            (key, indicator_id, json.dumps(answers), utcnow().isoformat()),
        )
        self.store.conn.commit()


def item_is_local_only(item: Item) -> bool:
    """Manual uploads and private files never go to Jev unless opted in."""
    if item.local_only:
        return True
    url = (item.url or "").strip().lower()
    if url.startswith("file:"):
        return True
    if item.source in {"file", "private"}:
        return True
    return False


def resolve_base(value: str | None) -> str:
    raw = (value or "").strip() or OFFICIAL_BASE
    host = (urlparse(raw).hostname or "").lower()
    if host in RESELLER_HOSTS or any(host.endswith("." + name) for name in RESELLER_HOSTS):
        log.warning("%s is a third-party reseller; using %s", raw, OFFICIAL_BASE)
        return OFFICIAL_BASE
    return raw.rstrip("/")


def systemone_url(base: str) -> str:
    root = resolve_base(base)
    if root.endswith("/v1/systemone"):
        return root
    return root + "/v1/systemone"


def models_url(base: str) -> str:
    root = resolve_base(base)
    if root.endswith("/v1/systemone"):
        root = root[: -len("/v1/systemone")]
    if root.endswith("/v1"):
        return root + "/models"
    return root + "/v1/models"


def probe_jev(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bool, str]:
    """Authenticated GET /v1/models. A missing key is FAIL, not a guess that Jev is down."""
    if not (api_key or "").strip():
        return False, "JEV_API_KEY is not set"
    url = models_url(base_url)
    headers = {"authorization": f"Bearer {api_key.strip()}"}
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"unreachable ({exc.__class__.__name__})"
    if response.status_code == 200:
        return True, f"GET {url} listed models"
    if response.status_code == 401:
        return False, f"HTTP 401 from {url} (key rejected)"
    return False, f"HTTP {response.status_code} from {url}"


def cache_key(
    *,
    artifact_hash: str,
    indicator_id: str,
    prompt_version: str,
    questions: dict,
    model: str,
) -> str:
    material = json.dumps(
        {
            "v": prompt_version,
            "indicator": indicator_id,
            "artifact": artifact_hash,
            "questions": questions,
            "model": model,
        },
        sort_keys=True,
    )
    return sha256_text(material)


def prepared_questions(indicator: Indicator, artifact_hash: str) -> dict:
    payload = questions_payload(indicator)
    rotated = {}
    for qid, question in payload.items():
        entry = dict(question)
        criteria = entry.get("criteria")
        if entry.get("type") == "choice" and isinstance(criteria, dict):
            entry["criteria"] = rotate_choice(criteria, f"{artifact_hash}:{qid}")
        elif entry.get("type") == "score" and isinstance(criteria, list) and len(criteria) > SCORE_LEVEL_CAP:
            log.warning("score %s has %s levels; sending the first %s", qid, len(criteria), SCORE_LEVEL_CAP)
            entry["criteria"] = criteria[:SCORE_LEVEL_CAP]
        rotated[qid] = entry
    return rotated


def rotate_choice(criteria: dict, salt: str) -> dict:
    """Move the first option. jev-1.13 leans toward whichever option is listed first."""
    keys = [str(key) for key in list(criteria)[:CHOICE_OPTION_CAP]]
    if len(criteria) > CHOICE_OPTION_CAP:
        log.warning("choice has %s options; sending %s", len(criteria), CHOICE_OPTION_CAP)
    if len(keys) < 2:
        return {key: criteria[key] for key in keys}
    shift = int(hashlib.sha256(salt.encode("utf-8")).hexdigest(), 16) % (len(keys) - 1) + 1
    order = keys[shift:] + keys[:shift]
    return {key: criteria[key] for key in order}


def code_facts(item: Item, terms: list[str], extra: str = "") -> str:
    """Dates and counts Jev 1.13 compares poorly. The arithmetic stays in this process."""
    visible = neutralize(f"{item.title}\n{item.text}\n{extra}")
    lines = [
        "CODE FACTS (computed locally; do not recompute dates or counts):",
        f"item_timestamp={item.ts.isoformat()}",
    ]
    seen: set[str] = set()
    for match in _DATE.finditer(visible):
        iso = match.group(0)
        if iso in seen:
            continue
        seen.add(iso)
        relation = _date_relation(iso, item)
        lines.append(f"{iso} is {relation} the item timestamp")
        if len(seen) >= 5:
            break
    counted = 0
    hay = visible.lower()
    for term in terms:
        needle = (term or "").strip().lower()
        if not needle or needle.startswith("/"):
            continue
        lines.append(f"count[{term}]={hay.count(needle)}")
        counted += 1
        if counted >= 8:
            break
    return "\n".join(lines)


def _date_relation(iso: str, item: Item) -> str:
    year, month, day = (int(part) for part in iso.split("-"))
    try:
        found = item.ts.replace(year=year, month=month, day=day)
    except ValueError:
        return "not a real calendar day, so it was not ordered"
    item_day = item.ts.date()
    found_day = found.date()
    if found_day < item_day:
        return "before"
    if found_day > item_day:
        return "after"
    return "the same calendar day as"


def jev_state(item: Item, terms: list[str], extra: str = "") -> str:
    """Focused chunks plus a spotlight fence. Long irrelevant text stays out."""
    snippets = excerpts(f"{item.title}\n{item.text}\n{extra}", terms)
    if snippets:
        body = "\n".join(snippets)
    else:
        body = trim_words(item.text, FOCUSED_WORDS)
    if extra:
        body = f"{body}\n{trim_words(neutralize(extra), FOCUSED_WORDS)}".strip()
    fenced = fence_untrusted(
        item.title,
        body,
        item.url,
        snippets=snippets,
        word_budget=FOCUSED_WORDS,
    )
    return code_facts(item, terms, extra) + "\n" + fenced


def fit_request(state: str, questions: dict) -> tuple[str, dict]:
    """Stay inside the published 32k / 64k token budgets. Estimate is len//4."""
    question_text = json.dumps(questions, ensure_ascii=False)
    longest = max((len(json.dumps(q, ensure_ascii=False)) for q in questions.values()), default=0)
    state_budget_chars = max(256, (STATE_TOKEN_BUDGET * 4) - longest)
    request_budget_chars = REQUEST_TOKEN_BUDGET * 4
    if len(state) > state_budget_chars:
        state = state[:state_budget_chars] + "\n[truncated by watch-tower]"
    while len(state) + len(question_text) > request_budget_chars and len(state) > 256:
        state = state[: int(len(state) * 0.8)] + "\n[truncated by watch-tower]"
    return state, questions


def _decision_from_scoped(cand: Candidate, scoped: dict) -> Decision:
    payload = {
        "answers": {f"{cand.indicator.id}::{key}": value for key, value in scoped.items()}
    }
    return _parse_systemone(payload, [cand], backend="jev")[cand.indicator.id]


def _backoff(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                return min(max(float(raw), 0.0), 8.0)
            except ValueError:
                pass
    return 0.4 * (2**attempt)
