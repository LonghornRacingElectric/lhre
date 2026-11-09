use anyhow::Result;
use prost::Message;
use std::collections::HashMap;
#[cfg(target_os = "linux")]
use std::io::Read;
use std::io::{BufRead, BufReader};
use std::net::{TcpListener, UdpSocket};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use sensor_proto::config::PacketConfig;
use sensor_proto::generated_mapping;
use sensor_proto::native::{self, Fanout, FieldPath, Sampler};
use sensor_proto::proto::artemis::ArtemisSensorData;

#[cfg(target_os = "linux")]
use socketcan::{CanFrame, CanSocket, EmbeddedFrame, Id, Socket, StandardId};

//use std::process::Command;

const MOCK_ADDR_0: &str = "127.0.0.1:5005";
const MOCK_ADDR_1: &str = "127.0.0.1:5006";
const SOCKET_PATH: &str = "/tmp/BEVO_cand.sock";
/// CAN transmit request socket. dashd connects here and sends frame requests;
/// cand owns the only write socket on the critical bus, so it stays the sole
/// CAN writer. Wire format below in `can_tx_listener_loop`.
#[cfg(target_os = "linux")]
const CAND_TX_SOCKET_PATH: &str = "/tmp/BEVO_cand_tx.sock";
const CAN_INTERFACE_0: &str = "can0";
const CAN_INTERFACE_1: &str = "can1";
const DEFAULT_PUBLISH_HZ: u64 = 100;
const DEFAULT_NMEA_LISTEN_ADDR: &str = "0.0.0.0:2000";

const TRACKED_BOARD_SOURCES: [&str; 8] =
    ["CSM", "DUI", "HVC", "Inverter", "PDU", "TSM", "USM", "VCU"];

#[derive(Debug)]
struct RawCanMessage {
    id: u32,
    payload: Vec<u8>,
    sample_time_ns: i64,
}

#[derive(Debug, Clone, Default)]
struct ExternalDynamicsData {
    gps: Vec<f32>,
    gps_imu: Vec<f32>,
    gps_speed: f32,
    gps_precise: Vec<f64>,
}

fn main() -> Result<()> {
    run(false)
}

