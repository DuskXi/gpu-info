// Bounded research probe for CUDA 12.6+. No CUDA context or workload is created.
// Default: enumerate PM metrics. --sample [metric ...]: sample GPU 0 for 6 seconds.
// --check validates a single-pass set; --describe prints metadata; --sass only enumerates.
// --catalog pm|range CHIP [metric ...]: offline metadata/configuration, no CUDA initialization.
#include <cuda.h>
#include <cupti_target.h>
#include <cupti_version.h>
#include <cupti_profiler_target.h>
#include <cupti_profiler_host.h>
#include <cupti_pmsampling.h>
#include <cupti_sass_metrics.h>
#include <cmath>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <vector>
#include <string>

void check(CUptiResult result, const char* operation) {
    if (result == CUPTI_SUCCESS) return;
    const char* message = nullptr;
    cuptiGetResultString(result, &message);
    throw std::runtime_error(std::string(operation) + ": " + (message ? message : "unknown"));
}
#define CALL(name, params) check(name(&(params)), #name)

struct Session {
    bool initialized = false, started = false;
    CUpti_Profiler_Host_Object* host = nullptr;
    CUpti_PmSampling_Object* sampler = nullptr;
    ~Session() {
        if (started) {
            CUpti_PmSampling_Stop_Params p{CUpti_PmSampling_Stop_Params_STRUCT_SIZE};
            p.pPmSamplingObject = sampler; cuptiPmSamplingStop(&p);
        }
        if (sampler) {
            CUpti_PmSampling_Disable_Params p{CUpti_PmSampling_Disable_Params_STRUCT_SIZE};
            p.pPmSamplingObject = sampler; cuptiPmSamplingDisable(&p);
        }
        if (host) {
            CUpti_Profiler_Host_Deinitialize_Params p{CUpti_Profiler_Host_Deinitialize_Params_STRUCT_SIZE};
            p.pHostObject = host; cuptiProfilerHostDeinitialize(&p);
        }
        if (initialized) {
            CUpti_Profiler_DeInitialize_Params p{CUpti_Profiler_DeInitialize_Params_STRUCT_SIZE};
            cuptiProfilerDeInitialize(&p);
        }
    }
};

int catalog(int argc, char** argv) {
    if (argc < 4) throw std::runtime_error("usage: --catalog pm|range CHIP [metric ...]");
    const std::string kind = argv[2];
    if (kind != "pm" && kind != "range") throw std::runtime_error("catalog kind must be pm or range");
    uint32_t version = 0;
    check(cuptiGetVersion(&version), "cuptiGetVersion");
    std::cout << "offline_catalog cupti=" << version << " kind=" << kind << " chip=" << argv[3]
              << " counter_availability=not_checked\n";
    // A fresh host per requested metric avoids a failed configuration poisoning later checks.
    for (int index = 4; index < argc || (index == 4 && argc == 4); ++index) {
        Session session;
        CUpti_Profiler_Host_Initialize_Params host{CUpti_Profiler_Host_Initialize_Params_STRUCT_SIZE};
        host.profilerType = kind == "pm" ? CUPTI_PROFILER_TYPE_PM_SAMPLING : CUPTI_PROFILER_TYPE_RANGE_PROFILER;
        host.pChipName = argv[3];
        CALL(cuptiProfilerHostInitialize, host); session.host = host.pHostObject;
        if (argc == 4) {
            for (int type = 0; type < CUPTI_METRIC_TYPE__COUNT; ++type) {
                CUpti_Profiler_Host_GetBaseMetrics_Params list{CUpti_Profiler_Host_GetBaseMetrics_Params_STRUCT_SIZE};
                list.pHostObject = session.host; list.metricType = static_cast<CUpti_MetricType>(type);
                CALL(cuptiProfilerHostGetBaseMetrics, list);
                for (size_t i = 0; i < list.numMetrics; ++i) std::cout << list.ppMetricNames[i] << '\n';
            }
            return 0;
        }
        const char* metric = argv[index];
        std::cout << "metric=" << metric << '\n';
        try {
            CUpti_Profiler_Host_GetMetricProperties_Params properties{CUpti_Profiler_Host_GetMetricProperties_Params_STRUCT_SIZE};
            properties.pHostObject = session.host; properties.pMetricName = metric;
            CALL(cuptiProfilerHostGetMetricProperties, properties);
            std::cout << "description=" << properties.pDescription << " unit=" << properties.pDimUnit << '\n';
            CUpti_Profiler_Host_ConfigAddMetrics_Params add{CUpti_Profiler_Host_ConfigAddMetrics_Params_STRUCT_SIZE};
            add.pHostObject = session.host; add.ppMetricNames = &metric; add.numMetrics = 1;
            CALL(cuptiProfilerHostConfigAddMetrics, add);
            CUpti_Profiler_Host_GetConfigImageSize_Params size{CUpti_Profiler_Host_GetConfigImageSize_Params_STRUCT_SIZE};
            size.pHostObject = session.host; CALL(cuptiProfilerHostGetConfigImageSize, size);
            std::vector<uint8_t> config(size.configImageSize);
            CUpti_Profiler_Host_GetConfigImage_Params image{CUpti_Profiler_Host_GetConfigImage_Params_STRUCT_SIZE};
            image.pHostObject = session.host; image.pConfigImage = config.data(); image.configImageSize = config.size();
            CALL(cuptiProfilerHostGetConfigImage, image);
            CUpti_Profiler_Host_GetNumOfPasses_Params passes{CUpti_Profiler_Host_GetNumOfPasses_Params_STRUCT_SIZE};
            passes.pConfigImage = config.data(); passes.configImageSize = config.size();
            CALL(cuptiProfilerHostGetNumOfPasses, passes);
            std::cout << "offline_passes=" << passes.numOfPasses << " (not a live sampling result)\n";
        } catch (const std::exception& error) {
            std::cout << "unavailable=" << error.what() << '\n';
        }
    }
    return 0;
}

