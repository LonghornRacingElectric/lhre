//! Fresh decoded updates shared by acquisition, local logs and the uplink.

use prost::Message;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub use crate::proto::artemis::{
    ArtemisSensorData as SensorData, FieldPath, TelemetryBatch, TelemetryUpdate,
};

pub const LOGGER_SOCKET: &str = "/tmp/BEVO_cand_loggerd.sock";
pub const PUBLISH_SOCKET: &str = "/tmp/BEVO_cand_publishd.sock";
pub const QUEUE_CAPACITY: usize = 16_384;
pub const MAX_FRAME_BYTES: usize = 65_536;
pub const FORMAT_VERSION: u32 = 1;

static STOPPING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
extern "C" fn stop(_: std::ffi::c_int) {
    STOPPING.store(true, Ordering::Relaxed);
}

/// POSIX signal handlers only set a lock-free flag; the writer flushes normally.
pub fn install_shutdown() {
    extern "C" {
        fn signal(sig: std::ffi::c_int, handler: extern "C" fn(std::ffi::c_int)) -> usize;
    }
    unsafe {
        signal(2, stop);
        signal(15, stop);
    }
}
pub fn stopping() -> bool {
    STOPPING.load(Ordering::Relaxed)
}

pub struct FieldDefinition {
    pub group: u32,
    pub field: u32,
    pub group_name: &'static str,
    pub name: &'static str,
    pub pool: &'static str,
    pub length: usize,
    pub repeated: bool,
    pub uplink_hz: Option<f64>,
    pub native_hz: f64,
    pub limited: bool,
}
pub struct SourceField {
    pub group: u32,
    pub field: u32,
    pub slots: &'static [usize],
    pub stride: usize,
}
pub struct SourceDefinition {
    pub base_id: u32,
    pub quantity: u32,
    pub period_ns: i64,
    pub minimum_bytes: usize,
    pub fields: &'static [SourceField],
}

include!(concat!(env!("OUT_DIR"), "/generated_native.rs"));

pub fn now_ns() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(i64::MAX as u128) as i64
}

pub fn definition(path: &FieldPath) -> Option<&'static FieldDefinition> {
    FIELDS
        .iter()
        .find(|field| field.group == path.group && field.field == path.field)
}

pub fn update(source: &SensorData, stamp: i64, paths: Vec<FieldPath>) -> Option<TelemetryUpdate> {
    let mut values = SensorData::default();
    values.time = stamp / 1_000_000;
    let paths: Vec<_> = paths
        .into_iter()
        .filter(|path| copy_field(source, &mut values, path))
        .collect();
    (!paths.is_empty()).then_some(TelemetryUpdate {
        sample_time_ns: stamp,
        values: Some(values),
        updated_fields: paths,
    })
}

struct Assembly {
    started: i64,
    seen: Vec<bool>,
}

#[derive(Default)]
pub struct Sampler {
    arrays: BTreeMap<(u32, u32), Assembly>,
}

impl Sampler {
    pub fn can_update(
        &mut self,
        id: u32,
        payload_size: usize,
        stamp: i64,
        values: &SensorData,
    ) -> Option<TelemetryUpdate> {
        let source = SOURCES
            .iter()
            .find(|source| id >= source.base_id && id - source.base_id < source.quantity)?;
        if payload_size < source.minimum_bytes {
            return None;
        }
        let index = (id - source.base_id) as usize;
        let mut paths = Vec::new();
        for binding in source.fields {
            let path = FieldPath {
                group: binding.group,
                field: binding.field,
            };
            let field = definition(&path)?;
            if field.repeated {
                let state = self
                    .arrays
                    .entry((field.group, field.field))
                    .or_insert_with(|| Assembly {
                        started: stamp,
                        seen: vec![false; field.length],
                    });
                if source.period_ns > 0 && stamp.saturating_sub(state.started) >= source.period_ns {
                    state.seen.fill(false);
                    state.started = stamp;
                }
                for slot in binding.slots {
                    if let Some(seen) = state.seen.get_mut(index * binding.stride + slot) {
                        *seen = true;
                    }
                }
                if state.seen.iter().any(|seen| !seen) {
                    continue;
                }
                state.seen.fill(false);
                state.started = stamp;
            }
            paths.push(path);
        }
        update(values, stamp, paths)
    }
}

/// Each socket has its own queue and writer; slow clients never hold CAN locks.
#[derive(Clone)]
pub struct Fanout {
    writers: Vec<mpsc::SyncSender<TelemetryUpdate>>,
    dropped: Arc<AtomicU64>,
}

