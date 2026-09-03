// facewin — the face in a window, without a browser around it.
//
// A face is a canvas app that needs a web engine, not a browser: no tabs,
// no extensions, no omnibox, no separate GPU and utility processes. This is
// WKWebView, the one macOS already ships, in a window. Measured on an
// M-series Mac against the board face: 116 MB total (23 MB app + 93 MB web
// content) versus a Chromium tab at 99-520 MB on top of ~720 MB of browser
// the tab cannot exist without. The win is not per-pixel efficiency, it is
// that nothing has to keep a browser open to see the face.
//
// Build once (macOS ships the compiler with the Command Line Tools):
//   swiftc -O bin/facewin.swift -o bin/facewin
// Run:
//   python3 server.py --no-open & bin/facewin http://127.0.0.1:8790/
//   bin/facewin http://127.0.0.1:8790/faces/board/ --overlay
//
// ponytail: no toolbar, no reload key, no state. Quit and relaunch is the
// reload. Add a key handler when you actually miss one.
import Cocoa
import WebKit

// facewin URL [--overlay [--idle SECONDS]] [--snapshot out.png [--after SECONDS]]
//   --snapshot renders the page, writes a PNG of the web view after
//   SECONDS (default 5) and exits -- a picture of the glass with no
//   screen-recording permission and no browser. It is how an agent, or a
//   test, can SEE a card instead of trusting a description of it.
//   --overlay puts the face OVER everything: a borderless, transparent
//   panel covering the main display's visible area, on every Space and
//   above full-screen apps. The URL gets ?overlay=1, which tells the face
//   to drop its wallpaper and chrome and keep the face and the glass.
//   It lives by one chord, LEFT Option + LEFT Command (the right-hand
//   pair is someone else's shortcut and never counts):
//     held    the page gets the mouse and the keys -- Opt+Cmd+Enter is the
//             talk key, Opt+Cmd+1 picks a mic mode -- while the app you
//             are in stays the active app. The chord is stripped before
//             the page sees a key, so "1" arrives as "1", not "¡".
//     tapped  shows the overlay.
//   The voice shows it too: listening, thinking, speaking, a new card,
//   and the settings screen holds it up, with the keyboard, as long as
//   it is open. --idle SECONDS (default 10) without any of that and it
//   fades out. "Stop listening" fades it AT ONCE (the voice line marks
//   the mic hushed) and only the chord or the menu shows it again until
//   she is addressed. A menu bar item is the sign it is alive while
//   faded: its glyph is the face's state (mic.slash while hushed), its
//   menu is Show, Settings, Quit Overlay.
//   No Dock icon in overlay mode. bin/overlay.sh wraps this.
let args = CommandLine.arguments
let overlay = args.contains("--overlay")
var url = URL(string: args.count > 1 && !args[1].hasPrefix("--")
              ? args[1] : "http://127.0.0.1:8790/")!
if overlay, var c = URLComponents(url: url, resolvingAgainstBaseURL: false) {
    c.queryItems = (c.queryItems ?? []) + [URLQueryItem(name: "overlay", value: "1")]
    url = c.url ?? url
}
func arg(_ flag: String) -> String? {
    args.firstIndex(of: flag).flatMap { i in i + 1 < args.count ? args[i + 1] : nil }
}
let snapPath = arg("--snapshot")
let snapAfter = arg("--after").flatMap(Double.init) ?? 5
let idle = arg("--idle").flatMap(Double.init) ?? 10
let app = NSApplication.shared
// --snapshot mode is a camera, not a window for a person: no Dock icon, no
// focus steal, and the window sits off every screen. WebKit still lays out
// and paints an off-screen window, which is all takeSnapshot needs.
app.setActivationPolicy(snapPath == nil && !overlay ? .regular : .accessory)
// Cmd-Q, the one menu item: the overlay has no window chrome to close.
let menu = NSMenu(), appItem = NSMenuItem(), appMenu = NSMenu()
appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)),
                keyEquivalent: "q")
appItem.submenu = appMenu
menu.addItem(appItem)
app.mainMenu = menu