int main(int argc, char** argv) {
    try {
        if (argc > 1 && std::string(argv[1]) == "--catalog") return catalog(argc, argv);
        if (cuInit(0) != CUDA_SUCCESS) throw std::runtime_error("cuInit failed");
        Session session;
        CUpti_Profiler_Initialize_Params init{CUpti_Profiler_Initialize_Params_STRUCT_SIZE};
        CALL(cuptiProfilerInitialize, init); session.initialized = true;
        CUpti_Device_GetChipName_Params chip{CUpti_Device_GetChipName_Params_STRUCT_SIZE};
        chip.deviceIndex = 0; CALL(cuptiDeviceGetChipName, chip);
        std::cout << "chip=" << chip.pChipName << '\n';
        if (argc > 1 && std::string(argv[1]) == "--sass") {
            CUpti_SassMetrics_GetNumOfMetrics_Params count{CUpti_SassMetrics_GetNumOfMetrics_Params_STRUCT_SIZE};
            count.pChipName = chip.pChipName; CALL(cuptiSassMetricsGetNumOfMetrics, count);
            std::vector<CUpti_SassMetrics_MetricDetails> metrics(count.numOfMetrics);
            CUpti_SassMetrics_GetMetrics_Params list{CUpti_SassMetrics_GetMetrics_Params_STRUCT_SIZE};
            list.pChipName = chip.pChipName; list.numOfMetrics = metrics.size(); list.pMetricsList = metrics.data();
            CALL(cuptiSassMetricsGetMetrics, list);
            for (const auto& m : metrics) std::cout << m.pMetricName << " | " << m.pMetricDescription << '\n';
            return 0;
        }
        for (int api = 0; api < 3; ++api) {
            CUpti_Profiler_DeviceSupported_Params p{CUpti_Profiler_DeviceSupported_Params_STRUCT_SIZE};
            p.cuDevice = 0; p.api = static_cast<CUpti_Profiler_API>(api);
            CALL(cuptiProfilerDeviceSupported, p);
            std::cout << "api=" << api << " supported=" << p.isSupported << " (3=yes)\n";
        }
        CUpti_PmSampling_GetCounterAvailability_Params availability{CUpti_PmSampling_GetCounterAvailability_Params_STRUCT_SIZE};
        availability.deviceIndex = 0; CALL(cuptiPmSamplingGetCounterAvailability, availability);
        std::vector<uint8_t> available(availability.counterAvailabilityImageSize);
        availability.pCounterAvailabilityImage = available.data();
        CALL(cuptiPmSamplingGetCounterAvailability, availability);
        CUpti_Profiler_Host_Initialize_Params host{CUpti_Profiler_Host_Initialize_Params_STRUCT_SIZE};
        host.profilerType = CUPTI_PROFILER_TYPE_PM_SAMPLING;
        host.pChipName = chip.pChipName; host.pCounterAvailabilityImage = available.data();
        CALL(cuptiProfilerHostInitialize, host); session.host = host.pHostObject;
        if (argc > 1 && std::string(argv[1]) == "--describe") {
            for (int i = 2; i < argc; ++i) {
                CUpti_Profiler_Host_GetMetricProperties_Params p{CUpti_Profiler_Host_GetMetricProperties_Params_STRUCT_SIZE};
                p.pHostObject = session.host; p.pMetricName = argv[i];
                CALL(cuptiProfilerHostGetMetricProperties, p);
                std::cout << argv[i] << " | " << p.pDescription << " | unit=" << p.pDimUnit << '\n';
            }
            return 0;
        }
        if (argc < 2) {
            for (int type = 0; type < CUPTI_METRIC_TYPE__COUNT; ++type) {
                CUpti_Profiler_Host_GetBaseMetrics_Params p{CUpti_Profiler_Host_GetBaseMetrics_Params_STRUCT_SIZE};
                p.pHostObject = session.host; p.metricType = static_cast<CUpti_MetricType>(type);
                CALL(cuptiProfilerHostGetBaseMetrics, p);
                for (size_t i = 0; i < p.numMetrics; ++i) std::cout << p.ppMetricNames[i] << '\n';
            }
            return 0;
        }
        const std::string mode = argv[1];
        if (mode != "--sample" && mode != "--check") throw std::runtime_error("use --sample, --check, --describe or --sass");
        std::vector<const char*> metrics;
        for (int i = 2; i < argc; ++i) metrics.push_back(argv[i]);
        if (metrics.empty()) metrics = {
            "sm__cycles_active.avg.pct_of_peak_sustained_elapsed",
            "sm__pipe_tensor_cycles_active_realtime.avg.pct_of_peak_sustained_elapsed"
        };
        CUpti_Profiler_Host_ConfigAddMetrics_Params add{CUpti_Profiler_Host_ConfigAddMetrics_Params_STRUCT_SIZE};
        add.pHostObject = session.host; add.ppMetricNames = metrics.data(); add.numMetrics = metrics.size();
        CALL(cuptiProfilerHostConfigAddMetrics, add);
        CUpti_Profiler_Host_GetConfigImageSize_Params configSize{CUpti_Profiler_Host_GetConfigImageSize_Params_STRUCT_SIZE};
        configSize.pHostObject = session.host; CALL(cuptiProfilerHostGetConfigImageSize, configSize);
        std::vector<uint8_t> config(configSize.configImageSize);
        CUpti_Profiler_Host_GetConfigImage_Params configImage{CUpti_Profiler_Host_GetConfigImage_Params_STRUCT_SIZE};
        configImage.pHostObject = session.host; configImage.configImageSize = config.size(); configImage.pConfigImage = config.data();
        CALL(cuptiProfilerHostGetConfigImage, configImage);
        CUpti_Profiler_Host_GetNumOfPasses_Params passes{CUpti_Profiler_Host_GetNumOfPasses_Params_STRUCT_SIZE};
        passes.pConfigImage = config.data(); passes.configImageSize = config.size();
        CALL(cuptiProfilerHostGetNumOfPasses, passes);
        std::cout << "passes=" << passes.numOfPasses << '\n';
        if (passes.numOfPasses != 1) throw std::runtime_error("refusing multi-pass configuration");
        if (mode == "--check") return 0;
        CUpti_PmSampling_Enable_Params enable{CUpti_PmSampling_Enable_Params_STRUCT_SIZE};
        enable.deviceIndex = 0; CALL(cuptiPmSamplingEnable, enable); session.sampler = enable.pPmSamplingObject;
        CUpti_PmSampling_SetConfig_Params set{CUpti_PmSampling_SetConfig_Params_STRUCT_SIZE};
        set.pPmSamplingObject = session.sampler; set.pConfig = config.data(); set.configSize = config.size();
        set.hardwareBufferSize = 8 * 1024 * 1024; set.samplingInterval = 100000000;
        set.triggerMode = CUPTI_PM_SAMPLING_TRIGGER_MODE_GPU_TIME_INTERVAL;
        CALL(cuptiPmSamplingSetConfig, set);
        CUpti_PmSampling_GetCounterDataSize_Params size{CUpti_PmSampling_GetCounterDataSize_Params_STRUCT_SIZE};
        size.pPmSamplingObject = session.sampler; size.pMetricNames = metrics.data(); size.numMetrics = metrics.size(); size.maxSamples = 128;
        CALL(cuptiPmSamplingGetCounterDataSize, size);
        std::vector<uint8_t> data(size.counterDataSize);
        CUpti_PmSampling_CounterDataImage_Initialize_Params image{CUpti_PmSampling_CounterDataImage_Initialize_Params_STRUCT_SIZE};
        image.pPmSamplingObject = session.sampler; image.pCounterData = data.data(); image.counterDataSize = data.size();
        CALL(cuptiPmSamplingCounterDataImageInitialize, image);
        std::cout << "hardware_buffer_bytes=" << set.hardwareBufferSize << " counter_image_bytes=" << data.size() << '\n';
        CUpti_PmSampling_Start_Params start{CUpti_PmSampling_Start_Params_STRUCT_SIZE};
        start.pPmSamplingObject = session.sampler; CALL(cuptiPmSamplingStart, start); session.started = true;
        size_t seen = 0;
        for (int second = 1; second <= 6; ++second) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            CUpti_PmSampling_DecodeData_Params decode{CUpti_PmSampling_DecodeData_Params_STRUCT_SIZE};
            decode.pPmSamplingObject = session.sampler; decode.pCounterDataImage = data.data(); decode.counterDataImageSize = data.size();
            CALL(cuptiPmSamplingDecodeData, decode);
            CUpti_PmSampling_GetCounterDataInfo_Params info{CUpti_PmSampling_GetCounterDataInfo_Params_STRUCT_SIZE};
            info.pCounterDataImage = data.data(); info.counterDataImageSize = data.size();
            CALL(cuptiPmSamplingGetCounterDataInfo, info);
            std::vector<double> sum(metrics.size(), 0), values(metrics.size());
            const size_t count = info.numCompletedSamples - seen;
            for (; seen < info.numCompletedSamples; ++seen) {
                CUpti_Profiler_Host_EvaluateToGpuValues_Params eval{CUpti_Profiler_Host_EvaluateToGpuValues_Params_STRUCT_SIZE};
                eval.pHostObject = session.host; eval.pCounterDataImage = data.data(); eval.counterDataImageSize = data.size();
                eval.ppMetricNames = metrics.data(); eval.numMetrics = metrics.size(); eval.rangeIndex = seen; eval.pMetricValues = values.data();
                CALL(cuptiProfilerHostEvaluateToGpuValues, eval);
                for (size_t i = 0; i < metrics.size(); ++i) {
                    if (!std::isfinite(values[i])) throw std::runtime_error("non-finite metric value");
                    sum[i] += values[i];
                }
            }
            std::cout << "second=" << second << " new_samples=" << count << " overflow=" << static_cast<int>(decode.overflow) << '\n';
            if (decode.overflow) throw std::runtime_error("hardware buffer overflow; samples invalid");
            if (count) for (size_t i = 0; i < metrics.size(); ++i) std::cout << metrics[i] << '=' << sum[i] / count << '\n';
            std::cout << std::flush;
        }
        CUpti_PmSampling_Stop_Params stop{CUpti_PmSampling_Stop_Params_STRUCT_SIZE};
        stop.pPmSamplingObject = session.sampler; CALL(cuptiPmSamplingStop, stop); session.started = false;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
