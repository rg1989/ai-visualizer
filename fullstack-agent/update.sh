#!/bin/bash
# This folder is one component of a single-repo stack mirror, so it does not
# update itself -- the whole stack moves together.
cd "$(dirname "$0")/.." || exit 1
exec git pull --ff-only