pub fn run(force_mock: bool) -> Result<()> {
    let use_mock = force_mock
        || cfg!(not(target_os = "linux"))
        || matches!(
            std::env::var("CAND_USE_MOCK")
                .ok()
                .as_deref()
                .map(|value| value.to_ascii_lowercase()),
            Some(value) if value == "1" || value == "true" || value == "yes"
        );
    let can_interface_0 =
        std::env::var("CAND_CAN_INTERFACE_0").unwrap_or_else(|_| CAN_INTERFACE_0.to_string());
    let can_interface_1 =
        std::env::var("CAND_CAN_INTERFACE_1").unwrap_or_else(|_| CAN_INTERFACE_1.to_string());
    let publish_hz = std::env::var("CAND_PUBLISH_HZ")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_PUBLISH_HZ);

    let packets = sensor_proto::config::packets();
    let packet_source_lookup = Arc::new(build_packet_source_lookup(&packets));
    let board_last_seen = Arc::new(Mutex::new(default_board_last_seen_map()));
    let fanout = Fanout::bind()?;

    // This is where CAN IDs are mapped to their JSON configuration
    let packet_map = Arc::new(
        packets
            .into_iter()
            .map(|p| (p.packet_id, p))
            .collect::<HashMap<u32, PacketConfig>>(),
    );

    let sensor_data_cache = Arc::new(Mutex::new(ArtemisSensorData::default()));
    let external_dynamics = Arc::new(Mutex::new(ExternalDynamicsData::default()));
    let (raw_tx, raw_rx) = mpsc::channel::<RawCanMessage>();

    let can_packet_map = Arc::clone(&packet_map);
    #[cfg(target_os = "linux")]
    let can_interface_0_clone = can_interface_0.clone();
    #[cfg(target_os = "linux")]
    let can_interface_1_clone = can_interface_1.clone();

    if use_mock {
        let can_reader_tx_0 = raw_tx.clone();
        thread::spawn(move || {
            if let Err(e) = mock_can_reader_loop(can_reader_tx_0, MOCK_ADDR_0) {
                eprintln!("[CAND-CAN] Error: {:?}", e);
            }
        });

        let can_reader_tx_1 = raw_tx.clone();
        thread::spawn(move || {
            if let Err(e) = mock_can_reader_loop(can_reader_tx_1, MOCK_ADDR_1) {
                eprintln!("[CAND-CAN] Error: {:?}", e);
            }
        });
    } else {
        #[cfg(target_os = "linux")]
        {
            let can_reader_tx_0 = raw_tx.clone();
            thread::spawn(move || {
                if let Err(e) = can_socket_reader_loop(can_reader_tx_0, can_interface_0_clone) {
                    eprintln!("[CAND-CAN-0] Error: {:?}", e);
                }
            });

            let can_reader_tx_1 = raw_tx.clone();
            thread::spawn(move || {
                if let Err(e) = can_socket_reader_loop(can_reader_tx_1, can_interface_1_clone) {
                    eprintln!("[CAND-CAN-1] Error: {:?}", e);
                }
            });

            // CAN transmit path on the critical bus (can0) — where the VCU listens.
            // Single writer thread owns the only TX socket; dashd feeds it frame
            // requests over CAND_TX_SOCKET_PATH (e.g. the 0x029 VCU Mode Command).
            let can_interface_tx = can_interface_0.clone();
            thread::spawn(move || {
                if let Err(e) = can_tx_listener_loop(can_interface_tx) {
                    eprintln!("[CAND-TX] Error: {:?}", e);
                }
            });
        }
    }

    let processing_sensor_data = Arc::clone(&sensor_data_cache);
    let processing_packet_sources = Arc::clone(&packet_source_lookup);
    let processing_board_last_seen = Arc::clone(&board_last_seen);
    let processing_fanout = fanout.clone();
    thread::spawn(move || {
        can_processing_loop(
            processing_sensor_data,
            can_packet_map,
            processing_packet_sources,
            processing_board_last_seen,
            raw_rx,
            processing_fanout,
        );
    });

    let nmea_external_dynamics = Arc::clone(&external_dynamics);
    let nmea_listen_addr = std::env::var("CAND_NMEA_LISTEN_ADDR")
        .unwrap_or_else(|_| DEFAULT_NMEA_LISTEN_ADDR.to_string());
    let nmea_fanout = fanout.clone();
    thread::spawn(move || {
        if let Err(e) = nmea_listener_loop(nmea_external_dynamics, nmea_listen_addr, nmea_fanout) {
            eprintln!("[CAND-NMEA] Error: {:?}", e);
        }
    });

    let ipc_sensor_data = Arc::clone(&sensor_data_cache);
    let ipc_external_dynamics = Arc::clone(&external_dynamics);
    let ipc_board_last_seen = Arc::clone(&board_last_seen);
    thread::spawn(move || {
        if let Err(e) = ipc_server_loop(
            ipc_sensor_data,
            ipc_external_dynamics,
            ipc_board_last_seen,
            publish_hz,
            fanout,
        ) {
            eprintln!("[CAND-IPC] Error: {:?}", e);
        }
    });

    if use_mock {
        println!(
            "[CAND] Started in MOCK mode ({}, {}) @ {} Hz",
            MOCK_ADDR_0, MOCK_ADDR_1, publish_hz
        );
    } else {
        println!(
            "[CAND] Started in REAL mode ({}, {}) @ {} Hz",
            can_interface_0, can_interface_1, publish_hz
        );
    }
    loop {
        thread::park();
    }
}

