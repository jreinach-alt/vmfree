"""Tests for VMware Tools removal from guest disk images.

All tests use mocked subprocess calls — no actual libguestfs required.
Tests verify correct command construction, package removal for RPM/DEB,
kernel module blacklisting, service disabling, and cleanup actions.
"""

import subprocess

from vmfree.fixup.vmware_tools import (
    VMWARE_MODULES,
    VMWARE_PACKAGES_DEB,
    VMWARE_PACKAGES_RPM,
    VMWARE_PATHS,
    VMWARE_SERVICES,
    FixupResult,
    check_libguestfs_installed,
    remove_vmware_tools_linux,
)


def _mock_run_success(cmd, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _mock_run_failure(cmd, **kwargs):
    """Mock subprocess.run that always fails."""
    return subprocess.CompletedProcess(cmd, 1, "", "command failed")


def _mock_run_not_found(cmd, **kwargs):
    """Mock subprocess.run that raises FileNotFoundError."""
    raise FileNotFoundError("virt-customize not found")


# Track which commands were called
class CommandTracker:
    """Track subprocess commands for assertions."""

    def __init__(self, returncode=0):
        self.commands: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


# ---------------------------------------------------------------------------
# FixupResult dataclass
# ---------------------------------------------------------------------------

class TestFixupResult:
    """Test the FixupResult dataclass."""

    def test_default_values(self):
        r = FixupResult(success=True)
        assert r.success is True
        assert r.actions == []
        assert r.errors == []

    def test_with_actions(self):
        r = FixupResult(success=True, actions=["removed package"])
        assert len(r.actions) == 1

    def test_with_errors(self):
        r = FixupResult(success=False, errors=["disk not found"])
        assert r.success is False
        assert len(r.errors) == 1


# ---------------------------------------------------------------------------
# check_libguestfs_installed
# ---------------------------------------------------------------------------

class TestCheckLibguestfs:
    """Test libguestfs availability check."""

    def test_available(self):
        assert check_libguestfs_installed(run_command=_mock_run_success) is True

    def test_not_available_error(self):
        assert check_libguestfs_installed(run_command=_mock_run_failure) is False

    def test_not_installed(self):
        assert check_libguestfs_installed(run_command=_mock_run_not_found) is False


# ---------------------------------------------------------------------------
# remove_vmware_tools_linux — success path
# ---------------------------------------------------------------------------

class TestRemoveToolsSuccess:
    """Test VMware Tools removal with successful commands."""

    def test_returns_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = remove_vmware_tools_linux(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_has_actions(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = remove_vmware_tools_linux(disk, run_command=_mock_run_success)
        assert len(result.actions) > 0

    def test_no_errors(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = remove_vmware_tools_linux(disk, run_command=_mock_run_success)
        assert len(result.errors) == 0

    def test_detects_package_manager(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = remove_vmware_tools_linux(disk, run_command=_mock_run_success)
        pkg_actions = [a for a in result.actions if "package manager" in a]
        assert len(pkg_actions) == 1

    def test_removes_rpm_packages(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        remove_vmware_tools_linux(disk, run_command=tracker)
        rpm_cmds = [
            cmd for cmd in tracker.commands
            if any("rpm -e" in arg for arg in cmd)
        ]
        assert len(rpm_cmds) == len(VMWARE_PACKAGES_RPM)

    def test_blacklists_modules(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = remove_vmware_tools_linux(disk, run_command=_mock_run_success)
        blacklist_actions = [a for a in result.actions if "Blacklisted" in a]
        assert len(blacklist_actions) == 1
        assert str(len(VMWARE_MODULES)) in blacklist_actions[0]

    def test_disables_services(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        remove_vmware_tools_linux(disk, run_command=tracker)
        svc_cmds = [
            cmd for cmd in tracker.commands
            if any("systemctl disable" in arg for arg in cmd)
        ]
        assert len(svc_cmds) == len(VMWARE_SERVICES)

    def test_cleans_vmware_paths(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        remove_vmware_tools_linux(disk, run_command=tracker)
        cleanup_cmds = [
            cmd for cmd in tracker.commands
            if any("rm -rf" in arg for arg in cmd)
        ]
        assert len(cleanup_cmds) == len(VMWARE_PATHS)


# ---------------------------------------------------------------------------
# remove_vmware_tools_linux — error paths
# ---------------------------------------------------------------------------

class TestRemoveToolsErrors:
    """Test error handling during VMware Tools removal."""

    def test_disk_not_found(self):
        result = remove_vmware_tools_linux(
            "/nonexistent/disk.qcow2",
            run_command=_mock_run_success,
        )
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_virt_customize_missing(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = remove_vmware_tools_linux(
            disk, run_command=_mock_run_not_found,
        )
        assert result.success is False
        assert any("not found" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestCommandConstruction:
    """Test that correct virt-customize commands are built."""

    def test_virt_customize_with_disk_path(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        remove_vmware_tools_linux(disk, run_command=tracker)
        # All virt-customize commands should reference the disk
        vc_cmds = [
            cmd for cmd in tracker.commands
            if "virt-customize" in cmd
        ]
        for cmd in vc_cmds:
            assert "-a" in cmd
            assert str(disk) in cmd

    def test_virt_cat_for_detection(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        remove_vmware_tools_linux(disk, run_command=tracker)
        # Should try virt-cat for package manager detection
        cat_cmds = [cmd for cmd in tracker.commands if "virt-cat" in cmd]
        assert len(cat_cmds) >= 1

    def test_module_blacklist_content(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        remove_vmware_tools_linux(disk, run_command=tracker)
        # Find the --write command for module blacklist
        write_cmds = [
            cmd for cmd in tracker.commands
            if any("modprobe.d/vmware-blacklist.conf" in arg for arg in cmd)
        ]
        assert len(write_cmds) == 1


# ---------------------------------------------------------------------------
# DEB package removal
# ---------------------------------------------------------------------------

class TestDebPackageRemoval:
    """Test DEB package removal when Debian-based distro is detected."""

    def test_removes_deb_packages(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        call_count = 0

        def mock_deb_detect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # First virt-cat calls fail (not RPM), then succeed on debian_version
            if "virt-cat" in cmd:
                if "/etc/debian_version" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, "12", "")
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = remove_vmware_tools_linux(disk, run_command=mock_deb_detect)
        assert result.success is True
        deb_actions = [a for a in result.actions if "DEB" in a]
        assert len(deb_actions) == len(VMWARE_PACKAGES_DEB)


# ---------------------------------------------------------------------------
# Constants validation
# ---------------------------------------------------------------------------

class TestConstants:
    """Verify the VMware-related constant lists are populated."""

    def test_rpm_packages_not_empty(self):
        assert len(VMWARE_PACKAGES_RPM) > 0

    def test_deb_packages_not_empty(self):
        assert len(VMWARE_PACKAGES_DEB) > 0

    def test_modules_not_empty(self):
        assert len(VMWARE_MODULES) > 0
        assert "vmw_pvscsi" in VMWARE_MODULES
        assert "vmxnet3" in VMWARE_MODULES

    def test_services_not_empty(self):
        assert len(VMWARE_SERVICES) > 0
        assert "vmtoolsd.service" in VMWARE_SERVICES

    def test_paths_not_empty(self):
        assert len(VMWARE_PATHS) > 0
        assert "/etc/vmware-tools" in VMWARE_PATHS
