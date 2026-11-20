// Headless device PM sampler. stdout: NDJSON; stdin line/EOF: graceful stop.
// No CUDA context or workload; one 8 MiB hardware buffer, single-pass only.
#include <cuda.h>
#include <cupti_target.h>
#include <cupti_profiler_target.h>
#include <cupti_profiler_host.h>
#include <cupti_pmsampling.h>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

static std::atomic<bool> running{true};
void check(CUptiResult result, const char* operation) {
    if (result == CUPTI_SUCCESS) return;
    const char* error = nullptr;
    cuptiGetResultString(result, &error);
    throw std::runtime_error(std::string(operation) + ": " + (error ? error : "unknown"));
}
#define CALL(name, p) check(name(&(p)), #name)

struct Session {
    bool initialized = false, started = false;
    CUpti_PmSampling_Object* sampler = nullptr;
    std::vector<CUpti_Profiler_Host_Object*> hosts;
    void stop() {
        if (!started) return;
        CUpti_PmSampling_Stop_Params p{CUpti_PmSampling_Stop_Params_STRUCT_SIZE};
        p.pPmSamplingObject = sampler; CALL(cuptiPmSamplingStop, p); started = false;
    }
    ~Session() {
        try { stop(); } catch (...) {}
        if (sampler) {
            CUpti_PmSampling_Disable_Params p{CUpti_PmSampling_Disable_Params_STRUCT_SIZE};
            p.pPmSamplingObject = sampler; cuptiPmSamplingDisable(&p);
        }
        for (auto* host : hosts) {
            CUpti_Profiler_Host_Deinitialize_Params p{CUpti_Profiler_Host_Deinitialize_Params_STRUCT_SIZE};
            p.pHostObject = host; cuptiProfilerHostDeinitialize(&p);
        }
        if (initialized) {
            CUpti_Profiler_DeInitialize_Params p{CUpti_Profiler_DeInitialize_Params_STRUCT_SIZE};
            cuptiProfilerDeInitialize(&p);
        }
    }
};
struct Profile {
    std::string name;
    std::vector<std::string> names;
    std::vector<const char*> metrics;
    std::vector<uint8_t> config, data;
    CUpti_Profiler_Host_Object* host = nullptr;
};
double wallTime() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}
int setting(const char* name, int fallback, int minimum, int maximum) {
    const char* raw = std::getenv(name);
    int value = raw ? std::stoi(raw) : fallback;
    if (value < minimum || value > maximum) throw std::runtime_error(std::string("invalid ") + name);
    return value;
}

