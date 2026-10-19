#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <SimConnect.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr int kProtocol = 1;

enum DataDefinition { DEFINITION_AIRCRAFT = 1, DEFINITION_FACILITY = 2 };
enum RequestId { REQUEST_AIRCRAFT = 1, REQUEST_FACILITY = 2 };

struct AircraftData {
    double latitude_deg;
    double longitude_deg;
    double altitude_ft;
    double pitch_deg;
    double bank_deg;
    double heading_true_deg;
};

#pragma pack(push, 1)
// Field order must match add_facility_definitions().
struct RunwayFacilityData {
    double latitude_deg;
    double longitude_deg;
    double altitude_m;
    float heading_deg;
    float length_m;
    float width_m;
    float slope_deg;
    float true_slope_deg;
    std::int32_t primary_number;
    std::int32_t primary_designator;
    std::int32_t secondary_number;
    std::int32_t secondary_designator;
    std::int8_t edge_lights;
    std::int8_t center_lights;
};
struct PavementFacilityData {
    float length_m;
    float width_m;
    std::int32_t enabled;
};
struct ApproachLightsFacilityData {
    std::int32_t system;
    std::int32_t strobe_count;
    std::int32_t has_end_lights;
    std::int32_t has_reil_lights;
    std::int32_t has_touchdown_lights;
    std::int32_t on_ground;
    std::int32_t enabled;
    float offset_m;
    float spacing_m;
    float slope_deg;
};
#pragma pack(pop)
static_assert(sizeof(RunwayFacilityData) == 62, "Unexpected facility data layout");
static_assert(sizeof(PavementFacilityData) == 12, "Unexpected pavement data layout");
static_assert(sizeof(ApproachLightsFacilityData) == 40, "Unexpected approach-light data layout");

struct RunwayFacilityRecord {
    DWORD unique_id;
    RunwayFacilityData data;
};
struct PavementFacilityRecord {
    DWORD unique_id;
    DWORD parent_unique_id;
    DWORD item_index;
    DWORD list_size;
    PavementFacilityData data;
};
struct ApproachLightsFacilityRecord {
    DWORD unique_id;
    DWORD parent_unique_id;
    DWORD item_index;
    DWORD list_size;
    ApproachLightsFacilityData data;
};

struct Command {
    std::string verb;
    std::string token;
    double pitch = 0;
    double bank = 0;
    double heading = 0;
    double fov_rad = 0;
    double x = 0;
    double y = 0;
    double z = 0;
    double target_x = 0;
    double target_y = 0;
    double target_z = 0;
    std::string icao;
};

HANDLE g_simconnect = nullptr;
std::atomic<bool> g_quit{false};
std::mutex g_commands_mutex;
std::queue<Command> g_commands;

bool g_ready_sent = false;
DWORD g_acquired_state = SIMCONNECT_CAMERA_NOT_ACQUIRED;
bool g_game_controlled = true;

bool g_snapshot_pending = false;
std::string g_snapshot_token;
bool g_snapshot_has_status = false;
bool g_snapshot_has_camera = false;
bool g_snapshot_has_aircraft = false;
SIMCONNECT_DATA_CAMERA g_camera{};
AircraftData g_aircraft{};

bool g_facility_pending = false;
std::string g_facility_token;
std::string g_facility_icao;
std::vector<RunwayFacilityRecord> g_facility_runways;
std::vector<PavementFacilityRecord> g_facility_pavements;
std::vector<ApproachLightsFacilityRecord> g_facility_approach_lights;

std::string utc_now() {
    SYSTEMTIME value{};
    GetSystemTime(&value);
    std::ostringstream out;
    out << std::setfill('0') << value.wYear << '-'
        << std::setw(2) << value.wMonth << '-' << std::setw(2) << value.wDay << 'T'
        << std::setw(2) << value.wHour << ':' << std::setw(2) << value.wMinute << ':'
        << std::setw(2) << value.wSecond << '.' << std::setw(3) << value.wMilliseconds << 'Z';
    return out.str();
}

long long steady_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

