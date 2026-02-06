"""Tests for the VMDK descriptor inspector.

Tests against:
  - tests/fixtures/sample-descriptor.vmdk (monolithicSparse, lsilogic)
  - Inline descriptors for flat, snapshot/delta, twoGbMaxExtent, and error cases
"""

from pathlib import Path

import pytest

from vmfree.models import DiskType
from vmfree.parsers.vmdk import SECTOR_SIZE, VMDKExtent, inspect_vmdk

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture file: monolithicSparse
# ---------------------------------------------------------------------------

class TestSampleDescriptor:
    """Test against the sample-descriptor.vmdk fixture file."""

    @pytest.fixture
    def info(self):
        return inspect_vmdk(FIXTURES / "sample-descriptor.vmdk")

    def test_version(self, info):
        assert info.version == 1

    def test_cid(self, info):
        assert info.cid == "fffffffe"

    def test_parent_cid_no_parent(self, info):
        assert info.parent_cid == "ffffffff"
        assert info.has_parent is False

    def test_create_type(self, info):
        assert info.create_type == "monolithicSparse"

    def test_disk_type(self, info):
        assert info.disk_type == DiskType.MONOLITHIC_SPARSE

    def test_single_extent(self, info):
        assert len(info.extents) == 1

    def test_extent_details(self, info):
        ext = info.extents[0]
        assert ext.access == "RW"
        assert ext.sectors == 134217728
        assert ext.extent_type == "SPARSE"
        assert ext.filename == "sample-descriptor.vmdk"

    def test_virtual_size_bytes(self, info):
        expected = 134217728 * SECTOR_SIZE  # 64 GiB
        assert info.virtual_size_bytes == expected

    def test_virtual_size_gb(self, info):
        assert abs(info.virtual_size_gb - 64.0) < 0.01

    def test_adapter_type(self, info):
        assert info.adapter_type == "lsilogic"

    def test_geometry(self, info):
        c, h, s = info.geometry
        assert c == 8354
        assert h == 255
        assert s == 63

    def test_hardware_version(self, info):
        assert info.hardware_version == 21

    def test_path(self, info):
        assert info.path == FIXTURES / "sample-descriptor.vmdk"


# ---------------------------------------------------------------------------
# Inline: monolithicFlat descriptor
# ---------------------------------------------------------------------------

class TestMonolithicFlat:
    """Test a monolithicFlat descriptor (separate -flat.vmdk data file)."""

    @pytest.fixture
    def info(self, tmp_path):
        desc = tmp_path / "server.vmdk"
        desc.write_text(
            "# Disk DescriptorFile\n"
            "version=1\n"
            "CID=abcdef01\n"
            "parentCID=ffffffff\n"
            'createType="monolithicFlat"\n'
            "\n"
            '# Extent description\n'
            'RW 41943040 FLAT "server-flat.vmdk" 0\n'
            "\n"
            'ddb.virtualHWVersion = "19"\n'
            'ddb.geometry.cylinders = "2610"\n'
            'ddb.geometry.heads = "255"\n'
            'ddb.geometry.sectors = "63"\n'
            'ddb.adapterType = "ide"\n'
        )
        return inspect_vmdk(desc)

    def test_create_type(self, info):
        assert info.create_type == "monolithicFlat"

    def test_disk_type(self, info):
        assert info.disk_type == DiskType.MONOLITHIC_FLAT

    def test_extent_flat(self, info):
        ext = info.extents[0]
        assert ext.extent_type == "FLAT"
        assert ext.filename == "server-flat.vmdk" or "server-flat.vmdk" in ext.filename

    def test_virtual_size(self, info):
        # 41943040 sectors * 512 = 20 GiB
        expected_gb = (41943040 * SECTOR_SIZE) / (1024 ** 3)
        assert abs(info.virtual_size_gb - expected_gb) < 0.01

    def test_no_parent(self, info):
        assert info.has_parent is False

    def test_adapter_type(self, info):
        assert info.adapter_type == "ide"


# ---------------------------------------------------------------------------
# Inline: snapshot/delta VMDK with parent
# ---------------------------------------------------------------------------

class TestSnapshotDelta:
    """Test a delta VMDK that references a parent (snapshot chain)."""

    @pytest.fixture
    def info(self, tmp_path):
        desc = tmp_path / "server-000001.vmdk"
        desc.write_text(
            "# Disk DescriptorFile\n"
            "version=1\n"
            "CID=abcdef02\n"
            "parentCID=abcdef01\n"
            'createType="monolithicSparse"\n'
            'parentFileNameHint="server.vmdk"\n'
            "\n"
            'RW 41943040 SPARSE "server-000001.vmdk"\n'
            "\n"
            'ddb.virtualHWVersion = "19"\n'
        )
        return inspect_vmdk(desc)

    def test_has_parent(self, info):
        assert info.has_parent is True

    def test_parent_cid(self, info):
        assert info.parent_cid == "abcdef01"

    def test_cid(self, info):
        assert info.cid == "abcdef02"

    def test_parent_filename_hint(self, info):
        assert info.ddb.get("parentfilenamehint") == "server.vmdk"


