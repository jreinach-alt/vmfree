"""Tests for guest network configuration fixup.

All tests use mocked subprocess calls — no actual libguestfs required.
Tests verify interface name replacement across Netplan, ifcfg,
systemd-networkd, and NetworkManager configurations, udev rule
cleanup, and DHCP fallback creation.
"""

import subprocess

from vmfree.fixup.network import (
    IFCFG_DIR,
    KVM_DEFAULT_IFACE,
    NETPLAN_DIR,
    NETWORKD_DIR,
    NM_DIR,
    UDEV_NET_RULES,
    VMWARE_IFACE_NAMES,
    fix_guest_network,
    set_dhcp_fallback,
)


def _mock_run_success(cmd, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return subprocess.CompletedProcess(cmd, 0, "file1.yaml\nfile2.yaml", "")


def _mock_run_failure(cmd, **kwargs):
    """Mock subprocess.run that always fails."""
    return subprocess.CompletedProcess(cmd, 1, "", "failed")


def _mock_run_not_found(cmd, **kwargs):
    """Mock that raises FileNotFoundError."""
    raise FileNotFoundError("virt-customize not found")


class CommandTracker:
    """Track subprocess commands for assertions."""

    def __init__(self, returncode=0, stdout=""):
        self.commands: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, self.returncode, self.stdout, "",
        )


# ---------------------------------------------------------------------------
# fix_guest_network — success path
# ---------------------------------------------------------------------------

