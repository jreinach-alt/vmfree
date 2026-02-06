"""Tests for v0.1.0 post-testing bug fixes.

Bug 1: CDROM/ISO devices should be filtered out of disk list.
Bug 2: Split sparse VMDKs should report their virtual size.
Bug 3: Output directory should default to source file's parent.
Nice-to-have: --execute flag runs Proxmox commands directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vmfree.cli import _run_migration
from vmfree.parsers.vmx import parse_vmx_file

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_run_success(cmd, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return subprocess.CompletedProcess(cmd, 0, "", "")


# ---------------------------------------------------------------------------
# Bug 1: Filter out CDROM/ISO devices
# ---------------------------------------------------------------------------

class TestCdromFiltering:
    """CDROM and ISO devices should not appear in the disk list."""

    def test_cdrom_device_type_filtered(self, tmp_path):
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'scsi0.present = "TRUE"\n'
            'scsi0:0.fileName = "disk.vmdk"\n'
            'scsi0:0.present = "TRUE"\n'
            'ide1:0.fileName = "installer.iso"\n'
            'ide1:0.present = "TRUE"\n'
            'ide1:0.deviceType = "cdrom-image"\n'
        )
        vm = parse_vmx_file(vmx)
        # Only the VMDK should be present, not the ISO
        assert len(vm.disks) == 1
        assert "disk.vmdk" in vm.disks[0].path

    def test_atapi_device_type_filtered(self, tmp_path):
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'ide0:0.fileName = "disk.vmdk"\n'
            'ide0:0.present = "TRUE"\n'
            'ide0:1.fileName = "/dev/cdrom"\n'
            'ide0:1.present = "TRUE"\n'
            'ide0:1.deviceType = "atapi-cdrom"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 1

    def test_iso_extension_filtered(self, tmp_path):
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'scsi0.present = "TRUE"\n'
            'scsi0:0.fileName = "disk.vmdk"\n'
            'scsi0:0.present = "TRUE"\n'
            'ide1:0.fileName = "/path/to/ubuntu.iso"\n'
            'ide1:0.present = "TRUE"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 1
        assert vm.disks[0].path.endswith("disk.vmdk")

    def test_auto_detect_filtered(self, tmp_path):
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'scsi0.present = "TRUE"\n'
            'scsi0:0.fileName = "disk.vmdk"\n'
            'scsi0:0.present = "TRUE"\n'
            'ide1:0.fileName = "auto detect"\n'
            'ide1:0.present = "TRUE"\n'
            'ide1:0.deviceType = "cdrom-raw"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 1

    def test_cdrom_without_device_type_but_iso_extension(self, tmp_path):
        """ISO files should be filtered even without deviceType key."""
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'ide0:0.fileName = "install.ISO"\n'
            'ide0:0.present = "TRUE"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 0

    def test_multiple_cdroms_all_filtered(self, tmp_path):
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'scsi0.present = "TRUE"\n'
            'scsi0:0.fileName = "disk.vmdk"\n'
            'scsi0:0.present = "TRUE"\n'
            'ide0:0.fileName = "cd1.iso"\n'
            'ide0:0.present = "TRUE"\n'
            'ide0:1.fileName = "auto detect"\n'
            'ide0:1.present = "TRUE"\n'
            'ide1:0.fileName = "/dev/sr0"\n'
            'ide1:0.present = "TRUE"\n'
            'ide1:0.deviceType = "atapi-cdrom"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 1
        assert "disk.vmdk" in vm.disks[0].path

    def test_existing_fixtures_have_no_cdroms(self):
        """Existing test fixtures should still parse correctly."""
        for vmx_name in ("sample-ubuntu.vmx", "sample-windows.vmx", "sample-bios.vmx"):
            vm = parse_vmx_file(FIXTURES / vmx_name)
            for disk in vm.disks:
                assert not disk.path.lower().endswith(".iso")


# ---------------------------------------------------------------------------
# Bug 2: VMDK size inspection during VMX parsing
# ---------------------------------------------------------------------------

class TestVmdkSizeInspection:
    """VMX parser should populate disk size from VMDK descriptor inspection."""

    def test_ubuntu_disk_has_size(self):
        """Ubuntu fixture has a real VMDK descriptor — size should be populated."""
        vm = parse_vmx_file(FIXTURES / "sample-ubuntu.vmx")
        assert len(vm.disks) == 1
        # Ubuntu-Server-22.vmdk has: RW 134217728 SPARSE "Ubuntu-Server-22.vmdk"
        # 134217728 sectors * 512 bytes = 68719476736 bytes = 64 GB
        assert vm.disks[0].size_bytes == 134217728 * 512

    def test_windows_disks_have_size(self):
        """Windows fixture has two VMDK descriptors."""
        vm = parse_vmx_file(FIXTURES / "sample-windows.vmx")
        assert len(vm.disks) == 2
        # Both should have non-zero sizes from VMDK inspection
        assert vm.disks[0].size_bytes > 0
        assert vm.disks[1].size_bytes > 0

    def test_bios_disk_has_size(self):
        """CentOS BIOS fixture should have disk size."""
        vm = parse_vmx_file(FIXTURES / "sample-bios.vmx")
        assert len(vm.disks) == 1
        assert vm.disks[0].size_bytes > 0

    def test_missing_vmdk_gets_zero_size(self, tmp_path):
        """When VMDK file doesn't exist, size should be 0."""
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "TestVM"\n'
            'scsi0.present = "TRUE"\n'
            'scsi0:0.fileName = "nonexistent.vmdk"\n'
            'scsi0:0.present = "TRUE"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 1
        assert vm.disks[0].size_bytes == 0

    def test_split_sparse_vmdk_sums_extents(self, tmp_path):
        """Split sparse VMDKs have multiple extents — size is the sum."""
        # Create a twoGbMaxExtentSparse descriptor with 3 extents
        vmdk = tmp_path / "split.vmdk"
        vmdk.write_text(
            '# Disk DescriptorFile\n'
            'version=1\n'
            'CID=12345678\n'
            'parentCID=ffffffff\n'
            'createType="twoGbMaxExtentSparse"\n'
            'RW 4194304 SPARSE "split-s001.vmdk"\n'
            'RW 4194304 SPARSE "split-s002.vmdk"\n'
            'RW 2097152 SPARSE "split-s003.vmdk"\n'
            'ddb.virtualHWVersion = "21"\n'
            'ddb.adapterType = "lsilogic"\n'
        )
        vmx = tmp_path / "test.vmx"
        vmx.write_text(
            'displayName = "SplitVM"\n'
            'scsi0.present = "TRUE"\n'
            'scsi0:0.fileName = "split.vmdk"\n'
            'scsi0:0.present = "TRUE"\n'
        )
        vm = parse_vmx_file(vmx)
        assert len(vm.disks) == 1
        # (4194304 + 4194304 + 2097152) * 512 = 5368709120 bytes = 5 GB
        expected = (4194304 + 4194304 + 2097152) * 512
        assert vm.disks[0].size_bytes == expected

    def test_disk_type_populated_from_vmdk(self):
        """Disk type should be populated from VMDK inspection."""
        from vmfree.models import DiskType
        vm = parse_vmx_file(FIXTURES / "sample-ubuntu.vmx")
        assert vm.disks[0].disk_type == DiskType.MONOLITHIC_SPARSE