fn is_tracked_board_source(source: &str) -> bool {
    TRACKED_BOARD_SOURCES
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(source))
}

fn canonical_board_name(source: &str) -> Option<&'static str> {
    TRACKED_BOARD_SOURCES
        .iter()
        .find(|candidate| candidate.eq_ignore_ascii_case(source))
        .copied()
}

fn packet_source(packet: &PacketConfig) -> Option<String> {
    packet
        .from
        .iter()
        .find_map(|source| canonical_board_name(source))
        .map(ToString::to_string)
}

fn build_packet_source_lookup(packets: &[PacketConfig]) -> HashMap<u32, String> {
    let mut source_lookup = HashMap::new();
    for packet in packets {
        let Some(source) = packet_source(packet) else {
            continue;
        };
        let quantity = packet.quantity.max(1);
        for offset in 0..quantity {
            let packet_id = packet.packet_id.saturating_add(offset);
            source_lookup.insert(packet_id, source.clone());
        }
    }
    source_lookup
}

fn default_board_last_seen_map() -> HashMap<String, f32> {
    TRACKED_BOARD_SOURCES
        .iter()
        .map(|board| (board.to_string(), -1.0_f32))
        .collect()
}

fn mark_board_seen(
    board_last_seen: &Arc<Mutex<HashMap<String, f32>>>,
    packet_sources: &HashMap<u32, String>,
    packet_id: u32,
) {
    let Some(source) = packet_sources.get(&packet_id) else {
        return;
    };
    if !is_tracked_board_source(source) {
        return;
    }
    if let Some(value) = board_last_seen.lock().unwrap().get_mut(source) {
        *value = 0.0;
    }
}

fn tick_board_last_seen(
    board_last_seen: &Arc<Mutex<HashMap<String, f32>>>,
    elapsed_seconds: f32,
) -> HashMap<String, f32> {
    let mut locked = board_last_seen.lock().unwrap();
    if elapsed_seconds > 0.0 {
        for value in locked.values_mut() {
            if *value >= 0.0 {
                *value += elapsed_seconds;
            }
        }
    }
    locked.clone()
}

// continuously reads CAN frames from the specified socketCAN interface and
// sends them to the processing thread over a channel.

#[cfg(target_os = "linux")]
fn can_socket_reader_loop(raw_tx: Sender<RawCanMessage>, can_interface: String) -> Result<()> {
    let socket = CanSocket::open(&can_interface)?;
    loop {
        let frame = socket.read_frame()?;

        if let Id::Standard(id) = frame.id() {
            let payload = frame.data().to_vec();
            let message = RawCanMessage {
                id: id.as_raw() as u32,
                payload,
                sample_time_ns: native::now_ns(),
            };
            if raw_tx.send(message).is_err() {
                return Ok(());
            }
        }
    }
}

