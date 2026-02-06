#!/bin/bash
# VMFree — Repository Scaffolding Script
# Run this after cloning to create the full directory structure.
# Usage: bash scripts/init-structure.sh

set -e

echo "🔓 VMFree — Setting up project structure..."

# Source package directories
dirs=(
    "vmfree"
    "vmfree/parsers"
    "vmfree/converter"
    "vmfree/mapper"
    "vmfree/generators"
    "vmfree/network"
    "vmfree/fixup"
    "vmfree/utils"
    "tests"
    "tests/fixtures"
    "tests/test_parsers"
    "tests/test_mapper"
    "tests/test_generators"
    "docs"
    "scripts"
)

for dir in "${dirs[@]}"; do
    mkdir -p "$dir"
    echo "  📁 $dir/"
done

# Create __init__.py for all Python packages
python_packages=(
    "vmfree"
    "vmfree/parsers"
    "vmfree/converter"
    "vmfree/mapper"
    "vmfree/generators"
    "vmfree/network"
    "vmfree/fixup"
    "vmfree/utils"
    "tests"
    "tests/test_parsers"
    "tests/test_mapper"
    "tests/test_generators"
)

for pkg in "${python_packages[@]}"; do
    if [ ! -f "$pkg/__init__.py" ]; then
        touch "$pkg/__init__.py"
        echo "  🐍 $pkg/__init__.py"
    fi
done

echo ""
echo "✅ Structure ready. Next steps:"
echo "   1. pip install -e '.[dev]'"
echo "   2. Open Claude Code and point it at this repo"
echo "   3. Tell it: 'Read CLAUDE.md, then start Sprint 1'"
echo ""
echo "Let's change the world. 🚀"
