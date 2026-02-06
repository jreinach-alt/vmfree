"""VMFree CLI — main entry point.

Provides commands for migrating VMware VMs to KVM/Proxmox,
inspecting VM configurations, and converting disk formats.
Connects the full 7-stage pipeline with Rich terminal UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vmfree import __version__
from vmfree.converter.disk import convert_disk
from vmfree.generators.libvirt import generate_libvirt_xml
from vmfree.generators.proxmox import generate_proxmox_commands
from vmfree.mapper.hardware import map_hardware
from vmfree.network.detect import detect_bridge
from vmfree.parsers.vmx import parse_vmx_file

console = Console()


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
@click.option("--output", type=click.Path(), default=".",
              help="Output directory for converted files.")
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
@click.option("-v", "--verbose", is_flag=True, help="Detailed output.")
def migrate(source, target, output, bridge, storage, vmid, disk_format,
            no_fixup, windows_safe, preserve_mac, dry_run, verbose):
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
        verbose=verbose,
    )


@main.command()
@click.argument("source", type=click.Path(exists=True))
def inspect(source):
    """Analyze a VMware VM without migrating."""
    click.echo(f"Inspect for {source} is not yet implemented.")


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
def validate(source):
    """Run pre-flight checks on a VMware VM."""
    click.echo(f"Validate for {source} is not yet implemented.")


# ---------------------------------------------------------------------------
# Migration pipeline
# ---------------------------------------------------------------------------

def _run_migration(
    *,
    source: str,
    target: str,
    output_dir: str,
    bridge: str | None,
    storage: str,
    vmid: int,
    disk_format: str,
    windows_safe: bool,
    preserve_mac: bool,
    dry_run: bool,
    verbose: bool,
    run_command: object = None,
) -> bool:
    """Execute the full migration pipeline.

    Returns True on success, False on failure.
    """
    console.print(f"\n  [bold]VMFree v{__version__}[/bold] — VMware to KVM Migration Tool\n")

    # -- Stage 1: Parse input --
    source_path = Path(source)
    try:
        vm = parse_vmx_file(source_path)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"  [red]Error:[/red] {e}")
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