// The overlay is a NON-ACTIVATING panel: it can take the keyboard while the
// app you are working in stays the active app, the way Spotlight does, so
// there is nothing to hand back on release except the keyboard itself.
// The window server treats a web view as one solid sheet (a click on a
// transparent pixel still lands here), which is why the mouse is only
// accepted while the chord is held.
// Left Option + left Command, and only those: the low byte of the modifier
// flags says which side a key is on (IOLLEvent.h: NX_DEVICELCMDKEYMASK 0x08,
// NX_DEVICELALTKEYMASK 0x20; the right-hand keys are 0x10 and 0x40).
let leftChord: UInt = 0x08 | 0x20
func chordHeld(_ flags: UInt) -> Bool { flags & leftChord == leftChord }

final class OverlayPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func sendEvent(_ e: NSEvent) {
        // left Opt+Cmd is the overlay's chord, not part of the shortcut
        if e.type == .keyDown || e.type == .keyUp,
           chordHeld(e.modifierFlags.rawValue),
           let k = NSEvent.keyEvent(
               with: e.type, location: e.locationInWindow,
               modifierFlags: e.modifierFlags.intersection(.deviceIndependentFlagsMask)
                   .subtracting([.option, .command]),
               timestamp: e.timestamp, windowNumber: e.windowNumber, context: nil,
               characters: e.charactersIgnoringModifiers ?? "",
               charactersIgnoringModifiers: e.charactersIgnoringModifiers ?? "",
               isARepeat: e.isARepeat, keyCode: e.keyCode) {
            super.sendEvent(k)
            return
        }
        super.sendEvent(e)
    }
}
let win: NSWindow = overlay
    ? OverlayPanel(contentRect: NSScreen.main?.visibleFrame
                       ?? NSRect(x: 0, y: 0, width: 1280, height: 800),
                   styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: false)
    : NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
               styleMask: [.titled, .closable, .resizable, .miniaturizable],
               backing: .buffered, defer: false)
win.title = "face"
let web = WKWebView(frame: win.contentView!.bounds,
                    configuration: WKWebViewConfiguration())
web.autoresizingMask = [.width, .height]
if overlay {
    win.level = .statusBar
    win.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary,
                              .stationary, .ignoresCycle]
    win.isOpaque = false
    win.backgroundColor = .clear
    win.hasShadow = false
    win.ignoresMouseEvents = true
    (win as? NSPanel)?.hidesOnDeactivate = false
    // the page's transparent body shows the desktop through the web view
    web.setValue(false, forKey: "drawsBackground")
    // ponytail: main display only, sized once. A second screen or a Dock
    // that moves gets nothing; a window per NSScreen.screens when it matters.
}
win.contentView!.addSubview(web)
web.load(URLRequest(url: url))
if snapPath == nil {
    if overlay { win.orderFrontRegardless() }        // no key, no focus steal
    else { win.center(); win.makeKeyAndOrderFront(nil) }
} else {
    win.setFrameOrigin(NSPoint(x: -20000, y: -20000))
    win.orderFrontRegardless()
}

// Overlay state lives at top level: the menu bar item's selectors need an
// NSObject to hang on, and that object has to reach the same state.
var grabbed = false, awake = true, menuOpen = false, hushed = false, lastRev = -1, barSt = ""
var awakeUntil = Date().addingTimeInterval(idle)
var bar: NSStatusItem? = nil
func fade(to a: CGFloat, over d: Double) {
    NSAnimationContext.runAnimationGroup { c in c.duration = d; win.animator().alphaValue = a }
}
func wake() {
    if !awake { fade(to: 1, over: 0.12); awake = true }
    awakeUntil = Date().addingTimeInterval(idle)
}
// Grabbed = the page has the mouse and the keys: while the chord is held,
// and while the settings screen is open, so it (and the boot-time mic
// picker, which is the same screen) can be driven without the chord.
func setGrab(_ on: Bool) {
    if on == grabbed { return }
    grabbed = on
    win.ignoresMouseEvents = !on
    if on { wake(); win.makeKeyAndOrderFront(nil) }
    else { win.orderOut(nil); win.orderFrontRegardless() }   // keyboard back to your app
}
// The menu bar item's glyph is the face's state -- the one sign the overlay
// is alive while it is faded out.
func barState(_ st: String) {
    guard st != barSt, let b = bar?.button else { return }
    barSt = st
    let sym = ["listening": "ear", "thinking": "hourglass", "speaking": "waveform",
               "hushed": "mic.slash"][st] ?? "circle.dotted"
    if let img = NSImage(systemSymbolName: sym, accessibilityDescription: "face: " + st) {
        img.isTemplate = true
        b.image = img
        b.title = ""
    } else {
        b.image = nil
        b.title = ["listening": "●", "thinking": "◐", "speaking": "◉", "hushed": "⊘"][st] ?? "◌"
    }
    b.toolTip = "AI Visualizer overlay: " + st
}
final class Bar: NSObject {
    @objc func show(_ s: Any?) { wake() }
    @objc func settings(_ s: Any?) { wake(); web.evaluateJavaScript("AV.settings&&AV.settings()") }
}
let barTarget = Bar()

