/*
 * DJMAKER Essentia runtime bridge.
 *
 * This program is distributed under the GNU Affero General Public License v3.0
 * because it is linked with Essentia (AGPL-3.0-only).
 *
 * It intentionally does not decode audio files. DJMAKER uses FFmpeg to produce
 * mono 44.1 kHz float32 PCM and passes that stream to this process. Keeping
 * decoding outside Essentia makes the runtime small and reproducible on Windows
 * and macOS.
 */

#include <essentia/algorithm.h>
#include <essentia/algorithmfactory.h>
#include <essentia/essentia.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#ifndef DJMAKER_ESSENTIA_RUNTIME_VERSION
#define DJMAKER_ESSENTIA_RUNTIME_VERSION "dev"
#endif

#ifndef DJMAKER_ESSENTIA_UPSTREAM_SHA
#define DJMAKER_ESSENTIA_UPSTREAM_SHA "unknown"
#endif

namespace {

using essentia::Real;
using essentia::standard::Algorithm;
using essentia::standard::AlgorithmFactory;

constexpr int kRequiredSampleRate = 44100;
constexpr int kDefaultMinTempo = 40;
constexpr int kDefaultMaxTempo = 208;

struct AnalysisResult {
    Real bpm = 0.0F;
    Real bpm_confidence = 0.0F;
    std::string key;
    std::string scale;
    Real key_strength = 0.0F;
};

class EssentiaSession {
public:
    EssentiaSession() { essentia::init(); }
    ~EssentiaSession() { essentia::shutdown(); }