void emit_error(const std::string& message, const std::string& token = "") {
    std::cout << "{\"event\":\"error\",\"protocol\":" << kProtocol;
    if (!token.empty()) std::cout << ",\"token\":\"" << token << "\"";
    // All messages passed here are fixed ASCII strings or HRESULT numbers.
    std::cout << ",\"message\":\"" << message << "\"}" << std::endl;
}

bool succeeded(HRESULT result, const char* operation, const std::string& token = "") {
    if (SUCCEEDED(result)) return true;
    std::ostringstream message;
    message << operation << " failed (HRESULT 0x" << std::hex
            << static_cast<unsigned long>(result) << ')';
    emit_error(message.str(), token);
    return false;
}

void maybe_emit_snapshot() {
    if (!g_snapshot_pending || !g_snapshot_has_status || !g_snapshot_has_camera ||
        !g_snapshot_has_aircraft) return;
    std::cout << std::setprecision(15)
        << "{\"event\":\"snapshot\",\"protocol\":" << kProtocol
        << ",\"token\":\"" << g_snapshot_token << "\""
        << ",\"utc\":\"" << utc_now() << "\""
        << ",\"steady_ns\":" << steady_ns()
        << ",\"status\":{\"acquired_state\":" << g_acquired_state
        << ",\"game_controlled\":" << (g_game_controlled ? "true" : "false") << "}"
        << ",\"camera\":{\"x\":" << g_camera.Position.x
        << ",\"y\":" << g_camera.Position.y << ",\"z\":" << g_camera.Position.z
        << ",\"position_referential\":" << static_cast<int>(g_camera.PositionReferential)
        << ",\"target_x\":" << g_camera.TargetedPos.x
        << ",\"target_y\":" << g_camera.TargetedPos.y
        << ",\"target_z\":" << g_camera.TargetedPos.z
        << ",\"pitch_deg\":" << g_camera.Pbh.Pitch
        << ",\"bank_deg\":" << g_camera.Pbh.Bank
        << ",\"heading_deg\":" << g_camera.Pbh.Heading
        << ",\"rotation_referential\":" << static_cast<int>(g_camera.RotationReferential)
        << ",\"fov_rad\":" << g_camera.Fov << "}"
        << ",\"aircraft\":{\"latitude_deg\":" << g_aircraft.latitude_deg
        << ",\"longitude_deg\":" << g_aircraft.longitude_deg
        << ",\"altitude_ft\":" << g_aircraft.altitude_ft
        << ",\"pitch_deg\":" << g_aircraft.pitch_deg
        << ",\"bank_deg\":" << g_aircraft.bank_deg
        << ",\"heading_true_deg\":" << g_aircraft.heading_true_deg << "}}"
        << std::endl;
    g_snapshot_pending = false;
}