// Single-writer CAN transmit path. dashd connects to CAND_TX_SOCKET_PATH and
// sends frame requests; we own the only write socket on `can_interface` so cand
// stays the sole CAN writer. One write socket is opened up front and reused.
//
// Wire format per request (13 bytes, fixed):
//   [0..4]  CAN id   (u32, little-endian; standard 11-bit)
//   [4]     dlc      (u8, clamped 0..=8)
//   [5..13] data     (8 bytes; only the first `dlc` are transmitted)
// A client may send multiple back-to-back requests on one connection.
#[cfg(target_os = "linux")]
fn can_tx_listener_loop(can_interface: String) -> Result<()> {
    let _ = std::fs::remove_file(CAND_TX_SOCKET_PATH);
    let listener = UnixListener::bind(CAND_TX_SOCKET_PATH)?;
    let socket = CanSocket::open(&can_interface)?;
    println!(
        "[CAND-TX] CAN transmit listener on {} -> {}",
        CAND_TX_SOCKET_PATH, can_interface
    );
    for stream in listener.incoming() {
        let mut stream = match stream {
            Ok(s) => s,
            Err(_) => continue,
        };
        let mut buf = [0u8; 13];
        // read_exact returns Err at EOF / short read -> connection done.
        while stream.read_exact(&mut buf).is_ok() {
            let id = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
            let dlc = (buf[4] as usize).min(8);
            let data = &buf[5..5 + dlc];
            // Standard CAN ids are 11-bit (<= 0x7FF). Range-check BEFORE casting:
            // `id as u16` would silently truncate a >16-bit / extended id
            // (e.g. 0x10005 -> 0x5) and transmit to the wrong node.
            let sid = match (id <= 0x7FF).then(|| StandardId::new(id as u16)).flatten() {
                Some(s) => s,
                None => {
                    eprintln!("[CAND-TX] rejecting non-standard CAN id {:#x}", id);
                    continue;
                }
            };
            match CanFrame::new(sid, data) {
                Some(frame) => match socket.write_frame(&frame) {
                    Ok(()) => println!(
                        "[CAND-TX] sent CAN {:#05x} dlc={} data={:02x?}",
                        id, dlc, data
                    ),
                    Err(e) => eprintln!("[CAND-TX] write_frame failed: {:?}", e),
                },
                None => eprintln!("[CAND-TX] could not build frame (dlc {})", dlc),
            }
        }
    }
    Ok(())
}

// reads CAN frames from the mock UDP socket used for local testing.

fn mock_can_reader_loop(raw_tx: Sender<RawCanMessage>, mock_addr: &'static str) -> Result<()> {
    let socket = UdpSocket::bind(mock_addr)?;
    println!("[CAND-CAN] Listening on {mock_addr}");
    let mut buf = [0u8; 12];
    loop {
        let (len, _) = socket.recv_from(&mut buf)?;
        if len < 4 {
            continue;
        }

        let id = u32::from_le_bytes(buf[0..4].try_into().unwrap());
        let sample_time_ns = native::now_ns();
        let payload = buf[4..len].to_vec();
        if raw_tx
            .send(RawCanMessage {
                id,
                payload,
                sample_time_ns,
            })
            .is_err()
        {
            return Ok(());
        }
    }
}

// takes raw CAN messages from the reader thread, looks up their config, and updates the proto struct in-place with the new values

fn can_processing_loop(
    data_cache: Arc<Mutex<ArtemisSensorData>>,
    packet_map: Arc<HashMap<u32, PacketConfig>>,
    packet_sources: Arc<HashMap<u32, String>>,
    board_last_seen: Arc<Mutex<HashMap<String, f32>>>,
    raw_rx: Receiver<RawCanMessage>,
    fanout: Fanout,
) {
    let mut sampler = Sampler::default();
    while let Ok(message) = raw_rx.recv() {
        mark_board_seen(&board_last_seen, &packet_sources, message.id);
        if let Some((config, series_index)) = resolve_packet_config(&packet_map, message.id) {
            let mut locked = data_cache.lock().unwrap();
            process_raw_data(&mut locked, &message.payload, config, series_index);
            let update = sampler.can_update(
                message.id,
                message.payload.len(),
                message.sample_time_ns,
                &locked,
            );
            drop(locked);
            if let Some(update) = update {
                fanout.send(update);
            }
        }
    }
}

fn resolve_packet_config<'a>(
    packet_map: &'a HashMap<u32, PacketConfig>,
    message_id: u32,
) -> Option<(&'a PacketConfig, usize)> {
    if let Some(config) = packet_map.get(&message_id) {
        return Some((config, 0));
    }

    packet_map.values().find_map(|config| {
        let quantity = config.quantity.max(1);
        let end_id = config.packet_id.saturating_add(quantity);
        if message_id >= config.packet_id && message_id < end_id {
            Some((config, (message_id - config.packet_id) as usize))
        } else {
            None
        }
    })
}

