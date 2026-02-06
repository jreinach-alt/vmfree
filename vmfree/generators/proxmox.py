"""Proxmox VE command generator.

Produces a sequence of `qm` CLI commands that create and configure a VM
on Proxmox VE from a VMDefinition and mapped hardware. Commands are
returned as a list of strings that can be executed or displayed in
dry-run mode.

Command sequence:
  1. qm create — VM shell with CPU, memory, machine type, BIOS, NIC
  2. qm importdisk — Import each converted disk into Proxmox storage
  3. qm set — Attach imported disks and configure boot order
  4. qm set --efidisk0 — Create EFI disk for UEFI guests

Critical notes:
  - Proxmox ZFS storage can't accept qcow2 directly — must use qm importdisk.
  - Windows safe-mode: --scsihw lsi --net0 e1000
  - EFI guests need an efidisk0 with pre-enrolled keys.
"""

from __future__ import annotations

from pathlib import Path

from vmfree.mapper.hardware import MappedHardware
from vmfree.models import VMDefinition


def generate_proxmox_commands(
    vm: VMDefinition,
    hw: MappedHardware,
    *,
    vmid: int = 100,
    disk_paths: list[str | Path] | None = None,
    storage: str = "local-lvm",
    bridge: str = "vmbr0",
    preserve_mac: bool = True,
    snapshot_name: str | None = None,
) -> list[str]:
    """Generate a sequence of Proxmox qm commands for VM creation.

    Args:
        vm: Parsed VM definition from VMware config.
        hw: Mapped hardware from the hardware mapper.
        vmid: Proxmox VM ID.
        disk_paths: Paths to converted disk images for import.
            If None, generates placeholder paths.
        storage: Proxmox storage target (e.g. "local-lvm", "local-zfs").
        bridge: Network bridge (default: vmbr0).
        preserve_mac: Whether to preserve original MAC addresses.
        snapshot_name: If set, create a snapshot with this name after import.

    Returns:
        Ordered list of qm command strings ready for execution.
    """
    commands: list[str] = []

    # 1. Create VM
    commands.append(_build_create_command(vm, hw, vmid, bridge, preserve_mac))

    # 2. Import disks
    if disk_paths is None:
        disk_paths = [f"{vm.name}-{i}.qcow2" for i in range(len(vm.disks))]

    for disk_path in disk_paths:
        commands.append(_build_import_command(vmid, disk_path, storage))

    # 3. Attach disks and set boot order
    commands.extend(_build_disk_attach_commands(vm, hw, vmid, storage))

    # 4. EFI disk for UEFI guests
    if hw.firmware_path:
        commands.append(_build_efidisk_command(vmid, storage))

    # 5. Snapshot after import (safety net before first boot)
    if snapshot_name:
        commands.append(
            f"qm snapshot {vmid} {snapshot_name}"
            f" --description 'VMFree pre-boot snapshot'"
        )

    return commands


def _build_create_command(
    vm: VMDefinition,
    hw: MappedHardware,
    vmid: int,
    bridge: str,
    preserve_mac: bool,
) -> str:
    """Build the qm create command."""
    parts = [
        f"qm create {vmid}",
        f"--name {vm.name}",
        f"--memory {vm.memory_mb}",
        f"--cores {vm.vcpus}",
        "--cpu x86-64-v2-AES",
        "--machine q35",
    ]

    # SCSI hardware controller
    scsihw = _map_scsihw(hw)
    parts.append(f"--scsihw {scsihw}")

    # BIOS type
    if hw.firmware_path:
        parts.append("--bios ovmf")
    else:
        parts.append("--bios seabios")

    # Network
    nic_config = _build_nic_string(vm, hw, bridge, preserve_mac)
    parts.append(f"--net0 {nic_config}")

    return " \\\n  ".join(parts)


def _build_import_command(vmid: int, disk_path: str | Path, storage: str) -> str:
    """Build a qm importdisk command."""
    return f"qm importdisk {vmid} {disk_path} {storage}"


def _build_disk_attach_commands(
    vm: VMDefinition,
    hw: MappedHardware,
    vmid: int,
    storage: str,
) -> list[str]:
    """Build commands to attach imported disks and set boot order."""
    commands: list[str] = []

    # Determine disk interface prefix
    if hw.controller == "ide":
        prefix = "ide"
    elif hw.controller in ("virtio-scsi", "nvme"):
        prefix = "scsi"
    else:
        prefix = "virtio"

    for i in range(len(vm.disks)):
        disk_ref = f"{storage}:vm-{vmid}-disk-{i}"
        commands.append(f"qm set {vmid} --{prefix}{i} {disk_ref}")

    # Set boot order to first disk
    if vm.disks:
        commands.append(f"qm set {vmid} --boot order={prefix}0")

    return commands


def _build_efidisk_command(vmid: int, storage: str) -> str:
    """Build the EFI disk creation command."""
    return f"qm set {vmid} --efidisk0 {storage}:1,efitype=4m,pre-enrolled-keys=1"


def _build_nic_string(
    vm: VMDefinition,
    hw: MappedHardware,
    bridge: str,
    preserve_mac: bool,
) -> str:
    """Build the NIC configuration string for --net0."""
    # Map NIC model to Proxmox naming
    nic_model = _map_nic_model(hw)
    parts = [f"{nic_model},bridge={bridge}"]

    if preserve_mac and vm.nics and vm.nics[0].mac_address:
        mac = vm.nics[0].mac_address.replace("-", ":")
        parts.append(f"macaddr={mac}")

    return ",".join(parts)


def _map_scsihw(hw: MappedHardware) -> str:
    """Map the KVM controller type to Proxmox --scsihw value."""
    mapping = {
        "virtio-scsi": "virtio-scsi-single",
        "ide": "lsi",
        "nvme": "virtio-scsi-single",
    }
    return mapping.get(hw.controller, "virtio-scsi-single")


def _map_nic_model(hw: MappedHardware) -> str:
    """Map the KVM NIC type to Proxmox NIC model name."""
    mapping = {
        "virtio-net": "virtio",
        "e1000": "e1000",
        "e1000e": "e1000",  # Proxmox uses e1000 for both
    }
    return mapping.get(hw.nic, "virtio")
