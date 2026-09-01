@echo off
rem One component of a single-repo stack mirror: the whole
rem stack updates together.
cd /d "%~dp0.."
git pull --ff-only
