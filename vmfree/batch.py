"""Batch migration engine for migrating multiple VMs at once.

Discovers VM files in a directory or reads an inventory file, then
migrates each VM sequentially with progress tracking, error handling,
and resume capability.

State is persisted to a JSON file so interrupted batches can be resumed
without re-converting already-completed VMs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

VM_EXTENSIONS = {".vmx", ".ovf", ".ova"}


@dataclass
class BatchItem:
    """Status of a single VM in a batch migration."""

    source_path: str
    status: str = "pending"  # pending | running | success | failed | skipped
    error: str = ""
    duration_seconds: float = 0.0


@dataclass
class BatchResult:
    """Aggregate result of a batch migration run."""

    items: list[BatchItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for i in self.items if i.status == "success")

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for i in self.items if i.status == "skipped")

    @property
    def pending(self) -> int:
        return sum(1 for i in self.items if i.status == "pending")


def discover_vms(directory: str | Path) -> list[Path]:
    """Walk a directory tree and find all VMX, OVF, and OVA files.

    Args:
        directory: Root directory to search.

    Returns:
        Sorted list of discovered VM file paths.
    """
    directory = Path(directory)
    results: list[Path] = []

    if not directory.is_dir():
        return results

    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() in VM_EXTENSIONS and path.is_file():
            results.append(path)

    return results


def load_inventory(inventory_file: str | Path) -> list[Path]:
    """Load VM paths from an inventory file (one path per line).

    Lines starting with # are comments. Blank lines are skipped.

    Args:
        inventory_file: Path to the inventory text file.

    Returns:
        List of VM file paths.
    """
    inventory_file = Path(inventory_file)
    results: list[Path] = []

    for line in inventory_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        results.append(Path(line))

    return results


def load_state(state_file: str | Path) -> dict[str, str]:
    """Load batch state from a JSON file for resume capability.

    Args:
        state_file: Path to the state JSON file.

    Returns:
        Dict mapping source path strings to status strings.
    """
    state_file = Path(state_file)
    if not state_file.exists():
        return {}

    try:
        data = json.loads(state_file.read_text())
        return {item["source_path"]: item["status"] for item in data.get("items", [])}
    except (json.JSONDecodeError, KeyError):
        return {}


def save_state(state_file: str | Path, items: list[BatchItem]) -> None:
    """Save batch state to a JSON file for resume capability.

    Args:
        state_file: Path to write the state file.
        items: Current batch items with their statuses.
    """
    state_file = Path(state_file)
    data = {
        "items": [
            {
                "source_path": item.source_path,
                "status": item.status,
                "error": item.error,
                "duration_seconds": item.duration_seconds,
            }
            for item in items
        ]
    }
    state_file.write_text(json.dumps(data, indent=2))


def run_batch(
    sources: list[Path],
    *,
    migrate_fn: object,
    resume: bool = True,
    stop_on_error: bool = False,
    state_file: str | Path | None = None,
    **migrate_kwargs,
) -> BatchResult:
    """Run batch migration over a list of VM source files.

    Args:
        sources: List of VM source file paths.
        migrate_fn: Callable to migrate a single VM.
            Signature: (source: str, **kwargs) -> bool
        resume: If True, skip VMs that already succeeded in a previous run.
        stop_on_error: If True, stop the batch on the first failure.
        state_file: Path for persisting batch state (for resume).
        **migrate_kwargs: Additional keyword arguments passed to migrate_fn.

    Returns:
        BatchResult with per-VM status.
    """
    result = BatchResult()

    # Load previous state for resume
    prev_state: dict[str, str] = {}
    if resume and state_file:
        prev_state = load_state(state_file)

    # Build batch items
    for source in sources:
        item = BatchItem(source_path=str(source))
        if resume and prev_state.get(str(source)) == "success":
            item.status = "skipped"
        result.items.append(item)

    # Execute migrations
    for item in result.items:
        if item.status == "skipped":
            continue

        item.status = "running"
        start_time = time.monotonic()

        try:
            success = migrate_fn(source=item.source_path, **migrate_kwargs)
            item.status = "success" if success else "failed"
            if not success:
                item.error = "Migration returned failure"
        except Exception as exc:  # noqa: BLE001
            item.status = "failed"
            item.error = str(exc)

        item.duration_seconds = time.monotonic() - start_time

        # Persist state after each VM
        if state_file:
            save_state(state_file, result.items)

        if item.status == "failed" and stop_on_error:
            # Mark remaining items as pending (not attempted)
            break

    return result


def generate_report(result: BatchResult) -> str:
    """Generate a markdown report of the batch migration.

    Args:
        result: BatchResult from run_batch().

    Returns:
        Markdown-formatted report string.
    """
    lines = [
        "# VMFree Batch Migration Report",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total | {result.total} |",
        f"| Succeeded | {result.succeeded} |",
        f"| Failed | {result.failed} |",
        f"| Skipped | {result.skipped} |",
        "",
        "## Per-VM Results",
        "",
        "| VM | Status | Duration | Error |",
        "|----|--------|----------|-------|",
    ]

    for item in result.items:
        name = Path(item.source_path).stem
        duration = f"{item.duration_seconds:.1f}s" if item.duration_seconds > 0 else "-"
        error = item.error or "-"
        lines.append(f"| {name} | {item.status} | {duration} | {error} |")

    lines.append("")
    return "\n".join(lines)