fn nmea_listener_loop(
    external_dynamics: Arc<Mutex<ExternalDynamicsData>>,
    listen_addr: String,
    fanout: Fanout,
) -> Result<()> {
    let listener = TcpListener::bind(&listen_addr)?;
    println!("[CAND-NMEA] Listening on {}", listen_addr);

    for incoming in listener.incoming() {
        match incoming {
            Ok(stream) => {
                let peer = stream.peer_addr().ok();
                println!("[CAND-NMEA] Client connected: {:?}", peer);
                let reader = BufReader::new(stream);
                for line in reader.lines() {
                    let sentence = match line {
                        Ok(v) => v,
                        Err(e) => {
                            eprintln!("[CAND-NMEA] Read error: {:?}", e);
                            break;
                        }
                    };
                    let trimmed = sentence.trim();
                    let stamp = native::now_ns();
                    let paths = update_external_dynamics_from_sentence(&external_dynamics, trimmed);
                    let mut values = ArtemisSensorData::default();
                    apply_external_dynamics(&mut values, &external_dynamics.lock().unwrap());
                    if let Some(update) = native::update(&values, stamp, paths) {
                        fanout.send(update);
                    }
                }
            }
            Err(e) => {
                eprintln!("[CAND-NMEA] Accept error: {:?}", e);
            }
        }
    }
    Ok(())
}

fn update_external_dynamics_from_sentence(
    external_dynamics: &Arc<Mutex<ExternalDynamicsData>>,
    sentence: &str,
) -> Vec<FieldPath> {
    let mut names = Vec::new();
    if sentence.is_empty() {
        return Vec::new();
    }

    if let Some(precise) = parse_ppos(sentence) {
        external_dynamics.lock().unwrap().gps_precise = precise;
        return dynamics_paths(&["gps_precise"]);
    }

    if let Some((lat, lon)) = parse_nmea_gps(sentence) {
        let mut locked = external_dynamics.lock().unwrap();
        locked.gps.clear();
        locked.gps.push(lat);
        locked.gps.push(lon);
        names.push("gps");
        // no return: RMC also carries speed, fall through to parse_nmea_gps_speed
    }

    if let Some((x, y, z)) = parse_pimu_xyz(sentence) {
        let mut locked = external_dynamics.lock().unwrap();
        locked.gps_imu.clear();
        locked.gps_imu.push(x);
        locked.gps_imu.push(y);
        locked.gps_imu.push(z);
        names.push("gps_imu");
    }

    if let Some(speed) = parse_nmea_gps_speed(sentence) {
        let mut locked = external_dynamics.lock().unwrap();
        locked.gps_speed = speed;
        names.push("gps_speed");
    }
    dynamics_paths(&names)
}

fn dynamics_paths(names: &[&str]) -> Vec<FieldPath> {
    native::FIELDS
        .iter()
        .filter(|f| f.group_name == "dynamics" && names.contains(&f.name))
        .map(|f| FieldPath {
            group: f.group,
            field: f.field,
        })
        .collect()
}

/// `$PPOS,lat,lon,h,sigma_h,src,utc*CS`: ChudPi's best position at full precision
/// (gbx_bestpos.py), as [lat deg, lon deg, ellipsoid height m, 1-sigma horizontal error m,
/// src (1 = standard, 2 = RTK), fix time in Unix s]. Empty while ChudPi has no solution, so
/// an old position is not kept. A sentence with a bad checksum is ignored.
fn parse_ppos(sentence: &str) -> Option<Vec<f64>> {
    let (body, checksum) = sentence.strip_prefix('$')?.split_once('*')?;
    if !body.starts_with("PPOS,")
        || u8::from_str_radix(checksum.trim(), 16).ok()? != body.bytes().fold(0, |a, b| a ^ b)
    {
        return None;
    }
    let values: Vec<f64> = body
        .split(',')
        .skip(1)
        .map_while(|f| f.parse().ok())
        .collect();
    Some(if values.len() == 6 {
        values
    } else {
        Vec::new()
    })
}

