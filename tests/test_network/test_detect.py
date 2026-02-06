"""Tests for network bridge auto-detection.

Uses a mocked /sys/class/net/ filesystem to simulate different host
configurations without requiring root or real network interfaces.
"""

from vmfree.network.detect import (
    DEFAULT_BRIDGES,
    detect_bridge,
    find_bridges,
)


def _make_sysfs(tmp_path, interfaces):
    """Create a fake /sys/class/net/ tree.

    Args:
        tmp_path: pytest tmp_path fixture.
        interfaces: Dict of {name: is_bridge}.

    Returns:
        Path to the fake sysfs net directory.
    """
    sysfs = tmp_path / "sys" / "class" / "net"
    sysfs.mkdir(parents=True)
    for name, is_bridge in interfaces.items():
        iface_dir = sysfs / name
        iface_dir.mkdir()
        if is_bridge:
            (iface_dir / "bridge").mkdir()
    return sysfs


def _make_route_reader(lines):
    """Create a route_reader callable returning given lines."""
    def reader():
        return lines
    return reader


# ---------------------------------------------------------------------------
# find_bridges
# ---------------------------------------------------------------------------

class TestFindBridges:
    """Test bridge discovery from sysfs."""

    def test_single_bridge(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "eth0": False})
        assert find_bridges(sysfs) == ["br0"]

    def test_multiple_bridges(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"vmbr0": True, "br0": True, "eth0": False})
        result = find_bridges(sysfs)
        assert set(result) == {"br0", "vmbr0"}
        assert result == sorted(result)

    def test_no_bridges(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"eth0": False, "wlan0": False})
        assert find_bridges(sysfs) == []

    def test_nonexistent_sysfs(self, tmp_path):
        assert find_bridges(tmp_path / "nonexistent") == []

    def test_all_bridges(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "br1": True, "virbr0": True})
        assert len(find_bridges(sysfs)) == 3


# ---------------------------------------------------------------------------
# detect_bridge
# ---------------------------------------------------------------------------

class TestDetectBridge:
    """Test bridge auto-detection logic."""

    def test_single_bridge_selected(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "eth0": False})
        result = detect_bridge(sysfs_path=sysfs)
        assert result == "br0"

    def test_no_bridges_default_kvm(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"eth0": False})
        result = detect_bridge(target="kvm", sysfs_path=sysfs)
        assert result == DEFAULT_BRIDGES["kvm"]

    def test_no_bridges_default_proxmox(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"eth0": False})
        result = detect_bridge(target="proxmox", sysfs_path=sysfs)
        assert result == "vmbr0"

    def test_multiple_bridges_prefer_default_route(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "vmbr0": True, "eth0": False})
        route_reader = _make_route_reader([
            "vmbr0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0",
        ])
        result = detect_bridge(sysfs_path=sysfs, route_reader=route_reader)
        assert result == "vmbr0"

    def test_multiple_bridges_default_route_on_br0(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "br1": True})
        route_reader = _make_route_reader([
            "br0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0",
        ])
        result = detect_bridge(sysfs_path=sysfs, route_reader=route_reader)
        assert result == "br0"

    def test_multiple_bridges_no_default_route_prefer_vmbr0(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "vmbr0": True})
        route_reader = _make_route_reader([])
        result = detect_bridge(sysfs_path=sysfs, route_reader=route_reader)
        assert result == "vmbr0"

    def test_multiple_bridges_no_match_prefer_br0(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "custom-br": True})
        route_reader = _make_route_reader([
            "eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0",
        ])
        result = detect_bridge(sysfs_path=sysfs, route_reader=route_reader)
        assert result == "br0"

    def test_multiple_bridges_no_known_names(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"custom1": True, "custom2": True})
        route_reader = _make_route_reader([])
        result = detect_bridge(sysfs_path=sysfs, route_reader=route_reader)
        # Should return first sorted
        assert result == "custom1"

    def test_route_reader_exception(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"br0": True, "br1": True})

        def broken_reader():
            raise OSError("no route")

        result = detect_bridge(sysfs_path=sysfs, route_reader=broken_reader)
        # Should fall back to preferred name
        assert result == "br0"

    def test_proxmox_single_vmbr0(self, tmp_path):
        sysfs = _make_sysfs(tmp_path, {"vmbr0": True})
        result = detect_bridge(target="proxmox", sysfs_path=sysfs)
        assert result == "vmbr0"


# ---------------------------------------------------------------------------
# Default bridges table
# ---------------------------------------------------------------------------

class TestDefaults:
    """Test the default bridge table."""

    def test_kvm_default(self):
        assert DEFAULT_BRIDGES["kvm"] == "virbr0"

    def test_proxmox_default(self):
        assert DEFAULT_BRIDGES["proxmox"] == "vmbr0"
