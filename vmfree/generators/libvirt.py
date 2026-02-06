"""Libvirt domain XML generator.

Produces a complete, valid libvirt domain XML document from a VMDefinition
and mapped hardware configuration. Uses xml.etree.ElementTree for proper
XML construction — no string concatenation.

Output XML follows libvirt's domain format specification and includes:
  - q35 machine type (modern chipset with PCIe)
  - EFI (OVMF) or BIOS boot
  - virtio-scsi or IDE storage controller
  - virtio-net or e1000/e1000e network interfaces
  - VNC graphics with virtio-vga display
  - QEMU guest agent channel
  - MAC address preservation
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from vmfree.mapper.hardware import MappedHardware
from vmfree.models import VMDefinition


def generate_libvirt_xml(
    vm: VMDefinition,
    hw: MappedHardware,
    *,
    disk_paths: list[str | Path] | None = None,
    disk_format: str = "qcow2",
    bridge: str = "br0",
    preserve_mac: bool = True,
) -> str:
    """Generate a complete libvirt domain XML string.

    Args:
        vm: Parsed VM definition from VMware config.
        hw: Mapped hardware from the hardware mapper.
        disk_paths: Paths to converted disk images (qcow2/raw).
            If None, generates placeholder paths based on VM name.
        disk_format: Format of the converted disks ("qcow2" or "raw").
        bridge: Host bridge interface to attach NICs to.
        preserve_mac: Whether to preserve original MAC addresses.

    Returns:
        Pretty-printed libvirt domain XML string.
    """
    domain = ET.Element("domain", type="kvm")

    # Basic VM identity
    ET.SubElement(domain, "name").text = vm.name
    ET.SubElement(domain, "memory", unit="MiB").text = str(vm.memory_mb)
    ET.SubElement(domain, "vcpu").text = str(vm.vcpus)

    # OS section
    _build_os(domain, vm, hw)

    # Features
    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")

    # CPU
    ET.SubElement(domain, "cpu", mode="host-passthrough")

    # Devices
    devices = ET.SubElement(domain, "devices")
    _build_disks(devices, vm, hw, disk_paths, disk_format)
    _build_controllers(devices, hw)
    _build_nics(devices, vm, hw, bridge, preserve_mac)
    _build_graphics(devices, hw)
    _build_guest_agent(devices)

    return _indent_xml(domain)


def _build_os(domain: ET.Element, vm: VMDefinition, hw: MappedHardware) -> None:
    """Build the <os> section with boot config and firmware."""
    os_elem = ET.SubElement(domain, "os")
    os_type = ET.SubElement(os_elem, "type", arch="x86_64", machine="q35")
    os_type.text = "hvm"

    if hw.firmware_path:
        ET.SubElement(
            os_elem, "loader",
            readonly="yes",
            type="pflash",
        ).text = hw.firmware_path

    ET.SubElement(os_elem, "boot", dev="hd")


def _build_disks(
    devices: ET.Element,
    vm: VMDefinition,
    hw: MappedHardware,
    disk_paths: list[str | Path] | None,
    disk_format: str,
) -> None:
    """Build <disk> elements for each virtual disk."""
    if disk_paths is None:
        disk_paths = [
            f"/var/lib/libvirt/images/{vm.name}-{i}.{disk_format}"
            for i in range(len(vm.disks))
        ]

    # Determine bus type and device prefix based on controller
    if hw.controller == "ide":
        bus = "ide"
        dev_prefix = "hd"
    elif hw.controller == "virtio-scsi":
        bus = "scsi"
        dev_prefix = "sd"
    else:
        bus = "virtio"
        dev_prefix = "vd"

    dev_letters = "abcdefghijklmnop"

    for i, (_disk_def, disk_path) in enumerate(zip(vm.disks, disk_paths, strict=False)):
        disk = ET.SubElement(devices, "disk", type="file", device="disk")
        ET.SubElement(disk, "driver", name="qemu", type=disk_format, cache="none")
        ET.SubElement(disk, "source", file=str(disk_path))
        dev_name = f"{dev_prefix}{dev_letters[i]}"
        ET.SubElement(disk, "target", dev=dev_name, bus=bus)


def _build_controllers(devices: ET.Element, hw: MappedHardware) -> None:
    """Build storage controller elements."""
    if hw.controller == "virtio-scsi":
        ET.SubElement(
            devices, "controller",
            type="scsi",
            model="virtio-scsi",
            index="0",
        )


def _build_nics(
    devices: ET.Element,
    vm: VMDefinition,
    hw: MappedHardware,
    bridge: str,
    preserve_mac: bool,
) -> None:
    """Build <interface> elements for each NIC."""
    for nic in vm.nics:
        iface = ET.SubElement(devices, "interface", type="bridge")
        ET.SubElement(iface, "source", bridge=bridge)
        ET.SubElement(iface, "model", type=hw.nic)

        if preserve_mac and nic.mac_address:
            ET.SubElement(iface, "mac", address=nic.mac_address)


def _build_graphics(devices: ET.Element, hw: MappedHardware) -> None:
    """Build VNC graphics and video display elements."""
    ET.SubElement(devices, "graphics", type="vnc", port="-1", listen="0.0.0.0")
    video = ET.SubElement(devices, "video")
    ET.SubElement(video, "model", type=hw.display.replace("-", ""))


def _build_guest_agent(devices: ET.Element) -> None:
    """Build QEMU guest agent channel."""
    channel = ET.SubElement(devices, "channel", type="unix")
    ET.SubElement(
        channel, "target",
        type="virtio",
        name="org.qemu.guest_agent.0",
    )


def _indent_xml(elem: ET.Element) -> str:
    """Pretty-print an XML element tree with proper indentation."""
    ET.indent(elem, space="  ")
    return ET.tostring(elem, encoding="unicode", xml_declaration=True) + "\n"