fn parse_nmea_gps(sentence: &str) -> Option<(f32, f32)> {
    if !sentence.starts_with('$') {
        return None;
    }
    let payload = sentence.split('*').next()?;
    let fields: Vec<&str> = payload.split(',').collect();
    let msg = fields.first()?;

    let (lat_raw, lat_hemi, lon_raw, lon_hemi) = if msg.ends_with("GGA") {
        (
            *fields.get(2)?,
            *fields.get(3)?,
            *fields.get(4)?,
            *fields.get(5)?,
        )
    } else if msg.ends_with("RMC") {
        (
            *fields.get(3)?,
            *fields.get(4)?,
            *fields.get(5)?,
            *fields.get(6)?,
        )
    } else {
        return None;
    };

    let lat = nmea_coord_to_decimal(lat_raw, lat_hemi)?;
    let lon = nmea_coord_to_decimal(lon_raw, lon_hemi)?;
    Some((lat, lon))
}

fn nmea_coord_to_decimal(raw: &str, hemi: &str) -> Option<f32> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }
    let value: f64 = raw.parse().ok()?;
    let degrees = (value / 100.0).floor();
    let minutes = value - (degrees * 100.0);
    let mut decimal = degrees + minutes / 60.0;

    let hemi_upper = hemi.trim().to_ascii_uppercase();
    if hemi_upper == "S" || hemi_upper == "W" {
        decimal *= -1.0;
    }
    Some(decimal as f32)
}

fn parse_pimu_xyz(sentence: &str) -> Option<(f32, f32, f32)> {
    if !sentence.starts_with('$') {
        return None;
    }
    let payload = sentence.split('*').next()?;
    let fields: Vec<&str> = payload.split(',').collect();
    let msg = fields.first()?;

    if !msg.ends_with("PIMU") {
        return None;
    }

    // PIMU format: $PIMU,time,tIndex,ax,ay,az,...
    let x: f32 = fields.get(3)?.trim().parse().ok()?;
    let y: f32 = fields.get(4)?.trim().parse().ok()?;
    let z: f32 = fields.get(5)?.trim().parse().ok()?;
    Some((x, y, z))
}

fn parse_nmea_gps_speed(sentence: &str) -> Option<f32> {
    if !sentence.starts_with('$') {
        return None;
    }
    let payload = sentence.split('*').next()?;
    let fields: Vec<&str> = payload.split(',').collect();
    let msg = fields.first()?;

    let speed_knots = if msg.ends_with("RMC") {
        // RMC format: field[7] is speed in knots
        *fields.get(7)?
    } else if msg.ends_with("VTG") {
        // VTG format: field[5] is speed in knots
        *fields.get(5)?
    } else {
        return None;
    };

    let speed: f32 = speed_knots.trim().parse().ok()?;
    Some(speed)
}

fn apply_external_dynamics(data: &mut ArtemisSensorData, external_dynamics: &ExternalDynamicsData) {
    let dynamics = data.dynamics.get_or_insert_with(Default::default);
    dynamics.gps = external_dynamics.gps.clone();
    dynamics.gps_imu = external_dynamics.gps_imu.clone();
    dynamics.gps_speed = external_dynamics.gps_speed;
    dynamics.gps_precise = external_dynamics.gps_precise.clone();
}

fn board_last_seen_value(board_last_seen: &HashMap<String, f32>, board: &str) -> f32 {
    board_last_seen.get(board).copied().unwrap_or(-1.0)
}

