// OCR local con el reconocimiento de texto de macOS (Vision). Nada sale de la Mac.
// Uso: ocr <archivo.pdf|imagen> [--json]   Imprime el texto (una página por bloque, separadas por \f).
// Con --json imprime, por página, cada renglón con su caja (para resaltar en el original).
import AppKit
import PDFKit
import Vision

func cgImage(from url: URL, page: Int) -> (CGImage, CGSize)? {
    if url.pathExtension.lowercased() == "pdf" {
        guard let doc = PDFDocument(url: url), let pg = doc.page(at: page) else { return nil }
        let box = pg.bounds(for: .mediaBox)
        let scale: CGFloat = 2600 / max(box.width, box.height)  // ~ 300 dpi en una hoja carta
        let size = CGSize(width: box.width * scale, height: box.height * scale)
        let img = pg.thumbnail(of: size, for: .mediaBox)
        var rect = CGRect(origin: .zero, size: img.size)
        guard let cg = img.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { return nil }
        return (cg, box.size)
    }
    guard let img = NSImage(contentsOf: url) else { return nil }
    var rect = CGRect(origin: .zero, size: img.size)
    guard let cg = img.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { return nil }
    return (cg, img.size)
}

func pageCount(_ url: URL) -> Int {
    url.pathExtension.lowercased() == "pdf" ? (PDFDocument(url: url)?.pageCount ?? 0) : 1
}

struct Line { let text: String; let x: Double; let y: Double; let w: Double; let h: Double; let conf: Float }

func recognize(_ cg: CGImage) -> [Line] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["es-MX", "es-ES", "en-US"]
    req.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    let obs = (req.results ?? [])
    // Orden de lectura: de arriba abajo; renglones a la misma altura, de izquierda a derecha.
    let sorted = obs.sorted { a, b in
        let dy = a.boundingBox.midY - b.boundingBox.midY
        if abs(dy) > 0.008 { return dy > 0 }
        return a.boundingBox.minX < b.boundingBox.minX
    }
    return sorted.compactMap { o in
        guard let c = o.topCandidates(1).first else { return nil }
        let b = o.boundingBox
        return Line(text: c.string, x: b.minX, y: 1 - b.maxY, w: b.width, h: b.height, conf: c.confidence)
    }
}

guard CommandLine.arguments.count > 1 else { FileHandle.standardError.write(Data("Uso: ocr <archivo> [--json]\n".utf8)); exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let asJSON = CommandLine.arguments.contains("--json")
var pages: [[Line]] = []
for p in 0..<max(1, pageCount(url)) {
    guard let (cg, _) = cgImage(from: url, page: p) else { pages.append([]); continue }
    pages.append(recognize(cg))
}
if asJSON {
    let out = pages.enumerated().map { (i, ls) in
        ["page": i + 1, "lines": ls.map { ["text": $0.text, "x": $0.x, "y": $0.y, "w": $0.w, "h": $0.h, "conf": Double($0.conf)] }] as [String: Any]
    }
    let data = try JSONSerialization.data(withJSONObject: out, options: [])
    print(String(data: data, encoding: .utf8)!)
} else {
    print(pages.map { $0.map(\.text).joined(separator: "\n") }.joined(separator: "\n\u{0C}\n"))
}
