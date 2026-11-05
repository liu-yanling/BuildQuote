// 本地中文 PDF OCR（macOS Vision 框架，免费，无需联网）
// 用法：swift ocr.swift <输入.pdf> <输出.txt>
import Foundation
import PDFKit
import Vision
import CoreGraphics

guard CommandLine.arguments.count >= 3 else {
    FileHandle.standardError.write("用法: swift ocr.swift <输入.pdf> <输出.txt>\n".data(using: .utf8)!)
    exit(1)
}
let inputPath = CommandLine.arguments[1]
let outputPath = CommandLine.arguments[2]

guard let doc = PDFDocument(url: URL(fileURLWithPath: inputPath)) else {
    FileHandle.standardError.write("无法打开 PDF: \(inputPath)\n".data(using: .utf8)!)
    exit(2)
}

let scale: CGFloat = 2.5
var allText = ""
for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let rect = page.bounds(for: .mediaBox)
    let w = Int(rect.width * scale)
    let h = Int(rect.height * scale)

    let colorSpace = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                              bytesPerRow: 0, space: colorSpace,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { continue }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.saveGState()
    // 扫描件水平镜像（合同文字左右颠倒），额外做一次水平翻转
    ctx.translateBy(x: CGFloat(w), y: CGFloat(h))
    ctx.scaleBy(x: -scale, y: -scale)
    page.draw(with: .mediaBox, to: ctx)
    ctx.restoreGState()
    guard let cgImage = ctx.makeImage() else { continue }

    let request = VNRecognizeTextRequest { req, _ in
        guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
        for o in obs {
            if let t = o.topCandidates(1).first {
                allText += t.string + "\n"
            }
        }
    }
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? handler.perform([request])
    FileHandle.standardError.write("第 \(i + 1)/\(doc.pageCount) 页完成（当前累计 \(allText.count) 字符）\n".data(using: .utf8)!)
}

try? allText.write(toFile: outputPath, atomically: true, encoding: .utf8)
FileHandle.standardError.write("OCR 完成，共 \(allText.count) 字符 → \(outputPath)\n".data(using: .utf8)!)
