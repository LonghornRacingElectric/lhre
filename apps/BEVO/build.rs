//! Generates everything derived from the CAN schema at build time:
//!
//!   1. `_.rs` — prost bindings for the Bazel-generated can_packets.proto
//!   2. `generated_mapping.rs` — the name→proto-field dispatch used by cand
//!   3. `generated_config.rs` — typed CAN tables and CSV columns/array lengths
//!
//! Outputs land in OUT_DIR and are compiled into sensor_proto. Nothing generated is
//! checked in. Bazel supplies PROTOC / PROTO_FILE / CAN_JSON as declared
//! inputs from the shared textproto pipeline.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::env;
use std::fs;
use std::path::PathBuf;

use prost::Message;
use prost_types::{field_descriptor_proto, DescriptorProto, FileDescriptorSet};
use serde_json::Value;

fn main() {
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR not set"));

    let proto_path = env::var("PROTO_FILE")
        .expect("PROTO_FILE must be supplied by Bazel; build //apps/BEVO:pi_core_binaries");
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

    let can_json_path = PathBuf::from(
        env::var("CAN_JSON")
            .expect("CAN_JSON must be supplied by Bazel; build //apps/BEVO:pi_core_binaries"),
    );

    let fds_bytes = fs::read(&fds_path).expect("reading descriptor set");
    let fds = FileDescriptorSet::decode(fds_bytes.as_slice()).expect("decoding descriptor set");
    let can_json = fs::read_to_string(&can_json_path).expect("reading can.json");
    let mapping = generate_mapping(&fds, &can_json);
    fs::write(out_dir.join("generated_mapping.rs"), mapping).expect("writing generated_mapping.rs");
    let contract_path = env::var("SCHEMA_CONTRACT").expect("SCHEMA_CONTRACT must be supplied by Bazel");
    let contract: Value = serde_json::from_slice(&fs::read(contract_path).expect("reading schema contract")).expect("parsing schema contract");
    fs::write(out_dir.join("generated_config.rs"), generate_config(&can_json, &contract)).expect("writing generated_config.rs");

    println!("cargo:rerun-if-env-changed=PROTO_FILE");
    println!("cargo:rerun-if-env-changed=CAN_JSON");
    println!("cargo:rerun-if-changed={}", proto_path);
    println!("cargo:rerun-if-changed={}", can_json_path.display());
}

fn generate_config(can_json: &str, contract: &Value) -> String {
    let packets: Vec<Value> = serde_json::from_str(can_json).expect("parsing generated CAN map");
    let mut code = vec!["pub fn packets() -> Vec<PacketConfig> { vec![".to_string()];
    for packet in packets {
        let sources: Vec<String> = packet["from"].as_array().unwrap().iter()
            .map(|name| format!("{:?}.to_string()", name.as_str().unwrap())).collect();
        code.push(format!("PacketConfig {{ packet_id: {}, packet_name: {:?}.to_string(), from: vec![{}], quantity: {}, bytes: vec![",
            packet["packet_id"], packet["packet_name"].as_str().unwrap(), sources.join(","), packet["quantity"]));
        for signal in packet["bytes"].as_array().unwrap() {
            let protobuf = if signal["protobuf"].is_object() {
                let mapping = &signal["protobuf"];
                let index = mapping["field_index"].as_u64().map(|value| format!("Some({value})")).unwrap_or_else(|| "None".to_string());
                format!("Some(ProtobufMapping {{ field_name: {:?}.to_string(), repeated: {}, field_index: {} }})",
                    mapping["field"].as_str().unwrap(), mapping["repeated"], index)
            } else { "None".to_string() };
            let bitfield = if let Some(bits) = signal["bitfield_encoding"].as_array() {
                let entries: Vec<String> = bits.iter().map(|bit| format!("BitfieldMapping {{ protobuf_field: {:?}.to_string(), bit_index: {} }}",
                    bit["protobuf_field"].as_str().unwrap(), bit["bit_index"])).collect();
                format!("Some(vec![{}])", entries.join(","))
            } else { "None".to_string() };
            code.push(format!("SignalConfig {{ start_byte: {}, length: {}, name: {:?}.to_string(), conv_type: {:?}.to_string(), precision: {:?}, bitfield_encoding: {}, protobuf: {} }},",
                signal["start_byte"], signal["length"], signal["name"].as_str().unwrap(), signal["conv_type"].as_str().unwrap(),
                signal["precision"].as_f64().unwrap(), bitfield, protobuf));
        }
        code.push("] },".to_string());
    }
    code.push("] }".to_string());
    let mut headers = Vec::new();
    let mut repeated = Vec::new();
    for field in contract["fields"].as_array().unwrap() {
        let names: Vec<&str> = field["names"].as_array().unwrap().iter().map(|name| name.as_str().unwrap()).collect();
        let name = names.join(".");
        if field["repeated"].as_bool().unwrap() {
            let count = field["layout"].as_array().unwrap().len();
            repeated.push(format!("({:?}, {count})", names.last().unwrap()));
            headers.extend((0..count).map(|index| format!("{name}[{index}]")));
        } else { headers.push(name); }
    }
    code.push(format!("pub const CSV_HEADERS: &[&str] = &{headers:?};"));
    code.push(format!("pub const REPEATED_FIELDS: &[(&str, usize)] = &[{}];", repeated.join(",")));
    code.join("\n")
}

fn value_expression(field_type: &str) -> &str {
    match field_type {
        "float" => "val",
        "double" => "val as f64",
        "uint32" => "val as u32",
        "uint64" => "val as u64",
        "int64" => "val as i64",
        "bool" => "val != 0.0",
        _ => "val as i32",
    }
}

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
    let mut rep_signals: BTreeMap<String, String> = BTreeMap::new();
    let mut bool_signals: BTreeSet<String> = BTreeSet::new();
    for pkt in &packets {
        let signals = pkt
            .get("bytes")
            .or_else(|| pkt.get("signals"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for sig in &signals {
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
                continue;
            }

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
                rep_signals.insert(fname.to_string(), pb["type"].as_str().expect("generated protobuf type").to_string());
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
    for (field_name, field_type) in &rep_signals {
        let var = &field_to_var[field_name];
        let expression = value_expression(field_type);
        code.push(format!(
            "                \"{field_name}\" => crate::set_vec_index(&mut {var}.{field_name}, i, {expression}),"
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
            let expression = value_expression(field_type);
            code.push(format!("            \"{field_name}\" => {var}.{field_name} = {expression},"));
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
