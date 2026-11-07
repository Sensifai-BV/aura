"""Async Orion-LD helpers and NGSI-LD normalized → simple value conversion."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

JSON = {"Accept": "application/json"}  # httpx adds Content-Type for json bodies; Orion-LD rejects it on GET


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def prop(value: Any, observed_at: str | None = None, unit: str | None = None) -> dict:
    attr: dict = {"type": "Property", "value": value, "observedAt": observed_at or now_iso()}
    if unit:
        attr["unitCode"] = unit
    return attr


def rel(target: str) -> dict:
    return {"type": "Relationship", "object": target}


def simplify(entity: dict) -> dict:
    """Normalized NGSI-LD entity → {attr: value} plus id/type/_observedAt (observedAt, else modifiedAt)."""
    out: dict = {"id": entity.get("id"), "type": entity.get("type")}
    latest = None
    for key, attr in entity.items():
        if key in ("id", "type", "@context") or not isinstance(attr, dict):
            continue
        t = attr.get("type")
        if t == "Relationship":
            out[key] = attr.get("object")
        elif t == "GeoProperty":
            out[key] = attr.get("value")
        else:
            v = attr.get("value")
            if isinstance(v, dict) and "@value" in v:
                v = v["@value"]
            out[key] = v
        obs = attr.get("observedAt") or attr.get("modifiedAt")  # DDS-bridged attributes carry only modifiedAt
        if obs and (latest is None or obs > latest):
            latest = obs
    out["_observedAt"] = latest
    return out


class Orion:
    def __init__(self, base_url: str):
        self.root = base_url.rstrip("/")
        self.base = self.root + "/ngsi-ld/v1"
        self.http = httpx.AsyncClient(timeout=10.0, headers=JSON)

    async def version(self) -> dict:
        r = await self.http.get(self.root + "/version")
        r.raise_for_status()
        return r.json()

    async def upsert(self, entities: list[dict]) -> None:
        r = await self.http.post(f"{self.base}/entityOperations/upsert", params={"options": "update"}, json=entities)
        if r.status_code >= 300:
            log.warning("upsert failed %s: %s", r.status_code, r.text[:400])

    async def patch(self, entity_id: str, attrs: dict) -> None:
        r = await self.http.patch(f"{self.base}/entities/{entity_id}/attrs", json=attrs)
        if r.status_code >= 300:
            log.warning("patch %s failed %s: %s", entity_id, r.status_code, r.text[:400])

    async def get(self, entity_id: str) -> dict | None:
        r = await self.http.get(f"{self.base}/entities/{entity_id}", params={"options": "sysAttrs"})
        return r.json() if r.status_code == 200 else None

    async def query(self, entity_type: str | None = None, limit: int = 1000, q: str | None = None) -> list[dict]:
        params: dict = {"limit": limit}
        if entity_type:
            params["type"] = entity_type
        else:
            params["local"] = "true"
        if q:
            params["q"] = q
        r = await self.http.get(f"{self.base}/entities", params=params)
        return r.json() if r.status_code == 200 else []

    async def types(self) -> list[str]:
        r = await self.http.get(f"{self.base}/types")
        if r.status_code != 200:
            return []
        body = r.json()
        names = body.get("typeList", []) if isinstance(body, dict) else [t.get("typeName") or t.get("id") for t in body]
        return [n.rsplit("/", 1)[-1] for n in names if n]

    async def count(self, entity_type: str) -> int:
        r = await self.http.get(f"{self.base}/entities", params={"type": entity_type, "count": "true", "limit": 0})
        try:
            return int(r.headers.get("NGSILD-Results-Count", "0"))
        except ValueError:
            return 0

    async def ensure_subscription(self, sub_id: str, entity_types: list[str], endpoint: str) -> None:
        await self.http.delete(f"{self.base}/subscriptions/{sub_id}")
        body = {
            "id": sub_id,
            "type": "Subscription",
            "description": "aura-monitor analytics plane",
            "entities": [{"type": t} for t in entity_types],
            "notification": {"format": "normalized", "sysAttrs": True,
                             "endpoint": {"uri": endpoint, "accept": "application/json"}},
        }
        r = await self.http.post(f"{self.base}/subscriptions", json=body)
        if r.status_code >= 300:
            raise RuntimeError(f"subscription failed {r.status_code}: {r.text[:300]}")

    async def subscriptions(self) -> list[dict]:
        r = await self.http.get(f"{self.base}/subscriptions")
        return r.json() if r.status_code == 200 else []
