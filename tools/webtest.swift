// Abre una página en WebKit real, ejecuta pasos y reporta errores de JavaScript. Uso: webtest pasos.json
import AppKit
import WebKit

final class Log: NSObject, WKScriptMessageHandler {
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        print("  [navegador] \(m.body)"); fflush(stdout)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let cfg = WKWebViewConfiguration()
cfg.userContentController.add(Log(), name: "log")
let hook = """
window.addEventListener('error', e => webkit.messageHandlers.log.postMessage('ERROR: ' + e.message + ' @' + (e.filename||'').split('/').pop() + ':' + e.lineno));
window.addEventListener('unhandledrejection', e => webkit.messageHandlers.log.postMessage('PROMESA RECHAZADA: ' + ((e.reason && (e.reason.stack || e.reason.message)) || e.reason)));
const __ce = console.error; console.error = (...a) => { webkit.messageHandlers.log.postMessage('console.error: ' + a.join(' ')); __ce(...a); };
"""
cfg.userContentController.addUserScript(WKUserScript(source: hook, injectionTime: .atDocumentStart, forMainFrameOnly: true))
let size = NSRect(x: 0, y: 0, width: 1280, height: 1500)
let web = WKWebView(frame: size, configuration: cfg)
let window = NSWindow(contentRect: size, styleMask: [.titled], backing: .buffered, defer: false)
window.contentView = web
window.orderBack(nil)

func spin(_ seconds: Double) {
    let end = Date().addingTimeInterval(seconds)
    while Date() < end { RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02)) }
}

func evalJS(_ js: String, timeout: Double = 20) -> String {
    // Todo se envuelve en una promesa para poder esperar fetch y renderizados.
    let wrapped = "window.__res = undefined; (async () => { try { return await (async () => { \(js) })(); } catch (e) { return 'EXCEPCIÓN: ' + (e && e.stack || e); } })().then(r => { window.__res = (r === undefined ? '' : (typeof r === 'string' ? r : JSON.stringify(r))); });"
    web.evaluateJavaScript(wrapped) { _, _ in }
    let end = Date().addingTimeInterval(timeout)
    while Date() < end {
        var out: String?
        var done = false
        web.evaluateJavaScript("window.__res") { v, _ in out = v as? String; done = true }
        while !done { RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01)) }
        if let o = out { return o }
        spin(0.05)
    }
    return "(tiempo agotado)"
}

func shot(_ path: String) {
    var done = false
    let conf = WKSnapshotConfiguration()
    web.takeSnapshot(with: conf) { image, _ in
        if let image = image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
           let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: path))
        }
        done = true
    }
    while !done { RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02)) }
}

final class Nav: NSObject, WKNavigationDelegate {
    var finished = false
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { finished = true }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { print("  [navegador] falló la carga: \(e.localizedDescription)"); finished = true }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { print("  [navegador] no se pudo abrir: \(e.localizedDescription)"); finished = true }
}
let nav = Nav()
web.navigationDelegate = nav

guard CommandLine.arguments.count > 1,
      let data = FileManager.default.contents(atPath: CommandLine.arguments[1]),
      let steps = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
    print("Uso: webtest pasos.json"); exit(2)
}
for (i, step) in steps.enumerated() {
    if let url = step["load"] as? String {
        nav.finished = false
        web.load(URLRequest(url: URL(string: url)!))
        let end = Date().addingTimeInterval(20)
        while !nav.finished && Date() < end { spin(0.05) }
        print("[\(i)] cargó \(url)")
    }
    if let w = step["wait"] as? Double { spin(w) }
    if let js = step["eval"] as? String {
        let r = evalJS(js)
        print("[\(i)] \(step["say"] as? String ?? "eval"): \(r)")
    }
    if let p = step["shot"] as? String { shot(p); print("[\(i)] captura: \(p)") }
    fflush(stdout)
}
exit(0)
