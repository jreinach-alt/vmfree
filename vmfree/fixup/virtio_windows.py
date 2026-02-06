"""Windows VirtIO driver injection for guest disk images.

Injects VirtIO drivers (storage, network, balloon, display) into a Windows
guest disk image using libguestfs, enabling the VM to boot with VirtIO
devices instead of the safe IDE+e1000 fallback.

Without driver injection, switching from VMware's PVSCSI/vmxnet3 to KVM's
virtio-scsi/virtio-net causes a Windows BSOD. This module solves that by:
  1. Locating the virtio-win ISO on the host
  2. Detecting the Windows version in the guest
  3. Copying the appropriate driver files to the guest disk
  4. Adding service registry entries so Windows loads the drivers at boot

All subprocess calls accept an injectable run_command parameter for
testing without real libguestfs binaries or virtio-win ISO.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from vmfree.fixup.vmware_tools import FixupResult

# Common locations where the virtio-win ISO is installed
VIRTIO_ISO_PATHS = [
    "/usr/share/virtio-win/virtio-win.iso",
    "/usr/share/virtio-win/virtio-win-latest.iso",
    "/usr/share/virtio-win.iso",
]

# VirtIO driver names and their subdirectory paths in the ISO
VIRTIO_DRIVERS = {
    "vioscsi": "vioscsi",       # SCSI storage controller
    "viostor": "viostor",       # Block storage
    "netkvm": "NetKVM",         # Network
    "balloon": "Balloon",       # Memory balloon
    "qxldod": "qxldod",         # Display (QXL)
}

# Map VMware guestOS identifiers to virtio-win directory names
# Ordered longest-first so "windows2019srvnext" matches before "windows2019srv"
WINDOWS_VERSION_MAP: dict[str, str] = {
    "windows2019srvnext-64": "2k22",
    "windows2019srvnext": "2k22",
    "windows2019srv-64": "2k19",
    "windows2019srv": "2k19",
    "windows9-64": "w10",
    "windows9": "w10",
    "win2025": "2k25",
    "win2022": "2k22",
    "win2019": "2k19",
    "win11": "w11",
    "win10": "w10",
}

# Driver service registry entries (service name → driver sys file)
DRIVER_SERVICE_ENTRIES = {
    "vioscsi": "vioscsi.sys",
    "viostor": "viostor.sys",
    "netkvm": "netkvm.sys",
    "BalloonService": "balloon.sys",
}


@dataclass
class VirtIODriverSet:
    """Resolved VirtIO driver paths for a specific Windows version."""

    windows_version_dir: str     # e.g., "2k22"
    arch: str = "amd64"          # Architecture directory
    drivers: dict[str, str] = None  # driver_name → ISO subdirectory

    def __post_init__(self):
        if self.drivers is None:
            self.drivers = dict(VIRTIO_DRIVERS)


def detect_virtio_iso() -> Path | None:
    """Find the virtio-win ISO on the host system.

    Checks common installation paths in order.

    Returns:
        Path to the ISO file, or None if not found.
    """
    for iso_path in VIRTIO_ISO_PATHS:
        path = Path(iso_path)
        if path.exists():
            return path
    return None


def resolve_windows_version(guest_os: str) -> str:
    """Map a VMware guestOS identifier to a virtio-win version directory.

    Args:
        guest_os: VMware guest OS string (e.g., "windows2019srvnext-64").

    Returns:
        Virtio-win directory name (e.g., "2k22"), or "w10" as default.
    """
    os_lower = guest_os.lower()
    for pattern, version_dir in WINDOWS_VERSION_MAP.items():
        if pattern in os_lower:
            return version_dir
    # Default to Windows 10 drivers — broadly compatible
    return "w10"


def inject_virtio_drivers(
    disk_path: str | Path,
    *,
    guest_os: str = "",
    iso_path: str | Path | None = None,
    run_command: object = None,
) -> FixupResult:
    """Inject VirtIO drivers into a Windows guest disk image.

    Copies driver files from the virtio-win ISO into the guest's
    C:\\Windows\\Drivers\\virtio directory, then adds service registry
    entries so the drivers load on first boot.

    Args:
        disk_path: Path to the guest disk image.
        guest_os: VMware guestOS string for version detection.
        iso_path: Override path to the virtio-win ISO.
        run_command: Injectable callable for subprocess testing.

    Returns:
        FixupResult with success status and list of actions taken.
    """
    disk = Path(disk_path)
    runner = run_command or subprocess.run

    if not disk.exists():
        return FixupResult(
            success=False,
            errors=[f"Disk image not found: {disk}"],
        )

    # Find virtio-win ISO
    resolved_iso = Path(iso_path) if iso_path else detect_virtio_iso()

    if resolved_iso is None or not resolved_iso.exists():
        return FixupResult(
            success=False,
            errors=[
                "virtio-win ISO not found. Install it with: "
                "dnf install virtio-win (RHEL/CentOS) or "
                "download from https://fedorapeople.org/groups/virt/virtio-win/"
            ],
        )

    result = FixupResult(success=True)

    # Resolve Windows version for driver path selection
    win_ver = resolve_windows_version(guest_os)
    driver_set = VirtIODriverSet(windows_version_dir=win_ver)
    result.actions.append(f"Windows version: {win_ver} (from: {guest_os or 'default'})")

    # Create the driver staging directory in the guest
    _create_driver_directory(disk, result, runner=runner)

    # Copy each driver from the ISO to the guest
    for driver_name, iso_subdir in driver_set.drivers.items():
        _copy_driver(
            disk, resolved_iso, driver_name, iso_subdir,
            win_ver, driver_set.arch, result, runner=runner,
        )

    # Add driver service entries to the SYSTEM registry hive
    _add_registry_services(disk, result, runner=runner)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _create_driver_directory(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Create C:\\Windows\\Drivers\\virtio in the guest."""
    runner = runner or subprocess.run

    cmd = [
        "virt-customize", "-a", str(disk),
        "--mkdir", "/Windows/Drivers/virtio",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append("Created C:\\Windows\\Drivers\\virtio")
        else:
            result.actions.append("Could not create driver directory")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False


def _copy_driver(
    disk: Path,
    iso_path: Path,
    driver_name: str,
    iso_subdir: str,
    win_ver: str,
    arch: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Copy a single driver from the virtio ISO to the guest disk."""
    runner = runner or subprocess.run

    # The ISO path structure is: <driver>/<win_ver>/<arch>/*.sys, *.inf, *.cat
    iso_driver_path = f"{iso_subdir}/{win_ver}/{arch}"
    guest_driver_path = f"/Windows/Drivers/virtio/{driver_name}"

    cmd = [
        "virt-customize", "-a", str(disk),
        "--run-command",
        f"mkdir -p '{guest_driver_path}' && "
        f"cp -r /media/virtio/{iso_driver_path}/* '{guest_driver_path}/' "
        f"2>/dev/null || true",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append(f"Copied driver: {driver_name} ({win_ver}/{arch})")
        else:
            result.actions.append(f"Driver copy failed: {driver_name}")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False


def _add_registry_services(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Add VirtIO driver service entries to the Windows SYSTEM registry."""
    runner = runner or subprocess.run

    for service_name, sys_file in DRIVER_SERVICE_ENTRIES.items():
        # Build a .reg file content for the service entry
        reg_content = (
            f"[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\{service_name}]\n"
            f'"Type"=dword:00000001\n'
            f'"Start"=dword:00000000\n'
            f'"ErrorControl"=dword:00000001\n'
            f'"ImagePath"="\\\\SystemRoot\\\\Drivers\\\\virtio\\\\{service_name}\\\\{sys_file}"\n'
            f'"DisplayName"="{service_name}"\n'
        )

        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"echo '{reg_content}' > /tmp/virtio-{service_name}.reg && "
            f"hivexregedit --merge --prefix 'HKLM\\SYSTEM' "
            f"/cygdrive/c/Windows/System32/config/SYSTEM "
            f"/tmp/virtio-{service_name}.reg 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Added registry service: {service_name}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return
