"""Disk conversion engine — wraps qemu-img for VMDK to qcow2/raw conversion.

Handles the full lifecycle:
  1. Snapshot chain detection and merging (leaf-to-root).
  2. Format conversion (VMDK → qcow2 or raw).
  3. Progress reporting via callback for Rich integration.
  4. Post-conversion integrity verification.

Critical rules:
  - Always point qemu-img at the DESCRIPTOR .vmdk, never the -flat.vmdk.
  - Both descriptor and flat files must be in the same directory.
  - Snapshot chains: merge deltas from leaf → root before converting base.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vmfree.converter.validate import check_output_integrity


@dataclass
class ConversionResult:
    """Result of a disk conversion operation.

    Attributes:
        success: Whether the conversion completed successfully.
        output_path: Path to the converted disk image.
        message: Human-readable status message.
        errors: List of error messages if conversion failed.
    """

    success: bool
    output_path: Path | None = None
    message: str = ""
    errors: list[str] = field(default_factory=list)


# Type alias for progress callbacks: (percent: float, bytes_written: int)
ProgressCallback = Callable[[float, int], None]


def convert_disk(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    disk_format: str = "qcow2",
    compress: bool = False,
    preallocation: str | None = None,
    progress_callback: ProgressCallback | None = None,
    run_command: object = None,
) -> ConversionResult:
    """Convert a VMDK disk to qcow2 or raw format using qemu-img.

    Args:
        source_path: Path to the VMDK descriptor file (NOT the -flat.vmdk).
        output_dir: Directory to write the converted disk into.
        disk_format: Target format — "qcow2" or "raw".
        compress: Enable qcow2 compression (-c). Only applies to qcow2 format.
        preallocation: Preallocation mode ("off", "metadata", "full", or None).
        progress_callback: Optional callback for progress updates.
            Called with (percent, bytes_written).
        run_command: Optional callable to execute subprocess commands.
            Signature: (args, **kwargs) -> CompletedProcess.
            Defaults to subprocess.run. Used for testing.

    Returns:
        ConversionResult with success status and output path.
    """
    source = Path(source_path)
    outdir = Path(output_dir)
    runner = run_command or subprocess.run

    if not source.exists():
        return ConversionResult(
            success=False,
            errors=[f"Source VMDK not found: {source}"],
        )

    if not outdir.exists():
        return ConversionResult(
            success=False,
            errors=[f"Output directory not found: {outdir}"],
        )

    ext = "qcow2" if disk_format == "qcow2" else "raw"
    output_path = outdir / f"{source.stem}.{ext}"

    cmd = [
        "qemu-img", "convert",
        "-p",               # Progress reporting
        "-f", "vmdk",       # Source format
        "-O", disk_format,  # Target format
    ]

    # qcow2-specific options
    if disk_format == "qcow2" and compress:
        cmd.append("-c")

    output_options: list[str] = []
    if preallocation:
        output_options.append(f"preallocation={preallocation}")
    if output_options:
        cmd.extend(["-o", ",".join(output_options)])

    cmd.extend([str(source), str(output_path)])

    try:
        result = runner(
            cmd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ConversionResult(
            success=False,
            errors=["qemu-img not found on PATH"],
        )

    if result.returncode != 0:
        return ConversionResult(
            success=False,
            output_path=output_path,
            errors=[f"qemu-img convert failed: {result.stderr.strip()}"],
        )

    # Parse any progress from stderr/stdout for the callback
    if progress_callback and result.stderr:
        _parse_progress(result.stderr, progress_callback)

    # Signal completion
    if progress_callback:
        progress_callback(100.0, 0)

    return ConversionResult(
        success=True,
        output_path=output_path,
        message=f"Converted {source.name} → {output_path.name}",
    )


def merge_snapshot_chain(
    chain: list[str | Path],
    *,
    run_command: object = None,
) -> ConversionResult:
    """Merge a VMDK snapshot chain from leaf to root.

    The chain should be ordered leaf-first: [current, delta-000002, delta-000001, base].
    Each delta is committed into its parent using `qemu-img commit`.
    After merging, the base VMDK contains the fully-merged state.

    Args:
        chain: Ordered list of VMDK paths, leaf first.
        run_command: Optional callable for subprocess execution.

    Returns:
        ConversionResult pointing to the merged base VMDK.
    """
    if not chain:
        return ConversionResult(success=False, errors=["Empty snapshot chain"])

    if len(chain) == 1:
        return ConversionResult(
            success=True,
            output_path=Path(chain[0]),
            message="Single disk, no merge needed",
        )

    runner = run_command or subprocess.run

    # Commit each delta into its parent, starting from the leaf.
    # qemu-img commit merges a delta into its backing file.
    # We go leaf → root, committing each along the way.
    for delta_path in chain[:-1]:
        delta = Path(delta_path)
        cmd = ["qemu-img", "commit", "-f", "vmdk", str(delta)]

        try:
            result = runner(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            return ConversionResult(
                success=False,
                errors=["qemu-img not found on PATH"],
            )

        if result.returncode != 0:
            return ConversionResult(
                success=False,
                errors=[f"Merge failed for {delta.name}: {result.stderr.strip()}"],
            )

    base = Path(chain[-1])
    return ConversionResult(
        success=True,
        output_path=base,
        message=f"Merged {len(chain) - 1} snapshots into {base.name}",
    )


def verify_conversion(
    output_path: str | Path,
    disk_format: str = "qcow2",
    *,
    run_command: object = None,
) -> ConversionResult:
    """Verify a converted disk image using qemu-img check.

    Args:
        output_path: Path to the converted disk.
        disk_format: Format of the disk (qcow2, raw).
        run_command: Optional callable for subprocess execution.

    Returns:
        ConversionResult indicating pass/fail.
    """
    result = check_output_integrity(
        output_path,
        disk_format=disk_format,
        run_command=run_command,
    )
    return ConversionResult(
        success=result.ok,
        output_path=Path(output_path),
        message=result.message,
    )


def _parse_progress(output: str, callback: ProgressCallback) -> None:
    """Parse qemu-img progress output and invoke callback.

    qemu-img -p outputs lines like: "    (XX.XX/100%)"
    or simple percentages like "12.50" on stderr.
    """
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%?", output):
        pct = float(match.group(1))
        if 0 <= pct <= 100:
            callback(pct, 0)