impl Fanout {
    pub fn bind() -> std::io::Result<Self> {
        let dropped = Arc::new(AtomicU64::new(0));
        let mut writers = Vec::new();
        for path in [LOGGER_SOCKET, PUBLISH_SOCKET] {
            let _ = std::fs::remove_file(path);
            let listener = UnixListener::bind(path)?;
            let client = Arc::new(Mutex::new(None::<UnixStream>));
            let accepted = client.clone();
            thread::spawn(move || {
                for stream in listener.incoming().flatten() {
                    let _ = stream.set_write_timeout(Some(Duration::from_millis(100)));
                    *accepted.lock().unwrap() = Some(stream);
                }
            });
            let (tx, rx) = mpsc::sync_channel::<TelemetryUpdate>(QUEUE_CAPACITY);
            writers.push(tx);
            thread::spawn(move || {
                while let Ok(update) = rx.recv() {
                    let bytes = update.encode_to_vec();
                    loop {
                        let sent = {
                            let mut client = client.lock().unwrap();
                            if let Some(stream) = client.as_mut() {
                                if write_frame(stream, &bytes).is_ok() {
                                    true
                                } else {
                                    *client = None;
                                    false
                                }
                            } else {
                                false
                            }
                        };
                        if sent {
                            break;
                        }
                        thread::sleep(Duration::from_millis(20));
                    }
                }
            });
        }
        Ok(Self { writers, dropped })
    }

    pub fn send(&self, update: TelemetryUpdate) {
        for writer in &self.writers {
            if writer.try_send(update.clone()).is_err() {
                let count = self.dropped.fetch_add(1, Ordering::Relaxed) + 1;
                if count == 1 || count % 1000 == 0 {
                    eprintln!("[NATIVE] consumer queue overflow: {count} dropped updates");
                }
            }
        }
    }
}

pub fn write_frame(stream: &mut impl Write, bytes: &[u8]) -> std::io::Result<()> {
    stream.write_all(&(bytes.len() as u32).to_be_bytes())?;
    stream.write_all(bytes)
}

pub fn read_frame(stream: &mut impl Read) -> std::io::Result<Vec<u8>> {
    let mut length = [0; 4];
    stream.read_exact(&mut length)?;
    let length = u32::from_be_bytes(length) as usize;
    if length > MAX_FRAME_BYTES {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "native frame exceeds limit",
        ));
    }
    let mut bytes = vec![0; length];
    stream.read_exact(&mut bytes)?;
    Ok(bytes)
}

#[derive(Default)]
pub struct UplinkLimits {
    sent: BTreeMap<(u32, u32), Instant>,
    pending: BTreeMap<(u32, u32), (Instant, TelemetryUpdate)>,
}

impl UplinkLimits {
    pub fn push(&mut self, sample: TelemetryUpdate, now: Instant) -> Vec<TelemetryUpdate> {
        let Some(values) = sample.values.as_ref() else {
            return Vec::new();
        };
        let mut immediate = Vec::new();
        for path in &sample.updated_fields {
            let Some(field) = definition(path) else {
                continue;
            };
            let period = field
                .uplink_hz
                .filter(|hz| field.limited && (field.native_hz == 0.0 || *hz < field.native_hz))
                .map(|hz| Duration::from_secs_f64(1.0 / hz));
            let key = (path.group, path.field);
            if let (Some(period), Some(last)) = (period, self.sent.get(&key)) {
                let deadline = *last + period;
                if now < deadline {
                    if let Some(value) = update(values, sample.sample_time_ns, vec![path.clone()]) {
                        self.pending.insert(key, (deadline, value));
                    }
                    continue;
                }
            }
            self.pending.remove(&key);
            self.sent.insert(key, now);
            immediate.push(path.clone());
        }
        let mut result: Vec<_> = update(values, sample.sample_time_ns, immediate)
            .into_iter()
            .collect();
        result.extend(self.flush_due(now));
        result
    }

    pub fn deadline(&self) -> Option<Instant> {
        self.pending.values().map(|(deadline, _)| *deadline).min()
    }

    pub fn flush_due(&mut self, now: Instant) -> Vec<TelemetryUpdate> {
        let due: Vec<_> = self
            .pending
            .iter()
            .filter(|(_, (deadline, _))| *deadline <= now)
            .map(|(key, _)| *key)
            .collect();
        due.into_iter()
            .map(|key| {
                self.sent.insert(key, now);
                self.pending.remove(&key).unwrap().1
            })
            .collect()
    }
}