fn apply_board_last_seen(data: &mut ArtemisSensorData, board_last_seen: &HashMap<String, f32>) {
    let board_status = data.board_status.get_or_insert_with(Default::default);
    board_status.csm_last_seen_s = board_last_seen_value(board_last_seen, "CSM");
    board_status.dui_last_seen_s = board_last_seen_value(board_last_seen, "DUI");
    board_status.hvc_last_seen_s = board_last_seen_value(board_last_seen, "HVC");
    board_status.inverter_last_seen_s = board_last_seen_value(board_last_seen, "Inverter");
    board_status.pdu_last_seen_s = board_last_seen_value(board_last_seen, "PDU");
    board_status.tsm_last_seen_s = board_last_seen_value(board_last_seen, "TSM");
    board_status.usm_last_seen_s = board_last_seen_value(board_last_seen, "USM");
    board_status.vcu_last_seen_s = board_last_seen_value(board_last_seen, "VCU");
}

// takes raw CAN data + the JSON config for that CAN ID, updates the proto struct in-place with the new values

fn repeated_field_span(config: &PacketConfig) -> usize {
    config
        .bytes
        .iter()
        .filter_map(|signal| {
            signal
                .protobuf
                .as_ref()
                .filter(|protobuf| protobuf.repeated)
                .and_then(|protobuf| protobuf.field_index)
        })
        .max()
        .map(|index| index + 1)
        .unwrap_or(0)
}

fn process_raw_data(
    data: &mut ArtemisSensorData,
    payload: &[u8],
    config: &PacketConfig,
    series_index: usize,
) {
    let repeated_span = repeated_field_span(config);
    for signal in &config.bytes {
        if signal.start_byte + signal.length > payload.len() { continue; }
        
        if signal.conv_type == "bitfield" {
            if let Some(mappings) = &signal.bitfield_encoding {
                for m in mappings {
                    let is_set = ((payload[signal.start_byte] >> m.bit_index) & 1) != 0;
                    generated_mapping::update_proto_bool_generated(data, &m.protobuf_field, is_set);
                }
            }
            continue;
        }

        let val = match signal.conv_type.as_str() {
            "uint32" => {
                // 0x1C9 net_energy / regen_energy use uint32 with precision
                // 0.001 (Wh). Previously fell through the `_ => continue` arm
                // so the fields were silently dropped and the website saw 0.
                let bytes = [
                    payload[signal.start_byte], payload[signal.start_byte+1],
                    payload[signal.start_byte+2], payload[signal.start_byte+3],
                ];
                (u32::from_le_bytes(bytes) as f64) * signal.precision
            },
            "int32" => {
                let bytes = [
                    payload[signal.start_byte], payload[signal.start_byte+1],
                    payload[signal.start_byte+2], payload[signal.start_byte+3],
                ];
                (i32::from_le_bytes(bytes) as f64) * signal.precision
            },
            "uint16" => {
                let bytes = [payload[signal.start_byte], payload[signal.start_byte+1]];
                (u16::from_le_bytes(bytes) as f64) * signal.precision
            },
            "int16" => {
                let bytes = [payload[signal.start_byte], payload[signal.start_byte+1]];
                (i16::from_le_bytes(bytes) as f64) * signal.precision
            },
            "uint8" => (payload[signal.start_byte] as f64) * signal.precision,
            "int8" => (payload[signal.start_byte] as i8 as f64) * signal.precision,
            _ => continue,
        };

        if let Some(pb) = &signal.protobuf {
            if pb.repeated {
                if let Some(field_index) = pb.field_index {
                    let mut adjusted_pb = pb.clone();
                    adjusted_pb.field_index = Some(field_index + series_index.saturating_mul(repeated_span));
                    generated_mapping::update_proto_field_generated(
                        data,
                        &adjusted_pb.field_name,
                        val as f32,
                        &adjusted_pb,
                    );
                }
            } else {
                generated_mapping::update_proto_field_generated(data, &pb.field_name, val as f32, pb);
            }
        }
    }
}

