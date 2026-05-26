#!/usr/bin/env bash
# Rewrite a course repository's git history to drop every *.http-cassette.yaml
# blob, then restore the current cassette set at the new tip so the working
# tree at HEAD remains byte-identical to the source repository's master.
#
# This is destructive: every commit SHA on the rewritten branch is new, and
# any clone of the old history must be re-cloned (or hard-reset) after the
# cutover. Do NOT run this against your live working repo; run it against a
# fresh clone in a sandbox, verify the resulting tip tree matches the live
# master tree, then decide whether to adopt.
#
# Requires git-filter-repo (https://github.com/newren/git-filter-repo).
#
# Usage:
#   scripts/rewrite_strip_cassette_history.sh <source_repo> <sandbox_dir>
#
# Example:
#   scripts/rewrite_strip_cassette_history.sh \
#       /path/to/PythonCourses \
#       /tmp/cassette-rewrite-experiment
#
# After it finishes:
#   * <sandbox_dir>/PythonCourses-rewrite/      <- rewritten repo (DO NOT push)
#   * <sandbox_dir>/cassette-stash/             <- cassette snapshot for re-add
#   * Prints the tip-tree comparison so you can confirm content parity.

set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <source_repo> <sandbox_dir>" >&2
    exit 2
fi

SRC="$1"
SANDBOX="$2"

if [[ ! -d "$SRC/.git" ]]; then
    echo "error: $SRC is not a git repository" >&2
    exit 2
fi

if ! command -v git-filter-repo >/dev/null 2>&1; then
    echo "error: git-filter-repo not on PATH" >&2
    echo "install: https://github.com/newren/git-filter-repo" >&2
    exit 2
fi

mkdir -p "$SANDBOX"
SRC_NAME=$(basename "$(realpath "$SRC")")
REWRITE_DIR="$SANDBOX/$SRC_NAME-rewrite"
STASH_DIR="$SANDBOX/cassette-stash"

rm -rf "$REWRITE_DIR" "$STASH_DIR"

echo "=== Cloning $SRC -> $REWRITE_DIR"
git clone "$SRC" "$REWRITE_DIR"

cd "$REWRITE_DIR"
git checkout master

echo "=== Stashing current cassettes -> $STASH_DIR"
mkdir -p "$STASH_DIR"
find slides -name '*.http-cassette.yaml' -type f | while read -r f; do
    mkdir -p "$STASH_DIR/$(dirname "$f")"
    cp "$f" "$STASH_DIR/$f"
done
n_stashed=$(find "$STASH_DIR" -name '*.http-cassette.yaml' | wc -l)
echo "stashed $n_stashed cassettes"

echo "=== Pre-rewrite stats"
pre_size=$(du -sh .git | awk '{print $1}')
pre_commits=$(git rev-list --count HEAD)
pre_blobs=$(git rev-list --objects --all | grep -c '\.http-cassette\.yaml' || true)
echo "  .git size:            $pre_size"
echo "  commit count:         $pre_commits"
echo "  cassette blobs:       $pre_blobs"

echo "=== Running git-filter-repo"
git filter-repo --path-glob '*.http-cassette.yaml' --invert-paths --force

echo "=== Restoring cassettes"
find "$STASH_DIR" -name '*.http-cassette.yaml' | while read -r src; do
    rel="${src#$STASH_DIR/}"
    mkdir -p "$(dirname "$rel")"
    cp "$src" "$rel"
done
git add slides/
git -c user.email=experiment@local -c user.name=experiment \
    commit -m "Restore HTTP-replay cassettes (history rewrite)"

echo "=== Post-rewrite stats"
post_size=$(du -sh .git | awk '{print $1}')
post_commits=$(git rev-list --count HEAD)
post_blobs=$(git rev-list --objects --all | grep -c '\.http-cassette\.yaml' || true)
echo "  .git size:            $post_size  (was $pre_size)"
echo "  commit count:         $post_commits  (was $pre_commits)"
echo "  cassette blobs:       $post_blobs  (was $pre_blobs)"

echo "=== Verifying tip tree matches live master"
rewritten_tree=$(git rev-parse HEAD^{tree})
live_tree=$(git -C "$SRC" rev-parse master^{tree})
echo "  rewritten tip tree:   $rewritten_tree"
echo "  live master tree:     $live_tree"
if [[ "$rewritten_tree" = "$live_tree" ]]; then
    echo "  MATCH: working tree at new tip is byte-identical to live master"
    exit 0
else
    echo "  MISMATCH: the rewritten tip diverges from live master — investigate"
    echo "  diff via:  git diff $rewritten_tree $live_tree"
    exit 1
fi
