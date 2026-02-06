"""Tests for bootloader fixup (initramfs rebuild + GRUB update).

All tests use mocked subprocess calls — no actual libguestfs required.
Tests verify initramfs detection, virtio module injection, dracut/
update-initramfs command construction, GRUB config regeneration,
and fstab backup.
"""

import subprocess

from vmfree.fixup.bootloader import (
    VIRTIO_EXTRA_MODULES,
    VIRTIO_MODULES,
    fix_fstab_disk_references,
    full_bootloader_fixup,
    rebuild_initramfs,
    update_grub,
)


def _mock_run_success(cmd, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _mock_run_failure(cmd, **kwargs):
    """Mock subprocess.run that always fails."""
    return subprocess.CompletedProcess(cmd, 1, "", "failed")


def _mock_run_not_found(cmd, **kwargs):
    """Mock that raises FileNotFoundError."""
    raise FileNotFoundError("virt-customize not found")


class CommandTracker:
    """Track subprocess commands for assertions."""

    def __init__(self, returncode=0):
        self.commands: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


# ---------------------------------------------------------------------------
# rebuild_initramfs
# ---------------------------------------------------------------------------

class TestRebuildInitramfs:
    """Test initramfs rebuild."""

    def test_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = rebuild_initramfs(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_disk_not_found(self):
        result = rebuild_initramfs("/nonexistent/disk.qcow2",
                                   run_command=_mock_run_success)
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_detects_dracut(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = rebuild_initramfs(disk, run_command=_mock_run_success)
        assert any("dracut" in a for a in result.actions)

    def test_has_actions(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = rebuild_initramfs(disk, run_command=_mock_run_success)
        assert len(result.actions) >= 2  # detect + rebuild

    def test_adds_virtio_modules(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = rebuild_initramfs(disk, run_command=_mock_run_success)
        total = len(VIRTIO_MODULES) + len(VIRTIO_EXTRA_MODULES)
        module_actions = [a for a in result.actions if "virtio modules" in a]
        assert len(module_actions) == 1
        assert str(total) in module_actions[0]

    def test_dracut_commands(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        rebuild_initramfs(disk, run_command=tracker)
        # Should have dracut-related commands
        dracut_cmds = [
            cmd for cmd in tracker.commands
            if any("dracut" in arg for arg in cmd)
        ]
        assert len(dracut_cmds) >= 1

    def test_dracut_config_written(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        rebuild_initramfs(disk, run_command=tracker)
        # Should write dracut.conf.d/virtio.conf
        write_cmds = [
            cmd for cmd in tracker.commands
            if any("dracut.conf.d/virtio.conf" in arg for arg in cmd)
        ]
        assert len(write_cmds) == 1

    def test_virt_customize_not_found(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_detect_ok_customize_missing(cmd, **kwargs):
            # Detection works, but virt-customize raises FileNotFoundError
            if "virt-cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise FileNotFoundError("virt-customize not found")

        result = rebuild_initramfs(
            disk, run_command=mock_detect_ok_customize_missing,
        )
        assert result.success is False


class TestRebuildInitramfsDebian:
    """Test initramfs rebuild with Debian/Ubuntu detection."""

    def test_detects_update_initramfs(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_debian(cmd, **kwargs):
            # Dracut check fails, update-initramfs succeeds
            if "virt-cat" in cmd and "/usr/bin/dracut" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = rebuild_initramfs(disk, run_command=mock_debian)
        assert result.success is True
        assert any("update-initramfs" in a for a in result.actions)

    def test_debian_modules_appended(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_debian(cmd, **kwargs):
            if "virt-cat" in cmd and "/usr/bin/dracut" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        tracker_cmds = []

        def mock_debian_track(cmd, **kwargs):
            tracker_cmds.append(list(cmd))
            if "virt-cat" in cmd and "/usr/bin/dracut" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        rebuild_initramfs(disk, run_command=mock_debian_track)
        # Should have command referencing initramfs-tools/modules
        module_cmds = [
            cmd for cmd in tracker_cmds
            if any("initramfs-tools/modules" in arg for arg in cmd)
        ]
        assert len(module_cmds) == 1

    def test_update_initramfs_command(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_debian(cmd, **kwargs):
            if "virt-cat" in cmd and "/usr/bin/dracut" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        tracker_cmds = []

        def mock_debian_track(cmd, **kwargs):
            tracker_cmds.append(list(cmd))
            if "virt-cat" in cmd and "/usr/bin/dracut" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        rebuild_initramfs(disk, run_command=mock_debian_track)
        rebuild_cmds = [
            cmd for cmd in tracker_cmds
            if "virt-customize" in cmd
            and any("update-initramfs -u" in arg for arg in cmd)
        ]
        assert len(rebuild_cmds) == 1


class TestNoInitramfsTool:
    """Test behavior when no known initramfs tool is found."""

    def test_graceful_skip(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = rebuild_initramfs(disk, run_command=_mock_run_failure)
        assert result.success is True
        assert any("skipping" in a.lower() for a in result.actions)


# ---------------------------------------------------------------------------
# update_grub
# ---------------------------------------------------------------------------

class TestUpdateGrub:
    """Test GRUB configuration regeneration."""

    def test_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = update_grub(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_disk_not_found(self):
        result = update_grub("/nonexistent/disk.qcow2",
                             run_command=_mock_run_success)
        assert result.success is False

    def test_grub_detected(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = update_grub(disk, run_command=_mock_run_success)
        assert any("GRUB" in a for a in result.actions)

    def test_grub_mkconfig_command(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        update_grub(disk, run_command=tracker)
        grub_cmds = [
            cmd for cmd in tracker.commands
            if any("grub" in arg.lower() for arg in cmd)
        ]
        assert len(grub_cmds) >= 1

    def test_no_grub_skips(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_no_grub(cmd, **kwargs):
            if "virt-cat" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = update_grub(disk, run_command=mock_no_grub)
        assert result.success is True
        assert any("not detected" in a.lower() for a in result.actions)

    def test_virt_customize_not_found(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_detect_ok_customize_missing(cmd, **kwargs):
            if "virt-cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise FileNotFoundError("virt-customize not found")

        result = update_grub(
            disk, run_command=mock_detect_ok_customize_missing,
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# fix_fstab_disk_references
# ---------------------------------------------------------------------------

class TestFixFstab:
    """Test fstab device name fixup."""

    def test_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_fstab_disk_references(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_disk_not_found(self):
        result = fix_fstab_disk_references(
            "/nonexistent/disk.qcow2",
            run_command=_mock_run_success,
        )
        assert result.success is False

    def test_checks_fstab(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_fstab_disk_references(disk, run_command=_mock_run_success)
        assert any("fstab" in a for a in result.actions)

    def test_fstab_command_references_disk(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        fix_fstab_disk_references(disk, run_command=tracker)
        fstab_cmds = [
            cmd for cmd in tracker.commands
            if any("fstab" in arg for arg in cmd)
        ]
        assert len(fstab_cmds) >= 1


# ---------------------------------------------------------------------------
# full_bootloader_fixup
# ---------------------------------------------------------------------------

class TestFullBootloaderFixup:
    """Test the combined bootloader fixup pipeline."""

    def test_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = full_bootloader_fixup(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_combines_all_actions(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = full_bootloader_fixup(disk, run_command=_mock_run_success)
        # Should have actions from initramfs + grub + fstab
        assert len(result.actions) >= 3

    def test_disk_not_found(self):
        result = full_bootloader_fixup(
            "/nonexistent/disk.qcow2",
            run_command=_mock_run_success,
        )
        assert result.success is False

    def test_partial_failure(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        call_count = 0

        def mock_partial_fail(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # Fail on the 5th call (during grub regen)
            if call_count == 5:
                raise FileNotFoundError("virt-customize not found")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = full_bootloader_fixup(disk, run_command=mock_partial_fail)
        # Should have some errors but continue with other steps
        assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    """Test virtio module constants."""

    def test_core_modules(self):
        assert "virtio" in VIRTIO_MODULES
        assert "virtio_pci" in VIRTIO_MODULES
        assert "virtio_blk" in VIRTIO_MODULES
        assert "virtio_scsi" in VIRTIO_MODULES
        assert "virtio_net" in VIRTIO_MODULES

    def test_extra_modules(self):
        assert "virtio_balloon" in VIRTIO_EXTRA_MODULES
        assert "virtio_console" in VIRTIO_EXTRA_MODULES
