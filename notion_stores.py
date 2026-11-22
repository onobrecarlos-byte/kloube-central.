"""Read the current store roster from the Notion advertising board."""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

DEFAULT_SOURCE = "44f49e70-d938-4b60-b3b2-708a607afab5"
ACTIVE_STATUSES = {
    "Em Teste (7 Dias)",
    "Faturando >100$",
    "Faturando 100$ - 500$/dia",
    "Faturando 500$ - 1.000$/dia",
    "Faturando 1.000$ - 2.000$/dia",
}
_last_snapshot = None


class NotionUnavailable(RuntimeError):
    pass


def enabled():
    return bool(os.environ.get("NOTION_API_KEY", "").strip())


def _domain(value):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = parsed.hostname or ""
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return ""
    return host.lower().rstrip(".")


def _decode(page):
    properties = page["properties"]
    name = "".join(fragment["plain_text"] for fragment in properties["Loja"]["title"]).strip()
    status = (properties["Status"]["select"] or {}).get("name", "")
    return {
        "id": page["id"],
        "name": name,
        "domain": _domain(properties["Domínio"]["url"]),
        "status": status,
        "active": status in ACTIVE_STATUSES,
    }


def _read_snapshot():
    token = os.environ["NOTION_API_KEY"].strip()
    rows = []
    cursor = None
    seen = set()
    try:
        source = UUID(os.environ.get("NOTION_DATA_SOURCE_ID", DEFAULT_SOURCE).strip())
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            request = Request(
                f"https://api.notion.com/v1/data_sources/{source}/query",
                method="POST",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Notion-Version": "2026-03-11"},
            )
            with urlopen(request, timeout=5) as response:
                payload = json.load(response)
            if payload.get("object") != "list":
                raise ValueError("Expected a list")
            request_status = payload.get("request_status") or {}
            if request_status.get("type", "complete") != "complete":
                raise ValueError("Incomplete Notion results")
            rows.extend(_decode(page) for page in payload["results"]
                        if page.get("object") == "page"
                        and not any(page.get(key) for key in ("archived", "is_archived", "in_trash")))
            if not payload["has_more"]:
                break
            cursor = payload["next_cursor"]
            if not cursor or cursor in seen:
                raise ValueError("Invalid Notion cursor")
            seen.add(cursor)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NotionUnavailable("Não foi possível consultar o quadro Anúncios no Notion.") from exc
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "rows": rows}


def snapshot(*, allow_stale=False):
    global _last_snapshot
    if not enabled():
        return None, False
    try:
        fresh = _read_snapshot()
    except NotionUnavailable:
        if allow_stale:
            return _last_snapshot, True
        raise
    _last_snapshot = fresh
    return fresh, False


def monitor_roster(existing, current):
    """Keep saved results, returning only current active hosts as monitor targets."""
    if current is None:
        return existing
    by_host = {_domain(row.get("domain")): row for row in existing}
    targets = {}
    for row in current["rows"]:
        domain = row["domain"]
        if not row["active"] or not row["name"] or not domain or domain in targets:
            continue
        old = by_host.get(domain) or {}
        identity = hashlib.sha256(f"{row['id']}:{domain}".encode()).hexdigest()[:20]
        targets[domain] = {
            "id": f"notion_{identity}", "domain": domain, "url": f"https://{domain}",
            "status": "unknown", "status_label": "Aguardando verificação",
            "checked_at": "", "created_at": current["checked_at"], **old,
            "notion_page_id": row["id"], "name": row["name"],
        }
    return list(targets.values())