# ---------------------------------------------------------------------------
# Inline: twoGbMaxExtentSparse (multiple extents)
# ---------------------------------------------------------------------------

class TestTwoGbMaxExtent:
    """Test a split VMDK with multiple extents."""

    @pytest.fixture
    def info(self, tmp_path):
        desc = tmp_path / "bigdisk.vmdk"
        desc.write_text(
            "# Disk DescriptorFile\n"
            "version=1\n"
            "CID=11111111\n"
            "parentCID=ffffffff\n"
            'createType="twoGbMaxExtentSparse"\n'
            "\n"
            'RW 4194304 SPARSE "bigdisk-s001.vmdk"\n'
            'RW 4194304 SPARSE "bigdisk-s002.vmdk"\n'
            'RW 4194304 SPARSE "bigdisk-s003.vmdk"\n'
            "\n"
            'ddb.virtualHWVersion = "14"\n'
            'ddb.adapterType = "buslogic"\n'
        )
        return inspect_vmdk(desc)

    def test_disk_type(self, info):
        assert info.disk_type == DiskType.TWO_GB_MAX_EXTENT_SPARSE

    def test_three_extents(self, info):
        assert len(info.extents) == 3

    def test_extent_filenames(self, info):
        names = [e.filename for e in info.extents]
        assert names == ["bigdisk-s001.vmdk", "bigdisk-s002.vmdk", "bigdisk-s003.vmdk"]

    def test_total_size_sums_extents(self, info):
        expected = 3 * 4194304 * SECTOR_SIZE
        assert info.virtual_size_bytes == expected

    def test_adapter_type(self, info):
        assert info.adapter_type == "buslogic"


# ---------------------------------------------------------------------------
# VMDKExtent unit tests
# ---------------------------------------------------------------------------

class TestVMDKExtent:
    """Test the VMDKExtent dataclass."""

    def test_size_bytes(self):
        ext = VMDKExtent(access="RW", sectors=2048, extent_type="SPARSE", filename="x.vmdk")
        assert ext.size_bytes == 2048 * 512

    def test_zero_sectors(self):
        ext = VMDKExtent(access="RW", sectors=0, extent_type="ZERO", filename="x.vmdk")
        assert ext.size_bytes == 0


# ---------------------------------------------------------------------------
# All createType classifications
# ---------------------------------------------------------------------------

class TestDiskTypeClassification:
    """Verify that every known createType maps to the correct DiskType."""

    @pytest.fixture
    def _make_desc(self, tmp_path):
        def _inner(create_type: str):
            desc = tmp_path / f"{create_type}.vmdk"
            desc.write_text(
                "# Disk DescriptorFile\n"
                "version=1\n"
                "CID=ffffffff\n"
                "parentCID=ffffffff\n"
                f'createType="{create_type}"\n'
                "\n"
                f'RW 1024 SPARSE "{create_type}.vmdk"\n'
            )
            return inspect_vmdk(desc)
        return _inner

    def test_monolithic_sparse(self, _make_desc):
        assert _make_desc("monolithicSparse").disk_type == DiskType.MONOLITHIC_SPARSE

    def test_monolithic_flat(self, _make_desc):
        assert _make_desc("monolithicFlat").disk_type == DiskType.MONOLITHIC_FLAT

    def test_two_gb_sparse(self, _make_desc):
        assert _make_desc("twoGbMaxExtentSparse").disk_type == DiskType.TWO_GB_MAX_EXTENT_SPARSE

    def test_two_gb_flat(self, _make_desc):
        assert _make_desc("twoGbMaxExtentFlat").disk_type == DiskType.TWO_GB_MAX_EXTENT_FLAT

    def test_vmfs(self, _make_desc):
        assert _make_desc("vmfs").disk_type == DiskType.VMFS

    def test_vmfs_sparse(self, _make_desc):
        assert _make_desc("vmfsSparse").disk_type == DiskType.VMFS_SPARSE

    def test_vmfs_thin(self, _make_desc):
        assert _make_desc("vmfsThin").disk_type == DiskType.VMFS_THIN

    def test_se_sparse(self, _make_desc):
        assert _make_desc("seSparse").disk_type == DiskType.SE_SPARSE

    def test_stream_optimized(self, _make_desc):
        assert _make_desc("streamOptimized").disk_type == DiskType.STREAM_OPTIMIZED

    def test_unknown_type(self, _make_desc):
        assert _make_desc("somethingNew").disk_type == DiskType.UNKNOWN


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestVmdkErrors:
    """Test error cases for VMDK inspector."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            inspect_vmdk("/nonexistent/disk.vmdk")

    def test_binary_file_rejected(self, tmp_path):
        binary = tmp_path / "binary.vmdk"
        binary.write_bytes(b"KDMV\x03\x00\x00\x00")
        with pytest.raises(ValueError, match="binary data"):
            inspect_vmdk(binary)

    def test_missing_create_type(self, tmp_path):
        desc = tmp_path / "bad.vmdk"
        desc.write_text("# Disk DescriptorFile\nversion=1\nCID=aaa\nparentCID=ffffffff\n")
        with pytest.raises(ValueError, match="missing createType"):
            inspect_vmdk(desc)
