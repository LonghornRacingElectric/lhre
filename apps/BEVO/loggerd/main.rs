use anyhow::Result;
use prost::Message;
use sensor_proto::native::{self, TelemetryUpdate};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

struct CsvLogger {
    writer: BufWriter<File>,
    headers: Vec<String>,
    rows_since_flush: usize,
}

fn pool_headers(pool: &str) -> Vec<String> {
    let mut columns = Vec::new();
    for field in native::FIELDS.iter().filter(|f| f.pool == pool) {
        let name = format!("{}.{}", field.group_name, field.name);
        if field.repeated {
            columns.extend((0..field.length).map(|i| format!("{name}[{i}]")));
        } else {
            columns.push(name);
        }
    }
    columns.sort_by(|a, b| natural_cmp(a, b));
    let mut headers = vec![
        "time".into(),
        "sample_time_ns".into(),
        "updated_fields".into(),
    ];
    headers.extend(columns);
    headers
}

impl CsvLogger {
    fn new(path: &Path, pool: &str) -> Result<Self> {
        let headers = pool_headers(pool);
        let mut writer =
            BufWriter::new(OpenOptions::new().write(true).create_new(true).open(path)?);
        write_csv_record(&mut writer, headers.iter().map(String::as_str))?;
        Ok(Self {
            writer,
            headers,
            rows_since_flush: 0,
        })
    }
    fn row(&mut self, row: &BTreeMap<String, String>) -> Result<()> {
        write_csv_record(
            &mut self.writer,
            self.headers
                .iter()
                .map(|key| row.get(key).map_or("", String::as_str)),
        )?;
        self.rows_since_flush += 1;
        if self.rows_since_flush >= 100 {
            self.flush()?;
        }
        Ok(())
    }
    fn flush(&mut self) -> Result<()> {
        self.writer.flush()?;
        self.rows_since_flush = 0;
        Ok(())
    }
}

fn rows(update: &TelemetryUpdate) -> Result<BTreeMap<&'static str, BTreeMap<String, String>>> {
    let Some(values) = &update.values else {
        return Ok(BTreeMap::new());
    };
    let value = serde_json::to_value(values)?;
    let mut pools: BTreeMap<_, BTreeMap<String, String>> = BTreeMap::new();
    let mut masks: BTreeMap<_, Vec<String>> = BTreeMap::new();
    for path in &update.updated_fields {
        let Some(field) = native::definition(path) else {
            continue;
        };
        let name = format!("{}.{}", field.group_name, field.name);
        if let Some(child) = value.get(field.group_name).and_then(|g| g.get(field.name)) {
            flatten_json_value(&name, child, pools.entry(field.pool).or_default());
            masks.entry(field.pool).or_default().push(name);
        }
    }
    for (pool, row) in &mut pools {
        row.insert("time".into(), values.time.to_string());
        row.insert("sample_time_ns".into(), update.sample_time_ns.to_string());
        row.insert(
            "updated_fields".into(),
            serde_json::to_string(&masks[pool])?,
        );
    }
    Ok(pools)
}