if overlay && snapPath == nil {
    bar = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let m = NSMenu()
    m.addItem(withTitle: "Show", action: #selector(Bar.show(_:)), keyEquivalent: "").target = barTarget
    m.addItem(withTitle: "Settings…", action: #selector(Bar.settings(_:)), keyEquivalent: ",").target = barTarget
    m.addItem(NSMenuItem.separator())
    m.addItem(withTitle: "Quit Overlay", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    bar?.menu = m
    barState("idle")
    // The chord is polled: reading the modifier state needs no permission,
    // unlike a global key monitor. 30 ms is under a keystroke.
    Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
        // flagsState keeps the left/right bits; NSEvent's class-level flags may not
        let chord = chordHeld(UInt(CGEventSource.flagsState(.combinedSessionState).rawValue))
        setGrab(chord || menuOpen)
        if grabbed { awakeUntil = Date().addingTimeInterval(idle) }
        else if awake && Date() > awakeUntil { fade(to: 0, over: 0.8); awake = false }
    }
    // The voice: listening, thinking, speaking, or a new card is the agent
    // reaching for you. Asked of the page, which already knows. "Stop
    // listening" is the one voice event that HIDES: the mic line carries
    // hush (AV.mic, from the voice line's .voice_mic), the overlay fades at
    // once instead of --idle seconds later, and neither her "Stopped." nor
    // a card wakes it until the person comes back (talk key, wake word,
    // typing clear the hush). The chord and the menu still show it on demand.
    Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in
        web.evaluateJavaScript("[String(AV.state),Number((AV.glass&&AV.glass.rev)||0),!!document.getElementById('av-mode-picker'),!!(AV.mic&&AV.mic.hush)]") { r, _ in
            guard let a = r as? [Any], a.count == 4, let st = a[0] as? String else { return }
            menuOpen = (a[2] as? Bool) ?? false
            let hush = (a[3] as? Bool) ?? false
            let rev = (a[1] as? NSNumber)?.intValue ?? 0
            let newCard = lastRev >= 0 && rev != lastRev
            lastRev = rev
            if hush && !hushed { awakeUntil = Date() }     // the chord timer fades it now
            hushed = hush
            if (!hush && (st != "idle" || newCard)) || menuOpen { wake() }
            barState(hush ? "hushed" : st)
        }
    }
}

// Closing the window quits: this is one window, not a document app.
final class Quit: NSObject, NSWindowDelegate {
    func windowWillClose(_ n: Notification) { NSApplication.shared.terminate(nil) }
}
let quit = Quit()
win.delegate = quit

if let path = snapPath {
    DispatchQueue.main.asyncAfter(deadline: .now() + snapAfter) {
        web.takeSnapshot(with: nil) { image, error in
            guard let image = image, let tiff = image.tiffRepresentation,
                  let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:]) else {
                FileHandle.standardError.write(
                    "facewin: snapshot failed: \(error.map { "\($0)" } ?? "no image")\n".data(using: .utf8)!)
                exit(2)
            }
            do { try png.write(to: URL(fileURLWithPath: path)) } catch { exit(3) }
            exit(0)
        }
    }
}
if snapPath == nil && !overlay { app.activate(ignoringOtherApps: true) }
app.run()