void emit_facility() {
    std::cout << std::setprecision(15)
              << "{\"event\":\"facility\",\"protocol\":" << kProtocol
              << ",\"token\":\"" << g_facility_token << "\""
              << ",\"icao\":\"" << g_facility_icao << "\",\"runways\":[";
    for (std::size_t i = 0; i < g_facility_runways.size(); ++i) {
        const auto& record = g_facility_runways[i];
        const auto& r = record.data;
        if (i) std::cout << ',';
        std::cout << "{\"unique_id\":" << record.unique_id
                  << ",\"latitude_deg\":" << r.latitude_deg
                  << ",\"longitude_deg\":" << r.longitude_deg
                  << ",\"altitude_m\":" << r.altitude_m
                  << ",\"heading_deg\":" << r.heading_deg
                  << ",\"length_m\":" << r.length_m
                  << ",\"width_m\":" << r.width_m
                  << ",\"slope_deg\":" << r.slope_deg
                  << ",\"true_slope_deg\":" << r.true_slope_deg
                  << ",\"primary_number\":" << r.primary_number
                  << ",\"primary_designator\":" << r.primary_designator
                  << ",\"secondary_number\":" << r.secondary_number
                  << ",\"secondary_designator\":" << r.secondary_designator
                  << ",\"edge_lights\":" << static_cast<int>(r.edge_lights)
                  << ",\"center_lights\":" << static_cast<int>(r.center_lights) << '}';
    }
    std::cout << "],\"pavements\":[";
    for (std::size_t i = 0; i < g_facility_pavements.size(); ++i) {
        const auto& record = g_facility_pavements[i];
        if (i) std::cout << ',';
        std::cout << "{\"unique_id\":" << record.unique_id
                  << ",\"parent_unique_id\":" << record.parent_unique_id
                  << ",\"item_index\":" << record.item_index
                  << ",\"list_size\":" << record.list_size
                  << ",\"length_m\":" << record.data.length_m
                  << ",\"width_m\":" << record.data.width_m
                  << ",\"enabled\":" << (record.data.enabled ? "true" : "false") << '}';
    }
    std::cout << "],\"approach_lights\":[";
    for (std::size_t i = 0; i < g_facility_approach_lights.size(); ++i) {
        const auto& record = g_facility_approach_lights[i];
        const auto& a = record.data;
        if (i) std::cout << ',';
        std::cout << "{\"unique_id\":" << record.unique_id
                  << ",\"parent_unique_id\":" << record.parent_unique_id
                  << ",\"item_index\":" << record.item_index
                  << ",\"list_size\":" << record.list_size
                  << ",\"system\":" << a.system
                  << ",\"strobe_count\":" << a.strobe_count
                  << ",\"has_end_lights\":" << (a.has_end_lights ? "true" : "false")
                  << ",\"has_reil_lights\":" << (a.has_reil_lights ? "true" : "false")
                  << ",\"has_touchdown_lights\":" << (a.has_touchdown_lights ? "true" : "false")
                  << ",\"on_ground\":" << (a.on_ground ? "true" : "false")
                  << ",\"enabled\":" << (a.enabled ? "true" : "false")
                  << ",\"offset_m\":" << a.offset_m
                  << ",\"spacing_m\":" << a.spacing_m
                  << ",\"slope_deg\":" << a.slope_deg << '}';
    }
    std::cout << "]}" << std::endl;
    g_facility_pending = false;
}

