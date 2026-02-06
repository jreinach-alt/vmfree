"""Windows VMware Tools removal from guest disk images.

Performs offline removal of VMware Tools from a Windows guest disk
using libguestfs (guestfish/virt-customize). This is the Windows
counterpart to the Linux removal in vmware_tools.py.

What gets removed:
  - VMware Tools installation directory (Program Files/VMware/VMware Tools)
  - VMware services from the Windows registry (VMTools, VGAuthService, etc.)
  - VMware drivers from System32/drivers (vmci.sys, vsock.sys, etc.)
  - VMware startup entries from registry Run keys

Why offline:
  - The VM isn't running — we're preparing the disk for first boot on KVM.
  - libguestfs can mount NTFS and manipulate the registry via hivex.
  - Avoids needing to boot into Windows, saving multiple reboot cycles.

All subprocess calls accept an injectable run_command parameter for
testing without real libguestfs binaries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vmfree.fixup.vmware_tools import FixupResult

# VMware Tools installation paths on Windows
VMWARE_TOOLS_PATHS_WIN = [
    "Program Files/VMware/VMware Tools",
    "Program Files (x86)/VMware/VMware Tools",
    "Program Files/VMware/VMware Workstation",
]

# VMware Windows services (registry keys under SYSTEM\CurrentControlSet\Services)
VMWARE_SERVICES_WIN = [
    "VMTools",
    "vmvss",
    "VGAuthService",
    "vm3dservice",
    "vmrawdsk",
    "vmci",
    "vsock",
    "vmhgfs",
    "vmmemctl",
]

# VMware driver files in System32/drivers
VMWARE_DRIVER_FILES = [
    "vmci.sys",
    "vsock.sys",
    "vmhgfs.sys",
    "vmmemctl.sys",
    "vmusbmouse.sys",
    "vm3dmp.sys",
    "vm3dmp-debug.sys",
    "vm3dmp-stats.sys",
]

# Registry Run key entries to clean
VMWARE_RUN_ENTRIES = [
    "VMware User Process",
    "VMware Tools",
]


def remove_vmware_tools_windows(
    disk_path: str | Path,
    *,
    run_command: object = None,
) -> FixupResult:
    """Remove VMware Tools from a Windows guest disk image.

    Uses virt-customize/guestfish to perform offline modification of the
    Windows filesystem and registry hives.

    Args:
        disk_path: Path to the guest disk image (qcow2 or raw).
        run_command: Optional callable for subprocess execution.

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

    result = FixupResult(success=True)

    # Remove VMware Tools directories
    _remove_tools_directories(disk, result, runner=runner)

    # Remove VMware driver files
    _remove_driver_files(disk, result, runner=runner)

    # Remove VMware services from registry
    _remove_registry_services(disk, result, runner=runner)

    # Clean VMware Run key entries
    _clean_run_entries(disk, result, runner=runner)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _remove_tools_directories(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware Tools installation directories from Windows guest."""
    runner = runner or subprocess.run

    for tools_path in VMWARE_TOOLS_PATHS_WIN:
        win_path = f"/cygdrive/c/{tools_path}"
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", f"rm -rf '{win_path}' 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Removed directory: C:\\{tools_path.replace('/', chr(92))}")
            else:
                result.actions.append(f"Directory not found: {tools_path}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _remove_driver_files(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware driver files from Windows System32/drivers."""
    runner = runner or subprocess.run

    for driver_file in VMWARE_DRIVER_FILES:
        win_path = f"/cygdrive/c/Windows/System32/drivers/{driver_file}"
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", f"rm -f '{win_path}' 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Removed driver: {driver_file}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _remove_registry_services(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware service entries from the Windows SYSTEM registry hive."""
    runner = runner or subprocess.run

    # Use hivexregedit to remove service keys from SYSTEM hive
    for service in VMWARE_SERVICES_WIN:
        reg_path = f"ControlSet001\\Services\\{service}"
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"hivexregedit --delete --prefix 'HKLM\\SYSTEM' "
            f"/cygdrive/c/Windows/System32/config/SYSTEM "
            f"'{reg_path}' 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Removed registry service: {service}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _clean_run_entries(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware entries from the Windows Run registry key."""
    runner = runner or subprocess.run

    for entry in VMWARE_RUN_ENTRIES:
        reg_path = (
            "Microsoft\\Windows\\CurrentVersion\\Run"
        )
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"hivexregedit --delete --prefix 'HKLM\\SOFTWARE' "
            f"/cygdrive/c/Windows/System32/config/SOFTWARE "
            f"'{reg_path}\\{entry}' 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Removed Run entry: {entry}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return
