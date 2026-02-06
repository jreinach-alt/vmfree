"""VMDK descriptor file inspector.

VMDK descriptor files are small text files that describe the layout of a
virtual disk. They are NOT the binary flat/sparse data files — qemu-img
must always be pointed at the descriptor, never at the -flat.vmdk.

This module parses VMDK descriptors to extract:
  - createType (monolithicSparse, monolithicFlat, twoGbMaxExtentSparse, etc.)
  - Extent descriptions (size in sectors, access, type, filename)
  - Disk Data Base (DDB) entries (geometry, adapter type, hardware version)
  - Parent CID for snapshot chain detection
  - Virtual size computed from extent sectors

VMDK descriptor format:
  - Lines starting with # are comments.
  - Key=value pairs (no spaces around =, or with spaces).
  - Extent lines: <access> <sectors> <type> "<filename>"
  - DDB lines: ddb.<key> = "<value>"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vmfree.models import DiskType

# Sector size is always 512 bytes in VMDK
SECTOR_SIZE = 512


@dataclass
class VMDKExtent:
    """A single extent in a VMDK descriptor.

    Attributes:
        access: Access mode (RW, RDONLY, NOACCESS).
        sectors: Number of 512-byte sectors in this extent.
        extent_type: Extent storage type (SPARSE, FLAT, ZERO, VMFS, VMFSSPARSE).
        filename: File backing this extent.
    """

    access: str
    sectors: int
    extent_type: str
    filename: str

    @property
    def size_bytes(self) -> int:
        """Size of this extent in bytes."""
        return self.sectors * SECTOR_SIZE


@dataclass
class VMDKInfo:
    """Parsed information from a VMDK descriptor file.

    Attributes:
        path: Path to the descriptor file.
        version: Descriptor version number.
        cid: Content ID of this descriptor.
        parent_cid: Content ID of the parent descriptor (ffffffff = no parent).
        create_type: VMDK creation type string.
        disk_type: Parsed DiskType enum.
        extents: List of extent descriptions.
        ddb: Disk Data Base key-value entries.
    """

    path: Path
    version: int = 1
    cid: str = "ffffffff"
    parent_cid: str = "ffffffff"
    create_type: str = ""
    disk_type: DiskType = DiskType.UNKNOWN
    extents: list[VMDKExtent] = field(default_factory=list)
    ddb: dict[str, str] = field(default_factory=dict)

    @property
    def virtual_size_bytes(self) -> int:
        """Total virtual disk size in bytes, summed from all extents."""
        return sum(ext.size_bytes for ext in self.extents)

    @property
    def virtual_size_gb(self) -> float:
        """Total virtual disk size in gigabytes."""
        return self.virtual_size_bytes / (1024 ** 3)

    @property
    def has_parent(self) -> bool:
        """Whether this VMDK is a delta/snapshot that references a parent."""
        return self.parent_cid.lower() != "ffffffff"

    @property
    def adapter_type(self) -> str:
        """Storage adapter type from DDB (lsilogic, ide, buslogic, etc.)."""
        return self.ddb.get("ddb.adaptertype", "")

    @property
    def geometry(self) -> tuple[int, int, int]:
        """Disk geometry as (cylinders, heads, sectors) from DDB."""
        c = int(self.ddb.get("ddb.geometry.cylinders", "0"))
        h = int(self.ddb.get("ddb.geometry.heads", "0"))
        s = int(self.ddb.get("ddb.geometry.sectors", "0"))
        return (c, h, s)

    @property
    def hardware_version(self) -> int:
        """Virtual hardware version from DDB."""
        return int(self.ddb.get("ddb.virtualhwversion", "0"))


def inspect_vmdk(path: str | Path) -> VMDKInfo:
    """Parse a VMDK descriptor file and return its metadata.

    Args:
        path: Path to the VMDK descriptor file.

    Returns:
        VMDKInfo with all parsed fields.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not a valid VMDK descriptor.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"VMDK descriptor not found: {path}")

    raw = path.read_text(encoding="utf-8", errors="replace")

    # Quick check: VMDK descriptors start with a comment or version line.
    # Binary VMDK data files start with "KDMV" or other binary magic.
    stripped = raw.lstrip()
    if stripped and stripped[0] not in ("#", "v", "V", "\n", "\r"):
        raise ValueError(
            f"Not a VMDK descriptor (looks like binary data): {path}"
        )

    info = VMDKInfo(path=path)
    _parse_descriptor(raw, info)

    if not info.create_type:
        raise ValueError(f"VMDK descriptor missing createType: {path}")

    info.disk_type = _classify_disk_type(info.create_type)
    return info


def _parse_descriptor(text: str, info: VMDKInfo) -> None:
    """Parse descriptor text and populate VMDKInfo fields."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # DDB entries: ddb.key = "value"
        if line.lower().startswith("ddb."):
            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip().strip('"')
            info.ddb[key] = value
            continue

        # Key=value entries (version, CID, parentCID, createType)
        if "=" in line and not _is_extent_line(line):
            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip().strip('"')

            if key == "version":
                info.version = int(value)
            elif key == "cid":
                info.cid = value
            elif key == "parentcid":
                info.parent_cid = value
            elif key == "createtype":
                info.create_type = value
            elif key == "parentfilenamehint":
                # Snapshot parent path — we store in ddb for convenience
                info.ddb["parentfilenamehint"] = value
            continue

        # Extent lines: <access> <sectors> <type> "<filename>"
        extent = _parse_extent_line(line)
        if extent:
            info.extents.append(extent)


def _is_extent_line(line: str) -> bool:
    """Check if a line looks like an extent description."""
    parts = line.split()
    if len(parts) < 4:
        return False
    return parts[0] in ("RW", "RDONLY", "NOACCESS")


def _parse_extent_line(line: str) -> VMDKExtent | None:
    """Parse an extent description line.

    Format: <access> <sectors> <type> "<filename>" [offset]
    Example: RW 134217728 SPARSE "disk.vmdk"
    """
    parts = line.split()
    if len(parts) < 4:
        return None

    access = parts[0]
    if access not in ("RW", "RDONLY", "NOACCESS"):
        return None

    try:
        sectors = int(parts[1])
    except ValueError:
        return None

    extent_type = parts[2]
    # Filename is everything from the first quote to the last quote
    remainder = " ".join(parts[3:])
    filename = remainder.strip('"')

    return VMDKExtent(
        access=access,
        sectors=sectors,
        extent_type=extent_type,
        filename=filename,
    )


def _classify_disk_type(create_type: str) -> DiskType:
    """Map a createType string to a DiskType enum value."""
    mapping = {
        "monolithicsparse": DiskType.MONOLITHIC_SPARSE,
        "monolithicflat": DiskType.MONOLITHIC_FLAT,
        "twogbmaxextentsparse": DiskType.TWO_GB_MAX_EXTENT_SPARSE,
        "twogbmaxextentflat": DiskType.TWO_GB_MAX_EXTENT_FLAT,
        "vmfs": DiskType.VMFS,
        "vmfssparse": DiskType.VMFS_SPARSE,
        "vmfsthin": DiskType.VMFS_THIN,
        "sesparse": DiskType.SE_SPARSE,
        "streamoptimized": DiskType.STREAM_OPTIMIZED,
    }
    return mapping.get(create_type.lower(), DiskType.UNKNOWN)
