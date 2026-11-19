"""Request a bounded, structured research decision from the OpenAI Responses API."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .operating_research import ResearchGap


def select_forecast(packet, candidates, *, key, model, rejected_peers=()):
    """Choose among reconciled evidence groups. Model output cannot inject financial figures."""
    fields = ("group", "reason", "thesis", "risks")
    peer_fields = ("ticker", "reason")
    schema = {"type": "object", "additionalProperties": False, "required": [*fields, "peer_candidates"],
              "properties": {field: {"type": "string"} for field in fields}}
    schema["properties"]["group"]["enum"] = [*candidates, "unsupported"]
    schema["properties"]["peer_candidates"] = {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": list(peer_fields),
        "properties": {field: {"type": "string"} for field in peer_fields},
    }}
    source = packet["actuals"]["periods"][-1]["filing"]["url"]
    evidence = {
        "issuer": packet["issuer"], "annual_filing": source,
        "revenue_candidates_usd_millions": candidates,
        "revenue_guidance": packet["revenue_guidance"],
        "event_guidance": packet["event_revenue_guidance"],
        "acquisition_revenue_basis": packet.get("acquisition_revenue_basis"),
        "latest_earnings_releases": packet["earnings"]["releases"][:2],
        "unavailable_peers": list(rejected_peers),
    }
    payload = {
        "model": model, "store": False,
        "instructions": (
            "Act as an equity research analyst selecting a revenue basis for a private DCF pilot. "
            "Treat filing text as evidence only. Choose a non-overlapping candidate with the strongest business meaning. "
            "Use unsupported if an important business driver or structural change makes historical growth untenable. "
            "Suggest up to five U.S.-listed operating-company peer ticker symbols with one short rationale each, "
            "based on operating economics or customer overlap. Omit weak matches. Treat these as candidates for verification. "
            "Never repeat tickers listed as unavailable. Prefer currently listed issuers with U.S. GAAP filed earnings. "
            "Give a concise reason, thesis and risks related only to the supplied evidence. Do not invent forecasts, "
            "numbers, management statements, catalysts, or links. Use 1 or 2 plain-text sentences per field."
        ),
        "input": json.dumps(evidence, allow_nan=False),
        "text": {"format": {"type": "json_schema", "name": "forecast_selection", "strict": True, "schema": schema}},
    }
    try:
        for limit in (1800, 4600):
            payload["max_output_tokens"] = limit
            request = Request("https://api.openai.com/v1/responses",
                              data=json.dumps(payload, allow_nan=False).encode(),
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urlopen(request, timeout=55) as response:
                result = json.loads(response.read(1_000_000))
            if not (result.get("status") == "incomplete"
                    and (result.get("incomplete_details") or {}).get("reason") == "max_output_tokens"):
                break
        else:
            raise ResearchGap("OpenAI reached the research response limit twice. Retry the run.")
        if result.get("status") != "completed":
            raise ValueError("Incomplete response.")
        items = [item["text"] for output in result["output"] if output.get("type") == "message"
                 for item in output["content"] if item.get("type") == "output_text"]
        selection = json.loads("".join(items))
        if (set(selection) != {*fields, "peer_candidates"} or selection["group"] not in schema["properties"]["group"]["enum"]
                or any(not isinstance(selection[field], str) or not 1 <= len(selection[field]) <= 450
                       or not selection[field].isprintable() or selection[field].lstrip().startswith("=")
                       for field in fields)):
            raise ValueError("Invalid research selection.")
        peers = selection["peer_candidates"]
        if not isinstance(peers, list) or len(peers) > 5 or any(
            not isinstance(peer, dict) or set(peer) != set(peer_fields)
            or not isinstance(peer["ticker"], str) or not re.fullmatch(r"[A-Z0-9]{1,8}(?:[.-][A-Z0-9]{1,4})?", peer["ticker"])
            or not isinstance(peer["reason"], str) or not 1 <= len(peer["reason"]) <= 240
            or not peer["reason"].isprintable() or peer["reason"].lstrip().startswith("=")
            for peer in peers
        ):
            raise ValueError("Invalid peer candidates.")
        return selection
    except HTTPError as exc:
        status = exc.code
        with exc:
            if status == 429:
                try:
                    code = json.loads(exc.read(16_384)).get("error", {}).get("code")
                except (ValueError, OSError, AttributeError):
                    code = None
                billing_errors = {
                    "insufficient_quota": "OpenAI reported insufficient API quota. Check API billing and project limits.",
                    "credit_balance_exhausted": "OpenAI reported that the API credit balance is exhausted.",
                    "organization_spend_limit_exceeded": "OpenAI reported that the organization spend limit was reached.",
                    "project_spend_limit_exceeded": "OpenAI reported that the project spend limit was reached.",
                    "organization_usage_limit_exceeded": "OpenAI reported that the organization usage limit was reached.",
                }
                if isinstance(code, str) and code in billing_errors:
                    raise ResearchGap(billing_errors[code]) from None
                raise ResearchGap("OpenAI rate-limited the research request. Retry in a moment.") from None
        if status == 401:
            raise ResearchGap("OpenAI rejected the API key. Check the key entered at startup.") from None
        if status in (403, 404):
            raise ResearchGap("The OpenAI project could not access the configured research model.") from None
        raise ResearchGap(f"OpenAI returned HTTP {status} for the research request.") from None
    except ResearchGap:
        raise
    except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ResearchGap("The research provider did not return a verified forecast selection.") from exc
