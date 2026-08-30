"""The raw cache: the vendor's answer, kept exactly as it arrived.

Two jobs, and they are the same job seen from different ends.

**Resume.** A download of ninety symbols over fifteen years is a long-running
operation on somebody's laptop, and it will be interrupted — a closed lid, a
dropped connection, a rate limit that outlasts the retry budget. Re-running the
command must not re-download what already arrived. The cache is what makes that
true, and it is keyed on the request's *identity* (provider, dataset, symbol,
date range) rather than on a URL hash, so a person can look in the directory and
see what they have.

**Provenance.** The file on disk is the raw evidence behind every normalized
row. Its SHA-256 goes in the manifest, so a package's numbers can be traced back
to bytes a vendor actually sent, and a later argument about whether a price was
adjusted is settled by opening the file rather than by recollection.

**Nothing here is ever rewritten.** A cache entry is written once. Refreshing
means deleting and re-fetching, which `--force-refresh` does explicitly. An
in-place update would break the second job to make the first slightly faster.

Layout:

```
<root>/raw/<provider>/<dataset>/<symbol>__<start>_<end>.json
<root>/raw/<provider>/<dataset>/<symbol>__<start>_<end>.meta.json
```

The sidecar holds the redacted URL, the fetch timestamp, the byte count and the
digest. It never holds a credential — the URL is redacted before it reaches
here, and there is a test asserting so.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from tradeit.acquisition.base import AcquisitionDataset, FetchRequest


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One stored response."""

    path: Path
    digest: str
    size_bytes: int
    fetched_at: dt.datetime
    url: str

    def read(self) -> bytes:
        return self.path.read_bytes()


@dataclass(slots=True)
class RawCache:
    """Immutable, human-browsable storage for vendor responses."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # -- paths ---------------------------------------------------------------

    def directory(self, provider: str, dataset: AcquisitionDataset) -> Path:
        return self.root / "raw" / provider / str(dataset)

    def path_for(self, provider: str, request: FetchRequest) -> Path:
        return self.directory(provider, request.dataset) / f"{request.identity}.json"

    def _meta_path(self, body: Path) -> Path:
        return body.with_suffix(".meta.json")

    # -- reads ---------------------------------------------------------------

    def get(self, provider: str, request: FetchRequest) -> CacheEntry | None:
        """Return a stored response, or ``None``.

        A body whose sidecar is missing or whose digest does not match is
        treated as absent rather than repaired: a half-written file from an
        interrupted run must not be mistaken for a complete download, and the
        cost of being wrong is one re-fetch.
        """
        body = self.path_for(provider, request)
        meta = self._meta_path(body)
        if not body.exists() or not meta.exists():
            return None
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        raw = body.read_bytes()
        digest = sha256_bytes(raw)
        if digest != payload.get("sha256"):
            return None
        return CacheEntry(
            path=body,
            digest=digest,
            size_bytes=len(raw),
            fetched_at=dt.datetime.fromisoformat(payload["fetched_at"]),
            url=payload.get("url", ""),
        )

    # -- writes --------------------------------------------------------------

    def put(
        self,
        provider: str,
        request: FetchRequest,
        raw: bytes,
        *,
        url: str,
        fetched_at: dt.datetime | None = None,
    ) -> CacheEntry:
        """Store one response and its sidecar.

        ``url`` must already be redacted. This function does not redact, on
        purpose: redaction belongs where the credential is known, and doing it
        here would mean the credential had already travelled one layer further
        than it needed to.
        """
        body = self.path_for(provider, request)
        body.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_bytes(raw)
        moment = fetched_at or dt.datetime.now(dt.UTC)

        # Write body first, sidecar second. An interruption between the two
        # leaves an entry `get` treats as absent, which is the safe direction.
        body.write_bytes(raw)
        self._meta_path(body).write_text(
            json.dumps(
                {
                    "provider": provider,
                    "dataset": str(request.dataset),
                    "symbol": request.symbol,
                    "start": request.start.isoformat() if request.start else None,
                    "end": request.end.isoformat() if request.end else None,
                    "url": url,
                    "sha256": digest,
                    "bytes": len(raw),
                    "fetched_at": moment.isoformat(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return CacheEntry(path=body, digest=digest, size_bytes=len(raw), fetched_at=moment, url=url)

    def discard(self, provider: str, request: FetchRequest) -> bool:
        """Remove an entry so the next fetch goes to the vendor. Used by --force-refresh."""
        body = self.path_for(provider, request)
        meta = self._meta_path(body)
        removed = False
        for path in (meta, body):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    # -- accounting ----------------------------------------------------------

    def total_bytes(self) -> int:
        root = self.root / "raw"
        if not root.exists():
            return 0
        return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())

    def file_count(self) -> int:
        root = self.root / "raw"
        if not root.exists():
            return 0
        return sum(1 for p in root.rglob("*.json") if not p.name.endswith(".meta.json"))


__all__ = ["CacheEntry", "RawCache", "sha256_bytes"]
