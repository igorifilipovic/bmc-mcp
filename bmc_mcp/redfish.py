"""Minimal READ-ONLY Redfish client.

Only HTTP GET is ever issued; there is deliberately no method for POST/PATCH/DELETE.
Discovery is generic (walks /redfish/v1/{Systems,Managers,Chassis}/*/LogServices), so the
same code works for iDRAC, iRMC, iLO and Oracle iLOM without vendor-specific paths.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import BmcHost

ROOT = "/redfish/v1"
MAX_MESSAGE_LEN = 500
LOGSVC_CACHE_TTL = 300.0

SEVERITY_RANK = {"ok": 0, "informational": 0, "info": 0, "warning": 1, "critical": 2}


class RedfishError(Exception):
    pass


def severity_rank(value: Any) -> int:
    return SEVERITY_RANK.get(str(value or "").strip().lower(), 0)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
_LOGSVC_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _semaphore(host: BmcHost) -> asyncio.Semaphore:
    sem = _SEMAPHORES.get(host.name)
    if sem is None:
        sem = _SEMAPHORES[host.name] = asyncio.Semaphore(host.max_concurrency)
    return sem


class Redfish:
    def __init__(self, http: httpx.AsyncClient, host: BmcHost):
        self.http = http
        self.host = host

    # -- transport -------------------------------------------------------------
    @staticmethod
    def _local_path(path: str) -> str:
        """Always stay on the configured BMC, even if it returns an absolute URL."""
        if path.startswith("http://") or path.startswith("https://"):
            p = urlparse(path)
            return p.path + (f"?{p.query}" if p.query else "")
        return path

    async def _get(self, path: str) -> dict:
        path = self._local_path(path)
        async with _semaphore(self.host):
            try:
                r = await self.http.get(path)
            except httpx.TimeoutException:
                raise RedfishError(f"{self.host.name}: timeout talking to BMC")
            except httpx.HTTPError as e:
                raise RedfishError(f"{self.host.name}: connection error ({type(e).__name__})")
        if r.status_code in (401, 403):
            raise RedfishError(
                f"{self.host.name}: authentication failed (HTTP {r.status_code}), check the BMC account"
            )
        if r.status_code == 404:
            raise RedfishError(f"{self.host.name}: resource not found: {path}")
        if r.status_code >= 400:
            raise RedfishError(f"{self.host.name}: HTTP {r.status_code} for {path}")
        try:
            return r.json()
        except ValueError:
            raise RedfishError(f"{self.host.name}: invalid JSON from {path}")

    async def _members(self, collection_path: str) -> list[str]:
        try:
            doc = await self._get(collection_path)
        except RedfishError:
            return []
        return [m["@odata.id"] for m in doc.get("Members", []) if "@odata.id" in m]

    # -- systems ---------------------------------------------------------------
    async def systems(self) -> list[dict]:
        out = []
        for path in await self._members(f"{ROOT}/Systems"):
            d = await self._get(path)
            status = d.get("Status") or {}
            out.append({
                "id": d.get("Id"),
                "name": d.get("Name"),
                "manufacturer": d.get("Manufacturer"),
                "model": d.get("Model"),
                "serial_number": d.get("SerialNumber"),
                "bios_version": d.get("BiosVersion"),
                "power_state": d.get("PowerState"),
                "state": status.get("State"),
                "health": status.get("Health"),
                "health_rollup": status.get("HealthRollup"),
            })
        if not out:
            raise RedfishError(f"{self.host.name}: no systems found under {ROOT}/Systems")
        return out

    # -- logs ------------------------------------------------------------------
    async def log_services(self) -> list[dict]:
        cached = _LOGSVC_CACHE.get(self.host.name)
        if cached and time.monotonic() - cached[0] < LOGSVC_CACHE_TTL:
            return cached[1]

        services: dict[str, dict] = {}
        for base in ("Systems", "Managers", "Chassis"):
            for member in await self._members(f"{ROOT}/{base}"):
                try:
                    doc = await self._get(member)
                except RedfishError:
                    continue
                link = (doc.get("LogServices") or {}).get("@odata.id")
                if not link:
                    continue
                for svc_path in await self._members(link):
                    if svc_path in services:
                        continue
                    try:
                        svc = await self._get(svc_path)
                    except RedfishError:
                        continue
                    entries = (svc.get("Entries") or {}).get("@odata.id") or f"{svc_path.rstrip('/')}/Entries"
                    services[svc_path] = {
                        "id": svc.get("Id") or svc_path.rstrip("/").split("/")[-1],
                        "name": svc.get("Name"),
                        "scope": f"{base}/{doc.get('Id')}",
                        "path": svc_path,
                        "entries_path": entries,
                    }
        result = list(services.values())
        _LOGSVC_CACHE[self.host.name] = (time.monotonic(), result)
        return result

    async def entries(self, svc: dict, max_pages: int) -> tuple[list[dict], bool]:
        raw: list[dict] = []
        next_path: str | None = svc["entries_path"]
        pages = 0
        truncated = False
        while next_path:
            if pages >= max_pages:
                truncated = True
                break
            page = await self._get(next_path)
            pages += 1
            raw.extend(m for m in page.get("Members", []) if isinstance(m, dict))
            next_path = page.get("Members@odata.nextLink")
        return [_normalize(e, svc) for e in raw], truncated


def _normalize(e: dict, svc: dict) -> dict:
    message = e.get("Message") or e.get("Name") or ""
    return {
        "log": svc["id"],
        "scope": svc["scope"],
        "id": e.get("Id"),
        "created": e.get("Created") or e.get("EventTimestamp"),
        "severity": e.get("Severity"),
        "message": str(message)[:MAX_MESSAGE_LEN],
        "message_id": e.get("MessageId"),
        "entry_type": e.get("EntryType"),
        "sensor_type": e.get("SensorType"),
    }


@asynccontextmanager
async def open_session(host: BmcHost):
    async with httpx.AsyncClient(
        base_url=f"https://{host.address}:{host.port}",
        auth=(host.username, host.password),
        verify=host.verify_tls,
        timeout=host.timeout,
        headers={"Accept": "application/json", "OData-Version": "4.0"},
        follow_redirects=False,
    ) as http:
        yield Redfish(http, host)


async def get_events(
    host: BmcHost,
    *,
    log_service: str | None,
    limit: int,
    min_severity: str,
    since_hours: float | None,
    max_pages: int,
) -> dict:
    async with open_session(host) as rf:
        services = await rf.log_services()
        if log_service:
            q = log_service.lower()
            services = [
                s for s in services
                if q in s["id"].lower() or q in (s["name"] or "").lower()
            ]
            if not services:
                raise RedfishError(f"{host.name}: no log service matching '{log_service}'")

        results = await asyncio.gather(
            *(rf.entries(s, max_pages) for s in services), return_exceptions=True
        )

    min_rank = severity_rank(min_severity)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=since_hours) if since_hours else None
    )
    events: list[dict] = []
    errors: list[str] = []
    fetch_truncated = False
    queried: list[str] = []
    for svc, res in zip(services, results):
        if isinstance(res, Exception):
            errors.append(f"{svc['id']}: {res}")
            continue
        entries, truncated = res
        queried.append(svc["id"])
        fetch_truncated |= truncated
        for e in entries:
            if severity_rank(e["severity"]) < min_rank:
                continue
            dt = parse_dt(e["created"])
            if cutoff and dt and dt < cutoff:
                continue  # entries with unparseable timestamps are kept on purpose
            events.append(e)

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    events.sort(key=lambda e: parse_dt(e["created"]) or epoch, reverse=True)
    return {
        "server": host.name,
        "logs_queried": queried,
        "total_matched": len(events),
        "returned": min(len(events), limit),
        "fetch_truncated": fetch_truncated,
        "errors": errors,
        "events": events[:limit],
    }
