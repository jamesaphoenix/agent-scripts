// Targeted, local OCR evidence for disputed native URL crops. No network calls.
// JSON array of absolute PNG paths on stdin; JSON evidence array on stdout.
import Foundation
import Vision
import ImageIO

let input = FileHandle.standardInput.readDataToEndOfFile()
let paths = try JSONDecoder().decode([String].self, from: input)
var output: [[String: Any]] = []
for path in paths {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-GB"]
    do {
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path))
        try handler.perform([request])
        let lines: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let text = observation.topCandidates(1).first else { return nil }
            return ["text": text.string, "confidence": text.confidence,
                    "bounds": [observation.boundingBox.minX, observation.boundingBox.minY,
                               observation.boundingBox.width, observation.boundingBox.height]]
        }
        output.append(["path": path, "lines": lines, "method": "Apple Vision accurate, language correction disabled"])
    } catch {
        output.append(["path": path, "error": String(describing: error)])
    }
}
let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(data)
