# -*- coding: utf-8 -*-
import calendar
import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
import uuid
import unicodedata
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

BRT_TZ = timezone(timedelta(hours=-3))
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_FILE = BASE_DIR / "blocks_data.json"
DOMAINS_DATA_FILE = BASE_DIR / "data.json"
PORT = int(os.environ.get("KLOUBE_BLOCKS_PORT", "8766"))

MONTH_NAMES_PT = {
    1: "JANEIRO",
    2: "FEVEREIRO",
    3: "MARÇO",
    4: "ABRIL",
    5: "MAIO",
    6: "JUNHO",
    7: "JULHO",
    8: "AGOSTO",
    9: "SETEMBRO",
    10: "OUTUBRO",
    11: "NOVEMBRO",
    12: "DEZEMBRO",
}


def canonical_domain_url(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "—":return ""
    parsed = urllib.parse.urlparse(value if "://" in value else "https://" + value)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    return "https://" + hostname if hostname else ""


def now_iso() -> str:
    return datetime.now(BRT_TZ).strftime("%Y-%m-%d %H:%M:%S")


STORE_IDENTITY_CUTOFF = "2026-11"


def normalize_store_name(s_name: str, month_key: str = STORE_IDENTITY_CUTOFF) -> str:
    clean = (s_name or "").strip()
    if month_key and month_key < STORE_IDENTITY_CUTOFF:
        clean = re.sub(r"\s*(?:[-–—#]\s*|\s+)\d+\s*$", "", clean).strip()
    low = clean.lower()
    if low in ("paro pet / pt pet", "paro pet", "pt pet", "paropet"):
        return "ParoPet"
    if low in ("ionix labs", "ionix"):
        return "Ionix Labs"
    if low in ("breeze wear",):
        return "Breeze Wear"
    if low in ("long well", "longwell"):
        return "LongWell"
    if low in ("krakotai", "krakatoa"):
        return "Krakatoa"
    if low in ("dr dent", "dr. dent", "drdent", "dr.dent", "dr-dent"):
        return "Dr. Dent"
    return clean


def record_store_name(record):
    month = record.get("month_key") or record.get("created_at", "")[:7]
    return normalize_store_name(record.get("store_name", ""), month or STORE_IDENTITY_CUTOFF)


def update_catalog_from_event(data: dict, event: dict):
    """Keep operational status in sync with publications and store closures."""
    event_type = event.get("event_type")
    if event_type not in ("store_published", "store_reactivated", "store_inactive"):
        return
    name = record_store_name(event)
    if not name:
        return
    catalog = data.setdefault("stores_catalog", {})
    key = next((key for key in catalog
                if _name_words(normalize_store_name(key)) == _name_words(name)), name)
    entry = catalog.setdefault(key, {
        "name": name, "current_domain": "", "domain_swaps_count": 0,
        "domain_history": [], "publications": [], "suspensions": [],
    })
    timestamp = event.get("created_at", "")
    if timestamp < entry.get("status_verified_at", ""):
        return
    entry["status"] = "Inativa" if event_type == "store_inactive" else "Ativa"
    entry["status_verified_at"] = timestamp
    entry["status_source"] = event.get("source") or "manual"
    if event.get("domain") and event["domain"] != "—":
        entry["current_domain"] = event["domain"]


def reconcile_catalog(data: dict) -> dict:
    """Recover lifecycle events saved before catalog synchronization existed."""
    baseline = data.get("operational_snapshot", {}).get("verified_at", "")
    for entry in data.get("stores_catalog", {}).values():
        if baseline and not entry.get("status_verified_at"):
            entry["status_verified_at"] = baseline
    now = now_iso()
    # Records are newest first; reversing keeps their insertion order for ties.
    events = sorted(reversed(data.get("records", [])), key=lambda r: r.get("created_at", ""))
    for event in events:
        # The operational snapshot is authoritative for everything before it.
        stamp = event.get("created_at", "")
        missing = not any(_name_words(normalize_store_name(k)) == _name_words(record_store_name(event))
                          for k in data.get("stores_catalog", {}))
        if stamp <= now and (baseline < stamp or (stamp[:7] >= STORE_IDENTITY_CUTOFF and missing)):
            update_catalog_from_event(data, event)
    return data


def normalize_google_ads_account(raw: str) -> str:
    clean = (raw or "").strip()
    if not clean:
        return "—"
    m_seq = re.match(r"^(?:google\s*ads|ads|conta)\s*#?\s*(\d{1,4})$", clean, re.I)
    if m_seq:
        return f"Google Ads {m_seq.group(1)}"
    if re.match(r"^\d{1,3}$", clean):
        return f"Google Ads {clean}"
    digits = re.sub(r"\D", "", clean)
    if len(digits) == 10 and "ads" not in clean.lower():
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return clean


def _name_words(value):
    value = unicodedata.normalize("NFKD", value or "").casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _identified_store(chunk, known_stores):
    candidates = [normalize_store_name(n) for n in known_stores if n]
    invalid = {"", "loja nao identificada", "nao identificada", "nova loja", "loja", "site", "cliente", "nenhuma", "n a"}
    labeled = re.search(r"(?:^|\n)\s*(?:loja|cliente)\s*[:\-]\s*([^\n]+)", chunk, re.I)
    if labeled:
        name = re.split(r"\s+[-–—]\s+|\s*\|\s*", labeled.group(1))[0].strip(" *_.:")
        if _name_words(name) in invalid or not re.search(r"[^\W\d_]", name, re.U):
            return ""
        for known in candidates:
            if _name_words(known) == _name_words(name):
                return known
        return normalize_store_name(name)
    words = " " + _name_words(chunk) + " "
    matched = {n for n in candidates if _name_words(n) not in invalid and " " + _name_words(n) + " " in words}
    # Prefer a complete name over a shorter name contained in it.
    matched = {n for n in matched if not any(n != other and " " + _name_words(n) + " " in " " + _name_words(other) + " " for other in matched)}
    return next(iter(matched)) if len(matched) == 1 else ""


def _identified_account(chunk):
    # Accept either the ten-digit customer ID or an explicit numbered account.
    cid = re.search(r"(?<!\d)(\d{3}[- ]\d{3}[- ]\d{4}|\d{10})(?!\d)", chunk)
    if cid:
        return normalize_google_ads_account(cid.group(1))
    number = re.search(r"\b(?:google\s*ads|conta(?:\s+(?:do\s+)?google(?:\s*ads)?)?|ads)\s*[:#\-]?\s*(\d{1,4})(?![\d-])\b", chunk, re.I)
    return f"Google Ads {number.group(1)}" if number else ""


def publication_type(record, data):
    if record.get("event_type") != "store_published":
        return record.get("event_type")
    words = _name_words(record.get("reason", ""))
    if re.search(r"\b(reativad[ao]|republicad[ao]|novamente|voltou|retornou)\b", words):
        return "store_reactivated"
    name = _name_words(record_store_name(record))
    stamp = record.get("created_at", "")
    for previous in data.get("records", []):
        if previous.get("id") != record.get("id") and _name_words(record_store_name(previous)) == name and previous.get("created_at", "") < stamp:
            return "store_reactivated"
    for month, config in data.get("months_config", {}).items():
        if month <= stamp[:7] and any(_name_words(normalize_store_name(n, month)) == name for n in config.get("start_stores_list", [])):
            return "store_reactivated"
    return "store_published"


def parse_whatsapp_chat_messages(raw_text: str, known_stores: list[str]) -> list[dict]:
    text = (raw_text or "").strip()
    if not text:
        return []

    wa_msg_pattern = re.compile(
        r"(?:^|\n)(?:\[?(\d{1,2}/\d{1,2}/\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]?(?:\s*-\s*|\s+)([^:\n]{1,35}):\s*)",
        re.MULTILINE,
    )
    text = text.replace("\u200e", "").replace("\u200f", "").replace("\u202f", " ").replace("\xa0", " ")
    matches = list(wa_msg_pattern.finditer(text))

    blocks = []
    if matches:
        for i, m in enumerate(matches):
            date_str = m.group(1)
            time_str = m.group(2)
            sender = m.group(3).strip()
            start_pos = m.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            msg_body = text[start_pos:end_pos].strip()

            created_at = now_iso()
            try:
                parts = date_str.split("/")
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                if year < 100:
                    year += 2000
                # WhatsApp exports with AM/PM use the US month/day format.
                if re.search(r"[AP]M", time_str, re.I):
                    month, day = day, month
                    clock = datetime.strptime(time_str.upper(), "%I:%M:%S %p" if time_str.count(":") == 2 else "%I:%M %p")
                else:
                    clock = datetime.strptime(time_str, "%H:%M:%S" if time_str.count(":") == 2 else "%H:%M")
                created_at = datetime(year, month, day, clock.hour, clock.minute, clock.second).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

            if msg_body:
                blocks.append({"body": msg_body, "created_at": created_at, "sender": sender})
    else:
        raw_chunks = re.split(r"(?:\r?\n\s*\r?\n|^[-=_]{3,}\s*$)", text, flags=re.MULTILINE)
        for ch in raw_chunks:
            if ch.strip():
                blocks.append({"body": ch.strip(), "created_at": now_iso(), "sender": "Grupo Kloube"})

    events = []
    for item in blocks:
        chunk = re.sub(r"[*_~]", "", item["body"])
        created_at = item["created_at"]
        month_key = created_at[:7]
        lower = chunk.lower()
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue

        store_name = ""
        domain = ""
        account_name = ""
        reason = ""

        # Padrão: Loja: X / Novo dominio: Y
        swap_match = re.search(r"Loja\s*[:\-]\s*([^\n\r]+)[\s\S]*?(?:Novo\s*dom[ií]nio|Novo\s*link|Nova\s*url)\s*[:\-]\s*([^\n\r\s]+)", chunk, re.IGNORECASE)
        if swap_match:
            s_name = _identified_store(chunk, known_stores)
            if not s_name:
                continue
            new_dom = swap_match.group(2).strip()
            events.append({
                "event_type": "domain_swapped",
                "store_name": s_name,
                "domain": new_dom,
                "account_number": "—",
                "inactive_category": "",
                "reason": f"Troca de domínio registrada para {new_dom}",
                "created_at": created_at,
                "month_key": month_key,
                "source": "whatsapp_group",
            })
            continue

        # Padrão Kloube: Google ads XX - Suspensa
        ads_header_match = re.search(r"Google\s*ads\s*(\d{1,4})(?:\s*[-–—]\s*(?:Suspensa|Bloqueada))?", chunk, re.IGNORECASE)
        if ads_header_match:
            account_name = f"Google Ads {ads_header_match.group(1)}"

        # Loja: Maggie - Bloqueios constantes...
        store_match = re.search(
            r"Loja\s*[:\-]\s*([^-\n\r–—]+)(?:[-–—\s]*([^\n\r]*))?",
            chunk,
            re.IGNORECASE,
        )
        extra_comment = ""
        if store_match:
            store_name = store_match.group(1).strip()
            extra_comment = (store_match.group(2) or "").strip()
        else:
            simple_store = re.search(r"(?:loja|cliente|site)\s*[:\-]\s*([^\n\r]+)", chunk, re.IGNORECASE)
            if simple_store:
                store_name = simple_store.group(1).strip()

        # Motivo: ...
        reason_match = re.search(
            r"(?:motivo|raz[aã]o|causa)\s*[:\-]\s*([^\n\r]+)",
            chunk,
            re.IGNORECASE,
        )
        if reason_match:
            reason = reason_match.group(1).strip()

        dom_match = re.search(r"(?:dom[ií]nio|domain|url|site)\s*[:\-]\s*([^\n\r\s]+)", chunk, re.IGNORECASE)
        if dom_match:
            domain = dom_match.group(1).strip()

        store_name = _identified_store(chunk, known_stores)
        account_name = _identified_account(chunk)
        if not store_name:
            continue

        # Reativação explícita: uma loja já existente voltou ao ar.
        if re.search(r"\b(reativad[ao]|republicad[ao]|subimos?\s+novamente|subi\s+novamente|voltou\s+ao\s+ar|no\s+ar\s+novamente|publicad[ao]\s+novamente)\b", lower):
            events.append({"event_type": "store_reactivated", "store_name": store_name,
                           "domain": domain, "account_number": account_name or "—",
                           "inactive_category": "", "reason": reason or "Loja reativada",
                           "created_at": created_at, "month_key": month_key, "source": "whatsapp_group"})
            continue

        # Loja Publicada
        if re.search(r"\b(publicad[ao]|subi\s+a\s+loja|nova\s+loja\s+no\s+ar|loja\s+publicada)\b", lower):
            events.append({
                "event_type": "store_published",
                "store_name": store_name or "Nova Loja",
                "domain": domain or "",
                "account_number": "—",
                "inactive_category": "",
                "reason": reason or "Loja publicada",
                "created_at": created_at,
                "month_key": month_key,
                "source": "whatsapp_group",
            })
            continue

        # DMCA: regra Kloube "quando for loja caiu Significa DMCA"
        if re.search(r"\b(dmca|dmc)\b", lower) or re.search(r"(?:loja\s+caiu|a\s+loja\s+caiu|caiu\s+a\s+loja)", lower):
            events.append({
                "event_type": "store_inactive",
                "store_name": store_name or "Loja não identificada",
                "domain": domain or "",
                "account_number": account_name or "—",
                "inactive_category": "dmca",
                "reason": reason or "DMCA (Loja caiu)",
                "created_at": created_at,
                "month_key": month_key,
                "source": "whatsapp_group",
            })
            continue

        # Domínio caiu
        if re.search(r"\b(dom[ií]nio\s+caiu|dominio\s+fora|sem\s+dom[ií]nio|dom[ií]nio.*?off)\b", lower):
            events.append({
                "event_type": "store_inactive",
                "store_name": store_name or "Loja não identificada",
                "domain": domain or "",
                "account_number": account_name or "—",
                "inactive_category": "domain_down",
                "reason": reason or "Domínio caiu",
                "created_at": created_at,
                "month_key": month_key,
                "source": "whatsapp_group",
            })
            continue

        # Conta Google Ads Suspensa: loja e número são obrigatórios.
        if account_name and re.search(r"\b(suspens[aã]o|suspens[ao]|bloquead[ao]|bloqueios?|conta\s+caiu)\b", lower):
            final_reason = reason or "Suspensão / bloqueio no Google Ads"
            if extra_comment and extra_comment not in final_reason:
                final_reason = f"{final_reason} ({extra_comment})"

            is_store_inactivated = bool(
                re.search(r"(?:n[aã]o\s+se\s+mantem|inativ[ao]|encerrad[ao]|loja\s+parou|bloqueios\s+constantes)", lower)
            )

            events.append({
                "event_type": "ads_suspended",
                "store_name": store_name or "Loja não identificada",
                "domain": domain or "",
                "account_number": account_name or "Google Ads",
                "inactive_category": "",
                "reason": final_reason,
                "created_at": created_at,
                "month_key": month_key,
                "source": "whatsapp_group",
            })

            if is_store_inactivated:
                events.append({
                    "event_type": "store_inactive",
                    "store_name": store_name or "Loja não identificada",
                    "domain": domain or "",
                    "account_number": account_name or "Google Ads",
                    "inactive_category": "ads_block",
                    "reason": f"Suspensão / bloqueio no Google Ads ({account_name})",
                    "created_at": created_at,
                    "month_key": month_key,
                    "source": "whatsapp_group",
                })

    return events


from shared_github import StorageUnavailable, StorageConflict

class SharedStorageError(StorageUnavailable):
    pass


class SharedStorageConflict(SharedStorageError, StorageConflict):
    pass


class BlocksStore:
    def __init__(self, path: Path = DATA_FILE):
        self.path = path
        self.tmp_path = Path("/tmp/kloube_blocks_data.json")
        self.lock = threading.Lock()
        self.cloud_snapshot = threading.local()
        from shared_github import GitHubFile
        self.shared_file = GitHubFile("blocks_data.json")
        self._ensure_file()

    def _active_read_path(self) -> Path:
        if os.environ.get("VERCEL") and self.tmp_path.exists():
            return self.tmp_path
        return self.path

    def _write_raw(self, content: str):
        try:
            self.path.write_text(content, encoding="utf-8")
        except OSError:
            self.tmp_path.parent.mkdir(parents=True, exist_ok=True)
            self.tmp_path.write_text(content, encoding="utf-8")

    def get_monitor_domains_stores(self) -> list[dict]:
        stores = []
        seen = set()
        try:
            if DOMAINS_DATA_FILE.exists():
                dom_data = json.loads(DOMAINS_DATA_FILE.read_text(encoding="utf-8"))
                for d in dom_data.get("domains", []):
                    name = (d.get("name") or d.get("domain") or "").strip()
                    domain = (d.get("domain") or "").strip()
                    status = d.get("status", "online")
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        stores.append(
                            {
                                "name": name,
                                "domain": domain,
                                "status": status,
                                "reason": d.get("reason", ""),
                            }
                        )
        except Exception:
            pass
        return stores

    def _ensure_file(self):
        if not self.path.exists() and not self.tmp_path.exists():
            initial = {
                "schema_version": 2,
                "months_config": {
                    "2026-10": {"is_closed": True, "start_stores_list": []},
                    "2026-11": {"is_closed": False, "start_stores_list": []},
                },
                "whatsapp_config": {
                    "target_group_name": "EQUIPE KLOUBE ECOM",
                    "read_only_mode": True,
                    "allowed_phones": [],
                },
                "stores_catalog": {},
                "records": [],
                "updated_at": now_iso(),
            }
            self._write_raw(json.dumps(initial, indent=2, ensure_ascii=False))

    def get_cloud_credentials(self) -> tuple[str, str] | None:
        url = (
            os.environ.get("KV_REST_API_URL")
            or os.environ.get("UPSTASH_REDIS_REST_URL")
            or ""
        ).strip()
        token = (
            os.environ.get("KV_REST_API_TOKEN")
            or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
            or ""
        ).strip()
        if url and token:
            return (url.rstrip("/"), token)
        return None

    def _cloud_cmd(self, url: str, token: str, cmd_list: list) -> any:
        import urllib.request
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(cmd_list).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if "result" not in res:
                    raise SharedStorageError("Resposta inválida do armazenamento compartilhado.")
                return res["result"]
        except Exception as e:
            print(f"[CloudKV] Error executing {cmd_list[0] if cmd_list else ''}: {e}", flush=True)
            raise SharedStorageError("Falha ao conectar ao armazenamento compartilhado.") from e

    def _load_local_data(self) -> dict:
        target = self._active_read_path()
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        except Exception:
            self._ensure_file()
            target = self._active_read_path()
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        return data

    def load(self) -> dict:
        return reconcile_catalog(self._load())

    def _load(self) -> dict:
        with self.lock:
            creds = self.get_cloud_credentials()
            if creds:
                url, token = creds
                result = self._cloud_cmd(url, token, ["GET", "kloube_blocks_data"])
                if result is None:
                    initial = json.dumps(self._load_local_data(), ensure_ascii=False)
                    self._cloud_cmd(url, token, ["SET", "kloube_blocks_data", initial, "NX"])
                    result = self._cloud_cmd(url, token, ["GET", "kloube_blocks_data"])
                try:
                    data = json.loads(result)
                except (TypeError, ValueError) as exc:
                    raise SharedStorageError("Dados compartilhados inválidos.") from exc
                if not isinstance(data, dict):
                    raise SharedStorageError("Dados compartilhados inválidos.")
                self.cloud_snapshot.value = result
                return data

            from shared_github import enabled
            if enabled():
                return self.shared_file.load()
            if os.environ.get("VERCEL"):
                raise SharedStorageError("Configure o armazenamento compartilhado no GitHub antes de usar o app online.")
            return self._load_local_data()

    def save(self, data: dict):
        with self.lock:
            data["updated_at"] = now_iso()
            raw_str = json.dumps(data, indent=2, ensure_ascii=False)
            creds = self.get_cloud_credentials()
            if creds:
                url, token = creds
                previous = getattr(self.cloud_snapshot, "value", None)
                if previous is None:
                    raise SharedStorageError("Atualize a página antes de salvar.")
                script = (
                    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end "
                    "redis.call('SET', KEYS[1], ARGV[2]); return 1"
                )
                if self._cloud_cmd(url, token, [
                    "EVAL", script, "1", "kloube_blocks_data", previous, raw_str
                ]) != 1:
                    raise SharedStorageConflict("Outra atualização foi salva. Atualize a página e tente novamente.")
                self.cloud_snapshot.value = raw_str
                return
            from shared_github import enabled
            if enabled():
                self.shared_file.save(data)
                return
            if os.environ.get("VERCEL"):
                raise SharedStorageError("Configure o armazenamento compartilhado antes de salvar.")
            self._write_raw(raw_str)


store = BlocksStore()
import monitor_bridge
monitor_bridge.configure(store, normalize_store_name)


def _filter_records_by_period(records: list[dict], month_key: str, period_filter: str, day_str: str = "", start_date: str = "", end_date: str = "") -> list[dict]:
    now_dt = datetime.now(BRT_TZ)
    today_str = day_str.strip() if day_str else now_dt.strftime("%Y-%m-%d")

    out = []
    for r in records:
        created = r.get("created_at", "")
        r_month = r.get("month_key") or created[:7]
        rec_date = created[:10]

        if period_filter == "day":
            if not created.startswith(today_str):
                continue
        elif period_filter == "week":
            try:
                dt = datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT_TZ)
                if (now_dt - dt).total_seconds() > 7 * 86400:
                    continue
            except Exception:
                if not created.startswith(today_str[:7]):
                    continue
        elif period_filter == "month":
            if r_month != month_key:
                continue
        elif period_filter == "custom":
            if start_date and rec_date < start_date:
                continue
            if end_date and rec_date > end_date:
                continue
        out.append(r)
    return out


