// vision_track: where the people are in a video, frame by frame, with Apple's Vision framework (built into macOS; no models to download).
// Used by the doodle effects (_scripts/doodles.py) to draw ON a person: trace them, fill them, put a face over theirs, find their hands.
//   vision_track <video> <outdir> [--every N] [--mask-width W] [--masks-only] [--main-person]
// For every Nth frame (default 1): outdir/mask_<frame>.png, the person mask (8-bit grey, W wide, default 640), and one line of
// outdir/track.jsonl: {"n": frame, "t": seconds, "faces": [{"box": [x, y, w, h], "pts": {region: [[x, y], ...]}}],
//                      "pose": [{joint: [x, y, confidence]}]}  -- every coordinate a fraction of the frame, top-left origin.
// --masks-only: the person masks alone (for every frame: outlines that stay on a moving body), no faces, pose or jsonl lines.
// --main-person: the mask holds the biggest person only (Vision tells people apart: someone walking by behind them, even touching
//   their outline, is left out). A frame where Vision finds no separate people keeps the plain mask. Each frame with anyone else
//   in it also gets others_<frame>.png, everyone but the biggest person (to cut them out of a plain mask, which is surer of hands).
//   Needs macOS 14 or newer (that is where Vision learned to tell people apart): on an older Mac this one option says so and stops;
//   everything else builds and runs there as before.
// --saliency: each line also gets "salient": [[x, y, w, h], ...], where the eye goes (Lab_Render's framing on a "subject" with no face).
// Built by doodles.py the first time it's needed (swiftc -O -o _scripts/vision_track _scripts/vision_track.swift).
import Foundation
import AVFoundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
if args.count < 3 || args.contains("--help") {
    print("usage: vision_track <video> <outdir> [--every N] [--mask-width W] [--masks-only] [--main-person]"); exit(args.contains("--help") ? 0 : 2)
}
func opt(_ name: String, _ def: Int) -> Int {
    if let i = args.firstIndex(of: name), i + 1 < args.count, let v = Int(args[i + 1]) { return v }
    return def
}
let every = max(1, opt("--every", 1)), maskW = opt("--mask-width", 640), masksOnly = args.contains("--masks-only"), saliency = args.contains("--saliency"), mainPerson = args.contains("--main-person")
if mainPerson, #unavailable(macOS 14.0) {
    FileHandle.standardError.write("--main-person needs macOS 14 or newer: this Mac is on an older version. Everything else works here.\n".data(using: .utf8)!); exit(2)
}
let url = URL(fileURLWithPath: args[1]), out = URL(fileURLWithPath: args[2])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

let asset = AVURLAsset(url: url)
let sema = DispatchSemaphore(value: 0)
var track: AVAssetTrack? = nil
asset.loadTracks(withMediaType: .video) { t, _ in track = t?.first; sema.signal() }
sema.wait()
guard let vt = track else { print("no video track"); exit(1) }
let reader = try AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: vt, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
output.alwaysCopiesSampleData = false
reader.add(output)
reader.startReading()

let ci = CIContext(options: [.useSoftwareRenderer: false])
let outName = masksOnly ? "masks.jsonl" : "track.jsonl"
FileManager.default.createFile(atPath: out.appendingPathComponent(outName).path, contents: nil)
let jsonl = try FileHandle(forWritingTo: out.appendingPathComponent(outName))
func r4(_ v: CGFloat) -> Double { (Double(v) * 10000).rounded() / 10000 }

