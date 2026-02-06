"""Tests for the vmfree validate CLI command.

Tests pre-flight validation checks with Rich-formatted output for
VMX, OVF sources, covering disk existence, guest OS detection,
firmware validation, and output directory checks.
"""

from pathlib import Path

from click.testing import CliRunner

from vmfree.cli import _run_validate, main

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestValidateCliHelp:
    """Test validate command registration and help."""

    def test_validate_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "validate" in result.output

    def test_validate_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert result.exit_code == 0
        assert "pre-flight" in result.output.lower() or "Pre-flight" in result.output

    def test_validate_has_target_option(self):
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert "--target" in result.output

    def test_validate_has_output_option(self):
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert "--output" in result.output


# ---------------------------------------------------------------------------
# VMX file validation
# ---------------------------------------------------------------------------

class TestValidateVmxUbuntu:
    """Test validate on sample-ubuntu.vmx."""

    def test_exit_code_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert result.exit_code == 0

    def test_shows_source_parsed(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "Ubuntu-Server-22" in result.output

    def test_shows_guest_os(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "ubuntu-64" in result.output

    def test_shows_firmware(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "EFI" in result.output

    def test_shows_disk_check(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        # The VMDK stub exists in fixtures
        assert "Disk found" in result.output or "Disk missing" in result.output

    def test_shows_network_bridge(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "bridge" in result.output.lower()


class TestValidateVmxWindows:
    """Test validate on Windows VM."""

    def test_shows_windows_warning(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-windows.vmx"),
        ])
        assert "Windows" in result.output
        assert "safe mode" in result.output

    def test_shows_both_disks(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-windows.vmx"),
        ])
        # Both disk files should be checked
        assert "Win2022-DC" in result.output


# ---------------------------------------------------------------------------
# OVF file validation
# ---------------------------------------------------------------------------

class TestValidateOvf:
    """Test validate on OVF fixtures."""

    def test_validate_ovf_ubuntu(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.ovf"),
        ])
        assert result.exit_code == 0
        assert "Ubuntu-Server-22" in result.output

    def test_validate_ovf_windows(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-windows.ovf"),
        ])
        assert result.exit_code == 0
        assert "Win2022-DC" in result.output
        assert "Windows" in result.output


# ---------------------------------------------------------------------------
# _run_validate returns results
# ---------------------------------------------------------------------------

class TestRunValidate:
    """Test the _run_validate function directly."""

    def test_returns_results_list(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
        )
        assert isinstance(results, list)
        assert len(results) > 0

    def test_source_parsed_ok(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
        )
        sources = [msg for ok, msg in results if "Source parsed" in msg]
        assert len(sources) == 1
        ok_val = next(ok for ok, msg in results if "Source parsed" in msg)
        assert ok_val is True

    def test_guest_os_present(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
        )
        os_checks = [msg for _, msg in results if "Guest OS" in msg]
        assert len(os_checks) == 1

    def test_firmware_check(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
        )
        fw_checks = [msg for _, msg in results if "Firmware" in msg]
        assert len(fw_checks) == 1
        assert "EFI" in fw_checks[0]

    def test_bios_firmware(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-bios.vmx"),
        )
        fw_checks = [msg for _, msg in results if "Firmware" in msg]
        assert "BIOS" in fw_checks[0]

    def test_windows_safe_mode_note(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-windows.vmx"),
        )
        win_checks = [msg for _, msg in results if "Windows" in msg]
        assert len(win_checks) >= 1

    def test_disk_existence_checked(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
        )
        disk_checks = [msg for _, msg in results if "Disk" in msg]
        assert len(disk_checks) >= 1

    def test_output_dir_checked(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            output_dir=".",
        )
        dir_checks = [msg for _, msg in results if "Output directory" in msg]
        assert len(dir_checks) == 1

    def test_bridge_detected(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="kvm",
        )
        bridge_checks = [msg for _, msg in results if "bridge" in msg.lower()]
        assert len(bridge_checks) >= 1

    def test_proxmox_target(self):
        results = _run_validate(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="proxmox",
        )
        bridge_checks = [msg for _, msg in results if "bridge" in msg.lower()]
        assert len(bridge_checks) >= 1


# ---------------------------------------------------------------------------
# Validation panel display
# ---------------------------------------------------------------------------

class TestValidateDisplay:
    """Test Rich panel display in CLI output."""

    def test_shows_validation_title(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "Validation" in result.output

    def test_shows_pass_or_fail_markers(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "PASS" in result.output or "FAIL" in result.output

    def test_shows_preflight_header(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "validate", str(FIXTURES / "sample-ubuntu.vmx"),
        ])
        assert "Pre-flight" in result.output
