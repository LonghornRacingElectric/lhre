use anyhow::Result;
use prost::Message;
use rumqttc::{Client, MqttOptions, QoS};
use sensor_proto::native::{self, TelemetryBatch, TelemetryUpdate, UplinkLimits};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::mpsc::{self, SyncSender};
use std::thread;
use std::time::{Duration, Instant};

const BATCH_DELAY: Duration = Duration::from_millis(5);
const BATCH_COUNT: usize = 64;
const OUTBOUND_CAPACITY: usize = 2048;

fn mqtt_sender() -> SyncSender<Vec<u8>> {
    let host = std::env::var("PUBLISHD_MQTT_HOST").unwrap_or_else(|_| "100.88.194.126".into());
    let port = std::env::var("PUBLISHD_MQTT_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1883);
    let id = std::env::var("PUBLISHD_MQTT_CLIENT_ID").unwrap_or_else(|_| "BEVO-ARTEMIS".into());
    let mut options = MqttOptions::new(format!("{id}-{}", std::process::id()), host, port);
    options.set_keep_alive(Duration::from_secs(20));
    let (client, mut connection) = Client::new(options, 64);
    thread::spawn(move || {
        for event in connection.iter() {
            if let Err(error) = event {
                eprintln!("[PUBLISHD] MQTT connection: {error}");
                thread::sleep(Duration::from_millis(200));
            }
        }
    });
    let (tx, rx) = mpsc::sync_channel::<Vec<u8>>(OUTBOUND_CAPACITY);
    thread::spawn(move || {
        while let Ok(bytes) = rx.recv() {
            if let Err(error) = client.publish("artemis/data", QoS::AtLeastOnce, false, bytes) {
                eprintln!("[PUBLISHD] MQTT publish: {error}");
            }
        }
    });
    tx
}

fn flush(batch: &mut TelemetryBatch, sender: &SyncSender<Vec<u8>>, dropped: &mut u64) {
    if batch.updates.is_empty() {
        return;
    }
    let bytes = batch.encode_to_vec();
    batch.updates.clear();
    if sender.try_send(bytes).is_err() {
        *dropped += 1;
        if *dropped == 1 || *dropped % 1000 == 0 {
            eprintln!("[PUBLISHD] outbound overflow: {dropped} batches dropped");
        }
    }
}

fn main() -> Result<()> {
    native::install_shutdown();
    let release = std::env::var("BEVO_CORE_RELEASE_ROOT").map_err(|_| {
        anyhow::anyhow!("publishd requires a managed Artemis release and reconciliation plan")
    })?;
    let projection = sensor_proto::projection::Projection::from_release(Path::new(&release))
        .map_err(anyhow::Error::msg)?;
    let sender = mqtt_sender();
    let (tx, rx) = mpsc::sync_channel::<TelemetryUpdate>(native::QUEUE_CAPACITY);
    thread::spawn(move || loop {
        match UnixStream::connect(native::PUBLISH_SOCKET) {
            Ok(mut stream) => {
                while let Ok(bytes) = native::read_frame(&mut stream) {
                    match TelemetryUpdate::decode(&bytes[..]) {
                        Ok(update) => {
                            if tx.send(update).is_err() {
                                return;
                            }
                        }
                        Err(error) => eprintln!("[PUBLISHD] malformed native update: {error}"),
                    }
                }
            }
            Err(_) => thread::sleep(Duration::from_millis(200)),
        }
    });
    let mut limits = UplinkLimits::default();
    let mut batch = TelemetryBatch {
        format_version: native::FORMAT_VERSION,
        updates: Vec::new(),
    };
    let mut batch_deadline = Instant::now() + BATCH_DELAY;
    let mut dropped = 0;
    while !native::stopping() {
        let now = Instant::now();
        let batch_wait = if batch.updates.is_empty() {
            now + Duration::from_secs(1)
        } else {
            batch_deadline
        };
        let deadline = limits.deadline().map_or(batch_wait, |d| d.min(batch_wait));
        let updates = match rx.recv_timeout(deadline.saturating_duration_since(now)) {
            Ok(update) => limits.push(update, Instant::now()),
            Err(mpsc::RecvTimeoutError::Timeout) => limits.flush_due(Instant::now()),
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        };
        for update in updates {
            if let Some(update) = projection.project_update(&update) {
                if batch.encoded_len() + update.encoded_len() + 8 > native::MAX_FRAME_BYTES {
                    flush(&mut batch, &sender, &mut dropped);
                }
                if batch.updates.is_empty() {
                    batch_deadline = Instant::now() + BATCH_DELAY;
                }
                batch.updates.push(update);
                if batch.updates.len() >= BATCH_COUNT {
                    flush(&mut batch, &sender, &mut dropped);
                }
            }
        }
        if Instant::now() >= batch_deadline {
            flush(&mut batch, &sender, &mut dropped);
            batch_deadline = Instant::now() + BATCH_DELAY;
        }
    }
    flush(&mut batch, &sender, &mut dropped);
    Ok(())
}
