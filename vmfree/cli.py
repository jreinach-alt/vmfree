"""VMFree CLI — main entry point.

Provides commands for migrating VMware VMs to KVM/Proxmox,
inspecting VM configurations, and converting disk formats.
Connects the full 7-stage pipeline with Rich terminal UI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vmfree import __version__
from vmfree.converter.disk import convert_disk
from vmfree.converter.validate import check_free_space, check_qemu_img_installed
from vmfree.generators.libvirt import generate_libvirt_xml
from vmfree.generators.proxmox import generate_proxmox_commands
from vmfree.mapper.hardware import (
    map_controller,
    map_display,
    map_firmware,
    map_hardware,
    map_nic,
)
from vmfree.models import VMDefinition
from vmfree.network.detect import detect_bridge
from vmfree.parsers.ovf import parse_ovf_or_ova
from vmfree.parsers.vmx import parse_vmx_file

console = Console()


def _parse_source(source: str) -> VMDefinition:
    """Parse a VMware source file (VMX, OVF, or OVA) into a VMDefinition.

    Dispatches to the correct parser based on file extension.

    Raises:
        click.ClickException: If the file type is unsupported or parsing fails.
    """
    source_path = Path(source)
    suffix = source_path.suffix.lower()

    try:
        if suffix == ".vmx":
            return parse_vmx_file(source_path)
        if suffix in (".ovf", ".ova"):
            return parse_ovf_or_ova(source_path)
        raise click.ClickException(
            f"Unsupported file type: {suffix}. Use .vmx, .ovf, or .ova"
        )
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e


@click.group()
@click.version_option(version=__version__, prog_name="vmfree")
def main():
    """VMFree — One-command migration from VMware to KVM/Proxmox.

    Converts VMware virtual machines (VMX, OVF/OVA, VMDK) to run on
    KVM-based hypervisors with working network connectivity.
    """


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--target", type=click.Choice(["kvm", "proxmox"]), required=True,
              help="Target hypervisor.")
@click.option("--output", type=click.Path(), default=None,
              help="Output directory for converted files (default: same as source).")
@click.option("--bridge", default=None, help="Network bridge (auto-detected if omitted).")
@click.option("--storage", default="local-lvm", help="Proxmox storage target.")
@click.option("--vmid", type=int, default=100, help="Proxmox VM ID.")
@click.option("--format", "disk_format", type=click.Choice(["qcow2", "raw"]), default="qcow2",
              help="Output disk format.")
@click.option("--no-fixup", is_flag=True, help="Skip guest OS modifications.")
@click.option("--windows-safe", is_flag=True,
              help="Use IDE+e1000 for Windows (default for detected Windows guests).")
@click.option("--preserve-mac", is_flag=True, help="Keep original MAC addresses.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without doing it.")
@click.option("--execute", is_flag=True,
              help="Execute Proxmox qm commands directly instead of writing a script.")
@click.option("-v", "--verbose", is_flag=True, help="Detailed output.")
def migrate(source, target, output, bridge, storage, vmid, disk_format,
            no_fixup, windows_safe, preserve_mac, dry_run, execute, verbose):
    """Migrate a VMware VM to KVM/Proxmox."""
    _run_migration(
        source=source,
        target=target,
        output_dir=output,
        bridge=bridge,
        storage=storage,
        vmid=vmid,
        disk_format=disk_format,
        windows_safe=windows_safe,
        preserve_mac=preserve_mac,
        dry_run=dry_run,
        execute=execute,
        verbose=verbose,
    )


@main.command()
@click.argument("source", type=click.Path(exists=True))
def inspect(source):
    """Analyze a VMware VM without migrating."""
    _run_inspect(source)


def _run_inspect(source: str) -> VMDefinition:
    """Parse and display VM analysis. Returns VMDefinition for testing."""
    vm = _parse_source(source)

    console.print(f"\n  [bold]VMFree v{__version__}[/bold] — VM Inspector\n")

    # -- General info --
    info_table = Table(show_header=False, box=None, padding=(0, 1))
    info_table.add_column(style="dim", min_width=16)
    info_table.add_column()

    info_table.add_row("Name:", vm.name)
    info_table.add_row("Guest OS:", vm.guest_os or "(not set)")
    info_table.add_row("vCPUs:", str(vm.vcpus))
    info_table.add_row("Memory:", f"{vm.memory_mb} MB")
    info_table.add_row("Firmware:", vm.firmware.value.upper())
    hw_ver = str(vm.hardware_version) if vm.hardware_version else "unknown"
    info_table.add_row("HW Version:", hw_ver)
    info_table.add_row("Source:", str(vm.source_file))

    if vm.is_windows:
        info_table.add_row("Windows:", "Yes (safe mode recommended)")

    console.print(Panel(info_table, title="VM Information", border_style="cyan"))

    # -- Disks --
    if vm.disks:
        disk_table = Table(show_header=True, box=None, padding=(0, 1))
        disk_table.add_column("#", style="dim")
        disk_table.add_column("Path")
        disk_table.add_column("Controller")
        disk_table.add_column("Size")
        disk_table.add_column("Boot")

        for i, disk in enumerate(vm.disks):
            size_str = _format_size(disk.size_bytes) if disk.size_bytes else "unknown"
            boot_str = "yes" if disk.is_boot else ""
            disk_table.add_row(
                str(i), Path(disk.path).name, f"{disk.controller}:{disk.unit}",
                size_str, boot_str,
            )

        console.print(Panel(disk_table, title="Disks", border_style="cyan"))

    # -- NICs --
    if vm.nics:
        nic_table = Table(show_header=True, box=None, padding=(0, 1))
        nic_table.add_column("#", style="dim")
        nic_table.add_column("Type")
        nic_table.add_column("MAC")
        nic_table.add_column("Connection")
        nic_table.add_column("Network")

        for i, nic in enumerate(vm.nics):
            nic_table.add_row(
                str(i), nic.virtual_dev, nic.mac_address or "(auto)",
                nic.connection_type, nic.network_name,
            )

        console.print(Panel(nic_table, title="Network Interfaces", border_style="cyan"))

    # -- Hardware mapping preview --
    hw_table = Table(show_header=True, box=None, padding=(0, 1))
    hw_table.add_column("VMware", style="dim")
    hw_table.add_column("KVM")

    if vm.scsi_controller:
        hw_table.add_row(
            f"Controller: {vm.scsi_controller}",
            map_controller(vm.scsi_controller),
        )

    for nic in vm.nics:
        hw_table.add_row(
            f"NIC: {nic.virtual_dev}",
            map_nic(nic.virtual_dev),
        )

    hw_table.add_row(f"Display: {vm.display}", map_display(vm.display))

    fw_path = map_firmware(vm.firmware.value)
    fw_label = f"OVMF ({fw_path})" if fw_path else "SeaBIOS"
    hw_table.add_row(f"Firmware: {vm.firmware.value}", fw_label)

    console.print(Panel(hw_table, title="Hardware Mapping Preview", border_style="cyan"))
    console.print()

    return vm


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--format", "disk_format", type=click.Choice(["qcow2", "raw"]), default="qcow2",
              help="Output disk format.")
@click.option("--output", type=click.Path(), default=".", help="Output directory.")
def convert(source, disk_format, output):
    """Convert a VMDK disk to qcow2 or raw format."""
    click.echo(f"Convert for {source} is not yet implemented.")


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--target", type=click.Choice(["kvm", "proxmox"]), default="kvm",
              help="Target hypervisor for validation context.")
@click.option("--output", type=click.Path(), default=".",
              help="Output directory for space check.")
def validate(source, target, output):
    """Run pre-flight checks on a VMware VM."""
    _run_validate(source=source, target=target, output_dir=output)


def _run_validate(
    *,
    source: str,
    target: str = "kvm",
    output_dir: str = ".",
) -> list[tuple[bool, str]]:
    """Run pre-flight checks and display results.

    Returns list of (ok, message) tuples for testing.
    """
    console.print(f"\n  [bold]VMFree v{__version__}[/bold] — Pre-flight Validation\n")

    results: list[tuple[bool, str]] = []

    # Check 1: Parse source file
    try:
        vm = _parse_source(source)
        results.append((True, f"Source parsed: {vm.name}"))
    except click.ClickException as e:
        results.append((False, f"Source parse failed: {e.message}"))
        _display_validation_results(results)
        return results

    # Check 2: Guest OS identification
    if vm.guest_os:
        results.append((True, f"Guest OS: {vm.guest_os}"))
    else:
        results.append((False, "Guest OS not identified"))

    # Check 3: Windows detection warning
    if vm.is_windows:
        results.append((
            True,
            "Windows detected: safe mode (IDE+e1000) will be used",
        ))

    # Check 4: Firmware
    fw_label = "EFI (OVMF required)" if vm.is_efi else "BIOS (SeaBIOS)"
    results.append((True, f"Firmware: {fw_label}"))

    # Check 5: OVMF firmware file exists (EFI only)
    if vm.is_efi:
        ovmf_path = Path(map_firmware("efi") or "")
        if ovmf_path.exists():
            results.append((True, f"OVMF found: {ovmf_path}"))
        else:
            results.append((
                False,
                f"OVMF not found: {ovmf_path} "
                "(install ovmf package)",
            ))

    # Check 6: Disk files exist
    for disk in vm.disks:
        disk_path = Path(disk.path)
        if disk_path.exists():
            results.append((True, f"Disk found: {disk_path.name}"))
        else:
            results.append((False, f"Disk missing: {disk_path.name}"))

    # Check 7: qemu-img available
    qemu_check = check_qemu_img_installed()
    results.append((qemu_check.ok, qemu_check.message))

    # Check 8: Output directory
    outdir = Path(output_dir)
    if outdir.exists():
        results.append((True, f"Output directory: {outdir}"))
    else:
        results.append((False, f"Output directory missing: {outdir}"))

    # Check 9: Free space on output
    if outdir.exists():
        total_disk_bytes = sum(d.size_bytes for d in vm.disks)
        if total_disk_bytes > 0:
            space_check = check_free_space(outdir, total_disk_bytes)
            results.append((space_check.ok, space_check.message))

    # Check 10: Network bridge detection
    bridge = detect_bridge(target=target)
    results.append((True, f"Network bridge: {bridge}"))

    _display_validation_results(results)
    return results


def _display_validation_results(results: list[tuple[bool, str]]) -> None:
    """Display validation results in a Rich panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold", min_width=4)
    table.add_column()

    all_ok = True
    for ok, msg in results:
        if ok:
            table.add_row("[green]PASS[/green]", msg)
        else:
            table.add_row("[red]FAIL[/red]", msg)
            all_ok = False

    border = "green" if all_ok else "red"
    title = "Validation Passed" if all_ok else "Validation Failed"
    console.print(Panel(table, title=title, border_style=border))
    console.print()