var n = 0
while let sb = output.copyNextSampleBuffer() {
    defer { n += 1 }
    if n % every != 0 { continue }
    guard let px = CMSampleBufferGetImageBuffer(sb) else { continue }
    let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb))
    let W = CGFloat(CVPixelBufferGetWidth(px)), H = CGFloat(CVPixelBufferGetHeight(px))
    let seg = VNGeneratePersonSegmentationRequest(); seg.qualityLevel = .accurate; seg.outputPixelFormat = kCVPixelFormatType_OneComponent8
    let faces = VNDetectFaceLandmarksRequest()
    let pose = VNDetectHumanBodyPoseRequest()
    let sal = VNGenerateAttentionBasedSaliencyImageRequest()
    let h = VNImageRequestHandler(cvPixelBuffer: px, options: [:])
    try? h.perform(masksOnly ? [seg] : saliency ? [seg, faces, pose, sal] : [seg, faces, pose])
    // the mask, scaled to maskW wide
    var mbuf: CVPixelBuffer? = seg.results?.first?.pixelBuffer
    if mainPerson, #available(macOS 14.0, *) {   // the biggest of the people Vision tells apart (its label picture: one value per person), as a mask of the whole frame
        let inst = VNGeneratePersonInstanceMaskRequest()
        try? h.perform([inst])
        if let o = inst.results?.first, o.allInstances.count > 0 {
            let lb = o.instanceMask; CVPixelBufferLockBaseAddress(lb, .readOnly)
            var count = [Int](repeating: 0, count: 256)
            if let base = CVPixelBufferGetBaseAddress(lb) {
                let w = CVPixelBufferGetWidth(lb), hh = CVPixelBufferGetHeight(lb), row = CVPixelBufferGetBytesPerRow(lb)
                for y in 0..<hh { let p = base.advanced(by: y * row).assumingMemoryBound(to: UInt8.self); for x in 0..<w { count[Int(p[x])] += 1 } }
            }
            CVPixelBufferUnlockBaseAddress(lb, .readOnly)
            if let best = o.allInstances.max(by: { count[$0] < count[$1] }), let one = try? o.generateScaledMaskForImage(forInstances: IndexSet(integer: best), from: h) {
                mbuf = one
                var rest = o.allInstances; rest.remove(best)
                if !rest.isEmpty, let ob = try? o.generateScaledMaskForImage(forInstances: rest, from: h) {
                    var oi = CIImage(cvPixelBuffer: ob); let s = CGFloat(maskW) / oi.extent.width
                    oi = oi.transformed(by: CGAffineTransform(scaleX: s, y: CGFloat(maskW) * H / W / oi.extent.height))
                    if let cg = ci.createCGImage(oi, from: oi.extent, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray()) {
                        let f = out.appendingPathComponent(String(format: "others_%06d.png", n)) as CFURL
                        if let d = CGImageDestinationCreateWithURL(f, UTType.png.identifier as CFString, 1, nil) { CGImageDestinationAddImage(d, cg, nil); CGImageDestinationFinalize(d) }
                    }
                }
            }
        }
    }
    if let m = mbuf {
        var im = CIImage(cvPixelBuffer: m)
        let s = CGFloat(maskW) / im.extent.width
        im = im.transformed(by: CGAffineTransform(scaleX: s, y: CGFloat(maskW) * H / W / im.extent.height))
        if let cg = ci.createCGImage(im, from: im.extent, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray()) {
            let f = out.appendingPathComponent(String(format: "mask_%06d.png", n)) as CFURL
            if let d = CGImageDestinationCreateWithURL(f, UTType.png.identifier as CFString, 1, nil) { CGImageDestinationAddImage(d, cg, nil); CGImageDestinationFinalize(d) }
        }
    }
    if masksOnly { continue }
    var fl: [[String: Any]] = []
    for f in faces.results ?? [] {
        let b = f.boundingBox
        var pts: [String: [[Double]]] = [:]
        if let L = f.landmarks {
            let regions: [(String, VNFaceLandmarkRegion2D?)] = [("contour", L.faceContour), ("leftEye", L.leftEye), ("rightEye", L.rightEye),
                ("leftBrow", L.leftEyebrow), ("rightBrow", L.rightEyebrow), ("nose", L.nose), ("outerLips", L.outerLips), ("innerLips", L.innerLips),
                ("leftPupil", L.leftPupil), ("rightPupil", L.rightPupil), ("median", L.medianLine)]
            for (name, reg) in regions {
                guard let reg = reg else { continue }
                pts[name] = reg.pointsInImage(imageSize: CGSize(width: W, height: H)).map { [r4($0.x / W), r4(1 - $0.y / H)] }
            }
        }
        fl.append(["box": [r4(b.minX), r4(1 - b.maxY), r4(b.width), r4(b.height)], "pts": pts])
    }
    var pl: [[String: [Double]]] = []
    for p in pose.results ?? [] {
        var j: [String: [Double]] = [:]
        if let all = try? p.recognizedPoints(.all) {
            for (k, v) in all where v.confidence > 0.1 { j[k.rawValue.rawValue] = [r4(v.location.x), r4(1 - v.location.y), r4(CGFloat(v.confidence))] }
        }
        pl.append(j)
    }
    var line: [String: Any] = ["n": n, "t": (t * 1000).rounded() / 1000, "faces": fl, "pose": pl]
    if saliency { line["salient"] = (sal.results?.first?.salientObjects ?? []).map { o -> [Double] in let b = o.boundingBox; return [r4(b.minX), r4(1 - b.maxY), r4(b.width), r4(b.height)] } }
    if let d = try? JSONSerialization.data(withJSONObject: line), let s = String(data: d, encoding: .utf8) { jsonl.write((s + "\n").data(using: .utf8)!) }
}
jsonl.closeFile()
print("frames read: \(n)")
