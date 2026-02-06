"""VMFree CLI — main entry point.

Provides commands for migrating VMware VMs to KVM/Proxmox,
inspecting VM configurations, and converting disk formats.
"""

import click

from vmfree import __version__


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
@click.option("--vmid", type=int, default=None, help="Proxmox VM ID.")
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
    click.echo(f"Migration from {source} to {target} is not yet implemented.")


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
