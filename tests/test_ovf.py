"""Tests for the OVF/OVA parser.

Tests OVF XML parsing with namespace handling, OVA tar extraction,
VM definition extraction from real-world-like OVF structures.
"""

import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vmfree.models import DiskType, Firmware
from vmfree.parsers.ovf import (
    NS_OVF,
    NS_RASD,
    _extract_disk_references,
    _extract_file_references,
    _get_rasd,
    _local_tag,
    _parse_capacity,
    parse_ova_file,
    parse_ovf_file,
    parse_ovf_or_ova,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# OVF file parsing — Ubuntu (EFI, pvscsi, vmxnet3, single disk)
# ---------------------------------------------------------------------------

class TestParseOvfUbuntu:
    """Test OVF parsing with the Ubuntu fixture."""

    def test_parse_returns_vm_definition(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.name == "Ubuntu-Server-22"

    def test_guest_os(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.guest_os == "ubuntu64Guest"

    def test_memory(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.memory_mb == 4096

    def test_vcpus(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.vcpus == 4

    def test_firmware_efi(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.firmware == Firmware.EFI
        assert vm.is_efi

    def test_hardware_version(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.hardware_version == 21

    def test_single_disk(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert len(vm.disks) == 1

    def test_disk_path_resolved(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        disk = vm.disks[0]
        assert "Ubuntu-Server-22-disk1.vmdk" in disk.path

    def test_disk_capacity(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        disk = vm.disks[0]
        assert disk.size_bytes == 68719476736  # 64 GiB

    def test_disk_is_boot(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.disks[0].is_boot is True

    def test_disk_type_stream_optimized(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.disks[0].disk_type == DiskType.STREAM_OPTIMIZED

    def test_single_nic(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert len(vm.nics) == 1

    def test_nic_vmxnet3(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.nics[0].virtual_dev == "vmxnet3"

    def test_nic_network_name(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.nics[0].network_name == "VM Network"

    def test_scsi_controller(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.scsi_controller == "virtualscsi"

    def test_source_file(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.source_file == FIXTURES / "sample-ubuntu.ovf"

    def test_not_windows(self):
        vm = parse_ovf_file(FIXTURES / "sample-ubuntu.ovf")
        assert vm.is_windows is False


# ---------------------------------------------------------------------------
# OVF file parsing — Windows (BIOS, lsilogic, e1000, two disks)
# ---------------------------------------------------------------------------

class TestParseOvfWindows:
    """Test OVF parsing with the Windows fixture."""

    def test_name(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.name == "Win2022-DC"

    def test_guest_os_windows(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert "windows" in vm.guest_os.lower()

    def test_is_windows(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.is_windows is True

    def test_memory(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.memory_mb == 8192

    def test_vcpus(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.vcpus == 2

    def test_firmware_bios(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.firmware == Firmware.BIOS

    def test_hardware_version_19(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.hardware_version == 19

    def test_two_disks(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert len(vm.disks) == 2

    def test_first_disk_is_boot(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.disks[0].is_boot is True
        assert vm.disks[1].is_boot is False

    def test_disk_capacities(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.disks[0].size_bytes == 80 * (1024 ** 3)  # 80 GiB
        assert vm.disks[1].size_bytes == 40 * (1024 ** 3)  # 40 GiB

    def test_disk_filenames(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert "Win2022-DC-disk1.vmdk" in vm.disks[0].path
        assert "Win2022-DC-disk2.vmdk" in vm.disks[1].path

    def test_nic_e1000(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert len(vm.nics) == 1
        assert vm.nics[0].virtual_dev == "e1000"

    def test_scsi_controller_lsilogic(self):
        vm = parse_ovf_file(FIXTURES / "sample-windows.ovf")
        assert vm.scsi_controller == "lsilogic"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestOvfErrors:
    """Test error handling for malformed or missing OVF files."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="OVF file not found"):
            parse_ovf_file("/nonexistent/path.ovf")

    def test_invalid_xml(self, tmp_path):
        bad_ovf = tmp_path / "bad.ovf"
        bad_ovf.write_text("This is not XML at all")
        with pytest.raises(ET.ParseError):
            parse_ovf_file(bad_ovf)

    def test_wrong_root_element(self, tmp_path):
        bad_ovf = tmp_path / "wrong.ovf"
        bad_ovf.write_text('<?xml version="1.0"?><NotAnEnvelope/>')
        with pytest.raises(ValueError, match="Not an OVF file"):
            parse_ovf_file(bad_ovf)

    def test_missing_virtual_system(self, tmp_path):
        minimal = tmp_path / "minimal.ovf"
        minimal.write_text(
            f'<?xml version="1.0"?>'
            f'<Envelope xmlns="{NS_OVF}"></Envelope>'
        )
        with pytest.raises(ValueError, match="missing VirtualSystem"):
            parse_ovf_file(minimal)

    def test_missing_virtual_hardware_section(self, tmp_path):
        minimal = tmp_path / "novhs.ovf"
        minimal.write_text(
            f'<?xml version="1.0"?>'
            f'<Envelope xmlns="{NS_OVF}">'
            f'  <VirtualSystem ovf:id="test" xmlns:ovf="{NS_OVF}">'
            f'  </VirtualSystem>'
            f'</Envelope>'
        )
        with pytest.raises(ValueError, match="missing VirtualHardwareSection"):
            parse_ovf_file(minimal)


# ---------------------------------------------------------------------------
# OVA parsing (tar archive with .ovf + .vmdk)
# ---------------------------------------------------------------------------

class TestParseOva:
    """Test OVA tar archive parsing."""

    def test_ova_not_found(self):
        with pytest.raises(FileNotFoundError, match="OVA file not found"):
            parse_ova_file("/nonexistent/path.ova")

    def test_ova_not_tar(self, tmp_path):
        fake_ova = tmp_path / "fake.ova"
        fake_ova.write_text("This is not a tar file")
        with pytest.raises(ValueError, match="Not a valid OVA"):
            parse_ova_file(fake_ova)

    def test_ova_missing_ovf(self, tmp_path):
        ova_path = tmp_path / "no-ovf.ova"
        with tarfile.open(str(ova_path), "w") as tar:
            dummy = tmp_path / "dummy.vmdk"
            dummy.write_text("not a real vmdk")
            tar.add(str(dummy), arcname="dummy.vmdk")
        with pytest.raises(ValueError, match="does not contain an .ovf"):
            parse_ova_file(ova_path)

    def test_ova_parses_contained_ovf(self, tmp_path):
        # Build an OVA containing our Ubuntu fixture
        ovf_src = FIXTURES / "sample-ubuntu.ovf"
        ova_path = tmp_path / "ubuntu.ova"
        with tarfile.open(str(ova_path), "w") as tar:
            tar.add(str(ovf_src), arcname="Ubuntu-Server-22.ovf")
        vm = parse_ova_file(ova_path)
        assert vm.name == "Ubuntu-Server-22"
        assert vm.vcpus == 4
        assert vm.memory_mb == 4096

    def test_ova_source_file_points_to_ova(self, tmp_path):
        ovf_src = FIXTURES / "sample-ubuntu.ovf"
        ova_path = tmp_path / "ubuntu.ova"
        with tarfile.open(str(ova_path), "w") as tar:
            tar.add(str(ovf_src), arcname="Ubuntu-Server-22.ovf")
        vm = parse_ova_file(ova_path)
        assert vm.source_file == ova_path

    def test_ova_efi_firmware_detected(self, tmp_path):
        ovf_src = FIXTURES / "sample-ubuntu.ovf"
        ova_path = tmp_path / "ubuntu.ova"
        with tarfile.open(str(ova_path), "w") as tar:
            tar.add(str(ovf_src), arcname="Ubuntu-Server-22.ovf")
        vm = parse_ova_file(ova_path)
        assert vm.firmware == Firmware.EFI


# ---------------------------------------------------------------------------
# parse_ovf_or_ova dispatcher
# ---------------------------------------------------------------------------

class TestParseOvfOrOva:
    """Test the auto-dispatch function."""

    def test_dispatches_ovf(self):
        vm = parse_ovf_or_ova(FIXTURES / "sample-ubuntu.ovf")
        assert vm.name == "Ubuntu-Server-22"

    def test_dispatches_ova(self, tmp_path):
        ovf_src = FIXTURES / "sample-windows.ovf"
        ova_path = tmp_path / "windows.ova"
        with tarfile.open(str(ova_path), "w") as tar:
            tar.add(str(ovf_src), arcname="Win2022-DC.ovf")
        vm = parse_ovf_or_ova(ova_path)
        assert vm.name == "Win2022-DC"


# ---------------------------------------------------------------------------
# Internal helper tests
# ---------------------------------------------------------------------------

class TestCapacityParsing:
    """Test capacity unit conversion."""

    def test_bytes(self):
        assert _parse_capacity("1073741824", "byte") == 1073741824

    def test_gigabytes(self):
        assert _parse_capacity("64", "byte * 2^30") == 64 * (1024 ** 3)

    def test_megabytes(self):
        assert _parse_capacity("512", "byte * 2^20") == 512 * (1024 ** 2)

    def test_kilobytes(self):
        assert _parse_capacity("1024", "byte * 2^10") == 1024 * 1024

    def test_gb_label(self):
        assert _parse_capacity("10", "GigaBytes") == 10 * (1024 ** 3)

    def test_mb_label(self):
        assert _parse_capacity("256", "MegaBytes") == 256 * (1024 ** 2)

    def test_invalid_number(self):
        assert _parse_capacity("abc", "byte") == 0


class TestLocalTag:
    """Test XML namespace stripping."""

    def test_with_namespace(self):
        assert _local_tag("{http://example.com}Item") == "Item"

    def test_without_namespace(self):
        assert _local_tag("Item") == "Item"

    def test_complex_namespace(self):
        assert _local_tag(f"{{{NS_RASD}}}ResourceType") == "ResourceType"


class TestFileReferences:
    """Test References/File extraction."""

    def test_extract_from_ubuntu_ovf(self):
        tree = ET.parse(FIXTURES / "sample-ubuntu.ovf")
        refs = _extract_file_references(tree.getroot())
        assert "file1" in refs
        assert refs["file1"] == "Ubuntu-Server-22-disk1.vmdk"

    def test_extract_from_windows_ovf(self):
        tree = ET.parse(FIXTURES / "sample-windows.ovf")
        refs = _extract_file_references(tree.getroot())
        assert len(refs) == 2
        assert refs["file1"] == "Win2022-DC-disk1.vmdk"
        assert refs["file2"] == "Win2022-DC-disk2.vmdk"

    def test_empty_references(self):
        root = ET.fromstring(f'<Envelope xmlns="{NS_OVF}"/>')
        refs = _extract_file_references(root)
        assert refs == {}


class TestDiskReferences:
    """Test DiskSection/Disk extraction."""

    def test_extract_from_ubuntu_ovf(self):
        tree = ET.parse(FIXTURES / "sample-ubuntu.ovf")
        disks = _extract_disk_references(tree.getroot())
        assert "vmdisk1" in disks
        file_ref, capacity = disks["vmdisk1"]
        assert file_ref == "file1"
        assert capacity == 68719476736

    def test_extract_from_windows_ovf(self):
        tree = ET.parse(FIXTURES / "sample-windows.ovf")
        disks = _extract_disk_references(tree.getroot())
        assert len(disks) == 2
        _, cap1 = disks["vmdisk1"]
        _, cap2 = disks["vmdisk2"]
        assert cap1 == 80 * (1024 ** 3)
        assert cap2 == 40 * (1024 ** 3)


class TestGetRasd:
    """Test RASD field extraction from Item elements."""

    def test_gets_resource_type(self):
        xml = f"""<Item xmlns:rasd="{NS_RASD}">
            <rasd:ResourceType>3</rasd:ResourceType>
        </Item>"""
        item = ET.fromstring(xml)
        assert _get_rasd(item, "ResourceType") == "3"

    def test_returns_empty_for_missing(self):
        xml = f"""<Item xmlns:rasd="{NS_RASD}">
            <rasd:ResourceType>3</rasd:ResourceType>
        </Item>"""
        item = ET.fromstring(xml)
        assert _get_rasd(item, "NonExistent") == ""

    def test_strips_whitespace(self):
        xml = f"""<Item xmlns:rasd="{NS_RASD}">
            <rasd:Description>  Some text  </rasd:Description>
        </Item>"""
        item = ET.fromstring(xml)
        assert _get_rasd(item, "Description") == "Some text"
