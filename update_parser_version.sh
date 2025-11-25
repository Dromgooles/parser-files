#!/bin/bash
#
# Update parser_version.json with new checksums and version
# Usage: ./update_parser_version.sh [new_version]
#
# If no version is provided, it will increment the patch version automatically
#
# NOTE: Copy this script to ~/Developer/dromgooles/parser-files and run it from there

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="$SCRIPT_DIR/parser_version.json"

# Get current version from existing file or default to 1.0.0
CURRENT_VERSION="1.0.0"
if [ -f "$VERSION_FILE" ]; then
    CURRENT_VERSION=$(grep '"version":' "$VERSION_FILE" | sed 's/.*"version": "\(.*\)".*/\1/')
fi

# Use provided version or auto-increment patch version
if [ -n "$1" ]; then
    NEW_VERSION="$1"
else
    # Auto-increment patch version (e.g., 1.0.0 -> 1.0.1)
    IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
    PATCH=$((PATCH + 1))
    NEW_VERSION="$MAJOR.$MINOR.$PATCH"
fi

echo "📦 Updating parser version from $CURRENT_VERSION to $NEW_VERSION"

# Calculate checksums and sizes
PARSE_SHA=$(shasum -a 256 "$SCRIPT_DIR/parse.py" | awk '{print $1}')
CUSTOM_SHA=$(shasum -a 256 "$SCRIPT_DIR/custom_parsers.py" | awk '{print $1}')
PARSE_SIZE=$(wc -c < "$SCRIPT_DIR/parse.py" | tr -d ' ')
CUSTOM_SIZE=$(wc -c < "$SCRIPT_DIR/custom_parsers.py" | tr -d ' ')
UPDATED=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Create version file
cat > "$VERSION_FILE" << EOF
{
  "version": "$NEW_VERSION",
  "updated": "$UPDATED",
  "files": {
    "parse.py": {
      "sha256": "$PARSE_SHA",
      "size": $PARSE_SIZE
    },
    "custom_parsers.py": {
      "sha256": "$CUSTOM_SHA",
      "size": $CUSTOM_SIZE
    }
  },
  "minAppVersion": "1.0.0"
}
EOF

echo "✅ Version file updated:"
cat "$VERSION_FILE"

echo ""
echo "📝 Next steps:"
echo "1. Commit the updated parser files:"
echo "   git add parse.py custom_parsers.py parser_version.json"
echo "   git commit -m \"Update parsers to version $NEW_VERSION\""
echo ""
echo "2. Push to GitHub main branch:"
echo "   git push origin main"
echo ""
echo "3. App will auto-update parsers on next launch!"