void CALLBACK dispatch(SIMCONNECT_RECV* data, DWORD, void*) {
    switch (data->dwID) {
    case SIMCONNECT_RECV_ID_CAMERA_STATUS: {
        const auto* status = reinterpret_cast<SIMCONNECT_RECV_CAMERA_STATUS*>(data);
        g_acquired_state = status->acquiredState;
        g_game_controlled = status->bGameControlled != FALSE;
        if (!g_ready_sent && g_acquired_state == SIMCONNECT_CAMERA_ACQUIRED) {
            std::cout << "{\"event\":\"ready\",\"protocol\":" << kProtocol
                      << ",\"camera_acquired\":true}" << std::endl;
            g_ready_sent = true;
        } else if (!g_ready_sent && g_acquired_state == SIMCONNECT_CAMERA_USER_DISABLED) {
            emit_error("MSFS Camera API is disabled in the simulator options");
            g_quit = true;
        } else if (!g_ready_sent && g_acquired_state == SIMCONNECT_CAMERA_ACQUIRED_BY_OTHER) {
            emit_error("The add-on camera is owned by another client");
            g_quit = true;
        }
        if (g_snapshot_pending) g_snapshot_has_status = true;
        maybe_emit_snapshot();
        break;
    }
    case SIMCONNECT_RECV_ID_CAMERA_DATA: {
        const auto* camera = reinterpret_cast<SIMCONNECT_RECV_CAMERA_DATA*>(data);
        g_camera = camera->CameraData;
        if (g_snapshot_pending) g_snapshot_has_camera = true;
        maybe_emit_snapshot();
        break;
    }
    case SIMCONNECT_RECV_ID_SIMOBJECT_DATA: {
        const auto* response = reinterpret_cast<SIMCONNECT_RECV_SIMOBJECT_DATA*>(data);
        if (response->dwRequestID == REQUEST_AIRCRAFT) {
            g_aircraft = *reinterpret_cast<const AircraftData*>(&response->dwData);
            if (g_snapshot_pending) g_snapshot_has_aircraft = true;
            maybe_emit_snapshot();
        }
        break;
    }
    case SIMCONNECT_RECV_ID_FACILITY_DATA: {
        const auto* response = reinterpret_cast<SIMCONNECT_RECV_FACILITY_DATA*>(data);
        if (g_facility_pending && response->UserRequestId == REQUEST_FACILITY &&
            response->Type == SIMCONNECT_FACILITY_DATA_RUNWAY) {
            RunwayFacilityData runway{};
            std::memcpy(&runway, &response->Data, sizeof(runway));
            g_facility_runways.push_back({response->UniqueRequestId, runway});
        } else if (g_facility_pending && response->UserRequestId == REQUEST_FACILITY &&
                   response->Type == SIMCONNECT_FACILITY_DATA_PAVEMENT) {
            PavementFacilityData pavement{};
            std::memcpy(&pavement, &response->Data, sizeof(pavement));
            g_facility_pavements.push_back({response->UniqueRequestId,
                response->ParentUniqueRequestId, response->ItemIndex, response->ListSize, pavement});
        } else if (g_facility_pending && response->UserRequestId == REQUEST_FACILITY &&
                   response->Type == SIMCONNECT_FACILITY_DATA_APPROACH_LIGHTS) {
            ApproachLightsFacilityData approach{};
            std::memcpy(&approach, &response->Data, sizeof(approach));
            g_facility_approach_lights.push_back({response->UniqueRequestId,
                response->ParentUniqueRequestId, response->ItemIndex, response->ListSize, approach});
        }
        break;
    }
    case SIMCONNECT_RECV_ID_FACILITY_DATA_END: {
        const auto* response = reinterpret_cast<SIMCONNECT_RECV_FACILITY_DATA_END*>(data);
        if (g_facility_pending && response->RequestId == REQUEST_FACILITY) emit_facility();
        break;
    }
    case SIMCONNECT_RECV_ID_EXCEPTION: {
        const auto* error = reinterpret_cast<SIMCONNECT_RECV_EXCEPTION*>(data);
        std::ostringstream message;
        message << "SimConnect exception " << error->dwException
                << " (send " << error->dwSendID << ", index " << error->dwIndex << ')';
        emit_error(message.str(), g_snapshot_pending ? g_snapshot_token : "");
        g_snapshot_pending = false;
        g_facility_pending = false;
        break;
    }
    case SIMCONNECT_RECV_ID_QUIT:
        emit_error("MSFS closed the SimConnect session");
        g_quit = true;
        break;
    default:
        break;
    }
}

void read_commands() {
    std::string line;
    while (!g_quit && std::getline(std::cin, line)) {
        std::istringstream input(line);
        Command command;
        input >> command.verb;
        if (command.verb == "QUIT") {
            g_quit = true;
            break;
        }
        if (command.verb == "SNAPSHOT") {
            if (!(input >> command.token)) continue;
        } else if (command.verb == "SET") {
            if (!(input >> command.token >> command.pitch >> command.bank >> command.heading
                        >> command.fov_rad >> command.x >> command.y >> command.z)) continue;
        } else if (command.verb == "SET_TARGET") {
            if (!(input >> command.token >> command.fov_rad >> command.x >> command.y >> command.z
                        >> command.target_x >> command.target_y >> command.target_z)) continue;
        } else if (command.verb == "FACILITY") {
            if (!(input >> command.token >> command.icao)) continue;
        } else {
            continue;
        }
        std::lock_guard<std::mutex> lock(g_commands_mutex);
        g_commands.push(command);
    }
    // Parent process ended without QUIT: release the camera instead of leaving
    // an orphan helper running indefinitely.
    g_quit = true;
}