# ---------------------------------------------------------------------------
# Migration pipeline
# ---------------------------------------------------------------------------

def _run_migration(
    *,
    source: str,
    target: str,
    output_dir: str | None,
    bridge: str | None,
    storage: str,
    vmid: int,
    disk_format: str,
    windows_safe: bool,
    preserve_mac: bool,
    dry_run: bool,
    execute: bool = False,
    verbose: bool,
    run_command: object = None,
) -> bool:
    """Execute the full migration pipeline.

    Returns True on success, False on failure.
    """
    # Default output to the source file's directory
    if output_dir is None:
        output_dir = str(Path(source).parent)

    console.print(f"\n  [bold]VMFree v{__version__}[/bold] — VMware to KVM Migration Tool\n")

    # -- Stage 1: Parse input --
    try:
        vm = _parse_source(source)
    except click.ClickException as e:
        console.print(f"  [red]Error:[/red] {e.message}")
        sys.exit(1)

    # Auto-detect Windows → enable safe mode
    if vm.is_windows and not windows_safe:
        windows_safe = True

    # -- Stage 2: Map hardware --
    firmware_str = vm.firmware.value
    nic_type = vm.nics[0].virtual_dev if vm.nics else "e1000"
    hw = map_hardware(
        vm.scsi_controller,
        nic_type,
        vm.display,
        firmware_str,
        windows_safe=windows_safe,
    )

    # -- Stage 3: Detect network bridge --
    if bridge is None:
        bridge = detect_bridge(target=target)

    # -- Stage 4: Pre-flight checks --
    preflight_items = _build_preflight_display(vm, hw, bridge, target, storage)

    preflight_table = Table(show_header=False, box=None, padding=(0, 1))
    preflight_table.add_column(style="bold")
    for check_ok, msg in preflight_items:
        icon = "[green]ok[/green]" if check_ok else "[red]!![/red]"
        preflight_table.add_row(icon, msg)

    console.print(Panel(
        preflight_table,
        title="Pre-flight Check",
        border_style="cyan",
    ))

    if dry_run:
        return _show_dry_run(vm, hw, target, disk_format, bridge,
                             storage, vmid, preserve_mac, output_dir)

    # -- Stage 5: Convert disks --
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    converted_paths: list[Path] = []

    for disk in vm.disks:
        console.print(f"\n  Converting [bold]{Path(disk.path).name}[/bold]...")
        result = convert_disk(
            disk.path,
            outdir,
            disk_format=disk_format,
            run_command=run_command,
        )
        if not result.success:
            for err in result.errors:
                console.print(f"  [red]Error:[/red] {err}")
            sys.exit(1)
        if result.output_path:
            converted_paths.append(result.output_path)
            console.print(f"  [green]ok[/green] {result.message}")

    # -- Stage 6: Generate config --
    console.print()
    if target == "kvm":
        xml = generate_libvirt_xml(
            vm, hw,
            disk_paths=converted_paths,
            disk_format=disk_format,
            bridge=bridge,
            preserve_mac=preserve_mac,
        )
        xml_path = outdir / f"{vm.name}.xml"
        xml_path.write_text(xml)
        console.print(f"  [green]ok[/green] Libvirt XML written to {xml_path}")
    else:
        cmds = generate_proxmox_commands(
            vm, hw,
            vmid=vmid,
            disk_paths=[str(p) for p in converted_paths],
            storage=storage,
            bridge=bridge,
            preserve_mac=preserve_mac,
        )
        script_path = outdir / f"{vm.name}-proxmox.sh"
        script_path.write_text("#!/bin/bash\nset -e\n\n" + "\n\n".join(cmds) + "\n")
        console.print(f"  [green]ok[/green] Proxmox commands written to {script_path}")

        if execute:
            console.print("\n  [bold]Executing Proxmox commands...[/bold]")
            _execute_proxmox_commands(cmds, run_command=run_command)

    # -- Summary --
    _show_summary(vm, hw, target, bridge, converted_paths, vmid, preserve_mac)
    return True


