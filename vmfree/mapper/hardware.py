"""VMware to KVM hardware device translation.

Maps VMware virtual hardware types to their KVM/QEMU equivalents.
This is the core of what makes a migrated VM boot correctly — wrong
mappings mean the guest can't find its disk or network.

Critical gotcha for Windows: switching PVSCSI/vmxnet3 directly to virtio
without injecting drivers causes a BSOD. For Windows guests, we default
to safe fallbacks (IDE + e1000) unless the caller explicitly requests
virtio with driver injection.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------

CONTROLLER_MAP: dict[str, str] = {
    "pvscsi": "virtio-scsi",
    "lsilogic": "virtio-scsi",
    "lsisas1068": "virtio-scsi",
    "buslogic": "virtio-scsi",
    "ahci": "virtio-scsi",
    "nvme": "nvme",
}

NIC_MAP: dict[str, str] = {
    "vmxnet3": "virtio-net",
    "e1000": "e1000",
    "e1000e": "e1000e",
    "vlance": "virtio-net",
}

DISPLAY_MAP: dict[str, str] = {
    "svga": "virtio-vga",
    "vmware": "virtio-vga",
}

FIRMWARE_MAP: dict[str, str | None] = {
    "efi": "/usr/share/OVMF/OVMF_CODE.fd",
    "bios": None,
}

# Safe fallbacks for Windows guests — avoids BSOD from missing virtio drivers.
WINDOWS_SAFE_CONTROLLER = "ide"
WINDOWS_SAFE_NIC = "e1000"


@dataclass
class MappedHardware:
    """Result of mapping VMware hardware to KVM equivalents.

    Attributes:
        controller: KVM storage controller (virtio-scsi, ide, nvme, etc.).
        nic: KVM NIC model (virtio-net, e1000, e1000e).
        display: KVM display adapter (virtio-vga, qxl).
        firmware_path: Path to OVMF firmware, or None for SeaBIOS.
        is_safe_mode: True if Windows-safe fallbacks were applied.
    """

    controller: str
    nic: str
    display: str
    firmware_path: str | None
    is_safe_mode: bool = False


def map_controller(vmware_type: str, *, windows_safe: bool = False) -> str:
    """Map a VMware storage controller to its KVM equivalent.

    Args:
        vmware_type: VMware controller type (pvscsi, lsilogic, etc.).
        windows_safe: If True, return IDE instead of virtio to avoid BSOD.

    Returns:
        KVM controller type string.
    """
    if windows_safe:
        return WINDOWS_SAFE_CONTROLLER
    return CONTROLLER_MAP.get(vmware_type.lower(), "virtio-scsi")


def map_nic(vmware_type: str, *, windows_safe: bool = False) -> str:
    """Map a VMware NIC type to its KVM equivalent.

    Args:
        vmware_type: VMware NIC type (vmxnet3, e1000, e1000e, vlance).
        windows_safe: If True, return e1000 instead of virtio to avoid BSOD.

    Returns:
        KVM NIC model string.
    """
    if windows_safe:
        return WINDOWS_SAFE_NIC
    return NIC_MAP.get(vmware_type.lower(), "virtio-net")


def map_display(vmware_type: str) -> str:
    """Map a VMware display adapter to its KVM equivalent.

    Args:
        vmware_type: VMware display type (svga, vmware).

    Returns:
        KVM display model string.
    """
    return DISPLAY_MAP.get(vmware_type.lower(), "virtio-vga")


def map_firmware(firmware_type: str) -> str | None:
    """Map firmware type to OVMF path (or None for BIOS/SeaBIOS).

    Args:
        firmware_type: "efi" or "bios".

    Returns:
        Path to OVMF firmware file, or None for SeaBIOS.
    """
    return FIRMWARE_MAP.get(firmware_type.lower())


def map_hardware(
    scsi_controller: str,
    nic_type: str,
    display: str,
    firmware: str,
    *,
    windows_safe: bool = False,
) -> MappedHardware:
    """Map all VMware hardware to KVM equivalents in one call.

    Args:
        scsi_controller: VMware SCSI controller type.
        nic_type: VMware NIC type.
        display: VMware display type.
        firmware: "efi" or "bios".
        windows_safe: Use safe fallbacks for Windows guests.

    Returns:
        MappedHardware with all translated device types.
    """
    return MappedHardware(
        controller=map_controller(scsi_controller, windows_safe=windows_safe),
        nic=map_nic(nic_type, windows_safe=windows_safe),
        display=map_display(display),
        firmware_path=map_firmware(firmware),
        is_safe_mode=windows_safe,
    )
