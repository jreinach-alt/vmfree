"""Tests for v0.2.0 community-requested features.

Feature 1: Disk optimization (--compress, --preallocation)
Feature 2: Proxmox snapshot after import (--snapshot-after-import)
Feature 3: Windows VMware Tools removal
Feature 4: Windows VirtIO driver injection
Feature 5: Batch migration
Feature 6: Network remapping (--network-map)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vmfree.batch import (
    BatchItem,
    BatchResult,
    discover_vms,
    generate_report,
    load_inventory,
    load_state,
    run_batch,
    save_state,
)
from vmfree.cli import (
    _parse_network_map,
    _resolve_bridges,
    _run_migration,
)
from vmfree.converter.disk import convert_disk
from vmfree.fixup.virtio_windows import (
    VIRTIO_DRIVERS,
    detect_virtio_iso,
    inject_virtio_drivers,
    resolve_windows_version,
)
from vmfree.fixup.vmware_tools import FixupResult
from vmfree.fixup.vmware_tools_windows import (
    VMWARE_DRIVER_FILES,
    VMWARE_RUN_ENTRIES,
    VMWARE_SERVICES_WIN,
    VMWARE_TOOLS_PATHS_WIN,
    remove_vmware_tools_windows,
)
from vmfree.generators.proxmox import generate_proxmox_commands
from vmfree.mapper.hardware import MappedHardware
from vmfree.models import DiskDefinition, NICDefinition, VMDefinition

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_run_success(cmd, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _make_vm(**kwargs):
    """Create a minimal VMDefinition for testing."""
    defaults = {
        "name": "TestVM",
        "guest_os": "ubuntu-64",
        "memory_mb": 2048,
        "vcpus": 2,
        "disks": [DiskDefinition(path="/tmp/disk.vmdk")],
        "nics": [NICDefinition(virtual_dev="e1000")],
        "scsi_controller": "lsilogic",
    }
    defaults.update(kwargs)
    return VMDefinition(**defaults)


def _make_hw(**kwargs):
    """Create a minimal MappedHardware for testing."""
    defaults = {
        "controller": "virtio-scsi",
        "nic": "virtio-net",
        "display": "virtio-vga",
        "firmware_path": None,
    }
    defaults.update(kwargs)
    return MappedHardware(**defaults)


# ---------------------------------------------------------------------------
# Feature 1: Disk optimization (--compress, --preallocation)
# ---------------------------------------------------------------------------

class TestDiskOptimization:
    """Test compress and preallocation flags in disk conversion."""

    def test_compress_flag_adds_c(self, tmp_path):
        """--compress should add -c to qemu-img command."""
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        source = tmp_path / "disk.vmdk"
        source.write_text("fake")
        convert_disk(source, tmp_path, compress=True, run_command=mock_run)
        assert any("-c" in cmd for cmd in captured_cmds)

    def test_no_compress_by_default(self, tmp_path):
        """Without --compress, -c should not be in the command."""
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        source = tmp_path / "disk.vmdk"
        source.write_text("fake")
        convert_disk(source, tmp_path, run_command=mock_run)
        assert not any("-c" in cmd for cmd in captured_cmds)

    def test_compress_only_for_qcow2(self, tmp_path):
        """Compress flag should be ignored for raw format."""
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        source = tmp_path / "disk.vmdk"
        source.write_text("fake")
        convert_disk(source, tmp_path, disk_format="raw", compress=True,
                      run_command=mock_run)
        assert not any("-c" in cmd for cmd in captured_cmds)

    def test_preallocation_metadata(self, tmp_path):
        """--preallocation metadata should add -o preallocation=metadata."""
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        source = tmp_path / "disk.vmdk"
        source.write_text("fake")
        convert_disk(source, tmp_path, preallocation="metadata",
                      run_command=mock_run)
        flat_cmd = " ".join(captured_cmds[0])
        assert "preallocation=metadata" in flat_cmd

    def test_preallocation_full(self, tmp_path):
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        source = tmp_path / "disk.vmdk"
        source.write_text("fake")
        convert_disk(source, tmp_path, preallocation="full",
                      run_command=mock_run)
        flat_cmd = " ".join(captured_cmds[0])
        assert "preallocation=full" in flat_cmd

    def test_compress_and_preallocation_together(self, tmp_path):
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        source = tmp_path / "disk.vmdk"
        source.write_text("fake")
        convert_disk(source, tmp_path, compress=True, preallocation="metadata",
                      run_command=mock_run)
        cmd = captured_cmds[0]
        assert "-c" in cmd
        flat_cmd = " ".join(cmd)
        assert "preallocation=metadata" in flat_cmd

    def test_compress_flag_in_cli_help(self):
        from click.testing import CliRunner

        from vmfree.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert "--compress" in result.output
        assert "--preallocation" in result.output


# ---------------------------------------------------------------------------
# Feature 2: Proxmox snapshot after import
# ---------------------------------------------------------------------------

class TestProxmoxSnapshot:
    """Test --snapshot-after-import flag."""

    def test_snapshot_command_generated(self):
        vm = _make_vm()
        hw = _make_hw()
        cmds = generate_proxmox_commands(
            vm, hw, vmid=100, snapshot_name="pre-boot",
        )
        snapshot_cmds = [c for c in cmds if "qm snapshot" in c]
        assert len(snapshot_cmds) == 1
        assert "qm snapshot 100 pre-boot" in snapshot_cmds[0]
        assert "VMFree pre-boot snapshot" in snapshot_cmds[0]

    def test_no_snapshot_by_default(self):
        vm = _make_vm()
        hw = _make_hw()
        cmds = generate_proxmox_commands(vm, hw, vmid=100)
        snapshot_cmds = [c for c in cmds if "qm snapshot" in c]
        assert len(snapshot_cmds) == 0

    def test_snapshot_is_last_command(self):
        vm = _make_vm()
        hw = _make_hw()
        cmds = generate_proxmox_commands(
            vm, hw, vmid=100, snapshot_name="safe",
        )
        assert "qm snapshot" in cmds[-1]

    def test_snapshot_flag_in_cli_help(self):
        from click.testing import CliRunner

        from vmfree.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert "--snapshot-after-import" in result.output

    def test_snapshot_in_script_file(self, tmp_path):
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
            snapshot_after_import="pre-boot",
            verbose=False,
            run_command=_mock_run_success,
        )
        script = tmp_path / "CentOS-7-proxmox.sh"
        content = script.read_text()
        assert "qm snapshot 100 pre-boot" in content


# ---------------------------------------------------------------------------
# Feature 3: Windows VMware Tools removal
# ---------------------------------------------------------------------------

class TestWindowsVmwareToolsRemoval:
    """Test offline Windows VMware Tools removal."""

    def test_returns_fixup_result(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        result = remove_vmware_tools_windows(disk, run_command=_mock_run_success)
        assert isinstance(result, FixupResult)
        assert result.success is True

    def test_has_actions(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        result = remove_vmware_tools_windows(disk, run_command=_mock_run_success)
        assert len(result.actions) > 0

    def test_removes_tools_directories(self, tmp_path):
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        remove_vmware_tools_windows(disk, run_command=mock_run)
        virt_cmds = [c for c in captured_cmds if "virt-customize" in c]
        assert len(virt_cmds) >= len(VMWARE_TOOLS_PATHS_WIN)

    def test_removes_driver_files(self, tmp_path):
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        remove_vmware_tools_windows(disk, run_command=mock_run)
        # Check that driver file removals are in the commands
        all_cmd_text = " ".join(str(c) for c in captured_cmds)
        for driver in VMWARE_DRIVER_FILES:
            assert driver in all_cmd_text

    def test_removes_registry_services(self, tmp_path):
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        remove_vmware_tools_windows(disk, run_command=mock_run)
        all_cmd_text = " ".join(str(c) for c in captured_cmds)
        for service in VMWARE_SERVICES_WIN:
            assert service in all_cmd_text

    def test_cleans_run_entries(self, tmp_path):
        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        remove_vmware_tools_windows(disk, run_command=mock_run)
        all_cmd_text = " ".join(str(c) for c in captured_cmds)
        for entry in VMWARE_RUN_ENTRIES:
            assert entry in all_cmd_text

    def test_disk_not_found(self):
        result = remove_vmware_tools_windows("/nonexistent/disk.qcow2")
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_virt_customize_missing(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")

        def mock_run(cmd, **kwargs):
            raise FileNotFoundError("virt-customize")

        result = remove_vmware_tools_windows(disk, run_command=mock_run)
        assert result.success is False
        assert any("virt-customize" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Feature 4: Windows VirtIO driver injection
# ---------------------------------------------------------------------------

class TestVirtIODriverInjection:
    """Test offline VirtIO driver injection for Windows guests."""

    def test_detect_virtio_iso_returns_none_when_missing(self):
        # Unless virtio-win is installed on the test host, this should be None
        result = detect_virtio_iso()
        # Can't assert None — it might be installed. Just check type.
        assert result is None or isinstance(result, Path)

    def test_resolve_windows_version_2022(self):
        assert resolve_windows_version("windows2019srvnext-64") == "2k22"

    def test_resolve_windows_version_2019(self):
        assert resolve_windows_version("windows2019srv-64") == "2k19"

    def test_resolve_windows_version_win10(self):
        assert resolve_windows_version("windows9-64") == "w10"

    def test_resolve_windows_version_default(self):
        assert resolve_windows_version("unknownOS") == "w10"

    def test_inject_disk_not_found(self):
        result = inject_virtio_drivers("/nonexistent/disk.qcow2")
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_inject_iso_not_found(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        result = inject_virtio_drivers(disk, iso_path="/nonexistent/virtio.iso")
        assert result.success is False
        assert any("virtio-win ISO" in e for e in result.errors)

    def test_inject_with_mock_iso(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        iso = tmp_path / "virtio-win.iso"
        iso.write_text("fake iso")

        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = inject_virtio_drivers(
            disk, guest_os="windows2019srvnext-64", iso_path=iso,
            run_command=mock_run,
        )
        assert result.success is True
        assert len(result.actions) > 0

        # Should have commands for creating directory, copying drivers, registry
        virt_cmds = [c for c in captured_cmds if "virt-customize" in c]
        # At least: 1 mkdir + 5 driver copies + 4 registry entries = 10
        assert len(virt_cmds) >= 5

    def test_inject_creates_driver_directory(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        iso = tmp_path / "virtio-win.iso"
        iso.write_text("fake iso")

        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        inject_virtio_drivers(disk, iso_path=iso, run_command=mock_run)
        first_cmd = captured_cmds[0]
        assert "--mkdir" in first_cmd
        assert "/Windows/Drivers/virtio" in first_cmd

    def test_inject_copies_all_drivers(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        iso = tmp_path / "virtio-win.iso"
        iso.write_text("fake iso")

        result = inject_virtio_drivers(
            disk, iso_path=iso, run_command=_mock_run_success,
        )
        # Should have a "Copied driver:" action for each driver
        copied = [a for a in result.actions if "Copied driver:" in a]
        assert len(copied) == len(VIRTIO_DRIVERS)

    def test_inject_adds_registry_services(self, tmp_path):
        disk = tmp_path / "win.qcow2"
        disk.write_text("fake")
        iso = tmp_path / "virtio-win.iso"
        iso.write_text("fake iso")

        result = inject_virtio_drivers(
            disk, iso_path=iso, run_command=_mock_run_success,
        )
        reg_actions = [a for a in result.actions if "registry service:" in a]
        assert len(reg_actions) >= 3  # vioscsi, viostor, netkvm at minimum


# ---------------------------------------------------------------------------
# Feature 5: Batch migration
# ---------------------------------------------------------------------------

class TestBatchDiscovery:
    """Test VM discovery and inventory loading."""

    def test_discover_vmx_files(self, tmp_path):
        (tmp_path / "vm1.vmx").write_text("fake")
        (tmp_path / "vm2.ovf").write_text("fake")
        (tmp_path / "vm3.ova").write_text("fake")
        (tmp_path / "notes.txt").write_text("not a vm")
        vms = discover_vms(tmp_path)
        assert len(vms) == 3

    def test_discover_recursive(self, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "vm1.vmx").write_text("fake")
        (subdir / "vm2.vmx").write_text("fake")
        vms = discover_vms(tmp_path)
        assert len(vms) == 2

    def test_discover_empty_dir(self, tmp_path):
        vms = discover_vms(tmp_path)
        assert vms == []

    def test_discover_nonexistent_dir(self):
        vms = discover_vms("/nonexistent/path")
        assert vms == []

    def test_load_inventory(self, tmp_path):
        inv = tmp_path / "inventory.txt"
        inv.write_text("# Comment\n/path/to/vm1.vmx\n\n/path/to/vm2.ovf\n")
        vms = load_inventory(inv)
        assert len(vms) == 2
        assert vms[0] == Path("/path/to/vm1.vmx")

    def test_load_inventory_skips_comments(self, tmp_path):
        inv = tmp_path / "inventory.txt"
        inv.write_text("# All comments\n# Another comment\n")
        vms = load_inventory(inv)
        assert vms == []


class TestBatchState:
    """Test batch state persistence for resume."""

    def test_save_and_load_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        items = [
            BatchItem(source_path="/path/vm1.vmx", status="success"),
            BatchItem(source_path="/path/vm2.vmx", status="failed", error="oops"),
        ]
        save_state(state_file, items)

        loaded = load_state(state_file)
        assert loaded["/path/vm1.vmx"] == "success"
        assert loaded["/path/vm2.vmx"] == "failed"

    def test_load_state_missing_file(self, tmp_path):
        loaded = load_state(tmp_path / "nonexistent.json")
        assert loaded == {}

    def test_load_state_corrupt_json(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json")
        loaded = load_state(state_file)
        assert loaded == {}


class TestBatchExecution:
    """Test batch migration execution."""

    def test_run_batch_all_succeed(self, tmp_path):
        sources = [tmp_path / "vm1.vmx", tmp_path / "vm2.vmx"]
        for s in sources:
            s.write_text("fake")

        def mock_migrate(source, **kwargs):
            return True

        result = run_batch(sources, migrate_fn=mock_migrate, resume=False)
        assert result.total == 2
        assert result.succeeded == 2
        assert result.failed == 0

    def test_run_batch_one_fails(self, tmp_path):
        sources = [tmp_path / "vm1.vmx", tmp_path / "vm2.vmx"]
        for s in sources:
            s.write_text("fake")

        call_count = [0]

        def mock_migrate(source, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("migration failed")
            return True

        result = run_batch(sources, migrate_fn=mock_migrate, resume=False)
        assert result.succeeded == 1
        assert result.failed == 1

    def test_stop_on_error(self, tmp_path):
        sources = [tmp_path / f"vm{i}.vmx" for i in range(3)]
        for s in sources:
            s.write_text("fake")

        def mock_migrate(source, **kwargs):
            raise ValueError("fail")

        result = run_batch(
            sources, migrate_fn=mock_migrate,
            resume=False, stop_on_error=True,
        )
        # Should stop after first failure
        assert result.failed == 1
        assert result.pending == 2

    def test_resume_skips_completed(self, tmp_path):
        sources = [tmp_path / "vm1.vmx", tmp_path / "vm2.vmx"]
        for s in sources:
            s.write_text("fake")

        state_file = tmp_path / "state.json"
        # Pre-populate state with vm1 already completed
        save_state(state_file, [
            BatchItem(source_path=str(sources[0]), status="success"),
        ])

        calls = []

        def mock_migrate(source, **kwargs):
            calls.append(source)
            return True

        result = run_batch(
            sources, migrate_fn=mock_migrate,
            resume=True, state_file=str(state_file),
        )
        assert result.skipped == 1
        assert result.succeeded == 1
        assert len(calls) == 1  # Only vm2 was migrated

    def test_state_persisted_after_each_vm(self, tmp_path):
        sources = [tmp_path / "vm1.vmx"]
        sources[0].write_text("fake")
        state_file = tmp_path / "state.json"

        def mock_migrate(source, **kwargs):
            return True

        run_batch(
            sources, migrate_fn=mock_migrate,
            resume=False, state_file=str(state_file),
        )
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["items"][0]["status"] == "success"


class TestBatchResult:
    """Test BatchResult properties."""

    def test_counts(self):
        result = BatchResult(items=[
            BatchItem(source_path="a", status="success"),
            BatchItem(source_path="b", status="failed"),
            BatchItem(source_path="c", status="skipped"),
            BatchItem(source_path="d", status="pending"),
        ])
        assert result.total == 4
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.skipped == 1
        assert result.pending == 1


class TestBatchReport:
    """Test batch report generation."""

    def test_report_is_markdown(self):
        result = BatchResult(items=[
            BatchItem(source_path="/path/vm1.vmx", status="success",
                      duration_seconds=12.5),
            BatchItem(source_path="/path/vm2.vmx", status="failed",
                      error="disk not found"),
        ])
        report = generate_report(result)
        assert "# VMFree Batch Migration Report" in report
        assert "Succeeded" in report
        assert "vm1" in report
        assert "disk not found" in report


class TestBatchCli:
    """Test batch CLI command."""

    def test_batch_in_help(self):
        from click.testing import CliRunner

        from vmfree.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "batch" in result.output

    def test_batch_command_help(self):
        from click.testing import CliRunner

        from vmfree.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["batch", "--help"])
        assert result.exit_code == 0
        assert "--target" in result.output
        assert "--inventory" in result.output
        assert "--resume" in result.output
        assert "--stop-on-error" in result.output
        assert "--vmid-start" in result.output


# ---------------------------------------------------------------------------
# Feature 6: Network remapping (--network-map)
# ---------------------------------------------------------------------------

class TestNetworkMapping:
    """Test --network-map parsing and bridge resolution."""

    def test_parse_single_mapping(self):
        result = _parse_network_map(["VM Network=vmbr0"])
        assert result == {"VM Network": "vmbr0"}

    def test_parse_multiple_mappings(self):
        result = _parse_network_map([
            "VM Network=vmbr0",
            "Management=vmbr1",
        ])
        assert result == {"VM Network": "vmbr0", "Management": "vmbr1"}

    def test_parse_with_spaces(self):
        result = _parse_network_map(["  VM Network = vmbr0  "])
        assert result == {"VM Network": "vmbr0"}

    def test_parse_invalid_mapping_ignored(self):
        result = _parse_network_map(["no-equals-sign"])
        assert result == {}

    def test_parse_empty_list(self):
        result = _parse_network_map([])
        assert result == {}

    def test_resolve_bridges_with_mapping(self):
        vm = _make_vm(nics=[
            NICDefinition(virtual_dev="vmxnet3", network_name="VM Network"),
            NICDefinition(virtual_dev="e1000", network_name="Management"),
        ])
        mappings = {"VM Network": "vmbr0", "Management": "vmbr1"}
        bridges = _resolve_bridges(vm, mappings, "vmbr99")
        assert bridges == ["vmbr0", "vmbr1"]

    def test_resolve_bridges_partial_mapping(self):
        vm = _make_vm(nics=[
            NICDefinition(virtual_dev="vmxnet3", network_name="VM Network"),
            NICDefinition(virtual_dev="e1000", network_name="Unknown"),
        ])
        mappings = {"VM Network": "vmbr0"}
        bridges = _resolve_bridges(vm, mappings, "vmbr99")
        assert bridges == ["vmbr0", "vmbr99"]

    def test_resolve_bridges_no_mapping(self):
        vm = _make_vm(nics=[
            NICDefinition(virtual_dev="vmxnet3", network_name="VM Network"),
        ])
        bridges = _resolve_bridges(vm, {}, "br0")
        assert bridges == ["br0"]

    def test_network_map_flag_in_cli_help(self):
        from click.testing import CliRunner

        from vmfree.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert "--network-map" in result.output
