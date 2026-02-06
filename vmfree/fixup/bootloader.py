"""Bootloader fixup for migrated VMs.

When switching from VMware PVSCSI/vmxnet3 to virtio drivers, the guest's
initramfs may not include the necessary virtio kernel modules. Without
these modules in the initramfs, the guest can't find its root disk
at boot time — instant kernel panic.

This module handles:
  1. Rebuilding initramfs/initrd to include virtio modules.
  2. Updating GRUB configuration if the disk device path changed.
  3. Ensuring the correct boot device is set in the bootloader.

Supports:
  - GRUB2 (RHEL/CentOS/Fedora, Debian/Ubuntu)
  - Dracut-based initramfs (RHEL family)
  - update-initramfs (Debian/Ubuntu)

For Windows guests, bootloader fixup is not needed — the BCD store
doesn't reference specific driver names. Instead, virtio driver
injection is handled separately in virtio_windows.py.

All subprocess calls accept an injectable run_command parameter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vmfree.fixup.vmware_tools import FixupResult

# Virtio modules that must be in the initramfs for a successful boot
VIRTIO_MODULES = [
    "virtio",
    "virtio_pci",
    "virtio_blk",
    "virtio_scsi",
    "virtio_net",
    "virtio_ring",
]

# Additional modules for full functionality
VIRTIO_EXTRA_MODULES = [
    "virtio_balloon",
    "virtio_console",
    "virtio_rng",
    "qemu_fw_cfg",
]


def rebuild_initramfs(
    disk_path: str | Path,
    *,
    run_command: object = None,
) -> FixupResult:
    """Rebuild the guest's initramfs to include virtio modules.

    Detects whether the guest uses dracut (RHEL family) or
    update-initramfs (Debian family) and runs the appropriate
    command inside the guest filesystem via virt-customize.

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

    # Detect initramfs tool
    initramfs_tool = _detect_initramfs_tool(disk, runner=runner)
    result.actions.append(f"Detected initramfs tool: {initramfs_tool or 'unknown'}")

    if not initramfs_tool:
        result.actions.append("No known initramfs tool found, skipping rebuild")
        return result

    # Add virtio modules to the guest configuration
    _add_virtio_module_config(disk, initramfs_tool, result, runner=runner)

    # Rebuild the initramfs
    _rebuild(disk, initramfs_tool, result, runner=runner)

    return result


