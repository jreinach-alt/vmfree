"""Tests for pre-flight and post-conversion validation."""

import subprocess
from unittest.mock import patch

from vmfree.converter.validate import (
    ValidationResult,
    check_disk_exists,
    check_flat_file,
    check_free_space,
    check_output_integrity,
    check_qemu_img_installed,
    run_preflight,
)


class TestCheckQemuImgInstalled:
    """Test qemu-img binary detection."""

    def test_found(self):
        with patch("vmfree.converter.validate.shutil.which", return_value="/usr/bin/qemu-img"):
            result = check_qemu_img_installed()
            assert result.ok is True
            assert "qemu-img found" in result.message

    def test_not_found(self):
        with patch("vmfree.converter.validate.shutil.which", return_value=None):
            result = check_qemu_img_installed()
            assert result.ok is False
            assert "not found" in result.message


class TestCheckDiskExists:
    """Test disk file existence check."""

    def test_exists(self, tmp_path):
        disk = tmp_path / "test.vmdk"
        disk.write_text("descriptor")
        result = check_disk_exists(disk)
        assert result.ok is True

    def test_not_exists(self):
        result = check_disk_exists("/nonexistent/disk.vmdk")
        assert result.ok is False
        assert "not found" in result.message

    def test_directory_not_file(self, tmp_path):
        result = check_disk_exists(tmp_path)
        assert result.ok is False


class TestCheckFlatFile:
    """Test flat VMDK companion file check."""

    def test_flat_exists(self, tmp_path):
        desc = tmp_path / "server.vmdk"
        flat = tmp_path / "server-flat.vmdk"
        desc.write_text("descriptor")
        flat.write_bytes(b"\x00" * 100)
        result = check_flat_file(desc)
        assert result.ok is True
        assert "Flat file found" in result.message

    def test_sparse_no_flat_ok(self, tmp_path):
        desc = tmp_path / "server.vmdk"
        desc.write_text("descriptor")
        result = check_flat_file(desc)
        assert result.ok is True
        assert "sparse" in result.message.lower() or "self-contained" in result.message.lower()


class TestCheckFreeSpace:
    """Test free space check."""

    def test_enough_space(self, tmp_path):
        # tmp_path should have plenty of space
        result = check_free_space(tmp_path, 1024)
        assert result.ok is True
        assert "Free space" in result.message

    def test_not_enough_space(self, tmp_path):
        # Request an absurdly large amount
        result = check_free_space(tmp_path, 10 ** 18)
        assert result.ok is False
        assert "Insufficient" in result.message

    def test_dir_not_found(self):
        result = check_free_space("/nonexistent/dir", 1024)
        assert result.ok is False


class TestCheckOutputIntegrity:
    """Test post-conversion integrity check."""

    def test_file_not_found(self):
        result = check_output_integrity("/nonexistent/disk.qcow2")
        assert result.ok is False

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.qcow2"
        empty.write_bytes(b"")
        result = check_output_integrity(empty)
        assert result.ok is False
        assert "empty" in result.message

    def test_raw_skips_check(self, tmp_path):
        raw = tmp_path / "disk.raw"
        raw.write_bytes(b"\x00" * 100)
        result = check_output_integrity(raw, disk_format="raw")
        assert result.ok is True

    def test_qemu_check_passes(self, tmp_path):
        disk = tmp_path / "disk.qcow2"
        disk.write_bytes(b"\x00" * 100)

        def mock_run(*a, **kw):
            return subprocess.CompletedProcess(a[0], 0, "", "")

        result = check_output_integrity(disk, run_command=mock_run)
        assert result.ok is True

    def test_qemu_check_fails(self, tmp_path):
        disk = tmp_path / "disk.qcow2"
        disk.write_bytes(b"\x00" * 100)

        def mock_run(*a, **kw):
            return subprocess.CompletedProcess(a[0], 1, "", "corruption found")

        result = check_output_integrity(disk, run_command=mock_run)
        assert result.ok is False
        assert "corruption" in result.message

    def test_qemu_not_installed(self, tmp_path):
        disk = tmp_path / "disk.qcow2"
        disk.write_bytes(b"\x00" * 100)

        def raise_fnf(*a, **kw):
            raise FileNotFoundError

        result = check_output_integrity(disk, run_command=raise_fnf)
        assert result.ok is False


class TestRunPreflight:
    """Test combined pre-flight check runner."""

    @patch("vmfree.converter.validate.shutil.which", return_value="/usr/bin/qemu-img")
    def test_all_pass(self, _mock_which, tmp_path):
        disk = tmp_path / "test.vmdk"
        disk.write_text("descriptor")
        results = run_preflight(disk, tmp_path)
        assert all(r.ok for r in results)

    @patch("vmfree.converter.validate.shutil.which", return_value=None)
    def test_qemu_missing_flagged(self, _mock_which, tmp_path):
        disk = tmp_path / "test.vmdk"
        disk.write_text("descriptor")
        results = run_preflight(disk, tmp_path)
        assert results[0].ok is False  # qemu-img check
        assert results[1].ok is True   # disk exists

    def test_with_space_estimate(self, tmp_path):
        disk = tmp_path / "test.vmdk"
        disk.write_text("descriptor")
        with patch("vmfree.converter.validate.shutil.which", return_value="/usr/bin/qemu-img"):
            results = run_preflight(disk, tmp_path, estimated_bytes=1024)
            assert len(results) == 4  # includes space check


class TestValidationResult:
    """Test the ValidationResult dataclass."""

    def test_ok_result(self):
        r = ValidationResult(ok=True, message="all good")
        assert r.ok is True
        assert r.message == "all good"

    def test_failed_result(self):
        r = ValidationResult(ok=False, message="bad")
        assert r.ok is False
