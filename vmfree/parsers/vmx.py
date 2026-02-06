"""VMX file parser.

VMX files are VMware's virtual machine configuration format — plain text
key-value pairs that describe every aspect of a VM. This module reads a
VMX file and produces a VMDefinition dataclass.

Key format quirks:
  - Keys are case-insensitive (we normalize to lowercase).
  - Values are quoted strings: key = "value"
  - Comments start with # (rare in practice).
  - Disk entries use hierarchical keys: scsi0:0.fileName = "disk.vmdk"
  - NIC entries use: ethernet0.virtualDev = "vmxnet3"
  - Boolean values are strings "TRUE"/"FALSE".
"""

from __future__ import annotations

from pathlib import Path

from vmfree.models import DiskDefinition, DiskType, Firmware, NICDefinition, VMDefinition
from vmfree.parsers.vmdk import inspect_vmdk


def parse_vmx_file(path: str | Path) -> VMDefinition:
    """Parse a VMX file and return a VMDefinition.

    Args:
        path: Path to the .vmx file.

    Returns:
        A fully populated VMDefinition.

    Raises:
        FileNotFoundError: If the VMX file does not exist.
        ValueError: If the file is missing critical fields (displayName).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"VMX file not found: {path}")

    raw = path.read_text(encoding="utf-8", errors="replace")
    entries = parse_vmx_keyvalues(raw)

    name = entries.get("displayname", "")
    if not name:
        raise ValueError(f"VMX file missing displayName: {path}")

    guest_os = entries.get("guestos", "")
    memory_mb = int(entries.get("memsize", "1024"))
    vcpus = int(entries.get("numvcpus", "1"))
    hw_version = int(entries.get("virtualhw.version", "0"))

    firmware_str = entries.get("firmware", "bios").lower()
    firmware = Firmware.EFI if firmware_str == "efi" else Firmware.BIOS

    scsi_controller = _extract_scsi_controller(entries)
    disks = _extract_disks(entries, path.parent)
    nics = _extract_nics(entries)
    display = _extract_display(entries)

    return VMDefinition(
        name=name,
        guest_os=guest_os,
        memory_mb=memory_mb,
        vcpus=vcpus,
        firmware=firmware,
        disks=disks,
        nics=nics,
        scsi_controller=scsi_controller,
        display=display,
        source_file=path,
        hardware_version=hw_version,
    )


def parse_vmx_keyvalues(text: str) -> dict[str, str]:
    """Parse VMX text into a lowercase-key dict of string values.

    Handles:
      - Quoted and unquoted values.
      - Comment lines (# prefix).
      - Blank lines.
      - The .encoding directive.

    Args:
        text: Raw VMX file content.

    Returns:
        Dict mapping lowercase keys to unquoted string values.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip().strip('"')
        result[key] = value
    return result


def _extract_scsi_controller(entries: dict[str, str]) -> str:
    """Extract the primary SCSI controller type from VMX entries."""
    return entries.get("scsi0.virtualdev", "")


def _extract_display(entries: dict[str, str]) -> str:
    """Extract the display adapter type."""
    if entries.get("svga.present", "").lower() == "true":
        return "svga"
    return entries.get("svga.vramsize", "svga") and "svga"


def _extract_disks(entries: dict[str, str], vmx_dir: Path) -> list[DiskDefinition]:
    """Extract all disk definitions from VMX entries.

    Scans for scsi*:*.fileName, ide*:*.fileName, and sata*:*.fileName keys.
    Filters out CDROM/ISO devices — these are not convertible disks.
    Marks the first disk on the first controller as the boot disk.
    """
    disks: list[DiskDefinition] = []
    seen: set[tuple[str, int]] = set()

    for bus_prefix in ("scsi", "ide", "sata"):
        for bus_id in range(4):
            controller = f"{bus_prefix}{bus_id}"
            for unit_id in range(16):
                present_key = f"{controller}:{unit_id}.present"
                filename_key = f"{controller}:{unit_id}.filename"
                device_type_key = f"{controller}:{unit_id}.devicetype"

                if entries.get(present_key, "").lower() != "true":
                    continue
                filename = entries.get(filename_key, "")
                if not filename:
                    continue

                # Skip CDROM/ISO devices — not convertible disks
                device_type = entries.get(device_type_key, "").lower()
                if "cdrom" in device_type or "atapi" in device_type:
                    continue
                if filename.lower().endswith(".iso"):
                    continue
                # "auto detect" is a CDROM with no media
                if filename.lower() == "auto detect":
                    continue

                disk_path = str(vmx_dir / filename)

                disk_key = (controller, unit_id)
                if disk_key in seen:
                    continue
                seen.add(disk_key)

                # Try to inspect the VMDK descriptor for size and type
                size_bytes = 0
                disk_type = DiskType.UNKNOWN
                disk_file = vmx_dir / filename
                if disk_file.exists() and disk_file.suffix.lower() == ".vmdk":
                    try:
                        vmdk_info = inspect_vmdk(disk_file)
                        size_bytes = vmdk_info.virtual_size_bytes
                        disk_type = vmdk_info.disk_type
                    except (ValueError, OSError):
                        pass

                is_boot = len(disks) == 0
                disks.append(DiskDefinition(
                    path=disk_path,
                    controller=controller,
                    unit=unit_id,
                    size_bytes=size_bytes,
                    disk_type=disk_type,
                    is_boot=is_boot,
                ))

    return disks


def _extract_nics(entries: dict[str, str]) -> list[NICDefinition]:
    """Extract all NIC definitions from VMX entries.

    Scans for ethernet0..ethernet9 entries. Handles both generated and
    static MAC addresses.
    """
    nics: list[NICDefinition] = []

    for nic_id in range(10):
        prefix = f"ethernet{nic_id}"
        present_key = f"{prefix}.present"

        if entries.get(present_key, "").lower() != "true":
            continue

        virtual_dev = entries.get(f"{prefix}.virtualdev", "e1000")
        connection_type = entries.get(f"{prefix}.connectiontype", "bridged")
        network_name = entries.get(f"{prefix}.networkname", "")

        # MAC address can be static or generated
        address_type = entries.get(f"{prefix}.addresstype", "")
        if address_type == "static":
            mac = entries.get(f"{prefix}.address", "")
        else:
            mac = entries.get(f"{prefix}.generatedaddress", "")

        nics.append(NICDefinition(
            virtual_dev=virtual_dev,
            mac_address=mac,
            connection_type=connection_type,
            network_name=network_name,
        ))

    return nics
