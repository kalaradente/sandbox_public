// macOS Vision OCR. Usage: swift ocr.swift img1.jpg img2.jpg ...
// Also detects faces. Prints one JSON line per image: {"img":..., "lines":[{"text","conf","x","y","w","h"}]} (box normalised, origin top-left)
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    var out: [[String: Any]] = []
    var faces: [[String: Any]] = []
    if let img = NSImage(contentsOfFile: path),
       let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = true
        let freq = VNDetectFaceRectanglesRequest()
        try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req, freq])
        for f in freq.results ?? [] {
            let b = f.boundingBox
            faces.append(["x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height, "conf": f.confidence])
        }
        for o in req.results ?? [] {
            guard let c = o.topCandidates(1).first else { continue }
            let b = o.boundingBox
            out.append(["text": c.string, "conf": c.confidence,
                        "x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height])
        }
    }
    let d = try! JSONSerialization.data(withJSONObject: ["img": path, "lines": out, "faces": faces])
    print(String(data: d, encoding: .utf8)!)
    fflush(stdout)
}