def _build_preflight_display(
    vm, hw, bridge, target, storage,
) -> list[tuple[bool, str]]:
    """Build the list of preflight check display items."""
    items: list[tuple[bool, str]] = []

    # VM parsed
    items.append((
        True,
        f"VMX parsed: {vm.name} ({vm.guest_os}, {vm.vcpus} vCPU, "
        f"{vm.memory_mb} MB RAM)",
    ))

    # Disks found
    for disk in vm.disks:
        disk_name = Path(disk.path).name
        boot_tag = " [boot]" if disk.is_boot else ""
        items.append((True, f"Disk: {disk_name}{boot_tag}"))

    # Firmware
    fw_label = "EFI (OVMF)" if hw.firmware_path else "BIOS (SeaBIOS)"
    items.append((True, f"Firmware: {fw_label}"))

    # Hardware mapping
    items.append((True, f"Controller: {vm.scsi_controller} -> {hw.controller}"))
    if vm.nics:
        items.append((True, f"NIC: {vm.nics[0].virtual_dev} -> {hw.nic}"))

    # Windows safe mode
    if hw.is_safe_mode:
        items.append((True, "Windows safe mode: IDE + e1000 (no virtio drivers needed)"))

    # Network bridge
    items.append((True, f"Network bridge: {bridge}"))

    # Target
    if target == "proxmox":
        items.append((True, f"Proxmox storage: {storage}"))

    return items