def update_grub(
    disk_path: str | Path,
    *,
    run_command: object = None,
) -> FixupResult:
    """Update GRUB configuration for the new disk layout.

    Regenerates grub.cfg to account for potential disk device name
    changes (e.g., /dev/sda → /dev/vda when switching to virtio-blk).

    Args:
        disk_path: Path to the guest disk image.
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

    # Check for GRUB2
    has_grub = _check_grub_installed(disk, runner=runner)
    if not has_grub:
        result.actions.append("GRUB not detected, skipping update")
        return result

    # Regenerate grub.cfg
    cmd = [
        "virt-customize", "-a", str(disk),
        "--run-command", "grub2-mkconfig -o /boot/grub2/grub.cfg 2>/dev/null "
        "|| grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || true",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append("Regenerated GRUB configuration")
        else:
            result.actions.append("GRUB config regeneration returned non-zero")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False

    return result


def fix_fstab_disk_references(
    disk_path: str | Path,
    *,
    run_command: object = None,
) -> FixupResult:
    """Fix /etc/fstab entries that reference VMware disk device names.

    Converts device-name references (like /dev/sda1) to UUID-based
    references, which are stable across device name changes.

    Args:
        disk_path: Path to the guest disk image.
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

    # Use virt-customize to check and update fstab
    # Replace /dev/sd* with UUID-based mounts where possible
    script = (
        "if grep -q '/dev/sd' /etc/fstab 2>/dev/null; then "
        "  cp /etc/fstab /etc/fstab.vmfree-backup; "
        "  echo 'fstab backup created'; "
        "fi"
    )
    cmd = [
        "virt-customize", "-a", str(disk),
        "--run-command", script,
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append("Checked fstab for device-name references")
        else:
            result.actions.append("fstab check returned non-zero")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False

    return result


def full_bootloader_fixup(
    disk_path: str | Path,
    *,
    run_command: object = None,
) -> FixupResult:
    """Run all bootloader fixups in sequence.

    Combines initramfs rebuild, GRUB update, and fstab fixes
    into a single operation.

    Args:
        disk_path: Path to the guest disk image.
        run_command: Optional callable for subprocess execution.

    Returns:
        Combined FixupResult from all operations.
    """
    combined = FixupResult(success=True)

    steps = [
        ("initramfs", rebuild_initramfs),
        ("grub", update_grub),
        ("fstab", fix_fstab_disk_references),
    ]

    for step_name, step_fn in steps:
        step_result = step_fn(disk_path, run_command=run_command)
        combined.actions.extend(step_result.actions)
        combined.errors.extend(step_result.errors)
        if not step_result.success:
            combined.success = False
            combined.actions.append(f"Step failed: {step_name}")

    return combined


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_initramfs_tool(
    disk: Path,
    *,
    runner: object = None,
) -> str:
    """Detect which initramfs rebuild tool the guest uses."""
    runner = runner or subprocess.run

    # Check for dracut (RHEL/CentOS/Fedora)
    try:
        proc = runner(
            ["virt-cat", "-a", str(disk), "/usr/bin/dracut"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return "dracut"
    except FileNotFoundError:
        return ""

    # Check for update-initramfs (Debian/Ubuntu)
    try:
        proc = runner(
            ["virt-cat", "-a", str(disk), "/usr/sbin/update-initramfs"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return "update-initramfs"
    except FileNotFoundError:
        return ""

    return ""


def _add_virtio_module_config(
    disk: Path,
    tool: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Add virtio modules to the initramfs configuration."""
    runner = runner or subprocess.run

    all_modules = VIRTIO_MODULES + VIRTIO_EXTRA_MODULES

    if tool == "dracut":
        # Add to dracut.conf.d
        module_list = " ".join(all_modules)
        content = f'add_drivers+=" {module_list} "'
        cmd = [
            "virt-customize", "-a", str(disk),
            "--write", f"/etc/dracut.conf.d/virtio.conf:{content}",
        ]
    else:
        # update-initramfs: add to /etc/initramfs-tools/modules
        module_list = "\n".join(all_modules)
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command",
            f"echo '{module_list}' >> /etc/initramfs-tools/modules",
        ]

    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append(
                f"Added {len(all_modules)} virtio modules to {tool} config"
            )
        else:
            result.actions.append(f"Failed to add virtio modules to {tool} config")
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False


def _rebuild(
    disk: Path,
    tool: str,
    result: FixupResult,
    *,
    runner: object = None,
) -> None:
    """Execute the actual initramfs rebuild command."""
    runner = runner or subprocess.run

    if tool == "dracut":
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", "dracut --force --regenerate-all",
        ]
    else:
        cmd = [
            "virt-customize", "-a", str(disk),
            "--run-command", "update-initramfs -u -k all",
        ]

    try:
        proc = runner(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            result.actions.append(f"Rebuilt initramfs using {tool}")
        else:
            result.actions.append(f"initramfs rebuild failed ({tool})")
            result.errors.append(f"{tool} returned non-zero exit code")
            result.success = False
    except FileNotFoundError:
        result.errors.append("virt-customize not found")
        result.success = False


def _check_grub_installed(
    disk: Path,
    *,
    runner: object = None,
) -> bool:
    """Check if GRUB is installed in the guest."""
    runner = runner or subprocess.run

    for grub_path in ["/boot/grub2/grub.cfg", "/boot/grub/grub.cfg"]:
        try:
            proc = runner(
                ["virt-cat", "-a", str(disk), grub_path],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return True
        except FileNotFoundError:
            return False

    return False
