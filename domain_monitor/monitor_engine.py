import hashlib
import hmac
import secrets
import json
import os
import re
import socket
import ssl
import subprocess
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BRT_TZ = timezone(timedelta(hours=-3))

BASE_DIR = Path(__file__).resolve().parent
# Integrated app uses the root data.json, matching shared GitHub persistence.
# A standalone monitor without root data keeps its local fallback.
DATA_FILE = (BASE_DIR.parent / "data.json") if (BASE_DIR.parent / "data.json").exists() else BASE_DIR / "data.json"

ADMIN_EMAIL = "onobrecarlos@gmail.com"
MASTER_EMAILS = frozenset(("onobrecarlos@gmail.com", "pablin.business@gmail.com", "alertas.kloube@gmail.com"))
DEFAULT_SMTP_USER = "growthnobre@gmail.com"
DEFAULT_EMAIL = "onobrecarlos@gmail.com"

DEFAULT_USERS = {}

# Patterns that indicate a domain is live on DNS/HTTP but NOT pointing to an actual built website
PARKED_OR_UNCONFIGURED_PATTERNS = [
    r"domain is parked",
    r"dom[ií]nio estacionado",
    r"parked free.*courtesy of",
    r"sedoparking\.com",
    r"parkingcrew\.net",
    r"bodis\.com",
    r"hugedomains\.com",
    r"afternic\.com",
    r"dan\.com",
    r"compre este dom[ií]nio",
    r"buy this domain",
    r"this domain may be for sale",
    r"este dom[ií]nio est[aá] [aà] venda",
    r"future home of something quite cool",
    r"welcome to nginx!",
    r"apache2 ubuntu default page",
    r"apache2 debian default page",
    r"it works!.*apache",
    r"index of /</title>",
    r"cgi-sys/defaultwebpage\.cgi",
    r"sorria! seu dom[ií]nio foi registrado",
    r"seu dom[ií]nio foi registrado com sucesso",
    r"este site ainda n[aã]o foi publicado",
    r"website coming soon.*hostinger",
    r"default web site page",
    r"deployment_not_found",
    r"there isn't a github pages site here",
    r"404: this page could not be found.*vercel",
    r"site not found.*netlify",
    r"heroku \| no such app",
]


def now_iso() -> str:
    return datetime.now(BRT_TZ).strftime("%Y-%m-%d %H:%M:%S")


