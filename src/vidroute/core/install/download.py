"""Resumable, verified downloads restricted to an allow-list of domains.

* Every URL — including each redirect hop — must be on the manifest's
  ``allowed_domains`` list (suffix match).
* Partial data is kept in ``$VIDROUTE_HOME/cache/downloads/<sha256>.part`` and
  resumed with HTTP ``Range``; partial files are keyed by the expected hash, so
  different mirrors serving the same bytes share progress.
* A file is moved into place (``os.replace``) only after its size and SHA-256
  match; a mismatch deletes the partial file and tries the next mirror.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from vidroute import __version__
from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.errors import Cancelled, RunFailed
from vidroute.core.util.hashing import sha256_file
from vidroute.core.util.jsonio import write_json_atomic

CHUNK = 1024 * 256
USER_AGENT = f"vidroute/{__version__} (+https://github.com/wblearner-mini/vidroute)"


class DomainNotAllowed(RunFailed):
    default_code = "domain_not_allowed"


class ChecksumMismatch(RunFailed):
    default_code = "checksum_mismatch"


def host_allowed(url: str, allowed: Iterable[str]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    return bool(host) and any(host == d or host.endswith("." + d) for d in allowed)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed: tuple[str, ...]) -> None:
        super().__init__()
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not host_allowed(newurl, self.allowed):
            raise DomainNotAllowed(
                f"Download redirected to a domain that is not allowed: {urllib.parse.urlsplit(newurl).hostname}",
                hint="Check models/manifest.yaml allowed_domains or try another --mirror",
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and req.has_header("Range"):
            new.add_header("Range", req.get_header("Range"))
        return new


@dataclass
class DownloadResult:
    path: Path
    url: str
    resumed_from: int
    bytes_downloaded: int
    seconds: float


class TooSlow(RunFailed):
    """The source works but crawls."""


class Downloader:
    def __init__(
        self,
        allowed_domains: Iterable[str],
        ctx: Ctx | None = None,
        retries: int = 3,
        timeout: float = 30.0,
        backoff: float = 1.5,
        part_dir: Path | None = None,
        min_speed: float = 50 * 1024,
        speed_window: float = 30.0,
    ) -> None:
        self.allowed = tuple(d.lower() for d in allowed_domains)
        self.ctx = ctx or Ctx()
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self.part_dir = part_dir or paths.downloads_dir()
        # A source slower than min_speed (bytes/s) over speed_window seconds is left for the next one: a trickle never
        # hits the socket timeout, and 240 MB at 20 KB/s takes hours. 0 turns the check off (see install_model).
        self.min_speed = min_speed
        self.speed_window = speed_window
        self.slow_seen = False
        self.opener = urllib.request.build_opener(_CheckedRedirects(self.allowed))

    # ------------------------------------------------------------- public
    def fetch(self, urls: list[str], dest: Path, sha256: str, size: int | None = None, label: str = "") -> DownloadResult:
        sha256 = sha256.lower()
        label = label or dest.name
        if dest.is_file() and (size is None or dest.stat().st_size == size) and sha256_file(dest) == sha256:
            return DownloadResult(dest, "local", 0, 0, 0.0)
        errors: list[str] = []
        for url in urls:
            if not host_allowed(url, self.allowed):
                errors.append(f"{url}: domain not allowed")
                continue
            for attempt in range(1, self.retries + 1):
                self.ctx.check_cancelled()
                try:
                    return self._fetch_one(url, dest, sha256, size, label)
                except (DomainNotAllowed, Cancelled):
                    raise
                except ChecksumMismatch as exc:
                    errors.append(f"{url}: {exc.message}")
                    break  # bad bytes from this mirror: do not retry it
                except TooSlow as exc:
                    self.slow_seen = True
                    errors.append(f"{url}: {exc.message}")
                    self.ctx.progress("download", None, None, f"{label}: {exc.message}, trying the next source")
                    break  # partial data is kept: the next mirror resumes it
                except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException, RunFailed) as exc:
                    msg = getattr(exc, "message", None) or str(getattr(exc, "reason", exc))
                    errors.append(f"{url} (attempt {attempt}): {msg}")
                    if attempt < self.retries:
                        time.sleep(self.backoff * attempt)
        raise RunFailed(
            f"Could not download {label} from any mirror.",
            code="download_failed",
            hint="vidroute install --mirror auto  (re-run to resume; partial data is kept)",
            data={"errors": errors[-6:]},
        )

    # ------------------------------------------------------------ internals
    def _paths(self, sha256: str) -> tuple[Path, Path]:
        paths.ensure_dir(self.part_dir)
        part = self.part_dir / f"{sha256}.part"
        return part, part.with_name(part.name + ".json")

    def _fetch_one(self, url: str, dest: Path, sha256: str, size: int | None, label: str) -> DownloadResult:
        part, meta_path = self._paths(sha256)
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have > size:
            part.unlink()
            have = 0
        start = time.monotonic()
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and size is not None and have == size:
                resp = None  # already complete
            else:
                raise RunFailed(f"HTTP {exc.code} {exc.reason}", code="http_error") from exc
        resumed_from = have
        downloaded = 0
        if resp is not None:
            with resp:
                final_url = resp.geturl()
                if not host_allowed(final_url, self.allowed):
                    raise DomainNotAllowed(f"Download ended on a domain that is not allowed: {final_url}")
                status = getattr(resp, "status", 200)
                if have and status == 206:
                    m = re.match(r"bytes (\d+)-", resp.headers.get("Content-Range", ""))
                    if not m or int(m.group(1)) != have:
                        have = 0  # unexpected range: start over
                        resumed_from = 0
                elif have:
                    have = 0  # server ignored Range: start over
                    resumed_from = 0
                total = size
                length = resp.headers.get("Content-Length")
                if total is None and length and length.isdigit():
                    total = int(length) + have
                if have:
                    self.ctx.progress("download", have, total, f"{label} (resuming at {have / 1e6:.1f} MB)")
                write_json_atomic(meta_path, {"url": url, "sha256": sha256, "size": total, "dest": str(dest)})
                mode = "ab" if have else "wb"
                with part.open(mode) as fh:
                    done = have
                    last_report = 0.0
                    window_start, window_bytes = time.monotonic(), 0
                    while True:
                        if self.ctx.cancelled:
                            fh.flush()
                            raise Cancelled("Download cancelled; partial data kept for resume")
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        window_bytes += len(chunk)
                        if self.min_speed and now - window_start >= self.speed_window:
                            speed = window_bytes / (now - window_start)
                            if speed < self.min_speed:
                                fh.flush()
                                raise TooSlow(f"too slow ({speed / 1024:.0f} KB/s)", code="too_slow")
                            window_start, window_bytes = now, 0
                        if now - last_report > 0.5:
                            self.ctx.progress("download", done, total, label)
                            last_report = now
                    fh.flush()
                    os.fsync(fh.fileno())
                self.ctx.progress("download", done, total, label)
        actual_size = part.stat().st_size
        if size is not None and actual_size < size:
            raise RunFailed(f"incomplete download ({actual_size}/{size} bytes); will resume", code="incomplete")
        digest = sha256_file(part)
        if digest != sha256 or (size is not None and actual_size != size):
            part.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            raise ChecksumMismatch(
                f"SHA-256 mismatch for {label}: expected {sha256[:12]}…, got {digest[:12]}… ({actual_size} bytes)"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, dest)
        meta_path.unlink(missing_ok=True)
        return DownloadResult(dest, url, resumed_from, downloaded, time.monotonic() - start)


def verify_file(path: Path, sha256: str, size: int | None = None) -> bool:
    try:
        if size is not None and path.stat().st_size != size:
            return False
    except OSError:
        return False
    return sha256_file(path) == sha256.lower()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