class TestFixGuestNetworkSuccess:
    """Test network fixup with successful commands."""

    def test_returns_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_guest_network(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_has_actions(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_guest_network(disk, run_command=_mock_run_success)
        assert len(result.actions) > 0

    def test_removes_udev_rules(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_guest_network(disk, run_command=_mock_run_success)
        udev_actions = [a for a in result.actions if "udev" in a]
        assert len(udev_actions) >= 1

    def test_detects_network_config(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_guest_network(disk, run_command=_mock_run_success)
        detect_actions = [
            a for a in result.actions if "Detected network config" in a
        ]
        assert len(detect_actions) == 1


class TestFixGuestNetworkErrors:
    """Test error handling in network fixup."""

    def test_disk_not_found(self):
        result = fix_guest_network(
            "/nonexistent/disk.qcow2",
            run_command=_mock_run_success,
        )
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_virt_customize_missing(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = fix_guest_network(disk, run_command=_mock_run_not_found)
        assert result.success is False


# ---------------------------------------------------------------------------
# Netplan fixup
# ---------------------------------------------------------------------------

class TestNetplanFixup:
    """Test Netplan YAML config fixup."""

    def test_detects_netplan(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_netplan(cmd, **kwargs):
            if "virt-ls" in cmd and NETPLAN_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "01-netcfg.yaml\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(disk, run_command=mock_netplan)
        assert any("netplan" in a.lower() for a in result.actions)

    def test_replaces_vmware_ifaces(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_netplan(cmd, **kwargs):
            if "virt-ls" in cmd and NETPLAN_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "01-netcfg.yaml\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(disk, run_command=mock_netplan)
        netplan_actions = [a for a in result.actions if "Netplan" in a]
        assert len(netplan_actions) >= 1

    def test_explicit_old_iface(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_netplan(cmd, **kwargs):
            if "virt-ls" in cmd and NETPLAN_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "01-netcfg.yaml\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(
            disk, old_iface="ens192", new_iface="ens3",
            run_command=mock_netplan,
        )
        actions_text = " ".join(result.actions)
        assert "ens192" in actions_text
        assert "ens3" in actions_text


# ---------------------------------------------------------------------------
# ifcfg fixup (RHEL/CentOS)
# ---------------------------------------------------------------------------

class TestIfcfgFixup:
    """Test ifcfg-style network script fixup."""

    def test_detects_ifcfg(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_ifcfg(cmd, **kwargs):
            if "virt-ls" in cmd and IFCFG_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "ifcfg-ens192\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(disk, run_command=mock_ifcfg)
        assert any("ifcfg" in a.lower() for a in result.actions)

    def test_renames_config_file(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        tracker = CommandTracker(stdout="ifcfg-ens192")

        def mock_ifcfg(cmd, **kwargs):
            tracker.commands.append(list(cmd))
            if "virt-ls" in cmd and IFCFG_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "ifcfg-ens192\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        fix_guest_network(
            disk, old_iface="ens192", run_command=mock_ifcfg,
        )
        mv_cmds = [
            cmd for cmd in tracker.commands
            if any("mv" in arg and "ifcfg-" in arg for arg in cmd)
        ]
        assert len(mv_cmds) >= 1


# ---------------------------------------------------------------------------
# systemd-networkd fixup
# ---------------------------------------------------------------------------

class TestNetworkdFixup:
    """Test systemd-networkd .network file fixup."""

    def test_detects_networkd(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_networkd(cmd, **kwargs):
            if "virt-ls" in cmd and NETWORKD_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "10-ens192.network\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(disk, run_command=mock_networkd)
        assert any("networkd" in a.lower() for a in result.actions)


# ---------------------------------------------------------------------------
# NetworkManager fixup
# ---------------------------------------------------------------------------

class TestNetworkManagerFixup:
    """Test NetworkManager connection file fixup."""

    def test_detects_nm(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_nm(cmd, **kwargs):
            if "virt-ls" in cmd and NM_DIR in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "ens192.nmconnection\n", "",
                )
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(disk, run_command=mock_nm)
        assert any("NetworkManager" in a for a in result.actions)


# ---------------------------------------------------------------------------
# No static config (DHCP)
# ---------------------------------------------------------------------------

class TestNoStaticConfig:
    """Test behavior when no static network config is found."""

    def test_dhcp_detected(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_no_config(cmd, **kwargs):
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = fix_guest_network(disk, run_command=mock_no_config)
        assert result.success is True
        assert any("DHCP" in a for a in result.actions)


# ---------------------------------------------------------------------------
# set_dhcp_fallback
# ---------------------------------------------------------------------------

class TestSetDhcpFallback:
    """Test DHCP fallback configuration."""

    def test_success(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = set_dhcp_fallback(disk, run_command=_mock_run_success)
        assert result.success is True

    def test_creates_config(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = set_dhcp_fallback(disk, run_command=_mock_run_success)
        assert any("DHCP fallback" in a for a in result.actions)

    def test_custom_iface(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        set_dhcp_fallback(disk, iface="eth0", run_command=tracker)
        # Should reference eth0 in the netplan config
        netplan_cmds = [
            cmd for cmd in tracker.commands
            if any("eth0" in arg for arg in cmd)
        ]
        assert len(netplan_cmds) >= 1

    def test_disk_not_found(self):
        result = set_dhcp_fallback(
            "/nonexistent/disk.qcow2",
            run_command=_mock_run_success,
        )
        assert result.success is False

    def test_virt_customize_not_found(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        result = set_dhcp_fallback(disk, run_command=_mock_run_not_found)
        assert result.success is False


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestCommandConstruction:
    """Test that correct virt-customize commands are built."""

    def test_udev_removal_command(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")
        tracker = CommandTracker()
        fix_guest_network(disk, run_command=tracker)
        udev_cmds = [
            cmd for cmd in tracker.commands
            if any(UDEV_NET_RULES in arg for arg in cmd)
        ]
        assert len(udev_cmds) >= 1

    def test_cleanup_state_commands(self, tmp_path):
        disk = tmp_path / "test.qcow2"
        disk.write_text("fake disk")

        def mock_no_config(cmd, **kwargs):
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        tracker_cmds = []

        def mock_track(cmd, **kwargs):
            tracker_cmds.append(list(cmd))
            if "virt-ls" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        fix_guest_network(disk, run_command=mock_track)
        cleanup_cmds = [
            cmd for cmd in tracker_cmds
            if any("NetworkManager" in arg and "rm -rf" in arg for arg in cmd)
        ]
        assert len(cleanup_cmds) >= 1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    """Verify the network fixup constants."""

    def test_vmware_iface_names(self):
        assert len(VMWARE_IFACE_NAMES) > 0
        assert "ens192" in VMWARE_IFACE_NAMES
        assert "ens160" in VMWARE_IFACE_NAMES

    def test_kvm_default_iface(self):
        assert KVM_DEFAULT_IFACE == "ens18"

    def test_config_dirs(self):
        assert NETPLAN_DIR == "/etc/netplan"
        assert IFCFG_DIR == "/etc/sysconfig/network-scripts"
        assert NETWORKD_DIR == "/etc/systemd/network"
        assert NM_DIR == "/etc/NetworkManager/system-connections"