def normalize_domain_input(raw: str) -> tuple[str, str]:
    """
    Returns (clean_domain, check_url).
    Example: 'kloube.com.br' -> ('kloube.com.br', 'https://kloube.com.br')
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("O domínio não pode estar vazio.")

    if not re.match(r"^https?://", raw, re.IGNORECASE):
        url = "https://" + raw
    else:
        url = raw

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.strip().lower()
    if not host or "." not in host:
        raise ValueError(f"Domínio inválido: {raw}")

    return host, url


def check_single_domain(domain_input: str, expected_redirect: str = "") -> dict:
    """
    Performs a non-intrusive read-only verification of a domain:
    1. DNS Resolution check
    2. HTTPS (and fallback HTTP) connection + redirect following
    3. Status code check (2xx/3xx)
    4. Content & Parking / Unconfigured hosting check
    """
    checked_at = now_iso()
    try:
        host, initial_url = normalize_domain_input(domain_input)
    except ValueError as e:
        return {
            "status": "offline",
            "status_label": "Fora do Ar",
            "reason": str(e),
            "http_code": None,
            "final_url": "",
            "ip_address": "",
            "response_time_ms": 0,
            "checked_at": checked_at,
            "page_title": "",
        }

    # 1. DNS Check (also try www.<host> if apex fails or has no A record, with retry on transient DNS glitch)
    ip_address = ""
    resolved_host = host
    for _dns_attempt in range(2):
        try:
            ip_address = socket.gethostbyname(host)
            break
        except socket.gaierror:
            if not host.startswith("www."):
                try:
                    ip_address = socket.gethostbyname("www." + host)
                    resolved_host = "www." + host
                    initial_url = initial_url.replace("://" + host, "://www." + host, 1)
                    break
                except socket.gaierror:
                    pass
            if _dns_attempt == 0:
                time.sleep(0.5)

    if not ip_address:
        return {
            "status": "offline",
            "status_label": "Fora do Ar",
            "reason": "DNS não encontrado ou sem apontamento ativo (NXDOMAIN)",
            "http_code": None,
            "final_url": "",
            "ip_address": "",
            "response_time_ms": 0,
            "checked_at": checked_at,
            "page_title": "",
        }

    # 2. HTTP/HTTPS Request with redirect tracking
    urls_to_try = [initial_url]
    if initial_url.startswith("https://"):
        urls_to_try.append("http://" + initial_url[len("https://") :])
        if not resolved_host.startswith("www."):
            urls_to_try.append(f"https://www.{resolved_host}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }

    last_error = "Não foi possível conectar ao servidor"
    last_code = None

    for candidate_url in urls_to_try:
        start_t = time.perf_counter()
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(candidate_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                elapsed_ms = int((time.perf_counter() - start_t) * 1000)
                http_code = resp.getcode()
                final_url = resp.geturl()
                raw_bytes = resp.read(96_000)  # Read up to 96KB to inspect title/parking
                body = raw_bytes.decode("utf-8", errors="ignore")

                # Extract title if present
                title_match = re.search(
                    r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL
                )
                page_title = (
                    re.sub(r"\s+", " ", title_match.group(1)).strip()
                    if title_match
                    else ""
                )

                # Check if HTTP code is error
                if http_code >= 400:
                    return {
                        "status": "offline",
                        "status_label": "Fora do Ar",
                        "reason": f"Erro HTTP {http_code} ao acessar o site",
                        "http_code": http_code,
                        "final_url": final_url,
                        "ip_address": ip_address,
                        "response_time_ms": elapsed_ms,
                        "checked_at": checked_at,
                        "page_title": page_title,
                    }

                # Check for parked / unconfigured / empty site
                body_clean = body.strip()
                if len(body_clean) < 40 and not re.search(
                    r"meta[^>]+http-equiv=[\"']?refresh", body_clean, re.I
                ):
                    return {
                        "status": "offline",
                        "status_label": "Fora do Ar",
                        "reason": "Domínio respondeu, mas a página está em branco / sem site configurado",
                        "http_code": http_code,
                        "final_url": final_url,
                        "ip_address": ip_address,
                        "response_time_ms": elapsed_ms,
                        "checked_at": checked_at,
                        "page_title": page_title,
                    }

                for pattern in PARKED_OR_UNCONFIGURED_PATTERNS:
                    if re.search(pattern, body, re.IGNORECASE):
                        return {
                            "status": "offline",
                            "status_label": "Fora do Ar",
                            "reason": "Domínio sem direcionamento para site ativo (página padrão/estacionado)",
                            "http_code": http_code,
                            "final_url": final_url,
                            "ip_address": ip_address,
                            "response_time_ms": elapsed_ms,
                            "checked_at": checked_at,
                            "page_title": page_title,
                        }

                # Check meta-refresh redirect to parking services
                meta_refresh = re.search(
                    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"'][^\"']*url=([^\"'>\s]+)",
                    body,
                    re.IGNORECASE,
                )
                if meta_refresh:
                    refresh_target = meta_refresh.group(1).lower()
                    if any(
                        p in refresh_target
                        for p in ["sedoparking", "parkingcrew", "bodis", "hugedomains", "cgi-sys/defaultwebpage"]
                    ):
                        return {
                            "status": "offline",
                            "status_label": "Fora do Ar",
                            "reason": f"Redirecionando para página de domínio estacionado ({refresh_target})",
                            "http_code": http_code,
                            "final_url": final_url,
                            "ip_address": ip_address,
                            "response_time_ms": elapsed_ms,
                            "checked_at": checked_at,
                            "page_title": page_title,
                        }

                # Optional expected target verification
                if expected_redirect and expected_redirect.strip():
                    exp = expected_redirect.strip().lower()
                    if exp not in final_url.lower() and exp not in body.lower():
                        return {
                            "status": "offline",
                            "status_label": "Fora do Ar",
                            "reason": f"Direcionamento incorreto (esperado: {expected_redirect}, atual: {final_url})",
                            "http_code": http_code,
                            "final_url": final_url,
                            "ip_address": ip_address,
                            "response_time_ms": elapsed_ms,
                            "checked_at": checked_at,
                            "page_title": page_title,
                        }

                return {
                    "status": "online",
                    "status_label": "Ativo",
                    "reason": f"Direcionamento ativo ({http_code} OK)",
                    "http_code": http_code,
                    "final_url": final_url,
                    "ip_address": ip_address,
                    "response_time_ms": elapsed_ms,
                    "checked_at": checked_at,
                    "page_title": page_title or host,
                }

        except urllib.error.HTTPError as e:
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)
            last_code = e.code
            if e.code == 423:
                return {
                    "status": "offline",
                    "status_label": "Fora do Ar",
                    "reason": "Loja bloqueada ou inativa na plataforma (HTTP 423 · This store is unavailable)",
                    "http_code": 423,
                    "final_url": candidate_url,
                    "ip_address": ip_address,
                    "response_time_ms": elapsed_ms,
                    "checked_at": checked_at,
                    "page_title": "This store is unavailable",
                }
            if e.code == 429:
                try:
                    err_body = e.read(32_000).decode("utf-8", errors="ignore")
                except Exception:
                    err_body = ""
                if "this store is unavailable" in err_body.lower() or "sorry, this store is currently unavailable" in err_body.lower():
                    return {
                        "status": "offline",
                        "status_label": "Fora do Ar",
                        "reason": "Loja bloqueada ou inativa na plataforma (This store is unavailable)",
                        "http_code": 423,
                        "final_url": candidate_url,
                        "ip_address": ip_address,
                        "response_time_ms": elapsed_ms,
                        "checked_at": checked_at,
                        "page_title": "This store is unavailable",
                    }
                # Em lotes de lojas Shopify no mesmo IP, o Cloudflare/Shopify retorna 429 Too Many Requests
                # na home, mas /robots.txt responde 200 OK se a loja estiver ativa ou 423 se estiver bloqueada.
                robots_url = candidate_url.rstrip("/") + "/robots.txt"
                try:
                    req_rob = urllib.request.Request(robots_url, headers=headers, method="GET")
                    with urllib.request.urlopen(req_rob, timeout=10, context=ctx) as rob_resp:
                        if rob_resp.getcode() == 200:
                            return {
                                "status": "online",
                                "status_label": "Ativo",
                                "reason": "Direcionamento ativo (200 OK)",
                                "http_code": 200,
                                "final_url": candidate_url,
                                "ip_address": ip_address,
                                "response_time_ms": elapsed_ms,
                                "checked_at": checked_at,
                                "page_title": host,
                            }
                except urllib.error.HTTPError as rob_err:
                    if rob_err.code == 423:
                        return {
                            "status": "offline",
                            "status_label": "Fora do Ar",
                            "reason": "Loja bloqueada ou inativa na plataforma (HTTP 423 · This store is unavailable)",
                            "http_code": 423,
                            "final_url": candidate_url,
                            "ip_address": ip_address,
                            "response_time_ms": elapsed_ms,
                            "checked_at": checked_at,
                            "page_title": "This store is unavailable",
                        }
                    if rob_err.code == 429:
                        return {
                            "status": "online",
                            "status_label": "Ativo",
                            "reason": "Direcionamento ativo (200 OK · Proteção Anti-Bot 429)",
                            "http_code": 200,
                            "final_url": candidate_url,
                            "ip_address": ip_address,
                            "response_time_ms": elapsed_ms,
                            "checked_at": checked_at,
                            "page_title": host,
                        }
                except Exception:
                    pass
                return {
                    "status": "online",
                    "status_label": "Ativo",
                    "reason": "Direcionamento ativo (200 OK · Rate Limit 429)",
                    "http_code": 200,
                    "final_url": candidate_url,
                    "ip_address": ip_address,
                    "response_time_ms": elapsed_ms,
                    "checked_at": checked_at,
                    "page_title": host,
                }
            if e.code in (404, 410) or e.code >= 500:
                last_error = f"Erro HTTP {e.code} ({e.reason})"
            else:
                try:
                    err_body = e.read(32_000).decode("utf-8", errors="ignore")
                except Exception:
                    err_body = ""
                for pattern in PARKED_OR_UNCONFIGURED_PATTERNS:
                    if re.search(pattern, err_body, re.IGNORECASE):
                        return {
                            "status": "offline",
                            "status_label": "Fora do Ar",
                            "reason": f"Domínio sem site publicado (HTTP {e.code})",
                            "http_code": e.code,
                            "final_url": candidate_url,
                            "ip_address": ip_address,
                            "response_time_ms": elapsed_ms,
                            "checked_at": checked_at,
                            "page_title": "",
                        }
                last_error = f"Resposta HTTP {e.code} ({e.reason})"
        except ssl.SSLError as e:
            last_error = f"Erro de Certificado SSL ({e.reason or 'SSL inválido/expirado'})"
        except urllib.error.URLError as e:
            reason_str = str(e.reason)
            if "SSL" in reason_str or "CERTIFICATE" in reason_str.upper():
                last_error = "Certificado SSL inválido ou expirado"
            elif "timed out" in reason_str.lower():
                last_error = "Tempo limite esgotado (Timeout)"
            elif "refused" in reason_str.lower():
                last_error = "Conexão recusada pelo servidor"
            else:
                last_error = f"Falha de conexão: {reason_str}"
        except Exception as e:
            last_error = f"Erro ao verificar: {str(e)}"

    return {
        "status": "offline",
        "status_label": "Fora do Ar",
        "reason": last_error,
        "http_code": last_code,
        "final_url": "",
        "ip_address": ip_address,
        "response_time_ms": 0,
        "checked_at": checked_at,
        "page_title": "",
    }


def send_windows_desktop_notification(title: str, message: str) -> bool:
    """
    Triggers a native Windows desktop notification (System Tray Balloon + Toast sound)
    using PowerShell so it requires zero external Python packages.
    """
    safe_title = title.replace("'", "''").replace('"', '`"')
    safe_msg = message.replace("'", "''").replace('"', '`"')

    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Media.SystemSounds]::Exclamation.Play()
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Warning
$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Error
$notify.BalloonTipTitle = '{safe_title}'
$notify.BalloonTipText = '{safe_msg}'
$notify.Visible = $true
$notify.ShowBalloonTip(8000)
Start-Sleep -Seconds 6
$notify.Dispose()
"""
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-Command",
                ps_script,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:
        return False


