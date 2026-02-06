"""Tests for the VMX parser.

Covers three fixture VMs:
  - sample-ubuntu.vmx: EFI + PVSCSI + vmxnet3 (bridged, generated MAC)
  - sample-windows.vmx: Multi-disk, static MAC, Windows guestOS, custom network
  - sample-bios.vmx: BIOS + lsilogic + e1000e + NAT
"""

from pathlib import Path

import pytest

from vmfree.models import Firmware
from vmfree.parsers.vmx import parse_vmx_file, parse_vmx_keyvalues

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Low-level key-value parser tests
# ---------------------------------------------------------------------------

class TestParseVmxKeyValues:
    """Tests for the raw key=value parser."""

    def test_basic_keyvalue(self):
        text = 'displayName = "MyVM"'
        result = parse_vmx_keyvalues(text)
        assert result["displayname"] == "MyVM"

    def test_keys_lowercased(self):
        text = 'DisplayName = "Test"'
        result = parse_vmx_keyvalues(text)
        assert "displayname" in result

    def test_strips_quotes(self):
        text = 'memsize = "4096"'
        result = parse_vmx_keyvalues(text)
        assert result["memsize"] == "4096"

    def test_ignores_comments(self):
        text = '# this is a comment\ndisplayName = "VM"'
        result = parse_vmx_keyvalues(text)
        assert "displayname" in result
        assert len(result) == 1

    def test_ignores_blank_lines(self):
        text = '\n\ndisplayName = "VM"\n\n'
        result = parse_vmx_keyvalues(text)
        assert result["displayname"] == "VM"

    def test_encoding_directive(self):
        text = '.encoding = "UTF-8"\ndisplayName = "VM"'
        result = parse_vmx_keyvalues(text)
        assert result[".encoding"] == "UTF-8"
        assert result["displayname"] == "VM"

    def test_hierarchical_keys(self):
        text = 'scsi0:0.fileName = "disk.vmdk"'
        result = parse_vmx_keyvalues(text)
        assert result["scsi0:0.filename"] == "disk.vmdk"

    def test_unquoted_value(self):
        text = "numvcpus = 2"
        result = parse_vmx_keyvalues(text)
        assert result["numvcpus"] == "2"


# ---------------------------------------------------------------------------
# Ubuntu fixture: EFI + PVSCSI + vmxnet3
# ---------------------------------------------------------------------------

class TestUbuntuVmx:
    """Parse sample-ubuntu.vmx — EFI, PVSCSI, vmxnet3, bridged."""

    @pytest.fixture
    def vm(self):
        return parse_vmx_file(FIXTURES / "sample-ubuntu.vmx")

    def test_name(self, vm):
        assert vm.name == "Ubuntu-Server-22"

    def test_guest_os(self, vm):
        assert vm.guest_os == "ubuntu-64"

    def test_memory(self, vm):
        assert vm.memory_mb == 4096

    def test_vcpus(self, vm):
        assert vm.vcpus == 4

    def test_firmware_efi(self, vm):
        assert vm.firmware == Firmware.EFI
        assert vm.is_efi is True

    def test_scsi_controller_pvscsi(self, vm):
        assert vm.scsi_controller == "pvscsi"

    def test_not_windows(self, vm):
        assert vm.is_windows is False

    def test_hardware_version(self, vm):
        assert vm.hardware_version == 21

    def test_single_disk(self, vm):
        assert len(vm.disks) == 1

    def test_disk_path(self, vm):
        disk = vm.disks[0]
        assert disk.path.endswith("Ubuntu-Server-22.vmdk")

    def test_disk_controller(self, vm):
        assert vm.disks[0].controller == "scsi0"
        assert vm.disks[0].unit == 0

    def test_boot_disk(self, vm):
        assert vm.disks[0].is_boot is True
        assert vm.boot_disk is vm.disks[0]

    def test_single_nic(self, vm):
        assert len(vm.nics) == 1

    def test_nic_vmxnet3(self, vm):
        assert vm.nics[0].virtual_dev == "vmxnet3"

    def test_nic_mac_generated(self, vm):
        assert vm.nics[0].mac_address == "00:0c:29:7d:2d:68"

    def test_nic_bridged(self, vm):
        assert vm.nics[0].connection_type == "bridged"

    def test_display(self, vm):
        assert vm.display == "svga"

    def test_source_file(self, vm):
        assert vm.source_file == FIXTURES / "sample-ubuntu.vmx"


# ---------------------------------------------------------------------------
# Windows fixture: multi-disk, static MAC, Windows guestOS
# ---------------------------------------------------------------------------