def _show_dry_run(vm, hw, target, disk_format, bridge, storage, vmid,
                  preserve_mac, output_dir) -> bool:
    """Show what would be done without doing it."""
    console.print("\n  [yellow]DRY RUN[/yellow] — no changes will be made.\n")

    # Show planned disk conversions
    for disk in vm.disks:
        name = Path(disk.path).name
        stem = Path(disk.path).stem
        console.print(f"  Would convert: {name} -> {stem}.{disk_format}")

    # Show planned config output
    console.print()
    if target == "kvm":
        placeholder_paths = [
            f"{Path(output_dir) / Path(d.path).stem}.{disk_format}"
            for d in vm.disks
        ]
        xml = generate_libvirt_xml(
            vm, hw,
            disk_paths=placeholder_paths,
            disk_format=disk_format,
            bridge=bridge,
            preserve_mac=preserve_mac,
        )
        console.print(Panel(xml, title="Libvirt XML (preview)", border_style="dim"))
    else:
        placeholder_paths = [
            f"{Path(output_dir) / Path(d.path).stem}.{disk_format}"
            for d in vm.disks
        ]
        cmds = generate_proxmox_commands(
            vm, hw,
            vmid=vmid,
            disk_paths=placeholder_paths,
            storage=storage,
            bridge=bridge,
            preserve_mac=preserve_mac,
        )
        console.print(Panel(
            "\n".join(cmds),
            title="Proxmox Commands (preview)",
            border_style="dim",
        ))

    return True


