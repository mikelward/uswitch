// mswitch-helper: starts `mswitch switch` when a desk's USB switch arrives, when
// the user logs in, and when the Mac wakes (SPEC.md D15, D16). It decides
// nothing itself: every desk check is mswitch's (D10, D11), and mswitch runs one
// switch at a time, so a trigger arriving mid-run is never lost (D15).
//
// `mswitch install` compiles it, and the LaunchAgent that `install` and `learn`
// write runs it as:
//
//   mswitch-helper PROGRAM [--wake] [--usb VENDOR PRODUCT SERIAL LOCATION]...
//
// VENDOR and PRODUCT are four hex digits; SERIAL and LOCATION (0x14100000) are
// empty to match any, as in mswitch.conf (D1, D17).

import AppKit
import Foundation
import IOKit

struct Trigger {
    let vendor: Int
    let product: Int
    let serial: String
    let location: UInt32?
}

struct Arguments {
    let program: String
    let wake: Bool
    let triggers: [Trigger]
}

// To the log the LaunchAgent names, unbuffered, between the lines of the runs it starts.
func log(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardOutput.write(Data("\(stamp) mswitch-helper: \(message)\n".utf8))
}

func usage() -> Never {
    log("usage: mswitch-helper PROGRAM [--wake] [--usb VENDOR PRODUCT SERIAL LOCATION]...")
    exit(2)
}

func parse(_ argv: [String]) -> Arguments {
    var rest = argv
    guard !rest.isEmpty, rest[0].hasPrefix("/") else { usage() }
    let program = rest.removeFirst()
    var wake = false
    var triggers: [Trigger] = []
    while !rest.isEmpty {
        let flag = rest.removeFirst()
        switch flag {
        case "--wake":
            wake = true
        case "--usb":
            guard rest.count >= 4,
                  let vendor = Int(rest[0], radix: 16),
                  let product = Int(rest[1], radix: 16)
            else { usage() }
            var location: UInt32?
            if !rest[3].isEmpty {
                guard rest[3].hasPrefix("0x"), let value = UInt32(rest[3].dropFirst(2), radix: 16) else { usage() }
                location = value
            }
            triggers.append(Trigger(vendor: vendor, product: product, serial: rest[2], location: location))
            rest.removeFirst(4)
        default:
            usage()
        }
    }
    return Arguments(program: program, wake: wake, triggers: triggers)
}

let arguments = parse(Array(CommandLine.arguments.dropFirst()))

func runSwitch(_ why: String) {
    log("\(why): running \(arguments.program) switch")
    let process = Process()
    process.executableURL = URL(fileURLWithPath: arguments.program)
    process.arguments = ["switch"]
    process.terminationHandler = { finished in
        log("\(arguments.program) switch exited with status \(finished.terminationStatus)")
    }
    do {
        try process.run()
    } catch {
        log("can't run \(arguments.program): \(error)")
    }
}

func number(_ service: io_service_t, _ key: String) -> NSNumber? {
    IORegistryEntryCreateCFProperty(service, key as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue() as? NSNumber
}

func text(_ service: io_service_t, _ keys: [String]) -> String {
    for key in keys {
        let value = IORegistryEntryCreateCFProperty(service, key as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue()
        if let value = value as? String {
            return value.trimmingCharacters(in: .whitespaces)
        }
    }
    return ""
}

func matches(_ trigger: Trigger, _ service: io_service_t) -> Bool {
    guard number(service, "idVendor")?.intValue == trigger.vendor,
          number(service, "idProduct")?.intValue == trigger.product
    else { return false }
    if !trigger.serial.isEmpty && text(service, ["USB Serial Number", "kUSBSerialNumberString"]) != trigger.serial {
        return false
    }
    if let location = trigger.location {
        // A T2 Mac's internal bus reports its location ID sign-extended.
        guard let raw = number(service, "locationID"), UInt32(truncatingIfNeeded: raw.uint64Value) == location else {
            return false
        }
    }
    return true
}

// Takes every device the iterator holds, which also re-arms the notification.
// The devices already there when the helper starts are no arrival.
func arrived(_ iterator: io_iterator_t, armed: Bool) {
    var matched = false
    var service = IOIteratorNext(iterator)
    while service != 0 {
        if arguments.triggers.contains(where: { matches($0, service) }) {
            matched = true
        }
        IOObjectRelease(service)
        service = IOIteratorNext(iterator)
    }
    if matched && armed {
        runSwitch("a desk's USB switch arrived")
    }
}

if !arguments.triggers.isEmpty {
    guard let port = IONotificationPortCreate(mach_port_t(MACH_PORT_NULL)) else {
        log("can't create an IOKit notification port")
        exit(1)
    }
    CFRunLoopAddSource(CFRunLoopGetMain(), IONotificationPortGetRunLoopSource(port).takeUnretainedValue(), .defaultMode)
    var iterator: io_iterator_t = 0
    let result = IOServiceAddMatchingNotification(
        port, kIOFirstMatchNotification, IOServiceMatching("IOUSBHostDevice"),
        { _, found in arrived(found, armed: true) }, nil, &iterator)
    if result != KERN_SUCCESS {
        log("can't watch for USB devices: IOKit error \(result)")
        exit(1)
    }
    arrived(iterator, armed: false)
}

var wakeObserver: NSObjectProtocol?
if arguments.wake {
    wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
        forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
    ) { _ in runSwitch("the Mac woke") }
    runSwitch("logged in")
}

log("watching \(arguments.triggers.count) USB switch(es)\(arguments.wake ? " and wakes" : "")")
RunLoop.main.run()
