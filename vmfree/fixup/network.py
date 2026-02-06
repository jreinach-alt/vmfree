"""Guest network configuration fixup.

When migrating from VMware to KVM, the virtual NIC type changes
(vmxnet3 → virtio-net), which changes the PCI slot and therefore
the Linux predictable network interface name (e.g., ens192 → ens18).

If the guest has static network configuration (IP address, routes,
DNS), those configs reference the old interface name and will break
on first boot.

This module handles:
  1. Detecting guest network configuration files and their format.
  2. Updating interface names in config files (Netplan, NetworkManager,
     ifcfg scripts, systemd-networkd).
  3. Removing stale udev rules that pin NIC names to MAC+PCI slot.
  4. Optionally setting DHCP as a safe fallback.

All operations are performed offline via libguestfs (virt-customize).
All subprocess calls accept an injectable run_command parameter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vmfree.fixup.vmware_tools import FixupResult

# Common VMware interface names mapped to expected KVM names.
# The actual name depends on the PCI slot, but these are common defaults.
VMWARE_IFACE_NAMES = [
    "ens160",
    "ens192",
    "ens224",
    "ens256",
    "ens161",
    "ens193",
]

# Expected KVM/virtio interface name (PCI slot-dependent)
KVM_DEFAULT_IFACE = "ens18"

# Network config file locations by distribution/tool
NETPLAN_DIR = "/etc/netplan"
IFCFG_DIR = "/etc/sysconfig/network-scripts"
NETWORKD_DIR = "/etc/systemd/network"
NM_DIR = "/etc/NetworkManager/system-connections"

# Stale udev rules that pin NIC names to VMware PCI slots
UDEV_NET_RULES = "/etc/udev/rules.d/70-persistent-net.rules"


def fix_guest_network(
    disk_path: str | Path,
    *,
    old_iface: str = "",
    new_iface: str = KVM_DEFAULT_IFACE,
    run_command: object = None,
) -> FixupResult:
    """Fix guest network configuration for the new virtual NIC.

    Detects the guest's network config format and updates interface
    names from the VMware default to the expected KVM name.

    Args:
        disk_path: Path to the guest disk image (qcow2 or raw).
        old_iface: VMware interface name to replace. If empty,
            auto-detects common VMware names.
        new_iface: New interface name for KVM. Defaults to "ens18".
        run_command: Optional callable for subprocess execution.

    Returns:
        FixupResult with success status and actions taken.
    """
    disk = Path(disk_path)
    runner = run_command or subprocess.run

    if not disk.exists():
        return FixupResult(
            success=False,
            errors=[f"Disk image not found: {disk}"],
        )

    result = FixupResult(success=True)

    # Step 1: Remove stale udev rules
    _remove_udev_rules(disk, result, runner=runner)

    # Step 2: Detect and fix network config
    config_type = _detect_network_config(disk, runner=runner)
    result.actions.append(f"Detected network config: {config_type or 'none'}")

    if config_type == "netplan":
        _fix_netplan(disk, old_iface, new_iface, result, runner=runner)
    elif config_type == "ifcfg":
        _fix_ifcfg(disk, old_iface, new_iface, result, runner=runner)
    elif config_type == "networkd":
        _fix_networkd(disk, old_iface, new_iface, result, runner=runner)
    elif config_type == "nm":
        _fix_network_manager(disk, old_iface, new_iface, result, runner=runner)
    else:
        result.actions.append("No static network config found, likely uses DHCP")

    # Step 3: Clean up VMware-specific network state
    _cleanup_network_state(disk, result, runner=runner)

    return result


def set_dhcp_fallback(
    disk_path: str | Path,
    iface: str = KVM_DEFAULT_IFACE,
    *,
    run_command: object = None,
) -> FixupResult:
    """Set DHCP as a fallback on the target interface.

    Creates a simple Netplan or ifcfg config that enables DHCP
    on the primary interface, ensuring the VM gets connectivity
    even if the static config fixup fails.

    Args:
        disk_path: Path to the guest disk image.
        iface: Interface name to configure.
        run_command: Optional callable for subprocess execution.

    Returns:
        FixupResult with actions taken.
    """
    disk = Path(disk_path)
    runner = run_command or subprocess.run

    if not disk.exists():
        return FixupResult(
            success=False,
            errors=[f"Disk image not found: {disk}"],
        )

    result = FixupResult(success=True)

    # Write a simple Netplan DHCP config
    netplan_content = (
        "network:\\n"
        "  version: 2\\n"
        "  ethernets:\\n"
        f"    {iface}:\\n"
        "      dhcp4: true\\n"
    )
    cmd = [
        "virt-customize", "-a", str(disk),
        "--run-command",
        f"echo -e '{netplan_content}' > /etc/netplan/99-vmfree-dhcp.yaml "
        "2>/dev/null || true",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append(f"Created DHCP fallback config for {iface}")
        else:
            result.actions.append("Could not create DHCP fallback config")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _remove_udev_rules(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Remove stale udev persistent net rules."""
    runner = runner or subprocess.run

    cmd = [
        "virt-customize", "-a", str(disk),
        "--run-command",
        f"rm -f {UDEV_NET_RULES} 2>/dev/null || true",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append("Removed stale udev persistent net rules")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False


def _detect_network_config(
    disk: Path,
    *,
    runner: object = None,
) -> str:
    """Detect which network configuration tool the guest uses."""
    runner = runner or subprocess.run

    checks = [
        (NETPLAN_DIR, "netplan"),
        (IFCFG_DIR, "ifcfg"),
        (NETWORKD_DIR, "networkd"),
        (NM_DIR, "nm"),
    ]

    for config_path, config_type in checks:
        try:
            proc = runner(
                ["virt-ls", "-a", str(disk), config_path],
                capture_output=True, text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return config_type
        except FileNotFoundError:
            return ""

    return ""


def _fix_netplan(
    disk: Path,
    old_iface: str,
    new_iface: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Fix Netplan YAML config files."""
    runner = runner or subprocess.run

    ifaces_to_replace = [old_iface] if old_iface else VMWARE_IFACE_NAMES
    for iface in ifaces_to_replace:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"find {NETPLAN_DIR} -name '*.yaml' -exec "
            f"sed -i 's/{iface}/{new_iface}/g' {{}} \\;",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(
                    f"Updated Netplan: {iface} -> {new_iface}"
                )
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _fix_ifcfg(
    disk: Path,
    old_iface: str,
    new_iface: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Fix ifcfg-style network scripts (RHEL/CentOS)."""
    runner = runner or subprocess.run

    ifaces_to_replace = [old_iface] if old_iface else VMWARE_IFACE_NAMES
    for iface in ifaces_to_replace:
        old_file = f"{IFCFG_DIR}/ifcfg-{iface}"
        new_file = f"{IFCFG_DIR}/ifcfg-{new_iface}"

        # Rename the config file and update DEVICE= inside
        script = (
            f"if [ -f {old_file} ]; then "
            f"  mv {old_file} {new_file}; "
            f"  sed -i 's/DEVICE={iface}/DEVICE={new_iface}/g' {new_file}; "
            f"  sed -i 's/NAME={iface}/NAME={new_iface}/g' {new_file}; "
            f"fi"
        )
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", script,
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(
                    f"Updated ifcfg: {iface} -> {new_iface}"
                )
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _fix_networkd(
    disk: Path,
    old_iface: str,
    new_iface: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Fix systemd-networkd .network files."""
    runner = runner or subprocess.run

    ifaces_to_replace = [old_iface] if old_iface else VMWARE_IFACE_NAMES
    for iface in ifaces_to_replace:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"find {NETWORKD_DIR} -name '*.network' -exec "
            f"sed -i 's/Name={iface}/Name={new_iface}/g' {{}} \\;",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(
                    f"Updated networkd: {iface} -> {new_iface}"
                )
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _fix_network_manager(
    disk: Path,
    old_iface: str,
    new_iface: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Fix NetworkManager connection files."""
    runner = runner or subprocess.run

    ifaces_to_replace = [old_iface] if old_iface else VMWARE_IFACE_NAMES
    for iface in ifaces_to_replace:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"find {NM_DIR} -type f -exec "
            f"sed -i 's/interface-name={iface}/interface-name={new_iface}/g' "
            f"{{}} \\;",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(
                    f"Updated NetworkManager: {iface} -> {new_iface}"
                )
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return


def _cleanup_network_state(
    disk: Path,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Clean up stale network state files."""
    runner = runner or subprocess.run

    state_paths = [
        "/var/lib/NetworkManager/*",
        "/run/NetworkManager/*",
        "/etc/udev/rules.d/70-persistent-net.rules",
        "/etc/udev/rules.d/75-persistent-net-generator.rules",
    ]

    for path in state_paths:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", f"rm -rf {path} 2>/dev/null || true",
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                result.actions.append(f"Cleaned up: {path}")
        except FileNotFoundError:
            result.errors.append("virt-customize not found")
            result.success = False
            return