def _execute_proxmox_commands(
    cmds: list[str],
    *,
    run_command: object = None,
) -> None:
    """Execute Proxmox qm commands sequentially.

    Each multi-line command (joined with backslash-newline) is flattened
    into a single line before execution.

    Args:
        cmds: List of qm command strings from the generator.
        run_command: Injectable callable for testing (default: subprocess.run).
    """
    if run_command is None:
        def run_command(cmd, **kwargs):
            return subprocess.run(cmd, **kwargs)

    for cmd in cmds:
        # Flatten multi-line commands (backslash continuations)
        flat_cmd = cmd.replace(" \\\n  ", " ")
        console.print(f"  [dim]$[/dim] {flat_cmd}")
        result = run_command(flat_cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            console.print(f"  [red]Error (exit {result.returncode}):[/red] {stderr}")
            sys.exit(1)
        if result.stdout.strip():
            console.print(f"  {result.stdout.strip()}")
        console.print("  [green]ok[/green]")


def _show_summary(vm, hw, target, bridge, converted_paths, vmid, preserve_mac):
    """Show the migration summary panel."""
    summary = Table(show_header=False, box=None, padding=(0, 1))
    summary.add_column(style="dim")
    summary.add_column()

    summary.add_row("VM Name:", vm.name)
    summary.add_row("Target:", target.upper())
    summary.add_row("Firmware:", "EFI (OVMF)" if hw.firmware_path else "BIOS")
    summary.add_row("Controller:", hw.controller)
    summary.add_row("NIC:", hw.nic)
    summary.add_row("Bridge:", bridge)

    if target == "proxmox":
        summary.add_row("VM ID:", str(vmid))

    for path in converted_paths:
        summary.add_row("Disk:", str(path))

    if preserve_mac and vm.nics:
        mac = vm.nics[0].mac_address
        if mac:
            summary.add_row("MAC:", mac)

    if hw.is_safe_mode:
        summary.add_row("Mode:", "Windows Safe (IDE + e1000)")

    console.print()
    console.print(Panel(summary, title="Migration Complete", border_style="green"))
    console.print()
