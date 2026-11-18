// Tiny background helper for Mac Sysmon. macOS only reveals the Wi-Fi name (SSID) to an app
// that has Location Services permission, which a bare Python script cannot hold. This signed
// app bundle holds it, and writes the current SSID to a small JSON file the monitor reads.
// It does not read, store or send the location itself.
import Cocoa
import CoreLocation
import CoreWLAN

final class Helper: NSObject, CLLocationManagerDelegate {
    let lm = CLLocationManager()
    let outPath: String

    init(outPath: String) {
        self.outPath = outPath
        super.init()
        lm.delegate = self
    }

    func start() {
        lm.requestWhenInUseAuthorization()
        Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.write() }
        write()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) { write() }

    func write() {
        let ssid = CWWiFiClient.shared().interface()?.ssid() ?? ""
        let doc: [String: Any] = ["ssid": ssid, "auth": Int(lm.authorizationStatus.rawValue),
                                  "ts": Date().timeIntervalSince1970]
        guard let data = try? JSONSerialization.data(withJSONObject: doc) else { return }
        try? data.write(to: URL(fileURLWithPath: outPath), options: .atomic)
    }
}

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1]
    : NSHomeDirectory() + "/Library/Application Support/mac-sysmon/ssid.json"
try? FileManager.default.createDirectory(
    atPath: (out as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let helper = Helper(outPath: out)
helper.start()
app.run()
