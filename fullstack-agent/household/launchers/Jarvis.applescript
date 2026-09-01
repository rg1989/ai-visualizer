(* "AI Visualizer.app" — a real stay-open applet, not a script that fires and exits.
   It owns the agent: quitting it (Cmd-Q, Dock > Quit, menu) takes the whole
   process tree down and lets the iTerm window close with it. If you close
   the iTerm window instead, the app notices and quits itself, so the Dock
   never claims Jarvis is running when it is not.
   Build:  jarvisctl build   (osacompile alone drops the icon)  *)

property ctl : ""
property seen : false
property win : missing value

on ctlPath()
	if ctl is "" then ¬
		set ctl to (POSIX path of (path to home folder)) & "my-agent/launchers/jarvisctl"
	return quoted form of ctl
end ctlPath

on agentPid()
	try
		return (do shell script ctlPath() & " status") as integer
	on error
		return 0
	end try
end agentPid

on run
	ctlPath() -- resolve `ctl` before the iTerm block reads it
	if agentPid() > 0 then
		tell application "iTerm" to activate -- already up; just show it
		return
	end if
	set wasRunning to running of application "iTerm"
	tell application "iTerm"
		-- `launch`, not `activate`: activating a cold iTerm sends it an open
		-- event on top of its own startup. Neither stops it opening a window
		-- of its own -- that is its "open no windows at startup" preference,
		-- which is ours to work WITH, not to flip behind the owner's back.
		launch
		if wasRunning then
			-- iTerm was already up, so every window on screen is the owner's.
			-- Never touch those; take a new one.
			-- iTerm splits `command` itself and does NOT run it through a shell,
			-- so `quoted form of` (single quotes) would arrive literally. Hand it
			-- to sh with double quotes instead.
			set win to (create window with default profile command ("/bin/sh -c \"" & ctl & " run\""))
		else
			-- Cold start: iTerm opens exactly ONE window for itself. Creating
			-- ours beside it is the second window nobody asked for -- so wait
			-- for its window and run in that one instead.
			set win to missing value
			repeat 40 times
				if (count of windows) > 0 then exit repeat
				delay 0.05
			end repeat
			if (count of windows) > 0 then
				set win to current window
				-- `write text` DOES go through the shell, unlike `command`
				-- above, so here quoting is both possible and required.
				tell current session of win to write text (quoted form of ctl & " run")
			else
				set win to (create window with default profile command ("/bin/sh -c \"" & ctl & " run\""))
			end if
		end if
		activate
	end tell
end run

on idle
	-- only start watching once we have actually seen it alive, so a slow
	-- start does not quit the app before the agent writes its pid
	if agentPid() > 0 then
		set seen to true
	else if seen then
		quit
	end if
	return 2
end idle

on quit
	do shell script ctlPath() & " stop"
	-- iTerm keeps a window open after its command dies (it just renames it),
	-- which is the whole complaint. Close the one we opened.
	if win is not missing value then
		try
			tell application "iTerm" to close win
		end try
		set win to missing value
	end if
	set seen to false
	continue quit
end quit
