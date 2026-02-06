"""Tests for the libvirt domain XML generator.

Validates XML structure for:
  - Ubuntu EFI + PVSCSI + vmxnet3 (virtio-scsi + virtio-net + OVMF)
  - Windows safe-mode (IDE + e1000 + BIOS)
  - CentOS BIOS + lsilogic + e1000e
"""

import xml.etree.ElementTree as ET

from vmfree.generators.libvirt import generate_libvirt_xml
from vmfree.mapper.hardware import map_hardware
from vmfree.models import (
    DiskDefinition,
    Firmware,
    NICDefinition,
    VMDefinition,
)


def _make_ubuntu_vm() -> VMDefinition:
    """Ubuntu EFI + PVSCSI + vmxnet3."""
    return VMDefinition(
        name="Ubuntu-Server-22",
        guest_os="ubuntu-64",
        memory_mb=4096,
        vcpus=4,
        firmware=Firmware.EFI,
        disks=[DiskDefinition(path="/tmp/Ubuntu-Server-22.vmdk", controller="scsi0",
                              unit=0, is_boot=True)],
        nics=[NICDefinition(virtual_dev="vmxnet3", mac_address="00:0c:29:7d:2d:68",
                            connection_type="bridged")],
        scsi_controller="pvscsi",
        display="svga",
    )


def _make_windows_vm() -> VMDefinition:
    """Windows multi-disk, static MAC."""
    return VMDefinition(
        name="Win2022-DC",
        guest_os="windows2019srvnext-64",
        memory_mb=8192,
        vcpus=2,
        firmware=Firmware.BIOS,
        disks=[
            DiskDefinition(path="/tmp/Win2022-DC.vmdk", controller="scsi0",
                           unit=0, is_boot=True),
            DiskDefinition(path="/tmp/Win2022-DC-data.vmdk", controller="scsi0",
                           unit=1, is_boot=False),
        ],
        nics=[NICDefinition(virtual_dev="vmxnet3", mac_address="00:50:56:a1:b2:c3",
                            connection_type="custom")],
        scsi_controller="lsilogic",
        display="svga",
    )


def _make_centos_vm() -> VMDefinition:
    """CentOS BIOS + lsilogic + e1000e."""
    return VMDefinition(
        name="CentOS-7",
        guest_os="centos-64",
        memory_mb=2048,
        vcpus=1,
        firmware=Firmware.BIOS,
        disks=[DiskDefinition(path="/tmp/CentOS-7.vmdk", controller="scsi0",
                              unit=0, is_boot=True)],
        nics=[NICDefinition(virtual_dev="e1000e", mac_address="00:0c:29:aa:bb:cc",
                            connection_type="nat")],
        scsi_controller="lsilogic",
        display="svga",
    )


def _parse(xml_str: str) -> ET.Element:
    """Parse XML string into element tree."""
    return ET.fromstring(xml_str)


# ---------------------------------------------------------------------------
# Ubuntu EFI + PVSCSI + vmxnet3
# ---------------------------------------------------------------------------

class TestUbuntuLibvirt:
    """Test libvirt XML generation for Ubuntu EFI VM."""

    def setup_method(self):
        self.vm = _make_ubuntu_vm()
        self.hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        self.xml = generate_libvirt_xml(
            self.vm, self.hw,
            disk_paths=["/var/lib/libvirt/images/Ubuntu-Server-22.qcow2"],
            bridge="br0",
        )
        self.root = _parse(self.xml)

    def test_domain_type(self):
        assert self.root.tag == "domain"
        assert self.root.get("type") == "kvm"

    def test_name(self):
        assert self.root.findtext("name") == "Ubuntu-Server-22"

    def test_memory(self):
        mem = self.root.find("memory")
        assert mem.text == "4096"
        assert mem.get("unit") == "MiB"

    def test_vcpus(self):
        assert self.root.findtext("vcpu") == "4"

    def test_os_type(self):
        os_type = self.root.find("os/type")
        assert os_type.text == "hvm"
        assert os_type.get("arch") == "x86_64"
        assert os_type.get("machine") == "q35"

    def test_efi_loader(self):
        loader = self.root.find("os/loader")
        assert loader is not None
        assert "OVMF" in loader.text
        assert loader.get("readonly") == "yes"
        assert loader.get("type") == "pflash"

    def test_boot_dev(self):
        boot = self.root.find("os/boot")
        assert boot.get("dev") == "hd"

    def test_features(self):
        assert self.root.find("features/acpi") is not None
        assert self.root.find("features/apic") is not None

    def test_cpu_passthrough(self):
        cpu = self.root.find("cpu")
        assert cpu.get("mode") == "host-passthrough"

    def test_disk(self):
        disk = self.root.find("devices/disk")
        assert disk.get("type") == "file"
        assert disk.get("device") == "disk"
        driver = disk.find("driver")
        assert driver.get("name") == "qemu"
        assert driver.get("type") == "qcow2"
        source = disk.find("source")
        assert source.get("file") == "/var/lib/libvirt/images/Ubuntu-Server-22.qcow2"

    def test_disk_bus_scsi(self):
        target = self.root.find("devices/disk/target")
        assert target.get("bus") == "scsi"
        assert target.get("dev") == "sda"

    def test_scsi_controller(self):
        ctrl = self.root.find("devices/controller[@type='scsi']")
        assert ctrl is not None
        assert ctrl.get("model") == "virtio-scsi"

    def test_nic_bridge(self):
        iface = self.root.find("devices/interface")
        assert iface.get("type") == "bridge"
        assert iface.find("source").get("bridge") == "br0"
        assert iface.find("model").get("type") == "virtio-net"

    def test_mac_preserved(self):
        mac = self.root.find("devices/interface/mac")
        assert mac is not None
        assert mac.get("address") == "00:0c:29:7d:2d:68"

    def test_vnc_graphics(self):
        graphics = self.root.find("devices/graphics")
        assert graphics.get("type") == "vnc"

    def test_video_display(self):
        model = self.root.find("devices/video/model")
        assert model is not None
        assert "virtio" in model.get("type")

    def test_guest_agent_channel(self):
        channel = self.root.find("devices/channel")
        assert channel.get("type") == "unix"
        target = channel.find("target")
        assert target.get("type") == "virtio"
        assert target.get("name") == "org.qemu.guest_agent.0"

    def test_valid_xml(self):
        """Ensure the output is well-formed XML."""
        ET.fromstring(self.xml)  # Would raise if malformed