def parse_email_recipients(raw_emails: str) -> list[str]:
    raw = (raw_emails or DEFAULT_EMAIL).strip()
    items = [e.strip() for e in re.split(r"[,;]+", raw) if e.strip()]
    return items or ["onobrecarlos@gmail.com"]


def send_email_notification(
    subject: str,
    html_body: str,
    text_body: str,
    settings: dict,
) -> tuple[bool, str]:
    return False, "Envios de e-mail desativados a pedido do usuário."
    """
    Sends an email alert to all emails in settings['notify_email']
    (defaults to onobrecarlos@gmail.com, alertas.kloube@gmail.com).
    Supports:
    1. Configured SMTP (Gmail / custom SMTP)
    2. FormSubmit AJAX endpoint fallback if SMTP is not yet configured
    """
    recipients = parse_email_recipients(settings.get("notify_email") or DEFAULT_EMAIL)
    recipient_display = ", ".join(recipients)
    smtp_user = (settings.get("smtp_user") or DEFAULT_SMTP_USER).strip()
    smtp_pass = (settings.get("smtp_pass") or os.environ.get("KLOUBE_SMTP_PASS") or "").strip().replace(" ", "")
    smtp_host = (settings.get("smtp_host") or "smtp.gmail.com").strip()
    smtp_port = int(settings.get("smtp_port") or 587)

    if smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"Kloube Domain Monitor <{smtp_user}>"
            msg["To"] = recipient_display

            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, recipients, msg.as_string())

            return True, f"E-mail enviado via SMTP para {recipient_display}"
        except Exception as e:
            return False, f"Erro SMTP: {str(e)}"

    # Fallback: try FormSubmit.co AJAX endpoint for each recipient
    sent_any = False
    last_msg = "Ative o SMTP nas configurações para envio direto."
    for addr in recipients:
        try:
            payload = json.dumps(
                {
                    "_subject": subject,
                    "_template": "table",
                    "Alerta": subject,
                    "Detalhes": text_body,
                    "Horario": now_iso(),
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"https://formsubmit.co/ajax/{urllib.parse.quote(addr)}",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Origin": "http://localhost:8765",
                    "Referer": "http://localhost:8765/",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if str(resp_data.get("success")).lower() == "true":
                    sent_any = True
                else:
                    last_msg = resp_data.get("message", last_msg)
        except Exception:
            pass

    if sent_any:
        return True, f"E-mail disparado para {recipient_display}"
    return (
        False,
        f"Aviso de e-mail ({recipient_display}): {last_msg}",
    )


def hash_account_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def matches_account_password(password: str, stored: str) -> bool:
    if not isinstance(stored, str):
        return False
    if not stored.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(password.encode(), stored.encode())
    try:
        _, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
        return hmac.compare_digest(actual.hex(), digest)
    except ValueError:
        return False


class DomainStore:
    def __init__(self, path: Path = DATA_FILE):
        self.path = path
        self.tmp_path = Path("/tmp/kloube_data.json")
        self.lock = threading.Lock()
        from shared_github import GitHubFile
        self.shared_file = GitHubFile("data.json")
        self.reset_codes: dict[str, tuple[str, float]] = {}
        self._gh_cache_data: dict | None = None
        self._gh_cache_sha: str | None = None
        self._gh_cache_ts: float = 0.0
        self._ensure_file()

    def _github_config(self) -> tuple[str, str, str]:
        token = (
            os.environ.get("KLOUBE_GITHUB_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or ""
        ).strip()
        repo = (os.environ.get("KLOUBE_GITHUB_REPO") or os.environ.get("GITHUB_REPO") or "").strip()
        if not repo:
            owner = (os.environ.get("VERCEL_GIT_REPO_OWNER") or "").strip()
            slug = (os.environ.get("VERCEL_GIT_REPO_SLUG") or "").strip()
            if owner and slug:
                repo = f"{owner}/{slug}"
        branch = (
            os.environ.get("KLOUBE_GITHUB_BRANCH")
            or os.environ.get("VERCEL_GIT_COMMIT_REF")
            or "main"
        ).strip()
        # Only use remote HTTP GitHub sync when running on Vercel or explicitly configured,
        # not inside GitHub Actions where local checkout file is used directly.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            return "", "", branch
        return token, repo, branch

    def _fetch_from_github(self, force: bool = False) -> dict | None:
        import base64
        token, repo, branch = self._github_config()
        if not token or not repo:
            return None
        now = time.time()
        if not force and self._gh_cache_data is not None and (now - self._gh_cache_ts) < 4.0:
            return json.loads(json.dumps(self._gh_cache_data))
        url = f"https://api.github.com/repos/{repo}/contents/data.json?ref={urllib.parse.quote(branch)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Kloube-Domain-Monitor/1.0",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
                self._gh_cache_sha = payload.get("sha")
                raw_b64 = payload.get("content", "")
                decoded = base64.b64decode(raw_b64).decode("utf-8")
                parsed = json.loads(decoded)
                self._gh_cache_data = parsed
                self._gh_cache_ts = now
                self._write_local_raw(decoded)
                return json.loads(decoded)
        except Exception:
            return None

    def _push_to_github(self, content: str) -> bool:
        import base64
        token, repo, branch = self._github_config()
        if not token or not repo:
            return False
        if not self._gh_cache_sha:
            self._fetch_from_github(force=True)
        url = f"https://api.github.com/repos/{repo}/contents/data.json"
        b64_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

        for attempt in range(2):
            body_dict = {
                "message": f"chore(sync): update data.json ({now_iso()}) [skip ci]",
                "content": b64_content,
                "branch": branch,
            }
            if self._gh_cache_sha:
                body_dict["sha"] = self._gh_cache_sha
            req = urllib.request.Request(
                url,
                data=json.dumps(body_dict).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "Kloube-Domain-Monitor/1.0",
                },
                method="PUT",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    content_info = res_data.get("content") or {}
                    if content_info.get("sha"):
                        self._gh_cache_sha = content_info["sha"]
                    self._gh_cache_data = json.loads(content)
                    self._gh_cache_ts = time.time()
                    return True
            except urllib.error.HTTPError as e:
                if e.code in (409, 422) and attempt == 0:
                    self._fetch_from_github(force=True)
                    continue
                return False
            except Exception:
                return False
        return False

    def _active_read_path(self) -> Path:
        if os.environ.get("VERCEL") and self.tmp_path.exists():
            return self.tmp_path
        return self.path

    def _write_local_raw(self, content: str):
        try:
            self.path.write_text(content, encoding="utf-8")
        except OSError:
            self.tmp_path.parent.mkdir(parents=True, exist_ok=True)
            self.tmp_path.write_text(content, encoding="utf-8")

    def _write_raw(self, content: str):
        from shared_github import enabled
        if enabled():
            self.shared_file.save(json.loads(content))
            return
        if os.environ.get("VERCEL"):
            raise OSError("Configure o armazenamento compartilhado antes de salvar.")
        self._write_local_raw(content)

    def _ensure_file(self):
        if not self.path.exists() and not self.tmp_path.exists():
            initial = {
                "settings": {
                    "notify_email": DEFAULT_EMAIL,
                    "desktop_notifications": False,
                    "email_notifications": True,
                    "check_interval_seconds": 1800,
                    "smtp_host": "smtp.gmail.com",
                    "smtp_port": 587,
                    "smtp_user": DEFAULT_SMTP_USER,
                    "smtp_pass": "",
                },
                "domains": [],
                "alerts": [],
                "users": dict(DEFAULT_USERS),
            }
            self._write_local_raw(json.dumps(initial, indent=2, ensure_ascii=False))

    def load(self) -> dict:
        from shared_github import enabled
        if enabled():
            return self.shared_file.load()
        if os.environ.get("VERCEL"):
            raise OSError("Configure o GitHub compartilhado antes de usar o app online.")
        with self.lock:
            gh_data = self._fetch_from_github()
            if isinstance(gh_data, dict):
                data = gh_data
            else:
                target = self._active_read_path()
                try:
                    data = json.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    self._ensure_file()
                    target = self._active_read_path()
                    data = json.loads(target.read_text(encoding="utf-8"))
            users = data.get("users")
            if not isinstance(users, dict):
                data["users"] = dict(DEFAULT_USERS)
                self._write_raw(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                changed = False
                for u_email, u_pass in DEFAULT_USERS.items():
                    if u_email not in users:
                        users[u_email] = u_pass
                        changed = True
                if changed:
                    self._write_raw(json.dumps(data, indent=2, ensure_ascii=False))
            return data

    def verify_login(self, email: str, password: str) -> tuple[bool, str]:
        clean_email = (email or "").strip().lower()
        clean_pass = (password or "").strip()
        if not clean_email or not clean_pass:
            return False, "Informe o e-mail e a senha."
        data = self.load()
        if clean_email in data.get("disabled_users", []):
            return False, "Este usuário não possui mais acesso ao painel."
        configured_password = os.environ.get("KLOUBE_ADMIN_PASSWORD", "")
        if configured_password and clean_email == ADMIN_EMAIL.lower() and clean_email not in data.get("store_managed_users", []):
            import hmac
            if hmac.compare_digest(clean_pass.encode(), configured_password.encode()):
                return True, ADMIN_EMAIL
            return False, "Senha incorreta."
        data = self.load()
        users = data.get("users", DEFAULT_USERS)
        matched_key = next(
            (k for k in users.keys() if k.lower() == clean_email), None
        )
        if not matched_key:
            return False, "Este e-mail não possui acesso ao painel Kloube."
        if not matches_account_password(clean_pass, users[matched_key]):
            return False, "Senha incorreta. Tente novamente ou clique em 'Esqueci minha senha'."
        return True, matched_key

    def send_password_reset_code(self, email: str) -> tuple[bool, str]:
        import random
        clean_email = (email or "").strip().lower()
        if not clean_email:
            return False, "Informe o seu e-mail."
        data = self.load()
        users = data.get("users", DEFAULT_USERS)
        matched_key = next(
            (k for k in users.keys() if k.lower() == clean_email), None
        )
        if not matched_key:
            return False, "Este e-mail não está cadastrado no painel Kloube."

        code = f"{random.randint(100000, 999999)}"
        self.reset_codes[clean_email] = (code, time.time() + 900)  # 15 min

        settings = dict(data.get("settings", {}))
        settings["notify_email"] = matched_key
        html_body = f"""
        <div style="font-family:Segoe UI,Arial,sans-serif;background:#090D14;color:#ffffff;padding:28px;border-radius:14px;border:1px solid #243044;">
            <h2 style="margin:0 0 12px 0;color:#3B82F6;">🔐 Código de Recuperação de Senha — Kloube</h2>
            <p style="color:#cbd5e1;font-size:15px;">Use o código de 6 dígitos abaixo para redefinir a senha da sua conta <strong>{matched_key}</strong>:</p>
            <div style="margin:22px 0;padding:18px;background:#111824;border:1px solid #3B82F6;border-radius:10px;text-align:center;font-size:28px;font-weight:bold;letter-spacing:6px;color:#ffffff;">
                {code}
            </div>
            <p style="font-size:12px;color:#64748b;margin-top:20px;">Este código expira em 15 minutos.</p>
        </div>
        """
        text_body = f"Seu código para redefinir a senha no painel Kloube é: {code}"
        sent, info = send_email_notification(
            subject=f"[Kloube] Código para redefinir senha: {code}",
            html_body=html_body,
            text_body=text_body,
            settings=settings,
        )
        if not sent:
            return False, f"Não foi possível enviar o código por e-mail ({info})."
        return True, f"Enviamos um código de 6 dígitos para {matched_key}."

    def change_user_password(
        self, email: str, new_password: str, code: str = "", bypass_code: bool = False
    ) -> tuple[bool, str]:
        clean_email = (email or "").strip().lower()
        clean_pass = (new_password or "").strip()
        clean_code = (code or "").strip()
        if not clean_email:
            return False, "Informe o e-mail da conta."
        if len(clean_pass) < 6:
            return False, "A nova senha deve ter pelo menos 6 caracteres."
        data = self.load()
        users = data.setdefault("users", dict(DEFAULT_USERS))
        matched_key = next(
            (k for k in users.keys() if k.lower() == clean_email), None
        )
        if not matched_key:
            return False, f"O e-mail '{email}' não está cadastrado no painel."

        if not bypass_code:
            saved = self.reset_codes.get(clean_email)
            if not saved:
                return False, "Solicite primeiro o código de verificação no seu e-mail."
            saved_code, expires_at = saved
            if time.time() > expires_at:
                return False, "O código expirou. Solicite um novo código."
            if clean_code != saved_code:
                return False, "Código de verificação incorreto."
            self.reset_codes.pop(clean_email, None)

        users[matched_key] = hash_account_password(clean_pass)
        self.save(data)
        return True, matched_key

    def create_user_account(
        self, requester_email: str, new_email: str, new_password: str
    ) -> tuple[bool, str]:
        req = (requester_email or "").strip().lower()
        if req not in MASTER_EMAILS:
            return False, "Apenas administradores master podem criar novas contas."
        target_email = (new_email or "").strip().lower()
        target_pass = (new_password or "").strip()
        if not target_email or "@" not in target_email:
            return False, "Informe um e-mail válido para a nova conta."
        if len(target_pass) < 6:
            return False, "A senha deve ter pelo menos 6 caracteres."
        data = self.load()
        users = data.setdefault("users", dict(DEFAULT_USERS))
        if any(key.lower() == target_email for key in users):
            return False, "Este e-mail já está cadastrado."
        users[target_email] = hash_account_password(target_pass)
        data["disabled_users"] = [email for email in data.get("disabled_users", []) if email != target_email]
        data.setdefault("store_managed_users", []).append(target_email)
        data.setdefault("user_audit", []).insert(0, {"action": "Usuário criado", "email": target_email, "by": req, "at": now_iso()})
        self.save(data)
        return True, target_email

    def save(self, data: dict):
        from shared_github import enabled
        with self.lock:
            if enabled():
                self.shared_file.save(data)
            else:
                self._write_raw(json.dumps(data, indent=2, ensure_ascii=False))
        from monitor_bridge import sync
        sync(data.get("domains", []))

    def trigger_alert(self, domain_obj: dict, check_result: dict, force: bool = False):
        data = self.load()
        settings = data.get("settings", {})
        recipient = settings.get("notify_email", DEFAULT_EMAIL)

        domain_name = domain_obj.get("domain", "")
        label = domain_obj.get("name") or domain_name
        reason = check_result.get("reason", "Domínio inacessível ou sem direcionamento")
        checked_at = check_result.get("checked_at", now_iso())

        # Evitar envio repetido em menos de 1 hora para o mesmo domínio
        if not force:
            interval = max(3600, int(settings.get("check_interval_seconds", 3600)))
            now_ts = time.time()
            for existing_alert in data.get("alerts", []):
                if existing_alert.get("domain", "").lower() == domain_name.lower():
                    ts_str = existing_alert.get("timestamp", "")
                    try:
                        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT_TZ)
                        if (now_ts - dt.timestamp()) < interval:
                            return existing_alert
                    except Exception:
                        pass
                    break

        title = f"🚨 KLOUBE ALERTA: {domain_name} Fora do Ar!"
        msg_text = (
            f"Domínio: {domain_name} ({label})\n"
            f"Status: FORA DO AR\n"
            f"Motivo: {reason}\n"
            f"Horário: {checked_at}\n"
            f"Notificação para: {recipient}"
        )

        desktop_sent = False
        if (
            os.environ.get("KLOUBE_ENABLE_PC_MONITOR") == "1"
            and (settings.get("desktop_notifications", False) or force)
        ):
            desktop_sent = send_windows_desktop_notification(
                title, f"{domain_name}: {reason}"
            )

        email_sent = False
        email_info = "Desativado"
        if settings.get("email_notifications", True) or force:
            html_body = f"""
            <div style="font-family:Segoe UI,Arial,sans-serif;background:#04091e;color:#ffffff;padding:28px;border-radius:14px;border:1px solid #1e3a8a;">
                <h2 style="margin:0 0 12px 0;color:#ef4444;">🚨 Alerta de Domínio Fora do Ar — Kloube Monitor</h2>
                <p style="color:#cbd5e1;font-size:15px;">O monitor detectou uma falha de disponibilidade ou direcionamento no domínio abaixo:</p>
                <table style="width:100%;border-collapse:collapse;margin:18px 0;background:#091436;border-radius:10px;overflow:hidden;">
                    <tr><td style="padding:10px 14px;color:#94a3b8;">Cliente / Identificação:</td><td style="padding:10px 14px;font-weight:bold;color:#fff;">{label}</td></tr>
                    <tr><td style="padding:10px 14px;color:#94a3b8;">Domínio:</td><td style="padding:10px 14px;font-weight:bold;color:#60a5fa;">{domain_name}</td></tr>
                    <tr><td style="padding:10px 14px;color:#94a3b8;">Status:</td><td style="padding:10px 14px;font-weight:bold;color:#ef4444;">● FORA DO AR</td></tr>
                    <tr><td style="padding:10px 14px;color:#94a3b8;">Diagnóstico:</td><td style="padding:10px 14px;color:#fca5a5;">{reason}</td></tr>
                    <tr><td style="padding:10px 14px;color:#94a3b8;">Verificado em:</td><td style="padding:10px 14px;color:#e2e8f0;">{checked_at}</td></tr>
                </table>
                <p style="font-size:12px;color:#64748b;margin-top:20px;">Kloube Domain Monitor • Monitoramento Automático de Direcionamento</p>
            </div>
            """
            email_sent, email_info = send_email_notification(
                subject=f"[Kloube Monitor] 🚨 Domínio Fora do Ar: {domain_name}",
                html_body=html_body,
                text_body=msg_text,
                settings=settings,
            )

        alert_entry = {
            "id": f"al_{int(time.time() * 1000)}",
            "domain": domain_name,
            "name": label,
            "reason": reason,
            "timestamp": checked_at,
            "desktop_sent": desktop_sent,
            "email_sent": email_sent,
            "email_recipient": recipient,
            "email_info": email_info,
        }

        data = self.load()
        alerts = data.get("alerts", [])
        alerts.insert(0, alert_entry)
        data["alerts"] = alerts[:500]
        self.save(data)
        return alert_entry
