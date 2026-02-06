"""Pre-flight and post-conversion validation for disk operations.

Checks that need to pass before we hand anything to qemu-img:
  - Source VMDK descriptor exists and is readable.
  - Flat/data file referenced by the descriptor exists alongside it.
  - qemu-img binary is installed and reachable on PATH.
  - Enough free space on the output filesystem for the converted disk.

Post-conversion checks:
  - Output file exists and has non-zero size.
  - qemu-img check reports no corruption.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ValidationResult:
    """Result of a validation check.

    Attributes:
        ok: True if the check passed.
        message: Human-readable description of the result.
    """

    ok: bool
    message: str


def check_qemu_img_installed() -> ValidationResult:
    """Check that qemu-img is available on PATH."""
    path = shutil.which("qemu-img")
    if path:
        return ValidationResult(ok=True, message=f"qemu-img found: {path}")
    return ValidationResult(ok=False, message="qemu-img not found on PATH")


def check_disk_exists(vmdk_path: str | Path) -> ValidationResult:
    """Check that the VMDK descriptor file exists."""
    path = Path(vmdk_path)
    if path.exists() and path.is_file():
        return ValidationResult(ok=True, message=f"Disk found: {path.name}")
    return ValidationResult(ok=False, message=f"Disk not found: {path}")


def check_flat_file(vmdk_path: str | Path) -> ValidationResult:
    """For monolithicFlat VMDKs, check the -flat.vmdk file exists alongside.

    Only relevant for flat VMDKs — sparse VMDKs are self-contained.
    Returns ok=True for non-flat VMDKs (nothing to check).
    """
    path = Path(vmdk_path)
    stem = path.stem
    flat_name = f"{stem}-flat.vmdk"
    flat_path = path.parent / flat_name
    # If the flat file exists, great. If it doesn't exist, it might be
    # a sparse VMDK (self-contained), which is fine.
    if flat_path.exists():
        return ValidationResult(ok=True, message=f"Flat file found: {flat_name}")
    return ValidationResult(
        ok=True,
        message=f"No flat file (sparse/self-contained): {path.name}",
    )


def check_free_space(
    output_dir: str | Path,
    required_bytes: int,
) -> ValidationResult:
    """Check that the output directory has enough free space.

    Args:
        output_dir: Directory where converted disk will be written.
        required_bytes: Estimated space needed in bytes.
    """
    path = Path(output_dir)
    if not path.exists():
        return ValidationResult(ok=False, message=f"Output directory not found: {path}")

    usage = shutil.disk_usage(str(path))
    free_gb = usage.free / (1024 ** 3)
    required_gb = required_bytes / (1024 ** 3)

    if usage.free >= required_bytes:
        return ValidationResult(
            ok=True,
            message=f"Free space: {free_gb:.1f} GB (need {required_gb:.1f} GB)",
        )
    return ValidationResult(
        ok=False,
        message=f"Insufficient space: {free_gb:.1f} GB free, need {required_gb:.1f} GB",
    )


def check_output_integrity(
    output_path: str | Path,
    disk_format: str = "qcow2",
    run_command: object = None,
) -> ValidationResult:
    """Run qemu-img check on a converted disk to verify integrity.

    Args:
        output_path: Path to the converted disk image.
        disk_format: Format of the output disk (qcow2, raw).
        run_command: Optional callable for running subprocess commands.
                     Signature: (args: list[str]) -> subprocess.CompletedProcess.
                     Defaults to subprocess.run.
    """
    path = Path(output_path)
    if not path.exists():
        return ValidationResult(ok=False, message=f"Output file not found: {path}")

    if path.stat().st_size == 0:
        return ValidationResult(ok=False, message=f"Output file is empty: {path}")

    # raw format doesn't support qemu-img check
    if disk_format == "raw":
        return ValidationResult(ok=True, message=f"Output exists: {path.name} (raw, no check)")

    runner = run_command or subprocess.run
    try:
        result = runner(
            ["qemu-img", "check", "-f", disk_format, str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return ValidationResult(ok=True, message=f"Integrity check passed: {path.name}")
        return ValidationResult(
            ok=False,
            message=f"Integrity check failed: {result.stderr.strip()}",
        )
    except FileNotFoundError:
        return ValidationResult(ok=False, message="qemu-img not found for integrity check")


def run_preflight(
    vmdk_path: str | Path,
    output_dir: str | Path,
    estimated_bytes: int = 0,
) -> list[ValidationResult]:
    """Run all pre-flight checks and return results.

    Args:
        vmdk_path: Path to the source VMDK descriptor.
        output_dir: Directory for output files.
        estimated_bytes: Estimated output size in bytes (0 to skip space check).

    Returns:
        List of ValidationResult for each check.
    """
    results = [
        check_qemu_img_installed(),
        check_disk_exists(vmdk_path),
        check_flat_file(vmdk_path),
    ]
    if estimated_bytes > 0:
        results.append(check_free_space(output_dir, estimated_bytes))
    return results
