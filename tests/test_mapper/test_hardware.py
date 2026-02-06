"""Tests for VMware-to-KVM hardware translation tables.

Every mapping in the translation tables has at least one test.
Also covers Windows safe-mode fallbacks and the combined map_hardware function.
"""

import pytest

from vmfree.mapper.hardware import (
    CONTROLLER_MAP,
    DISPLAY_MAP,
    FIRMWARE_MAP,
    NIC_MAP,
    WINDOWS_SAFE_CONTROLLER,
    WINDOWS_SAFE_NIC,
    MappedHardware,
    map_controller,
    map_display,
    map_firmware,
    map_hardware,
    map_nic,
)

# ---------------------------------------------------------------------------
# Controller mapping
# ---------------------------------------------------------------------------

class TestMapController:
    """Test every VMware SCSI controller maps to correct KVM type."""

    def test_pvscsi(self):
        assert map_controller("pvscsi") == "virtio-scsi"

    def test_lsilogic(self):
        assert map_controller("lsilogic") == "virtio-scsi"

    def test_lsisas1068(self):
        assert map_controller("lsisas1068") == "virtio-scsi"

    def test_buslogic(self):
        assert map_controller("buslogic") == "virtio-scsi"

    def test_ahci(self):
        assert map_controller("ahci") == "virtio-scsi"

    def test_nvme(self):
        assert map_controller("nvme") == "nvme"

    def test_case_insensitive(self):
        assert map_controller("PVSCSI") == "virtio-scsi"
        assert map_controller("LsiLogic") == "virtio-scsi"

    def test_unknown_defaults_to_virtio_scsi(self):
        assert map_controller("unknownctrl") == "virtio-scsi"

    def test_windows_safe_returns_ide(self):
        assert map_controller("pvscsi", windows_safe=True) == WINDOWS_SAFE_CONTROLLER
        assert map_controller("pvscsi", windows_safe=True) == "ide"

    def test_windows_safe_overrides_all(self):
        for ctrl in CONTROLLER_MAP:
            assert map_controller(ctrl, windows_safe=True) == "ide"

    def test_all_table_entries_covered(self):
        """Verify every key in CONTROLLER_MAP is tested above."""
        expected_keys = {"pvscsi", "lsilogic", "lsisas1068", "buslogic", "ahci", "nvme"}
        assert set(CONTROLLER_MAP.keys()) == expected_keys


# ---------------------------------------------------------------------------
# NIC mapping
# ---------------------------------------------------------------------------