# ---------------------------------------------------------------------------
# Bug 3: Output directory defaults to source parent
# ---------------------------------------------------------------------------

class TestOutputDirectoryDefault:
    """Output directory should default to source file's parent directory."""

    def test_output_defaults_to_source_parent(self, tmp_path):
        """When output_dir is None, should use source file's parent."""
        # Create source VMX in a subdirectory
        src_dir = tmp_path / "vms" / "ubuntu"
        src_dir.mkdir(parents=True)
        # Copy fixture
        vmx_content = (FIXTURES / "sample-bios.vmx").read_text()
        (src_dir / "CentOS-7.vmx").write_text(vmx_content)
        # Copy VMDK
        vmdk_content = (FIXTURES / "CentOS-7.vmdk").read_text()
        (src_dir / "CentOS-7.vmdk").write_text(vmdk_content)

        result = _run_migration(
            source=str(src_dir / "CentOS-7.vmx"),
            target="kvm",
            output_dir=None,  # Should default to src_dir
            bridge="br0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=False,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        assert result is True
        # XML should be written to the source directory, not CWD
        xml_file = src_dir / "CentOS-7.xml"
        assert xml_file.exists()

    def test_explicit_output_dir_still_works(self, tmp_path):
        """Explicit --output should override the default."""
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        result = _run_migration(
            source=str(FIXTURES / "sample-bios.vmx"),
            target="kvm",
            output_dir=str(out_dir),
            bridge="br0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=False,
            dry_run=False,
            verbose=False,
            run_command=_mock_run_success,
        )
        assert result is True
        assert (out_dir / "CentOS-7.xml").exists()


# ---------------------------------------------------------------------------
# Nice-to-have: --execute flag
# ---------------------------------------------------------------------------

class TestExecuteFlag:
    """Test --execute flag runs Proxmox commands directly."""

    def test_execute_flag_in_help(self):
        from click.testing import CliRunner

        from vmfree.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert "--execute" in result.output

    def test_execute_runs_commands(self, tmp_path):
        """With --execute, qm commands should be run via run_command."""
        executed_commands: list[str] = []

        def mock_run(cmd, **kwargs):
            executed_commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        _run_migration(
            source=str(FIXTURES / "sample-bios.vmx"),
            target="proxmox",
            output_dir=str(tmp_path),
            bridge="vmbr0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=False,
            dry_run=False,
            execute=True,
            verbose=False,
            run_command=mock_run,
        )
        # Should have executed qm commands via run_command
        # Filter for the execute phase commands (shell=True calls)
        qm_cmds = [c for c in executed_commands if isinstance(c, str) and "qm" in c]
        assert len(qm_cmds) > 0
        assert any("qm create" in cmd for cmd in qm_cmds)

    def test_no_execute_by_default(self, tmp_path):
        """Without --execute, commands should only be written to script."""
        executed_commands: list[str] = []

        def mock_run(cmd, **kwargs):
            executed_commands.append(str(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        _run_migration(
            source=str(FIXTURES / "sample-bios.vmx"),
            target="proxmox",
            output_dir=str(tmp_path),
            bridge="vmbr0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=False,
            dry_run=False,
            execute=False,
            verbose=False,
            run_command=mock_run,
        )
        # qemu-img calls happen, but no qm commands
        qm_cmds = [c for c in executed_commands if "qm" in c]
        assert len(qm_cmds) == 0

    def test_execute_script_also_written(self, tmp_path):
        """Even with --execute, the script file should still be written."""
        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        _run_migration(
            source=str(FIXTURES / "sample-bios.vmx"),
            target="proxmox",
            output_dir=str(tmp_path),
            bridge="vmbr0",
            storage="local-lvm",
            vmid=100,
            disk_format="qcow2",
            windows_safe=False,
            preserve_mac=False,
            dry_run=False,
            execute=True,
            verbose=False,
            run_command=mock_run,
        )
        script = tmp_path / "CentOS-7-proxmox.sh"
        assert script.exists()
        assert "qm create" in script.read_text()

    def test_execute_aborts_on_failure(self, tmp_path):
        """If a qm command fails, execution should abort."""
        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if isinstance(cmd, str) and "qm create" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "qm create failed")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with pytest.raises(SystemExit):
            _run_migration(
                source=str(FIXTURES / "sample-bios.vmx"),
                target="proxmox",
                output_dir=str(tmp_path),
                bridge="vmbr0",
                storage="local-lvm",
                vmid=100,
                disk_format="qcow2",
                windows_safe=False,
                preserve_mac=False,
                dry_run=False,
                execute=True,
                verbose=False,
                run_command=mock_run,
            )