/// Cached snapshots serve the dashboard; only fresh updates reach history.
fn ipc_server_loop(
    sensor_data: Arc<Mutex<ArtemisSensorData>>,
    external_dynamics: Arc<Mutex<ExternalDynamicsData>>,
    board_last_seen: Arc<Mutex<HashMap<String, f32>>>,
    publish_hz: u64,
    fanout: Fanout,
) -> Result<()> {
    let _ = std::fs::remove_file(SOCKET_PATH);
    let listener = UnixListener::bind(SOCKET_PATH)?;
    let clients = Arc::new(Mutex::new(Vec::<UnixStream>::new()));
    let accepted = clients.clone();
    thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = stream.set_write_timeout(Some(Duration::from_millis(20)));
            accepted.lock().unwrap().push(stream);
        }
    });
    let interval = Duration::from_secs_f64(1.0 / publish_hz as f64);
    let mut status_tick = Instant::now();
    let mut health_tick = Instant::now() - Duration::from_secs(1);
    loop {
        let start = Instant::now();
        let status = tick_board_last_seen(&board_last_seen, status_tick.elapsed().as_secs_f32());
        status_tick = Instant::now();
        let mut snapshot = sensor_data.lock().unwrap().clone();
        snapshot.time = native::now_ns() / 1_000_000;
        apply_external_dynamics(&mut snapshot, &external_dynamics.lock().unwrap());
        apply_board_last_seen(&mut snapshot, &status);
        if health_tick.elapsed() >= Duration::from_secs(1) {
            let paths = native::FIELDS
                .iter()
                .filter(|f| f.group_name == "board_status")
                .map(|f| FieldPath {
                    group: f.group,
                    field: f.field,
                })
                .collect();
            if let Some(update) = native::update(&snapshot, native::now_ns(), paths) {
                fanout.send(update);
            }
            health_tick = Instant::now();
        }
        let bytes = snapshot.encode_to_vec();
        clients
            .lock()
            .unwrap()
            .retain_mut(|stream| native::write_frame(stream, &bytes).is_ok());
        thread::sleep(interval.saturating_sub(start.elapsed()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ppos_keeps_full_precision_through_the_frame() {
        // A sentence made from ChudPi's recording of 2026-10-10.
        let state = Arc::new(Mutex::new(ExternalDynamicsData::default()));
        update_external_dynamics_from_sentence(
            &state,
            "$PPOS,30.139619879,-97.630361770,136.754,0.792,1,1791653675.300*16",
        );
        let mut data = ArtemisSensorData::default();
        apply_external_dynamics(&mut data, &state.lock().unwrap());
        let sent = ArtemisSensorData::decode(&data.encode_to_vec()[..])
            .unwrap()
            .dynamics
            .unwrap();
        assert_eq!(
            sent.gps_precise,
            vec![
                30.139619879,
                -97.630361770,
                136.754,
                0.792,
                1.0,
                1791653675.300
            ]
        );
        assert!(sent.gps.is_empty(), "$PPOS must not touch the old field");

        // GGA still fills the old field and leaves the new one alone.
        update_external_dynamics_from_sentence(
            &state,
            "$GPGGA,190115.30,3008.3772,N,09737.8217,W,1,08,1.0,163.1,M,-26.3,M,,*00",
        );
        assert_eq!(state.lock().unwrap().gps.len(), 2);
        assert_eq!(state.lock().unwrap().gps_precise.len(), 6);

        // A damaged or cut-off sentence changes nothing; "no solution" clears the field.
        update_external_dynamics_from_sentence(
            &state,
            "$PPOS,30.139619879,-97.630361770,136.754,0.792,1,1791653675.300*17",
        );
        update_external_dynamics_from_sentence(
            &state,
            "$PPOS,30.139619879,-97.630361770,136.754,0.792,1,17916536",
        );
        assert_eq!(state.lock().unwrap().gps_precise.len(), 6);
        update_external_dynamics_from_sentence(&state, "$PPOS,,,,,0,*2C");
        assert!(state.lock().unwrap().gps_precise.is_empty());
    }
}
