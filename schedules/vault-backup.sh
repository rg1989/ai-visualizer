#!/bin/bash
# Hourly snapshot of the memory vault to its private repo.
#
# ponytail: this exists because the writer is an AI, not because disks
# fail. Git is the undo button for a note it rewrote or dropped; the
# offsite copy is the bonus. Silent when nothing changed, so the log is
# only ever real edits.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
V="$HOME/ai-visualizer-obsidian-vault"
cd "$V" || exit 0

# nothing staged, nothing modified, nothing new -> say nothing
if git diff --quiet && git diff --cached --quiet \
   && [ -z "$(git ls-files -o --exclude-standard)" ]; then
  exit 0
fi

git add -A
git -c user.name="vault-backup" -c user.email="vault-backup@localhost" \
    commit -q -m "vault: $(date '+%Y-%m-%d %H:%M')" || exit 0
# Offline is not an error: the next run pushes this commit and the next.
git push -q origin main 2>/dev/null \
  && echo "$(date '+%F %T') pushed" \
  || echo "$(date '+%F %T') committed, push deferred"