void process_command(const Command& command) {
    if (command.verb == "SET" || command.verb == "SET_TARGET") {
        if (g_acquired_state != SIMCONNECT_CAMERA_ACQUIRED || g_game_controlled) {
            emit_error("Camera cannot be set because it is unavailable or game-controlled", command.token);
            return;
        }
        SIMCONNECT_DATA_CAMERA camera{};
        camera.Position = {command.x, command.y, command.z};
        camera.PositionReferential = SIMCONNECT_POSITION_REFERENTIAL_EYEPOINT;
        camera.PositionReferentialObjectId = 0;
        camera.RotationReferential = SIMCONNECT_POSITION_REFERENTIAL_WORLD;
        camera.RotationReferentialObjectId = 0;
        camera.Fov = command.fov_rad;
        DWORD mask = SIMCONNECT_CAMERA_DATA_MASK_ALL_ROTATION;
        if (command.verb == "SET_TARGET") {
            camera.TargetedPos = {command.target_x, command.target_y, command.target_z};
            mask = SIMCONNECT_CAMERA_DATA_MASK_ALL_TARGETED;
        } else {
            camera.Pbh.Pitch = static_cast<float>(command.pitch);
            camera.Pbh.Bank = static_cast<float>(command.bank);
            camera.Pbh.Heading = static_cast<float>(command.heading);
        }
        const auto result = SimConnect_CameraSet(g_simconnect, camera, mask);
        if (!succeeded(result, "SimConnect_CameraSet", command.token)) return;
        std::cout << "{\"event\":\"set\",\"protocol\":" << kProtocol
                  << ",\"token\":\"" << command.token << "\",\"ok\":true}" << std::endl;
        return;
    }

    if (command.verb == "FACILITY") {
        if (g_facility_pending) {
            emit_error("A facility request is already pending", command.token);
            return;
        }
        g_facility_pending = true;
        g_facility_token = command.token;
        g_facility_icao = command.icao;
        g_facility_runways.clear();
        g_facility_pavements.clear();
        g_facility_approach_lights.clear();
        const auto result = SimConnect_RequestFacilityData(
            g_simconnect, DEFINITION_FACILITY, REQUEST_FACILITY, command.icao.c_str(), "");
        if (!succeeded(result, "SimConnect_RequestFacilityData", command.token)) {
            g_facility_pending = false;
        }
        return;
    }

    if (g_snapshot_pending) {
        emit_error("A camera snapshot request is already pending", command.token);
        return;
    }
    g_snapshot_pending = true;
    g_snapshot_token = command.token;
    g_snapshot_has_status = g_snapshot_has_camera = g_snapshot_has_aircraft = false;
    bool ok = true;
    ok &= succeeded(SimConnect_CameraGetStatus(g_simconnect), "SimConnect_CameraGetStatus", command.token);
    ok &= succeeded(SimConnect_CameraGet(g_simconnect, SIMCONNECT_POSITION_REFERENTIAL_WORLD),
                    "SimConnect_CameraGet", command.token);
    ok &= succeeded(SimConnect_RequestDataOnSimObject(
                        g_simconnect, REQUEST_AIRCRAFT, DEFINITION_AIRCRAFT,
                        SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_PERIOD_ONCE),
                    "SimConnect_RequestDataOnSimObject", command.token);
    if (!ok) g_snapshot_pending = false;
}

bool add_aircraft_definitions() {
    return
        succeeded(SimConnect_AddToDataDefinition(g_simconnect, DEFINITION_AIRCRAFT,
                  "PLANE LATITUDE", "degrees", SIMCONNECT_DATATYPE_FLOAT64), "Define latitude") &&
        succeeded(SimConnect_AddToDataDefinition(g_simconnect, DEFINITION_AIRCRAFT,
                  "PLANE LONGITUDE", "degrees", SIMCONNECT_DATATYPE_FLOAT64), "Define longitude") &&
        succeeded(SimConnect_AddToDataDefinition(g_simconnect, DEFINITION_AIRCRAFT,
                  "PLANE ALTITUDE", "feet", SIMCONNECT_DATATYPE_FLOAT64), "Define altitude") &&
        succeeded(SimConnect_AddToDataDefinition(g_simconnect, DEFINITION_AIRCRAFT,
                  "PLANE PITCH DEGREES", "degrees", SIMCONNECT_DATATYPE_FLOAT64), "Define pitch") &&
        succeeded(SimConnect_AddToDataDefinition(g_simconnect, DEFINITION_AIRCRAFT,
                  "PLANE BANK DEGREES", "degrees", SIMCONNECT_DATATYPE_FLOAT64), "Define bank") &&
        succeeded(SimConnect_AddToDataDefinition(g_simconnect, DEFINITION_AIRCRAFT,
                  "PLANE HEADING DEGREES TRUE", "degrees", SIMCONNECT_DATATYPE_FLOAT64), "Define heading");
}

