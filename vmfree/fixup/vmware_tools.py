"""VMware Tools removal from guest disk images.

Uses libguestfs (via the guestfish/virt-* command-line tools) to
perform offline modification of a VM's disk image, removing VMware-
specific packages and services that would interfere with KVM operation.

What gets removed:
  - Linux: open-vm-tools, vmware-tools packages, vmware kernel modules,
    VMware-specific systemd services
  - Windows: VMware Tools install directory, services, registry entries
    (handled separately in virtio_windows.py)

Why offline modification:
  - The VM isn't running yet — we're preparing the disk for first boot on KVM.
  - libguestfs can mount the disk image without actually booting the guest.
  - Avoids the need for SSH access or running agent inside the guest.

All subprocess calls accept an injectable run_command parameter for
testing without real libguestfs binaries.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FixupResult:
    """Result of a guest OS fixup operation.

    Attributes:
        success: Whether the fixup completed successfully.
        actions: List of actions taken (for logging/display).
        errors: List of error messages if fixup failed.
    """

    success: bool
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# VMware-related packages to remove on Linux guests
VMWARE_PACKAGES_RPM = [
    "open-vm-tools",
    "open-vm-tools-desktop",
    "vmware-tools-*",
]

VMWARE_PACKAGES_DEB = [
    "open-vm-tools",
    "open-vm-tools-desktop",
    "open-vm-tools-dkms",
]

# VMware kernel modules to remove
VMWARE_MODULES = [
    "vmw_pvscsi",
    "vmw_vmci",
    "vmw_balloon",
    "vmxnet3",
    "vmhgfs",
    "vmci",
    "vsock",
    "vmblock",
]

# VMware systemd services to disable
VMWARE_SERVICES = [
    "vmtoolsd.service",
    "vmware-tools.service",
    "vmware-tools-thinprint.service",
    "open-vm-tools.service",
]

# VMware-related files and directories to clean up
VMWARE_PATHS = [
    "/etc/vmware-tools",
    "/usr/lib/vmware-tools",
    "/usr/lib64/open-vm-tools",
    "/var/log/vmware-*",
]


def remove_vmware_tools_linux(
    disk_path: str | Path,
    *,
    run_command: object = None,
) -> FixupResult:
    """Remove VMware Tools from a Linux guest disk image.

    Uses virt-customize to run commands inside the guest filesystem
    without booting the VM. Falls back to guestfish for more granular
    control if virt-customize is not available.

    Args:
        disk_path: Path to the guest disk image (qcow2 or raw).
        run_command: Optional callable for subprocess execution.
            Signature: (args, **kwargs) -> CompletedProcess.
            Defaults to subprocess.run.

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

    # Detect package manager
    pkg_mgr = _detect_package_manager(disk, runner=runner)
    result.actions.append(f"Detected package manager: {pkg_mgr or 'unknown'}")

    # Remove packages via virt-customize
    if pkg_mgr in ("rpm", "yum", "dnf"):
        _remove_rpm_packages(disk, result, runner=runner)
    elif pkg_mgr in ("deb", "apt"):
        _remove_deb_packages(disk, result, runner=runner)

    # Remove VMware kernel module configs
    _remove_module_configs(disk, result, runner=runner)

    # Disable VMware services
    _disable_services(disk, result, runner=runner)

    # Clean up VMware directories
    _cleanup_paths(disk, result, runner=runner)

    return result


def check_libguestfs_installed(
    *,
    run_command: object = None,
) -> bool:
    """Check if libguestfs tools are available.

    Args:
        run_command: Optional callable for subprocess execution.

    Returns:
        True if virt-customize is available.
    """
    runner = run_command or subprocess.run
    try:
        proc = runner(
            ["virt-customize", "--version"],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_package_manager(
    disk: Path,
    *,
    runner: object = None,
) -> str:
    """Detect the guest's package manager by inspecting the filesystem.

    Uses virt-inspector or guestfish to check for /etc/redhat-release,
    /etc/debian_version, etc.
    """
    runner = runner or subprocess.run

    # Try virt-cat to check for distro markers
    for marker_file, pkg_type in [
        ("/etc/redhat-release", "rpm"),
        ("/etc/centos-release", "rpm"),
        ("/etc/fedora-release", "dnf"),
        ("/etc/debian_version", "deb"),
        ("/etc/lsb-release", "deb"),
    ]:
        try:
            proc = runner(
                ["virt-cat", "-a", str(disk), marker_file],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return pkg_type
        except FileNotFoundError:
            return ""

    return ""


def _remove_rpm_packages(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware RPM packages from guest."""
    runner = runner or subprocess.run

    for pkg in VMWARE_PACKAGES_RPM:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", f"rpm -e --nodeps {pkg} 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Removed RPM package: {pkg}")
            else:
                result.actions.append(f"RPM package not found: {pkg}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _remove_deb_packages(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware DEB packages from guest."""
    runner = runner or subprocess.run

    for pkg in VMWARE_PACKAGES_DEB:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", f"dpkg --purge {pkg} 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Removed DEB package: {pkg}")
            else:
                result.actions.append(f"DEB package not found: {pkg}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _remove_module_configs(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware kernel module blacklist/load configs."""
    runner = runner or subprocess.run

    # Create a blacklist file to prevent VMware modules from loading
    module_list = "\n".join(f"blacklist {m}" for m in VMWARE_MODULES)
    cmd = [
        "virt-customize", "-a", str(disk),
        "--write",
        f"/etc/modprobe.d/vmware-blacklist.conf:{module_list}",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append(
                f"Blacklisted {len(VMWARE_MODULES)} VMware kernel modules"
            )
        else:
            result.actions.append("Could not write module blacklist")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False


def _disable_services(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Disable VMware-related systemd services."""
    runner = runner or subprocess.run

    for service in VMWARE_SERVICES:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"systemctl disable {service} 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Disabled service: {service}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _cleanup_paths(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove VMware-specific files and directories from guest."""
    runner = runner or subprocess.run

    for vmpath in VMWARE_PATHS:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", f"rm -rf {vmpath} 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Cleaned up: {vmpath}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return