def compute_summary_with_details(data: dict, month_key: str, period_filter: str = "month", day_str: str = "", start_date: str = "", end_date: str = "") -> dict:
    # Canonical presentation only: persisted records retain their original names and IDs.
    data = dict(data)
    data["records"] = [dict(r, store_name=record_store_name(r)) for r in data.get("records", [])]
    try:
        year_int, month_int = [int(x) for x in month_key.split("-")]
    except Exception:
        now_dt = datetime.now(BRT_TZ)
        year_int, month_int = now_dt.year, now_dt.month
        month_key = f"{year_int:04d}-{month_int:02d}"

    _, last_day = calendar.monthrange(year_int, month_int)
    month_name = MONTH_NAMES_PT.get(month_int, "MÊS")
    current_month_key = datetime.now(BRT_TZ).strftime("%Y-%m")
    is_current_month = month_key == current_month_key

    if period_filter == "day":
        active_day = day_str if day_str else datetime.now(BRT_TZ).strftime("%Y-%m-%d")
        try:
            d_obj = datetime.strptime(active_day, "%Y-%m-%d")
            subtitle = f"Filtro: {d_obj.strftime('%d/%m/%Y')}"
        except Exception:
            subtitle = f"Filtro: {active_day}"
    elif period_filter == "week":
        subtitle = "Filtro: Últimos 7 dias"
    elif period_filter == "custom":
        def _fmt(d_str):
            try:
                p = d_str.split("-")
                return f"{p[2]}/{p[1]}/{p[0]}"
            except Exception:
                return d_str
        subtitle = f"Filtro personalizado: de {_fmt(start_date) if start_date else 'início'} até {_fmt(end_date) if end_date else 'hoje'}"
    elif period_filter == "all":
        subtitle = "Filtro: Histórico Completo"
    else:
        subtitle = f"De 01/{month_int:02d} a {last_day:02d}/{month_int:02d}/{year_int}"

    m_cfg = data.get("months_config", {}).get(month_key, {})
    catalog = data.get("stores_catalog", {})
    monitor_stores = store.get_monitor_domains_stores()
    monitor_map = {s["name"].lower(): s for s in monitor_stores}

    def resolve_domain(s_name: str) -> str:
        if not s_name:
            return ""
        clean = s_name.strip()
        if clean in catalog:
            return catalog[clean].get("current_domain", "")
        for k, v in catalog.items():
            if k.lower() == clean.lower():
                return v.get("current_domain", "")
        if clean.lower() in monitor_map:
            return monitor_map[clean.lower()].get("domain", "")
        return ""

    def resolve_swaps_count(s_name: str) -> int:
        clean = (s_name or "").strip()
        if clean in catalog:
            return catalog[clean].get("domain_swaps_count", 0)
        for k, v in catalog.items():
            if k.lower() == clean.lower():
                return v.get("domain_swaps_count", 0)
        return 0

    # Start of month stores list
    start_names = m_cfg.get("start_stores_list")
    if not isinstance(start_names, list) or not start_names:
        if month_key == "2026-10":
            start_names = [
                "Body & Bra", "Haloring", "Dogshood", "Soya Paris", "Havie", "Ionix Labs",
                "Luveon", "ParoPet", "Donnea Milano", "Softari", "Epoch", "Scarlette",
                "Breeze", "Orre Naturals"
            ]
        else:
            start_names = [s["name"] for s in monitor_stores if s.get("status") == "online"][:20]

    start_stores_items = [
        {
            "store_name": name,
            "domain": resolve_domain(name),
            "domain_swaps_count": resolve_swaps_count(name),
            "detail": f"Ativa no início de {month_name.lower()}",
        }
        for name in start_names
    ]

    scoped_records = _filter_records_by_period(data.get("records", []), month_key, period_filter, day_str, start_date, end_date)

    def unique_stores(records):
        found = {}
        for record in records:
            found.setdefault(_name_words(normalize_store_name(record.get("store_name", ""))), record)
        return list(found.values())
    published_records = unique_stores([r for r in scoped_records if publication_type(r, data) == "store_published"])
    reactivated_records = unique_stores([r for r in scoped_records if publication_type(r, data) == "store_reactivated"])
    inactive_records = [r for r in scoped_records if r.get("event_type") == "store_inactive"]
    ads_records = [r for r in scoped_records if r.get("event_type") == "ads_suspended"]

    for r in published_records + reactivated_records + inactive_records + ads_records:
        if not r.get("domain") or r.get("domain") == "—":
            r["domain"] = resolve_domain(r.get("store_name", ""))
        r["domain_swaps_count"] = resolve_swaps_count(r.get("store_name", ""))

    dmca_records = [r for r in inactive_records if r.get("inactive_category") == "dmca"]
    ads_inact_records = [r for r in inactive_records if r.get("inactive_category") == "ads_block"]
    dom_inact_records = [r for r in inactive_records if r.get("inactive_category") == "domain_down"]

    # Inactive names set defined unconditionally
    inactive_names_lower = {
        (r.get("store_name") or "").strip().lower() for r in inactive_records
    }

    # Historical October 2026 handling (Exatamente alinhado à planilha oficial GID 20261030 e GID 0)
    if month_key == "2026-10" and period_filter in ("month", "all"):
        start_count = 14
        end_active_count = 20
        published_count = 6
        dmca_count = 5
        ads_inact_count = 5
        dom_inact_count = 1
        total_inactive = 11
        total_registered = 31
        ads_accounts_count = 19
        ads_stores_count = 7

        end_active_names = [
            "Sofitari", "Lush Essence", "Evolv", "Breeze Wear", "Grace & Ease",
            "Sleepsake", "Vivacuerpo", "Belluna", "Donnèa Milano", "Oré naturals",
            "Luveon", "Ionix Labs 2", "Eversock", "Snuggi", "Sondur Travel",
            "Scarlette", "ParoPet", "Krakatoa", "Libracases", "LongWell"
        ]
        active_stores_items = [
            {
                "store_name": name,
                "domain": resolve_domain(name),
                "domain_swaps_count": resolve_swaps_count(name),
                "detail": "Ativa no fechamento do mês",
            }
            for name in end_active_names
        ]

        # 11 Lojas Inativas Exatas de Outubro (Planilha GID 0)
        oct_dmca_stores = [
            {"name": "Sivara", "reason": "Loja caiu (DMCA)"},
            {"name": "Nesti", "reason": "Loja caiu (DMCA)"},
            {"name": "BREEZE", "reason": "Loja caiu (DMCA)"},
            {"name": "Body & Bra", "reason": "Loja caiu (DMCA)"},
            {"name": "Ionix Labs", "reason": "Loja caiu (DMCA) - Operao do card IONIX LABS; distinta de Ionix Labs 2"},
        ]
        oct_ads_stores = [
            {"name": "Soya Paris", "reason": "Bloqueios constantes no Ads"},
            {"name": "Blushe", "reason": "Bloqueios constantes no Ads"},
            {"name": "Dogs Hood 2", "reason": "Bloqueios constantes no Ads"},
            {"name": "Havie", "reason": "Bloqueios constantes no Ads"},
            {"name": "Halo Ring", "reason": "Bloqueios constantes no Ads"},
        ]
        oct_dom_stores = [
            {"name": "Maggie's Tanks", "reason": "DOMINIO CAIU"},
        ]

        dmca_records = [
            {
                "id": f"oct_dmca_{idx}",
                "event_type": "store_inactive",
                "store_name": it["name"],
                "domain": resolve_domain(it["name"]),
                "domain_swaps_count": resolve_swaps_count(it["name"]),
                "account_number": "—",
                "inactive_category": "dmca",
                "reason": it["reason"],
                "created_at": "2026-10-25 18:00:00",
                "registered_at": "25/10/2026",
            }
            for idx, it in enumerate(oct_dmca_stores)
        ]
        ads_inact_records = [
            {
                "id": f"oct_ads_{idx}",
                "event_type": "store_inactive",
                "store_name": it["name"],
                "domain": resolve_domain(it["name"]),
                "domain_swaps_count": resolve_swaps_count(it["name"]),
                "account_number": "Google Ads",
                "inactive_category": "ads_block",
                "reason": it["reason"],
                "created_at": "2026-10-26 14:00:00",
                "registered_at": "26/10/2026",
            }
            for idx, it in enumerate(oct_ads_stores)
        ]
        dom_inact_records = [
            {
                "id": f"oct_dom_{idx}",
                "event_type": "store_inactive",
                "store_name": it["name"],
                "domain": resolve_domain(it["name"]),
                "domain_swaps_count": resolve_swaps_count(it["name"]),
                "account_number": "—",
                "inactive_category": "domain_down",
                "reason": it["reason"],
                "created_at": "2026-10-27 10:00:00",
                "registered_at": "27/10/2026",
            }
            for idx, it in enumerate(oct_dom_stores)
        ]
        inactive_records = dmca_records + ads_inact_records + dom_inact_records
        inactive_names_lower = {it["name"].lower() for it in oct_dmca_stores + oct_ads_stores + oct_dom_stores}

    else:
        start_count = len(start_stores_items)
        published_count = len(published_records)
        dmca_count = len(dmca_records)
        ads_inact_count = len(ads_inact_records)
        dom_inact_count = len(dom_inact_records)
        total_inactive = dmca_count + ads_inact_count + dom_inact_count

        active_names = []
        seen_active = set()
        for nm in start_names + [r.get("store_name", "") for r in published_records]:
            clean_nm = (nm or "").strip()
            if clean_nm and clean_nm.lower() not in seen_active and clean_nm.lower() not in inactive_names_lower:
                seen_active.add(clean_nm.lower())
                active_names.append(clean_nm)

        active_stores_items = [
            {
                "store_name": name,
                "domain": resolve_domain(name),
                "domain_swaps_count": resolve_swaps_count(name),
                "detail": "Loja ativa no momento",
            }
            for name in active_names
        ]
        end_active_count = len(active_stores_items)
        total_registered = start_count + published_count
        ads_accounts_count = len(ads_records)

    today_key = datetime.now(BRT_TZ).strftime("%Y-%m-%d")
    active_is_current = (
        (period_filter == "month" and month_key == today_key[:7])
        or (period_filter == "day" and (day_str or today_key) == today_key)
        or period_filter in ("week", "all")
        or (period_filter == "custom" and (not start_date or start_date <= today_key) and (not end_date or end_date >= today_key))
    )
    if active_is_current:
        active_stores_items = [
            {"store_name": entry.get("name") or name,
             "domain": entry.get("current_domain", ""),
             "domain_swaps_count": entry.get("domain_swaps_count", 0),
             "detail": "Ativa na conferência operacional atual"}
            for name, entry in catalog.items()
            if str(entry.get("status", "")).strip().casefold() == "ativa"
        ]
        end_active_count = len(active_stores_items)

    # Group Ads suspensions by store
    affected_map = {}
    for r in ads_records:
        st_name = (r.get("store_name") or "Loja").strip()
        key = st_name.lower()
        if key not in affected_map:
            affected_map[key] = {
                "store_name": st_name,
                "domain": r.get("domain") or resolve_domain(st_name),
                "domain_swaps_count": resolve_swaps_count(st_name),
                "accounts": [],
                "reasons": [],
                "last_date": r.get("created_at", ""),
            }
        acc = r.get("account_number") or "Google Ads"
        affected_map[key]["accounts"].append(acc)
        if r.get("reason"):
            affected_map[key]["reasons"].append(r["reason"])

    # If Libra Cases was notified on Google Ads 79, link it if not already in affected_map
    if "Google Ads 79" in [r.get("account_number") for r in ads_records] and "libra cases" not in affected_map and month_key == "2026-11":
        affected_map["libra cases"] = {
            "store_name": "Libra Cases",
            "domain": resolve_domain("Libra Cases"),
            "domain_swaps_count": 0,
            "accounts": ["Google Ads 79"],
            "reasons": ["Notificada no Google Ads 79 / DMCA"],
            "last_date": "2026-11-03 01:01:00",
        }

    affected_stores_items = [
        {
            "store_name": v["store_name"],
            "domain": v["domain"],
            "domain_swaps_count": v["domain_swaps_count"],
            "accounts": v["accounts"],
            "count": len(v["accounts"]),
            "detail": f"{len(v['accounts'])} conta(s): {', '.join(v['accounts'])}{(' · Domínio: ' + v['domain']) if v['domain'] else ''}",
            "created_at": v["last_date"],
        }
        for v in affected_map.values()
    ]
    if not (month_key == "2026-10" and period_filter == "month"):
        ads_stores_count = len(affected_stores_items)

    all_registered_items = start_stores_items + [
        {
            "id": r.get("id"),
            "store_name": r.get("store_name"),
            "domain": resolve_domain(r.get("store_name", "")),
            "domain_swaps_count": resolve_swaps_count(r.get("store_name", "")),
            "detail": f"Publicada em {r.get('created_at', '')[:10]}",
            "created_at": r.get("created_at"),
        }
        for r in published_records
    ]

    # Build attention incidents for "O que precisa de atenção?"
    attention_incidents = []
    for r in inactive_records:
        cat = r.get("inactive_category")
        if cat == "dmca":
            tipo = "DMCA"
            icon = "shopify_red"
        elif cat == "ads_block":
            tipo = "Conta suspensa"
            icon = "google_ads"
        elif cat == "domain_down":
            tipo = "Domínio fora do ar"
            icon = "domain"
        else:
            tipo = r.get("reason") or "Loja inativa"
            icon = "shopify_red"

        created_str = r.get("created_at", "")
        reg_date = created_str[:10]
        try:
            parts = reg_date.split("-")
            reg_date = f"{parts[2]}/{parts[1]}/{parts[0]}"
        except Exception:
            pass

        attention_incidents.append({
            "id": r.get("id"),
            "store_name": normalize_store_name(r.get("store_name", "Loja")),
            "domain": r.get("domain", ""),
            "tipo": tipo,
            "incident_category": cat or "inactive",
            "icon": icon,
            "status": "Loja inativa",
            "status_type": "danger",
            "account_number": r.get("account_number", "—"),
            "reason": r.get("reason", ""),
            "registered_at": reg_date,
            "raw_date": created_str,
            "event_type": "store_inactive"
        })

    for r in ads_records:
        st_name = normalize_store_name(r.get("store_name", ""))
        already_in = any(
            x["store_name"].lower() == st_name.lower() and x["event_type"] == "store_inactive"
            for x in attention_incidents
        )
        created_str = r.get("created_at", "")
        reg_date = created_str[:10]
        try:
            parts = reg_date.split("-")
            reg_date = f"{parts[2]}/{parts[1]}/{parts[0]}"
        except Exception:
            pass

        is_store_inact = st_name.lower() in inactive_names_lower
        status_label = "Loja inativa" if is_store_inact else "Conta suspensa"
        status_type = "danger" if is_store_inact else "warning"

        if not already_in:
            attention_incidents.append({
                "id": r.get("id"),
                "store_name": st_name or "Loja",
                "domain": r.get("domain", ""),
                "tipo": "Conta suspensa",
                "incident_category": "ads_suspended",
                "icon": "google_ads",
                "status": status_label,
                "status_type": status_type,
                "account_number": r.get("account_number", "—"),
                "reason": r.get("reason", ""),
                "registered_at": reg_date,
                "raw_date": created_str,
                "event_type": "ads_suspended"
            })

    if active_is_current:
        current_active_names = {_name_words(item["store_name"]) for item in active_stores_items}
        for incident in attention_incidents:
            if _name_words(incident.get("store_name", "")) in current_active_names:
                incident["status"] = "Ativa hoje · histórico"
                incident["status_type"] = "success"

    attention_incidents.sort(key=lambda x: x.get("raw_date", ""), reverse=True)

    return {
        "month_key": month_key,
        "period_filter": period_filter,
        "day_str": day_str,
        "is_current_month": is_current_month,
        "month_name_lower": month_name.lower(),
        "title": f"{month_name} {year_int} — RESUMO DAS LOJAS",
        "subtitle": subtitle,
        "start_active_stores": start_count,
        "end_active_stores": end_active_count,
        "active_is_current": active_is_current,
        "operational_verified_at": data.get("operational_snapshot", {}).get("verified_at", ""),
        "active_label": "Lojas ativas no momento atual" if is_current_month else "Lojas ativas no fim do mês",
        "published_stores_count": published_count,
        "reactivated_stores_count": len(reactivated_records),
        "total_inactive": total_inactive,
        "total_registered": total_registered,
        "inactive_reasons": {
            "dmca": dmca_count,
            "ads_block": ads_inact_count,
            "domain_down": dom_inact_count,
        },
        "ads_suspensions": {
            "total_accounts": ads_accounts_count,
            "affected_stores": ads_stores_count,
        },
        "attention_incidents": attention_incidents,
        "details": {
            "start_stores": start_stores_items,
            "active_stores": active_stores_items,
            "published_stores": published_records,
            "reactivated_stores": reactivated_records,
            "inactive_all": inactive_records,
            "inactive_dmca": dmca_records,
            "inactive_ads": ads_inact_records,
            "inactive_domain": dom_inact_records,
            "registered_all": all_registered_items,
            "ads_accounts": ads_records,
            "ads_affected_stores": affected_stores_items,
        },
    }