bool add_facility_definitions() {
    const char* fields[] = {
        "OPEN AIRPORT", "OPEN RUNWAY",
        "LATITUDE", "LONGITUDE", "ALTITUDE", "HEADING", "LENGTH", "WIDTH",
        "SLOPE", "TRUE_SLOPE", "PRIMARY_NUMBER", "PRIMARY_DESIGNATOR",
        "SECONDARY_NUMBER", "SECONDARY_DESIGNATOR", "EDGE_LIGHTS", "CENTER_LIGHTS",
        "OPEN PRIMARY_THRESHOLD", "LENGTH", "WIDTH", "ENABLE", "CLOSE PRIMARY_THRESHOLD",
        "OPEN SECONDARY_THRESHOLD", "LENGTH", "WIDTH", "ENABLE", "CLOSE SECONDARY_THRESHOLD",
        "OPEN PRIMARY_APPROACH_LIGHTS", "SYSTEM", "STROBE_COUNT", "HAS_END_LIGHTS",
        "HAS_REIL_LIGHTS", "HAS_TOUCHDOWN_LIGHTS", "ON_GROUND", "ENABLE",
        "OFFSET", "SPACING", "SLOPE", "CLOSE PRIMARY_APPROACH_LIGHTS",
        "OPEN SECONDARY_APPROACH_LIGHTS", "SYSTEM", "STROBE_COUNT", "HAS_END_LIGHTS",
        "HAS_REIL_LIGHTS", "HAS_TOUCHDOWN_LIGHTS", "ON_GROUND", "ENABLE",
        "OFFSET", "SPACING", "SLOPE", "CLOSE SECONDARY_APPROACH_LIGHTS",
        "CLOSE RUNWAY", "CLOSE AIRPORT"
    };
    for (const char* field : fields) {
        if (!succeeded(SimConnect_AddToFacilityDefinition(
                g_simconnect, DEFINITION_FACILITY, field), "Define facility field")) return false;
    }
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    SetConsoleOutputCP(CP_UTF8);
    std::string client_id = "ODM Runway Capture";
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--client-id") client_id = argv[++i];
    }
    if (!succeeded(SimConnect_Open(&g_simconnect, "ODM Native Camera Bridge",
                                   nullptr, 0, nullptr, 0), "SimConnect_Open")) return 2;
    if (!add_aircraft_definitions() || !add_facility_definitions()) {
        SimConnect_Close(g_simconnect);
        return 2;
    }
    if (!succeeded(SimConnect_CameraAcquire(g_simconnect, client_id.c_str()),
                   "SimConnect_CameraAcquire")) {
        SimConnect_Close(g_simconnect);
        return 2;
    }

    std::thread input_thread(read_commands);
    input_thread.detach();
    while (!g_quit) {
        SimConnect_CallDispatch(g_simconnect, dispatch, nullptr);
        while (true) {
            Command command;
            {
                std::lock_guard<std::mutex> lock(g_commands_mutex);
                if (g_commands.empty()) break;
                command = g_commands.front();
                g_commands.pop();
            }
            process_command(command);
        }
        Sleep(1);
    }
    if (g_acquired_state == SIMCONNECT_CAMERA_ACQUIRED) {
        SimConnect_CameraRelease(g_simconnect, "");
    }
    SimConnect_Close(g_simconnect);
    return 0;
}
