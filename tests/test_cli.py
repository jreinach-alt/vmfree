"""End-to-end CLI tests for the migrate command.

Tests the full pipeline with mocked subprocess calls:
  - VMX parsing → hardware mapping → disk conversion → config generation
  - --dry-run mode (no disk conversion, shows preview)
  - --target kvm (libvirt XML output)
  - --target proxmox (qm command script output)
  - Windows auto-detection of safe mode
  - --preserve-mac flag
"""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from click.testing import CliRunner

from vmfree.cli import _run_migration, main

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_run_success(cmd, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return subprocess.CompletedProcess(cmd, 0, "", "")


# ---------------------------------------------------------------------------
# Click CLI integration tests
# ---------------------------------------------------------------------------

class TestCliHelp:
    """Test CLI help and version."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "VMFree" in result.output
        assert "migrate" in result.output

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_migrate_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert result.exit_code == 0
        assert "--target" in result.output
        assert "--dry-run" in result.output
        assert "--preserve-mac" in result.output


# ---------------------------------------------------------------------------
# Dry-run: KVM target
# ---------------------------------------------------------------------------

class TestDryRunKvm:
    """Test --dry-run with --target kvm."""

    def test_dry_run_shows_preflight(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "kvm",
            "--dry-run",
            "--bridge", "br0",
        ])
        assert result.exit_code == 0
        assert "Pre-flight Check" in result.output
        assert "Ubuntu-Server-22" in result.output

    def test_dry_run_shows_xml_preview(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "kvm",
            "--dry-run",
            "--bridge", "br0",
        ])
        assert "Libvirt XML" in result.output
        assert "DRY RUN" in result.output

    def test_dry_run_no_disk_conversion(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "kvm",
            "--dry-run",
            "--bridge", "br0",
        ])
        assert "Converting" not in result.output

    def test_dry_run_shows_firmware(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "kvm",
            "--dry-run",
            "--bridge", "br0",
        ])
        assert "EFI" in result.output or "OVMF" in result.output

    def test_dry_run_shows_disk_plan(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "kvm",
            "--dry-run",
            "--bridge", "br0",
        ])
        assert "Would convert" in result.output
        assert "Ubuntu-Server-22" in result.output


# ---------------------------------------------------------------------------
# Dry-run: Proxmox target
# ---------------------------------------------------------------------------

class TestDryRunProxmox:
    """Test --dry-run with --target proxmox."""

    def test_dry_run_shows_proxmox_commands(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "proxmox",
            "--dry-run",
            "--bridge", "vmbr0",
            "--vmid", "100",
        ])
        assert "Proxmox Commands" in result.output
        assert "qm create" in result.output

    def test_dry_run_proxmox_storage(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "migrate", str(FIXTURES / "sample-ubuntu.vmx"),
            "--target", "proxmox",
            "--dry-run",
            "--bridge", "vmbr0",
            "--storage", "ceph-pool",
        ])
        assert "ceph-pool" in result.output


# ---------------------------------------------------------------------------
# Full pipeline with mocked subprocess
# ---------------------------------------------------------------------------

class TestFullPipelineKvm:
    """Test full KVM migration with mocked qemu-img."""

    def test_produces_xml_file(self, tmp_path):
        result = _run_migration(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="kvm",
            output_dir=str(tmp_path),
            bridge="br0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        assert result is True
        xml_file = tmp_path / "Ubuntu-Server-22.xml"
        assert xml_file.exists()
        # Verify valid XML
        tree = ET.parse(xml_file)
        root = tree.getroot()
        assert root.tag == "domain"
        assert root.findtext("name") == "Ubuntu-Server-22"

    def test_xml_has_efi_loader(self, tmp_path):
        _run_migration(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="kvm",
            output_dir=str(tmp_path),
            bridge="br0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        xml_file = tmp_path / "Ubuntu-Server-22.xml"
        root = ET.parse(xml_file).getroot()
        loader = root.find("os/loader")
        assert loader is not None
        assert "OVMF" in loader.text

    def test_xml_has_mac_preserved(self, tmp_path):
        _run_migration(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="kvm",
            output_dir=str(tmp_path),
            bridge="br0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        xml_file = tmp_path / "Ubuntu-Server-22.xml"
        root = ET.parse(xml_file).getroot()
        mac = root.find("devices/interface/mac")
        assert mac is not None
        assert mac.get("address") == "00:0c:29:7d:2d:68"


class TestFullPipelineProxmox:
    """Test full Proxmox migration with mocked qemu-img."""

    def test_produces_script_file(self, tmp_path):
        result = _run_migration(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="proxmox",
            output_dir=str(tmp_path),
            bridge="vmbr0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        assert result is True
        script = tmp_path / "Ubuntu-Server-22-proxmox.sh"
        assert script.exists()
        content = script.read_text()
        assert "#!/bin/bash" in content
        assert "qm create 100" in content

    def test_script_has_efidisk(self, tmp_path):
        _run_migration(
            source=str(FIXTURES / "sample-ubuntu.vmx"),
            target="proxmox",
            output_dir=str(tmp_path),
            bridge="vmbr0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        content = (tmp_path / "Ubuntu-Server-22-proxmox.sh").read_text()
        assert "efidisk0" in content
        assert "ovmf" in content


# ---------------------------------------------------------------------------
# Windows auto-detection
# ---------------------------------------------------------------------------

class TestWindowsAutoDetect:
    """Test that Windows VMs auto-enable safe mode."""

    def test_windows_gets_safe_mode(self, tmp_path):
        _run_migration(
            source=str(FIXTURES / "sample-windows.vmx"),
            target="kvm",
            output_dir=str(tmp_path),
            bridge="br0",
            storage="local-lvm",
            vmid=200,
            disk_format="qcow2",
            windows_safe=False,  # Not explicitly set — should auto-detect
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        xml_file = tmp_path / "Win2022-DC.xml"
        root = ET.parse(xml_file).getroot()
        # Should use IDE (safe mode) not SCSI
        target_elem = root.find("devices/disk/target")
        assert target_elem.get("bus") == "ide"
        # Should use e1000 not virtio-net
        model = root.find("devices/interface/model")
        assert model.get("type") == "e1000"


# ---------------------------------------------------------------------------
# BIOS + lsilogic fixture
# ---------------------------------------------------------------------------

class TestBiosMigration:
    """Test BIOS VM migration."""

    def test_bios_no_efi(self, tmp_path):
        _run_migration(
            source=str(FIXTURES / "sample-bios.vmx"),
            target="kvm",
            output_dir=str(tmp_path),
            bridge="br0",
            storage="local-lvm",
            vmid=300,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=True,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        xml_file = tmp_path / "CentOS-7.xml"
        root = ET.parse(xml_file).getroot()
        loader = root.find("os/loader")
        assert loader is None  # BIOS, no OVMF

    def test_bios_virtio_scsi(self, tmp_path):
        _run_migration(
            source=str(FIXTURES / "sample-bios.vmx"),
            target="kvm",
            output_dir=str(tmp_path),
            bridge="br0",
            storage="local-lvm",
            vmid=300,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=False,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        xml_file = tmp_path / "CentOS-7.xml"
        root = ET.parse(xml_file).getroot()
        target_elem = root.find("devices/disk/target")
        assert target_elem.get("bus") == "scsi"
