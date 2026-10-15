"""Loading of the BMC inventory (hosts.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

VENDORS = {"idrac", "irmc", "ilo", "ilom", "generic"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class BmcHost:
    name: str
    vendor: str
    address: str
    username: str
    password: str
    port: int = 443
    verify_tls: bool = False  # BMCs almost always ship self-signed certificates
    timeout: float = 20.0
    max_concurrency: int = 2  # BMCs are slow and have tiny web servers


def _secret(entry: dict, key: str, host_name: str) -> str:
    """Value from `key`, or from the environment variable named in `<key>_env`."""
    if entry.get(key) is not None:
        return str(entry[key])
    env_name = entry.get(f"{key}_env")
    if env_name:
        value = os.environ.get(env_name)
        if value is None:
            raise ConfigError(
                f"Server '{host_name}': environment variable {env_name} is not set"
            )
        return value
    raise ConfigError(f"Server '{host_name}': missing '{key}' or '{key}_env'")


def load_hosts(path: str | Path) -> dict[str, BmcHost]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Hosts file not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    entries = data.get("servers")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("hosts file must contain a non-empty 'servers' list")

    hosts: dict[str, BmcHost] = {}
    for entry in entries:
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ConfigError("every server needs a 'name'")
        if name.lower() in {h.lower() for h in hosts}:
            raise ConfigError(f"duplicate server name: {name}")
        vendor = str(entry.get("vendor", "generic")).lower()
        if vendor not in VENDORS:
            raise ConfigError(
                f"Server '{name}': vendor must be one of {sorted(VENDORS)}"
            )
        address = str(entry.get("address", "")).strip()
        if not address:
            raise ConfigError(f"Server '{name}': missing 'address'")
        hosts[name] = BmcHost(
            name=name,
            vendor=vendor,
            address=address,
            username=_secret(entry, "username", name),
            password=_secret(entry, "password", name),
            port=int(entry.get("port", 443)),
            verify_tls=bool(entry.get("verify_tls", False)),
            timeout=float(entry.get("timeout", 20.0)),
            max_concurrency=int(entry.get("max_concurrency", 2)),
        )
    return hosts