class TestWindowsVmx:
    """Parse sample-windows.vmx — multi-disk, static MAC, Windows guest."""

    @pytest.fixture
    def vm(self):
        return parse_vmx_file(FIXTURES / "sample-windows.vmx")

    def test_name(self, vm):
        assert vm.name == "Win2022-DC"

    def test_guest_os(self, vm):
        assert vm.guest_os == "windows2019srvnext-64"

    def test_is_windows(self, vm):
        assert vm.is_windows is True

    def test_memory(self, vm):
        assert vm.memory_mb == 8192

    def test_vcpus(self, vm):
        assert vm.vcpus == 2

    def test_firmware_bios_default(self, vm):
        assert vm.firmware == Firmware.BIOS
        assert vm.is_efi is False

    def test_scsi_controller(self, vm):
        assert vm.scsi_controller == "lsilogic"

    def test_two_disks(self, vm):
        assert len(vm.disks) == 2

    def test_first_disk_is_boot(self, vm):
        assert vm.disks[0].is_boot is True
        assert vm.disks[0].path.endswith("Win2022-DC.vmdk")

    def test_second_disk_not_boot(self, vm):
        assert vm.disks[1].is_boot is False
        assert vm.disks[1].path.endswith("Win2022-DC-data.vmdk")

    def test_disks_on_same_controller(self, vm):
        assert vm.disks[0].controller == "scsi0"
        assert vm.disks[1].controller == "scsi0"
        assert vm.disks[0].unit == 0
        assert vm.disks[1].unit == 1

    def test_static_mac_address(self, vm):
        assert vm.nics[0].mac_address == "00:50:56:a1:b2:c3"

    def test_custom_connection_type(self, vm):
        assert vm.nics[0].connection_type == "custom"

    def test_network_name(self, vm):
        assert vm.nics[0].network_name == "VM Network"

    def test_nic_vmxnet3(self, vm):
        assert vm.nics[0].virtual_dev == "vmxnet3"

    def test_hardware_version(self, vm):
        assert vm.hardware_version == 19


# ---------------------------------------------------------------------------
# BIOS fixture: lsilogic + e1000e + NAT
# ---------------------------------------------------------------------------

class TestBiosVmx:
    """Parse sample-bios.vmx — BIOS, lsilogic, e1000e, NAT."""

    @pytest.fixture
    def vm(self):
        return parse_vmx_file(FIXTURES / "sample-bios.vmx")

    def test_name(self, vm):
        assert vm.name == "CentOS-7"

    def test_guest_os(self, vm):
        assert vm.guest_os == "centos-64"

    def test_memory(self, vm):
        assert vm.memory_mb == 2048

    def test_vcpus(self, vm):
        assert vm.vcpus == 1

    def test_firmware_bios(self, vm):
        assert vm.firmware == Firmware.BIOS
        assert vm.is_efi is False

    def test_scsi_controller_lsilogic(self, vm):
        assert vm.scsi_controller == "lsilogic"

    def test_not_windows(self, vm):
        assert vm.is_windows is False

    def test_single_disk(self, vm):
        assert len(vm.disks) == 1
        assert vm.disks[0].path.endswith("CentOS-7.vmdk")
        assert vm.disks[0].is_boot is True

    def test_nic_e1000e(self, vm):
        assert vm.nics[0].virtual_dev == "e1000e"

    def test_nic_nat(self, vm):
        assert vm.nics[0].connection_type == "nat"

    def test_nic_generated_mac(self, vm):
        assert vm.nics[0].mac_address == "00:0c:29:aa:bb:cc"

    def test_hardware_version(self, vm):
        assert vm.hardware_version == 14


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestVmxErrors:
    """Test error cases for the VMX parser."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_vmx_file("/nonexistent/path.vmx")

    def test_missing_display_name(self, tmp_path):
        vmx = tmp_path / "bad.vmx"
        vmx.write_text('guestOS = "linux"\n')
        with pytest.raises(ValueError, match="missing displayName"):
            parse_vmx_file(vmx)

    def test_empty_file(self, tmp_path):
        vmx = tmp_path / "empty.vmx"
        vmx.write_text("")
        with pytest.raises(ValueError, match="missing displayName"):
            parse_vmx_file(vmx)

    def test_defaults_when_minimal(self, tmp_path):
        vmx = tmp_path / "minimal.vmx"
        vmx.write_text('displayName = "Minimal"\n')
        vm = parse_vmx_file(vmx)
        assert vm.name == "Minimal"
        assert vm.memory_mb == 1024
        assert vm.vcpus == 1
        assert vm.firmware == Firmware.BIOS
        assert vm.disks == []
        assert vm.nics == []

    def test_no_disks_when_not_present(self, tmp_path):
        vmx = tmp_path / "no-disks.vmx"
        vmx.write_text(
            'displayName = "NoDisk"\n'
            'scsi0:0.fileName = "disk.vmdk"\n'
            'scsi0:0.present = "FALSE"\n'
        )
        vm = parse_vmx_file(vmx)
        assert vm.disks == []

    def test_boot_disk_none_when_no_disks(self, tmp_path):
        vmx = tmp_path / "nodisk.vmx"
        vmx.write_text('displayName = "NoDisk"\n')
        vm = parse_vmx_file(vmx)
        assert vm.boot_disk is None
