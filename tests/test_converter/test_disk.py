"""Tests for the disk conversion engine.

All subprocess calls are mocked — no actual qemu-img binary required.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call

from vmfree.converter.disk import (
    ConversionResult,
    _parse_progress,
    convert_disk,
    merge_snapshot_chain,
    verify_conversion,
)


def make_mock_run(returncode=0, stdout="", stderr=""):
    """Create a mock subprocess.run that returns a fixed result."""
    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return mock_run


# ---------------------------------------------------------------------------
# convert_disk
# ---------------------------------------------------------------------------

class TestConvertDisk:
    """Test VMDK to qcow2/raw conversion."""

    def test_successful_conversion(self, tmp_path):
        source = tmp_path / "server.vmdk"
        source.write_text("descriptor")
        outdir = tmp_path / "out"
        outdir.mkdir()

        result = convert_disk(source, outdir, run_command=make_mock_run())
        assert result.success is True
        assert result.output_path == outdir / "server.qcow2"
        assert "server.vmdk" in result.message

    def test_raw_format(self, tmp_path):
        source = tmp_path / "server.vmdk"
        source.write_text("descriptor")
        outdir = tmp_path / "out"
        outdir.mkdir()

        result = convert_disk(source, outdir, disk_format="raw",
                              run_command=make_mock_run())
        assert result.success is True
        assert result.output_path.suffix == ".raw"

    def test_source_not_found(self, tmp_path):
        result = convert_disk("/nonexistent/disk.vmdk", tmp_path)
        assert result.success is False
        assert "not found" in result.errors[0].lower()

    def test_output_dir_not_found(self, tmp_path):
        source = tmp_path / "disk.vmdk"
        source.write_text("descriptor")
        result = convert_disk(source, "/nonexistent/dir")
        assert result.success is False
        assert "not found" in result.errors[0].lower()

    def test_qemu_img_not_found(self, tmp_path):
        source = tmp_path / "disk.vmdk"
        source.write_text("descriptor")
        outdir = tmp_path / "out"
        outdir.mkdir()

        def raise_fnf(cmd, **kw):
            raise FileNotFoundError

        result = convert_disk(source, outdir, run_command=raise_fnf)
        assert result.success is False
        assert "qemu-img" in result.errors[0].lower()

    def test_qemu_img_fails(self, tmp_path):
        source = tmp_path / "disk.vmdk"
        source.write_text("descriptor")
        outdir = tmp_path / "out"
        outdir.mkdir()

        result = convert_disk(
            source, outdir,
            run_command=make_mock_run(returncode=1, stderr="qemu-img: error"),
        )
        assert result.success is False
        assert "error" in result.errors[0].lower()

    def test_correct_command_args(self, tmp_path):
        source = tmp_path / "disk.vmdk"
        source.write_text("descriptor")
        outdir = tmp_path / "out"
        outdir.mkdir()

        captured_cmd = []

        def capture_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        convert_disk(source, outdir, disk_format="qcow2", run_command=capture_run)

        assert captured_cmd[0] == "qemu-img"
        assert captured_cmd[1] == "convert"
        assert "-p" in captured_cmd
        assert "-f" in captured_cmd
        idx = captured_cmd.index("-f")
        assert captured_cmd[idx + 1] == "vmdk"
        assert "-O" in captured_cmd
        idx = captured_cmd.index("-O")
        assert captured_cmd[idx + 1] == "qcow2"
        assert str(source) in captured_cmd
        assert str(outdir / "disk.qcow2") in captured_cmd

    def test_progress_callback_called(self, tmp_path):
        source = tmp_path / "disk.vmdk"
        source.write_text("descriptor")
        outdir = tmp_path / "out"
        outdir.mkdir()

        callback = MagicMock()
        convert_disk(
            source, outdir,
            progress_callback=callback,
            run_command=make_mock_run(stderr="  25.00%\n  50.00%\n  100.00%\n"),
        )
        # At minimum, 100% completion callback is always called
        callback.assert_called()
        # Final call should be 100%
        last_call = callback.call_args_list[-1]
        assert last_call == call(100.0, 0)

    def test_descriptor_not_flat_in_command(self, tmp_path):
        """Verify we point at the descriptor, not the -flat.vmdk."""
        desc = tmp_path / "server.vmdk"
        flat = tmp_path / "server-flat.vmdk"
        desc.write_text("descriptor")
        flat.write_bytes(b"\x00" * 100)
        outdir = tmp_path / "out"
        outdir.mkdir()

        captured_cmd = []

        def capture_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        convert_disk(desc, outdir, run_command=capture_run)

        # The source arg should be the descriptor, not the flat file
        assert str(desc) in captured_cmd
        assert str(flat) not in captured_cmd


# ---------------------------------------------------------------------------
# merge_snapshot_chain
# ---------------------------------------------------------------------------

class TestMergeSnapshotChain:
    """Test snapshot chain merging (leaf to root)."""

    def test_empty_chain(self):
        result = merge_snapshot_chain([])
        assert result.success is False

    def test_single_disk_no_merge(self, tmp_path):
        disk = tmp_path / "base.vmdk"
        disk.write_text("base")
        result = merge_snapshot_chain([str(disk)])
        assert result.success is True
        assert result.output_path == disk
        assert "no merge" in result.message.lower()

    def test_two_disk_chain(self, tmp_path):
        delta = tmp_path / "delta.vmdk"
        base = tmp_path / "base.vmdk"
        delta.write_text("delta")
        base.write_text("base")

        captured = []

        def capture_run(cmd, **kwargs):
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = merge_snapshot_chain(
            [str(delta), str(base)],
            run_command=capture_run,
        )
        assert result.success is True
        assert result.output_path == base
        assert len(captured) == 1
        assert "commit" in captured[0]
        assert str(delta) in captured[0]

    def test_three_disk_chain(self, tmp_path):
        leaf = tmp_path / "current.vmdk"
        delta = tmp_path / "delta-001.vmdk"
        base = tmp_path / "base.vmdk"
        leaf.write_text("leaf")
        delta.write_text("delta")
        base.write_text("base")

        captured = []

        def capture_run(cmd, **kwargs):
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = merge_snapshot_chain(
            [str(leaf), str(delta), str(base)],
            run_command=capture_run,
        )
        assert result.success is True
        assert result.output_path == base
        # Should commit leaf then delta (2 commits)
        assert len(captured) == 2
        assert str(leaf) in captured[0]
        assert str(delta) in captured[1]

    def test_merge_failure(self, tmp_path):
        delta = tmp_path / "delta.vmdk"
        base = tmp_path / "base.vmdk"
        delta.write_text("delta")
        base.write_text("base")

        result = merge_snapshot_chain(
            [str(delta), str(base)],
            run_command=make_mock_run(returncode=1, stderr="merge error"),
        )
        assert result.success is False
        assert "merge error" in result.errors[0].lower()

    def test_qemu_img_missing(self, tmp_path):
        delta = tmp_path / "delta.vmdk"
        base = tmp_path / "base.vmdk"
        delta.write_text("delta")
        base.write_text("base")

        def raise_fnf(cmd, **kw):
            raise FileNotFoundError

        result = merge_snapshot_chain(
            [str(delta), str(base)],
            run_command=raise_fnf,
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# verify_conversion
# ---------------------------------------------------------------------------

class TestVerifyConversion:
    """Test post-conversion verification."""

    def test_passes(self, tmp_path):
        disk = tmp_path / "disk.qcow2"
        disk.write_bytes(b"\x00" * 100)
        result = verify_conversion(
            disk, run_command=make_mock_run(),
        )
        assert result.success is True

    def test_fails(self, tmp_path):
        disk = tmp_path / "disk.qcow2"
        disk.write_bytes(b"\x00" * 100)
        result = verify_conversion(
            disk,
            run_command=make_mock_run(returncode=1, stderr="corrupted"),
        )
        assert result.success is False

    def test_raw_no_check(self, tmp_path):
        disk = tmp_path / "disk.raw"
        disk.write_bytes(b"\x00" * 100)
        result = verify_conversion(disk, disk_format="raw")
        assert result.success is True


# ---------------------------------------------------------------------------
# Progress parsing
# ---------------------------------------------------------------------------

class TestParseProgress:
    """Test qemu-img progress output parsing."""

    def test_parses_percentages(self):
        callback = MagicMock()
        _parse_progress("  25.00%\n  50.00%\n  100.00%\n", callback)
        percents = [c.args[0] for c in callback.call_args_list]
        assert 25.0 in percents
        assert 50.0 in percents
        assert 100.0 in percents

    def test_no_progress(self):
        callback = MagicMock()
        _parse_progress("", callback)
        callback.assert_not_called()

    def test_ignores_out_of_range(self):
        callback = MagicMock()
        _parse_progress("150%", callback)
        # 150 is out of range, should not be called
        callback.assert_not_called()


# ---------------------------------------------------------------------------
# ConversionResult dataclass
# ---------------------------------------------------------------------------

class TestConversionResult:
    """Test the ConversionResult dataclass."""

    def test_success(self):
        r = ConversionResult(success=True, output_path=Path("/out/disk.qcow2"))
        assert r.success is True
        assert r.errors == []

    def test_failure(self):
        r = ConversionResult(success=False, errors=["bad thing"])
        assert r.success is False
        assert len(r.errors) == 1