fn main() -> Result<()> {
    native::install_shutdown();
    let dir = std::env::var("LOGGERD_LOG_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(std::env::var("BUILD_WORKSPACE_DIRECTORY").unwrap_or_else(|_| ".".into()))
                .join("apps/BEVO/loggerd/logs")
        });
    std::fs::create_dir_all(&dir)?;
    let start = native::now_ns() / 1_000_000;
    let (tx, rx) = mpsc::sync_channel::<TelemetryUpdate>(native::QUEUE_CAPACITY);
    thread::spawn(move || loop {
        match UnixStream::connect(native::LOGGER_SOCKET) {
            Ok(mut stream) => {
                while let Ok(bytes) = native::read_frame(&mut stream) {
                    if let Ok(update) = TelemetryUpdate::decode(&bytes[..]) {
                        if tx.send(update).is_err() {
                            return;
                        }
                    }
                }
            }
            Err(_) => thread::sleep(Duration::from_millis(200)),
        }
    });
    let mut loggers = BTreeMap::new();
    let mut flushed = Instant::now();
    while !native::stopping() {
        match rx.recv_timeout(Duration::from_secs(1).saturating_sub(flushed.elapsed())) {
            Ok(update) => {
                for (pool, row) in rows(&update)? {
                    if !loggers.contains_key(pool) {
                        loggers.insert(
                            pool,
                            CsvLogger::new(&dir.join(format!("artemis_{start}_{pool}.csv")), pool)?,
                        );
                    }
                    loggers.get_mut(pool).unwrap().row(&row)?;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
        if flushed.elapsed() >= Duration::from_secs(1) {
            for logger in loggers.values_mut() {
                logger.flush()?;
            }
            flushed = Instant::now();
        }
    }
    for logger in loggers.values_mut() {
        logger.flush()?;
    }
    Ok(())
}

/// Compare two strings treating runs of digits as numbers, so
/// `cells_v[2]` < `cells_v[10]` < `cells_v[100]`.
fn natural_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    let mut a = a.as_bytes();
    let mut b = b.as_bytes();
    loop {
        match (a.first(), b.first()) {
            (None, None) => return Ordering::Equal,
            (None, Some(_)) => return Ordering::Less,
            (Some(_), None) => return Ordering::Greater,
            (Some(&ca), Some(&cb)) => {
                let a_digit = ca.is_ascii_digit();
                let b_digit = cb.is_ascii_digit();
                if a_digit && b_digit {
                    let (na, ra) = take_number(a);
                    let (nb, rb) = take_number(b);
                    match na.cmp(&nb) {
                        Ordering::Equal => {
                            a = ra;
                            b = rb;
                        }
                        other => return other,
                    }
                } else if a_digit != b_digit {
                    // digits sort before non-digit characters
                    return if a_digit {
                        Ordering::Less
                    } else {
                        Ordering::Greater
                    };
                } else {
                    match ca.cmp(&cb) {
                        Ordering::Equal => {
                            a = &a[1..];
                            b = &b[1..];
                        }
                        other => return other,
                    }
                }
            }
        }
    }
}

/// Parse the leading run of ASCII digits as a number, returning it with the
/// remaining bytes. Over-long runs saturate to u64::MAX (indices are tiny).
fn take_number(bytes: &[u8]) -> (u64, &[u8]) {
    let mut end = 0;
    while end < bytes.len() && bytes[end].is_ascii_digit() {
        end += 1;
    }
    let number = std::str::from_utf8(&bytes[..end])
        .ok()
        .and_then(|text| text.parse::<u64>().ok())
        .unwrap_or(u64::MAX);
    (number, &bytes[end..])
}

fn flatten_json_value(prefix: &str, value: &Value, output: &mut BTreeMap<String, String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                let next_prefix = if prefix.is_empty() {
                    key.to_string()
                } else {
                    format!("{}.{}", prefix, key)
                };
                flatten_json_value(&next_prefix, child, output);
            }
        }
        Value::Array(items) => {
            for (index, child) in items.iter().enumerate() {
                let next_prefix = format!("{}[{}]", prefix, index);
                flatten_json_value(&next_prefix, child, output);
            }
        }
        Value::Null => {
            if !prefix.is_empty() {
                output.insert(prefix.to_string(), String::new());
            }
        }
        Value::Bool(boolean_value) => {
            output.insert(prefix.to_string(), boolean_value.to_string());
        }
        Value::Number(number_value) => {
            output.insert(prefix.to_string(), number_value.to_string());
        }
        Value::String(string_value) => {
            output.insert(prefix.to_string(), string_value.clone());
        }
    }
}

fn write_csv_record<'a>(
    writer: &mut BufWriter<File>,
    fields: impl Iterator<Item = &'a str>,
) -> Result<()> {
    let mut first = true;
    for field in fields {
        if !first {
            writer.write_all(b",")?;
        }
        first = false;
        let escaped = csv_escape(field);
        writer.write_all(escaped.as_bytes())?;
    }
    writer.write_all(b"\n")?;
    Ok(())
}

fn csv_escape(value: &str) -> String {
    let needs_quotes = value.contains(',') || value.contains('"') || value.contains('\n');
    if !needs_quotes {
        return value.to_string();
    }

    let escaped = value.replace('"', "\"\"");
    format!("\"{}\"", escaped)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pool_rows_only_contain_updated_fields_and_keep_empty_arrays() {
        let mut values = native::SensorData::default();
        values.controls = Some(Default::default());
        values.dynamics = Some(Default::default());
        let paths = native::FIELDS
            .iter()
            .filter(|f| f.name == "apps1_travel" || f.name == "gps_precise")
            .map(|f| native::FieldPath {
                group: f.group,
                field: f.field,
            })
            .collect();
        let sample = native::update(&values, 1_000_000_001, paths).unwrap();
        let rows = rows(&sample).unwrap();
        assert_eq!(rows.len(), 2);
        let controls = rows
            .values()
            .find(|r| r.contains_key("controls.apps1_travel"))
            .unwrap();
        assert_eq!(controls["controls.apps1_travel"], "0.0");
        assert!(!controls.contains_key("controls.enable"));
        let gps = rows
            .values()
            .find(|r| r["updated_fields"].contains("gps_precise"))
            .unwrap();
        assert!(!gps.contains_key("dynamics.gps_precise[0]"));
        assert_eq!(gps["sample_time_ns"], "1000000001");
    }
}
