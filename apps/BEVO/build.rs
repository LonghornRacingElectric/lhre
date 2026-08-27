//! Generates everything derived from the CAN schema at build time:
//!
//!   1. `_.rs` — prost bindings for schema/can_packets.proto (serde-derived)
//!   2. `generated_mapping.rs` — the name→proto-field dispatch used by cand
//!
//! Both land in OUT_DIR; lib.rs include!()s them. Nothing generated is
//! checked in. Under Bazel, PROTOC / PROTO_FILE / CAN_JSON point at the
//! hermetic protoc and the Bazel-generated can.json; under plain Cargo the
//! defaults kick in and can.json is produced by the in-repo Python
//! generator (python3, stdlib only).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

use prost::Message;
use prost_types::{field_descriptor_proto, DescriptorProto, FileDescriptorSet};
use serde_json::Value;

fn main() {
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_string());
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR not set"));

    let proto_path = env::var("PROTO_FILE").unwrap_or_else(|_| {
        PathBuf::from(&manifest_dir)
            .join("schema/can_packets.proto")
            .to_string_lossy()
            .to_string()
    });
    let mut proto_dir = PathBuf::from(&proto_path);
    proto_dir.pop();

    let fds_path = out_dir.join("can_packets_fds.bin");
    let mut config = prost_build::Config::new();
    config.include_file("_.rs");
    config.file_descriptor_set_path(&fds_path);
    config.type_attribute(".", "#[derive(serde::Serialize, serde::Deserialize)]");
    config
        .compile_protos(&[proto_path.clone()], &[proto_dir.to_string_lossy().to_string()])
        .unwrap();

    let can_json_path = match env::var("CAN_JSON") {
        Ok(path) => PathBuf::from(path),
        Err(_) => generate_can_json_with_python(&manifest_dir, &out_dir),
    };

    let fds_bytes = fs::read(&fds_path).expect("reading descriptor set");
    let fds = FileDescriptorSet::decode(fds_bytes.as_slice()).expect("decoding descriptor set");
    let can_json = fs::read_to_string(&can_json_path).expect("reading can.json");
    let mapping = generate_mapping(&fds, &can_json);
    fs::write(out_dir.join("generated_mapping.rs"), mapping).expect("writing generated_mapping.rs");

    println!("cargo:rerun-if-env-changed=PROTO_FILE");
    println!("cargo:rerun-if-env-changed=CAN_JSON");
    println!("cargo:rerun-if-changed={}", proto_path);
    for f in ["can_packets.csv", "can_bitfields.csv", "generate_can_json.py"] {
        println!(
            "cargo:rerun-if-changed={}",
            PathBuf::from(&manifest_dir).join("schema").join(f).display()
        );
    }
}

/// Cargo-only fallback: run the in-repo generator (stdlib-only Python).
fn generate_can_json_with_python(manifest_dir: &str, out_dir: &PathBuf) -> PathBuf {
    let schema = PathBuf::from(manifest_dir).join("schema");
    let out = out_dir.join("can.json");
    let status = Command::new("python3")
        .arg(schema.join("generate_can_json.py"))
        .arg(schema.join("can_packets.csv"))
        .arg(schema.join("can_bitfields.csv"))
        .arg(&out)
        .status()
        .expect("running schema/generate_can_json.py (Cargo builds need python3 on PATH)");
    assert!(status.success(), "generate_can_json.py failed");
    out
}