    EssentiaSession(const EssentiaSession&) = delete;
    EssentiaSession& operator=(const EssentiaSession&) = delete;
};

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (const unsigned char ch : value) {
        switch (ch) {
            case '\\': out << "\\\\"; break;
            case '"': out << "\\\""; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20U) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(ch) << std::dec << std::setfill(' ');
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

std::vector<Real> read_float32_pcm(std::istream& input) {
    std::vector<Real> audio;
    std::vector<float> block(64U * 1024U);

    while (input.good()) {
        input.read(
            reinterpret_cast<char*>(block.data()),
            static_cast<std::streamsize>(block.size() * sizeof(float))
        );
        const std::streamsize bytes = input.gcount();
        if (bytes == 0) {
            break;
        }
        if (bytes % static_cast<std::streamsize>(sizeof(float)) != 0) {
            throw std::runtime_error("PCM stream contains an incomplete float32 sample");
        }
        const std::size_t count = static_cast<std::size_t>(bytes) / sizeof(float);
        audio.reserve(audio.size() + count);
        for (std::size_t index = 0; index < count; ++index) {
            const float sample = block[index];
            if (!std::isfinite(sample)) {
                throw std::runtime_error("PCM stream contains NaN or infinity");
            }
            audio.push_back(static_cast<Real>(sample));
        }
    }

    if (input.bad()) {
        throw std::runtime_error("Failed while reading PCM stream");
    }
    if (audio.empty()) {
        throw std::runtime_error("PCM stream is empty");
    }
    return audio;
}

AnalysisResult analyze(
    const std::vector<Real>& audio,
    int sample_rate,
    const std::string& rhythm_method,
    int min_tempo,
    int max_tempo
) {
    if (sample_rate != kRequiredSampleRate) {
        throw std::runtime_error(
            "RhythmExtractor2013 requires 44100 Hz input; use FFmpeg resampling first"
        );
    }
    if (min_tempo <= 0 || max_tempo <= min_tempo) {
        throw std::runtime_error("Invalid BPM range");
    }
    if (rhythm_method != "multifeature" && rhythm_method != "degara") {
        throw std::runtime_error("Rhythm method must be 'multifeature' or 'degara'");
    }

    AlgorithmFactory& factory = AlgorithmFactory::instance();
    std::unique_ptr<Algorithm> rhythm(factory.create("RhythmExtractor2013"));
    std::unique_ptr<Algorithm> key_extractor(factory.create("KeyExtractor"));

    rhythm->configure(
        "method", rhythm_method,
        "minTempo", min_tempo,
        "maxTempo", max_tempo
    );
    key_extractor->configure("sampleRate", static_cast<Real>(sample_rate));

    AnalysisResult result;
    std::vector<Real> ticks;
    std::vector<Real> estimates;
    std::vector<Real> intervals;

    rhythm->input("signal").set(audio);
    rhythm->output("bpm").set(result.bpm);
    rhythm->output("ticks").set(ticks);
    rhythm->output("confidence").set(result.bpm_confidence);
    rhythm->output("estimates").set(estimates);
    rhythm->output("bpmIntervals").set(intervals);
    rhythm->compute();

    key_extractor->input("audio").set(audio);
    key_extractor->output("key").set(result.key);
    key_extractor->output("scale").set(result.scale);
    key_extractor->output("strength").set(result.key_strength);
    key_extractor->compute();

    return result;
}

void print_result(const AnalysisResult& result) {
    std::cout << std::fixed << std::setprecision(6)
              << "{\"bpm\":" << result.bpm
              << ",\"bpm_confidence\":" << result.bpm_confidence
              << ",\"key\":\"" << json_escape(result.key) << "\""
              << ",\"scale\":\"" << json_escape(result.scale) << "\""
              << ",\"key_strength\":" << result.key_strength
              << "}" << std::endl;
}

std::vector<Real> make_self_test_audio() {
    constexpr int seconds = 20;
    constexpr Real two_pi = static_cast<Real>(6.28318530717958647692);
    std::vector<Real> audio(static_cast<std::size_t>(kRequiredSampleRate * seconds), 0.0F);

    // A4 tone gives the tonal extractor stable content. A short impulse every
    // half-second creates an unambiguous 120 BPM pulse train for the rhythm graph.
    for (std::size_t index = 0; index < audio.size(); ++index) {
        const Real time = static_cast<Real>(index) / static_cast<Real>(kRequiredSampleRate);
        audio[index] = static_cast<Real>(0.08) * std::sin(two_pi * static_cast<Real>(440.0) * time);
    }
    const std::size_t beat_samples = static_cast<std::size_t>(kRequiredSampleRate / 2);
    const std::size_t pulse_length = 128;
    for (std::size_t beat = 0; beat < audio.size(); beat += beat_samples) {
        const std::size_t stop = std::min(audio.size(), beat + pulse_length);
        for (std::size_t index = beat; index < stop; ++index) {
            audio[index] += static_cast<Real>(0.8) *
                (static_cast<Real>(1.0) - static_cast<Real>(index - beat) / pulse_length);
        }
    }
    return audio;
}

void self_test() {
    const auto audio = make_self_test_audio();
    const AnalysisResult result = analyze(
        audio,
        kRequiredSampleRate,
        "degara",
        kDefaultMinTempo,
        kDefaultMaxTempo
    );
    if (!std::isfinite(result.bpm) || !std::isfinite(result.key_strength)) {
        throw std::runtime_error("Essentia self-test produced non-finite values");
    }
    std::cout << "{\"ok\":true,\"runtime\":\""
              << json_escape(DJMAKER_ESSENTIA_RUNTIME_VERSION)
              << "\",\"upstream\":\""
              << json_escape(DJMAKER_ESSENTIA_UPSTREAM_SHA)
              << "\"}" << std::endl;
}

std::string value_after(int argc, char* argv[], int& index, const std::string& option) {
    if (index + 1 >= argc) {
        throw std::runtime_error("Missing value for " + option);
    }
    ++index;
    return argv[index];
}

int parse_int(const std::string& value, const std::string& option) {
    std::size_t consumed = 0;
    const int parsed = std::stoi(value, &consumed);
    if (consumed != value.size()) {
        throw std::runtime_error("Invalid integer for " + option + ": " + value);
    }
    return parsed;
}

void print_usage(const char* executable) {
    std::cerr
        << "Usage:\n"
        << "  " << executable << " --version\n"
        << "  " << executable << " --self-test\n"
        << "  " << executable << " analyze [--input FILE|-] [--sample-rate 44100] "
        << "[--method multifeature|degara] [--min-tempo 40] [--max-tempo 208]\n\n"
        << "Input for analyze is mono float32 little-endian PCM. DJMAKER normally "
        << "creates it with FFmpeg.\n";
}

int run(int argc, char* argv[]) {
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::cout << "djmaker-essentia " << DJMAKER_ESSENTIA_RUNTIME_VERSION
                  << " upstream=" << DJMAKER_ESSENTIA_UPSTREAM_SHA << std::endl;
        return 0;
    }

    EssentiaSession session;

    if (argc == 2 && std::string(argv[1]) == "--self-test") {
        self_test();
        return 0;
    }

    if (argc < 2 || std::string(argv[1]) != "analyze") {
        print_usage(argv[0]);
        return 2;
    }

    std::string input_path = "-";
    std::string method = "multifeature";
    int sample_rate = kRequiredSampleRate;
    int min_tempo = kDefaultMinTempo;
    int max_tempo = kDefaultMaxTempo;

    for (int index = 2; index < argc; ++index) {
        const std::string option = argv[index];
        if (option == "--input") {
            input_path = value_after(argc, argv, index, option);
        } else if (option == "--sample-rate") {
            sample_rate = parse_int(value_after(argc, argv, index, option), option);
        } else if (option == "--method") {
            method = value_after(argc, argv, index, option);
        } else if (option == "--min-tempo") {
            min_tempo = parse_int(value_after(argc, argv, index, option), option);
        } else if (option == "--max-tempo") {
            max_tempo = parse_int(value_after(argc, argv, index, option), option);
        } else {
            throw std::runtime_error("Unknown option: " + option);
        }
    }

    std::vector<Real> audio;
    if (input_path == "-") {
        std::ios::sync_with_stdio(false);
#ifdef _WIN32
        if (_setmode(_fileno(stdin), _O_BINARY) == -1) {
            throw std::runtime_error("Cannot switch stdin to binary mode");
        }
#endif
        audio = read_float32_pcm(std::cin);
    } else {
        std::ifstream input(input_path, std::ios::binary);
        if (!input) {
            throw std::runtime_error("Cannot open input file: " + input_path);
        }
        audio = read_float32_pcm(input);
    }

    print_result(analyze(audio, sample_rate, method, min_tempo, max_tempo));
    return 0;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        return run(argc, argv);
    } catch (const essentia::EssentiaException& exc) {
        std::cerr << "Essentia error: " << exc.what() << std::endl;
        return 10;
    } catch (const std::exception& exc) {
        std::cerr << "Error: " << exc.what() << std::endl;
        return 11;
    }
}
