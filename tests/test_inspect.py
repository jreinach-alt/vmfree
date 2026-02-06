"""Tests for the vmfree inspect CLI command.

Tests the Rich-formatted VM analysis output for VMX, OVF, and OVA sources.
"""

from pathlib import Path

from click.testing import CliRunner

from vmfree.cli import _format_size, _run_inspect, main

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestInspectCliHelp:
    """Test inspect command registration and help."""

    def test_inspect_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "inspect" in result.output

    def test_inspect_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "--help"])
        assert result.exit_code == 0
        assert "Analyze" in result.output


# ---------------------------------------------------------------------------
# VMX file inspection
# ---------------------------------------------------------------------------

class TestInspectVmxUbuntu:
    """Test inspect on sample-ubuntu.vmx."""

    def test_exit_code_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert result.exit_code == 0

    def test_shows_vm_name(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "Ubuntu-Server-22" in result.output

    def test_shows_vcpus(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "4" in result.output

    def test_shows_memory(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "4096" in result.output

    def test_shows_firmware(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "EFI" in result.output

    def test_shows_guest_os(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "ubuntu-64" in result.output

    def test_shows_disk_info(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "Ubuntu-Server-22.vmdk" in result.output

    def test_shows_nic_info(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "vmxnet3" in result.output

    def test_shows_hardware_mapping(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "Hardware Mapping" in result.output

    def test_shows_vm_information_panel(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.vmx")])
        assert "VM Information" in result.output


class TestInspectVmxWindows:
    """Test inspect on sample-windows.vmx."""

    def test_shows_windows_flag(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-windows.vmx")])
        assert "Windows" in result.output

    def test_shows_windows_name(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-windows.vmx")])
        assert "Win2022-DC" in result.output

    def test_shows_two_disks(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-windows.vmx")])
        assert "Win2022-DC.vmdk" in result.output
        assert "Win2022-DC-data.vmdk" in result.output

    def test_shows_mac_address(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-windows.vmx")])
        assert "00:50:56:a1:b2:c3" in result.output


class TestInspectVmxBios:
    """Test inspect on sample-bios.vmx (BIOS + lsilogic)."""

    def test_shows_bios(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-bios.vmx")])
        assert "BIOS" in result.output

    def test_shows_controller_mapping(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-bios.vmx")])
        assert "lsilogic" in result.output
        assert "virtio-scsi" in result.output

    def test_shows_nic_mapping(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-bios.vmx")])
        assert "e1000e" in result.output


# ---------------------------------------------------------------------------
# OVF file inspection
# ---------------------------------------------------------------------------

class TestInspectOvf:
    """Test inspect on OVF fixtures."""

    def test_inspect_ovf_ubuntu(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.ovf")])
        assert result.exit_code == 0
        assert "Ubuntu-Server-22" in result.output

    def test_inspect_ovf_shows_efi(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.ovf")])
        assert "EFI" in result.output

    def test_inspect_ovf_windows(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-windows.ovf")])
        assert result.exit_code == 0
        assert "Win2022-DC" in result.output
        assert "Windows" in result.output

    def test_inspect_ovf_shows_disk_size(self):
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(FIXTURES / "sample-ubuntu.ovf")])
        assert "64.0 GB" in result.output


# ---------------------------------------------------------------------------
# _run_inspect returns VMDefinition
# ---------------------------------------------------------------------------

class TestRunInspect:
    """Test the _run_inspect function directly."""

    def test_returns_vm_definition(self):
        vm = _run_inspect(str(FIXTURES / "sample-ubuntu.vmx"))
        assert vm.name == "Ubuntu-Server-22"

    def test_returns_windows_vm(self):
        vm = _run_inspect(str(FIXTURES / "sample-windows.vmx"))
        assert vm.is_windows

    def test_returns_ovf_vm(self):
        vm = _run_inspect(str(FIXTURES / "sample-ubuntu.ovf"))
        assert vm.name == "Ubuntu-Server-22"
        assert vm.firmware.value == "efi"


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

class TestFormatSize:
    """Test the _format_size helper."""

    def test_bytes(self):
        assert _format_size(512) == "512 B"

    def test_kilobytes(self):
        assert _format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert _format_size(64 * 1024 ** 3) == "64.0 GB"

    def test_fractional_gb(self):
        assert _format_size(int(1.5 * 1024 ** 3)) == "1.5 GB"