/// Port of the retired codegen.py: emit the match-based dispatch that routes
/// decoded CAN signal names into the right OrionSensorData sub-message field.
/// Output is kept byte-identical to what codegen.py produced.
fn generate_mapping(fds: &FileDescriptorSet, can_json: &str) -> String {
    // Index all top-level messages by fully-qualified name.
    let mut messages: HashMap<String, &DescriptorProto> = HashMap::new();
    for file in &fds.file {
        let package = file.package.clone().unwrap_or_default();
        for msg in &file.message_type {
            let full_name = if package.is_empty() {
                msg.name().to_string()
            } else {
                format!("{}.{}", package, msg.name())
            };
            messages.insert(full_name, msg);
        }
    }

    // Resolve the sensor message the same way codegen.py did.
    let (message_full_name, msg_desc) = ["OrionSensorData", "SensorData"]
        .iter()
        .find_map(|preferred| {
            messages
                .iter()
                .find(|(full, m)| full.as_str() == *preferred || m.name() == *preferred)
                .map(|(full, m)| (full.clone(), *m))
        })
        .or_else(|| {
            messages
                .iter()
                .find(|(_, m)| m.name().ends_with("SensorData"))
                .map(|(full, m)| (full.clone(), *m))
        })
        .expect("no *SensorData message in descriptor");

    let preferred_var_map: HashMap<&str, &str> = [
        ("dynamics", "_d"),
        ("controls", "_c"),
        ("pack", "_p"),
        ("diagnostics_low", "_l"),
        ("diagnostics_high", "_h"),
        ("thermal", "_t"),
    ]
    .into_iter()
    .collect();

    // bindings: (top-level field name, local var), in declaration order.
    // field_to_var: sub-message field name -> local var of its parent.
    let mut bindings: Vec<(String, String)> = Vec::new();
    let mut field_to_var: HashMap<String, String> = HashMap::new();
    for field in &msg_desc.field {
        if field.r#type() != field_descriptor_proto::Type::Message {
            continue;
        }
        let var = preferred_var_map
            .get(field.name())
            .map(|v| v.to_string())
            .unwrap_or_else(|| format!("_{}", field.name()));
        bindings.push((field.name().to_string(), var.clone()));
        let type_name = field.type_name().trim_start_matches('.');
        let sub_msg = messages
            .get(type_name)
            .unwrap_or_else(|| panic!("unresolved message type {type_name}"));
        for sub_field in &sub_msg.field {
            field_to_var.insert(sub_field.name().to_string(), var.clone());
        }
    }
    let binding_lookup: HashMap<&str, &str> = bindings
        .iter()
        .map(|(field, var)| (var.as_str(), field.as_str()))
        .collect();

    // Walk can.json for the signals that carry a protobuf mapping.
    let packets: Vec<Value> = serde_json::from_str(can_json).expect("parsing can.json");
    let mut scalar_signals: BTreeMap<String, String> = BTreeMap::new();
    let mut rep_signals: BTreeSet<String> = BTreeSet::new();
    let mut bool_signals: BTreeSet<String> = BTreeSet::new();
    for pkt in &packets {
        let signals = pkt
            .get("bytes")
            .or_else(|| pkt.get("signals"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for sig in &signals {
            let Some(pb) = sig.get("protobuf").filter(|v| v.is_object()) else {
                continue;
            };
            let fname = ["field_name", "field", "name"]
                .iter()
                .find_map(|k| pb.get(*k).and_then(Value::as_str));
            let Some(fname) = fname else { continue };
            if !field_to_var.contains_key(fname) {
                continue;
            }

            if pb.get("repeated").and_then(Value::as_bool).unwrap_or(false) {
                rep_signals.insert(fname.to_string());
            } else {
                let ty = pb
                    .get("type")
                    .map(|v| match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    })
                    .unwrap_or_else(|| "float".to_string())
                    .to_lowercase();
                scalar_signals.insert(fname.to_string(), ty);
            }

            if sig.get("conv_type").and_then(Value::as_str) == Some("bitfield") {
                let mappings = ["bitfield_encoding", "mappings", "bits"]
                    .iter()
                    .find_map(|k| sig.get(*k).and_then(Value::as_array).cloned())
                    .unwrap_or_default();
                for mapping in &mappings {
                    let bit_name = ["protobuf_field", "field", "name"]
                        .iter()
                        .find_map(|k| mapping.get(*k).and_then(Value::as_str));
                    if let Some(bit_name) = bit_name {
                        if field_to_var.contains_key(bit_name) {
                            bool_signals.insert(bit_name.to_string());
                        }
                    }
                }
            }
        }
    }

    let rust_message_path = message_full_name.replace('.', "::");
    let rust_message_name = message_full_name.rsplit('.').next().unwrap();

    let mut code: Vec<String> = vec![
        "// THIS FILE IS AUTO-GENERATED. DO NOT EDIT.".to_string(),
        format!("use crate::proto::{rust_message_path};"),
        "use crate::config::ProtobufMapping;".to_string(),
        String::new(),
        format!("pub fn update_proto_field_generated(data: &mut {rust_message_name}, name: &str, val: f32, config: &ProtobufMapping) {{"),
    ];
    for (field_name, var) in &bindings {
        code.push(format!(
            "    let {var} = data.{field_name}.get_or_insert_with(Default::default);"
        ));
    }
    code.extend([
        String::new(),
        "    if config.repeated {".to_string(),
        "        if let Some(i) = config.field_index {".to_string(),
        "            match name {".to_string(),
    ]);
    for field_name in &rep_signals {
        let var = &field_to_var[field_name];
        code.push(format!(
            "                \"{field_name}\" => crate::set_vec_index(&mut {var}.{field_name}, i, val),"
        ));
    }
    code.extend([
        "                _ => (),".to_string(),
        "            }".to_string(),
        "        }".to_string(),
        "    } else {".to_string(),
        "        match name {".to_string(),
    ]);
    for (field_name, field_type) in &scalar_signals {
        let var = &field_to_var[field_name];
        if field_type == "bool" {
            code.push(format!(
                "            \"{field_name}\" => {var}.{field_name} = val != 0.0,"
            ));
        } else {
            code.push(format!("            \"{field_name}\" => {var}.{field_name} = val,"));
        }
    }
    code.extend([
        "            _ => (),".to_string(),
        "        }".to_string(),
        "    }".to_string(),
        "}".to_string(),
        String::new(),
        format!("pub fn update_proto_bool_generated(data: &mut {rust_message_name}, name: &str, val: bool) {{"),
    ]);

    let bool_prefixes: BTreeSet<&String> = bool_signals.iter().map(|f| &field_to_var[f]).collect();
    for var in &bool_prefixes {
        let field_name = binding_lookup[var.as_str()];
        code.push(format!(
            "    let {var} = data.{field_name}.get_or_insert_with(Default::default);"
        ));
    }
    code.push("    match name {".to_string());
    for field_name in &bool_signals {
        let var = &field_to_var[field_name];
        code.push(format!("        \"{field_name}\" => {var}.{field_name} = val,"));
    }
    code.extend(["        _ => (),".to_string(), "    }".to_string(), "}".to_string()]);

    code.join("\n")
}
