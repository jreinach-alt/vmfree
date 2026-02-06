"""Host network bridge auto-detection.

Discovers the active bridge interface on the host by examining
/sys/class/net/ for bridge interfaces and checking which one carries
the default route. Falls back to common defaults (vmbr0 for Proxmox,
virbr0 for libvirt) when detection fails.

The user can always override with the --bridge CLI flag.
"""

from __future__ import annotations

from pathlib import Path

# Default bridges by target hypervisor
DEFAULT_BRIDGES = {
    "proxmox": "vmbr0",
    "kvm": "virbr0",
}

# Standard sysfs path for network interfaces
SYSFS_NET = Path("/sys/class/net")


def detect_bridge(
    *,
    target: str = "kvm",
    sysfs_path: Path | None = None,
    route_reader: object = None,
) -> str:
    """Auto-detect the host's active network bridge.

    Detection strategy:
      1. Scan /sys/class/net/ for bridge interfaces (those with a bridge/ subdir).
      2. If multiple bridges exist, prefer the one with a default route.
      3. If no bridge is found, return a sensible default for the target hypervisor.

    Args:
        target: Target hypervisor ("kvm" or "proxmox").
        sysfs_path: Override sysfs path for testing.
        route_reader: Optional callable that returns route table lines.
            Signature: () -> list[str]. Defaults to reading /proc/net/route.

    Returns:
        Name of the detected or default bridge interface.
    """
    sysfs = sysfs_path or SYSFS_NET
    bridges = find_bridges(sysfs)

    if not bridges:
        return DEFAULT_BRIDGES.get(target, "br0")

    if len(bridges) == 1:
        return bridges[0]

    # Multiple bridges — prefer the one with a default route
    reader = route_reader or _read_proc_route
    default_iface = _find_default_route_interface(reader)

    if default_iface in bridges:
        return default_iface

    # Prefer known names
    for preferred in ("vmbr0", "br0", "virbr0"):
        if preferred in bridges:
            return preferred

    return bridges[0]


def find_bridges(sysfs_path: Path | None = None) -> list[str]:
    """Find all bridge interfaces by scanning sysfs.

    A directory in /sys/class/net/<iface>/bridge/ indicates a bridge.

    Args:
        sysfs_path: Override for /sys/class/net (for testing).

    Returns:
        Sorted list of bridge interface names.
    """
    sysfs = sysfs_path or SYSFS_NET
    bridges = []

    if not sysfs.exists():
        return bridges

    for iface_dir in sorted(sysfs.iterdir()):
        if iface_dir.is_dir() and (iface_dir / "bridge").exists():
            bridges.append(iface_dir.name)

    return bridges


def _find_default_route_interface(route_reader: object) -> str:
    """Find the interface that carries the default route.

    Parses /proc/net/route format:
      Iface  Destination  Gateway  Flags  RefCnt  Use  Metric  Mask  ...
    Default route has Destination == 00000000.

    Args:
        route_reader: Callable returning route table lines.

    Returns:
        Interface name, or empty string if not found.
    """
    try:
        lines = route_reader()
    except (OSError, TypeError):
        return ""

    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "00000000":
            return fields[0]

    return ""


def _read_proc_route() -> list[str]:
    """Read /proc/net/route and return its lines (excluding header)."""
    try:
        text = Path("/proc/net/route").read_text()
        lines = text.strip().splitlines()
        return lines[1:]  # Skip header
    except OSError:
        return []