def _build_state(month_key: str = "", period_filter: str = "month", day_str: str = "", start_date: str = "", end_date: str = "") -> dict:
    data = store.load()
    if not month_key:
        month_key = datetime.now(BRT_TZ).strftime("%Y-%m")
    if period_filter not in ("day", "week", "month", "all", "custom", "month_prev"):
        period_filter = "month"

    if period_filter == "month_prev":
        month_key = "2026-10"
        period_filter = "month"

    summary = compute_summary_with_details(data, month_key, period_filter, day_str, start_date, end_date)
    return {
        "ok": True,
        "server_time": now_iso(),
        "selected_month": month_key,
        "period_filter": period_filter,
        "selected_day": day_str or datetime.now(BRT_TZ).strftime("%Y-%m-%d"),
        "start_date": start_date,
        "end_date": end_date,
        "monthly_summary": summary,
        "deleted_records": data.get("deleted_records", []),
        "deletion_audit": data.get("deletion_audit", []),
        "stores_catalog": data.get("stores_catalog", {}),
        "whatsapp_config": data.get("whatsapp_config", {}),
        "records": [dict(r, store_name=record_store_name(r), display_event_type=publication_type(r, data)) for r in data.get("records", [])],
        "known_stores": store.get_monitor_domains_stores(),
    }