int main(int argc, char** argv) {
    try {
        if (argc < 3) throw std::runtime_error("usage: cupti_stream GPU group=metric,metric [group=...]");
        const int intervalMs = setting("GPU_INFO_PM_INTERVAL_MS", 100, 50, 100);
        const int publishMs = setting("GPU_INFO_PM_PUBLISH_MS", 1000, 250, 1000);
        const int batchesPerGroup = setting("GPU_INFO_PM_BATCHES_PER_GROUP", 3, 1, 10);
        if (cuInit(0) != CUDA_SUCCESS) throw std::runtime_error("CUDA init failed");
        CUdevice device = 0;
        if (std::string(argv[1]).find(':') != std::string::npos) {
            if (cuDeviceGetByPCIBusId(&device, argv[1]) != CUDA_SUCCESS) throw std::runtime_error("PCI device mapping failed");
        } else device = std::stoi(argv[1]);
        if (device < 0) throw std::runtime_error("invalid CUDA device");
        std::vector<Profile> profiles;
        Session session; // destroyed before profile backing buffers
        CUpti_Profiler_Initialize_Params init{CUpti_Profiler_Initialize_Params_STRUCT_SIZE};
        CALL(cuptiProfilerInitialize, init); session.initialized = true;
        CUpti_Device_GetChipName_Params chip{CUpti_Device_GetChipName_Params_STRUCT_SIZE};
        chip.deviceIndex = device; CALL(cuptiDeviceGetChipName, chip);
        CUpti_PmSampling_GetCounterAvailability_Params availability{CUpti_PmSampling_GetCounterAvailability_Params_STRUCT_SIZE};
        availability.deviceIndex = device; CALL(cuptiPmSamplingGetCounterAvailability, availability);
        std::vector<uint8_t> available(availability.counterAvailabilityImageSize);
        availability.pCounterAvailabilityImage = available.data(); CALL(cuptiPmSamplingGetCounterAvailability, availability);
        profiles.resize(argc - 2);
        for (int i = 2; i < argc; ++i) {
            auto& p = profiles[i - 2];
            const std::string argument = argv[i];
            const auto split = argument.find('=');
            if (split == std::string::npos) throw std::runtime_error("missing metric group");
            p.name = argument.substr(0, split);
            if (p.name.empty() || p.name.find_first_not_of("abcdefghijklmnopqrstuvwxyz_") != std::string::npos)
                throw std::runtime_error("invalid group name");
            std::istringstream stream(argument.substr(split + 1));
            for (std::string metric; std::getline(stream, metric, ',');) p.names.push_back(metric);
            if (p.names.empty()) throw std::runtime_error("empty metric set");
            for (const auto& name : p.names) p.metrics.push_back(name.c_str());
            CUpti_Profiler_Host_Initialize_Params host{CUpti_Profiler_Host_Initialize_Params_STRUCT_SIZE};
            host.profilerType = CUPTI_PROFILER_TYPE_PM_SAMPLING; host.pChipName = chip.pChipName;
            host.pCounterAvailabilityImage = available.data(); CALL(cuptiProfilerHostInitialize, host);
            p.host = host.pHostObject; session.hosts.push_back(p.host);
            CUpti_Profiler_Host_ConfigAddMetrics_Params add{CUpti_Profiler_Host_ConfigAddMetrics_Params_STRUCT_SIZE};
            add.pHostObject = p.host; add.ppMetricNames = p.metrics.data(); add.numMetrics = p.metrics.size();
            CALL(cuptiProfilerHostConfigAddMetrics, add);
            CUpti_Profiler_Host_GetConfigImageSize_Params size{CUpti_Profiler_Host_GetConfigImageSize_Params_STRUCT_SIZE};
            size.pHostObject = p.host; CALL(cuptiProfilerHostGetConfigImageSize, size); p.config.resize(size.configImageSize);
            CUpti_Profiler_Host_GetConfigImage_Params config{CUpti_Profiler_Host_GetConfigImage_Params_STRUCT_SIZE};
            config.pHostObject = p.host; config.pConfigImage = p.config.data(); config.configImageSize = p.config.size();
            CALL(cuptiProfilerHostGetConfigImage, config);
            CUpti_Profiler_Host_GetNumOfPasses_Params passes{CUpti_Profiler_Host_GetNumOfPasses_Params_STRUCT_SIZE};
            passes.pConfigImage = p.config.data(); passes.configImageSize = p.config.size(); CALL(cuptiProfilerHostGetNumOfPasses, passes);
            if (passes.numOfPasses != 1) throw std::runtime_error(p.name + " requires multiple passes; unsupported");
        }
        CUpti_PmSampling_Enable_Params enable{CUpti_PmSampling_Enable_Params_STRUCT_SIZE};
        enable.deviceIndex = device;
        std::thread([] { std::string command; std::getline(std::cin, command); running = false; }).detach();
        std::cout << std::setprecision(17);
        uint64_t sequence = 0, epoch = 0;
        size_t group = 0;
        while (running) {
            CALL(cuptiPmSamplingEnable, enable); session.sampler = enable.pPmSamplingObject;
            uint64_t previousEnd = 0;
            ++epoch;
            auto& p = profiles[group];
            CUpti_PmSampling_SetConfig_Params config{CUpti_PmSampling_SetConfig_Params_STRUCT_SIZE};
            config.pPmSamplingObject = session.sampler; config.pConfig = p.config.data(); config.configSize = p.config.size();
            config.hardwareBufferSize = 8 * 1024 * 1024; config.samplingInterval = uint64_t(intervalMs) * 1000000;
            config.triggerMode = CUPTI_PM_SAMPLING_TRIGGER_MODE_GPU_TIME_INTERVAL; CALL(cuptiPmSamplingSetConfig, config);
            CUpti_PmSampling_GetCounterDataSize_Params size{CUpti_PmSampling_GetCounterDataSize_Params_STRUCT_SIZE};
            size.pPmSamplingObject = session.sampler; size.pMetricNames = p.metrics.data(); size.numMetrics = p.metrics.size(); size.maxSamples = 128;
            CALL(cuptiPmSamplingGetCounterDataSize, size); p.data.resize(size.counterDataSize);
            CUpti_PmSampling_CounterDataImage_Initialize_Params image{CUpti_PmSampling_CounterDataImage_Initialize_Params_STRUCT_SIZE};
            image.pPmSamplingObject = session.sampler; image.pCounterData = p.data.data(); image.counterDataSize = p.data.size();
            CALL(cuptiPmSamplingCounterDataImageInitialize, image);
            CUpti_PmSampling_Start_Params start{CUpti_PmSampling_Start_Params_STRUCT_SIZE};
            start.pPmSamplingObject = session.sampler; CALL(cuptiPmSamplingStart, start); session.started = true;
            int emptyBatches = 0;
            for (int batch = 0; running && (profiles.size() == 1 || batch < batchesPerGroup); ++batch) {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(publishMs);
                while (running && std::chrono::steady_clock::now() < deadline)
                    std::this_thread::sleep_for(std::chrono::milliseconds(10));
                CUpti_PmSampling_DecodeData_Params decode{CUpti_PmSampling_DecodeData_Params_STRUCT_SIZE};
                decode.pPmSamplingObject = session.sampler; decode.pCounterDataImage = p.data.data(); decode.counterDataImageSize = p.data.size();
                CALL(cuptiPmSamplingDecodeData, decode);
                if (decode.overflow) throw std::runtime_error("hardware buffer overflow; stop to avoid invalid readings");
                CUpti_PmSampling_GetCounterDataInfo_Params info{CUpti_PmSampling_GetCounterDataInfo_Params_STRUCT_SIZE};
                info.pCounterDataImage = p.data.data(); info.counterDataImageSize = p.data.size(); CALL(cuptiPmSamplingGetCounterDataInfo, info);
                emptyBatches = info.numCompletedSamples ? 0 : emptyBatches + 1;
                if (emptyBatches >= 4) throw std::runtime_error("no completed GPU samples; collector stopped");
                std::cout << "{\"group\":\"" << p.name << "\",\"sequence\":" << ++sequence
                          << ",\"epoch\":" << epoch << ",\"received_at\":" << wallTime() << ",\"overflow\":false,\"incomplete_samples\":"
                          << info.numPopulatedSamples - info.numCompletedSamples << ",\"samples\":[";
                bool comma = false;
                for (size_t sample = 0; sample < info.numCompletedSamples; ++sample) {
                    CUpti_PmSampling_CounterData_GetSampleInfo_Params stamp{CUpti_PmSampling_CounterData_GetSampleInfo_Params_STRUCT_SIZE};
                    stamp.pPmSamplingObject = session.sampler; stamp.pCounterDataImage = p.data.data(); stamp.counterDataImageSize = p.data.size(); stamp.sampleIndex = sample;
                    CALL(cuptiPmSamplingCounterDataGetSampleInfo, stamp);
                    if (stamp.endTimestamp <= previousEnd || stamp.endTimestamp <= stamp.startTimestamp) continue;
                    previousEnd = stamp.endTimestamp;
                    std::vector<double> values(p.metrics.size());
                    CUpti_Profiler_Host_EvaluateToGpuValues_Params eval{CUpti_Profiler_Host_EvaluateToGpuValues_Params_STRUCT_SIZE};
                    eval.pHostObject = p.host; eval.pCounterDataImage = p.data.data(); eval.counterDataImageSize = p.data.size();
                    eval.ppMetricNames = p.metrics.data(); eval.numMetrics = p.metrics.size(); eval.rangeIndex = sample; eval.pMetricValues = values.data();
                    CALL(cuptiProfilerHostEvaluateToGpuValues, eval);
                    if (comma) std::cout << ','; comma = true;
                    std::cout << "{\"start_ns\":" << stamp.startTimestamp << ",\"end_ns\":" << stamp.endTimestamp << ",\"values\":[";
                    for (size_t j = 0; j < values.size(); ++j) {
                        if (!std::isfinite(values[j]) || values[j] < 0) throw std::runtime_error("invalid metric value");
                        if (j) std::cout << ','; std::cout << values[j];
                    }
                    std::cout << "]}";
                }
                std::cout << "]}" << std::endl;
                // Same reset/drain pattern as the CUPTI pm_sampling sample; report partial samples.
                CALL(cuptiPmSamplingCounterDataImageInitialize, image);
            }
            session.stop();
            CUpti_PmSampling_Disable_Params disable{CUpti_PmSampling_Disable_Params_STRUCT_SIZE};
            disable.pPmSamplingObject = session.sampler; CALL(cuptiPmSamplingDisable, disable); session.sampler = nullptr;
            group = (group + 1) % profiles.size();
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl; return 1;
    }
}