# ---------------------------------------------------------------------------
# Windows safe-mode (IDE + e1000)
# ---------------------------------------------------------------------------

class TestWindowsSafeLibvirt:
    """Test libvirt XML for Windows VM in safe mode."""

    def setup_method(self):
        self.vm = _make_windows_vm()
        self.hw = map_hardware("lsilogic", "vmxnet3", "svga", "bios", windows_safe=True)
        self.xml = generate_libvirt_xml(
            self.vm, self.hw,
            disk_paths=[
                "/var/lib/libvirt/images/Win2022-DC-0.qcow2",
                "/var/lib/libvirt/images/Win2022-DC-1.qcow2",
            ],
            bridge="br0",
        )
        self.root = _parse(self.xml)

    def test_no_efi_loader(self):
        loader = self.root.find("os/loader")
        assert loader is None

    def test_two_disks(self):
        disks = self.root.findall("devices/disk")
        assert len(disks) == 2

    def test_ide_bus(self):
        target = self.root.find("devices/disk/target")
        assert target.get("bus") == "ide"
        assert target.get("dev") == "hda"

    def test_second_disk(self):
        disks = self.root.findall("devices/disk")
        target = disks[1].find("target")
        assert target.get("dev") == "hdb"
        assert target.get("bus") == "ide"

    def test_e1000_nic(self):
        model = self.root.find("devices/interface/model")
        assert model.get("type") == "e1000"

    def test_static_mac_preserved(self):
        mac = self.root.find("devices/interface/mac")
        assert mac.get("address") == "00:50:56:a1:b2:c3"

    def test_no_scsi_controller(self):
        ctrl = self.root.find("devices/controller[@type='scsi']")
        assert ctrl is None


# ---------------------------------------------------------------------------
# CentOS BIOS + lsilogic + e1000e
# ---------------------------------------------------------------------------

class TestCentosLibvirt:
    """Test libvirt XML for CentOS BIOS VM."""

    def setup_method(self):
        self.vm = _make_centos_vm()
        self.hw = map_hardware("lsilogic", "e1000e", "svga", "bios")
        self.xml = generate_libvirt_xml(
            self.vm, self.hw,
            disk_paths=["/var/lib/libvirt/images/CentOS-7.qcow2"],
            bridge="virbr0",
        )
        self.root = _parse(self.xml)

    def test_name(self):
        assert self.root.findtext("name") == "CentOS-7"

    def test_memory(self):
        assert self.root.findtext("memory") == "2048"

    def test_vcpus(self):
        assert self.root.findtext("vcpu") == "1"

    def test_no_efi(self):
        assert self.root.find("os/loader") is None

    def test_scsi_bus(self):
        target = self.root.find("devices/disk/target")
        assert target.get("bus") == "scsi"

    def test_e1000e_nic(self):
        model = self.root.find("devices/interface/model")
        assert model.get("type") == "e1000e"

    def test_bridge_virbr0(self):
        source = self.root.find("devices/interface/source")
        assert source.get("bridge") == "virbr0"

    def test_mac_preserved(self):
        mac = self.root.find("devices/interface/mac")
        assert mac.get("address") == "00:0c:29:aa:bb:cc"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestLibvirtEdgeCases:
    """Test edge cases and options."""

    def test_no_mac_when_not_preserving(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw, preserve_mac=False)
        root = _parse(xml)
        mac = root.find("devices/interface/mac")
        assert mac is None

    def test_no_mac_when_empty(self):
        vm = _make_ubuntu_vm()
        vm.nics[0].mac_address = ""
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw, preserve_mac=True)
        root = _parse(xml)
        mac = root.find("devices/interface/mac")
        assert mac is None

    def test_default_disk_paths(self):
        """When no disk_paths given, generate placeholder paths."""
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw)
        root = _parse(xml)
        source = root.find("devices/disk/source")
        assert "/var/lib/libvirt/images/" in source.get("file")

    def test_raw_format(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw, disk_format="raw",
                                   disk_paths=["/tmp/disk.raw"])
        root = _parse(xml)
        driver = root.find("devices/disk/driver")
        assert driver.get("type") == "raw"

    def test_no_nics(self):
        vm = _make_ubuntu_vm()
        vm.nics = []
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw)
        root = _parse(xml)
        iface = root.find("devices/interface")
        assert iface is None

    def test_no_disks(self):
        vm = _make_ubuntu_vm()
        vm.disks = []
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw, disk_paths=[])
        root = _parse(xml)
        disk = root.find("devices/disk")
        assert disk is None

    def test_xml_declaration(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        xml = generate_libvirt_xml(vm, hw)
        assert xml.startswith("<?xml version")