class BlocksHandler(BaseHTTPRequestHandler):
    def _dispatch_domain_monitor(self, method):
        if not urllib.parse.urlparse(self.path).path.startswith("/monitoramento"):
            return False
        from domain_monitor.server import KloubeHandler
        if method == "GET":
            monitor_bridge.sync_publications(store.load())
        original_class, original_path = self.__class__, self.path
        self.path = self.path[len("/monitoramento"):] or "/"
        if self.path.startswith("?"):
            self.path = "/" + self.path
        try:
            # Bind the existing handler so all of its helper methods and login routes remain unchanged.
            self.__class__ = KloubeHandler
            handler = getattr(self, "do_" + method, None)
            if handler is None:
                self.send_error(405)
            else:
                handler()
        except StorageConflict as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=409)
        except (StorageUnavailable, OSError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=503)
        finally:
            self.__class__, self.path = original_class, original_path
        return True

    def do_PUT(self):
        from shared_auth import gate
        if gate(self, "PUT"):
            return
        if not self._dispatch_domain_monitor("PUT"):
            self._send_json({"ok": False, "error": "Rota não encontrada"}, status=404)

    def do_DELETE(self):
        from shared_auth import gate
        if gate(self, "DELETE"):
            return
        if not self._dispatch_domain_monitor("DELETE"):
            self._send_json({"ok": False, "error": "Rota não encontrada"}, status=404)

    protocol_version = "HTTP/1.1"
    close_connection = True

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}", flush=True)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_json(self, payload: dict, status: int = 200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except Exception:
            pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        return json.loads(body) if body else {}

    def do_GET(self):
        from shared_auth import gate
        if gate(self, "GET"):
            return
        if self._dispatch_domain_monitor("GET"):
            return
        print(f">>> do_GET called for: {self.path}", flush=True)
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/api/sync-status":
                creds = store.get_cloud_credentials()
                cur_data = store.load()
                from shared_github import enabled as github_enabled
                return self._send_json({
                    "ok": True,
                    "connected": bool(creds) or github_enabled(),
                    "provider": "Vercel KV / Upstash Redis" if creds else ("GitHub" if github_enabled() else "Local"),
                    "is_vercel": bool(os.environ.get("VERCEL")),
                    "updated_at": cur_data.get("updated_at") or now_iso(),
                })

            if path == "/api/state":
                month = (qs.get("month", [""])[0] or "").strip()
                period = (qs.get("period", ["month"])[0] or "month").strip()
                day = (qs.get("day", [""])[0] or "").strip()
                start_date = (qs.get("start_date", [""])[0] or "").strip()
                end_date = (qs.get("end_date", [""])[0] or "").strip()
                payload = _build_state(month, period, day, start_date, end_date)
                from shared_auth import current_user
                from domain_monitor.monitor_engine import MASTER_EMAILS
                payload["can_purge_trash"] = (current_user(self) or "").casefold() in MASTER_EMAILS
                return self._send_json(payload)

            if path == "/api/store-detail":
                store_name = (qs.get("name", [""])[0] or "").strip()
                norm_name = normalize_store_name(store_name)
                data = store.load()
                catalog = data.get("stores_catalog", {})
                all_records = data.get("records", [])

                dmca_list = []
                ads_list = []
                domain_list = []

                oct_dmca_map = {
                    "sivara": "Loja caiu (DMCA)",
                    "nesti": "Loja caiu (DMCA)",
                    "breeze": "Loja caiu (DMCA)",
                    "body & bra": "Loja caiu (DMCA)",
                    "ionix labs": "Loja caiu (DMCA) - Operação do card IONIX LABS; distinta de Ionix Labs 2",
                }
                oct_ads_map = {
                    "soya paris": "Bloqueios constantes no Ads",
                    "blushe": "Bloqueios constantes no Ads",
                    "dogs hood 2": "Bloqueios constantes no Ads",
                    "havie": "Bloqueios constantes no Ads",
                    "halo ring": "Bloqueios constantes no Ads",
                }
                oct_dom_map = {
                    "maggie's tanks": "DOMINIO CAIU",
                }

                if norm_name.lower() in oct_dmca_map:
                    dmca_list.append({
                        "date": "25/10/2026",
                        "raw_date": "2026-10-25 18:00:00",
                        "type": "DMCA",
                        "reason": oct_dmca_map[norm_name.lower()],
                        "account": "—",
                        "month": "",
                    })

                if norm_name.lower() in oct_ads_map:
                    ads_list.append({
                        "date": "26/10/2026",
                        "raw_date": "2026-10-26 14:00:00",
                        "type": "Conta suspensa",
                        "reason": oct_ads_map[norm_name.lower()],
                        "account": "Google Ads",
                        "month": "",
                    })

                if norm_name.lower() in oct_dom_map:
                    domain_list.append({
                        "date": "27/10/2026",
                        "raw_date": "2026-10-27 10:00:00",
                        "type": "Domínio fora do ar",
                        "reason": oct_dom_map[norm_name.lower()],
                        "account": "—",
                        "month": "",
                    })

                for r in all_records:
                    r_norm = normalize_store_name(r.get("store_name", ""))
                    if r_norm.lower() == norm_name.lower():
                        c_date = r.get("created_at", "")[:10]
                        try:
                            p = c_date.split("-")
                            fmt_date = f"{p[2]}/{p[1]}/{p[0]}"
                        except Exception:
                            fmt_date = c_date

                        evt = r.get("event_type")
                        cat = r.get("inactive_category")
                        acc = r.get("account_number", "—")

                        if cat == "dmca":
                            dmca_list.append({
                                "date": fmt_date,
                                "raw_date": r.get("created_at", ""),
                                "type": "DMCA",
                                "reason": r.get("reason", "DMCA"),
                                "account": acc,
                                "month": "",
                            })
                        elif cat == "domain_down":
                            domain_list.append({
                                "date": fmt_date,
                                "raw_date": r.get("created_at", ""),
                                "type": "Domínio fora do ar",
                                "reason": r.get("reason", "Domínio caiu"),
                                "account": acc,
                                "month": "",
                            })
                        elif evt == "ads_suspended" or cat == "ads_block":
                            ads_list.append({
                                "date": fmt_date,
                                "raw_date": r.get("created_at", ""),
                                "type": "Conta suspensa",
                                "reason": r.get("reason", "Conta Google Ads suspensa"),
                                "account": acc if acc != "—" else "Google Ads",
                                "month": "",
                            })

                # Sort by date descending
                ads_list.sort(key=lambda x: x.get("raw_date", ""), reverse=True)
                dmca_list.sort(key=lambda x: x.get("raw_date", ""), reverse=True)
                domain_list.sort(key=lambda x: x.get("raw_date", ""), reverse=True)

                all_incidents = ads_list + dmca_list + domain_list
                all_incidents.sort(key=lambda x: x.get("raw_date", ""), reverse=True)

                # Collect all domains associated
                cat_entry = catalog.get(norm_name, {})
                if not cat_entry:
                    for k, v in catalog.items():
                        if k.lower() == norm_name.lower():
                            cat_entry = v
                            break

                current_dom = canonical_domain_url(cat_entry.get("current_domain", ""))
                current_ads = cat_entry.get("current_ads_account", "")
                store_status = cat_entry.get("status", "Ativa")

                distinct_domains = []
                if current_dom and current_dom not in distinct_domains:
                    distinct_domains.append(current_dom)
                for dh in cat_entry.get("domain_history", []):
                    d_val = canonical_domain_url(dh.get("domain"))
                    if d_val and d_val not in distinct_domains:
                        distinct_domains.append(d_val)
                for r in all_records:
                    if normalize_store_name(r.get("store_name", "")).lower() == norm_name.lower():
                        d_val = canonical_domain_url(r.get("domain"))
                        if d_val and d_val not in distinct_domains:
                            distinct_domains.append(d_val)

                # Collect all ads accounts
                distinct_accounts = []
                if current_ads and current_ads != "—" and current_ads not in distinct_accounts:
                    distinct_accounts.append(current_ads)
                for sp in cat_entry.get("suspensions", []):
                    acc = normalize_google_ads_account(sp.get("account") or "")
                    if acc and acc != "—" and acc not in distinct_accounts:
                        distinct_accounts.append(acc)
                for r in all_records:
                    if normalize_store_name(r.get("store_name", "")).lower() == norm_name.lower():
                        acc = normalize_google_ads_account(r.get("account_number") or "")
                        if acc and acc != "—" and acc not in distinct_accounts:
                            distinct_accounts.append(acc)

                # The verified ADS ATIVO roster confirms an account in use even without its ID.
                def valid_account(value):
                    return bool(re.fullmatch(r"(?:Google\s*Ads\s*#?\s*\d{1,4}|\d{3}[- ]?\d{3}[- ]?\d{4}|\d{1,4})", value or "", re.I))
                distinct_accounts = list(dict.fromkeys(
                    normalize_google_ads_account(a) for a in distinct_accounts if valid_account(a)
                ))
                current_ads = normalize_google_ads_account(current_ads) if valid_account(current_ads) else ""
                verified_active = data.get("operational_snapshot", {}).get("active_stores", [])
                ads_in_use = bool(current_ads) or (
                    str(store_status).strip().casefold() == "ativa"
                    and any(_name_words(name) == _name_words(norm_name) for name in verified_active)
                )
                current_account_unidentified = ads_in_use and not current_ads
                account_count = max(len(distinct_accounts), 1 if ads_in_use else 0)

                # Collect publications
                publications = list(cat_entry.get("publications", []))
                for r in all_records:
                    if normalize_store_name(r.get("store_name", "")).lower() == norm_name.lower():
                        if r.get("event_type") in ("store_published", "store_reactivated"):
                            publications.append({
                                "date": r.get("created_at", "")[:16],
                                "event": "Publicação registrada",
                                "notes": r.get("reason", "Loja publicada")
                            })

                swaps_cnt = cat_entry.get("domain_swaps_count", 0)

                return self._send_json({
                    "ok": True,
                    "store": {
                        "name": norm_name,
                        "status": store_status,
                        "current_domain": current_dom,
                        "current_ads_account": current_ads,
                        "domain_swaps_count": swaps_cnt,
                        "domains_count": len(distinct_domains),
                        "domains_list": distinct_domains,
                        "total_dmca": len(dmca_list),
                        "dmca_dates": [x["date"] for x in dmca_list if x.get("date")],
                        "total_accounts_count": account_count,
                        "ads_in_use": ads_in_use,
                        "current_account_unidentified": current_account_unidentified,
                        "accounts_list": distinct_accounts,
                        "publications_count": len(publications),
                        "publications_list": publications,
                        "total_ads_blocks": len(ads_list),
                        "total_domain_drops": len(domain_list),
                        "total_incidents": len(all_incidents),
                        "ads_blocks": ads_list,
                        "dmca_events": dmca_list,
                        "domain_drops": domain_list,
                        "all_incidents": all_incidents,
                    }
                })

            if path == "/" or path == "":
                file_path = PUBLIC_DIR / "index.html"
            else:
                rel = path.lstrip("/")
                file_path = (PUBLIC_DIR / rel).resolve()
                if not str(file_path).startswith(str(PUBLIC_DIR.resolve())):
                    self.send_error(403)
                    return

            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return

            mime, _ = mimetypes.guess_type(str(file_path))
            mime = mime or "application/octet-stream"
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(content)
            except Exception:
                pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def do_POST(self):
        from shared_auth import gate
        if gate(self, "POST"):
            return
        if self._dispatch_domain_monitor("POST"):
            return
        try:
            with monitor_bridge.lock:
                self._handle_post()
        except (SharedStorageConflict, StorageConflict) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=409)
        except (SharedStorageError, StorageUnavailable) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=503)

    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json()
        except Exception:
            return self._send_json({"ok": False, "error": "JSON inválido"}, status=400)

        if path == "/api/cloud-config":
            if os.environ.get("VERCEL"):
                return self._send_json({"ok": False, "error": "Configure o Redis nas variáveis do projeto na Vercel."}, status=400)
            url = (body.get("url") or "").strip()
            token = (body.get("token") or "").strip()
            if not url or not token:
                return self._send_json({"ok": False, "error": "Informe URL e Token do Upstash Redis."}, status=400)
            res = store._cloud_cmd(url, token, ["PING"])
            if res != "PONG":
                return self._send_json({"ok": False, "error": "Falha ao conectar no Upstash Redis. Verifique URL e Token."}, status=400)
            os.environ["UPSTASH_REDIS_REST_URL"] = url
            os.environ["UPSTASH_REDIS_REST_TOKEN"] = token
            cur_data = store.load()
            store.save(cur_data)
            return self._send_json({"ok": True, "message": "Conectado ao Upstash Redis com sucesso!"})

        if path == "/api/domain-swap":
            store_name = (body.get("store_name") or "").strip()
            new_domain = (body.get("new_domain") or "").strip()
            reason = (body.get("reason") or "Troca de domínio").strip()
            date_str = (body.get("date") or now_iso()).strip()

            if not store_name or not new_domain:
                return self._send_json({"ok": False, "error": "Informe loja e novo domínio."}, status=400)

            data = store.load()
            catalog = data.setdefault("stores_catalog", {})
            st = catalog.setdefault(store_name, {
                "name": store_name,
                "current_domain": new_domain,
                "domain_swaps_count": 0,
                "status": "Ativa",
                "domain_history": [],
                "publications": [],
                "suspensions": [],
            })

            # Mark existing as anterior
            for hist in st.get("domain_history", []):
                if hist.get("status") == "atual":
                    hist["status"] = "anterior"

            st["current_domain"] = new_domain
            st["domain_swaps_count"] = st.get("domain_swaps_count", 0) + 1
            st.setdefault("domain_history", []).insert(0, {
                "domain": new_domain,
                "date": date_str[:10],
                "status": "atual",
                "reason": reason,
            })

            # Record event
            entry = {
                "id": f"rec_{uuid.uuid4().hex[:10]}",
                "event_type": "domain_swapped",
                "store_name": store_name,
                "domain": new_domain,
                "account_number": "—",
                "inactive_category": "",
                "reason": f"Novo domínio: {new_domain} ({reason})",
                "created_at": date_str,
                "month_key": date_str[:7],
                "source": "manual",
            }
            data.setdefault("records", []).insert(0, entry)
            store.save(data)
            return self._send_json({"ok": True, "store": st, "record": entry})

        if path == "/api/records":
            event_type = (body.get("event_type") or "ads_suspended").strip()
            store_name = (body.get("store_name") or "").strip()
            domain = (body.get("domain") or "").strip()
            account_number = normalize_google_ads_account(body.get("account_number") or "")
            inactive_category = (body.get("inactive_category") or "").strip()
            reason = (body.get("reason") or "").strip()
            custom_date = (body.get("created_at") or "").strip()
            also_mark_store_inactive = bool(body.get("also_mark_store_inactive", False))

            if not store_name:
                return self._send_json(
                    {"ok": False, "error": "Informe o nome da loja."}, status=400
                )

            created_dt = custom_date if len(custom_date) >= 7 else now_iso()
            if len(created_dt) == 10:
                created_dt = created_dt + " " + now_iso()[11:]
            month_key = created_dt[:7]

            default_reason = "Loja publicada"
            if event_type == "store_inactive":
                if inactive_category == "dmca":
                    default_reason = "DMCA"
                elif inactive_category == "domain_down":
                    default_reason = "Domínio caiu"
                else:
                    default_reason = "Suspensão / bloqueio no Google Ads"
            elif event_type == "ads_suspended":
                default_reason = "Suspensão / bloqueio no Google Ads"

            data = store.load()
            entry = {
                "id": f"rec_{uuid.uuid4().hex[:10]}",
                "event_type": event_type,
                "store_name": store_name,
                "domain": domain,
                "account_number": account_number if event_type == "ads_suspended" else "—",
                "inactive_category": inactive_category,
                "reason": reason or default_reason,
                "created_at": created_dt,
                "month_key": month_key,
                "source": body.get("source") or "manual",
            }
            data.setdefault("records", []).insert(0, entry)

            if event_type == "ads_suspended" and also_mark_store_inactive:
                inact_entry = {
                    "id": f"rec_{uuid.uuid4().hex[:10]}",
                    "event_type": "store_inactive",
                    "store_name": store_name,
                    "domain": domain,
                    "account_number": account_number,
                    "inactive_category": "ads_block",
                    "reason": f"Suspensão / bloqueio no Google Ads ({account_number})",
                    "created_at": created_dt,
                    "month_key": month_key,
                    "source": "manual",
                }
                data["records"].insert(0, inact_entry)
                update_catalog_from_event(data, inact_entry)

            update_catalog_from_event(data, entry)
            store.save(data)
            monitor_sync = monitor_bridge.sync_publications(data) if event_type == "store_published" else None
            return self._send_json(
                {"ok": True, "record": entry, "state": _build_state(month_key), "monitor_sync": monitor_sync}
            )

        if path == "/api/records/delete":
            rec_id = (body.get("id") or "").strip()
            if not rec_id:
                return self._send_json({"ok": False, "error": "ID ausente"}, status=400)
            data = store.load()
            records = data.get("records", [])
            deleted = next((r for r in records if r.get("id") == rec_id), None)
            if deleted is None:
                return self._send_json({"ok": False, "error": "Registro não encontrado."}, status=404)
            from shared_auth import current_user
            actor = current_user(self) or "usuário autenticado"
            timestamp = now_iso()
            trash_entry = dict(deleted, _deleted_at=timestamp, _deleted_by=actor, _trash_id="trash_" + uuid.uuid4().hex[:12])
            data.setdefault("deleted_records", []).insert(0, trash_entry)
            data.setdefault("deletion_audit", []).insert(0, {"action": "Movido para a lixeira", "record_id": rec_id, "store_name": deleted.get("store_name", ""), "at": timestamp, "by": actor})
            data["records"] = [r for r in records if r.get("id") != rec_id]
            store.save(data)
            return self._send_json({"ok": True, "records_count": len(data["records"])})

        if path == "/api/records/purge":
            from shared_auth import current_user
            from domain_monitor.monitor_engine import MASTER_EMAILS
            actor = current_user(self)
            if not actor or actor.casefold() not in MASTER_EMAILS:
                return self._send_json({"ok": False, "error": "Somente usuários master podem excluir definitivamente."}, status=403)
            trash_id = str(body.get("trash_id") or "").strip()
            if not trash_id or body.get("confirm") is not True:
                return self._send_json({"ok": False, "error": "Informe o registro e confirme a exclusão definitiva."}, status=400)
            data = store.load()
            entry = next((r for r in data.get("deleted_records", []) if r.get("_trash_id") == trash_id), None)
            if entry is None:
                return self._send_json({"ok": False, "error": "Registro não encontrado na lixeira."}, status=404)
            data["deleted_records"] = [r for r in data["deleted_records"] if r.get("_trash_id") != trash_id]
            data.setdefault("deletion_audit", []).insert(0, {
                "action": "Excluído definitivamente", "record_id": entry.get("id"),
                "store_name": entry.get("store_name", ""), "at": now_iso(), "by": actor})
            store.save(data)
            return self._send_json({"ok": True})

        if path == "/api/records/restore":
            trash_id = (body.get("trash_id") or "").strip()
            data = store.load()
            trashed = data.get("deleted_records", [])
            entry = next((r for r in trashed if r.get("_trash_id") == trash_id), None)
            if entry is None:
                return self._send_json({"ok": False, "error": "Registro não encontrado na lixeira."}, status=404)
            if any(r.get("id") == entry.get("id") for r in data.get("records", [])):
                return self._send_json({"ok": False, "error": "O registro já está no histórico."}, status=409)
            restored = {k: v for k, v in entry.items() if k not in ("_deleted_at", "_deleted_by", "_trash_id")}
            data.setdefault("records", []).insert(0, restored)
            data["deleted_records"] = [r for r in trashed if r.get("_trash_id") != trash_id]
            from shared_auth import current_user
            data.setdefault("deletion_audit", []).insert(0, {"action": "Restaurado", "record_id": restored.get("id"), "store_name": restored.get("store_name", ""), "at": now_iso(), "by": current_user(self) or "usuário autenticado"})
            store.save(data)
            return self._send_json({"ok": True})

        if path == "/api/records/update":
            rec_id = (body.get("id") or "").strip()
            if not rec_id:
                return self._send_json({"ok": False, "error": "ID ausente"}, status=400)
            data = store.load()
            records = data.get("records", [])
            found = False
            for r in records:
                if r.get("id") == rec_id:
                    if "event_type" in body:
                        r["event_type"] = body["event_type"]
                    if "store_name" in body:
                        r["store_name"] = body["store_name"]
                    if "domain" in body:
                        r["domain"] = body["domain"]
                    if "account_number" in body:
                        r["account_number"] = normalize_google_ads_account(body["account_number"])
                    if "inactive_category" in body:
                        r["inactive_category"] = body["inactive_category"]
                    if "reason" in body:
                        r["reason"] = body["reason"]
                    if "created_at" in body and body["created_at"]:
                        r["created_at"] = body["created_at"]
                        r["month_key"] = body["created_at"][:7]
                    found = True
                    break
            if not found:
                return self._send_json({"ok": False, "error": "Registro não encontrado"}, status=404)
            store.save(data)
            return self._send_json({"ok": True, "message": "Registro atualizado com sucesso"})

        if path == "/api/store-status":
            store_name = (body.get("store_name") or "").strip()
            new_status = (body.get("status") or "").strip()
            if not store_name or new_status not in ("Ativa", "Inativa"):
                return self._send_json({"ok": False, "error": "Informe a loja e o novo status (Ativa ou Inativa)."}, status=400)

            data = store.load()
            catalog = data.setdefault("stores_catalog", {})
            norm = normalize_store_name(store_name)
            st = None
            if norm in catalog:
                st = catalog[norm]
            else:
                for k, v in catalog.items():
                    if k.lower() == norm.lower():
                        st = v
                        break
            if not st:
                st = catalog.setdefault(norm, {
                    "name": norm,
                    "current_domain": "",
                    "domain_swaps_count": 0,
                    "status": new_status,
                    "domain_history": [],
                    "publications": [],
                    "suspensions": []
                })
            st["status"] = new_status
            st["status_verified_at"] = now_iso()
            st["status_source"] = "manual"
            store.save(data)
            return self._send_json({"ok": True, "store_name": norm, "status": new_status})

        if path == "/api/start-stores":
            month_key = (body.get("month_key") or "2026-11").strip()
            action = (body.get("action") or "").strip()
            store_name = (body.get("store_name") or "").strip()

            data = store.load()
            m_cfg = data.setdefault("months_config", {}).setdefault(month_key, {})
            current_list = m_cfg.setdefault("start_stores_list", [])

            if action == "add" and store_name:
                if not any(s.lower() == store_name.lower() for s in current_list):
                    current_list.append(store_name)
            elif action == "remove" and store_name:
                m_cfg["start_stores_list"] = [
                    s for s in current_list if s.lower() != store_name.lower()
                ]

            store.save(data)
            return self._send_json({"ok": True, "state": _build_state(month_key)})

        if path in ("/api/parse-and-import", "/api/whatsapp/import-paste", "/api/whatsapp/webhook"):
            raw_text = (body.get("raw_text") or body.get("text") or body.get("message") or "").strip()
            group_name = (body.get("group_name") or "").strip()
            sender_phone = (body.get("sender_phone") or "").strip()

            data = store.load()
            wa_cfg = data.get("whatsapp_config", {})
            target_group = (wa_cfg.get("target_group_name") or "EQUIPE KLOUBE ECOM").strip().lower()

            if path == "/api/whatsapp/webhook" and group_name:
                if target_group and target_group not in group_name.lower():
                    return self._send_json(
                        {
                            "ok": False,
                            "ignored": True,
                            "reason": f"Grupo '{group_name}' ignorado (alvo: '{wa_cfg.get('target_group_name')}')",
                        }
                    )

            allowed_phones = [p.strip() for p in wa_cfg.get("allowed_phones", []) if p.strip()]
            if path == "/api/whatsapp/webhook" and allowed_phones and sender_phone:
                if sender_phone not in allowed_phones:
                    return self._send_json(
                        {"ok": False, "ignored": True, "reason": "Remetente não autorizado."}
                    )

            monitor_stores = [s["name"] for s in store.get_monitor_domains_stores()]
            monitor_stores += list(data.get("stores_catalog", {}))
            monitor_stores += [v.get("name", "") for v in data.get("stores_catalog", {}).values() if isinstance(v, dict)]
            monitor_stores += [r.get("store_name", "") for r in data.get("records", [])]
            parsed_events = parse_whatsapp_chat_messages(raw_text, monitor_stores)

            if not parsed_events:
                return self._send_json(
                    {
                        "ok": False,
                        "events_imported": 0,
                        "error": "Nenhum evento válido: informe o nome da loja e, para suspensão do Google Ads, também o número da conta.",
                        "message": "Nenhum evento reconhecido na mensagem.",
                    }
                )

            records = data.setdefault("records", [])
            def event_key(ev):
                fields = ("event_type", "store_name", "domain", "account_number", "inactive_category", "created_at")
                return tuple(str(normalize_store_name(ev.get(k)) if k == "store_name" else ev.get(k) or "").strip().casefold() for k in fields)
            duplicate_candidates = records + data.get("deleted_records", [])
            seen = {event_key(ev) for ev in duplicate_candidates}
            imported_count = 0
            duplicates_count = 0
            for ev in parsed_events:
                key = event_key(ev)
                if key in seen or monitor_bridge.duplicate_record(duplicate_candidates + records, ev):
                    duplicates_count += 1
                    continue
                seen.add(key)
                ev_id = f"rec_{uuid.uuid4().hex[:10]}"
                ev["id"] = ev_id
                records.insert(0, ev)
                update_catalog_from_event(data, ev)
                imported_count += 1

            if imported_count:
                store.save(data)
            monitor_sync = monitor_bridge.sync_publications(data) if imported_count else None
            first_month = parsed_events[0].get("month_key", "")
            return self._send_json(
                {
                    "ok": True,
                    "events_imported": imported_count,
                    "imported_count": imported_count,
                    "duplicates_count": duplicates_count,
                    "monitor_sync": monitor_sync,
                    "state": _build_state(first_month),
                }
            )

        self.send_error(404)


def run():
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", PORT), BlocksHandler)
    print(f"Kloube Blocks Report Server running on http://localhost:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# Vercel's Python backend detection requires a top-level WSGI entrypoint.
def app(environ, start_response):
    from vercel_wsgi import application
    return application(environ, start_response)


if __name__ == "__main__":
    run()