class TestMapNic:
    """Test every VMware NIC type maps to correct KVM type."""

    def test_vmxnet3(self):
        assert map_nic("vmxnet3") == "virtio-net"

    def test_e1000_kept(self):
        assert map_nic("e1000") == "e1000"

    def test_e1000e_kept(self):
        assert map_nic("e1000e") == "e1000e"

    def test_vlance(self):
        assert map_nic("vlance") == "virtio-net"

    def test_case_insensitive(self):
        assert map_nic("VMXNET3") == "virtio-net"
        assert map_nic("E1000e") == "e1000e"

    def test_unknown_defaults_to_virtio_net(self):
        assert map_nic("unknownnic") == "virtio-net"

    def test_windows_safe_returns_e1000(self):
        assert map_nic("vmxnet3", windows_safe=True) == WINDOWS_SAFE_NIC
        assert map_nic("vmxnet3", windows_safe=True) == "e1000"

    def test_windows_safe_overrides_all(self):
        for nic in NIC_MAP:
            assert map_nic(nic, windows_safe=True) == "e1000"

    def test_all_table_entries_covered(self):
        expected_keys = {"vmxnet3", "e1000", "e1000e", "vlance"}
        assert set(NIC_MAP.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Display mapping
# ---------------------------------------------------------------------------

class TestMapDisplay:
    """Test every VMware display type maps to correct KVM type."""

    def test_svga(self):
        assert map_display("svga") == "virtio-vga"

    def test_vmware(self):
        assert map_display("vmware") == "virtio-vga"

    def test_case_insensitive(self):
        assert map_display("SVGA") == "virtio-vga"

    def test_unknown_defaults_to_virtio_vga(self):
        assert map_display("unknown") == "virtio-vga"

    def test_all_table_entries_covered(self):
        expected_keys = {"svga", "vmware"}
        assert set(DISPLAY_MAP.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Firmware mapping
# ---------------------------------------------------------------------------

class TestMapFirmware:
    """Test firmware type to OVMF path mapping."""

    def test_efi(self):
        result = map_firmware("efi")
        assert result is not None
        assert "OVMF" in result

    def test_bios(self):
        assert map_firmware("bios") is None

    def test_case_insensitive(self):
        assert map_firmware("EFI") is not None
        assert map_firmware("BIOS") is None

    def test_unknown_returns_none(self):
        assert map_firmware("unknown") is None

    def test_all_table_entries_covered(self):
        expected_keys = {"efi", "bios"}
        assert set(FIRMWARE_MAP.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Combined map_hardware()
# ---------------------------------------------------------------------------

class TestMapHardware:
    """Test the combined hardware mapping function."""

    def test_ubuntu_efi_pvscsi_vmxnet3(self):
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        assert hw.controller == "virtio-scsi"
        assert hw.nic == "virtio-net"
        assert hw.display == "virtio-vga"
        assert hw.firmware_path is not None
        assert "OVMF" in hw.firmware_path
        assert hw.is_safe_mode is False

    def test_centos_bios_lsilogic_e1000e(self):
        hw = map_hardware("lsilogic", "e1000e", "svga", "bios")
        assert hw.controller == "virtio-scsi"
        assert hw.nic == "e1000e"
        assert hw.display == "virtio-vga"
        assert hw.firmware_path is None
        assert hw.is_safe_mode is False

    def test_windows_safe_mode(self):
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "bios", windows_safe=True)
        assert hw.controller == "ide"
        assert hw.nic == "e1000"
        assert hw.display == "virtio-vga"
        assert hw.firmware_path is None
        assert hw.is_safe_mode is True

    def test_returns_mapped_hardware_instance(self):
        hw = map_hardware("pvscsi", "e1000", "svga", "bios")
        assert isinstance(hw, MappedHardware)

    def test_nvme_passthrough(self):
        hw = map_hardware("nvme", "e1000", "svga", "bios")
        assert hw.controller == "nvme"


# ---------------------------------------------------------------------------
# Ensure tables match CLAUDE.md spec
# ---------------------------------------------------------------------------

class TestSpecCompliance:
    """Verify translation tables match the spec in CLAUDE.md."""

    @pytest.mark.parametrize("vmware,expected", [
        ("pvscsi", "virtio-scsi"),
        ("lsilogic", "virtio-scsi"),
        ("buslogic", "virtio-scsi"),
        ("nvme", "nvme"),
        ("ahci", "virtio-scsi"),
    ])
    def test_controller_spec(self, vmware, expected):
        assert map_controller(vmware) == expected

    @pytest.mark.parametrize("vmware,expected", [
        ("vmxnet3", "virtio-net"),
        ("e1000", "e1000"),
        ("e1000e", "e1000e"),
        ("vlance", "virtio-net"),
    ])
    def test_nic_spec(self, vmware, expected):
        assert map_nic(vmware) == expected

    @pytest.mark.parametrize("vmware,expected", [
        ("svga", "virtio-vga"),
        ("vmware", "virtio-vga"),
    ])
    def test_display_spec(self, vmware, expected):
        assert map_display(vmware) == expected

    @pytest.mark.parametrize("firmware,has_path", [
        ("efi", True),
        ("bios", False),
    ])
    def test_firmware_spec(self, firmware, has_path):
        result = map_firmware(firmware)
        assert (result is not None) == has_path
